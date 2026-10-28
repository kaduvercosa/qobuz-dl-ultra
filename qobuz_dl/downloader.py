# ============================================================================
# downloader.py -- motor de download do qobuz_dl.
# Aqui mora a classe Download (baixa album/faixa/playlist), a logica de
# fallback de qualidade, o download segmentado (bypass de bloqueio Akamai)
# e as funcoes de apoio (capa, booklet, letras, nomes de arquivo/pasta).
# Ponto de entrada tipico: Download(...).download_id_by_type(...).
# ============================================================================
import asyncio
import logging
import os
import re
import shutil
import signal
import sys
import textwrap
import threading
import time
from typing import Any, Optional

import aiofiles
import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathvalidate import sanitize_filename
from tenacity import (
    AsyncRetrying,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)
from tqdm import tqdm

import qobuz_dl.metadata as metadata
import qobuz_dl.postprocess as postprocess
from qobuz_dl import musicbrainz, ui
from qobuz_dl.color import (
    OFF,
    RED,
    RESET,
)
from qobuz_dl.color import (
    WARNING as YELLOW,
)
from qobuz_dl.concurrency import run_bounded
from qobuz_dl.constants import (
    DEFAULT_FOLDER,
    DEFAULT_MULTIPLE_DISC_TRACK,
    DEFAULT_TRACK,
)
from qobuz_dl.db import handle_download_id
from qobuz_dl.exceptions import NonStreamable
from qobuz_dl.executors import heavy_executor
from qobuz_dl.fileio import replace_atomic
from qobuz_dl.http_download import (
    DownloadAborted,
    DownloadError,
    download_file,
    make_client,
)
from qobuz_dl.http_download import (
    PermanentDownloadError as HttpPermanentDownloadError,
)
from qobuz_dl.playlist_links import (
    absorb_playlist_copies,
    scan_dest_folder,
)
from qobuz_dl.resilient_http import request_async
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.storage import (
    InsufficientStorageError,
    ensure_free_space,
    estimate_audio_bytes,
    format_bytes,
)
from qobuz_dl.track_identity import (
    STATE_IN_PROGRESS,
    STATE_NEW,
    build_name_stems,
    release_claims,
    resolve_output_path,
)
from qobuz_dl.utils import (
    album_titulo_completo,
    classify_release_type,
    clean_filename,
    get_apple_hq_cover,
    qobuz_release_kind,
    qobuz_track_title,
    verify_audio_integrity,
)

from .lyrics_engine import LyricsEngine

# Ordem de fallback de qualidade quando o tier pedido falha por motivo de rede/servidor (NAO usado para faixas indisponiveis -- ver _PermanentDownloadError). 27=Hi-Res >96kHz | 7=Hi-Res 96kHz | 6=CD 16bit/44.1kHz | 5=MP3 320kbps
FALLBACK_TIERS = [27, 7, 6, 5]


def _flatten_artists(artist_data):
    """Flatten an artist value to the string used by download metadata."""
    if isinstance(artist_data, list) and artist_data:
        return str(artist_data[0])
    return str(artist_data) if artist_data else ""


from qobuz_dl.download_utils import (
    _artist_label,
    _clean_format_str,
    _desc_budget,  # noqa: F401 -- usado só em testes
    _get_safe_ncols,
    _PositionPool,
    _safe_get,
    _short_track_name,
    create_missing_placeholder,
    format_release_type,
    format_track_label,
    get_album_artist,
    is_track_streamable,
    process_folder_format_with_subdirs,
)

# ANTES: as 10 funções/classe acima estavam definidas aqui direto. Movidas
# pra qobuz_dl/download_utils.py (passo 1 de quebrar este arquivo em
# módulos menores -- downloader.py tinha 2526 linhas) porque eram
# exatamente as que já tinham cobertura real em
# tests/unit/test_downloader_pure.py antes da extração. Reimportadas aqui
# pra manter todo o resto deste arquivo (e qualquer código externo que já
# fazia `from qobuz_dl.downloader import format_release_type` etc.)
# funcionando sem precisar mudar uma linha.


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


_dir_locks: dict = {}


def _get_dir_lock(dirn: str) -> asyncio.Lock:
    """Get or create a lock for a directory to prevent concurrent access."""
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
    (inicio/fim de faixa), para frontends externos (scripts, apps) conseguirem
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


legacy_flag = False


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

# Rotulos de origem da capa gravados nas tags ("Capa: ..."). Definidos em
# metadata.py e iguais aos do comando `tags` (retro_tags).
COVER_SOURCE_APPLE_LABEL = metadata.COVER_SOURCE_APPLE
COVER_SOURCE_QOBUZ_LABEL = metadata.COVER_SOURCE_QOBUZ

# Origem das capas ja' gravadas NESTA execucao, por caminho do arquivo (embed,
# ou salva quando nao ha' embed). Faixas seguintes do mesmo album reaproveitam
# o arquivo e precisam saber de onde ele veio.
_COVER_SOURCES: dict = {}

# Notas curtas (ex.: "capa Apple (HQ)") que a faixa de playlist/lote deixa para a
# linha "Em Progresso" dela, indexadas pelo mesmo rótulo "[NN] título" que essa
# linha usa. Em playlist/lote a capa não ganha linhas próprias: várias faixas
# rodam ao mesmo tempo e as linhas de capa se misturariam com as das outras.
_PROGRESS_NOTES: dict = {}


def _progress_tail(track_name, size_mb=None):
    """Métricas da linha "Em Progresso": tamanho (se conhecido) + nota da capa."""
    tamanho = f"{size_mb:.1f} MB" if size_mb is not None else ""
    return ui.join_meta(tamanho, _PROGRESS_NOTES.get(track_name, ""))


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
        genius_token: Optional[str] = None,
        no_credits: bool = False,
        settings: Optional[QobuzDLSettings] = None,
        download_db=None,
        is_playlist: bool = False,
        playlist_track_number: Optional[int] = None,
        booklet_only: bool = False,
        playlist_as_albums: bool = False,
        playlist_title: Optional[str] = None,
        playlist_id: Optional[str] = None,
    ):
        """Initialize downloader with client, item ID, quality settings, and formatting options."""
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

        self.http_session = make_client(
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

        self.is_playlist = is_playlist
        self.playlist_track_number = playlist_track_number
        self.playlist_as_albums = playlist_as_albums
        self.playlist_title = playlist_title
        self.playlist_id = playlist_id

        self._original_folder_format = folder_format or DEFAULT_FOLDER
        self._original_track_format = track_format or DEFAULT_TRACK
        self._original_multiple_disc_track_format = (
            settings.multiple_disc_track_format
            if settings is not None
            else DEFAULT_MULTIPLE_DISC_TRACK
        )

    async def download_id_by_type(
        self, track=True, is_parallel=False, position_pool=None, suppress_header=False
    ):
        """Download an item by ID and type (album, track, artist, etc.)."""
        self.folder_format = self._original_folder_format
        self.track_format = self._original_track_format
        self.settings.multiple_disc_track_format = (
            self._original_multiple_disc_track_format
        )

        try:
            if not track:
                return await self.download_release(suppress_header=suppress_header)
            else:
                return await self.download_track(
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                    suppress_header=suppress_header,
                )
        finally:
            await self.close_session()

    async def close_session(self):
        """Close the HTTP session and release resources."""
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

    async def _process_track(
        self,
        idx,
        i,
        *,
        dirn,
        album_meta,
        is_multiple,
        is_parallel,
        position_pool,
        semaphore,
        report_track,
        cover_source=None,
    ):
        """Processa e baixa UMA faixa do álbum.

        Decide se a faixa é pulada (não-streamable ou apenas amostra),
        tenta obter a URL de stream, baixa+tageia, e reporta o resultado
        via `report_track` (callback assíncrono).

        EXTRAÍDO de dentro de `download_release()`, onde antes vivia como
        uma função aninhada chamada `process_track` -- fechava sobre
        `dirn`/`album_meta`/`semaphore`/etc. em vez de recebê-los como
        parâmetro, o que tornava impossível chamar/testar essa lógica sem
        rodar `download_release()` inteiro (metadados do álbum, formato,
        diretórios, handler de SIGINT, capa...). Comportamento idêntico
        ao original -- só trocou closure por parâmetros explícitos; o
        `download_release()` agora só tem um `process_track(idx, i)`
        fininho que repassa pra cá (ver mais abaixo).
        """
        if abort_event.is_set():
            return False
        async with semaphore:
            ui.set_track_index(ui.index_from_meta(i, album_meta))

            t_num = str(i.get("track_number", idx + 1)).zfill(2)
            _media_count = album_meta.get("media_count", 1)
            t_label = format_track_label(i, _media_count, default=idx + 1)
            t_title = i.get("title", "Faixa Desconhecida")

            streamable, reason = is_track_streamable(i)
            if not streamable:
                ui.skip(f"Indisponível: {t_title} ({reason})")
                create_missing_placeholder(i, dirn, reason, _media_count)
                await report_track(i, t_num, "pulada", reason)
                return "skipped"

            try:
                parse = await self.client.get_track_url(i["id"], fmt_id=self.quality)
            except Exception as e:
                ui.error(f"Erro de API na faixa [{t_label}] (ID: {i['id']}): {e}")
                create_missing_placeholder(i, dirn, f"Erro de API: {e}", _media_count)
                await report_track(i, t_num, "falha", f"Erro de API: {e}")
                return False

            if "sample" not in parse and parse.get("sampling_rate"):
                is_mp3 = True if int(self.quality) == 5 else False
                letras_info: dict[str, object] = {}
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
                    cover_source=cover_source,
                )
                status = "ok" if res is True else "falha"
                motivo = "" if res is True else "erro"
                await report_track(i, t_num, status, motivo, letras=letras_info)
                return res
            else:
                ui.skip(f"Pulada: {t_title} (Apenas amostra/demo)")
                create_missing_placeholder(
                    i, dirn, "Apenas amostra/demo (30s)", _media_count
                )
                await report_track(i, t_num, "pulada", "Apenas amostra/demo (30s)")
                return "skipped"

    async def _absorb_playlist_copies(self, dest_files=None, dest_dir=None, **reg):
        """Álbum/faixa avulsa concluído: tira das playlists a cópia duplicada.

        `dest_files`: {id da faixa: caminho do arquivo no álbum}; ou
        `dest_dir`: pasta do álbum, varrida aqui (só se o recurso estiver
        ligado). Nunca derruba o download (erro vira log de debug).
        """
        if getattr(self, "is_playlist", False):
            return
        cfg = getattr(self, "settings", None)
        root = getattr(cfg, "library_root", None)
        if not root or not getattr(cfg, "playlist_dedupe", True):
            return
        try:
            if dest_files is None and dest_dir:
                dest_files = await asyncio.to_thread(scan_dest_folder, dest_dir)
            if not dest_files:
                return
            stats = await absorb_playlist_copies(
                root,
                self.download_db,
                dest_files,
                reg={"quality": self.quality, **reg},
            )
        except Exception as e:
            logger.debug(f"Limpeza de duplicatas playlist/álbum falhou: {e}")
            return
        n = len(stats["absorbed"])
        if n:
            ui.ok(
                f"{n} faixa(s) que estavam em playlist agora ficam só neste "
                "álbum (a playlist aponta para elas)"
            )
        for kept in stats["kept"]:
            ui.skip(
                "Cópia da playlist mantida (qualidade maior que a do álbum): "
                f"{os.path.basename(kept)}"
            )

    async def download_release(self, suppress_header=False):
        """Download a full album/release with all tracks."""
        album_meta = await self.client.get_album_meta(self.item_id)

        if not album_meta.get("streamable"):
            raise NonStreamable("Este lançamento nao está disponível para streaming")

        if self.albums_only and (
            album_meta.get("release_type") != "album"
            or album_meta.get("artist").get("name") == "Various Artists"
        ):
            ui.skip(f"Ignorando Single/EP/VA: {album_meta.get('title', 'n/a')}")
            return

        album_title = _get_title(album_meta)
        url = album_meta.get("url", "")
        release_date = album_meta.get("release_date_original", "")

        format_info = await self._get_format(album_meta)
        file_format, quality_met, bit_depth, sampling_rate = format_info

        if not self.downgrade_quality and not quality_met:
            ui.skip(f"Pulando {album_title} pois não atende ao requisito de qualidade")
            return

        if getattr(self.settings, "check_storage", False):
            tracks = album_meta.get("tracks", {}).get("items", [])
            duration = album_meta.get("duration") or sum(
                float(track.get("duration") or 0) for track in tracks
            )
            estimate = estimate_audio_bytes(
                duration,
                self.quality,
                bit_depth=bit_depth,
                sampling_rate=sampling_rate,
            )
            if estimate:
                try:
                    ensure_free_space(self.path, estimate)
                except InsufficientStorageError as exc:
                    ui.error(str(exc))
                    ui.detail(
                        f"Estimativa do álbum {album_title}: {format_bytes(estimate)}."
                    )
                    return False

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
                ui.warn(
                    "Não foi possível renomear a pasta existente para [IN PROGRESS]. "
                    f"Operando em modo padrão. ({e})"
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

        active_workers = max(1, int(getattr(self.settings, "max_workers", 1)))
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
                    ("Título", album_title),
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
        cancelled_by_task = False
        results: list[Any] = []
        abort_event.clear()

        original_sigint = None
        try:
            original_sigint = signal.getsignal(signal.SIGINT)

            def custom_sigint_handler(sig, frame):
                """Handle Ctrl+C during downloads."""
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
                ui.skip("Pulando capa")

            # None = origem desconhecida/sem capa: nenhuma linha "Capa: ..." e'
            # gravada (antes ficava sem valor e levantava NameError).
            cover_source = None
            if self.settings.no_cover and not self.settings.embed_art:
                pass
            else:
                cover_source = await _get_cover_and_embed(
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
                    # Título + versão: "Rare" + "Deluxe" -> "Rare (Deluxe)".
                    # Só o título buscava a capa da edição padrão.
                    album=album_titulo_completo(album_meta),
                    upc=album_meta.get("upc", ""),
                    release_kind=_apple_release_kind(album_meta),
                    # Lista de faixas: é o que separa Deluxe de padrão.
                    album_tracks=[
                        qobuz_track_title(t)
                        for t in (album_meta.get("tracks") or {}).get("items") or []
                    ]
                    or None,
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
                ui.skip("Flag --booklet-only ativa. Pulando faixas de audio.")
                if is_standard_album and working_dirn == inprogress_dirn:
                    try:
                        os.rename(working_dirn, incomplete_dirn)
                    except OSError as e:
                        logger.warning(
                            f"    {YELLOW}[!] Não foi possível renomear a pasta para [INCOMPLETE]. ({e}){OFF}"
                        )
                return True

            # Separa a preparação (booklet, capas) das faixas, nos dois modos.
            ui.separator()

            semaphore = asyncio.Semaphore(active_workers)

            async def _report_track(i, t_num, status, motivo="", letras=None):
                """Report track download result to JSON progress stream."""
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
                """Repassa pra `self._process_track` (ver docstring dela --
                extraída daqui pra ser testável isoladamente). Só existe
                como função aninhada ainda porque fecha sobre variáveis
                locais deste método (`dirn`, `album_meta`, etc.) e sobre
                `_report_track`, que continua aninhada logo acima."""
                return await self._process_track(
                    idx,
                    i,
                    dirn=dirn,
                    album_meta=album_meta,
                    is_multiple=is_multiple,
                    is_parallel=is_parallel,
                    position_pool=position_pool,
                    semaphore=semaphore,
                    report_track=_report_track,
                    cover_source=cover_source,
                )

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

            album_tracks = album_meta["tracks"]["items"]
            completed_ids = set()
            if not getattr(self.settings, "verify_after_download", False):
                completed_ids = await postprocess.resumable_track_ids(dirn)
            results = [None] * len(album_tracks)
            album_track_items = list(enumerate(album_tracks))

            async def _bounded_process_track(_worker_index, entry):
                idx, track = entry
                if str(track.get("id", "")) in completed_ids:
                    index_token = ui.set_track_index(
                        ui.index_from_meta(track, album_meta)
                    )
                    try:
                        ui.skip(
                            f"Pulando: {track.get('title', 'Faixa Desconhecida')} "
                            "(Já existe no dispositivo)"
                        )
                    finally:
                        ui.reset_track_index(index_token)
                    return "existing"
                return await process_track(idx, track)

            try:
                pending_results = await run_bounded(
                    album_track_items,
                    _bounded_process_track,
                    limit=max(1, active_workers),
                )
                for (idx, _track), result in zip(album_track_items, pending_results):
                    results[idx] = result
            except (KeyboardInterrupt, asyncio.CancelledError) as exc:
                abort_event.set()
                aborted_by_user = True
                cancelled_by_task = isinstance(exc, asyncio.CancelledError)
                try:
                    await self.http_session.aclose()
                except Exception as e:
                    # Encerrando por CTRL+C/cancelamento -- fechar a sessao
                    # e' limpeza best-effort, nao pode impedir o `raise`
                    # abaixo de propagar a interrupcao.
                    logger.debug(
                        f"Falha ao fechar http_session durante cancelamento: {e}"
                    )
                raise

            for track_meta, result in zip(album_meta["tracks"]["items"], results):
                if isinstance(result, BaseException):
                    ui.error(
                        f"Faixa {track_meta.get('track_number', '?')} — "
                        f"{track_meta.get('title', 'Faixa Desconhecida')}: "
                        f"{type(result).__name__}: {result}"
                    )
                    logger.error(
                        "Exceção na faixa %s",
                        track_meta.get("id"),
                        exc_info=(type(result), result, result.__traceback__),
                    )
            failed_tracks = sum(
                res is not True and res != "existing" for res in results
            )

            if not abort_event.is_set():
                await asyncio.to_thread(_clean_embed_art, dirn, self.settings)
                if getattr(self, "fetch_lyrics", False) and not self.no_credits:
                    await asyncio.to_thread(
                        self._append_lyrics_to_booklet, dirn, album_title
                    )

        except (KeyboardInterrupt, SystemExit):
            abort_event.set()
            aborted_by_user = True
            ui.error("CTRL+C Interceptado: Protegendo arquivos e pastas...")
        finally:
            try:
                if original_sigint:
                    signal.signal(signal.SIGINT, original_sigint)
            except Exception as e:
                logger.debug(
                    f"Não foi possível restaurar handler de SIGINT original: {e}"
                )

            if aborted_by_user:
                # dá tempo das threads de download terminarem sem congelar o loop
                await asyncio.sleep(1.5)

            final_dirn = working_dirn
            if is_standard_album and working_dirn == inprogress_dirn:
                final_dirn = (
                    target_dirn
                    if (results and failed_tracks == 0 and not aborted_by_user)
                    else incomplete_dirn
                )
                try:
                    os.rename(working_dirn, final_dirn)
                except OSError as e:
                    ui.warn(
                        "Não foi possível renomear a pasta final "
                        f"(o bloqueio do SO ainda pode estar ativo). ({e})"
                    )
                    final_dirn = working_dirn

                if aborted_by_user:
                    ui.warn(
                        "Download abortado. Pasta marcada com sucesso como [INCOMPLETE]."
                    )
                elif failed_tracks > 0:
                    ui.warn(
                        f"Álbum baixado parcialmente ({failed_tracks} faixas puladas). "
                        "Pasta marcada como [INCOMPLETE]."
                    )

            if aborted_by_user and not cancelled_by_task:
                # Ctrl+C deve continuar sendo uma interrupção de usuário. Já um
                # cancelamento programático (Task.cancel) deve propagar
                # CancelledError, sem ser convertido em KeyboardInterrupt.
                raise KeyboardInterrupt

            # [FIX] handle_download_id() agora vive DENTRO deste 'if', junto com db_artist/db_album -- antes havia risco (sinalizado em revisao) de essas variaveis nao existirem quando failed_tracks>0/aborted_by_user e o handle_download_id ainda assim tentar rodar.
            if results and failed_tracks == 0 and not aborted_by_user:
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

                await self._absorb_playlist_copies(
                    dest_dir=final_dirn,
                    file_format=file_format,
                    quality_met=quality_met,
                    bit_depth=bit_depth,
                    sampling_rate=sampling_rate,
                    release_date=release_date,
                    artist=db_artist,
                    album=db_album,
                )

                # Registrar/Atualizar também no library.db para que qualquer download seja mantido no catálogo local
                try:
                    from qobuz_dl.library_cmd import library_db_path
                    from qobuz_dl.library_db import LibraryDB
                    from qobuz_dl.library_scan import mark_album_downloaded

                    lib_db = LibraryDB(library_db_path())
                    lib_id = await asyncio.to_thread(
                        lib_db.upsert_album,
                        "qobuz",
                        self.item_id,
                        db_album,
                        db_artist,
                        track_count=track_count,
                        bit_depth=bit_depth,
                        sample_rate=sampling_rate,
                        release_date=release_date,
                    )
                    await asyncio.to_thread(
                        mark_album_downloaded,
                        lib_db,
                        lib_id,
                        folder=final_dirn,
                        # A sentinela rica é gravada uma única vez abaixo.
                        sentinel_enabled=False,
                    )
                except Exception as _lib_exc:
                    logger.debug(f"Falha ao registrar no library.db: {_lib_exc}")

                # # Sentinela .streamrip.json (dedup pelo disco). Best-effort: nunca derruba o download.
                if getattr(self.settings, "write_sentinel", True):
                    try:
                        from qobuz_dl import sentinel as _sentinel

                        _items = (album_meta.get("tracks") or {}).get("items") or []
                        await asyncio.to_thread(
                            _sentinel.write_sentinel,
                            final_dirn,
                            _sentinel.build_payload(
                                "qobuz",
                                self.item_id,
                                db_album,
                                db_artist,
                                album_meta.get("tracks_count") or len(_items),
                                [
                                    {
                                        "id": str(t.get("id")),
                                        "title": t.get("title", ""),
                                        "success": True,
                                    }
                                    for t in _items
                                ],
                                release_date=release_date,
                                quality={
                                    "format": file_format,
                                    "bit_depth": bit_depth,
                                    "sampling_rate": sampling_rate,
                                },
                            ),
                        )
                    except Exception as _exc:
                        logger.debug(f"Sentinela nao gravada: {_exc}")

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
            existing_count = sum(1 for r in results if r == "existing")
            real_failed = sum(
                1 for r in results if r is False or isinstance(r, BaseException)
            )
            downloaded_count = sum(1 for r in results if r is True)

            ui.summary_downloads(
                "DO ÁLBUM",
                album_title,
                downloaded_count,
                track_count,
                skipped=skipped_count,
                failed=real_failed,
                existing=existing_count,
                notes=(
                    ["Faixas puladas marcadas em .missing.txt"]
                    if skipped_count > 0
                    else []
                ),
            )

        return bool(results) and failed_tracks == 0 and not aborted_by_user

    async def download_track(
        self, is_parallel=False, position_pool=None, suppress_header=False
    ):
        """Download a single track file from Qobuz."""
        parse = await self.client.get_track_url(self.item_id, self.quality)

        track_meta = await self.client.get_track_meta(self.item_id)
        track_title = _get_title(track_meta)

        success = False
        track_attr: dict[str, object] | None = None
        file_format = "Unknown"
        quality_met = False
        bit_depth: object | None = None
        sampling_rate: str | int | float | None = None
        dirn: str | None = None
        url = ""
        release_date = ""

        is_sample_only = "sample" in parse or not parse.get("sampling_rate")

        if is_sample_only:
            ui.skip(f"{track_title} (Apenas amostra/demo (30s))")
            success = False
            track_attr = None
        else:
            # Posição da faixa NA PLAYLIST. Serve só para exibição (barra de
            # progresso, logs). NÃO sobrescreve track_meta["track_number"]:
            # essa chave é o número da faixa no ÁLBUM e é o que vai para a
            # tag TRACKNUMBER/TRCK. O QOBUZTRACKID (id da faixa) também
            # continua vindo de track_meta["id"], intocado.
            playlist_index = None
            if (
                getattr(self, "is_playlist", False)
                and not getattr(self, "playlist_as_albums", False)
                and self.playlist_track_number
            ):
                playlist_index = self.playlist_track_number

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
                        ("Título", track_title),
                        ("Artista", artist),
                        ("Álbum", album_name),
                        (
                            "Qualidade",
                            (
                                f"{file_format} ({bit_depth}bit/{float(sampling_rate):g}kHz)"
                                if bit_depth and sampling_rate is not None
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
                ui.skip(
                    f"Pulando {track_title} pois não atende ao requisito de qualidade"
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
                if dirn is None:
                    raise ValueError(
                        "Não foi possivel determinar a pasta de destino da faixa."
                    )
                os.makedirs(dirn, exist_ok=True)

                # Em playlist/lote várias faixas rodam juntas: a capa não ganha
                # linhas próprias, vira uma nota na linha "Em Progresso" da faixa
                # (o rótulo "[NN] título" é o mesmo que essa linha usa).
                notas_capa: list[str] | None = (
                    [] if getattr(self, "is_playlist", False) else None
                )
                rotulo_faixa = ""
                if notas_capa is not None:
                    _no = (
                        f"{int(playlist_index):02d}"
                        if playlist_index
                        else format_track_label(
                            track_meta, track_meta.get("media_count", 1)
                        )
                    )
                    rotulo_faixa = f"[{_no}] {_get_title(track_meta)}"

                skip_saved_cover = getattr(self, "is_playlist", False) and not getattr(
                    self, "playlist_as_albums", False
                )

                if skip_saved_cover:
                    if getattr(self, "playlist_track_number", 1) == 1:
                        ui.skip(
                            "Pulando salvamento padrao de capa para manter a pasta da playlist limpa"
                        )
                elif self.settings.no_cover:
                    if notas_capa is None:
                        ui.skip("Pulando capa")

                embed_cover_path = None
                if self.settings.embed_art:
                    unique_embed_name = f".embed_{self.item_id}.jpg"
                    embed_cover_path = os.path.join(dirn, unique_embed_name)
                elif notas_capa is None:
                    ui.skip("Pulando arte incorporada")

                save_cover_now = not skip_saved_cover and not self.settings.no_cover
                cover_source = None
                if save_cover_now or self.settings.embed_art:
                    async with _get_dir_lock(dirn):
                        cover_source = await _get_cover_and_embed(
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
                            album=album_titulo_completo(track_meta.get("album", {})),
                            upc=track_meta.get("album", {}).get("upc", ""),
                            isrc=track_meta.get("isrc", ""),
                            release_kind=_apple_release_kind(
                                track_meta.get("album", {})
                            ),
                            track_title=track_meta.get("title", ""),
                            notas=notas_capa,
                        )
                if notas_capa:
                    _PROGRESS_NOTES[rotulo_faixa] = ui.join_meta(
                        *dict.fromkeys(notas_capa)
                    )

                is_mp3 = True if int(self.quality) == 5 else False

                # Faixa avulsa: separa as capas (preparação) da faixa, como no
                # álbum. Em playlist/lote não há fase de preparação -- as
                # faixas se intercalam --, então ali não entra linha em branco.
                if not getattr(self, "is_playlist", False):
                    ui.separator()

                letras_info: dict[str, object] = {}
                try:
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
                        cover_source=cover_source,
                        display_index=playlist_index,
                    )
                finally:
                    _PROGRESS_NOTES.pop(rotulo_faixa, None)

                if embed_cover_path and os.path.isfile(embed_cover_path):
                    try:
                        os.remove(embed_cover_path)
                    except OSError:
                        # Limpeza best-effort: o arquivo pode já ter sumido.
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

        if success and track_attr is not None and dirn is not None:
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
                saved_path=getattr(self, "last_downloaded_file", dirn),
                url=url,
                release_date=release_date,
                artist=db_artist,
                album=db_album,
            )

            # Faixa avulsa (não playlist): se ela também está numa playlist,
            # fica só esta cópia (ver playlist_links.py).
            _file = getattr(self, "last_downloaded_file", None)
            if _file and os.path.isfile(_file):
                await self._absorb_playlist_copies(
                    dest_files={str(self.item_id): _file},
                    file_format=file_format,
                    quality_met=quality_met,
                    bit_depth=bit_depth,
                    sampling_rate=sampling_rate,
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
            if dirn is not None:
                await postprocess.finalize_report(dirn, completo=bool(success))
            try:
                ui.summary(
                    "RESUMO DA FAIXA",
                    subtitle=track_title,
                    rows=[
                        (
                            "Status",
                            "Concluída com sucesso"
                            if success
                            else "Falhou ou foi pulada",
                            "ok" if success else "err",
                        )
                    ],
                )
            except Exception as e:
                ui.error(f"Erro ao gerar painel de resumo da Faixa: {e}")
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

        return bool(success)

    async def _com_lista_completa_de_faixas(self, track_metadata: dict) -> dict:
        """Faixa avulsa/playlist de álbum multi-disco: completa a lista de faixas.

        Só age com `continuous_track_numbers` ligado (numeração 1..N).

        A API da faixa (`track/get`) não traz a lista de faixas do álbum, e sem
        ela não dá para numerar 1..N. Busca o álbum uma vez (cache por id) e
        devolve uma CÓPIA de `track_metadata` com `album.tracks` preenchido. Em
        qualquer falha devolve o original e a faixa segue com a numeração por
        disco (comportamento anterior).
        """
        if not getattr(self.settings, "continuous_track_numbers", False):
            return track_metadata
        album = track_metadata.get("album") or {}
        total = album.get("tracks_count") or 0
        itens = (album.get("tracks") or {}).get("items") or []
        album_id = album.get("id")
        if (album.get("media_count") or 1) <= 1 or not total or not album_id:
            return track_metadata
        if len(itens) == total:
            return track_metadata

        cache = self.__dict__.setdefault("_cache_faixas_album", {})
        travas = self.__dict__.setdefault("_travas_faixas_album", {})
        chave = str(album_id)
        trava = travas.setdefault(chave, asyncio.Lock())
        async with trava:
            if chave not in cache:
                try:
                    meta = await self.client.get_album_meta(album_id)
                    cache[chave] = (meta.get("tracks") or {}).get("items") or []
                except Exception:
                    # Sem a lista, a faixa mantém a numeração por disco.
                    logger.debug(
                        "Falha ao buscar a lista de faixas do álbum %s",
                        album_id,
                        exc_info=True,
                    )
                    cache[chave] = []
        itens_completos = cache[chave]
        if len(itens_completos) != total:
            return track_metadata
        novo_album = {**album, "tracks": {"items": itens_completos}}
        return {**track_metadata, "album": novo_album}

    async def _download_and_tag(self, *args, **kwargs) -> bool:
        """Apply tags and present a final status only after all processing."""
        claimed_paths: list[str] = []
        track_meta = args[3] if len(args) > 3 else kwargs.get("track_metadata", {})
        album_meta = (
            args[4] if len(args) > 4 else kwargs.get("album_or_track_metadata", {})
        )
        display_index = kwargs.get(
            "display_index", args[13] if len(args) > 13 else None
        )
        parallel = kwargs.get("is_parallel", args[8] if len(args) > 8 else False)
        token = ui.set_track_index(
            ui.index_from_meta(track_meta, album_meta, display_index)
        )
        view_token = ui.begin_track_view(parallel)
        try:
            call_kwargs = dict(kwargs)
            call_kwargs.pop("_claimed", None)
            call_kwargs["_claimed"] = claimed_paths
            success = await self._download_and_tag_impl(*args, **call_kwargs)
            ui.finish_track_view(_get_title(track_meta), success is True)
            return success
        finally:
            try:
                release_claims(claimed_paths)
            finally:
                ui.end_track_view(view_token)
                ui.reset_track_index(token)

    async def _download_and_tag_impl(
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
        cover_source=None,
        display_index: Optional[int] = None,
        _claimed: Optional[list] = None,
    ) -> bool:
        """Download audio file and apply ID3/FLAC tags."""
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

        # Nomes candidatos (do mais simples ao mais específico) para a pasta
        # única de playlist. Preenchido só nesse modo. Veja `track_identity`.
        candidate_stems = None
        track_id_str = str(track_metadata.get("id") or "").strip()
        # Prefixo "[NN] " (posição na playlist) nos avisos de pulo.
        _skip_pfx = f"[{int(display_index):02d}] " if display_index else ""

        if getattr(self, "is_playlist", False) and not getattr(
            self, "playlist_as_albums", False
        ):
            clean_playlist_format = "{artist} - {track_title}"

            def _safe_name(text):
                """Torna um trecho seguro para uso em nome de arquivo."""
                return sanitize_filename(
                    clean_filename(text, legacy_charmap=legacy_flag),
                    replacement_text="_",
                )

            playlist_base_name = clean_playlist_format.format(**filename_attr)
            formatted_path = _safe_name(playlist_base_name)
            if track_id_str:
                # O nome só pelo "artista - título" não identifica uma faixa:
                # o mesmo par pode existir em álbuns diferentes. Os
                # candidatos adicionam álbum e, por fim, o ID da faixa.
                candidate_stems = build_name_stems(
                    playlist_base_name,
                    filename_attr.get("album_title"),
                    track_id_str,
                    sanitize=_safe_name,
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

        if candidate_stems:
            # Escolha ATÔMICA do nome (vale também para o modo paralelo, em
            # que o arquivo final só aparece em disco no fim do download):
            #  - existente com o ID desta faixa  -> segue o fluxo "já existe";
            #  - reservado por outra tarefa p/ ESTA mesma faixa -> duplicada
            #    dentro da playlist, nada a baixar;
            #  - senão, primeiro nome livre, reservado até o fim da tarefa.
            candidate_files = [
                os.path.join(root_dir, stem) + extension for stem in candidate_stems
            ]
            final_file, name_state = await loop.run_in_executor(
                None,
                lambda: resolve_output_path(candidate_files, track_id_str, _claimed),
            )
            if name_state == STATE_IN_PROGRESS:
                ui.skip(
                    f"{_skip_pfx}Pulando: {os.path.basename(final_file)} "
                    "(mesma faixa repetida na playlist)"
                )
                self.last_downloaded_file = final_file
                return True
            if name_state == STATE_NEW and final_file != candidate_files[0]:
                ui.step(
                    "Outra faixa já usa esse nome; salvando como "
                    f"{os.path.basename(final_file)}"
                )

        if os.path.exists(final_file):
            if getattr(self.settings, "verify_after_download", False):
                ok, message = await loop.run_in_executor(
                    heavy_executor(getattr(self.settings, "max_workers", 1)),
                    lambda: verify_audio_integrity(final_file),
                )
                if ok is False:
                    try:
                        os.replace(final_file, f"{final_file}.corrupt")
                    except OSError as e:
                        logger.error(
                            "Arquivo existente falhou na integridade e não pôde ser "
                            "isolado (%s): %s",
                            final_file,
                            e,
                        )
                        return False
                    ui.warn(
                        f"Arquivo existente inválido isolado ({message}); "
                        "baixando novamente."
                    )
                else:
                    if ok is None:
                        ui.warn(
                            "Integridade não verificada (ffmpeg indisponível); "
                            "mantendo o arquivo existente."
                        )
                    ui.skip(
                        f"{_skip_pfx}Pulando: {os.path.basename(final_file)} (Ja existe)"
                    )
                    self.last_downloaded_file = final_file
                    return True
            else:
                ui.skip(
                    f"{_skip_pfx}Pulando: {os.path.basename(final_file)} (Ja existe)"
                )
                self.last_downloaded_file = final_file
                return True

        if abort_event.is_set():
            return False

        await asyncio.sleep(1)
        try:
            track_url_dict["url"]
        except KeyError:
            ui.skip("Faixa nao disponivel para download")
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
        # Em playlist, o índice exibido é a posição na playlist (display_index);
        # as tags continuam usando o track_number do álbum em track_metadata.
        if display_index:
            track_no = f"{int(display_index):02d}"
        else:
            track_no = format_track_label(track_metadata, total_discs)
        desc = f"[{track_no}] {track_title}"

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

        # MusicBrainz (opcional) roda junto com o download, sem atrasá-lo. A
        # tarefa nunca levanta exceção; se a faixa for descartada antes da
        # etiquetagem ela simplesmente termina sozinha.
        mb_task = None
        if getattr(self.settings, "musicbrainz", False):
            _album_mb = (
                track_metadata.get("album") or {}
                if is_track
                else album_or_track_metadata
            )
            mb_task = asyncio.create_task(_musicbrainz_ids(track_metadata, _album_mb))

        for attempt_fmt in qualities_to_try:
            if abort_event.is_set():
                return False

            if attempt_fmt != int(self.quality):
                ui.warn(
                    "Downgrade automatico: tentando salvar em "
                    f"{TIER_NAMES[attempt_fmt]}..."
                )

            async def get_fresh_url(fmt=attempt_fmt, force_segments=False):
                """Obtain a fresh download URL from Qobuz API."""
                return await self.client.get_track_url(
                    track_metadata["id"], fmt_id=fmt, force_segments=force_segments
                )

            try:
                fresh_track_dict = await get_fresh_url(force_segments=False)

                if fresh_track_dict.get("sample") is True:
                    ui.skip(
                        f"Faixa [{track_no}] - {track_title} (URL retornada e apenas amostra)"
                    )
                    create_missing_placeholder(
                        track_metadata,
                        root_dir,
                        "URL retornada é apenas amostra",
                        total_discs,
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
                        ui.warn(f"Faixa indisponivel, pulando: {e}")
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
                        ui.warn(
                            f"Bloqueio Akamai detectado (ou falha de rede: {e}). "
                            "Ativando download segmentado de fallback..."
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
                                ui.error(
                                    "Download segmentado falhou no tier "
                                    f"{TIER_NAMES.get(attempt_fmt, attempt_fmt)}: {seg_e}"
                                )
                                continue
                        else:
                            ui.error(
                                "Nenhum formato valido retornado pelo servidor "
                                f"para o tier {TIER_NAMES.get(attempt_fmt, attempt_fmt)}."
                            )
                            continue

            except _PermanentDownloadError as e:
                if abort_event.is_set():
                    return False
                ui.warn(f"Faixa indisponível, pulando: {e}")
                return False
            except Exception as e:
                # [FIX] Agora reporta o motivo em vez de "pass" silencioso, para
                # que falhas ao obter a URL fresca (get_fresh_url) do tier atual
                # fiquem visiveis antes de tentar o proximo tier.
                logger.debug(f"Falha ao processar tier {attempt_fmt}: {e}")
                ui.warn(
                    f"Falha no tier {TIER_NAMES.get(attempt_fmt, attempt_fmt)}: {e}"
                )
                continue

        if not success and not abort_event.is_set():
            ui.error(
                f"FAIXA [{track_no}] DESCARTADA DEFINITIVAMENTE APOS TODOS OS DOWNGRADES."
            )
            ui.skip("Pulando para a proxima faixa...")
            return False

        if abort_event.is_set():
            return False

        is_mp3 = True if final_fmt == 5 else False
        extension = ".mp3" if is_mp3 else ".flac"

        tag_function = metadata.tag_mp3 if is_mp3 else metadata.tag_flac
        # Faixa avulsa/playlist: busca a lista de faixas do álbum para a
        # numeração contínua (1..N) das tags em álbuns com vários discos.
        tag_track_metadata = track_metadata
        tag_album_metadata = album_or_track_metadata
        if is_track:
            tag_track_metadata = await self._com_lista_completa_de_faixas(
                track_metadata
            )
        else:
            # Álbum com lista de faixas incompleta: mesma busca, sobre o álbum.
            tag_album_metadata = (
                await self._com_lista_completa_de_faixas(
                    {"album": album_or_track_metadata}
                )
            )["album"]
        mb_ids = await mb_task if mb_task is not None else None
        # So' passa o argumento quando há IDs confirmados: sem --musicbrainz a
        # chamada continua idêntica à de antes.
        _mb_kwargs = {"musicbrainz_ids": mb_ids} if mb_ids else {}
        try:
            await loop.run_in_executor(
                heavy_executor(getattr(self.settings, "max_workers", 1)),
                lambda: tag_function(
                    filename,
                    root_dir,
                    final_file,
                    tag_track_metadata,
                    tag_album_metadata,
                    is_track,
                    self.embed_art,
                    settings=self.settings,
                    embed_cover_path=embed_cover_path,
                    cover_source=cover_source,
                    **_mb_kwargs,
                ),
            )
        except Exception as e:
            ui.error(f"Erro ao aplicar tags: {e}")
            logger.debug("Falha ao aplicar tags", exc_info=True)
            return False

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
                            f"já em {translation_lang.upper()}, sem tradução"
                        )
                    else:
                        translation_note = (
                            f"sem tradução em {translation_lang.upper()} no Qobuz"
                        )

            def _inject_lyrics_and_print():
                """Inject lyrics into audio file and print status."""
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
                        isrc=track_metadata.get("isrc"),
                        duration=track_metadata.get("duration"),
                    )
                    if translation_note:
                        # Dentro do print_lock (não reentrante): formata com o
                        # ui e escreve por tqdm.write, como o motor de letras.
                        for linha in ui.tagged_lines(
                            "lyrics_note",
                            ui.tail(
                                f" {ui.tree_last()} Letras: {desc}", translation_note
                            ),
                        ):
                            tqdm.write(linha)
                    return resultado_letras

            resultado_letras = await ui.run_in_executor(
                loop,
                heavy_executor(getattr(self.settings, "max_workers", 1)),
                _inject_lyrics_and_print,
            )
            if letras_out is not None:
                letras_out.update(
                    _build_letras_report(
                        resultado_letras, translation_lang, qobuz_translation_response
                    )
                )

        if (
            getattr(self.settings, "verify_after_download", False)
            and not abort_event.is_set()
        ):

            def _run_verify():
                """Run post-download audio integrity verification."""
                return verify_audio_integrity(final_file)

            ok, verify_message = await loop.run_in_executor(
                heavy_executor(getattr(self.settings, "max_workers", 1)), _run_verify
            )
            if ok is False:
                with print_lock:
                    tqdm.write(
                        f"{RED}[!] Verificação de integridade falhou para "
                        f"{os.path.basename(final_file)}: {verify_message}{OFF}"
                    )
                logger.debug(f"Falha de integridade em {final_file}: {verify_message}")
                corrupt_path = f"{final_file}.corrupt"
                remaining_path = corrupt_path
                try:
                    os.replace(final_file, corrupt_path)
                    ui.warn(
                        f"Arquivo inválido preservado para diagnóstico como "
                        f"{os.path.basename(corrupt_path)}"
                    )
                except OSError as e:
                    logger.debug(
                        "Não foi possível isolar o arquivo corrompido %s: %s. "
                        "Tentando removê-lo.",
                        final_file,
                        e,
                    )
                    try:
                        os.remove(final_file)
                        remaining_path = ""
                    except OSError as remove_error:
                        remaining_path = final_file
                        logger.error(
                            "Arquivo inválido ainda permanece em %s: %s",
                            final_file,
                            remove_error,
                        )
                emit_progress_json(
                    self.settings,
                    "track_failed",
                    track_id=self.item_id,
                    path=remaining_path,
                    reason=verify_message,
                )
                return False
            if ok is None:
                ui.warn(f"Integridade do áudio não verificada: {verify_message}")

        emit_progress_json(
            self.settings,
            "track_done",
            track_id=self.item_id,
            path=final_file,
        )

        delay_time = getattr(self.settings, "delay", 0)
        if delay_time == 0 and "--delay" in sys.argv:
            try:
                delay_time = int(sys.argv[sys.argv.index("--delay") + 1])
            except (ValueError, IndexError) as e:
                logger.debug(f"--delay com valor invalido, ignorando: {e}")

        if delay_time > 0 and not abort_event.is_set():
            ui.step(f"Aguardando {delay_time} segundos para evitar rate limiting...")
            await asyncio.sleep(delay_time)

        self.last_downloaded_file = final_file
        return True

    @staticmethod
    def _get_filename_attr(track_artist, track_metadata: dict, album_metadata: dict):
        """Get a filename attribute with fallback chain."""

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
            "track_number": f"{track_metadata.get('track_number', 0):02}",
            "isrc": track_metadata.get("isrc"),
            "bit_depth": track_metadata.get("maximum_bit_depth"),
            "sampling_rate": track_metadata.get("maximum_sampling_rate"),
            "track_title": _get_title(track_metadata),
            "track_title_base": track_metadata.get("title"),
            "version": track_metadata.get("version"),
            "year": track_metadata.get("release_date_original", "").split("-")[0],
            "disc_number": f"{track_metadata.get('media_number'):02}",
            "release_date": track_metadata.get("release_date_original"),
            "ExplicitFlag": "🅴" if track_metadata.get("parental_warning") else "",
            "explicit": "🅴" if track_metadata.get("parental_warning") else "",
        }

    @staticmethod
    def _get_track_attr(meta, track_title, bit_depth, sampling_rate, file_format):
        """Get a track attribute with fallback chain."""
        album_meta = meta.get("album", {})

        album_artist_raw = get_album_artist(album_meta)
        album_artist_str = (
            _flatten_artists(album_artist_raw)
            if album_artist_raw
            else _safe_get(meta, "performer", "name")
        )

        # Classificação unificada (mesma usada no nome da pasta e na busca/
        # TUI -- ver classify_release_type em utils.py), calculada uma vez
        # e reaproveitada tanto em "release_type" quanto em "media_type".
        #
        # BUGFIX: "media_type" usava meta.get("product_type", "").capitalize()
        # -- o valor CRU da API, sem passar pela correção. Isso podia fazer
        # o mesmo relatório mostrar "media_type": "Single" e "release_type":
        # "Ep" pro MESMO álbum (ex.: um lançamento de 5 faixas que a
        # gravadora rotulou errado como "Single" na API), porque só um dos
        # dois campos usava a heurística de correção. Agora os dois vêm do
        # mesmo cálculo. Ver tests/regression/test_release_type.py.
        _tipo_lancamento = format_release_type(
            album_meta.get("release_type"),
            track_count=meta.get("track_count", ""),
            title=_get_title(album_meta),
            version=album_meta.get("version"),
            duration_seconds=album_meta.get("duration"),
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
            "media_type": _tipo_lancamento,
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
            "ExplicitFlag": "🅴" if album_meta.get("parental_warning") else "",
            "explicit": "🅴" if album_meta.get("parental_warning") else "",
            "release_type": _tipo_lancamento,
        }

    @staticmethod
    def _get_album_attr(meta, album_title, file_format, bit_depth, sampling_rate):
        """Get an album attribute with fallback chain."""

        album_artist_raw = get_album_artist(meta)
        album_artist_str = _flatten_artists(album_artist_raw)

        # Mesma correção do bloco de _get_track_attr acima: "media_type" e
        # "release_type" vêm do MESMO cálculo classificado, em vez de
        # "media_type" usar o product_type cru da API sem correção.
        _tipo_lancamento = format_release_type(
            meta.get("release_type"),
            track_count=meta.get("track_count"),
            title=album_title,
            version=meta.get("version"),
            duration_seconds=meta.get("duration"),
        )

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
            "media_type": _tipo_lancamento,
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
            "ExplicitFlag": "🅴" if meta.get("parental_warning") else "",
            "explicit": "🅴" if meta.get("parental_warning") else "",
            "release_type": _tipo_lancamento,
        }

    async def _get_format(self, item_dict, is_track_id=False, track_url_dict=None):
        """Get format string (FLAC/MP3) from quality and stream info."""
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
        """Determine folder and track filename formats."""
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

        legacy_flag = getattr(settings, "legacy_charmap", False)

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
                self.settings.multiple_disc_track_format = multi_disc_fmt
                return

        self.folder_format = DEFAULT_FOLDER
        self.track_format = DEFAULT_TRACK

    def _generate_tracklist(
        self, meta, dirn, album_title, file_format, bit_depth, sampling_rate
    ):
        """Generate a text file listing all tracks in the release."""
        if self.no_credits or abort_event.is_set():
            return

        safe_title = sanitize_filename(album_title)
        tracklist_path = os.path.join(dirn, f"{safe_title} - Tracklist.txt")

        if os.path.isfile(tracklist_path):
            return

        ui.step("Gerando Digital Booklet...")

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
                    meta.get("media_count") or 1,
                    max((track.get("media_number", 1) for track in tracks), default=1),
                )
                current_disc = None

                for track in tracks:
                    disc_num = track.get("media_number", 1)
                    if total_discs > 1 and disc_num != current_disc:
                        if current_disc is not None:
                            f.write("\n")
                        f.write(f"--- DISC {disc_num} ---\n\n")
                        current_disc = disc_num

                    t_num = format_track_label(track, total_discs)
                    t_title_base = track.get("title", "Unknown Title")
                    explicit_flag = " [E]" if track.get("parental_warning") else ""
                    t_title = f"{t_title_base}{explicit_flag}"

                    duration = int(track.get("duration", 0))
                    mins, secs = divmod(duration, 60)
                    dur_str = f"[{mins:02}:{secs:02}]"

                    f.write(f"{f'[{t_num}] {t_title}':<60} {dur_str}\n")

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

            ui.ok(
                f" {ui.tree_last()} Concluído: Digital Booklet.txt (Credits & Review)"
            )
        except Exception as e:
            ui.error(f"Erro criando booklet: {e}")

    async def _fetch_qobuz_lyrics_json(self, track_id, language=None):
        """Fetch lyrics JSON from Qobuz API."""
        return await fetch_qobuz_lyrics_json(
            self.client, self.http_session, track_id, language
        )

    def _append_lyrics_to_booklet(self, dirn, album_title):
        """Append lyrics to digital booklet text file."""
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
                ui.ok("Letras formatadas e anexadas ao Digital Booklet.")
            except Exception as e:
                # Anexar letras ao booklet e' um extra; o booklet em si ja'
                # foi gravado antes disto rodar, entao uma falha aqui nao
                # deve ser tratada como falha do download.
                logger.debug(f"Falha ao anexar letras ao Digital Booklet: {e}")


def _get_description(item: dict, track_title, multiple=None):
    """Get album description from metadata."""
    downloading_title = (
        f"{track_title} [{item.get('bit_depth', '')}/{item.get('sampling_rate', '')}]"
    )
    if multiple:
        downloading_title = f"[CD {multiple}] {downloading_title}"
    return downloading_title


async def _tqdm_download_atomic(
    url_or_callable,
    fname,
    track_name,
    *,
    is_parallel=False,
    session=None,
    position_pool=None,
):
    """Baixa áudio em ``.part`` e promove atomicamente após validar o corpo."""
    if abort_event.is_set():
        return
    started = time.monotonic()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
        "Connection": "keep-alive",
    }
    owns_session = session is None
    timeout_cfg = httpx.Timeout(60.0, connect=10.0)
    http = session or make_client(follow_redirects=True, timeout=timeout_cfg)
    bar = None

    if not is_parallel:
        ui.progress(ui.tail(f"Em Progresso: {track_name}", _progress_tail(track_name)))
        bar = tqdm(
            total=0,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
            desc=f"  {RESET}⬇️{RESET}",
            bar_format=(
                "{desc}: {percentage:3.0f}%|{bar}| "
                "{n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
            ),
            dynamic_ncols=True,
            leave=False,
        )
    elif position_pool:
        ui.progress(ui.tail(f"Em Progresso: {track_name}", _progress_tail(track_name)))

    def on_progress(downloaded: int, total: int | None) -> None:
        if bar is None:
            return
        if total is not None and bar.total != total:
            bar.total = total
        delta = downloaded - bar.n
        if delta > 0:
            bar.update(delta)

    try:
        result = await download_file(
            http,
            url_or_callable,
            fname,
            headers=headers,
            timeout=timeout_cfg,
            max_attempts=5,
            backoff_base=2.0,
            backoff_max=32.0,
            chunk_size=1024 * 1024,
            on_progress=on_progress,
            should_abort=abort_event.is_set,
        )
        if bar is not None:
            bar.close()
            bar = None
        ui.done(
            ui.tail(
                f" Concluído: {track_name}",
                ui.stats_text(result.size, time.monotonic() - started),
            )
        )
    except HttpPermanentDownloadError as exc:
        ui.warn(f"Indisponível: {track_name} ({exc})")
        raise _PermanentDownloadError(str(exc)) from exc
    except DownloadAborted:
        abort_event.set()
    except (KeyboardInterrupt, SystemExit):
        abort_event.set()
    except DownloadError as exc:
        raise RuntimeError(f"Falha definitiva após 5 tentativas: {exc}") from exc
    finally:
        if bar is not None:
            bar.close()
        if owns_session:
            try:
                await http.aclose()
            except Exception as exc:
                logger.debug("Falha ao fechar client HTTP: %s", exc)


async def tqdm_download(
    url_or_callable,
    fname,
    track_name,
    is_parallel=False,
    session=None,
    position_pool=None,
):
    """Download file with tqdm progress bar and atomic finalization."""
    return await _tqdm_download_atomic(
        url_or_callable,
        fname,
        track_name,
        is_parallel=is_parallel,
        session=session,
        position_pool=position_pool,
    )


async def fetch_qobuz_lyrics_json(client, http_session, track_id, language=None):
    """Fetch lyrics JSON from Qobuz API."""
    try:
        params = {"track_id": track_id}
        if language:
            params["language"] = language
        params["request_ts"] = int(time.time())
        params["request_sig"] = client._modern_sig(
            "track/lyricsUrl", params, client.sec
        )

        r = await request_async(
            client.session, "GET", client.base + "track/lyricsUrl", params=params
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

        resp = await request_async(http_session, "GET", lyrics_json_url, timeout=12)
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


def _get_title(item_dict):
    """Get base track title without version."""
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
    """Get extra/description from goodies metadata."""
    if abort_event.is_set():
        return
    extra_file = os.path.join(dirn, extra)
    if os.path.isfile(extra_file):
        ui.skip(f"Pulando {label}: {extra} (Já baixado)")
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
        ui.skip(f"Pulando {label} '{extra}': URL inacessível ({e})")


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
    http = session or make_client(follow_redirects=True)
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


def _apple_release_kind(album):
    """Atalho para utils.qobuz_release_kind (mesma regra do comando `tags`)."""
    return qobuz_release_kind(album)


async def _try_apple_cover_bytes(
    session,
    artist=None,
    album=None,
    upc=None,
    isrc=None,
    track_title=None,
    release_kind=None,
    album_tracks=None,
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
        resultado = await get_apple_hq_cover(
            session=session,
            upc=upc,
            isrc=isrc,
            artist=artist,
            album=album,
            track_title=track_title,
            release_kind=release_kind,
            # Mesmos critérios do comando `tags`: edição estrita (sem presumir
            # Deluxe/padrão) e, no álbum, confirmação pela lista de faixas.
            strict_edition=True,
            album_tracks=album_tracks,
            deep_album_search=bool(album_tracks),
            fallback_countries=bool(album_tracks),
        )
    except Exception as e:
        logger.debug(f"Busca de capa na Apple falhou, indo pra Qobuz: {e}")
        return None

    # get_apple_hq_cover devolve (url, fonte, motivo); tratar o retorno como
    # texto fazia re.sub() levantar TypeError e derrubava a etapa de capa.
    resultado_compat: object = resultado
    if isinstance(resultado_compat, tuple):
        apple_url = resultado_compat[0] if resultado_compat else None
        motivo = resultado_compat[2] if len(resultado_compat) > 2 else None
    else:
        apple_url, motivo = resultado_compat, None

    if not isinstance(apple_url, str) or not apple_url.strip():
        if motivo:
            logger.debug(f"Capa da Apple nao usada, indo pra Qobuz: {motivo}")
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
    """Fetch cover art bytes from Qobuz CDN."""
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
    release_kind=None,
    notas=None,
    album_tracks=None,
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

    Devolve a ORIGEM da imagem usada ("Apple/iTunes" ou "Qobuz") para
    ser gravada nas tags (`Capa: ...`). Quando as capas ja' existiam em
    disco, devolve a origem lembrada desta execucao; se nao ha' como
    saber (arquivo de uma execucao anterior) ou nada foi gravado,
    devolve None -- e nenhuma linha "Capa: ..." e' escrita, em vez de
    afirmar uma origem que pode estar errada.

    ``notas`` (lista) liga o modo playlist/lote: em vez de escrever linhas de
    capa, a função acrescenta notas curtas à lista (``"capa Apple (HQ)"``,
    ``"capa já baixada"``, ``"sem capa"``) e o chamador as põe na linha
    "Em Progresso" da faixa. Falhas ao gravar continuam saindo como aviso.
    """
    if abort_event.is_set():
        return None

    if not save_cover and not embed_art:
        return None

    saved_file = os.path.join(dirn, saved_name)
    embed_file = os.path.join(dirn, embed_name) if embed_name else None
    chave_origem = os.path.abspath(embed_file or saved_file)

    precisa_salva = save_cover and not os.path.isfile(saved_file)
    precisa_embed = embed_art and embed_file and not os.path.isfile(embed_file)

    if save_cover and not precisa_salva:
        if notas is not None:
            notas.append("capa já baixada")
        else:
            ui.skip(f"Pulando cover art: {saved_name} (Já baixado)")
    if embed_art and embed_file and not precisa_embed:
        if notas is not None:
            notas.append("capa já baixada")
        else:
            ui.skip(f"Ignorando arte da capa incorporada: {embed_name} (Já baixado)")

    if not precisa_salva and not precisa_embed:
        return _COVER_SOURCES.get(chave_origem)

    async def _gravar(caminho, dados, rotulo, rotulo_origem):
        """Write cover image bytes to disk."""
        try:
            async with aiofiles.open(caminho, "wb") as f:
                await f.write(dados)
            if notas is not None:
                notas.append(f"capa {rotulo_origem}")
            else:
                ui.ok(
                    ui.tail(
                        rotulo,
                        ui.join_meta(os.path.basename(caminho), rotulo_origem),
                    )
                )
            return True
        except OSError as e:
            ui.warn(f"Falha ao salvar {os.path.basename(caminho)}: {e}")
            return False

    # 1) Tenta a Apple uma unica vez -- se achar, a mesma imagem serve
    # tanto a versao salva quanto a de embed (nao ha' distincao de
    # "saved_art_size" vs "embedded_art_size" pra' capa da Apple).
    apple_bytes = await _try_apple_cover_bytes(
        session,
        artist=artist,
        album=album,
        upc=upc,
        isrc=isrc,
        track_title=track_title,
        release_kind=release_kind,
        album_tracks=album_tracks,
    )
    if apple_bytes:
        if precisa_salva:
            await _gravar(saved_file, apple_bytes, "Capa salva", "Apple (HQ)")
        if precisa_embed:
            await _gravar(embed_file, apple_bytes, "Capa de embed", "Apple (HQ)")
        _COVER_SOURCES[chave_origem] = COVER_SOURCE_APPLE_LABEL
        return COVER_SOURCE_APPLE_LABEL

    # 2) Fallback: Qobuz, respeitando saved_art_size/embedded_art_size separadamente (igual ao comportamento original), reaproveitando o arquivo salvo pro embed quando os dois tamanhos resolvem pra' mesma URL -- evita baixar a mesma imagem duas vezes no caso mais comum.
    saved_url = _resolve_art_url(item, saved_art_size) if precisa_salva else None
    embed_url = _resolve_art_url(item, embedded_art_size) if precisa_embed else None

    gravou_qobuz = False

    def _origem_qobuz():
        # So' afirma "Qobuz" se alguma imagem da Qobuz foi de fato gravada.
        if not gravou_qobuz:
            return _COVER_SOURCES.get(chave_origem)
        _COVER_SOURCES[chave_origem] = COVER_SOURCE_QOBUZ_LABEL
        return COVER_SOURCE_QOBUZ_LABEL

    if precisa_salva:
        dados = await _fetch_qobuz_cover_bytes(item, saved_art_size, session)
        if dados:
            await _gravar(saved_file, dados, "Capa salva", "Qobuz")
            gravou_qobuz = True
        else:
            if notas is not None:
                notas.append("sem capa")
            else:
                ui.skip("Pulando capa: nenhuma fonte disponível")

    if not precisa_embed:
        return _origem_qobuz()

    if (
        precisa_salva
        and saved_url == embed_url
        and os.path.isfile(saved_file)
        and embed_file is not None
    ):
        try:
            await asyncio.to_thread(lambda: shutil.copyfile(saved_file, embed_file))
            if notas is None:  # em playlist/lote a nota da capa salva já cobre
                ui.ok(ui.tail("Capa de embed", f"reaproveitada de {saved_name}"))
            gravou_qobuz = True
            return _origem_qobuz()
        except OSError as e:
            logger.debug(f"Falha ao copiar cover.jpg pra embed: {e}")

    dados = await _fetch_qobuz_cover_bytes(item, embedded_art_size, session)
    if dados:
        await _gravar(embed_file, dados, "Capa de embed", "Qobuz")
        gravou_qobuz = True
    else:
        if notas is not None:
            notas.append("sem capa")
        else:
            ui.skip("Pulando arte incorporada: nenhuma fonte disponível")
    return _origem_qobuz()


async def tqdm_download_segments(
    track_url_dict,
    fname,
    track_name,
    is_parallel=False,
    session=None,
    segment_workers=None,
    position_pool=None,
):
    """Download file using segmented approach with progress bar."""
    if abort_event.is_set():
        return
    R = RESET
    _t0 = time.monotonic()

    tmp_fname = fname + ".mp4"
    remux_tmp_fname = fname + ".part"
    n_segments = track_url_dict["n_segments"]
    url_template = track_url_dict["url_template"]
    raw_key = track_url_dict["raw_key"]

    workers = segment_workers if segment_workers else 4

    owns_session = session is None
    timeout_cfg = httpx.Timeout(60.0, connect=10.0)
    http = session or make_client(follow_redirects=True, timeout=timeout_cfg)

    async def get_seg_size(seg_num):
        """Get optimal segment size for segmented downloads."""
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

    # Consulta tamanhos em janelas limitadas. Criar uma task por segmento de
    # uma faixa longa causava uma rajada de conexões antes mesmo do download.
    sizes = []
    for inicio in range(0, n_segments + 1, workers):
        lote = range(inicio, min(inicio + workers, n_segments + 1))
        sizes.extend(await asyncio.gather(*(get_seg_size(i) for i in lote)))
    total_size = sum(sizes)

    position = position_pool.acquire() if (is_parallel and position_pool) else 0

    if is_parallel:
        size_mb = total_size / (1024 * 1024) if total_size else 0
        ui.progress(
            ui.tail(f"Em Progresso: {track_name}", _progress_tail(track_name, size_mb))
        )
        desc_len = position_pool.desc_len if position_pool else 14
        short_name = _short_track_name(track_name, desc_len)
        tqdm_desc = f" {short_name}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt}"
        ncols = position_pool.ncols if position_pool else _get_safe_ncols()
        dynamic_ncols = False
    else:
        ui.progress(ui.tail(f"Em Progresso: {track_name}", _progress_tail(track_name)))
        tqdm_desc = f"  {R}↪️{R}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ncols = None
        dynamic_ncols = True

    async def fetch_segment_fluid(seg_num):
        """Fetch a single segment with retries and error handling."""
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
                    if seg_data:
                        bar.update(-len(seg_data))
                    _n = attempt.retry_state.attempt_number
                    ui.warn(
                        f"Reconectando segmento {seg_num}. "
                        f"Tentativa {_n}/5 para {track_name}"
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

                    decrypted_data = await asyncio.to_thread(
                        _decrypt_qobuz_segment,
                        seg_data,
                        raw_key,
                        segment_uuid,
                    )
                    await file.write(decrypted_data)

                if n_segments >= 2:
                    # Mantém no máximo ``workers`` segmentos em memória e
                    # grava cada janela em ordem. O gather anterior acumulava
                    # a faixa inteira antes de escrever o primeiro byte.
                    for inicio in range(2, n_segments + 1, workers):
                        lote = range(inicio, min(inicio + workers, n_segments + 1))
                        results = await asyncio.gather(
                            *(fetch_segment_fluid(i) for i in lote)
                        )

                        for seg_data in results:
                            if not abort_event.is_set():
                                decrypted_data = await asyncio.to_thread(
                                    _decrypt_qobuz_segment,
                                    seg_data,
                                    raw_key,
                                    segment_uuid,
                                )
                                await file.write(decrypted_data)

        if abort_event.is_set():
            return
        if not is_parallel:
            ui.step(f" {ui.tree_last()} Montando o arquivo FLAC final...")

        try:
            os.remove(remux_tmp_fname)
        except FileNotFoundError:
            # Primeira tentativa ou temporário já limpo: nada a remover.
            pass

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
            remux_tmp_fname,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await remux.communicate()

        if remux.returncode != 0:
            raise ConnectionError(
                f"Falha no remux do FFmpeg para {fname}: {stderr.decode()}"
            )
        if not os.path.isfile(remux_tmp_fname) or os.path.getsize(remux_tmp_fname) == 0:
            raise ConnectionError(f"Remux do FFmpeg não gerou um FLAC válido: {fname}")
        replace_atomic(remux_tmp_fname, fname)

        ui.done(
            ui.tail(
                f"Concluído: {track_name}",
                ui.stats_text(total_size, time.monotonic() - _t0),
            )
        )

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
                # Limpeza best-effort: o arquivo pode já ter sumido.
                pass
        if os.path.isfile(remux_tmp_fname):
            try:
                os.remove(remux_tmp_fname)
            except OSError:
                # Falha de limpeza não deve mascarar o erro principal do download.
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
    """Extract UUID from Qobuz streaming URL."""
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
    """Decrypt a Qobuz audio segment using AES."""
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
    """Download digital booklets and bonus PDFs."""
    if abort_event.is_set():
        return
    try:
        for goody in album_meta.get("goodies", []):
            if abort_event.is_set():
                break
            if not goody.get("url"):
                continue
            goody_name = sanitize_filename(
                clean_filename(f"{album_meta.get('title')} ({goody.get('id')}).pdf")
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


async def _musicbrainz_ids(track_metadata: dict, album_meta: dict):
    """IDs do MusicBrainz só se título, versão, álbum e artista conferirem.

    Nunca levanta exceção: qualquer problema vira None (a faixa é tagueada
    normalmente, só sem os IDs).
    """
    try:
        album_meta = album_meta or {}
        artistas = metadata.track_artist_names(album_meta, track_metadata)
        achados = await musicbrainz.lookup_matching(
            isrc=track_metadata.get("isrc") or "",
            title=track_metadata.get("title") or "",
            version=track_metadata.get("version") or "",
            album=album_meta.get("title") or "",
            album_version=album_meta.get("version") or "",
            artists=artistas,
            upc=album_meta.get("upc") or "",
            duration=track_metadata.get("duration"),
            artist_text=_safe_get(track_metadata, "performer", "name") or "",
        )
        if not achados:
            logger.debug(
                "MusicBrainz: sem correspondência estrita para a faixa %s",
                track_metadata.get("id"),
            )
        return achados
    except Exception:
        logger.debug("MusicBrainz: falha ao buscar IDs", exc_info=True)
        return None


def _clean_embed_art(dirn, settings=None):
    """Clean and optimize embedded artwork for size/quality."""
    embed_file = os.path.join(dirn, EMB_COVER_NAME)
    if os.path.exists(embed_file):
        try:
            time.sleep(0.5)
            os.remove(embed_file)
        except OSError:
            # Capa embutida já removida ou em uso: segue sem remover.
            pass
