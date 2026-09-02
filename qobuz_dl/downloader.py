# ============================================================================
# downloader.py -- motor de download do qobuz_dl.
# Aqui mora a classe Download (baixa album/faixa/playlist), a logica de
# fallback de qualidade, o download segmentado (bypass de bloqueio Akamai)
# e as funcoes de apoio (capa, booklet, letras, nomes de arquivo/pasta).
# Ponto de entrada tipico: Download(...).download_id_by_type(...).
# ============================================================================
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.constants import (
    DEFAULT_FOLDER,
    DEFAULT_TRACK,
    DEFAULT_MULTIPLE_DISC_TRACK,
)
from qobuz_dl.db import handle_download_id
from qobuz_dl.utils import (
    get_album_artist,
    clean_filename,
    verify_audio_integrity,
    classify_release_type,
    get_apple_hq_cover,
)
from .lyrics_engine import LyricsEngine
import qobuz_dl.postprocess as postprocess
import logging
import os
import shutil
import sys
import time
import re
import threading
import signal
import textwrap
from typing import Optional, Tuple
import asyncio

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathvalidate import sanitize_filename, sanitize_filepath
from tqdm import tqdm

import qobuz_dl.metadata as metadata
from qobuz_dl import ui
from qobuz_dl.color import (
    OFF,
    GREEN,
    RED,
    WARNING as YELLOW,
    INFO as CYAN,
    RESET,
    MUTED,
)
from qobuz_dl.exceptions import NonStreamable

import aiofiles
from tenacity import (
    AsyncRetrying,
    stop_after_attempt,
    wait_exponential,
    retry_if_not_exception_type,
)

# Ordem de fallback de qualidade quando o tier pedido falha por motivo de rede/servidor (NAO usado para faixas indisponiveis -- ver _PermanentDownloadError). 27=Hi-Res >96kHz | 7=Hi-Res 96kHz | 6=CD 16bit/44.1kHz | 5=MP3 320kbps
FALLBACK_TIERS = [27, 7, 6, 5]


def is_track_streamable(track: dict) -> tuple[bool, str]:
    """
    [OPÇÃO A] Checagem prévia se a faixa está liberada para streaming completo.
    """
    streamable = track.get("streamable", False)
    sampleable = track.get("sampleable", False)
    purchasable = track.get("purchasable", False)

    if not streamable:
        if sampleable:
            return False, "Apenas amostra/demo (30s)"
        elif purchasable:
            return False, "Disponível apenas para compra avulsa"
        return False, "Não disponível na região"

    return True, ""


def create_missing_placeholder(track: dict, folder_path: str, reason: str):
    """
    [OPÇÃO C] Cria o arquivo .missing.txt na pasta do álbum
    """
    try:
        track_num = str(track.get("track_number", 0)).zfill(2)
        title = track.get("title", "Faixa").replace("/", "-").replace("\\", "-")
        artist = track.get("performer", {}).get(
            "name", track.get("artist", {}).get("name", "Desconhecido")
        )

        filename = f"{track_num}. {title} [INDISPONÍVEL].missing.txt"
        file_path = os.path.join(folder_path, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"Faixa: {track_num}. {title}\n")
            f.write(f"Artista: {artist}\n")
            f.write(f"Duração: {track.get('duration', 0)}s\n")
            f.write(f"Motivo: {reason}\n")
            f.write("Status: Faixa indisponível para streaming na conta/região.\n")
    except Exception:
        pass


class _PermanentDownloadError(Exception):
    """
    Erro de download que NÃO deve ser tentado de novo -- 401 (nao
    autenticado), 403 (sem licenca/regiao bloqueada), 404 (não existe),
    451 (indisponivel por motivo legal). Tentar de novo um desses e'
    tempo perdido, sempre vai falhar do mesmo jeito -- diferente de um
    timeout ou erro 5xx, que pode ser so' uma soneca passageira do
    servidor.
    """

    pass


print_lock = ui.print_lock

abort_event = threading.Event()


def _get_safe_ncols():
    """Delegado para ``ui.progress_ncols()`` (fonte unica de largura)."""
    return ui.progress_ncols()


def _desc_budget(ncols):
    FIXED_OVERHEAD = 2 + 4 + 1 + 1 + 1 + 13
    MIN_BAR = 6
    return max(6, min(30, ncols - FIXED_OVERHEAD - MIN_BAR - 1))


class _PositionPool:
    def __init__(self, size):
        self._lock = threading.Lock()
        self._free = list(range(max(size, 1)))
        self.ncols = _get_safe_ncols()
        self.desc_len = _desc_budget(self.ncols)

    def acquire(self):
        with self._lock:
            if self._free:
                return self._free.pop(0)
            return 0

    def release(self, pos):
        with self._lock:
            if pos not in self._free:
                self._free.append(pos)
                self._free.sort()


_dir_locks: dict = {}


def _get_dir_lock(dirn: str) -> asyncio.Lock:
    return _dir_locks.setdefault(dirn, asyncio.Lock())


def safe_print(*args, **kwargs):
    """
    Funcao de print thread-safe. Evita falhas visuais na interface
    ("guerra de cursores") quando varias threads de download tentam
    escrever no terminal ao mesmo tempo.

    Delega para ``ui.emit()``, o unico ponto real de escrita no terminal
    do projeto -- assim ``--quiet``, deteccao de TTY e o fallback para
    terminais nao-UTF-8 valem tambem aqui.
    """
    text = " ".join(map(str, args))
    ui.emit(text, end=kwargs.get("end", "\n"))


def print_download_header(kind: str, rows: list) -> None:
    """
    Cabecalho padronizado impresso uma unica vez no inicio de qualquer
    download (album, faixa unica, playlist ou lote de urls).
    """
    ui.header(kind, rows)


def emit_progress_json(settings, event, **fields):
    """
    Emite uma linha JSON em stdout descrevendo um evento de progresso
    (inicio/fim de faixa), para frontends (GUI web, app) conseguirem
    acompanhar downloads sem parsear a barra tqdm/ANSI no terminal.
    So' emite algo se settings.progress_json estiver ligado.
    """
    if not getattr(settings, "progress_json", False):
        return
    import json as _json

    payload = {"event": event, "ts": time.time(), **fields}
    with print_lock:
        print(_json.dumps(payload, ensure_ascii=False), flush=True)


def _build_letras_report(
    resultado: Optional[dict],
    translation_lang: Optional[str],
    qobuz_translation_response,
) -> dict:
    """
    Traduz o dict de retorno de LyricsEngine.fetch_and_inject() pro
    schema "letras" do report.json (situacao/sincronizada/bilingue/
    idioma_original/traducao_disponivel/fonte/destino/observacao).

    `qobuz_translation_response` decide "traducao_disponivel" -- e o
    resultado da consulta de traducao feita ANTES de chamar fetch_and_inject
    (ver _download_and_tag), mais confiavel que tentar deduzir isso do
    "language" combinado (ex.: "en+pt") que fetch_and_inject devolve,
    porque continua valendo mesmo quando a faixa acabou saindo por outra
    fonte (Musixmatch/LRCLIB/Genius, que nao sabem de traducao).
    """
    if not resultado:
        return {}

    if resultado.get("success"):
        situacao = "sucesso"
    elif resultado.get("error"):
        situacao = "falha"
    else:
        situacao = "nao_encontrada"

    idioma_original = ""
    lang = resultado.get("language")
    if lang and lang != "unknown":
        idioma_original = lang.split("+")[0]

    destino_partes = []
    if resultado.get("embedded"):
        destino_partes.append("metadata")
    if resultado.get("saved_external"):
        destino_partes.append(".lrc/.txt")

    return {
        "situacao": situacao,
        "sincronizada": bool(resultado.get("synchronized")),
        "bilingue": bool(resultado.get("bilingual")),
        "idioma_original": idioma_original,
        "traducao_disponivel": (
            bool(qobuz_translation_response) if translation_lang else None
        ),
        "fonte": resultado.get("source") or "",
        "destino": " + ".join(destino_partes),
        "observacao": resultado.get("error") or "",
    }


def format_release_type(
    api_release_type: str,
    track_count=0,
    title=None,
    version=None,
    duration_seconds=0,
) -> str:
    """
    Decide o texto final de {release_type} usado no nome da pasta
    (DEFAULT_FOLDER). Usa a MESMA classificacao unificada usada na busca/TUI
    (`classify_release_type`, em utils.py) -- antes esta funcao so confiava
    cegamente na tag "release_type" da API da Qobuz, que vem errada com
    frequencia (ex.: um lancamento de 5 faixas marcado "Single" pela
    gravadora ia pra pasta "Single/" em vez de "EP/"). Agora a contagem real
    de faixas manda (regra: <=3 Single, 4-7 EP, >7 Album), com prioridade
    pra palavras-chave explicitas no titulo/versao (live, compilation etc.).
    """
    tipo = classify_release_type(
        title=title,
        version=version,
        track_count=track_count,
        duration_seconds=duration_seconds,
        api_release_type=api_release_type,
    )
    if not tipo or tipo == "unknown":
        return "Desconhecido"
    if tipo == "ep":
        return "EP"
    return tipo.title()


legacy_flag = False


def process_folder_format_with_subdirs(
    folder_format, attr_dict, path=None, legacy_charmap=False
):
    path_parts = folder_format.split("/")
    cleaned_parts = []
    for part in path_parts:
        if not part:
            continue
        try:
            formatted_part = part.format(**attr_dict)
            cleaned_part = sanitize_filepath(
                clean_filename(formatted_part, legacy_charmap=legacy_charmap),
                replacement_text="_",
            )
            if cleaned_part and len(cleaned_part) > 120:
                start_f = cleaned_part[:60].rstrip(" .\"-_'")
                end_f = cleaned_part[-50:].lstrip(" .\"-_'")
                cleaned_part = f"{start_f}...{end_f}"
            if cleaned_part:
                cleaned_parts.append(cleaned_part)
        except KeyError as e:
            logger.warning(
                f"{YELLOW}Erro de formato ({e}), usando texto original.{OFF}"
            )
            cleaned_part = sanitize_filepath(
                clean_filename(part, legacy_charmap=legacy_charmap),
                replacement_text="_",
            )
            if cleaned_part and len(cleaned_part) > 120:
                start_f = cleaned_part[:60].rstrip(" .\"-_'")
                end_f = cleaned_part[-50:].lstrip(" .\"-_'")
                cleaned_part = f"{start_f}...{end_f}"
            if cleaned_part:
                cleaned_parts.append(cleaned_part)

    final_path = os.path.join(*cleaned_parts) if cleaned_parts else ""
    if path is not None:
        return os.path.join(path, final_path)
    return final_path


QL_DOWNGRADE = "FormatRestrictedByFormatAvailability"
DEFAULT_FORMATS = {
    "MP3": [
        "{album_artist} - {album_title} ({year}) [MP3]",
        "{track_number} - {track_title}",
    ],
    "Unknown": [
        "{album_artist} - {album_title}",
        "{track_number} - {track_title}",
    ],
}

EMB_COVER_NAME = "embed_cover.jpg"

# Limite de tamanho pra capa (bytes). Capa da Apple em 10000x10000 pode vir pesada demais em raras excecoes (albuns com arte muito detalhada); acima disso a gente reduz a resolucao em cascata em vez de usar essa versao. 16MB cobre folgado o tamanho tipico de uma APIC embutida sem deixar o arquivo de audio inchado por causa da capa.
MAX_COVER_BYTES = 16 * 1024 * 1024

# Cascata de resolucoes da Apple, da maior pra menor, usada quando a 10000x10000bb estoura MAX_COVER_BYTES. artworkUrl100 troca livremente "100x100bb" por qualquer "NxNbb" na URL.
_APPLE_COVER_SIZES = [
    "10000x10000bb",
    "6000x6000bb",
    "3000x3000bb",
    "1200x1200bb",
    "600x600bb",
]

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class Download:
    """
    O motor principal de Download, responsavel por buscar os arquivos de
    audio, booklets e metadados. Uma instancia = 1 item (album, faixa ou
    faixa de playlist). Ponto de entrada publico: download_id_by_type().
    """

    def __init__(
        self,
        client,
        item_id: str,
        path: str,
        quality: int,
        embed_art: bool = False,
        albums_only: bool = False,
        downgrade_quality: bool = False,
        cover_og_quality: bool = False,
        no_cover: bool = False,
        folder_format=None,
        track_format=None,
        fetch_lyrics: bool = False,
        no_lrc_files: bool = False,
        genius_token: str = None,
        no_credits: bool = False,
        settings: QobuzDLSettings = None,
        download_db=None,
        is_playlist: bool = False,
        playlist_track_number: int = None,
        booklet_only: bool = False,
        playlist_as_albums: bool = False,
        playlist_title: str = None,
        playlist_id: str = None,
    ):
        self.client = client
        self.item_id = item_id
        self.path = path
        self.quality = quality
        self.albums_only = albums_only
        self.embed_art = embed_art
        self.downgrade_quality = downgrade_quality
        self.cover_og_quality = cover_og_quality
        self.no_cover = no_cover
        self.folder_format = folder_format or DEFAULT_FOLDER
        self.track_format = track_format or DEFAULT_TRACK
        self.no_credits = no_credits
        self.booklet_only = booklet_only

        self.http_session = httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
                "Connection": "keep-alive",
            },
        )

        self.settings = settings or QobuzDLSettings()
        self.download_db = download_db

        self.fetch_lyrics = fetch_lyrics
        self.no_lrc_files = no_lrc_files
        if self.fetch_lyrics:
            self.lyrics_engine = LyricsEngine(
                genius_token,
                settings=self.settings,
            )

        self.settings = settings or QobuzDLSettings()
        self.download_db = download_db

        self.is_playlist = is_playlist
        self.playlist_track_number = playlist_track_number
        self.playlist_as_albums = playlist_as_albums
        self.playlist_title = playlist_title
        self.playlist_id = playlist_id

        self._original_folder_format = folder_format or DEFAULT_FOLDER
        self._original_track_format = track_format or DEFAULT_TRACK
        self._original_multiple_disc_track_format = (
            settings.multiple_disc_track_format
            if settings
            else DEFAULT_MULTIPLE_DISC_TRACK
        )

    async def download_id_by_type(
        self, track=True, is_parallel=False, position_pool=None, suppress_header=False
    ):
        self.folder_format = self._original_folder_format
        self.track_format = self._original_track_format
        if self.settings:
            self.settings.multiple_disc_track_format = (
                self._original_multiple_disc_track_format
            )

        try:
            if not track:
                await self.download_release(suppress_header=suppress_header)
            else:
                await self.download_track(
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                    suppress_header=suppress_header,
                )
        finally:
            await self.close_session()

    async def close_session(self):
        if hasattr(self, "lyrics_engine"):
            try:
                self.lyrics_engine.close()
            except Exception as e:
                logger.debug(f"Falha ao fechar lyrics_engine (ignorado): {e}")

        session = getattr(self, "http_session", None)
        if session is not None:
            try:
                await session.aclose()
            except Exception as e:
                logger.debug(f"Falha ao fechar http_session (ignorado): {e}")

    async def download_release(self, suppress_header=False):
        album_meta = await self.client.get_album_meta(self.item_id)

        if not album_meta.get("streamable"):
            raise NonStreamable("Este lançamento nao está disponível para streaming")

        if self.albums_only and (
            album_meta.get("release_type") != "album"
            or album_meta.get("artist").get("name") == "Various Artists"
        ):
            safe_print(f'{OFF}Ignorando Single/EP/VA: {album_meta.get("title", "n/a")}')
            return

        album_title = _get_title(album_meta)
        url = album_meta.get("url", "")
        release_date = album_meta.get("release_date_original", "")

        format_info = await self._get_format(album_meta)
        file_format, quality_met, bit_depth, sampling_rate = format_info

        if not self.downgrade_quality and not quality_met:
            safe_print(
                f"{OFF}[-] Pulando {album_title} pois não atende ao requisito de qualidade{OFF}"
            )
            return

        track_count = len(album_meta.get("tracks", {}).get("items", []))
        artist_name = _safe_get(album_meta, "artist", "name", default="")
        # Usado so pro report.json (cabecalho + campo por faixa) -- mais
        # preciso que `artist_name` pra releases com mais de um main-artist
        # oficial, mas nao mexe em nomeacao de pasta/arquivo/tags, que
        # continuam usando `artist_name` como sempre usaram.
        report_artist_name = _artist_label(album_meta, fallback=artist_name)
        # Classificacao Single/EP/Album do release (regra por contagem real
        # de faixas -- ver `classify_release_type` em utils.py). Como e o
        # MESMO release pra toda faixa deste download, calcula uma vez so
        # e usa tanto no cabecalho quanto (repetido) em cada faixa.
        report_tipo_lancamento = format_release_type(
            album_meta.get("release_type"),
            track_count=album_meta.get("track_count", track_count),
            title=album_title,
            version=album_meta.get("version"),
            duration_seconds=album_meta.get("duration"),
        )
        release_year = str(album_meta.get("release_date_original", ""))[:4]

        album_attr = self._get_album_attr(
            album_meta, album_title, file_format, bit_depth, sampling_rate
        )

        self._determine_formats(
            album_meta=album_meta,
            album_attr=album_attr,
            tracks_meta=album_meta["tracks"]["items"],
            track_attr=None,
            is_track=False,
            file_format=file_format,
            settings=self.settings,
        )

        legacy_flag = (
            getattr(self.settings, "legacy_charmap", False)
            if hasattr(self, "settings")
            else False
        )

        target_dirn = process_folder_format_with_subdirs(
            self.folder_format, album_attr, self.path, legacy_charmap=legacy_flag
        )

        base_path, folder_name = os.path.split(target_dirn)

        incomplete_dirn = os.path.join(base_path, f"[INCOMPLETE] {folder_name}")
        inprogress_dirn = os.path.join(base_path, f"[IN PROGRESS] {folder_name}")

        is_standard_album = not getattr(self, "is_playlist", False)

        if is_standard_album:
            working_dirn = inprogress_dirn
            try:
                if os.path.exists(incomplete_dirn):
                    os.rename(incomplete_dirn, working_dirn)
                elif os.path.exists(target_dirn):
                    os.rename(target_dirn, working_dirn)
            except OSError as e:
                safe_print(
                    f"{YELLOW}[!] Não foi possível renomear a pasta existente para [IN PROGRESS]. "
                    f"Operando em modo padrão. ({e}){OFF}"
                )
                working_dirn = target_dirn
        else:
            working_dirn = target_dirn

        os.makedirs(working_dirn, exist_ok=True)
        dirn = working_dirn

        media_count = album_meta.get("media_count", 1)
        is_multiple = True if media_count > 1 else False

        delay_time = getattr(self.settings, "delay", 0)
        if delay_time == 0 and "--delay" in sys.argv:
            try:
                delay_time = int(sys.argv[sys.argv.index("--delay") + 1])
            except (ValueError, IndexError) as e:
                logger.debug(f"--delay com valor invalido, ignorando: {e}")

        active_workers = int(getattr(self.settings, "max_workers", 1))
        is_parallel = False
        mode_label = "Sequencial"

        if delay_time > 0:
            active_workers = 1
            mode_label = "Sequencial (Safety Delay ativo)"
        elif active_workers > 1 and track_count > 1:
            is_parallel = True
            mode_label = f"Paralelo ({active_workers} workers)"

        if not suppress_header:
            print_download_header(
                "ÁLBUM",
                [
                    ("Álbum", album_title),
                    ("Artista", artist_name),
                    ("Ano", release_year or "--"),
                    ("Faixas", str(track_count)),
                    (
                        "Qualidade",
                        (
                            f"{file_format} ({bit_depth}bit/{float(sampling_rate):g}kHz)"
                            if bit_depth
                            else file_format
                        ),
                    ),
                    ("Modo", mode_label),
                ],
            )

        position_pool = _PositionPool(active_workers) if is_parallel else None

        failed_tracks = 0
        aborted_by_user = False
        abort_event.clear()

        original_sigint = None
        try:
            original_sigint = signal.getsignal(signal.SIGINT)

            def custom_sigint_handler(sig, frame):
                abort_event.set()
                raise KeyboardInterrupt

            signal.signal(signal.SIGINT, custom_sigint_handler)
        except Exception as e:
            logger.debug(f"Nao foi possivel instalar handler de SIGINT: {e}")

        try:
            self._generate_tracklist(
                album_meta, dirn, album_title, file_format, bit_depth, sampling_rate
            )

            if self.settings.no_cover:
                safe_print(f"{OFF}[*] Pulando capa{OFF}")

            if self.settings.no_cover and not self.settings.embed_art:
                pass
            else:
                await _get_cover_and_embed(
                    album_meta["image"]["large"],
                    dirn,
                    save_cover=not self.settings.no_cover,
                    embed_art=self.settings.embed_art,
                    saved_name="cover.jpg",
                    embed_name=EMB_COVER_NAME,
                    saved_art_size=self.settings.saved_art_size,
                    embedded_art_size=self.settings.embedded_art_size,
                    session=self.http_session,
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                    artist=_artist_label(
                        album_meta,
                        fallback=album_meta.get("artist", {}).get("name", ""),
                    ),
                    album=album_meta.get("title", ""),
                    upc=album_meta.get("upc", ""),
                )

            if "goodies" in album_meta:
                await _download_goodies(
                    album_meta,
                    dirn,
                    session=self.http_session,
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                )

            if getattr(self, "booklet_only", False):
                safe_print(
                    f"    {YELLOW}[*] Flag --booklet-only ativa. Pulando faixas de audio.{OFF}"
                )
                if is_standard_album and working_dirn == inprogress_dirn:
                    try:
                        os.rename(working_dirn, incomplete_dirn)
                    except OSError as e:
                        logger.warning(
                            f"    {YELLOW}[!] Não foi possível renomear a pasta para [INCOMPLETE]. ({e}){OFF}"
                        )
                return

            semaphore = asyncio.Semaphore(active_workers)

            async def _report_track(i, t_num, status, motivo="", letras=None):
                await postprocess.update_track_status(
                    dirn,
                    numero=t_num,
                    item_id=i.get("id"),
                    titulo=i.get("title", "Faixa Desconhecida"),
                    status=status,
                    artista=_artist_label(
                        i,
                        fallback=_safe_get(
                            i, "performer", "name", default=report_artist_name
                        ),
                    ),
                    artista_album=report_artist_name,
                    tipo_lancamento=report_tipo_lancamento,
                    motivo=motivo,
                    isrc=i.get("isrc", ""),
                    compositor=_safe_get(i, "composer", "name", default=""),
                    letras=letras,
                )

            async def process_track(idx, i):
                if abort_event.is_set():
                    return False
                async with semaphore:
                    t_num = str(i.get("track_number", idx + 1)).zfill(2)
                    t_title = i.get("title", "Faixa Desconhecida")

                    streamable, reason = is_track_streamable(i)
                    if not streamable:
                        safe_print(
                            f"    {CYAN}[PULADA]{RESET} Faixa {t_num} - {t_title} ({YELLOW}{reason}{RESET})"
                        )
                        create_missing_placeholder(i, dirn, reason)
                        await _report_track(i, t_num, "pulada", reason)
                        return "skipped"

                    try:
                        parse = await self.client.get_track_url(
                            i["id"], fmt_id=self.quality
                        )
                    except Exception as e:
                        safe_print(
                            f"{RED}[!] Erro de API na faixa {t_num} (ID: {i['id']}): {e}{OFF}"
                        )
                        create_missing_placeholder(i, dirn, f"Erro de API: {e}")
                        await _report_track(i, t_num, "falha", f"Erro de API: {e}")
                        return False

                    if "sample" not in parse and parse.get("sampling_rate"):
                        is_mp3 = True if int(self.quality) == 5 else False
                        letras_info = {}
                        res = await self._download_and_tag(
                            dirn,
                            idx,
                            parse,
                            i,
                            album_meta,
                            False,
                            is_mp3,
                            i.get("media_number") if is_multiple else None,
                            is_parallel=is_parallel,
                            position_pool=position_pool,
                            letras_out=letras_info,
                        )
                        status = "ok" if res is True else "falha"
                        motivo = (
                            ""
                            if res is True
                            else (str(res) if isinstance(res, Exception) else "erro")
                        )
                        await _report_track(
                            i, t_num, status, motivo, letras=letras_info
                        )
                        return res
                    else:
                        safe_print(
                            f"{CYAN}[PULADA]{RESET} Faixa {t_num} - {t_title} "
                            f"({YELLOW}Apenas amostra/demo{RESET})"
                        )
                        create_missing_placeholder(i, dirn, "Apenas amostra/demo (30s)")
                        await _report_track(
                            i, t_num, "pulada", "Apenas amostra/demo (30s)"
                        )
                        return "skipped"

            faixas_previstas = []
            for idx, i in enumerate(album_meta["tracks"]["items"]):
                faixas_previstas.append(
                    {
                        "numero": str(i.get("track_number", idx + 1)).zfill(2),
                        "id": i.get("id"),
                        "titulo": i.get("title", "Faixa"),
                        "artista": _artist_label(
                            i,
                            fallback=_safe_get(
                                i, "performer", "name", default=report_artist_name
                            ),
                        ),
                        "artista_album": report_artist_name,
                        "tipo_lancamento": report_tipo_lancamento,
                        "isrc": i.get("isrc", ""),
                        "compositor": _safe_get(i, "composer", "name", default=""),
                    }
                )
            await postprocess.init_report(
                dirn,
                tipo="album",
                titulo=album_title,
                artista=report_artist_name,
                tipo_lancamento=report_tipo_lancamento,
                item_id=self.item_id,
                extra={
                    "rotulo": _safe_get(album_meta, "label", "name", default=""),
                    "genero": _safe_get(album_meta, "genre", "name", default=""),
                    "upc": album_meta.get("upc", ""),
                    "url": url,
                },
                qualidade={
                    "formato": file_format,
                    "bit_depth": bit_depth,
                    "sampling_rate": sampling_rate,
                },
                faixas_previstas=faixas_previstas,
            )

            task_objs = [
                asyncio.create_task(process_track(idx, i))
                for idx, i in enumerate(album_meta["tracks"]["items"])
            ]

            try:
                results = await asyncio.gather(*task_objs, return_exceptions=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                abort_event.set()
                aborted_by_user = True
                for t in task_objs:
                    if not t.done():
                        t.cancel()
                try:
                    self.http_session.close()
                except Exception:
                    pass
                raise

            for res in results:
                if res is False or isinstance(res, Exception):
                    failed_tracks += 1

            if not abort_event.is_set():
                _clean_embed_art(dirn, self.settings)
                if getattr(self, "fetch_lyrics", False) and not self.no_credits:
                    self._append_lyrics_to_booklet(dirn, album_title)

        except (KeyboardInterrupt, SystemExit):
            abort_event.set()
            aborted_by_user = True
            safe_print(
                f"\n{RED}[!] CTRL+C Interceptado: Protegendo arquivos e pastas...{OFF}"
            )
        finally:
            try:
                if original_sigint:
                    signal.signal(signal.SIGINT, original_sigint)
            except Exception as e:
                logger.debug(
                    f"Não foi possível restaurar handler de SIGINT original: {e}"
                )

            if aborted_by_user:
                time.sleep(1.5)

            final_dirn = working_dirn
            if is_standard_album and working_dirn == inprogress_dirn:
                final_dirn = (
                    target_dirn
                    if (failed_tracks == 0 and not aborted_by_user)
                    else incomplete_dirn
                )
                try:
                    os.rename(working_dirn, final_dirn)
                except OSError as e:
                    safe_print(
                        f"{YELLOW}[!] Não foi possível renomear a pasta final "
                        f"(o bloqueio do SO ainda pode estar ativo). ({e}){OFF}"
                    )
                    final_dirn = working_dirn

                if aborted_by_user:
                    safe_print(
                        f"{YELLOW}[!] Download abortado. Pasta marcada com sucesso como [INCOMPLETE].{OFF}"
                    )
                elif failed_tracks > 0:
                    safe_print(
                        f"\n{YELLOW}[!] Álbum baixado parcialmente ({failed_tracks} faixas puladas). "
                        f"Pasta marcada como [INCOMPLETE].{OFF}"
                    )

            if aborted_by_user:
                os._exit(1)

            # [FIX] handle_download_id() agora vive DENTRO deste 'if', junto com db_artist/db_album -- antes havia risco (sinalizado em revisao) de essas variaveis nao existirem quando failed_tracks>0/aborted_by_user e o handle_download_id ainda assim tentar rodar.
            if failed_tracks == 0 and not aborted_by_user:
                db_artist = album_attr.get("album_artist", "Unknown")
                db_album = album_attr.get("album_title", "Unknown")

                await handle_download_id(
                    db_path=self.download_db,
                    item_id=self.item_id,
                    add_id=True,
                    media_type="album",
                    quality=self.quality,
                    file_format=file_format,
                    quality_met=quality_met,
                    bit_depth=bit_depth,
                    sampling_rate=sampling_rate,
                    saved_path=final_dirn,
                    url=url,
                    release_date=release_date,
                    artist=db_artist,
                    album=db_album,
                )

                await postprocess.finalize_report(
                    final_dirn,
                    completo=(failed_tracks == 0 and not aborted_by_user),
                    qualidade_atingida=quality_met,
                )
                if self.download_db:
                    postprocess.generate_index_entry(
                        self.download_db,
                        self.item_id,
                        album_title,
                        artist_name,
                        final_dirn,
                        file_format,
                        bit_depth,
                        sampling_rate,
                        release_date,
                        url,
                    )

            skipped_count = sum(1 for r in results if r == "skipped")
            real_failed = sum(1 for r in results if r is False)
            downloaded_count = sum(1 for r in results if r is True)

            safe_print(f"\n{CYAN}{'-' * 44}{RESET}")
            safe_print(f"  📊 RESUMO DO ÁLBUM: {GREEN}{RESET} {album_title}")
            safe_print(
                f" - Baixadas com sucesso : {GREEN}{downloaded_count}/{track_count}{RESET}"
            )
            if skipped_count > 0:
                safe_print(
                    f" - Faixas puladas (Demo/Indisponivel) : {YELLOW}{skipped_count}{RESET} "
                    f"(marcadas em .missing.txt)"
                )
            if real_failed > 0:
                safe_print(f" - Falhas de rede/download : {RED}{real_failed}{RESET}")
            safe_print(f"{CYAN}{'-' * 44}{RESET}\n")

    async def download_track(
        self, is_parallel=False, position_pool=None, suppress_header=False
    ):
        parse = await self.client.get_track_url(self.item_id, self.quality)

        track_meta = await self.client.get_track_meta(self.item_id)
        track_title = _get_title(track_meta)

        is_sample_only = "sample" in parse or not parse.get("sampling_rate")

        if is_sample_only:
            safe_print(
                f"{CYAN}[PULADA]{RESET} {track_title} "
                f"({YELLOW}Apenas amostra/demo (30s){RESET})"
            )
            success = False
            track_attr = None
        else:
            if (
                getattr(self, "is_playlist", False)
                and not getattr(self, "playlist_as_albums", False)
                and getattr(self, "playlist_track_number", None)
            ):
                track_meta["track_number"] = self.playlist_track_number

            track_album = track_meta.get("album", {})
            artist = _artist_label(
                track_meta, fallback=_safe_get(track_meta, "performer", "name") or ""
            )
            album_name = track_album.get("title", "--")
            # Artista "oficial" do release desta faixa (nao o performer da
            # faixa isolada) -- usado so pro cabecalho do report.json, pra
            # nao confundir "essa faixa tem um feat." com "esse release e
            # de mais de um artista de verdade". Ver `_artist_label`.
            report_track_artist = _artist_label(track_album, fallback=artist)
            # Classificacao Single/EP/Album do release ao qual ESTA FAIXA
            # pertence (nao de quantas faixas estao sendo baixadas nesta
            # sessao -- uma faixa avulsa de um album de 15 faixas continua
            # sendo, de fato, uma faixa de um Album, mesmo baixando so ela).
            report_track_tipo_lancamento = format_release_type(
                track_album.get("release_type"),
                track_count=track_album.get("track_count"),
                title=_get_title(track_album) if track_album.get("title") else None,
                version=track_album.get("version"),
                duration_seconds=track_album.get("duration"),
            )

            url = track_album.get("url", "")
            release_date = track_meta.get("release_date_original", "")
            format_info = await self._get_format(
                track_meta, is_track_id=True, track_url_dict=parse
            )
            file_format, quality_met, bit_depth, sampling_rate = format_info

            if not suppress_header:
                print_download_header(
                    "FAIXA",
                    [
                        ("Faixa", track_title),
                        ("Artista", artist),
                        ("Álbum", album_name),
                        (
                            "Qualidade",
                            (
                                f"{file_format} ({bit_depth}bit/{float(sampling_rate):g}kHz)"
                                if bit_depth
                                else file_format
                            ),
                        ),
                    ],
                )

            emit_progress_json(
                self.settings,
                "track_start",
                track_id=self.item_id,
                artist=artist,
                title=track_title,
            )

            folder_format, track_format = _clean_format_str(
                self.folder_format, self.track_format, str(bit_depth)
            )

            if not self.downgrade_quality and not quality_met:
                safe_print(
                    f"{OFF}Pulando {track_title} pois não atende ao requisito de qualidade{OFF}"
                )
                success = False
                track_attr = None
            else:
                track_attr = self._get_track_attr(
                    track_meta, track_title, bit_depth, sampling_rate, file_format
                )

                self._determine_formats(
                    album_meta=track_meta.get("album", {}),
                    album_attr=None,
                    tracks_meta=[track_meta],
                    track_attr=track_attr,
                    is_track=True,
                    file_format=file_format,
                    settings=self.settings,
                )

                legacy_flag = (
                    getattr(self.settings, "legacy_charmap", False)
                    if hasattr(self, "settings")
                    else False
                )

                dirn = process_folder_format_with_subdirs(
                    self.folder_format,
                    track_attr,
                    self.path,
                    legacy_charmap=legacy_flag,
                )
                os.makedirs(dirn, exist_ok=True)

                skip_saved_cover = getattr(self, "is_playlist", False) and not getattr(
                    self, "playlist_as_albums", False
                )

                if skip_saved_cover:
                    if getattr(self, "playlist_track_number", 1) == 1:
                        safe_print(
                            f"    {MUTED}[*] Pulando salvamento padrao de capa para manter a pasta da playlist limpa{OFF}"
                        )
                elif self.settings.no_cover:
                    safe_print(f"    {MUTED}[*] Pulando capa{OFF}")

                embed_cover_path = None
                if self.settings.embed_art:
                    unique_embed_name = f".embed_{self.item_id}.jpg"
                    embed_cover_path = os.path.join(dirn, unique_embed_name)
                else:
                    safe_print(f"    {MUTED}[*] Pulando arte incorporada{OFF}")

                save_cover_now = not skip_saved_cover and not self.settings.no_cover
                if save_cover_now or self.settings.embed_art:
                    async with _get_dir_lock(dirn):
                        await _get_cover_and_embed(
                            track_meta["album"]["image"]["large"],
                            dirn,
                            save_cover=save_cover_now,
                            embed_art=self.settings.embed_art,
                            saved_name="cover.jpg",
                            embed_name=(
                                embed_cover_path and os.path.basename(embed_cover_path)
                            )
                            or "",
                            saved_art_size=self.settings.saved_art_size,
                            embedded_art_size=self.settings.embedded_art_size,
                            session=self.http_session,
                            is_parallel=is_parallel,
                            position_pool=position_pool,
                            artist=_artist_label(
                                track_meta,
                                fallback=track_meta.get("album", {})
                                .get("artist", {})
                                .get("name", ""),
                            ),
                            album=track_meta.get("album", {}).get("title", ""),
                            upc=track_meta.get("album", {}).get("upc", ""),
                            isrc=track_meta.get("isrc", ""),
                            track_title=track_meta.get("title", ""),
                        )

                is_mp3 = True if int(self.quality) == 5 else False

                letras_info = {}
                success = await self._download_and_tag(
                    dirn,
                    self.item_id,
                    parse,
                    track_meta,
                    track_meta,
                    True,
                    is_mp3,
                    False,
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                    embed_cover_path=embed_cover_path,
                    letras_out=letras_info,
                )

                if embed_cover_path and os.path.isfile(embed_cover_path):
                    try:
                        os.remove(embed_cover_path)
                    except OSError:
                        pass

                # Vale pra faixa avulsa, lote de faixas E faixa de playlist:
                # a pasta (`dirn`) so existe a partir daqui, entao e aqui que
                # o report.json compartilhado da pasta e atualizado. Faz
                # upsert por id e reordena por numero -- se `dirn` for a
                # pasta de uma playlist (varias chamadas de download_track,
                # uma por faixa) o arquivo vai se completando na ordem certa
                # nao importa a ordem de conclusao entre elas.
                numero_report = (
                    self.playlist_track_number
                    if getattr(self, "is_playlist", False)
                    and getattr(self, "playlist_track_number", None)
                    else track_meta.get("track_number", 1)
                )
                tipo_report = (
                    "playlist" if getattr(self, "is_playlist", False) else "faixa"
                )
                await postprocess.update_track_status(
                    dirn,
                    numero=numero_report,
                    item_id=self.item_id,
                    titulo=track_title,
                    status="ok" if success else "falha",
                    artista=artist,
                    artista_album=report_track_artist,
                    tipo_lancamento=report_track_tipo_lancamento,
                    motivo="" if success else "Falha ou Pulada",
                    isrc=track_meta.get("isrc", ""),
                    compositor=_safe_get(track_meta, "composer", "name", default=""),
                    letras=letras_info,
                    tipo_default=tipo_report,
                    # titulo do CABECALHO da pasta (nao da faixa em si): usa
                    # o nome do album mesmo pra faixa avulsa/lote, porque a
                    # pasta e a pasta do album (pode receber outras faixas
                    # depois, inclusive o album completo) -- ver init_report
                    # em postprocess.py pra como isso e promovido quando o
                    # album completo chega depois.
                    titulo_default=(
                        (self.playlist_title or album_name)
                        if tipo_report == "playlist"
                        else album_name
                    ),
                    id_default=(
                        (self.playlist_id or "") if tipo_report == "playlist" else ""
                    ),
                )

        if success:
            db_artist = track_attr.get("artist", "Unknown")
            db_album = track_attr.get("album", "Unknown")

            await handle_download_id(
                db_path=self.download_db,
                item_id=self.item_id,
                add_id=True,
                media_type="track",
                quality=self.quality,
                file_format=file_format,
                quality_met=quality_met,
                bit_depth=bit_depth,
                sampling_rate=sampling_rate,
                saved_path=dirn,
                url=url,
                release_date=release_date,
                artist=db_artist,
                album=db_album,
            )

        is_batch_or_playlist = (
            getattr(self, "is_playlist", False)
            or getattr(self.settings, "pl_success", None) is not None
        )

        if not is_batch_or_playlist:
            # Faixa avulsa de verdade (nao playlist, nao lote): esta e a
            # unica faixa daquela pasta, entao ja da pra fechar o report.
            if "dirn" in locals():
                await postprocess.finalize_report(dirn, completo=bool(success))
            try:
                safe_print(f"\n{CYAN}{'-' * 44}{RESET}")
                safe_print(f"  📊 RESUMO DA FAIXA: {GREEN}{RESET} {track_title}")
                if success:
                    safe_print(f" - Status : {GREEN}Concluída com Sucesso{RESET}")
                else:
                    safe_print(f" - Status : {RED}Falhou ou foi pulada{RESET}")
                safe_print(f"{CYAN}{'-' * 44}{RESET}\n")
            except Exception as e:
                safe_print(
                    f"\n{RED}[!] Erro ao gerar painel de resumo da Faixa: {e}{RESET}\n"
                )
        else:
            # Lote/playlist: quem sabe quando a ULTIMA faixa terminou e o
            # orquestrador externo que chama download_track() em loop (fora
            # deste arquivo). Ele deve chamar
            # `await postprocess.finalize_report(dirn_da_pasta, completo=True)`
            # depois do loop terminar, senao o report fica "em_andamento" para
            # sempre. Para lote de faixas em pastas separadas isso e inofensivo
            # (so nao fecha o estado); para playlist convem fazer essa chamada.
            if success:
                self.settings.pl_success = getattr(self.settings, "pl_success", 0) + 1
            else:
                self.settings.pl_skipped = getattr(self.settings, "pl_skipped", 0) + 1

    async def _download_and_tag(
        self,
        root_dir,
        tmp_count,
        track_url_dict,
        track_metadata,
        album_or_track_metadata,
        is_track,
        is_mp3,
        multiple=None,
        is_parallel=False,
        position_pool=None,
        embed_cover_path=None,
        letras_out: Optional[dict] = None,
    ) -> bool:
        extension = ".mp3" if is_mp3 else ".flac"
        loop = asyncio.get_running_loop()

        track_artist = _safe_get(track_metadata, "performer", "name")
        filename_attr = self._get_filename_attr(
            track_artist,
            track_metadata,
            (
                album_or_track_metadata.get("album", {})
                if is_track
                else album_or_track_metadata
            ),
        )

        legacy_flag = (
            getattr(self.settings, "legacy_charmap", False)
            if hasattr(self, "settings")
            else False
        )

        if getattr(self, "is_playlist", False) and not getattr(
            self, "playlist_as_albums", False
        ):
            clean_playlist_format = "{artist} - {track_title}"
            formatted_path = sanitize_filename(
                clean_filename(
                    clean_playlist_format.format(**filename_attr),
                    legacy_charmap=legacy_flag,
                ),
                replacement_text="_",
            )
        elif multiple and self.settings.multiple_disc_one_dir:
            formatted_path = sanitize_filename(
                clean_filename(
                    self.settings.multiple_disc_track_format.format(**filename_attr),
                    legacy_charmap=legacy_flag,
                ),
                replacement_text="_",
            )
        else:
            base_formatted = sanitize_filename(
                clean_filename(
                    self.track_format.format(**filename_attr),
                    legacy_charmap=legacy_flag,
                ),
                replacement_text="_",
            )
            total_discs = album_or_track_metadata.get("media_count", 1)
            if multiple and total_discs > 1:
                try:
                    d_num = int(multiple) if not isinstance(multiple, bool) else 1
                except (ValueError, TypeError):
                    d_num = 1
                disc_folder = f"{self.settings.multiple_disc_prefix} {d_num:02}"
                formatted_path = os.path.join(disc_folder, base_formatted)
            else:
                formatted_path = base_formatted

        max_len = 180
        if len(formatted_path) > max_len:
            start_part = formatted_path[:110].rstrip(" .\"-_'")
            end_part = formatted_path[-60:].lstrip(" .\"-_'")
            formatted_path = f"{start_part}...{end_part}"

        final_file = os.path.join(root_dir, formatted_path) + extension

        if os.path.exists(final_file):
            safe_print(
                f"{CYAN}[*] Pulando: {os.path.basename(final_file)} (Ja existe){OFF}"
            )
            return True

        if abort_event.is_set():
            return False

        await asyncio.sleep(1)
        try:
            track_url_dict["url"]
        except KeyError:
            safe_print(f"{OFF}Faixa nao disponivel para download{OFF}")
            return False

        total_discs = album_or_track_metadata.get("media_count", 1)
        if multiple and total_discs > 1 and (not self.settings.multiple_disc_one_dir):
            try:
                d_num = int(multiple) if not isinstance(multiple, bool) else 1
            except (ValueError, TypeError):
                d_num = 1
            root_dir = os.path.join(
                root_dir, f"{self.settings.multiple_disc_prefix} {d_num:02}"
            )
        if not os.path.exists(root_dir):
            os.makedirs(root_dir, exist_ok=True)

        filename = os.path.join(root_dir, f"~tmp_{tmp_count:02}.tmp")
        track_title = _get_title(track_metadata)
        track_no = str(track_metadata.get("track_number", 0)).zfill(2)
        desc = f"{track_no}. {track_title}"

        FALLBACK_TIERS_LOCAL = [27, 7, 6, 5]
        TIER_NAMES = {
            27: "24-bit/>96kHz",
            7: "24-bit/96kHz",
            6: "16-bit/44.1kHz (CD)",
            5: "MP3 320kbps",
        }

        try:
            start_idx = FALLBACK_TIERS_LOCAL.index(int(self.quality))
        except ValueError:
            start_idx = 0

        qualities_to_try = FALLBACK_TIERS_LOCAL[start_idx:]
        success = False
        final_fmt = int(self.quality)

        for attempt_fmt in qualities_to_try:
            if abort_event.is_set():
                return False

            if attempt_fmt != int(self.quality):
                safe_print(
                    f"{YELLOW}[!] Downgrade automatico: tentando salvar em "
                    f"{TIER_NAMES[attempt_fmt]}...{OFF}"
                )

            async def get_fresh_url(fmt=attempt_fmt, force_segments=False):
                return await self.client.get_track_url(
                    track_metadata["id"], fmt_id=fmt, force_segments=force_segments
                )

            try:
                fresh_track_dict = await get_fresh_url(force_segments=False)

                if fresh_track_dict.get("sample") is True:
                    safe_print(
                        f"{CYAN}[PULADA]{RESET} Faixa {track_no} - {track_title} "
                        f"({YELLOW}URL retornada e apenas amostra{RESET})"
                    )
                    create_missing_placeholder(
                        track_metadata, root_dir, "URL retornada é apenas amostra"
                    )
                    return False

                if "url" in fresh_track_dict:
                    try:
                        await tqdm_download(
                            fresh_track_dict["url"],
                            filename,
                            desc,
                            is_parallel=is_parallel,
                            session=self.http_session,
                            position_pool=position_pool,
                        )
                        success = True
                        final_fmt = attempt_fmt
                        break
                    except _PermanentDownloadError as e:
                        if abort_event.is_set():
                            return False
                        safe_print(f"{YELLOW}[!] Faixa indisponivel, pulando: {e}{OFF}")
                        return False
                    except Exception as e:
                        # [FIX] Antes este bloco so' avisava sobre "bloqueio Akamai"
                        # e tentava o caminho segmentado; agora tambem loga o
                        # motivo real da primeira falha, pra facilitar debug.
                        if abort_event.is_set():
                            return False
                        logger.debug(
                            f"Falha no download direto (tier {attempt_fmt}): {e}"
                        )
                        safe_print(
                            f"{YELLOW}[!] Bloqueio Akamai detectado (ou falha de rede: {e}). "
                            f"Ativando download segmentado de fallback...{OFF}"
                        )
                        fresh_track_dict = await get_fresh_url(force_segments=True)

                        if "url_template" in fresh_track_dict:
                            try:
                                await tqdm_download_segments(
                                    fresh_track_dict,
                                    filename,
                                    desc,
                                    is_parallel=is_parallel,
                                    session=self.http_session,
                                    segment_workers=getattr(
                                        self.settings, "segment_workers", None
                                    ),
                                    position_pool=position_pool,
                                )
                                success = True
                                final_fmt = attempt_fmt
                                break
                            except Exception as seg_e:
                                # [FIX] Antes esse erro era engolido em silencio pelo
                                # "except Exception: pass" la embaixo -- agora e'
                                # sempre reportado ao usuario, com o motivo real.
                                logger.debug(
                                    f"Download segmentado falhou (tier {attempt_fmt}): {seg_e}"
                                )
                                safe_print(
                                    f"{RED}[!] Download segmentado falhou no tier "
                                    f"{TIER_NAMES.get(attempt_fmt, attempt_fmt)}: {seg_e}{OFF}"
                                )
                                continue
                        else:
                            safe_print(
                                f"{RED}[!] Nenhum formato valido retornado pelo servidor "
                                f"para o tier {TIER_NAMES.get(attempt_fmt, attempt_fmt)}.{OFF}"
                            )
                            continue

            except _PermanentDownloadError as e:
                if abort_event.is_set():
                    return False
                safe_print(f"{YELLOW}[!] Faixa indisponível, pulando: {e}{OFF}")
                return False
            except Exception as e:
                # [FIX] Agora reporta o motivo em vez de "pass" silencioso, para
                # que falhas ao obter a URL fresca (get_fresh_url) do tier atual
                # fiquem visiveis antes de tentar o proximo tier.
                logger.debug(f"Falha ao processar tier {attempt_fmt}: {e}")
                safe_print(
                    f"{YELLOW}[!] Falha no tier {TIER_NAMES.get(attempt_fmt, attempt_fmt)}: {e}{OFF}"
                )
                continue

        if not success and not abort_event.is_set():
            safe_print(
                f"\n{RED}[!] FAIXA {track_no} DESCARTADA DEFINITIVAMENTE APOS TODOS OS DOWNGRADES.{OFF}"
            )
            safe_print(f"{YELLOW}[!] Pulando para a proxima faixa...{OFF}\n")
            return False

        if abort_event.is_set():
            return False

        is_mp3 = True if final_fmt == 5 else False
        extension = ".mp3" if is_mp3 else ".flac"

        tag_function = metadata.tag_mp3 if is_mp3 else metadata.tag_flac
        try:
            await loop.run_in_executor(
                None,
                lambda: tag_function(
                    filename,
                    root_dir,
                    final_file,
                    track_metadata,
                    album_or_track_metadata,
                    is_track,
                    self.embed_art,
                    settings=self.settings,
                    embed_cover_path=embed_cover_path,
                ),
            )
        except Exception as e:
            safe_print(f"{RED}[!] Erro ao aplicar tags: {e}{OFF}")

        if (
            getattr(self, "fetch_lyrics", False)
            and hasattr(self, "lyrics_engine")
            and not abort_event.is_set()
        ):
            album_artist = _safe_get(track_metadata, "album", "artist", "name")
            performer_name = _safe_get(
                track_metadata, "performer", "name"
            ) or _safe_get(track_metadata, "artist", "name", default="Unknown")
            search_artist = (
                performer_name
                if album_artist in [None, "Various Artists"]
                else album_artist
            )
            search_album = _safe_get(track_metadata, "album", "title", default="")

            qobuz_lyrics_response = await self._fetch_qobuz_lyrics_json(
                track_metadata["id"]
            )

            qobuz_translation_response = None
            translation_lang = getattr(self.settings, "lyrics_translation_lang", "pt")
            if translation_lang:
                translation_json = await self._fetch_qobuz_lyrics_json(
                    track_metadata["id"], language=translation_lang
                )
                if isinstance(translation_json, dict):
                    qobuz_translation_response = translation_json.get("translation")

            translation_note = None
            if (
                translation_lang
                and not qobuz_translation_response
                and isinstance(qobuz_lyrics_response, dict)
            ):
                original_block = qobuz_lyrics_response.get("original")
                original_lang = (
                    original_block.get("lang")
                    if isinstance(original_block, dict)
                    else None
                )
                if original_lang:
                    if original_lang.lower() == translation_lang.lower():
                        translation_note = (
                            f"    ℹ️ Letras já em {GREEN}{translation_lang.upper()}{RESET} "
                            f"-- sem necessidade de tradução."
                        )
                    else:
                        translation_note = (
                            f"    ℹ️ Nenhuma tradução em {RED}{translation_lang.upper()}{RESET} "
                            f"disponivel no Qobuz ainda para esta faixa."
                        )

            def _inject_lyrics_and_print():
                with print_lock:
                    resultado_letras = self.lyrics_engine.fetch_and_inject(
                        file_path=final_file,
                        artist=search_artist,
                        track=track_title,
                        album=search_album,
                        save_lrc=not self.no_lrc_files,
                        embed_lyrics=getattr(self.settings, "embed_lyrics", True),
                        qobuz_lyrics_response=qobuz_lyrics_response,
                        qobuz_translation_response=qobuz_translation_response,
                        track_number=track_no,
                    )
                    if translation_note:
                        tqdm.write(
                            f"{OFF}{MUTED}[{track_no}]{OFF} {translation_note}{OFF}"
                        )
                    return resultado_letras

            resultado_letras = await loop.run_in_executor(
                None, _inject_lyrics_and_print
            )
            if letras_out is not None:
                letras_out.update(
                    _build_letras_report(
                        resultado_letras, translation_lang, qobuz_translation_response
                    )
                )

        emit_progress_json(
            self.settings,
            "track_done",
            track_id=self.item_id,
            path=final_file,
        )

        if (
            getattr(self.settings, "verify_after_download", False)
            and not abort_event.is_set()
        ):

            def _run_verify():
                return verify_audio_integrity(final_file)

            ok, verify_message = await loop.run_in_executor(None, _run_verify)
            if not ok:
                with print_lock:
                    tqdm.write(
                        f"{RED}[!] Verificacao de integridade falhou para "
                        f"{os.path.basename(final_file)}: {verify_message}{OFF}"
                    )
                logger.debug(f"Falha de integridade em {final_file}: {verify_message}")

        delay_time = getattr(self.settings, "delay", 0)
        if delay_time == 0 and "--delay" in sys.argv:
            try:
                delay_time = int(sys.argv[sys.argv.index("--delay") + 1])
            except (ValueError, IndexError) as e:
                logger.debug(f"--delay com valor invalido, ignorando: {e}")

        if delay_time > 0 and not abort_event.is_set():
            safe_print(
                f"{YELLOW}[*] Aguardando {delay_time} segundos para evitar rate limiting...{OFF}"
            )
            await asyncio.sleep(delay_time)

        return True

    @staticmethod
    def _get_filename_attr(track_artist, track_metadata: dict, album_metadata: dict):
        def _flatten_artists(artist_data):
            if isinstance(artist_data, list) and artist_data:
                return str(artist_data[0])
            return str(artist_data) if artist_data else ""

        album_artist_raw = get_album_artist(album_metadata)
        album_artist_str = (
            _flatten_artists(album_artist_raw) if album_artist_raw else track_artist
        )

        return {
            "artist": track_artist,
            "albumartist": album_artist_str,
            "tracktitle": _get_title(track_metadata),
            "album_title": _get_title(album_metadata),
            "album_title_base": album_metadata.get("title"),
            "album_artist": album_artist_str,
            "track_id": track_metadata.get("id"),
            "track_artist": track_artist,
            "track_composer": _safe_get(track_metadata, "composer", "name"),
            "track_number": f'{track_metadata.get("track_number", 0):02}',
            "isrc": track_metadata.get("isrc"),
            "bit_depth": track_metadata.get("maximum_bit_depth"),
            "sampling_rate": track_metadata.get("maximum_sampling_rate"),
            "track_title": _get_title(track_metadata),
            "track_title_base": track_metadata.get("title"),
            "version": track_metadata.get("version"),
            "year": track_metadata.get("release_date_original", "").split("-")[0],
            "disc_number": f'{track_metadata.get("media_number"):02}',
            "release_date": track_metadata.get("release_date_original"),
            "ExplicitFlag": "[E]" if track_metadata.get("parental_warning") else "",
            "explicit": "[E]" if track_metadata.get("parental_warning") else "",
        }

    @staticmethod
    def _get_track_attr(meta, track_title, bit_depth, sampling_rate, file_format):
        album_meta = meta.get("album", {})

        def _flatten_artists(artist_data):
            if isinstance(artist_data, list) and artist_data:
                return str(artist_data[0])
            return str(artist_data) if artist_data else ""

        album_artist_raw = get_album_artist(album_meta)
        album_artist_str = (
            _flatten_artists(album_artist_raw)
            if album_artist_raw
            else _safe_get(meta, "performer", "name")
        )

        return {
            "album": _get_title(album_meta),
            "artist": album_artist_str,
            "tracktitle": track_title,
            "track_title": track_title,
            "track_title_base": meta.get("title", ""),
            "album_id": meta.get("id", ""),
            "album_url": meta.get("url", ""),
            "album_title": _get_title(album_meta),
            "album_title_base": album_meta.get("title", ""),
            "album_artist": album_artist_str,
            "album_genre": meta.get("genre", {}).get("name", ""),
            "album_composer": meta.get("composer", {}).get("name", ""),
            "label": re.sub(
                r"\s*[\;\/]\s*|\s+\-\s+",
                " / ",
                " ".join(meta.get("label", {}).get("name", "").split()),
            ).strip(),
            "copyright": meta.get("copyright", ""),
            "upc": meta.get("upc", ""),
            "barcode": meta.get("upc", ""),
            "release_date": meta.get("release_date_original", ""),
            "year": meta.get("release_date_original", "").split("-")[0],
            "media_type": meta.get("product_type", "").capitalize(),
            "format": file_format,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "quality_tag": (
                "MP3"
                if str(file_format).upper() == "MP3"
                else (f"{file_format} {bit_depth}" if bit_depth else file_format)
            ),
            "album_version": meta.get("version", ""),
            "version_tag": f" - {meta.get('version')}" if meta.get("version") else "",
            "disc_count": meta.get("media_count", ""),
            "track_count": meta.get("track_count", ""),
            "ExplicitFlag": "[E]" if album_meta.get("parental_warning") else "",
            "explicit": "[E]" if album_meta.get("parental_warning") else "",
            "release_type": format_release_type(
                album_meta.get("release_type"),
                track_count=meta.get("track_count", ""),
                title=_get_title(album_meta),
                version=album_meta.get("version"),
                duration_seconds=album_meta.get("duration"),
            ),
        }

    @staticmethod
    def _get_album_attr(meta, album_title, file_format, bit_depth, sampling_rate):
        def _flatten_artists(artist_data):
            if isinstance(artist_data, list) and artist_data:
                return str(artist_data[0])
            return str(artist_data) if artist_data else ""

        album_artist_raw = get_album_artist(meta)
        album_artist_str = _flatten_artists(album_artist_raw)

        return {
            "artist": meta.get("artist", {}).get("name", ""),
            "album": album_title,
            "album_id": meta.get("id", ""),
            "album_url": meta.get("url", ""),
            "album_title": album_title,
            "album_title_base": meta.get("title", ""),
            "album_artist": album_artist_str,
            "album_genre": meta.get("genre", {}).get("name", ""),
            "album_composer": meta.get("composer", {}).get("name", ""),
            "label": re.sub(
                r"\s*[\;\/]\s*|\s+\-\s+",
                " / ",
                " ".join(meta.get("label", {}).get("name", "").split()),
            ).strip(),
            "copyright": meta.get("copyright", ""),
            "upc": meta.get("upc", ""),
            "barcode": meta.get("upc", ""),
            "release_date": meta.get("release_date_original", ""),
            "year": meta.get("release_date_original", "").split("-")[0],
            "media_type": meta.get("product_type", "").capitalize(),
            "format": file_format,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "quality_tag": (
                "MP3"
                if str(file_format).upper() == "MP3"
                else (f"{file_format} {bit_depth}" if bit_depth else file_format)
            ),
            "album_version": meta.get("version", ""),
            "version_tag": f" - {meta.get('version')}" if meta.get("version") else "",
            "disc_count": meta.get("media_count", 1),
            "track_count": meta.get("track_count", 1),
            "ExplicitFlag": "[E]" if meta.get("parental_warning") else "",
            "explicit": "[E]" if meta.get("parental_warning") else "",
            "release_type": format_release_type(
                meta.get("release_type"),
                track_count=meta.get("track_count"),
                title=album_title,
                version=meta.get("version"),
                duration_seconds=meta.get("duration"),
            ),
        }

    async def _get_format(self, item_dict, is_track_id=False, track_url_dict=None):
        if not is_track_id:
            if "tracks" not in item_dict or not item_dict["tracks"].get("items"):
                raise NonStreamable(
                    "Este lancamento nao tem faixas disponiveis (possivelmente "
                    "bloqueado por região ou removido)"
                )

        track_dict = item_dict if is_track_id else item_dict["tracks"]["items"][0]
        quality_met = True

        try:
            new_track_dict = (
                await self.client.get_track_url(track_dict["id"], fmt_id=self.quality)
                if not track_url_dict
                else track_url_dict
            )

            if not new_track_dict:
                raise KeyError("Nenhuma URL retornada pela API")

            restrictions = new_track_dict.get("restrictions")
            if isinstance(restrictions, list):
                if any(
                    restriction.get("code") == QL_DOWNGRADE
                    for restriction in restrictions
                ):
                    quality_met = False

            actual_format = "MP3" if int(self.quality) == 5 else "FLAC"

            return (
                actual_format,
                quality_met,
                new_track_dict["bit_depth"],
                new_track_dict["sampling_rate"],
            )
        except Exception as e:
            logger.debug(f"Não foi possível determinar o formato real: {e}")
            return ("Unknown", quality_met, None, None)

    def _determine_formats(
        self,
        album_meta,
        album_attr,
        tracks_meta,
        track_attr,
        is_track,
        file_format,
        settings: QobuzDLSettings,
    ):
        format_combinations = [
            (
                self._original_folder_format,
                self._original_track_format,
                self._original_multiple_disc_track_format,
            ),
            (
                settings.fallback_folder_format,
                self._original_track_format,
                self._original_multiple_disc_track_format,
            ),
            (
                settings.fallback_folder_format,
                DEFAULT_TRACK,
                DEFAULT_MULTIPLE_DISC_TRACK,
            ),
            (DEFAULT_FOLDER, DEFAULT_TRACK, DEFAULT_MULTIPLE_DISC_TRACK),
        ]

        media_count = album_meta.get("media_count", 1)
        is_multiple = True if media_count > 1 else False
        _extension = ".flac" if file_format.lower() == "flac" else ".mp3"

        legacy_flag = getattr(settings, "legacy_charmap", False) if settings else False

        for folder_fmt, track_fmt, multi_disc_fmt in format_combinations:
            folder_fmt, track_fmt = _clean_format_str(
                folder_fmt, track_fmt, file_format
            )

            valid_combination = True

            try:
                if is_track:
                    root_dir = process_folder_format_with_subdirs(
                        folder_fmt, track_attr, legacy_charmap=legacy_flag
                    )
                else:
                    root_dir = process_folder_format_with_subdirs(
                        folder_fmt, album_attr, legacy_charmap=legacy_flag
                    )

                for track_metadata in tracks_meta:
                    track_artist = _safe_get(track_metadata, "performer", "name")
                    filename_attr = self._get_filename_attr(
                        track_artist, track_metadata, album_meta
                    )

                    if is_multiple and self.settings.multiple_disc_one_dir:
                        _track_path = sanitize_filename(
                            clean_filename(
                                multi_disc_fmt.format(**filename_attr),
                                legacy_charmap=legacy_flag,
                            ),
                            replacement_text="_",
                        )
                    else:
                        if is_multiple and not self.settings.multiple_disc_one_dir:
                            _mnum = track_metadata["media_number"]
                            disc_dir = (
                                f"{self.settings.multiple_disc_prefix} {_mnum:02}"
                            )
                            os.path.join(root_dir, disc_dir)

                        _track_path = sanitize_filename(
                            clean_filename(
                                track_fmt.format(**filename_attr),
                                legacy_charmap=legacy_flag,
                            ),
                            replacement_text="_",
                        )
            except (KeyError, ValueError):
                valid_combination = False
                continue

            if valid_combination:
                self.folder_format = folder_fmt
                self.track_format = track_fmt
                if self.settings:
                    self.settings.multiple_disc_track_format = multi_disc_fmt
                return

        self.folder_format = DEFAULT_FOLDER
        self.track_format = DEFAULT_TRACK

    def _generate_tracklist(
        self, meta, dirn, album_title, file_format, bit_depth, sampling_rate
    ):
        if self.no_credits or abort_event.is_set():
            return

        safe_title = sanitize_filename(album_title)
        tracklist_path = os.path.join(dirn, f"{safe_title} - Tracklist.txt")

        if os.path.isfile(tracklist_path):
            return

        safe_print(f"{CYAN}[+] Gerando Digital Booklet...{OFF}")

        artist_name = _safe_get(meta, "artist", "name", default="Unknown Artist")
        composer = _safe_get(meta, "composer", "name", default="N/A")
        label = _safe_get(meta, "label", "name", default="Independent")
        raw_genre = _safe_get(meta, "genre", "name", default="Unknown Genre")
        genre = (
            metadata.LOCAL_GENRE_MAP.get(raw_genre, raw_genre)
            if raw_genre != "Unknown Genre"
            else raw_genre
        )
        release_date = meta.get("release_date_original", "Unknown Date")

        try:
            with open(tracklist_path, "w", encoding="utf-8") as f:
                explicit_tag = " [E]" if meta.get("parental_warning") else ""

                f.write("=" * 70 + "\n")
                f.write(f"ÁLBUM : {album_title}{explicit_tag}\n")
                if composer != "N/A":
                    f.write(f"COMPOSITOR : {composer}\n")
                f.write(f"MAIN ART. : {artist_name}\n")
                f.write(f"RÓTULO : {label}\n")
                f.write(f"GÊNERO : {genre}\n")
                f.write(f"DATA DE LANÇAMENTO : {release_date}\n")
                f.write(
                    f"QUALIDADE : {file_format} ({bit_depth}-Bit / {sampling_rate} kHz)\n"
                )
                f.write("=" * 70 + "\n\n")

                tracks = meta.get("tracks", {}).get("items", [])
                total_discs = max(
                    (track.get("media_number", 1) for track in tracks), default=1
                )
                current_disc = None

                for track in tracks:
                    disc_num = track.get("media_number", 1)
                    if total_discs > 1 and disc_num != current_disc:
                        if current_disc is not None:
                            f.write("\n")
                        f.write(f"--- DISC {disc_num} ---\n\n")
                        current_disc = disc_num

                    t_num = str(track.get("track_number", 0)).zfill(2)
                    t_title_base = track.get("title", "Unknown Title")
                    explicit_flag = " [E]" if track.get("parental_warning") else ""
                    t_title = f"{t_title_base}{explicit_flag}"

                    duration = int(track.get("duration", 0))
                    mins, secs = divmod(duration, 60)
                    dur_str = f"[{mins:02}:{secs:02}]"

                    f.write(f"{f'{t_num}. {t_title}':<60} {dur_str}\n")

                    performers_raw = track.get("performers", "")
                    if performers_raw:
                        for line in re.split(r"\r?\n|\s+-\s+", str(performers_raw)):
                            if line.strip():
                                f.write(f"  * {line.strip()}\n")
                    else:
                        t_artist = _safe_get(
                            track, "performer", "name", default=artist_name
                        )
                        f.write(f"  {t_artist}\n")
                    f.write("\n")

                description = meta.get("description")
                if description:
                    f.write(
                        "\n" + "=" * 70 + "\nÁLBUM REVIEW / NOTES\n" + "=" * 70 + "\n\n"
                    )
                    clean_desc = re.sub(
                        r"<[^<]+>", "", re.sub(r"<br\s*/?>", "\n", str(description))
                    )
                    for p in clean_desc.split("\n"):
                        if p.strip():
                            f.write(textwrap.fill(p.strip(), width=70) + "\n\n")

            safe_print(
                f"{GREEN} └─ Concluído: Digital Booklet.txt (Credits & Review){OFF}"
            )
        except Exception as e:
            safe_print(f"{RED}[!] Erro criando booklet: {e}{OFF}")

    async def _fetch_qobuz_lyrics_json(self, track_id, language=None):
        try:
            params = {"track_id": track_id}
            if language:
                params["language"] = language
            params["request_ts"] = int(time.time())
            params["request_sig"] = self.client._modern_sig(
                "track/lyricsUrl", params, self.client.sec
            )

            r = await self.client.session.request(
                "get", self.client.base + "track/lyricsUrl", params=params
            )
            if r.status_code != 200:
                return None
            lyrics_url_meta = r.json()

            lyrics_json_url = None
            if isinstance(lyrics_url_meta, dict):
                lyrics_json_url = lyrics_url_meta.get("url") or lyrics_url_meta.get(
                    "lyrics_url"
                )
                if not lyrics_json_url:
                    for k, v in lyrics_url_meta.items():
                        if "url" in k.lower() and isinstance(v, str):
                            lyrics_json_url = v
                            break

            if not lyrics_json_url:
                return None

            resp = await self.http_session.get(lyrics_json_url, timeout=12)
            if resp.status_code in (403, 404):
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None

    def _append_lyrics_to_booklet(self, dirn, album_title):
        if abort_event.is_set():
            return

        safe_title = sanitize_filename(album_title)
        tracklist_path = os.path.join(dirn, f"{safe_title} - Tracklist.txt")
        if not os.path.isfile(tracklist_path):
            return

        audio_files = []
        for root, _, files in os.walk(dirn):
            for f in files:
                if f.lower().endswith((".flac", ".mp3")):
                    audio_files.append(os.path.join(root, f))
        audio_files.sort()

        lyrics_to_append = []

        for audio_path in audio_files:
            base_path = os.path.splitext(audio_path)[0]
            lrc_path, txt_path = f"{base_path}.lrc", f"{base_path}.txt"
            base_name = os.path.basename(base_path)
            lyrics_text = ""

            if os.path.exists(lrc_path):
                with open(lrc_path, "r", encoding="utf-8") as f:
                    raw_lyrics = f.read()
                clean_lyrics = re.sub(
                    r"\[[a-zA-Z]+:.*?\]\n?|\[\d{2,}:\d{2}\.\d{2,3}\]", "", raw_lyrics
                )
                clean_lines = [
                    line.strip()
                    for line in clean_lyrics.splitlines()
                    if line.strip() or (lyrics_text and lyrics_text[-1] != "")
                ]
                lyrics_text = "\n".join(clean_lines).strip()
            elif os.path.exists(txt_path) and "Tracklist" not in txt_path:
                with open(txt_path, "r", encoding="utf-8") as f:
                    lyrics_text = f.read().strip()

            if lyrics_text:
                lyrics_to_append.append(f"--- {base_name} ---\n\n{lyrics_text}\n\n")

        if lyrics_to_append:
            try:
                with open(tracklist_path, "a", encoding="utf-8") as f:
                    f.write("\n" + "=" * 70 + "\nALBUM LYRICS\n" + "=" * 70 + "\n\n")
                    f.writelines(lyrics_to_append)
                safe_print(
                    f"{CYAN}[+] Letras formatadas e anexadas ao Digital Booklet.{OFF}"
                )
            except Exception:
                pass


def _get_description(item: dict, track_title, multiple=None):
    downloading_title = (
        f"{track_title} [{item.get('bit_depth', '')}/{item.get('sampling_rate', '')}]"
    )
    if multiple:
        downloading_title = f"[CD {multiple}] {downloading_title}"
    return downloading_title


async def tqdm_download(
    url_or_callable,
    fname,
    track_name,
    is_parallel=False,
    session=None,
    position_pool=None,
):
    if abort_event.is_set():
        return
    G, Y, C, R = GREEN, YELLOW, CYAN, RESET

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
        "Connection": "keep-alive",
    }

    position = position_pool.acquire() if (is_parallel and position_pool) else 0

    if not is_parallel:
        safe_print(f"{C}[+] Em Progresso: {track_name}{R}")
        tqdm_desc = f"  {R}⬇️{R}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ncols = None
        dynamic_ncols = True
    else:
        desc_len = position_pool.desc_len if position_pool else 14
        short_name = (
            track_name
            if len(track_name) <= desc_len
            else track_name[: desc_len - 3] + "..."
        )
        tqdm_desc = f" {short_name}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"
        ncols = position_pool.ncols if position_pool else _get_safe_ncols()
        dynamic_ncols = False

    downloaded_size = 0
    total_size = 0

    owns_session = session is None
    timeout_cfg = httpx.Timeout(60.0, connect=10.0)
    http = session or httpx.AsyncClient(follow_redirects=True)

    try:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(5),
                wait=wait_exponential(multiplier=2, min=2, max=32),
                retry=retry_if_not_exception_type(
                    (_PermanentDownloadError, KeyboardInterrupt, SystemExit)
                ),
                reraise=True,
            ):
                with attempt:
                    if abort_event.is_set():
                        return

                    url = (
                        url_or_callable()
                        if callable(url_or_callable)
                        else url_or_callable
                    )

                    if downloaded_size > 0:
                        headers["Range"] = f"bytes={downloaded_size}-"
                        mode = "ab"
                    else:
                        headers["Range"] = "bytes=0-"
                        mode = "wb"

                    if attempt.retry_state.attempt_number > 1:
                        _n = attempt.retry_state.attempt_number
                        safe_print(
                            f"\n{Y}[!] Falha de Rede. Tentativa {_n}/5 para {track_name}{R}"
                        )

                    async with http.stream(
                        "GET", url, headers=headers, timeout=timeout_cfg
                    ) as r:
                        if r.status_code == 416:
                            return
                        if r.status_code == 404:
                            raise _PermanentDownloadError(
                                "HTTP 404: arquivo não encontrado no servidor."
                            )
                        if r.status_code in (401, 403, 451):
                            raise _PermanentDownloadError(
                                f"HTTP {r.status_code}: faixa indisponivel (bloqueio de "
                                f"região, direitos autorais ou sessão expirada)."
                            )
                        if r.status_code not in [200, 206]:
                            raise Exception(f"Status do servidor: {r.status_code}")

                        if total_size == 0:
                            total_size = downloaded_size + int(
                                r.headers.get("content-length", 0)
                            )

                        if (
                            is_parallel
                            and downloaded_size == 0
                            and attempt.retry_state.attempt_number == 1
                        ):
                            size_mb = total_size / (1024 * 1024) if total_size else 0
                            safe_print(
                                f"{C}[+] Em Progresso: {track_name} [{size_mb:.1f} MB]{R}"
                            )

                        async with aiofiles.open(fname, mode) as file:
                            with tqdm(
                                total=total_size,
                                unit="iB",
                                unit_scale=True,
                                unit_divisor=1024,
                                desc=tqdm_desc,
                                initial=downloaded_size,
                                bar_format=b_format,
                                position=position,
                                leave=False,
                                ncols=ncols,
                                dynamic_ncols=dynamic_ncols,
                                disable=is_parallel,
                            ) as bar:
                                async for data in r.aiter_bytes(chunk_size=524288):
                                    if abort_event.is_set():
                                        return
                                    if data:
                                        size = await file.write(data)
                                        downloaded_size += size
                                        bar.update(size)

                        if downloaded_size >= total_size:
                            safe_print(f"{G} └─ Concluído: {track_name}{R}")
                            return

        except _PermanentDownloadError as e:
            if os.path.exists(fname):
                os.remove(fname)
            safe_print(f"{Y}[!] Indisponível: {track_name} ({e}){R}")
            raise

        except (KeyboardInterrupt, SystemExit):
            abort_event.set()
            if os.path.exists(fname):
                try:
                    os.remove(fname)
                except OSError:
                    pass
            return

        except Exception as e:
            if os.path.exists(fname):
                os.remove(fname)
            raise Exception(
                f"Timeout definitivo após 5 tentativas. Ultimo erro: {e}"
            ) from e

        if downloaded_size < total_size and not abort_event.is_set():
            if os.path.exists(fname):
                os.remove(fname)
            raise Exception("Download Incompleto")
    finally:
        if owns_session:
            try:
                await http.aclose()
            except Exception:
                pass
        if is_parallel and position_pool:
            position_pool.release(position)


def _get_title(item_dict):
    item_title = item_dict.get("title")
    version = item_dict.get("version")
    if version:
        item_title = (
            f"{item_title} ({version})"
            if version.lower() not in item_title.lower()
            else item_title
        )
    return item_title


def _resolve_art_url(item, art_size, og_quality=False):
    """
    Aplica a mesma substituicao de resolucao que sempre foi feita
    internamente (troca o "_600." da URL pelo tamanho pedido). Extraida
    como funcao separada para permitir COMPARAR duas URLs resolvidas --
    por exemplo, saved_art_size vs embedded_art_size -- sem duplicar essa
    logica em outro lugar.
    """
    if og_quality:
        art_size = "org"
    if art_size in ["50", "100", "150", "300", "600", "max", "org"]:
        return item.replace("_600.", f"_{art_size}.")
    return item


async def _get_extra(
    item,
    dirn,
    extra="cover.jpg",
    art_size=None,
    og_quality=False,
    session=None,
    label="file",
    is_parallel=False,
    position_pool=None,
):
    if abort_event.is_set():
        return
    extra_file = os.path.join(dirn, extra)
    if os.path.isfile(extra_file):
        safe_print(f"    {YELLOW}ℹ️ Pulando {label}: {extra} (Já baixado){OFF}")
        return

    item = _resolve_art_url(item, art_size, og_quality)

    try:
        await tqdm_download(
            item,
            extra_file,
            extra,
            is_parallel=is_parallel,
            session=session,
            position_pool=position_pool,
        )
    except Exception as e:
        safe_print(
            f"    {YELLOW}ℹ️ Pulando {label} '{extra}': URL inacessível ({e}){OFF}"
        )


async def _download_bytes_with_limit(url, session, max_bytes, headers=None):
    """
    Baixa uma URL inteira em memoria, abortando cedo se passar de
    max_bytes (sem gastar banda/tempo baixando o resto de uma imagem
    que a gente ja sabe que vai descartar). Devolve os bytes, ou None se
    a resposta veio vazia, deu erro, ou estourou o limite.

    Usada so' para capas (arquivos pequenos, cabe tudo em memoria) --
    NAO usar isso pra audio, que continua no fluxo de streaming em disco
    de tqdm_download (com retry/resume por Range).
    """
    owns_session = session is None
    http = session or httpx.AsyncClient(follow_redirects=True)
    try:
        async with http.stream(
            "GET", url, headers=headers, timeout=httpx.Timeout(20.0, connect=10.0)
        ) as r:
            if r.status_code != 200:
                return None
            content_length = int(r.headers.get("content-length", 0) or 0)
            if content_length and content_length > max_bytes:
                return None
            chunks = []
            total = 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                if total > max_bytes:
                    return None
                chunks.append(chunk)
            if total == 0:
                return None
            return b"".join(chunks)
    except Exception as e:
        logger.debug(f"Falha ao baixar capa em memoria ({url}): {e}")
        return None
    finally:
        if owns_session:
            await http.aclose()


_APPLE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
    )
}


async def _try_apple_cover_bytes(
    session, artist=None, album=None, upc=None, isrc=None, track_title=None
):
    """
    Tenta achar E baixar a capa da Apple, em cascata de resolucoes ate
    caber em MAX_COVER_BYTES. Devolve os bytes prontos, ou None se a
    Apple nao tiver o album/faixa com confianca suficiente, ou se
    nenhuma resolucao coube no limite -- em ambos os casos quem chamou
    deve cair pro Qobuz.

    Uma unica capa da Apple serve tanto a versao "salva" quanto a "de
    embed": diferente da Qobuz, a resolucao aqui nao segue
    saved_art_size/embedded_art_size (que sao conceitos especificos da
    Qobuz) -- e' sempre a maior que couber no limite de tamanho.
    """
    if not (artist and album):
        return None

    try:
        apple_url = await get_apple_hq_cover(
            session=session,
            upc=upc,
            isrc=isrc,
            artist=artist,
            album=album,
            track_title=track_title,
        )
    except Exception as e:
        logger.debug(f"Busca de capa na Apple falhou, indo pra Qobuz: {e}")
        return None

    if not apple_url:
        return None

    for tamanho in _APPLE_COVER_SIZES:
        url_tentativa = re.sub(r"\d+x\d+bb", tamanho, apple_url)
        dados = await _download_bytes_with_limit(
            url_tentativa, session, MAX_COVER_BYTES, headers=_APPLE_HEADERS
        )
        if dados:
            return dados

    logger.debug(
        "    ℹ️ Capa da Apple encontrada mas nenhuma resolucao coube em "
        f"    {MAX_COVER_BYTES // (1024 * 1024)}MB, usando Qobuz."
    )
    return None


async def _fetch_qobuz_cover_bytes(qobuz_item, art_size, session):
    qobuz_url = _resolve_art_url(qobuz_item, art_size)
    return await _download_bytes_with_limit(
        qobuz_url, session, MAX_COVER_BYTES, headers=_APPLE_HEADERS
    )


async def _get_cover_and_embed(
    item,
    dirn,
    save_cover,
    embed_art,
    saved_name,
    embed_name,
    saved_art_size,
    embedded_art_size,
    session=None,
    is_parallel=False,
    position_pool=None,
    artist=None,
    album=None,
    upc=None,
    isrc=None,
    track_title=None,
):
    """
    Baixa a capa salva (cover.jpg) e a capa de embed.

    Prioridade: capa em alta resolucao da Apple (com cascata de
    resolucoes ate caber em MAX_COVER_BYTES) -- so' cai pra Qobuz se a
    Apple nao encontrar o album/faixa com confianca suficiente, ou se
    nenhuma resolucao da Apple coube no limite de tamanho.

    Como a origem final so' e' conhecida depois de tentar baixar (Apple
    pode falhar em tempo real por rede, mesmo tendo achado a URL), a
    capa "salva" e a "de embed" sao resolvidas em uma unica tentativa e
    reaproveitadas uma pra outra sempre que possivel -- evita bater na
    Apple/Qobuz duas vezes pra' baixar essencialmente a mesma imagem.
    """
    if abort_event.is_set():
        return

    if not save_cover and not embed_art:
        return

    saved_file = os.path.join(dirn, saved_name)
    embed_file = os.path.join(dirn, embed_name) if embed_name else None

    precisa_salva = save_cover and not os.path.isfile(saved_file)
    precisa_embed = embed_art and embed_file and not os.path.isfile(embed_file)

    if save_cover and not precisa_salva:
        safe_print(f"    {YELLOW}ℹ️ Pulando cover art: {saved_name} (Já baixado){OFF}")
    if embed_art and embed_file and not precisa_embed:
        safe_print(
            f"    {YELLOW}ℹ️ Ignorando arte da capa incorporada: {embed_name} (Já baixado){OFF}"
        )

    if not precisa_salva and not precisa_embed:
        return

    async def _gravar(caminho, dados, rotulo, rotulo_origem):
        try:
            async with aiofiles.open(caminho, "wb") as f:
                await f.write(dados)
            safe_print(
                f"    {GREEN}[+] {rotulo} ({rotulo_origem}): {os.path.basename(caminho)}{OFF}"
            )
            return True
        except OSError as e:
            safe_print(
                f"    {YELLOW}[!] Falha ao salvar {os.path.basename(caminho)}: {e}{OFF}"
            )
            return False

    # 1) Tenta a Apple uma unica vez -- se achar, a mesma imagem serve
    # tanto a versao salva quanto a de embed (nao ha' distincao de
    # "saved_art_size" vs "embedded_art_size" pra' capa da Apple).
    apple_bytes = await _try_apple_cover_bytes(
        session, artist=artist, album=album, upc=upc, isrc=isrc, track_title=track_title
    )
    if apple_bytes:
        if precisa_salva:
            await _gravar(saved_file, apple_bytes, "Capa salva", "Apple (HQ)")
        if precisa_embed:
            await _gravar(embed_file, apple_bytes, "Capa de embed", "Apple (HQ)")
        return

    # 2) Fallback: Qobuz, respeitando saved_art_size/embedded_art_size separadamente (igual ao comportamento original), reaproveitando o arquivo salvo pro embed quando os dois tamanhos resolvem pra' mesma URL -- evita baixar a mesma imagem duas vezes no caso mais comum.
    saved_url = _resolve_art_url(item, saved_art_size) if precisa_salva else None
    embed_url = _resolve_art_url(item, embedded_art_size) if precisa_embed else None

    if precisa_salva:
        dados = await _fetch_qobuz_cover_bytes(item, saved_art_size, session)
        if dados:
            await _gravar(saved_file, dados, "Capa salva", "Qobuz")
        else:
            safe_print(f"    {YELLOW}ℹ️ Pulando capa: nenhuma fonte disponível{OFF}")

    if not precisa_embed:
        return

    if precisa_salva and saved_url == embed_url and os.path.isfile(saved_file):
        try:
            shutil.copyfile(saved_file, embed_file)
            safe_print(f"   {MUTED}🌁 Reutilizando cover.jpg, para o embed..{OFF}")
            return
        except OSError as e:
            logger.debug(f"Falha ao copiar cover.jpg pra embed: {e}")

    dados = await _fetch_qobuz_cover_bytes(item, embedded_art_size, session)
    if dados:
        await _gravar(embed_file, dados, "Capa de embed", "Qobuz")
    else:
        safe_print(
            f"    {YELLOW}ℹ️ Pulando arte incorporada: nenhuma fonte disponível{OFF}"
        )


def _clean_format_str(folder: str, track: str, file_format: str) -> Tuple[str, str]:
    final = []
    for _i, fs in enumerate((folder, track)):
        if fs.endswith(".mp3"):
            fs = fs[:-4]
        elif fs.endswith(".flac"):
            fs = fs[:-5]
        fs = fs.strip()
        final.append(fs)
    return tuple(final)


def _safe_get(d: dict, *keys, default=None):
    curr = d
    res = default
    for key in keys:
        res = curr.get(key, default)
        if res == default or not hasattr(res, "__getitem__"):
            return res
        else:
            curr = res
    return res


def _artist_label(item_dict: dict, fallback: str = "") -> str:
    """
    Nome(s) de artista "oficial(is)" de um item da Qobuz -- faixa OU album
    -- baseado nos artistas marcados como main-artist na propria metadata
    (`get_album_artist`, ja usado pra tag de arquivo). Quando ha mais de um
    main-artist creditado na MESMA faixa/release (ex.: uma colaboracao ou
    duo genuino), junta os nomes por virgula ("Artista A, Artista B") em
    vez de usar so o performer daquela faixa isolada, que pode capturar
    so um nome mesmo quando ha colaboracao.

    Usado apenas para popular o report.json (artista por faixa e do
    cabecalho/"artista_album") -- NAO substitui `artist_name`/`artist`
    usados em nomeacao de pasta/arquivo e tags, que continuam como estavam.

    Importante: isso e diferente de "Vários Artistas", que so aparece no
    CABECALHO da pasta quando FAIXAS DE RELEASES DIFERENTES acabam juntas
    numa playlist/lote (ver `_recalc_artista` em postprocess.py) -- aqui
    estamos sempre falando dos artistas de UM release/faixa so.
    """
    try:
        nomes = [n for n in (get_album_artist(item_dict or {}) or []) if n]
    except Exception:
        nomes = []
    if nomes:
        return ", ".join(nomes)
    return fallback


async def tqdm_download_segments(
    track_url_dict,
    fname,
    track_name,
    is_parallel=False,
    session=None,
    segment_workers=None,
    position_pool=None,
):
    if abort_event.is_set():
        return
    G, C, R = GREEN, CYAN, RESET

    tmp_fname = fname + ".mp4"
    n_segments = track_url_dict["n_segments"]
    url_template = track_url_dict["url_template"]
    raw_key = track_url_dict["raw_key"]

    workers = segment_workers if segment_workers else 4

    owns_session = session is None
    timeout_cfg = httpx.Timeout(60.0, connect=10.0)
    http = session or httpx.AsyncClient(follow_redirects=True, timeout=timeout_cfg)

    async def get_seg_size(seg_num):
        url = url_template.replace("$SEGMENT$", str(seg_num))
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=1, max=10),
                retry=retry_if_not_exception_type((KeyboardInterrupt, SystemExit)),
                reraise=True,
            ):
                with attempt:
                    if abort_event.is_set():
                        return 0
                    r = await http.head(url, timeout=5)
                    return int(r.headers.get("content-length", 0))
        except Exception as e:
            logger.debug(f"HEAD falhou para segmento (assumindo tamanho 0): {e}")
            return 0

    tasks_size = [get_seg_size(i) for i in range(n_segments + 1)]
    sizes = await asyncio.gather(*tasks_size)
    total_size = sum(sizes)

    position = position_pool.acquire() if (is_parallel and position_pool) else 0

    if is_parallel:
        size_mb = total_size / (1024 * 1024) if total_size else 0
        safe_print(f"{C}[+] Em progresso: {track_name} [{size_mb:.1f} MB]{R}")
        desc_len = position_pool.desc_len if position_pool else 14
        short_name = (
            track_name
            if len(track_name) <= desc_len
            else track_name[: desc_len - 3] + "..."
        )
        tqdm_desc = f" {short_name}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"
        ncols = position_pool.ncols if position_pool else _get_safe_ncols()
        dynamic_ncols = False
    else:
        safe_print(f"{C}[+] Em Progresso: {track_name}{R}")
        tqdm_desc = f"  {R}↪️{R}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ncols = None
        dynamic_ncols = True

    async def fetch_segment_fluid(seg_num):
        url = url_template.replace("$SEGMENT$", str(seg_num))
        seg_data = bytearray()

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(5),
            wait=wait_exponential(multiplier=2, min=2, max=32),
            retry=retry_if_not_exception_type((KeyboardInterrupt, SystemExit)),
            reraise=True,
        ):
            with attempt:
                if abort_event.is_set():
                    return bytearray()

                if attempt.retry_state.attempt_number > 1:
                    _n = attempt.retry_state.attempt_number
                    safe_print(
                        f"\n{YELLOW}[!] Reconectando segmento {seg_num}. "
                        f"Tentativa {_n}/5 para {track_name}{OFF}"
                    )

                seg_data.clear()
                async with http.stream("GET", url, timeout=15) as r:
                    r.raise_for_status()
                    async for chunk in r.aiter_bytes(chunk_size=524288):
                        if abort_event.is_set():
                            return bytearray()
                        seg_data.extend(chunk)
                        bar.update(len(chunk))
                return seg_data

    try:
        async with aiofiles.open(tmp_fname, "wb") as file:
            with tqdm(
                total=total_size,
                unit="iB",
                unit_scale=True,
                unit_divisor=1024,
                desc=tqdm_desc,
                bar_format=b_format,
                position=position,
                leave=False,
                ncols=ncols,
                dynamic_ncols=dynamic_ncols,
                disable=is_parallel,
            ) as bar:

                segment_uuid = None

                for i in range(2):
                    seg_data = await fetch_segment_fluid(i)
                    if abort_event.is_set():
                        return
                    if i == 1:
                        segment_uuid = _get_qobuz_segment_uuid(seg_data)
                        if segment_uuid is None:
                            raise ConnectionError(
                                f"Não foi possivel encontrar o UUID do segmento para {fname}"
                            )
                        decrypted_data = _decrypt_qobuz_segment(
                            seg_data, raw_key, segment_uuid
                        )

                if n_segments >= 2:
                    semaphore = asyncio.Semaphore(workers)

                    async def bounded_fetch(i):
                        async with semaphore:
                            return await fetch_segment_fluid(i)

                    tasks_seg = [bounded_fetch(i) for i in range(2, n_segments + 1)]
                    results = await asyncio.gather(*tasks_seg)

                    for seg_data in results:
                        if not abort_event.is_set():
                            decrypted_data = _decrypt_qobuz_segment(
                                seg_data, raw_key, segment_uuid
                            )
                            await file.write(decrypted_data)

        if abort_event.is_set():
            return
        if not is_parallel:
            safe_print(f" {G} └─ Montando o arquivo FLAC final...{R}")

        remux = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            tmp_fname,
            "-c:a",
            "copy",
            "-f",
            "flac",
            fname,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await remux.communicate()

        if remux.returncode != 0:
            raise ConnectionError(
                f"Falha no remux do FFmpeg para {fname}: {stderr.decode()}"
            )

        safe_print(f"{G} └─ Concluído: {track_name}{R}")

    except (KeyboardInterrupt, SystemExit):
        abort_event.set()
        return

    except Exception as e:
        if not abort_event.is_set():
            raise Exception(f"Download fatiado falhou: {e}") from e

    finally:
        if os.path.isfile(tmp_fname):
            try:
                os.remove(tmp_fname)
            except OSError:
                pass
        if owns_session:
            try:
                await http.aclose()
            except Exception as e:
                logger.debug(
                    f"Falha ao fechar sessão HTTP no cleanup final (ignorado): {e}"
                )
        if is_parallel and position_pool:
            position_pool.release(position)


def _get_qobuz_segment_uuid(segment_data):
    pos = 0
    while pos + 24 <= len(segment_data):
        size = int.from_bytes(segment_data[pos : pos + 4], "big")
        if size <= 0 or pos + size > len(segment_data):
            break
        if bytes(segment_data[pos + 4 : pos + 8]) == b"uuid":
            return bytes(segment_data[pos + 8 : pos + 24])
        pos += size
    return None


def _decrypt_qobuz_segment(segment_data, raw_key, segment_uuid):
    if segment_uuid is None:
        return bytes(segment_data)

    buf = bytearray(segment_data)
    pos = 0
    while pos + 8 <= len(buf):
        size = int.from_bytes(buf[pos : pos + 4], "big")
        if size <= 0 or pos + size > len(buf):
            break

        if (
            bytes(buf[pos + 4 : pos + 8]) == b"uuid"
            and bytes(buf[pos + 8 : pos + 24]) == segment_uuid
        ):
            pointer = pos + 28
            data_end = pos + int.from_bytes(buf[pointer : pointer + 4], "big")
            pointer += 4
            counter_len = buf[pointer]
            pointer += 1
            frame_count = int.from_bytes(buf[pointer : pointer + 3], "big")
            pointer += 3

            for _ in range(frame_count):
                frame_len = int.from_bytes(buf[pointer : pointer + 4], "big")
                pointer += 6
                flags = int.from_bytes(buf[pointer : pointer + 2], "big")
                pointer += 2
                frame_start, data_end = data_end, data_end + frame_len

                if flags:
                    counter = bytes(buf[pointer : pointer + counter_len]) + (
                        b"\x00" * (16 - counter_len)
                    )
                    decryptor = Cipher(
                        algorithms.AES(raw_key), modes.CTR(counter)
                    ).decryptor()
                    plaintext = (
                        decryptor.update(bytes(buf[frame_start:data_end]))
                        + decryptor.finalize()
                    )
                    buf[frame_start:data_end] = plaintext
                pointer += counter_len
        pos += size
    return bytes(buf)


async def _download_goodies(
    album_meta, dirn, session=None, is_parallel=False, position_pool=None
):
    if abort_event.is_set():
        return
    try:
        for goody in album_meta.get("goodies", []):
            if abort_event.is_set():
                break
            if not goody.get("url"):
                continue
            goody_name = sanitize_filename(
                clean_filename(f'{album_meta.get("title")} ({goody.get("id")}).pdf')
            )
            await _get_extra(
                goody.get("url"),
                dirn,
                extra=goody_name,
                session=session,
                label="booklet PDF",
                is_parallel=is_parallel,
                position_pool=position_pool,
            )
    except Exception as e:
        logger.error(f"{RED}Erro ao baixar goodies: {e}", exc_info=True)


def _clean_embed_art(dirn, settings=None):
    embed_file = os.path.join(dirn, EMB_COVER_NAME)
    if os.path.exists(embed_file):
        try:
            time.sleep(0.5)
            os.remove(embed_file)
        except OSError:
            pass
