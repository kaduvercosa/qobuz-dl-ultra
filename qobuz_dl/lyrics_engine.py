# ============================================================================
# lyrics_engine.py -- busca, normalizacao, fallback e gravacao de letras.
# Fluxo principal: LyricsEngine.fetch_and_inject() -> Qobuz -> LRCLIB -> Genius.
# As rotinas de persistencia sao _save_lrc_file() e _inject_metadata().
# ============================================================================
import os
import re
import logging
import httpx
from mutagen.id3 import ID3, USLT, TXXX, ID3NoHeaderError
from mutagen.flac import FLAC
from tqdm.rich import tqdm
from qobuz_dl.color import SUCCESS as GREEN, WARNING as YELLOW, ERROR as RED, RESET
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
        Combina letra original e traducao ordenando os versos pelo timestamp.
        Linhas traduzidas recebem o prefixo » para ficarem distinguiveis.
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
                final_lrc.append(f"{tag}  » {text}")
            else:
                final_lrc.append(f"{tag} {text}")

        return "\n".join(final_lrc)

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
    ):
        """
        Orquestra Qobuz -> LRCLIB -> Genius, respeitando as opcoes de saida.
        A traducao em portugues e priorizada quando estiver disponivel.
        Retorna dict com status da operacao para o chamador conferir.
        """
        result = {
            "success": False,
            "source": None,
            "language": None,
            "synchronized": False,
            "embedded": False,
            "saved_external": False,
        }

        if not save_lrc and not embed_lyrics:
            return result

        only_synced = getattr(self.settings, "only_synced_lyrics", False)

        try:
            tqdm.write(f"    🔍 Procurando letras para: {track}...")

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
                        is_bilingual = bool(best_trans and best_trans.get("synced"))
                        result["synchronized"] = True
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
                            tqdm.write(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}sincronizadas "
                                f"injetadas e salvas em .lrc (via Qobuz)!"
                            )
                        elif save_lrc:
                            tqdm.write(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}sincronizadas "
                                f"salvas em .lrc (via Qobuz)!"
                            )
                        elif embed_lyrics:
                            tqdm.write(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}sincronizadas "
                                f"injetadas no metadata (via Qobuz)!"
                            )
                        else:
                            tqdm.write(
                                f" {RED}❌ Falha ao gravar letras sincronizadas (Qobuz){RESET}"
                            )
                        return result

                    elif final_plain:
                        is_bilingual = bool(best_trans and best_trans.get("plain"))
                        result["synchronized"] = False
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
                            tqdm.write(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}padrao "
                                f"injetadas e salvas em .txt (via Qobuz)!"
                            )
                        elif save_lrc:
                            tqdm.write(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}padrao "
                                f"salvas em .txt (via Qobuz)!"
                            )
                        elif embed_lyrics:
                            tqdm.write(
                                f"    ✅ Letras {GREEN}{is_bilingual_str}{RESET}padrao "
                                f"injetadas no metadata (via Qobuz)!"
                            )
                        else:
                            tqdm.write(
                                f" {RED}❌ Falha ao gravar letras padrao (Qobuz){RESET}"
                            )
                        return result

            # Fallback Musicmatch
            mxm_lyrics = self._fetch_musixmatch_lyrics(artist, track)
            if mxm_lyrics:
                is_synced = bool(re.search(r"\[\d{2,}:\d{2}(?:\.\d+)?\]", mxm_lyrics))

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
                        tqdm.write(
                            f"    ✅ Letras {sync_str} injetadas e salvas em {ext_str} (via Musixmatch)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            f"    ✅ Letras {sync_str} salvas em {ext_str} (via Musixmatch)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            f"    ✅ Letras {sync_str} injetadas no metadata (via Musixmatch)!"
                        )
                    else:
                        tqdm.write(
                            f" {RED}❌ Falha ao gravar letras (Musixmatch){RESET}"
                        )

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
                        tqdm.write(
                            "    ✅ Letras sincronizadas injetadas e salvas como .lrc (via LRCLIB)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            "    ✅ Letras sincronizadas salvas como .lrc (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            "    ✅ Letras sincronizadas injetadas no metadata (via LRCLIB)!"
                        )
                    else:
                        tqdm.write(
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
                        tqdm.write(
                            "    ✅ Letras padrao injetadas e salvas como .txt (via LRCLIB)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            "    ✅ Letras padrao salvas como .txt (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            "    ✅ Letras padrao salvas no metadata (via LRCLIB)!"
                        )
                    else:
                        tqdm.write(
                            " {RED}❌ Falha ao gravar letras padrao (LRCLIB){RESET}"
                        )
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
                        tqdm.write("    ✅ Letras injetadas e salvas via Genius!")
                    elif save_lrc:
                        tqdm.write(
                            "    ✅ Letras salvas via Genius (embed desativado)!"
                        )
                    elif embed_lyrics:
                        tqdm.write("    ✅ Letras injetadas via Genius (Fallback)!")
                    else:
                        tqdm.write(" {RED}❌ Falha ao gravar letras (Genius){RESET}")
                    return result

            tqdm.write(f" {YELLOW}⚠️ Nenhuma letra encontrada para esta faixa.{RESET}")
            return result

        except Exception as e:
            tqdm.write(f" {RED}❌ Erro durante a pesquisa de letras: {e}{RESET}")
            logger.debug(f"fetch_and_inject falhou para {track}: {e}", exc_info=True)
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
