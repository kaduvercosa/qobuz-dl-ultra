# ============================================================================
# lyrics_engine.py -- busca, normalizacao, fallback e gravacao de letras.
# Fluxo principal: LyricsEngine.fetch_and_inject() -> Qobuz -> LRCLIB -> Genius.
# As rotinas de persistencia sao _save_lrc_file() e _inject_metadata().
# ============================================================================
import logging
import os
import re

import httpx
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX, USLT, ID3NoHeaderError
from tqdm import tqdm

from qobuz_dl.color import ERROR as RED
from qobuz_dl.color import MUTED, RESET
from qobuz_dl.color import SUCCESS as GREEN
from qobuz_dl.color import WARNING as YELLOW
from qobuz_dl.settings import QobuzDLSettings

logger = logging.getLogger(__name__)

try:
    import lyricsgenius
except ImportError:
    lyricsgenius = None


class LyricsEngine:
    """
    Responsavel por transformar letras da API em LRC/texto e grava-las
    em arquivos auxiliares e metadados FLAC/MP3.
    """

    def __init__(self, genius_token=None, session=None, settings=None):
        self.genius_token = genius_token
        self.genius = None
        self.settings = settings or QobuzDLSettings()
        self._mxm_token = None

        if self.genius_token and lyricsgenius:
            self.genius = lyricsgenius.Genius(
                self.genius_token, remove_section_headers=True
            )
            self.genius.verbose = False

        # Sessao HTTP sincrona separada (AsyncClient do downloader nao e' compativel)
        if isinstance(session, httpx.Client):
            self._owns_session = False
            self.session = session
        else:
            self._owns_session = True
            self.session = httpx.Client(follow_redirects=True)

    def close(self):
        """
        Fecha somente a sessao criada por esta classe; sessoes externas
        continuam vivas.
        """
        if self._owns_session:
            try:
                self.session.close()
            except Exception as e:
                logger.debug(f"Falha ao fechar sessao HTTP do LyricsEngine: {e}")

    @staticmethod
    def _ms_to_lrc_timestamp(ms):
        """Converte milissegundos para o formato LRC [MM:SS.mmm]."""
        minutes = ms // 60000
        seconds = (ms % 60000) / 1000.0
        return f"[{minutes:02d}:{seconds:06.3f}]"

    def _qobuz_lines_to_lrc(self, lines, inject_intro=False):
        """
        Converte linhas sincronizadas do Qobuz para LRC.
        O marcador inicial representa a introducao antes do primeiro verso.
        """
        lrc_rows = []
        intro_added = False

        for entry in lines:
            start = entry.get("start")
            if start is None:
                continue

            text = (entry.get("line") or "").strip()

            if inject_intro and not intro_added:
                if start > 0:
                    lrc_rows.append("[00:00.000]   » » » ")

                if text:
                    lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)} {text}")
                else:
                    if start == 0:
                        lrc_rows.append("[00:00.000]   » » » ")
                    else:
                        lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)} {text}")

                # A trava precisa ficar aqui fora para garantir que a introdução
                # seja injetada apenas uma única vez no arquivo.
                intro_added = True
            else:
                if text:
                    lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)} {text}")

        return "\n".join(lrc_rows) if lrc_rows else None

    @staticmethod
    def _qobuz_lines_to_plain(lines):
        """Converte linhas sincronizadas para texto simples, uma linha por verso."""
        plain_rows = [(entry.get("line") or "").strip() for entry in lines]
        text = "\n".join(plain_rows).strip("\n")
        return text if text else None

    def extract_qobuz_lyrics(self, lyrics_response, translation_response=None):
        """
        Normaliza letra original e traducao do Qobuz em um formato interno.
        O resultado separa conteudo sincronizado, texto simples e idiomas.
        """
        if not lyrics_response or not isinstance(lyrics_response, dict):
            return None

        original = lyrics_response.get("original")
        if not original or not isinstance(original, dict):
            return None

        lines = original.get("lines") or []
        if not lines:
            return None

        synced = self._qobuz_lines_to_lrc(lines, inject_intro=True)
        plain = self._qobuz_lines_to_plain(lines)

        if not synced and not plain:
            return None

        result = {
            "synced": synced,
            "plain": plain,
            "source": "qobuz",
            "lang": original.get("lang", "en"),
            "translation_langs": lyrics_response.get("translation_langs", []),
            "translations": [],
        }

        if translation_response and isinstance(translation_response, dict):
            t_lines = translation_response.get("lines") or []
            if t_lines:
                t_synced = self._qobuz_lines_to_lrc(t_lines, inject_intro=False)
                t_plain = self._qobuz_lines_to_plain(t_lines)
                if t_synced or t_plain:
                    result["translations"].append(
                        {
                            "language": translation_response.get("lang", "translated"),
                            "plain": t_plain,
                            "synced": t_synced,
                        }
                    )

        return result

    def _build_bilingual_lrc(self, original_lrc, translated_lrc):
        """
        Combina letra original e traducao: DUAS linhas com o MESMO
        timestamp [MM:SS.mmm] -- original primeiro, traducao logo depois
        (prefixo "  » ").

        Ja tentei duas alternativas pra fugir da colisao de timestamp
        (deslocar a traducao em 1ms; e uma linha de continuacao sem tag
        propria), mas na pratica (testado no Flacbox real) nenhuma das
        duas produz quebra de linha visual de verdade -- o player junta o
        texto todo associado ao timestamp corrente num bloco continuo,
        respeitando ou nao uma tag [tempo] separada. O que o Flacbox
        realmente reconhece pra empilhar original/traducao como duas
        linhas distintas e exatamente ESSE formato: duas entradas com o
        MESMO timestamp exato.

        Tradeoff que isso reintroduz: players "ingenuos" que guardam as
        linhas num dicionario indexado pelo timestamp (ex.:
        lyricsByTime["00:12.340"] = texto) tem a traducao sobrescrevendo a
        original nessa chave, e so ela fica destacada. Sem um exemplo
        concreto desse comportamento (o unico caso real observado ate
        agora, o Flacbox, funciona certo com timestamps identicos), fica
        certo priorizar o formato que comprovadamente funciona.
        """
        if not original_lrc or not translated_lrc:
            return original_lrc or translated_lrc

        def parse_lrc(lrc_text, is_translation):
            parsed = []
            for line in lrc_text.splitlines():
                tags = re.findall(r"\[\d{2,}:\d{2}\.\d{2,3}\]", line)
                text = re.sub(r"\[\d{2,}:\d{2}\.\d{2,3}\]", "", line).strip()

                if not text:
                    continue

                for tag in tags:
                    try:
                        m, s = tag.strip("[]").split(":")
                        s, ms = s.split(".")
                        time_ms = (
                            int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0")[:3])
                        )
                        parsed.append((time_ms, tag, text, is_translation))
                    except ValueError:
                        continue
            return parsed

        orig_parsed = parse_lrc(original_lrc, False)
        trans_parsed = parse_lrc(translated_lrc, True)

        combined = orig_parsed + trans_parsed
        combined.sort(key=lambda x: (x[0], x[3]))

        final_lrc = []
        for item in combined:
            tag, text, is_trans = item[1], item[2], item[3]
            if is_trans:
                final_lrc.append(f"{tag}   » {text}")
            else:
                final_lrc.append(f"{tag}  {text}")

        return "\n".join(final_lrc)

    def _inject_instrumental_pauses(self, lrc_text):
        """
        - Adiciona marcador de pausa instrumental '3 (• • •)' 0.5s após a última
        linha se houver um intervalo maior que 10 segundos na sincronização.
        - Ignora a injeção no início da música (se a linha anterior estiver em 00:00.000).
        """
        if not lrc_text:
            return lrc_text

        lines = lrc_text.splitlines()
        parsed_lines = []
        time_tag_re = re.compile(r"\[(\d{2,}):(\d{2})\.(\d{2,3})\]")

        # 1. Extrai o timestamp (ms) de cada linha
        for line in lines:
            tags = time_tag_re.findall(line)
            if not tags:
                parsed_lines.append({"time": None, "raw": line})
                continue

            m, s, ms = tags[0]
            time_ms = int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0")[:3])
            parsed_lines.append({"time": time_ms, "raw": line})

        new_lines = []
        last_time = None

        # 2. Percorre as linhas para calcular os saltos de tempo
        for item in parsed_lines:
            curr_time = item["time"]

            if last_time is not None and curr_time is not None:
                gap = curr_time - last_time

                # Se a diferença for > 10s e o tempo anterior for maior que zero
                if gap > 10000 and last_time > 0:
                    inst_time = last_time + 1000
                    pause_line = f"{self._ms_to_lrc_timestamp(inst_time)} • • •"

                    # Evita duplicar marcadores no mesmo instante
                    if not new_lines or new_lines[-1] != pause_line:
                        new_lines.append(pause_line)

            new_lines.append(item["raw"])

            # Atualiza o último tempo validado
            if curr_time is not None:
                last_time = curr_time

        return "\n".join(new_lines)

    def _fetch_musixmatch_lyrics(self, artist, title):
        """Busca letras sincronizadas no Musixmatch (síncrono)."""
        headers = {
            "x-mxm-app-version": "10.1.1",
            "User-Agent": "Musixmatch/2025120901 CFNetwork/1404.0.5 Darwin/22.3.0",
        }
        try:
            if not self._mxm_token:
                resp_token = self.session.get(
                    "https://apic-appmobile.musixmatch.com/ws/1.1/token.get?app_id=mac-ios-v2.0",
                    headers=headers,
                    timeout=8,
                )
                if resp_token.status_code == 200:
                    data_token = resp_token.json()
                    if (
                        data_token.get("message", {})
                        .get("header", {})
                        .get("status_code")
                        == 200
                    ):
                        self._mxm_token = data_token["message"]["body"]["user_token"]

            if self._mxm_token:
                params = {
                    "q_artist": artist,
                    "q_track": title,
                    "format": "json",
                    "namespace": "lyrics_richsynched",
                    "usertoken": self._mxm_token,
                    "app_id": "mac-ios-v2.0",
                }
                resp_lyric = self.session.get(
                    "https://apic-appmobile.musixmatch.com/ws/1.1/macro.subtitles.get",
                    params=params,
                    headers=headers,
                    timeout=8,
                )
                if resp_lyric.status_code == 200:
                    data = resp_lyric.json()
                    if (
                        data.get("message", {}).get("header", {}).get("status_code")
                        == 200
                    ):
                        body = data["message"]["body"]
                        if (
                            "macro_calls" in body
                            and "track.subtitles.get" in body["macro_calls"]
                        ):
                            sub_msg = body["macro_calls"]["track.subtitles.get"][
                                "message"
                            ]
                            if (
                                sub_msg["header"]["status_code"] == 200
                                and "subtitle_list" in sub_msg["body"]
                            ):
                                subtitle_list = sub_msg["body"]["subtitle_list"]
                                if subtitle_list:
                                    return subtitle_list[0]["subtitle"]["subtitle_body"]
        except Exception as e:
            logger.debug(f"Erro ao buscar no Musixmatch: {e}")
        return None

    def fetch_and_inject(
        self,
        file_path,
        artist,
        track,
        album,
        save_lrc=True,
        embed_lyrics=True,
        qobuz_lyrics_response=None,
        qobuz_translation_response=None,
        track_number=None,
    ):
        """
        Orquestra Qobuz -> LRCLIB -> Genius, respeitando as opcoes de saida.
        A traducao em portugues e priorizada quando estiver disponivel.
        Retorna dict com status da operacao para o chamador conferir.

        `track_number`, quando informado, prefixa toda linha impressa aqui
        com "[NN]" -- em modo paralelo, varias faixas buscam letra ao mesmo
        tempo, e sem essa marca nao da pra saber, so pelo texto, a qual
        faixa uma linha de resultado ("injetado!"/"sem traducao") pertence
        quando ela aparece longe da linha "Procurando letras para: <titulo>"
        que a precedeu (misturada com outras linhas de progresso de
        download no meio). Mesma numeracao usada em "Em Progresso: NN. ...".
        """
        _label = f"{MUTED}[{track_number}]{RESET} " if track_number else ""

        def _tw(msg):
            tqdm.write(f"{_label}{msg}")

        result = {
            "success": False,
            "source": None,
            "language": None,
            "synchronized": False,
            "bilingual": False,
            "embedded": False,
            "saved_external": False,
            "error": None,
        }

        if not save_lrc and not embed_lyrics:
            return result

        only_synced = getattr(self.settings, "only_synced_lyrics", False)

        try:
            _tw(f"    🔍 Procurando letras para: {track}...")

            qobuz_lyrics = self.extract_qobuz_lyrics(
                qobuz_lyrics_response, qobuz_translation_response
            )

            if qobuz_lyrics:
                if only_synced:
                    qobuz_lyrics["plain"] = None
                    for t in qobuz_lyrics.get("translations", []):
                        t["plain"] = None

                if qobuz_lyrics.get("synced") or qobuz_lyrics.get("plain"):
                    original_sync = qobuz_lyrics.get("synced")
                    original_plain = qobuz_lyrics.get("plain")
                    translations = qobuz_lyrics.get("translations", [])
                    orig_lang = str(qobuz_lyrics.get("lang") or "unknown").lower()

                    best_trans = None
                    for t in translations:
                        if "pt" in str(t.get("language", "")).lower():
                            best_trans = t
                            break
                    if not best_trans and translations:
                        best_trans = translations[0]

                    final_sync = original_sync
                    final_plain = original_plain

                    if best_trans and (
                        best_trans.get("synced") or best_trans.get("plain")
                    ):
                        trans_lang = str(
                            best_trans.get("language") or "unknown"
                        ).lower()
                        lang_tag = f"{orig_lang}+{trans_lang}"
                    else:
                        lang_tag = orig_lang

                    if best_trans:
                        if original_sync and best_trans.get("synced"):
                            final_sync = self._build_bilingual_lrc(
                                original_sync, best_trans.get("synced")
                            )
                        if original_plain and best_trans.get("plain"):
                            final_plain = (
                                f"{original_plain}\n\n"
                                f"--- TRADUCAO ({best_trans.get('language', 'pt').upper()}) ---\n\n"
                                f"{best_trans.get('plain')}"
                            )

                    source_label = "Qobuz"

                    if final_sync:
                        final_sync = self._inject_instrumental_pauses(final_sync)

                        is_bilingual = bool(best_trans and best_trans.get("synced"))
                        result["synchronized"] = True
                        result["bilingual"] = is_bilingual
                        result["language"] = lang_tag

                        if embed_lyrics:
                            saved = self._inject_metadata(
                                file_path,
                                final_sync,
                                source=source_label,
                                language=lang_tag,
                                bilingual=is_bilingual,
                            )
                            result["embedded"] = saved

                        if save_lrc:
                            saved = self._save_lrc_file(
                                file_path,
                                final_sync,
                                source=source_label,
                                language=lang_tag,
                            )
                            result["saved_external"] = saved

                        if result["embedded"] or result["saved_external"]:
                            result["success"] = True
                            result["source"] = source_label

                        is_bilingual_str = "BILINGUAL " if is_bilingual else ""
                        if embed_lyrics and save_lrc:
                            _tw(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}sincronizadas "
                                f"injetadas e salvas em .lrc (via Qobuz)!"
                            )
                        elif save_lrc:
                            _tw(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}sincronizadas "
                                f"salvas em .lrc (via Qobuz)!"
                            )
                        elif embed_lyrics:
                            _tw(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}sincronizadas "
                                f"injetadas no metadata (via Qobuz)!"
                            )
                        else:
                            _tw(
                                f" {RED}❌ Falha ao gravar letras sincronizadas (Qobuz){RESET}"
                            )
                        return result

                    elif final_plain:
                        is_bilingual = bool(best_trans and best_trans.get("plain"))
                        result["synchronized"] = False
                        result["bilingual"] = is_bilingual
                        result["language"] = lang_tag

                        if embed_lyrics:
                            saved = self._inject_metadata(
                                file_path,
                                final_plain,
                                source=source_label,
                                language=lang_tag,
                                bilingual=is_bilingual,
                            )
                            result["embedded"] = saved

                        if save_lrc:
                            saved = self._save_lrc_file(
                                file_path,
                                final_plain,
                                source=source_label,
                                language=lang_tag,
                            )
                            result["saved_external"] = saved

                        if result["embedded"] or result["saved_external"]:
                            result["success"] = True
                            result["source"] = source_label

                        is_bilingual_str = "BILINGUAL " if is_bilingual else ""
                        if embed_lyrics and save_lrc:
                            _tw(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}padrao "
                                f"injetadas e salvas em .txt (via Qobuz)!"
                            )
                        elif save_lrc:
                            _tw(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}padrao "
                                f"salvas em .txt (via Qobuz)!"
                            )
                        elif embed_lyrics:
                            _tw(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}padrao "
                                f"injetadas no metadata (via Qobuz)!"
                            )
                        else:
                            _tw(
                                f" {RED}❌ Falha ao gravar letras padrao (Qobuz){RESET}"
                            )
                        return result

            # Fallback Musicmatch
            mxm_lyrics = self._fetch_musixmatch_lyrics(artist, track)
            if mxm_lyrics:
                is_synced = bool(re.search(r"\[\d{2,}:\d{2}(?:\.\d+)?\]", mxm_lyrics))

                if is_synced:
                    mxm_lyrics = self._inject_instrumental_pauses(mxm_lyrics)

                if only_synced and not is_synced:
                    pass  # Pula para o LRCLIB se a restrição de sincronia estiver ativa
                else:
                    result["synchronized"] = is_synced
                    result["source"] = "Musixmatch"
                    result["language"] = "unknown"

                    if embed_lyrics:
                        saved = self._inject_metadata(
                            file_path,
                            mxm_lyrics,
                            source="Musixmatch",
                            language="unknown",
                        )
                        result["embedded"] = saved

                    if save_lrc:
                        saved = self._save_lrc_file(
                            file_path,
                            mxm_lyrics,
                            source="Musixmatch",
                            language="unknown",
                        )
                        result["saved_external"] = saved

                    if result["embedded"] or result["saved_external"]:
                        result["success"] = True

                    sync_str = "sincronizadas" if is_synced else "padrão"
                    ext_str = ".lrc" if is_synced else ".txt"

                    if embed_lyrics and save_lrc:
                        _tw(
                            f"    ✅ Letras {sync_str} injetadas e salvas em {ext_str} (via Musixmatch)!"
                        )
                    elif save_lrc:
                        _tw(
                            f"    ✅ Letras {sync_str} salvas em {ext_str} (via Musixmatch)!"
                        )
                    elif embed_lyrics:
                        _tw(
                            f"    ✅ Letras {sync_str} injetadas no metadata (via Musixmatch)!"
                        )
                    else:
                        _tw(f" {RED}❌ Falha ao gravar letras (Musixmatch){RESET}")

                    return result

            # Fallback LRCLIB
            lrclib_url = "https://lrclib.net/api/get"
            headers = {
                "User-Agent": "qobuz-dl-ultra/1.0 (https://github.com/kaduvercosa/qobuz-dl-ultra)"
            }

            params = {"artist_name": artist, "track_name": track, "album_name": album}
            response = self.session.get(
                lrclib_url, params=params, headers=headers, timeout=12
            )

            if response.status_code != 200:
                params = {"artist_name": artist, "track_name": track}
                response = self.session.get(
                    lrclib_url, params=params, headers=headers, timeout=12
                )

            if response.status_code == 200:
                data = response.json()
                synced_lyrics = data.get("syncedLyrics")
                plain_lyrics = data.get("plainLyrics")

                if only_synced:
                    plain_lyrics = None

                if synced_lyrics:
                    synced_lyrics = self._inject_instrumental_pauses(synced_lyrics)

                    result["synchronized"] = True
                    result["source"] = "LRCLIB"
                    result["language"] = "unknown"

                    if embed_lyrics:
                        saved = self._inject_metadata(
                            file_path,
                            synced_lyrics,
                            source="LRCLIB",
                            language="unknown",
                        )
                        result["embedded"] = saved

                    if save_lrc:
                        saved = self._save_lrc_file(
                            file_path,
                            synced_lyrics,
                            source="LRCLIB",
                            language="unknown",
                        )
                        result["saved_external"] = saved

                    if result["embedded"] or result["saved_external"]:
                        result["success"] = True

                    if embed_lyrics and save_lrc:
                        _tw(
                            "    ✅ Letras sincronizadas injetadas e salvas como .lrc (via LRCLIB)!"
                        )
                    elif save_lrc:
                        _tw(
                            "    ✅ Letras sincronizadas salvas como .lrc (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        _tw(
                            "    ✅ Letras sincronizadas injetadas no metadata (via LRCLIB)!"
                        )
                    else:
                        _tw(
                            " {RED}❌ Falha ao gravar letras sincronizadas (LRCLIB){RESET}"
                        )
                    return result

                elif plain_lyrics:
                    result["synchronized"] = False
                    result["source"] = "LRCLIB"
                    result["language"] = "unknown"

                    if embed_lyrics:
                        saved = self._inject_metadata(
                            file_path, plain_lyrics, source="LRCLIB", language="unknown"
                        )
                        result["embedded"] = saved

                    if save_lrc:
                        saved = self._save_lrc_file(
                            file_path, plain_lyrics, source="LRCLIB", language="unknown"
                        )
                        result["saved_external"] = saved

                    if result["embedded"] or result["saved_external"]:
                        result["success"] = True

                    if embed_lyrics and save_lrc:
                        _tw(
                            "    ✅ Letras padrao injetadas e salvas como .txt (via LRCLIB)!"
                        )
                    elif save_lrc:
                        _tw("    ✅ Letras padrao salvas como .txt (via LRCLIB)!")
                    elif embed_lyrics:
                        _tw("    ✅ Letras padrao salvas no metadata (via LRCLIB)!")
                    else:
                        _tw(" {RED}❌ Falha ao gravar letras padrao (LRCLIB){RESET}")
                    return result

            # Fallback Genius
            if self.genius and not only_synced:
                song = self.genius.search_song(track, artist)
                if song and song.lyrics:
                    result["synchronized"] = False
                    result["source"] = "Genius"
                    result["language"] = "unknown"

                    if embed_lyrics:
                        saved = self._inject_metadata(
                            file_path, song.lyrics, source="Genius", language="unknown"
                        )
                        result["embedded"] = saved

                    if save_lrc:
                        saved = self._save_lrc_file(
                            file_path, song.lyrics, source="Genius", language="unknown"
                        )
                        result["saved_external"] = saved

                    if result["embedded"] or result["saved_external"]:
                        result["success"] = True

                    if embed_lyrics and save_lrc:
                        _tw("    ✅ Letras injetadas e salvas via Genius!")
                    elif save_lrc:
                        _tw("    ✅ Letras salvas via Genius (embed desativado)!")
                    elif embed_lyrics:
                        _tw("    ✅ Letras injetadas via Genius (Fallback)!")
                    else:
                        _tw("    {RED}❌ Falha ao gravar letras (Genius){RESET}")
                    return result

            _tw(f"    {YELLOW}⚠️ Nenhuma letra encontrada para esta faixa.{RESET}")
            return result

        except Exception as e:
            _tw(f"    {RED}❌ Erro durante a pesquisa de letras: {e}{RESET}")
            logger.debug(f"fetch_and_inject falhou para {track}: {e}", exc_info=True)
            result["error"] = str(e)
            return result

    def _save_lrc_file(
        self, audio_file_path, synced_lyrics, source=None, language=None
    ):
        """
        Salva a letra sincronizada em .lrc com fonte e idioma no cabecalho.
        Retorna True/False para indicar sucesso.
        """
        try:
            base_name = os.path.splitext(audio_file_path)[0]
            lrc_path = f"{base_name}.lrc"

            header_lines = []
            if source:
                header_lines.append(f"[by:{source}]")
            if language:
                header_lines.append(f"[la:{language}]")

            content = (
                ("\n".join(header_lines) + "\n" + synced_lyrics)
                if header_lines
                else synced_lyrics
            )

            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except Exception as e:
            logger.debug(f"Falha ao salvar .lrc: {e}")
            return False

    def _inject_metadata(
        self, file_path, lyrics, source=None, language=None, bilingual=False
    ):
        """
        FLAC usa campos Vorbis; MP3 usa USLT e TXXX para idioma/bilinguismo.
        Falhas de tagging sao REPORTADAS (tqdm.write + logger.debug com o
        traceback resumido), nao mais engolidas em silencio.
        Retorna True/False para permitir que o chamador confira o resultado real.
        """
        if not lyrics:
            return False

        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == ".flac":
                audio = FLAC(file_path)
                audio["LYRICS"] = lyrics
                if source:
                    audio["LYRICS_SOURCE"] = source
                if language:
                    audio["LYRICS_LANG"] = language
                audio["LYRICS_BILINGUAL"] = "1" if bilingual else "0"
                audio.save()
            elif ext == ".mp3":
                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    audio = ID3()
                desc = source if source else ""
                audio.add(USLT(encoding=3, lang="eng", desc=desc, text=lyrics))
                if language:
                    audio.delall("TXXX:LYRICS_LANG")
                    audio.add(TXXX(encoding=3, desc="LYRICS_LANG", text=language))
                audio.delall("TXXX:LYRICS_BILINGUAL")
                audio.add(
                    TXXX(
                        encoding=3,
                        desc="LYRICS_BILINGUAL",
                        text="1" if bilingual else "0",
                    )
                )
                audio.save(file_path)
            else:
                return False
            return True
        except Exception as e:
            tqdm.write(
                f" {RED}❌ Falha ao gravar a letra no metadata de "
                f"{os.path.basename(file_path)}: {e}{RESET}"
            )
            logger.debug(f"_inject_metadata falhou em {file_path}: {e}", exc_info=True)
            return False
