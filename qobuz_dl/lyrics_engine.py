# ============================================================================
# lyrics_engine.py -- busca, normalizacao, fallback e gravacao de letras.
# Fluxo principal: LyricsEngine.fetch_and_inject() percorre as FONTES listadas
# em LyricsEngine.PROVIDERS (Qobuz -> BiniLyrics -> Musixmatch -> LRCLIB) e grava
# a primeira letra SINCRONIZADA encontrada. Letra plana (sem timestamps) e'
# sempre descartada: nenhuma fonte a devolve e as rotinas de persistencia
# (_save_lrc_file() e _inject_metadata()) se recusam a grava-la.
#
# COMO ADICIONAR UMA FONTE NOVA
#   1. Escreva um metodo `_provider_<nome>(self, query)` que devolva um
#      LyricsCandidate sincronizado (ou None se nao achou nada, se a fonte
#      so tem letra plana ou se ela nao serve).
#   2. Inclua o nome do metodo em LyricsEngine.PROVIDERS; a posicao na tupla
#      e a prioridade. Pronto: pausas instrumentais, gravacao, mensagens e
#      tratamento de erro sao feitos por fetch_and_inject().
# ============================================================================
import contextlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from typing import TypedDict

from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TXXX, USLT, ID3NoHeaderError
from tqdm import tqdm

from qobuz_dl import __version__, binilyrics, lyrics_comment, ui
from qobuz_dl.http_download import make_sync_client
from qobuz_dl.resilient_http import HttpBudgetExceeded, http_budget, request_sync
from qobuz_dl.settings import QobuzDLSettings

logger = logging.getLogger(__name__)

# Orcamento de espera por fonte de letra durante o DOWNLOAD (ver `network_budget`
# em fetch_and_inject): ate' quanto uma fonte pode esperar cooldown/retries antes
# de ser pulada, e a maior pausa de cooldown (429) que vale a pena esperar.
LYRICS_NETWORK_BUDGET = 15.0
LYRICS_MAX_COOLDOWN_WAIT = 5.0
# Uma pausa explícita do Qobuz só vira símbolo visível se o intervalo real
# entre o fim de um verso e o início do próximo tiver ao menos meio segundo.
MIN_PAUSE_DURATION_MS = 500

# Marca de tempo LRC ([mm:ss.xx] / [mm:ss.mmm]). Sem ao menos uma, o texto e'
# letra plana e nunca pode ser salvo nem injetado.
_LRC_TIMESTAMP_RE = re.compile(r"\[\d{2,}:\d{2}(?:\.\d+)?\]")


def has_lrc_timestamps(text) -> bool:
    """True se `text` tem pelo menos uma marca de tempo LRC."""
    return bool(text) and bool(_LRC_TIMESTAMP_RE.search(text))


# ---------------------------------------------------------------------------
# Prioridade das letras (1 = melhor). Vale no download e no comando `lyrics`.
# So letra SINCRONIZADA e' aceita, em qualquer posicao.
#   1. Qobuz bilingue (original + traducao sincronizadas)
#   2. Qobuz sincronizada
#   3. BiniLyrics sincronizada
#   4. Musixmatch sincronizada
#   5. LRCLIB sincronizada
# ---------------------------------------------------------------------------
RANK_QOBUZ_BILINGUAL = 1
RANK_QOBUZ_SYNCED = 2
RANK_BINILYRICS = 3
RANK_MUSIXMATCH = 4
RANK_LRCLIB = 5
RANK_PLAIN = 99  # letra sem marcas de tempo: pior que qualquer sincronizada


def lyrics_source_rank(source, bilingual: bool = False):
    """Prioridade (1 a 5) de uma letra sincronizada, pelo nome da fonte.

    None quando a fonte e' desconhecida/nao registrada (arquivos antigos).
    """
    nome = str(source or "").strip().lower()
    if not nome:
        return None
    if "qobuz" in nome:
        return RANK_QOBUZ_BILINGUAL if bilingual else RANK_QOBUZ_SYNCED
    if "binilyrics" in nome:
        return RANK_BINILYRICS
    if "musixmatch" in nome or "musicmatch" in nome:
        return RANK_MUSIXMATCH
    if "lrclib" in nome:
        return RANK_LRCLIB
    return None


# Separadores de artistas (so servem para achar o artista principal).
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,|;|&|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b)\s+",
    re.IGNORECASE,
)
# "(feat. X)", "[with Y]" no titulo.
_TITLE_FEAT_RE = re.compile(
    r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring|with)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
# "- Remastered 2011", "- 2009 Remaster", "- Mono", "- Bonus Track"...
_TITLE_VERSION_RE = re.compile(
    r"\s+[-\u2013]\s+(?:\d{4}\s+)?(?:remaster(?:ed)?|mono|stereo|deluxe|bonus track"
    r"|single version|album version)\b.*$",
    re.IGNORECASE,
)
_TITLE_VERSION_PAREN_RE = re.compile(
    r"\s*[\(\[]\s*(?:\d{4}\s+)?(?:remaster(?:ed)?|mono|stereo|deluxe|bonus track"
    r"|single version|album version)\b[^\)\]]*[\)\]]",
    re.IGNORECASE,
)
_LRC_TS_CAPTURE_RE = re.compile(r"\[(\d{2,}):(\d{2})(?:\.(\d+))?\]")
# Folga entre o ultimo verso e a duracao da faixa (arredondamentos, silencio).
_LAST_LINE_SLACK_S = 5
MAX_QUERY_VARIANTS = 4


def artist_variants(artist):
    """Artista exato primeiro; depois o artista principal (sem feat./lista)."""
    artist = (artist or "").strip()
    out = [artist] if artist else []
    if artist:
        main = _ARTIST_SPLIT_RE.split(artist, maxsplit=1)[0].strip()
        if main and main.casefold() not in {a.casefold() for a in out}:
            out.append(main)
    return out


def title_variants(track):
    """Titulo exato primeiro; depois sem (feat. ...) e sem sufixo de remaster."""
    track = (track or "").strip()
    out = [track] if track else []
    if not track:
        return out
    no_feat = _TITLE_FEAT_RE.sub("", track).strip()
    no_version = _TITLE_VERSION_PAREN_RE.sub("", _TITLE_VERSION_RE.sub("", track))
    clean = _TITLE_VERSION_PAREN_RE.sub("", _TITLE_VERSION_RE.sub("", no_feat)).strip()
    for cand in (no_feat, no_version.strip(), clean):
        if cand and cand.casefold() not in {t.casefold() for t in out}:
            out.append(cand)
    return out


def query_variants(artist, track):
    """Pares (artista, titulo) a tentar, do mais exato ao mais tolerante."""
    pairs = []
    for t in title_variants(track):
        for a in artist_variants(artist):
            if (a, t) not in pairs:
                pairs.append((a, t))
    # Ordem: exato, so artista limpo, so titulo limpo, ambos.
    return pairs[:MAX_QUERY_VARIANTS]


def lrc_fits_duration(text, duration):
    """False se o ultimo verso comeca DEPOIS do fim da faixa (versao errada).

    Sem duracao conhecida ou sem timestamps, nao ha o que checar: True.
    """
    try:
        duration = float(duration)
    except (TypeError, ValueError):
        return True
    if duration <= 0:
        return True
    last = None
    for m in _LRC_TS_CAPTURE_RE.finditer(text or ""):
        frac = (m.group(3) or "0").ljust(3, "0")[:3]
        secs = int(m.group(1)) * 60 + int(m.group(2)) + int(frac) / 1000
        last = secs if last is None else max(last, secs)
    return last is None or last <= duration + _LAST_LINE_SLACK_S


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


class LyricsResult(TypedDict):
    """Status devolvido por ``LyricsEngine.fetch_and_save`` ao chamador."""

    success: bool
    source: str | None
    language: str | None
    synchronized: bool
    bilingual: bool
    embedded: bool
    saved_external: bool
    error: str | None


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
    )
    # Prioridade de cada fonte (a mesma ordem de PROVIDERS). Fonte nova sem
    # entrada aqui conta como a pior e fica de fora quando `better_than` e' usado.
    PROVIDER_RANKS = {
        "_provider_qobuz": RANK_QOBUZ_SYNCED,
        "_provider_binilyrics": RANK_BINILYRICS,
        "_provider_musixmatch": RANK_MUSIXMATCH,
        "_provider_lrclib": RANK_LRCLIB,
    }
    # Varias faixas buscam letra ao mesmo tempo (modo paralelo): sem este lock,
    # cada thread pediria o seu proprio token ao Musixmatch de uma so' vez. Fica
    # na classe (e nao em __init__) para valer tambem em instancias criadas sem
    # __init__; so' serializa a obtencao do token, que acontece uma vez.
    _mxm_lock = threading.Lock()

    # Nome mostrado ao usuario quando uma fonte e' pulada por limite de requisicoes.
    PROVIDER_LABELS = {
        "_provider_qobuz": "Qobuz",
        "_provider_binilyrics": "BiniLyrics",
        "_provider_musixmatch": "Musixmatch",
        "_provider_lrclib": "LRCLIB",
    }

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
            self.session = make_sync_client(follow_redirects=True)
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
        """Converte linhas sincronizadas do Qobuz para LRC canonico.

        Linhas vazias ({"line": ""}) sao marcadores de pausa enviados pela
        propria Qobuz. O simbolo "♪ ♪ ♪" so e' emitido no meio do intervalo
        quando a duracao real entre o fim da linha anterior e o inicio da
        seguinte e' de pelo menos MIN_PAUSE_DURATION_MS.
        """
        lrc_rows = []
        prev_end = None
        pausa_pendente = False

        for entry in lines:
            start = entry.get("start")
            text = (entry.get("line") or "").strip()
            end = entry.get("end")

            # Linha vazia = marcador de pausa da Qobuz
            if start is None and not text:
                if prev_end is not None:
                    pausa_pendente = True
                continue

            if start is None:
                continue

            if pausa_pendente:
                # A linha vazia do Qobuz é necessária, mas não suficiente:
                # pausas curtas demais não justificam um símbolo no player.
                if prev_end is not None:
                    intervalo_ms = start - prev_end
                    if intervalo_ms >= MIN_PAUSE_DURATION_MS:
                        meio_ms = prev_end + intervalo_ms // 2
                        lrc_rows.append(f"{self._ms_to_lrc_timestamp(meio_ms)} ♪ ♪ ♪")

                pausa_pendente = False

            if text:
                lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)}{text}")

            if end is not None:
                prev_end = end
            elif text:
                prev_end = start

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

        # Ordena por timestamp e mantém a linha original antes da tradução.
        combined.sort(key=lambda x: (x[0], x[3]))

        result = []
        pausas_emitidas = set()
        for timestamp_ms, tag, text, _is_trans in combined:
            if not tag or not text:
                continue

            # A Qobuz pode enviar a linha vazia de pausa tanto na letra original
            # quanto na tradução. Como ambas são convertidas para LRC antes da
            # combinação, isso gera dois marcadores no mesmo instante. A pausa
            # é um evento único: conserva apenas a primeira ocorrência por tempo.
            if text == "♪ ♪ ♪":
                if timestamp_ms in pausas_emitidas:
                    continue
                pausas_emitidas.add(timestamp_ms)

            result.append(f"{tag}{text}")

        return "\n".join(result)

    def _inject_instrumental_pauses(self, lrc_text):
        """Pausas instrumentais agora vem das linhas vazias do proprio Qobuz.

        A antiga regra de intervalo > 10s foi desativada para não duplicar
        marcadores: as pausas explícitas vêm das linhas vazias do Qobuz e são
        convertidas por _qobuz_lines_to_lrc().
        """
        return lrc_text

    def _fetch_musixmatch_lyrics(
        self, artist, title, album=None, duration=None, isrc=None
    ):
        """Busca letras sincronizadas no Musixmatch (sincrono).

        `album`, `duration` (s) e `isrc` sao dicas opcionais que ajudam a achar
        a gravacao exata; sem elas a busca e' so por artista + titulo.
        """
        headers = {
            "x-mxm-app-version": "10.1.1",
            "User-Agent": "Musixmatch/2025120901 CFNetwork/1404.0.5 Darwin/22.3.0",
        }

        try:
            with self._mxm_lock:
                if not self._mxm_token:
                    resp_token = request_sync(
                        self.session,
                        "GET",
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
                            corpo = data_token["message"]["body"]
                            self._mxm_token = corpo["user_token"]

            if self._mxm_token:
                params = {
                    "q_artist": artist,
                    "q_track": title,
                    "format": "json",
                    "namespace": "lyrics_richsynched",
                    "usertoken": self._mxm_token,
                    "app_id": "mac-ios-v2.0",
                }
                if album:
                    params["q_album"] = album
                if duration:
                    params["q_duration"] = int(round(float(duration)))
                if isrc:
                    params["track_isrc"] = isrc

                resp_lyric = request_sync(
                    self.session,
                    "GET",
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
        except HttpBudgetExceeded:
            # Limite de requisições/prazo: quem chama (fetch_and_inject) pula
            # esta fonte em vez de repetir a mesma consulta com outras dicas.
            raise
        except Exception as e:
            logger.debug(f"Erro ao buscar no Musixmatch: {e}")
        return None

    # ------------------------------------------------------------------
    # Fontes de letra: cada `_provider_*` recebe um LyricsQuery e devolve um
    # LyricsCandidate (ou None). Excecoes de rede podem subir: quem chama
    # (fetch_and_inject) registra o erro e segue para a proxima fonte.
    # ------------------------------------------------------------------
    def _provider_qobuz(self, query):
        """Letra SINCRONIZADA enviada pela API do Qobuz (com traducao, se houver).

        Texto plano do Qobuz e' ignorado: sem timestamps, esta fonte nao serve.
        """
        lyrics = self.extract_qobuz_lyrics(
            query.qobuz_lyrics_response, query.qobuz_translation_response
        )
        if not lyrics:
            return None

        original_sync = lyrics.get("synced")
        if not original_sync:
            return None

        # So traducoes sincronizadas servem; prefere pt, senao a primeira.
        translations = [t for t in lyrics.get("translations", []) if t.get("synced")]
        orig_lang = str(lyrics.get("lang") or "unknown").lower()
        best = next(
            (t for t in translations if "pt" in str(t.get("language", "")).lower()),
            None,
        ) or (translations[0] if translations else None)

        if best:
            trans_lang = str(best.get("language") or "unknown").lower()
            lang_tag = f"{orig_lang}+{trans_lang}"
            final_sync = self._build_bilingual_lrc(original_sync, best["synced"])
        else:
            lang_tag = orig_lang
            final_sync = original_sync

        return LyricsCandidate(
            final_sync,
            "Qobuz",
            True,
            language=lang_tag,
            bilingual=bool(best),
        )

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
        response = request_sync(
            self.session,
            "GET",
            binilyrics.API_URL,
            params=params,
            headers=headers,
            timeout=10,
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

        ttml = request_sync(
            self.session, "GET", chosen["lyricsUrl"], headers=headers, timeout=15
        )
        if ttml.status_code != 200:
            return None
        doc = binilyrics.parse_ttml(ttml.text)

        # Letra plana (TTML sem tempos) nao serve.
        if doc.timing == "plain":
            return None

        target = getattr(self.settings, "lyrics_translation_lang", "pt")
        trans_lang = binilyrics.pick_translation_lang(doc, target or "")
        language = str(doc.language or "unknown").lower()
        bilingual = False

        # Por palavra so se o projeto pedir (settings.lyrics_word_sync):
        # a maioria dos players nao entende o LRC estendido.
        use_words = doc.timing == "word" and getattr(
            self.settings, "lyrics_word_sync", False
        )
        text = binilyrics.to_word_lrc(doc) if use_words else binilyrics.to_line_lrc(doc)
        if trans_lang:
            trans = binilyrics.translation_lrc(doc, trans_lang)
            if trans:
                text = self._build_bilingual_lrc(text, trans)
                bilingual = True

        if not has_lrc_timestamps(text):
            return None
        if bilingual:
            language = f"{language}+{trans_lang.lower()}"
        return LyricsCandidate(
            text,
            binilyrics.SOURCE_LABEL,
            True,
            language=language,
            bilingual=bilingual,
        )

    def _provider_musixmatch(self, query):
        """Musixmatch: so letra sincronizada (texto puro e' descartado).

        Usa album/duracao/ISRC como dicas; se com elas nao vier nada, repete so
        com artista + titulo e depois com as variantes limpas (sem feat. etc.).
        """
        hints = {"album": query.album, "duration": query.duration, "isrc": query.isrc}
        has_hints = any(hints.values())
        for artist, track in query_variants(query.artist, query.track):
            attempts = [hints, {}] if has_hints else [{}]
            for extra in attempts:
                text = self._fetch_musixmatch_lyrics(artist, track, **extra)
                if has_lrc_timestamps(text) and lrc_fits_duration(text, query.duration):
                    return LyricsCandidate(text, "Musixmatch", True)
        return None

    def _provider_lrclib(self, query):
        """LRCLIB: so `syncedLyrics`.

        Para cada variante (artista, titulo) tenta com album + duracao e repete
        sem o album. A duracao da faixa entra na busca e o resultado e'
        rejeitado se a duracao dele destoar (outra versao).
        """
        url = "https://lrclib.net/api/get"
        headers = {
            "User-Agent": (
                f"qobuz-dl-ultra/{__version__} "
                "(https://github.com/kaduvercosa/qobuz-dl-ultra)"
            )
        }
        try:
            duration = int(round(float(query.duration)))
        except (TypeError, ValueError):
            duration = None
        if duration is not None and duration <= 0:
            duration = None

        for artist, track in query_variants(query.artist, query.track):
            params = {"artist_name": artist, "track_name": track}
            if query.album:
                params["album_name"] = query.album
            if duration:
                params["duration"] = duration
            response = request_sync(
                self.session, "GET", url, params=params, headers=headers, timeout=12
            )
            if response.status_code != 200 and "album_name" in params:
                params = {k: v for k, v in params.items() if k != "album_name"}
                response = request_sync(
                    self.session, "GET", url, params=params, headers=headers, timeout=12
                )
            if response.status_code != 200:
                continue

            data = response.json() or {}
            synced = data.get("syncedLyrics")
            if not isinstance(synced, str) or not has_lrc_timestamps(synced):
                continue
            found = data.get("duration")
            if duration and found:
                try:
                    if abs(float(found) - duration) > binilyrics.DURATION_TOLERANCE_S:
                        continue
                except (TypeError, ValueError):
                    # Duração inválida no resultado: aceita a letra sem comparar.
                    pass
            if not lrc_fits_duration(synced, duration):
                continue
            return LyricsCandidate(synced, "LRCLIB", True)
        return None

    def _provider_genius(self, query):
        """Genius so tem texto puro: desativado (nunca devolve letra)."""
        return None

    # ------------------------------------------------------------------
    # Gravacao e mensagens (compartilhadas por todas as fontes)
    # ------------------------------------------------------------------
    @staticmethod
    def _delivery_message(candidate, embed_lyrics, save_lrc, ok, ident=""):
        """(tipo, mensagem) do resultado da gravação, no estilo da árvore.

        ``ident`` é o "[NN] título" da faixa: em modo paralelo a linha pode
        aparecer longe da que anunciou a busca, então ela se identifica.
        """
        nome = f" {ident}" if ident else ""
        ramo = f" {ui.tree_last()} "
        if not ok:
            return "error", ui.tail(
                f"{ramo}Falha ao gravar letras:{nome}",
                ui.join_meta("sincronizadas", f"via {candidate.source}"),
            )
        # O padrão (metadata + .lrc) não precisa ser repetido em toda linha:
        # só avisa quando a gravação foi parcial.
        if embed_lyrics and save_lrc:
            destino = ""
        elif save_lrc:
            destino = "só em .lrc"
        else:
            destino = "só no metadata"
        return ("ok" if embed_lyrics and save_lrc else "lyrics"), ui.tail(
            f"{ramo}Letras:{nome}",
            ui.join_meta(
                "BILINGUAL" if candidate.bilingual else "",
                "sincronizadas",
                destino,
                f"via {candidate.source}",
            ),
        )

    def _deliver(
        self, file_path, candidate, result, embed_lyrics, save_lrc, dry_run=False
    ):
        """Grava a letra (metadata e/ou arquivo) e preenche `result`.

        `dry_run=True` só preenche `result`, como se tivesse gravado: nada vai ao
        disco (usado pelo `lyrics --dry-run`).
        """
        result["synchronized"] = candidate.synchronized
        result["bilingual"] = candidate.bilingual
        result["language"] = candidate.language

        if dry_run:
            result["embedded"] = bool(embed_lyrics)
            result["saved_external"] = bool(save_lrc)
            result["success"] = True
            result["source"] = candidate.source
            return True

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
        quiet=False,
        better_than=None,
        buffer_output=False,
        network_budget=None,
        dry_run=False,
    ):
        """
        Percorre as fontes de LyricsEngine.PROVIDERS (Qobuz -> BiniLyrics ->
        Musixmatch -> LRCLIB) e grava a primeira letra SINCRONIZADA encontrada,
        respeitando as opcoes de saida. Letra plana e' sempre descartada. A traducao em portugues e priorizada quando estiver
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

        `quiet=True` omite as linhas de progresso/sucesso/aviso (quem chama,
        como o comando `lyrics`, monta a propria saida a partir do retorno).
        Linhas de ERRO continuam sendo impressas.

        `better_than` (prioridade, 1 a 5): so consulta fontes MELHORES que ela.
        Usado pelo comando `lyrics` para trocar uma letra de fonte inferior sem
        nunca gravar uma pior; sem resultado, o arquivo nao e' tocado.

        `buffer_output=True` (modo paralelo): guarda as linhas desta faixa e as
        escreve juntas, de uma vez, no fim -- sem ficar segurando o
        `print_lock` durante a rede, o que travava as outras faixas e o loop.

        `network_budget` (segundos, por fonte): limita quanto cada fonte pode
        ESPERAR (cooldown de 429, novas tentativas). Passando do combinado, a
        fonte e' pulada e a proxima e' tentada, em vez de dormir ate 60 s.
        `None` (padrao) mantem o comportamento de esperar o cooldown inteiro.
        """
        saida = []

        def _escrever(linhas):
            if buffer_output:
                saida.extend(linhas)
                return
            for linha in linhas:
                tqdm.write(linha)

        try:
            return self._fetch_and_inject(
                _escrever,
                network_budget,
                file_path,
                artist,
                track,
                album,
                save_lrc=save_lrc,
                embed_lyrics=embed_lyrics,
                qobuz_lyrics_response=qobuz_lyrics_response,
                qobuz_translation_response=qobuz_translation_response,
                track_number=track_number,
                isrc=isrc,
                duration=duration,
                quiet=quiet,
                better_than=better_than,
                dry_run=dry_run,
            )
        finally:
            if saida:
                # Um bloco so' por faixa: o lock e' tomado por um instante, so'
                # para as linhas nao se misturarem com as de outras faixas.
                with ui.print_lock:
                    for linha in saida:
                        tqdm.write(linha)

    def _fetch_and_inject(
        self,
        _escrever,
        network_budget,
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
        quiet=False,
        better_than=None,
        dry_run=False,
    ):
        """Corpo de `fetch_and_inject`; `_escrever(linhas)` e' o destino das linhas."""
        # "[NN] título": identifica a faixa em cada linha (modo paralelo).
        _ident = f"[{track_number}] {track}" if track_number else f"{track}"

        def _tw(kind, msg):
            """Escreve a mensagem no estilo unificado (tag + árvore + cinza).

            Formata com `ui.tagged_lines` (sem escrever) e entrega ao destino:
            direto no `tqdm.write` ou guardada até o fim (`buffer_output`).
            Nunca toma o `print_lock` aqui (não reentrante), então também não
            pode chamar `ui.ok()`/`ui.step()`.
            """
            if quiet and kind != "error":
                return
            _escrever(ui.tagged_lines(kind, msg))

        result: LyricsResult = {
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
            qobuz_lyrics_response=qobuz_lyrics_response,
            qobuz_translation_response=qobuz_translation_response,
            isrc=isrc,
            duration=duration,
        )

        _tw("search", f"Procurando letras: {_ident}")
        last_error = None
        # Fontes puladas por limite de requisicoes/prazo (nomes para o aviso).
        limitadas: list[str] = []

        for provider_name in self.PROVIDERS:
            if (
                better_than is not None
                and self.PROVIDER_RANKS.get(provider_name, RANK_PLAIN) >= better_than
            ):
                continue
            try:
                # Cada fonte tem o proprio orcamento de espera: uma fonte
                # limitada nao consome o tempo das seguintes.
                orcamento = (
                    http_budget(
                        network_budget,
                        max_cooldown_wait=min(LYRICS_MAX_COOLDOWN_WAIT, network_budget),
                    )
                    if network_budget
                    else contextlib.nullcontext()
                )
                with orcamento:
                    candidate = getattr(self, provider_name)(query)
                # Barreira final: so letra sincronizada passa, qualquer que
                # seja a fonte (inclusive as adicionadas no futuro).
                if candidate is not None and not (
                    candidate.synchronized and has_lrc_timestamps(candidate.text)
                ):
                    candidate = None
                if candidate is not None:
                    candidate.text = self._inject_instrumental_pauses(candidate.text)
            except HttpBudgetExceeded as e:
                # Fonte em cooldown (429) ou sem prazo: pula para a proxima em
                # vez de esperar. Nao conta como erro da faixa.
                rotulo = self.PROVIDER_LABELS.get(provider_name, provider_name)
                if rotulo not in limitadas:
                    limitadas.append(rotulo)
                logger.debug(f"fonte {provider_name} pulada para {track}: {e}")
                continue
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
            if limitadas:
                _tw(
                    "warn",
                    ui.tail(
                        f" {ui.tree_mid()} Letras: {_ident}",
                        ui.join_meta(
                            f"limite de requisições em {', '.join(limitadas)}",
                            "usando outra fonte",
                        ),
                    ),
                )
            try:
                ok = self._deliver(
                    file_path, candidate, result, embed_lyrics, save_lrc, dry_run
                )
                _tw(
                    *self._delivery_message(
                        candidate, embed_lyrics, save_lrc, ok, _ident
                    )
                )
            except Exception as e:
                _tw(
                    "error",
                    ui.tail(
                        f" {ui.tree_last()} Erro na pesquisa de letras: {_ident}",
                        str(e),
                    ),
                )
                logger.debug(f"gravacao falhou para {track}: {e}", exc_info=True)
                result["error"] = str(e)
            return result

        if last_error is not None:
            _tw(
                "error",
                ui.tail(
                    f" {ui.tree_last()} Erro na pesquisa de letras: {_ident}",
                    str(last_error),
                ),
            )
            result["error"] = str(last_error)
        elif limitadas:
            # Nao achou E houve fonte pulada: pode existir letra la. Avisa para
            # rodar de novo depois (o comando `lyrics` refaz so' as faltantes).
            _tw(
                "warn",
                ui.tail(
                    f" {ui.tree_last()} Nenhuma letra encontrada: {_ident}",
                    f"limite de requisições em {', '.join(limitadas)}",
                ),
            )
        else:
            _tw("warn", f" {ui.tree_last()} Nenhuma letra encontrada: {_ident}")
        return result

    def _save_lrc_file(
        self, audio_file_path, synced_lyrics, source=None, language=None
    ):
        """
        Salva a letra sincronizada em .lrc com fonte e idioma no cabecalho.
        Retorna True/False para indicar sucesso. Letra plana (sem timestamps)
        nunca e' salva: devolve False sem tocar no disco.
        """
        if not has_lrc_timestamps(synced_lyrics):
            logger.debug("Letra plana recusada: .lrc nao gravado")
            return False
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
        if not has_lrc_timestamps(lyrics):
            logger.debug("Letra plana recusada: metadata nao alterado")
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
                if source:
                    # Origem tambem no COMMENT (junto do Trk ID e da capa).
                    audio["COMMENT"] = lyrics_comment.merge_flac(
                        audio.get("COMMENT"),
                        lyrics_comment.build_line(
                            source, bilingual=bilingual, language=language
                        ),
                    )
                audio.save()
            elif ext == ".mp3":
                try:
                    audio_id3 = ID3(file_path)
                except ID3NoHeaderError:
                    audio_id3 = ID3()
                desc = source if source else ""
                # O frame USLT e' identificado por (descricao, idioma): sem apagar
                # os antigos, uma letra de outra fonte (descricao diferente) ficava
                # ao lado da nova e podia ser lida no lugar dela.
                audio_id3.delall("USLT")
                audio_id3.add(USLT(encoding=3, lang="eng", desc=desc, text=lyrics))
                if language:
                    audio_id3.delall("TXXX:LYRICS_LANG")
                    audio_id3.add(TXXX(encoding=3, desc="LYRICS_LANG", text=language))
                audio_id3.delall("TXXX:LYRICS_BILINGUAL")
                audio_id3.add(
                    TXXX(
                        encoding=3,
                        desc="LYRICS_BILINGUAL",
                        text="1" if bilingual else "0",
                    )
                )
                if source:
                    linha_letra = lyrics_comment.build_line(
                        source, bilingual=bilingual, language=language
                    )
                    comm = next(
                        (
                            f
                            for f in audio_id3.getall("COMM")
                            if any(str(t).strip() for t in (f.text or []))
                        ),
                        None,
                    )
                    if comm is not None:
                        atual = next((str(t) for t in comm.text if str(t).strip()), "")
                        comm.text = [lyrics_comment.merge_mp3(atual, linha_letra)]
                    else:
                        audio_id3.add(
                            COMM(encoding=3, lang="eng", desc="", text=[linha_letra])
                        )
                audio_id3.save(file_path)
            else:
                return False
            return True
        except Exception as e:
            for linha in ui.tagged_lines(
                "error",
                ui.tail(
                    f" {ui.tree_last()} Falha ao gravar a letra no metadata de "
                    f"{os.path.basename(file_path)}",
                    str(e),
                ),
            ):
                tqdm.write(linha)

            logger.debug(f"_inject_metadata falhou em {file_path}: {e}", exc_info=True)
            return False
