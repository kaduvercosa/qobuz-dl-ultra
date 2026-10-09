# ============================================================================
# lyrics_engine.py -- busca, normalizacao, fallback e gravacao de letras.
# Fluxo principal: LyricsEngine.fetch_and_inject() percorre as FONTES listadas
# em LyricsEngine.PROVIDERS (Qobuz -> BiniLyrics -> Musixmatch -> LRCLIB -> Genius) e grava
# a primeira letra encontrada. As rotinas de persistencia sao _save_lrc_file()
# e _inject_metadata().
#
# COMO ADICIONAR UMA FONTE NOVA
#   1. Escreva um metodo `_provider_<nome>(self, query)` que devolva um
#      LyricsCandidate (ou None se nao achou nada / a fonte nao serve).
#   2. Inclua o nome do metodo em LyricsEngine.PROVIDERS; a posicao na tupla
#      e a prioridade. Pronto: pausas instrumentais, gravacao, mensagens e
#      tratamento de erro sao feitos por fetch_and_inject().
# ============================================================================
import logging
import os
import re
from dataclasses import dataclass

import httpx
from mutagen.flac import FLAC
from mutagen.id3 import ID3, TXXX, USLT, ID3NoHeaderError
from tqdm import tqdm

from qobuz_dl import __version__, binilyrics
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


@dataclass
class LyricsQuery:
    """O que uma fonte precisa saber para procurar a letra de uma faixa."""

    artist: str
    track: str
    album: str
    only_synced: bool = False
    qobuz_lyrics_response: object = None
    qobuz_translation_response: object = None
    isrc: str | None = None
    duration: int | None = None  # segundos


@dataclass
class LyricsCandidate:
    """Letra encontrada por uma fonte, pronta para ser gravada."""

    text: str
    source: str
    synchronized: bool
    language: str = "unknown"
    bilingual: bool = False


class LyricsEngine:
    """
    Responsavel por transformar letras da API em LRC/texto e grava-las
    em arquivos auxiliares e metadados FLAC/MP3.
    """

    # Fontes em ordem de prioridade. Cada nome e um metodo
    # `_provider_*(query) -> LyricsCandidate | None` desta classe.
    PROVIDERS = (
        "_provider_qobuz",
        "_provider_binilyrics",
        "_provider_musixmatch",
        "_provider_lrclib",
        "_provider_genius",
    )

    def __init__(self, genius_token=None, session=None, settings=None):
        """Initialize lyrics engine with optional Genius token and HTTP session."""
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
        if session is None:
            self.session = httpx.Client(follow_redirects=True)
            self._owns_session = True
        else:
            self.session = session
            self._owns_session = False

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
        """Converte linhas sincronizadas do Qobuz para LRC canonico."""
        lrc_rows = []

        for entry in lines:
            start = entry.get("start")
            if start is None:
                continue

            text = (entry.get("line") or "").strip()
            if text:
                # LRC canonico: nenhum espaco artificial entre timestamp e texto.
                lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)}{text}")

        return "\n".join(lrc_rows) if lrc_rows else None

    def _normalize_lrc(self, lrc_text):
        """Normaliza uma letra LRC para o formato canonico do projeto."""
        if not lrc_text:
            return lrc_text

        timestamp_re = re.compile(r"\[(\d{2,}):(\d{2})\.(\d{1,3})\]")
        normalized = []

        for raw_line in lrc_text.splitlines():
            line = raw_line.rstrip()
            matches = list(timestamp_re.finditer(line))
            if not matches:
                normalized.append(line)
                continue

            # Mantem tags de tempo multiplas, mas normaliza todas para mm:ss.mmm.
            timestamps = []
            for match in matches:
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                millis = int(match.group(3).ljust(3, "0")[:3])
                timestamps.append(
                    self._ms_to_lrc_timestamp(minutes * 60000 + seconds * 1000 + millis)
                )

            text = timestamp_re.sub("", line).lstrip()
            normalized.append("".join(timestamps) + text)

        return "\n".join(normalized)

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

    def _build_bilingual_lrc(self, original_lrc: str, translated_lrc: str) -> str:
        """Combina letras originais e traduzidas com o mesmo timestamp."""
        if not original_lrc:
            return translated_lrc or ""
        if not translated_lrc:
            return original_lrc

        tags_re = re.compile(r"\[\d{2,}:\d{2}\.\d{2,3}\]")

        def parse_lrc(lrc_text, is_translation=False):
            # Só entram linhas com timestamp e texto: metadados LRC ([ti:],
            # [ar:]...) e linhas vazias são descartados, já que o cabeçalho é
            # gerado separadamente. Várias tags na mesma linha viram várias
            # entradas, uma por timestamp.
            lines = []
            for line in lrc_text.splitlines():
                tags = tags_re.findall(line)
                text = tags_re.sub("", line).strip()
                if not tags or not text:
                    continue
                for tag in tags:
                    lines.append((tag, text, is_translation))
            return lines

        orig_lines = parse_lrc(original_lrc, is_translation=False)
        trans_lines = parse_lrc(translated_lrc, is_translation=True)

        def tag_to_ms(tag):
            if not tag:
                return -1
            m = re.match(r"\[(\d{2,}):(\d{2})\.(\d{2,3})\]", tag)
            if not m:
                return -1
            return (
                int(m.group(1)) * 60000
                + int(m.group(2)) * 1000
                + int(m.group(3).ljust(3, "0")[:3])
            )

        combined = []
        for tag, text, is_trans in orig_lines + trans_lines:
            combined.append((tag_to_ms(tag), tag, text, is_trans))

        # Ordena por timestamp (x[0]) e usa is_translation (x[3]) como desempate
        # garante que a linha original vem sempre imediatamente antes da traduzida
        combined.sort(key=lambda x: (x[0], x[3]))

        result = []
        for _ms, tag, text, _is_trans in combined:
            if tag:
                # Original e tradução saem iguais, sem marcador: o que as
                # distingue é a ordem (original primeiro) no mesmo timestamp.
                result.append(f"{tag}{text}")
            else:
                if text:
                    result.append(text)

        return "\n".join(result)

    def _inject_instrumental_pauses(self, lrc_text):
        """
        - Adiciona marcador de pausa instrumental '• • •' 0.5s apos a ultima
        linha se houver um intervalo maior que 10 segundos na sincronizacao.
        - Ignora a injecao no inicio da musica (se a linha anterior estiver em 00:00.000).
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

                # Se a diferenca for > 10s e o tempo anterior for maior que zero
                if gap > 10000 and last_time > 0:
                    inst_time = last_time + 500
                    pause_line = f"{self._ms_to_lrc_timestamp(inst_time)}• • •"

                    # Evita duplicar marcadores no mesmo instante
                    if not new_lines or new_lines[-1] != pause_line:
                        new_lines.append(pause_line)

            new_lines.append(item["raw"])

            # Atualiza o ultimo tempo validado
            if curr_time is not None:
                last_time = curr_time

        return "\n".join(new_lines)

    def _fetch_musixmatch_lyrics(self, artist, title):
        """Busca letras sincronizadas no Musixmatch (sincrono)."""
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

    # ------------------------------------------------------------------
    # Fontes de letra: cada `_provider_*` recebe um LyricsQuery e devolve um
    # LyricsCandidate (ou None). Excecoes de rede podem subir: quem chama
    # (fetch_and_inject) registra o erro e segue para a proxima fonte.
    # ------------------------------------------------------------------
    def _provider_qobuz(self, query):
        """Letra enviada pela propria API do Qobuz (com traducao, se houver)."""
        lyrics = self.extract_qobuz_lyrics(
            query.qobuz_lyrics_response, query.qobuz_translation_response
        )
        if not lyrics:
            return None

        if query.only_synced:
            lyrics["plain"] = None
            for t in lyrics.get("translations", []):
                t["plain"] = None

        original_sync = lyrics.get("synced")
        original_plain = lyrics.get("plain")
        if not (original_sync or original_plain):
            return None

        translations = lyrics.get("translations", [])
        orig_lang = str(lyrics.get("lang") or "unknown").lower()

        # Prefere a traducao em portugues; senao, a primeira disponivel.
        best = next(
            (t for t in translations if "pt" in str(t.get("language", "")).lower()),
            None,
        ) or (translations[0] if translations else None)

        if best and (best.get("synced") or best.get("plain")):
            trans_lang = str(best.get("language") or "unknown").lower()
            lang_tag = f"{orig_lang}+{trans_lang}"
        else:
            lang_tag = orig_lang

        final_sync = original_sync
        final_plain = original_plain
        if best:
            if original_sync and best.get("synced"):
                final_sync = self._build_bilingual_lrc(
                    original_sync, best.get("synced")
                )
            if original_plain and best.get("plain"):
                trans_name = str(best.get("language") or "pt").upper()
                final_plain = (
                    f"{original_plain}\n\n"
                    f"--- TRADUCAO ({trans_name}) ---\n\n"
                    f"{best.get('plain')}"
                )

        if final_sync:
            return LyricsCandidate(
                final_sync,
                "Qobuz",
                True,
                language=lang_tag,
                bilingual=bool(best and best.get("synced")),
            )
        if final_plain:
            return LyricsCandidate(
                final_plain,
                "Qobuz",
                False,
                language=lang_tag,
                bilingual=bool(best and best.get("plain")),
            )
        return None

    def _provider_binilyrics(self, query):
        """BiniLyrics: TTML estilo Apple Music (sync por palavra/linha).

        Precisa da duracao da faixa (a API exige segundos) e usa o ISRC, quando
        existe, para achar a gravacao exata. Veja qobuz_dl/binilyrics.py.
        """
        try:
            duration = int(round(float(query.duration)))
        except (TypeError, ValueError):
            return None
        if duration <= 0:
            return None

        headers = {
            "User-Agent": (
                f"qobuz-dl-ultra/{__version__} "
                "(https://github.com/kaduvercosa/qobuz-dl-ultra)"
            )
        }
        params = {
            "track": query.track,
            "artist": query.artist,
            "duration": duration,
            "album": query.album or "",
        }
        response = self.session.get(
            binilyrics.API_URL, params=params, headers=headers, timeout=10
        )
        if response.status_code != 200:
            return None

        chosen = binilyrics.select_candidate(
            (response.json() or {}).get("results"),
            isrc=query.isrc,
            title=query.track,
            artist=query.artist,
            album=query.album,
            duration=duration,
        )
        if not chosen:
            return None

        ttml = self.session.get(chosen["lyricsUrl"], headers=headers, timeout=15)
        if ttml.status_code != 200:
            return None
        doc = binilyrics.parse_ttml(ttml.text)

        synchronized = doc.timing != "plain"
        if not synchronized and query.only_synced:
            return None

        target = getattr(self.settings, "lyrics_translation_lang", "pt")
        trans_lang = binilyrics.pick_translation_lang(doc, target or "")
        language = str(doc.language or "unknown").lower()
        bilingual = False

        if synchronized:
            # Por palavra so se o projeto pedir (settings.lyrics_word_sync):
            # a maioria dos players nao entende o LRC estendido.
            use_words = doc.timing == "word" and getattr(
                self.settings, "lyrics_word_sync", False
            )
            text = (
                binilyrics.to_word_lrc(doc)
                if use_words
                else binilyrics.to_line_lrc(doc)
            )
            if trans_lang:
                trans = binilyrics.translation_lrc(doc, trans_lang)
                if trans:
                    text = self._build_bilingual_lrc(text, trans)
                    bilingual = True
        else:
            text = binilyrics.to_plain(doc)
            if trans_lang:
                trans = binilyrics.translation_plain(doc, trans_lang)
                if trans:
                    label = trans_lang.upper()
                    text = f"{text}\n\n--- TRADUCAO ({label}) ---\n\n{trans}"
                    bilingual = True

        if not text.strip():
            return None
        if bilingual:
            language = f"{language}+{trans_lang.lower()}"
        return LyricsCandidate(
            text,
            binilyrics.SOURCE_LABEL,
            synchronized,
            language=language,
            bilingual=bilingual,
        )

    def _provider_musixmatch(self, query):
        """Musixmatch (letra sincronizada ou, as vezes, texto puro)."""
        text = self._fetch_musixmatch_lyrics(query.artist, query.track)
        if not text:
            return None
        synced = bool(re.search(r"\[\d{2,}:\d{2}(?:\.\d+)?\]", text))
        if query.only_synced and not synced:
            return None
        return LyricsCandidate(text, "Musixmatch", synced)

    def _provider_lrclib(self, query):
        """LRCLIB: tenta com o album e repete sem ele se nao achar."""
        url = "https://lrclib.net/api/get"
        headers = {
            "User-Agent": (
                f"qobuz-dl-ultra/{__version__} "
                "(https://github.com/kaduvercosa/qobuz-dl-ultra)"
            )
        }
        params = {
            "artist_name": query.artist,
            "track_name": query.track,
            "album_name": query.album,
        }
        response = self.session.get(url, params=params, headers=headers, timeout=12)
        if response.status_code != 200:
            params = {"artist_name": query.artist, "track_name": query.track}
            response = self.session.get(url, params=params, headers=headers, timeout=12)
        if response.status_code != 200:
            return None

        data = response.json()
        if data.get("syncedLyrics"):
            return LyricsCandidate(data["syncedLyrics"], "LRCLIB", True)
        if data.get("plainLyrics") and not query.only_synced:
            return LyricsCandidate(data["plainLyrics"], "LRCLIB", False)
        return None

    def _provider_genius(self, query):
        """Genius: ultimo recurso, so texto puro (nao serve com only_synced)."""
        if not self.genius or query.only_synced:
            return None
        song = self.genius.search_song(query.track, query.artist)
        if song and song.lyrics:
            return LyricsCandidate(song.lyrics, "Genius", False)
        return None

    # ------------------------------------------------------------------
    # Gravacao e mensagens (compartilhadas por todas as fontes)
    # ------------------------------------------------------------------
    @staticmethod
    def _delivery_message(candidate, embed_lyrics, save_lrc, ok):
        kind = "sincronizadas" if candidate.synchronized else "padrão"
        ext = ".lrc" if candidate.synchronized else ".txt"
        if not ok:
            return f" {RED}❌ Falha ao gravar letras {kind} ({candidate.source}){RESET}"
        if embed_lyrics and save_lrc:
            action = f"injetadas e salvas em {ext}"
        elif save_lrc:
            action = f"salvas em {ext}"
        else:
            action = "injetadas no metadata"
        bilingual = f"{GREEN}BILINGUAL{RESET} " if candidate.bilingual else ""
        return f" ✅ Letras {bilingual}{kind} {action} (via {candidate.source})!"

    def _deliver(self, file_path, candidate, result, embed_lyrics, save_lrc):
        """Grava a letra (metadata e/ou arquivo) e preenche `result`."""
        result["synchronized"] = candidate.synchronized
        result["bilingual"] = candidate.bilingual
        result["language"] = candidate.language

        if embed_lyrics:
            result["embedded"] = self._inject_metadata(
                file_path,
                candidate.text,
                source=candidate.source,
                language=candidate.language,
                bilingual=candidate.bilingual,
            )
        if save_lrc:
            result["saved_external"] = self._save_lrc_file(
                file_path,
                candidate.text,
                source=candidate.source,
                language=candidate.language,
            )

        if result["embedded"] or result["saved_external"]:
            result["success"] = True
            result["source"] = candidate.source
        return result["success"]

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
        isrc=None,
        duration=None,
    ):
        """
        Percorre as fontes de LyricsEngine.PROVIDERS (Qobuz -> Musixmatch ->
        LRCLIB -> Genius) e grava a primeira letra encontrada, respeitando as
        opcoes de saida. A traducao em portugues e priorizada quando estiver
        disponivel. Se uma fonte falhar (rede, API), segue para a proxima.
        Retorna dict com status da operacao para o chamador conferir.

        `track_number`, quando informado, prefixa toda linha impressa aqui
        com "[NN]" -- em modo paralelo, varias faixas buscam letra ao mesmo
        tempo, e sem essa marca nao da pra saber, so pelo texto, a qual
        faixa uma linha de resultado ("injetado!"/"sem traducao") pertence
        quando ela aparece longe da linha "Procurando letras para: "
        que a precedeu (misturada com outras linhas de progresso de
        download no meio). Mesma numeracao usada em "Em Progresso: [NN] ...".

        `isrc` e `duration` (segundos) sao opcionais; sem a duracao a fonte
        BiniLyrics e' ignorada.
        """
        _label = f"{MUTED}[{track_number}]{RESET} " if track_number else ""

        def _tw(msg):
            """Text wrapping helper for lyrics display."""
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

        query = LyricsQuery(
            artist=artist,
            track=track,
            album=album,
            only_synced=getattr(self.settings, "only_synced_lyrics", False),
            qobuz_lyrics_response=qobuz_lyrics_response,
            qobuz_translation_response=qobuz_translation_response,
            isrc=isrc,
            duration=duration,
        )

        _tw(f" 🔍 Procurando letras para: {track}...")
        last_error = None

        for provider_name in self.PROVIDERS:
            try:
                candidate = getattr(self, provider_name)(query)
                if candidate is not None and candidate.text:
                    if candidate.synchronized:
                        candidate.text = self._inject_instrumental_pauses(
                            candidate.text
                        )
            except Exception as e:
                # Uma fonte com problema nao pode impedir as seguintes.
                last_error = e
                logger.debug(
                    f"fonte {provider_name} falhou para {track}: {e}", exc_info=True
                )
                continue

            if candidate is None or not candidate.text:
                continue

            # Achou: grava e encerra (nao tenta as fontes seguintes).
            try:
                ok = self._deliver(file_path, candidate, result, embed_lyrics, save_lrc)
                _tw(self._delivery_message(candidate, embed_lyrics, save_lrc, ok))
            except Exception as e:
                _tw(f" {RED}❌ Erro durante a pesquisa de letras: {e}{RESET}")
                logger.debug(f"gravacao falhou para {track}: {e}", exc_info=True)
                result["error"] = str(e)
            return result

        if last_error is not None:
            _tw(f" {RED}❌ Erro durante a pesquisa de letras: {last_error}{RESET}")
            result["error"] = str(last_error)
        else:
            _tw(f" {YELLOW}⚠️ Nenhuma letra encontrada para esta faixa.{RESET}")
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

            synced_lyrics = self._normalize_lrc(synced_lyrics)

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
