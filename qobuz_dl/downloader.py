from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.constants import (
    DEFAULT_FOLDER,
    DEFAULT_TRACK,
    DEFAULT_MULTIPLE_DISC_TRACK,
)
from qobuz_dl.db import handle_download_id
from qobuz_dl.utils import get_album_artist, clean_filename, verify_audio_integrity
from .lyrics_engine import LyricsEngine
import logging
import os
import shutil
import sys
import time
import subprocess
import re
import threading
import signal
import textwrap
from typing import Tuple
import asyncio

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathvalidate import sanitize_filename, sanitize_filepath
from tqdm import tqdm

import qobuz_dl.metadata as metadata
from qobuz_dl.color import OFF, GREEN, RED, WARNING as YELLOW, INFO as CYAN, RESET
from qobuz_dl.exceptions import NonStreamable

# Ordem de qualidades para fallback apenas em falhas de conexão
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
        artist = track.get(
            "performer",
            {}).get(
            "name",
            track.get(
                "artist",
                {}).get(
                "name",
                "Desconhecido"))

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
    Erro de download que NAO deve ser tentado de novo -- 401 (nao
    autenticado), 403 (sem licenca/regiao bloqueada), 404 (nao existe),
    451 (indisponivel por motivo legal). Tentar de novo um desses e'
    tempo perdido, sempre vai falhar do mesmo jeito -- diferente de um
    timeout ou erro 5xx, que pode ser so' uma soneca passageira do
    servidor. Antes disso, qualquer erro nao-404 (incluindo 403/401)
    caia no mesmo loop de retry com backoff de ate' ~62s e imprimia
    "[!] Server block. Retrying..." repetidas vezes -- poluindo o
    terminal e dando a falsa impressao de que a conexao estava
    instavel quando na verdade a faixa so' nao estava disponivel
    mesmo (ex.: bloqueio de regiao, direito autoral).
    """
    pass


# UI Lock to prevent text scrambling during multithreading
print_lock = threading.Lock()

# Global Abort Event for graceful CTRL+C handling and file unlock
abort_event = threading.Event()


def _get_safe_ncols():
    try:
        return max(shutil.get_terminal_size(fallback=(80, 24)).columns - 1, 20)
    except Exception:
        return 40


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
    Thread-safe print function. Prevents UI glitches ("cursor wars")
    when multiple download threads attempt to log to the terminal simultaneously.
    """
    with print_lock:
        text = " ".join(map(str, args))
        end = kwargs.get("end", "\n")
        tqdm.write(text, end=end)


def print_download_header(kind: str, rows: list) -> None:
    """
    Cabecalho padronizado impresso uma unica vez, no inicio de qualquer
    download (album, faixa unica, playlist ou lote de urls). Mesmo formato
    pros 4 tipos -- so' as linhas (rows) mudam -- pra ficar facil escanear
    visualmente o que vai ser baixado antes das linhas de progresso comecarem.

    kind: rotulo do topo ("ALBUM", "FAIXA", "PLAYLIST", "LOTE DE URLS"...) --
          impresso entre colchetes (ex: "[ÁLBUM]"), com uma linha em branco
          logo depois pra separar visualmente do bloco de dados.
    rows: lista de tuplas (label, valor) exibidas alinhadas, ex.:
          [("Artista", "Daft Punk"), ("Faixas", "14")]

    Cor: usa RESET (Style.RESET_ALL) apos cada trecho colorido, nao OFF
    (Style.DIM) -- DIM so' reduz o brilho, nao limpa a cor de fato, entao o
    ciano "vazava" pro resto da linha (inclusive os valores, que nunca
    tinham cor propria). RESET limpa de verdade, os valores voltam a usar a
    cor padrao do terminal.

    Barra: largura calculada com base no conteudo real (maior linha entre o
    "[kind]" e as rows), com teto de 44 e piso de 20 -- antes era sempre 44
    fixo, estourando a tela em telas estreitas (ex: A-Shell no iPhone) mesmo
    quando o conteudo era bem mais curto que isso.
    """
    label_width = max((len(label) for label, _ in rows), default=8)
    BOLD = "\033[1m"  # Código ANSI para negrito

    header_line = f" [{kind}]"
    row_lines = [f" {label.upper():<{label_width}}  {value}" for label, value in rows]
    content_width = max([len(header_line)] + [len(l) for l in row_lines], default=20)
    bar_width = max(20, min(content_width, 44))
    bar = "━" * bar_width

    # [FAIXA] em negrito com a cor padrão (branco/preto). Linhas divisórias em Ciano.
    lines = [
        f"\n{CYAN}{bar}{RESET}",
        f"{BOLD} [{kind}]{RESET}",
        ""
    ]

    # Rótulo em Ciano, Valor na cor padrão (branco/preto)
    for label, value in rows:
        lines.append(f" {CYAN}{label.upper():<{label_width}}{RESET}  {value}")

    # Linha divisória final com a quebra de linha extra (\n) para espaçamento
    lines.append(f"{CYAN}{bar}{RESET}\n")
    safe_print("\n".join(lines))


def emit_progress_json(settings, event, **fields):
    """
    Emite uma linha JSON em stdout descrevendo um evento de progresso
    (inicio/fim de faixa), pra frontends (GUI web, app) conseguirem
    acompanhar downloads sem precisar fazer parsing de barra tqdm/ANSI no
    terminal. So' emite algo se settings.progress_json estiver ligado
    (--progress-json / progress_json=true no config.ini); sem isso, e' um
    no-op e o comportamento de sempre (so' tqdm + prints coloridos)
    continua identico.

    Cobre hoje os eventos "track_start" e "track_done" (ver chamadas em
    download_track() e _download_and_tag()). Falha de faixa (exceptions
    tratadas mais acima, em core.py) ainda NAO emite um evento
    "track_failed" -- e' o proximo passo natural se isso for util.
    """
    if not getattr(settings, "progress_json", False):
        return
    import json as _json

    payload = {"event": event, "ts": time.time(), **fields}
    with print_lock:
        # Linha unica, sem cor ANSI, pra ficar facil de dar json.loads()
        # por linha do lado de quem esta consumindo isso.
        print(_json.dumps(payload, ensure_ascii=False), flush=True)


# --- FIX ISSUE #216: Normalize Release Type ---
def format_release_type(release_type: str) -> str:
    if not release_type:
        return "Unknown"

    release_type = release_type.lower()
    if release_type == "ep":
        return "EP"

    return release_type.title()


# --------------------------------------------------------
legacy_flag = False


def process_folder_format_with_subdirs(
    folder_format, attr_dict, path=None, legacy_charmap=False
):
    path_parts = folder_format.split("/")
    cleaned_parts = []
    for part in path_parts:
        if part:
            try:
                formatted_part = part.format(**attr_dict)
                cleaned_part = sanitize_filepath(
                    clean_filename(
                        formatted_part,
                        legacy_charmap=(
                            legacy_flag if "legacy_flag" in locals() else legacy_charmap
                        ),
                    ),
                    replacement_text="_",
                )

                if cleaned_part and len(cleaned_part) > 120:
                    start_f = cleaned_part[:60].rstrip(" .\"-_'")
                    end_f = cleaned_part[-50:].lstrip(" .\"-_'")
                    cleaned_part = f"{start_f}...{end_f}"

                if cleaned_part:
                    cleaned_parts.append(cleaned_part)
            except KeyError as e:
                logger.warning(f"{YELLOW}Format error ({e}), using original text.{OFF}")
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

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class Download:
    """
    The main Download engine handling the retrieval of audio files, booklets, and metadata.
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

        # follow_redirects=True porque o requests seguia redirect por padrao
        # e o httpx.Client, ao contrario, NAO segue por padrao -- sem isso
        # aqui, qualquer URL de CDN que responda com redirect quebraria
        # silenciosamente (ficaria parecendo 30x em vez do arquivo real).
        self.http_session = httpx.AsyncClient(
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
                "Connection": "keep-alive",
            },
        )

        self.fetch_lyrics = fetch_lyrics
        self.no_lrc_files = no_lrc_files
        if self.fetch_lyrics:
            self.lyrics_engine = LyricsEngine(genius_token, session=self.http_session)

        self.settings = settings or QobuzDLSettings()
        self.download_db = download_db

        self.is_playlist = is_playlist
        self.playlist_track_number = playlist_track_number
        self.playlist_as_albums = playlist_as_albums

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
                # Antes engolia silencioso. So' cleanup best-effort mesmo
                # (nao interrompe o encerramento por causa disso), mas
                # logar em debug ajuda a perceber se isso comeca a falhar
                # sempre (ex.: engine mal inicializado).
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
            raise NonStreamable("This release is not streamable")

        if self.albums_only and (
            album_meta.get("release_type") != "album" or
            album_meta.get("artist").get("name") == "Various Artists"
        ):
            safe_print(f'{OFF}Ignoring Single/EP/VA: {album_meta.get("title", "n/a")}')
            return

        album_title = _get_title(album_meta)
        url = album_meta.get("url", "")
        release_date = album_meta.get("release_date_original", "")

        format_info = await self._get_format(album_meta)
        file_format, quality_met, bit_depth, sampling_rate = format_info

        if not self.downgrade_quality and not quality_met:
            safe_print(
                f"{OFF}Skipping {album_title} as it doesn't meet quality requirement"
            )
            return

        track_count = len(album_meta.get("tracks", {}).get("items", []))
        artist_name = _safe_get(album_meta, "artist", "name", default="")
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
                    f"{YELLOW}[!] Could not rename existing folder to [IN PROGRESS]. Operating in standard mode. ({e}){OFF}"
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
                # Antes era um "except: pass" cego (pegava ate' SystemExit/
                # KeyboardInterrupt). Restrito ao que pode realmente
                # acontecer aqui: --delay sem numero valido depois, ou sem
                # nada depois dele na linha de comando.
                logger.debug(f"--delay com valor invalido, ignorando: {e}")

        active_workers = int(getattr(self.settings, "max_workers", 1))
        is_parallel = False
        mode_label = "Sequencial"

        if delay_time > 0:
            active_workers = 1
            mode_label = "Sequencial (Safety Delay ativo)"
        elif active_workers > 1 and track_count > 1:
            # So' vale a pena paralelizar quando ha' mais de 1 faixa pra
            # baixar ao mesmo tempo -- com 1 faixa so', paralelo nao ganha
            # nada e so' troca a barra de progresso ao vivo pela linha
            # "silenciosa" do modo multithread.
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
                    ("Qualidade",
                     f"{file_format} ({bit_depth}bit/{float(sampling_rate):g}kHz)" if bit_depth else file_format),
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
            # So' falha em ambientes sem suporte a signal (ex.: threads
            # secundarias no Windows) -- nesse caso Ctrl+C so' nao aborta
            # tao graciosamente, mas nao deveria travar o download.
            logger.debug(f"Nao foi possivel instalar handler de SIGINT: {e}")

        try:
            self._generate_tracklist(
                album_meta, dirn, album_title, file_format, bit_depth, sampling_rate
            )

            loop = asyncio.get_event_loop()

            if self.settings.no_cover:
                safe_print(f"{OFF}[*] Skipping cover{OFF}")

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
                    f"{YELLOW}[*] --booklet-only flag active. Skipping audio tracks.{OFF}"
                )
                if is_standard_album and working_dirn == inprogress_dirn:
                    try:
                        os.rename(working_dirn, incomplete_dirn)
                    except OSError as e:
                        logger.warning(
                            f"{YELLOW}[!] Impossibile rinominare la cartella in [INCOMPLETE]. ({e}){OFF}"
                        )
                return

            semaphore = asyncio.Semaphore(active_workers)

            async def process_track(idx, i):
                if abort_event.is_set():
                    return False
                async with semaphore:
                    t_num = str(i.get("track_number", idx + 1)).zfill(2)
                    t_title = i.get("title", "Faixa Desconhecida")

                    # -------------------------------------------------------------
                    # [OPÇÃO A + B + C]: Pre-check ANTES de fazer chamada na API
                    # -------------------------------------------------------------
                    streamable, reason = is_track_streamable(i)
                    if not streamable:
                        safe_print(
                            f"{CYAN}[PULADA]{RESET} Faixa {t_num} - {t_title} ({YELLOW}{reason}{RESET})")
                        create_missing_placeholder(i, dirn, reason)
                        return "skipped"
                    # -------------------------------------------------------------

                    try:
                        parse = await self.client.get_track_url(
                            i["id"], fmt_id=self.quality
                        )
                    except Exception as e:
                        safe_print(
                            f"{RED}[!] Erro de API na faixa {t_num} (ID: {
                                i['id']}): {e}{OFF}"
                        )
                        create_missing_placeholder(i, dirn, f"Erro de API: {e}")
                        return False

                    if "sample" not in parse and parse.get("sampling_rate"):
                        is_mp3 = True if int(self.quality) == 5 else False
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
                        )
                        return res
                    else:
                        safe_print(
                            f"{CYAN}[PULADA]{RESET} Faixa {t_num} - {t_title} ({YELLOW}Apenas amostra/demo{RESET})")
                        create_missing_placeholder(i, dirn, "Apenas amostra/demo (30s)")
                        return "skipped"

                        # Cria tarefas gerenciáveis no asyncio
            task_objs = [
                asyncio.create_task(process_track(idx, i))
                for idx, i in enumerate(album_meta["tracks"]["items"])
            ]

            try:
                results = await asyncio.gather(*task_objs, return_exceptions=True)
            except (KeyboardInterrupt, asyncio.CancelledError):
                abort_event.set()
                aborted_by_user = True
                # Cancela imediatamente todas as outras faixas que estão na fila
                for t in task_objs:
                    if not t.done():
                        t.cancel()
                try:
                    self.http_session.close()  # Força o fechamento imediato de todos os sockets abertos
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
                f"\n{RED}[!] CTRL+C Intercepted: Securing files and folders...{OFF}"
            )

        finally:
            try:
                if original_sigint:
                    signal.signal(signal.SIGINT, original_sigint)
            except Exception as e:
                logger.debug(
                    f"Nao foi possivel restaurar handler de SIGINT original: {e}"
                )

        if aborted_by_user:
            time.sleep(1.5)

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
                    f"{YELLOW}[!] Could not rename final folder state (OS Lock might still be active). ({e}){OFF}"
                )
                final_dirn = working_dirn

            if aborted_by_user:
                safe_print(
                    f"{YELLOW}[!] Download aborted. Folder successfully marked as [INCOMPLETE].{OFF}"
                )
            elif failed_tracks > 0:
                safe_print(
                    f"\n{YELLOW}[!] Album downloaded partially ({failed_tracks} tracks skipped). Folder marked as [INCOMPLETE].{OFF}"
                )
        else:
            final_dirn = working_dirn

        if aborted_by_user:
            os._exit(1)

        if failed_tracks == 0 and not aborted_by_user:
            db_artist = album_attr.get("album_artist", "Unknown")
            db_album = album_attr.get("album_title", "Unknown")

        handle_download_id(
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

        # [OPÇÃO C]: Sumário Final Formatado
        skipped_count = sum(1 for r in results if r == "skipped")
        real_failed = sum(1 for r in results if r is False)
        downloaded_count = sum(1 for r in results if r is True)

        safe_print(f"\n{CYAN}{'━' * 44}{RESET}")
        safe_print(f"📊 {GREEN}RESUMO DO DOWNLOAD:{RESET} {album_title}")
        safe_print(
            f"   • Baixadas com sucesso : {GREEN}{downloaded_count}/{track_count}{RESET}")
        if skipped_count > 0:
            safe_print(
                f"   • Faixas puladas (Demo/Indisponível) : {YELLOW}{skipped_count}{RESET} (marcadas em .missing.txt)")
        if real_failed > 0:
            safe_print(f"   • Falhas de rede/download : {RED}{real_failed}{RESET}")
        safe_print(f"{CYAN}{'━' * 44}{RESET}\n")

    async def download_track(self, is_parallel=False,
                             position_pool=None, suppress_header=False):
        parse = await self.client.get_track_url(self.item_id, self.quality)
        if "sample" not in parse and parse.get("sampling_rate"):
            track_meta = await self.client.get_track_meta(self.item_id)

            if (
                getattr(self, "is_playlist", False) and
                not getattr(self, "playlist_as_albums", False) and
                getattr(self, "playlist_track_number", None)
            ):
                track_meta["track_number"] = self.playlist_track_number

            track_title = _get_title(track_meta)
            artist = _safe_get(track_meta, "performer", "name")
            album_name = track_meta.get("album", {}).get("title", "--")

            url = track_meta.get("album", {}).get("url", "")
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
                        ("Qualidade",
                         f"{file_format} ({bit_depth}bit/{float(sampling_rate):g}kHz)" if bit_depth else file_format),
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
                    f"{OFF}Skipping {track_title} as it doesn't meet quality requirement{OFF}"
                )
                return

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
                self.folder_format, track_attr, self.path, legacy_charmap=legacy_flag
            )
            os.makedirs(dirn, exist_ok=True)

            loop = asyncio.get_event_loop()

            skip_saved_cover = getattr(self, "is_playlist", False) and not getattr(
                self, "playlist_as_albums", False
            )
            if skip_saved_cover:
                # Imprime o aviso apenas na primeira faixa da playlist
                if getattr(self, "playlist_track_number", 1) == 1:
                    safe_print(
                        f"{OFF}[*] Skipping standard cover save to keep playlist folder clean{OFF}"
                    )
            elif self.settings.no_cover:
                safe_print(f"{OFF}[*] Skipping cover{OFF}")

            embed_cover_path = None
            if self.settings.embed_art:
                unique_embed_name = f".embed_{self.item_id}.jpg"
                embed_cover_path = os.path.join(dirn, unique_embed_name)
            else:
                safe_print(f"{OFF}[*] Skipping embedded art{OFF}")

            save_cover_now = not skip_saved_cover and not self.settings.no_cover
            if save_cover_now or self.settings.embed_art:
                async with _get_dir_lock(dirn):
                    await _get_cover_and_embed(
                        track_meta["album"]["image"]["large"],
                        dirn,
                        save_cover=save_cover_now,
                        embed_art=self.settings.embed_art,
                        saved_name="cover.jpg",
                        embed_name=(embed_cover_path and os.path.basename(
                            embed_cover_path)) or "",
                        saved_art_size=self.settings.saved_art_size,
                        embedded_art_size=self.settings.embedded_art_size,
                        session=self.http_session,
                        is_parallel=is_parallel,
                        position_pool=position_pool,
                    )

            is_mp3 = True if int(self.quality) == 5 else False

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
            )

            if embed_cover_path and os.path.isfile(embed_cover_path):
                try:
                    os.remove(embed_cover_path)
                except OSError:
                    pass

        if success:
            db_artist = track_attr.get("artist", "Unknown")
            db_album = track_attr.get("album", "Unknown")

            handle_download_id(
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
        else:
            safe_print(f"{OFF}[*] Demo. Skipping{OFF}")

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
    ) -> bool:
        extension = ".mp3" if is_mp3 else ".flac"
        loop = asyncio.get_event_loop()

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
                f"{CYAN}[*] Skipping: {
                    os.path.basename(final_file)} (Already exists){OFF}"
            )
            return True

        if abort_event.is_set():
            return False

        await asyncio.sleep(1)
        try:
            track_url_dict["url"]
        except KeyError:
            safe_print(f"{OFF}Track not available for download{OFF}")
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
        track_title = track_metadata.get("title")
        track_no = str(track_metadata.get("track_number", 0)).zfill(2)
        desc = f"{track_no}. {track_title}"

        FALLBACK_TIERS = [27, 7, 6, 5]
        TIER_NAMES = {
            27: "24-bit/>96kHz",
            7: "24-bit/96kHz",
            6: "16-bit/44.1kHz (CD)",
            5: "MP3 320kbps",
        }

        try:
            start_idx = FALLBACK_TIERS.index(int(self.quality))
        except ValueError:
            start_idx = 0

        qualities_to_try = FALLBACK_TIERS[start_idx:]
        success = False
        final_fmt = int(self.quality)

        for attempt_fmt in qualities_to_try:
            if abort_event.is_set():
                return False

            if attempt_fmt != int(self.quality):
                safe_print(
                    f"{YELLOW}[!] Automatic downgrade: Attempting to save in {
                        TIER_NAMES[attempt_fmt]}...{OFF}"
                )

            async def get_fresh_url(fmt=attempt_fmt, force_segments=False):
                return await self.client.get_track_url(
                    track_metadata["id"], fmt_id=fmt, force_segments=force_segments
                )

            try:
                fresh_track_dict = await get_fresh_url(force_segments=False)

                # [OPÇÃO E]: Se a API entregar amostra, não faz fallback inútil para outras qualidades
                if fresh_track_dict.get("sample") is True:
                    safe_print(
                        f"{CYAN}[PULADA]{RESET} Faixa {track_no} - {track_title} ({YELLOW}URL retornada é apenas amostra{RESET})")
                    create_missing_placeholder(
                        track_metadata, root_dir, "URL retornada é apenas amostra")
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
                        ),
                        success = True
                        final_fmt = attempt_fmt
                        break
                    except _PermanentDownloadError as e:
                        # Erro permanente (401/403/404/451): a faixa nao esta
                        # disponivel de verdade (regiao/licenca/sessao), nao
                        # e' um bloqueio passageiro de CDN. Tentar o fallback
                        # segmentado ou cair pra outra qualidade nunca vai
                        # resolver isso -- e' a mesma autorizacao em qualquer
                        # tier. Desiste da faixa inteira aqui, em vez de
                        # cascatear por ate' 4 tiers de qualidade, cada um
                        # tentando de novo (o que antes gerava uma parede de
                        # mensagens "Akamai block detected" enganosas pra uma
                        # faixa que so' nao estava disponivel mesmo).
                        if abort_event.is_set():
                            return False
                        safe_print(f"{YELLOW}[!] Faixa indisponível, pulando: {e}{OFF}")
                        return False
                    except Exception:
                        if abort_event.is_set():
                            return False
                        safe_print(
                            f"{YELLOW}[!] Akamai block detected. Activating fallback segmented download...{OFF}"
                        )
                        fresh_track_dict = await get_fresh_url(force_segments=True)

                if "url_template" in fresh_track_dict:
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
                    ),
                    success = True
                    final_fmt = attempt_fmt
                    break
                elif not success:
                    raise Exception("No valid format returned by the server.")

            except _PermanentDownloadError as e:
                # Mesmo raciocinio do except interno acima: se o fallback
                # segmentado tambem bateu num erro permanente, nao adianta
                # cascatear pelos tiers de qualidade restantes.
                if abort_event.is_set():
                    return False
                safe_print(f"{YELLOW}[!] Faixa indisponível, pulando: {e}{OFF}")
                return False
            except Exception:
                pass

        if not success and not abort_event.is_set():
            safe_print(
                f"\n{RED}[!] TRACK {track_no} DEFINITIVELY DISCARDED AFTER ALL DOWNGRADES.{OFF}"
            )
            safe_print(f"{YELLOW}[!] Skipping to the next track...{OFF}\n")
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
            safe_print(f"{RED}[!] Error tagging: {e}{OFF}")

        if (
            getattr(self, "fetch_lyrics", False) and
            hasattr(self, "lyrics_engine") and
            not abort_event.is_set()
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
                translation_lang and
                not qobuz_translation_response and
                isinstance(qobuz_lyrics_response, dict)
            ):
                original_block = qobuz_lyrics_response.get("original")
                original_lang = (
                    original_block.get("lang")
                    if isinstance(original_block, dict)
                    else None
                )
                if original_lang:
                    if original_lang.lower() == translation_lang.lower():
                        translation_note = f"    ℹ️  Lyrics already in {
                            translation_lang.upper()} -- no translation needed."
                    else:
                        translation_note = f"    ℹ️  No {
                            translation_lang.upper()} translation available on Qobuz yet for this track."

            def _inject_lyrics_and_print():
                with print_lock:
                    self.lyrics_engine.fetch_and_inject(
                        file_path=final_file,
                        artist=search_artist,
                        track=track_title,
                        album=search_album,
                        save_lrc=not self.no_lrc_files,
                        embed_lyrics=getattr(self.settings, "embed_lyrics", True),
                        qobuz_lyrics_response=qobuz_lyrics_response,
                        qobuz_translation_response=qobuz_translation_response,
                    )
                    if translation_note:
                        tqdm.write(f"{CYAN}{translation_note}{OFF}")

            await loop.run_in_executor(None, _inject_lyrics_and_print)

        emit_progress_json(
            self.settings,
            "track_done",
            track_id=self.item_id,
            path=final_file,
        )

        # Verificacao de integridade pos-download (opcional, off por padrao).
        # Decodifica o arquivo final inteiro com ffmpeg pra pegar corrupcao
        # real no stream de audio -- coisa que passa batido em downloads que
        # cortam no meio mas ainda geram um arquivo com tags/tamanho
        # plausiveis. Fica atras de um flag porque decodificar cada faixa
        # adiciona tempo real numa discografia grande; quem quiser sempre
        # ligado usa --verify-download (ver settings.py/cli.py) ou roda
        # "python check_audio.py --verify-library" depois, em lote.
        if (
            getattr(self.settings, "verify_after_download", False) and
            not abort_event.is_set()
        ):

            def _run_verify():
                return verify_audio_integrity(final_file)

            ok, verify_message = await loop.run_in_executor(None, _run_verify)
            if not ok:
                with print_lock:
                    tqdm.write(
                        f"{RED}[!] Verificação de integridade falhou para "
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
                f"{YELLOW}[*] Sleeping for {delay_time} seconds to prevent rate limiting...{OFF}"
            )
            await asyncio.sleep(delay_time)

        return True

    @staticmethod
    def _get_filename_attr(track_artist, track_metadata: dict, album_metadata: dict):
        def _flatten_artists(artist_data):
            if isinstance(artist_data, list):
                return ", ".join(artist_data)
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
            if isinstance(artist_data, list):
                return ", ".join(artist_data)
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
                " ／ ",
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
            "release_type": format_release_type(album_meta.get("release_type")),
        }

    @staticmethod
    def _get_album_attr(meta, album_title, file_format, bit_depth, sampling_rate):
        def _flatten_artists(artist_data):
            if isinstance(artist_data, list):
                return ", ".join(artist_data)
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
                " ∕ ",
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
            "release_type": format_release_type(meta.get("release_type")),
        }

    async def _get_format(self, item_dict, is_track_id=False, track_url_dict=None):
        if not is_track_id:
            if "tracks" not in item_dict or not item_dict["tracks"].get("items"):
                raise NonStreamable(
                    "This release has no tracks available (possibly region-locked or removed)"
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
                raise KeyError("No URL dict returned by API")

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

        except Exception:  # antes: (KeyError, requests.exceptions.HTTPError,
            # Exception) -- Exception ja cobria os outros dois,
            # era redundante; simplificado ao tirar o requests
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
        extension = ".flac" if file_format.lower() == "flac" else ".mp3"

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
                        track_path = sanitize_filename(
                            clean_filename(
                                multi_disc_fmt.format(**filename_attr),
                                legacy_charmap=legacy_flag,
                            ),
                            replacement_text="_",
                        )
                    else:
                        if is_multiple and not self.settings.multiple_disc_one_dir:
                            disc_dir = f"{
                                self.settings.multiple_disc_prefix} {
                                track_metadata['media_number']:02}"
                            os.path.join(root_dir, disc_dir)

                        track_path = sanitize_filename(
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

        safe_print(f"{CYAN}[+] Generating Digital Booklet...{OFF}")

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
                f.write(f"ALBUM      : {album_title}{explicit_tag}\n")
                if composer != "N/A":
                    f.write(f"COMPOSER   : {composer}\n")
                f.write(f"MAIN ART.  : {artist_name}\n")
                f.write(f"LABEL      : {label}\n")
                f.write(f"GENRE      : {genre}\n")
                f.write(f"RELEASE    : {release_date}\n")
                f.write(
                    f"QUALITY    : {file_format} ({bit_depth}-Bit / {sampling_rate} kHz)\n"
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
                                f.write(f"    * {line.strip()}\n")
                    else:
                        t_artist = _safe_get(
                            track, "performer", "name", default=artist_name
                        )
                        f.write(f"    {t_artist}\n")
                    f.write("\n")

                description = meta.get("description")
                if description:
                    f.write(
                        "\n" + "=" * 70 + "\nALBUM REVIEW / NOTES\n" + "=" * 70 + "\n\n"
                    )
                    clean_desc = re.sub(
                        r"<[^<]+>", "", re.sub(r"<br\s*/?>", "\n", str(description))
                    )
                    for p in clean_desc.split("\n"):
                        if p.strip():
                            f.write(textwrap.fill(p.strip(), width=70) + "\n\n")

            safe_print(
                f"{GREEN}  L Completed: Digital Booklet.txt (Credits & Review){OFF}"
            )
        except Exception as e:
            safe_print(f"{RED}[!] Error creating booklet: {e}{OFF}")

    async def _fetch_qobuz_lyrics_json(self, track_id, language=None):
        try:
            params = {"track_id": track_id}
            if language:
                params["language"] = language
            params["request_ts"] = int(time.time())
            params["request_sig"] = self.client._modern_sig(
                "track/lyricsUrl", params, self.client.sec
            )

            # httpx.AsyncClient.request() NAO e' um context manager (diferente
            # do aiohttp, de onde essa sintaxe "async with ... as r" veio) --
            # ele retorna a Response direto, ja pronta. E .status_code (nao
            # .status) e .json() e' sincrono no httpx (nao precisa de await).
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

            loop = asyncio.get_event_loop()
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
                    f"{CYAN}[+] Lyrics cleanly formatted and appended to Digital Booklet.{OFF}"
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
    G, Y, C, O, R = GREEN, YELLOW, CYAN, OFF, RESET

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
        "Connection": "keep-alive",
    }

    position = position_pool.acquire() if (is_parallel and position_pool) else 0

    if not is_parallel:
        safe_print(f"{C}[+] Em Progresso: {track_name}{O}")
        tqdm_desc = f" {R}⬇️{O}"
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
    max_retries = 5
    backoff_delays = [2, 4, 8, 16, 32]

    owns_session = session is None
    # timeout=(10, 60) no requests = (connect, read). Equivalente no httpx e
    # httpx.Timeout com connect e read separados (write/pool herdam do padrao
    # geral, aqui deixados generosos igual ao read pra nao cortar upload de
    # dados internos do proprio httpx).
    timeout_cfg = httpx.Timeout(60.0, connect=10.0)
    http = session or httpx.AsyncClient(follow_redirects=True)

    try:
        for attempt in range(max_retries):
            if abort_event.is_set():
                return
            try:
                url = (
                    url_or_callable() if callable(url_or_callable) else url_or_callable
                )

                if downloaded_size > 0:
                    headers["Range"] = f"bytes={downloaded_size}-"
                    mode = "ab"
                else:
                    headers["Range"] = "bytes=0-"
                    mode = "wb"

                # http.get(..., stream=True) do requests virou http.stream(...)
                # no httpx -- e no httpx isso e OBRIGATORIAMENTE um context
                # manager (nao da pra pegar a resposta e iterar depois fora do
                # "with", a conexao fecha na saida). Por isso o bloco que antes
                # vinha solto agora mora todo dentro do "with" abaixo.
                async with http.stream(
                    "GET", url, headers=headers, timeout=timeout_cfg,
                ) as r:
                    if r.status_code == 416:
                        return
                    if r.status_code == 404:
                        raise _PermanentDownloadError(
                            "HTTP 404: File not found on server.")
                    if r.status_code in (401, 403, 451):
                        raise _PermanentDownloadError(
                            f"HTTP {r.status_code}: faixa indisponível (bloqueio de "
                            f"região, direitos autorais ou sessão expirada)."
                        )
                    if r.status_code not in [200, 206]:
                        raise Exception(f"Status Server: {r.status_code}")

                    if total_size == 0:
                        total_size = downloaded_size + int(
                            r.headers.get("content-length", 0)
                        )

                    if is_parallel and downloaded_size == 0 and attempt == 0:
                        size_mb = total_size / (1024 * 1024)
                        safe_print(
                            f"{C}[+] Em Progresso: {track_name} [{size_mb:.1f} MB]{O}"
                        )

                    with open(fname, mode) as file, tqdm(
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

                        # iter_content() do requests -> iter_bytes() no httpx
                        async for data in r.aiter_bytes(chunk_size=65536):
                            if abort_event.is_set():
                                return
                            if data:
                                size = file.write(data)
                                downloaded_size += size
                                bar.update(size)

                if downloaded_size >= total_size:
                    safe_print(f"{G}  L Completed: {track_name}{O}")
                    return

            except _PermanentDownloadError as e:
                if os.path.exists(fname):
                    os.remove(fname)
                safe_print(f"{Y}[!] Indisponível: {track_name} ({e}){O}")
                raise

            except (KeyboardInterrupt, SystemExit):
                # Se o usuário apertar Ctrl+C, aborta IMEDIATAMENTE sem esperar nem
                # tentar de novo
                abort_event.set()
                if os.path.exists(fname):
                    try:
                        os.remove(fname)
                    except OSError:
                        pass
                return

            except Exception as e:
                # Se o usuário já pediu para parar, não tenta reconectar
                if abort_event.is_set():
                    if os.path.exists(fname):
                        try:
                            os.remove(fname)
                        except OSError:
                            pass
                        return

                if "404" in str(e):
                    if os.path.exists(fname):
                        os.remove(fname)
                    raise _PermanentDownloadError("HTTP 404: File not found on server.")

                if attempt < max_retries - 1:
                    wait = backoff_delays[attempt]
                    safe_print(
                        f"\n{Y}[!] Falha de rede. Tentando de novo em {wait}s ({
                            attempt + 1}/{max_retries}) | Detalhes: {e}{O}"
                    )
                    time.sleep(wait)

                else:
                    if os.path.exists(fname):
                        os.remove(fname)
                    raise Exception(f"Definitive timeout after {max_retries} attempts. Last error: {e}"
                                    )

        if downloaded_size < total_size and not abort_event.is_set():
            if os.path.exists(fname):
                os.remove(fname)
            raise Exception("Incomplete download")
    finally:
        if owns_session:
            try:
                http.aclose()
            except Exception as e:
                logger.debug(
                    f"Falha ao fechar sessao HTTP do download segmentado (ignorado): {e}"
                )
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
    Aplica a mesma substituicao de resolucao que _get_extra() sempre fez
    internamente (troca o "_600." da URL pelo tamanho pedido). Extraido pra
    funcao separada pra dar pra COMPARAR duas URLs resolvidas -- por
    exemplo, saved_art_size vs embedded_art_size -- sem ter que duplicar
    essa logica de substituicao em outro lugar.
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
        safe_print(f"{CYAN}[*] Skipping {label}: {extra} (Already downloaded){OFF}")
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
            f"  {YELLOW}[!] Skipping {label} '{extra}': URL unreachable ({e}){OFF}"
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
):
    """
    Baixa a capa salva (cover.jpg) e a capa de embed, evitando baixar a
    MESMA imagem duas vezes quando saved_art_size e embedded_art_size
    resolvem pra' URL identica (que e' o caso mais comum -- por padrao
    ambos vem "org" do template de config.ini gerado por cli.py). Antes,
    _get_extra() era chamado duas vezes sempre que embed_art estava
    ligado, sem checar se a segunda chamada ia baixar bytes identicos aos
    da primeira.

    Quando os tamanhos realmente diferem (usuario configurou resoluções
    diferentes de proposito), baixa os dois de verdade -- nao ha' como
    reaproveitar sem redimensionar a imagem localmente, e o projeto nao
    tem Pillow como dependencia pra isso.
    """
    if abort_event.is_set():
        return

    saved_url = _resolve_art_url(item, saved_art_size) if save_cover else None
    embed_url = _resolve_art_url(item, embedded_art_size) if embed_art else None

    if save_cover:
        await _get_extra(
            item, dirn, extra=saved_name, art_size=saved_art_size,
            session=session, label="cover art",
            is_parallel=is_parallel, position_pool=position_pool,
        )

    if not embed_art:
        return

    saved_file = os.path.join(dirn, saved_name)
    embed_file = os.path.join(dirn, embed_name)

    if os.path.isfile(embed_file):
        safe_print(
            f"{YELLOW}[*] Skipping embedded cover art: {embed_name} (Already downloaded){OFF}")
        return

    if save_cover and saved_url == embed_url and os.path.isfile(saved_file):
        try:
            shutil.copyfile(saved_file, embed_file)
            safe_print(f"{OFF}  [*] Reusing cover art for embed.{OFF}")
            return
        except OSError as e:
            logger.debug(f"Falha ao copiar cover.jpg pra embed, baixando de novo: {e}")

    await _get_extra(
        item, dirn, extra=embed_name, art_size=embedded_art_size,
        session=session, label="embedded cover art",
        is_parallel=is_parallel, position_pool=position_pool,
    )


def _clean_format_str(folder: str, track: str, file_format: str) -> Tuple[str, str]:
    final = []
    for i, fs in enumerate((folder, track)):
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
    G, C, O, R = GREEN, CYAN, OFF, RESET

    tmp_fname = fname + ".mp4"
    n_segments = track_url_dict["n_segments"]
    url_template = track_url_dict["url_template"]
    raw_key = track_url_dict["raw_key"]

    workers = segment_workers if segment_workers else 4

    owns_session = session is None
    http = session or httpx.AsyncClient(follow_redirects=True)

    async def get_seg_size(seg_num):
        if abort_event.is_set():
            return 0
        url = url_template.replace("$SEGMENT$", str(seg_num))
        try:
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
        size_mb = total_size / (1024 * 1024)
        safe_print(f"{C}[+] In progresso: {track_name} [{size_mb:.1f} MB]{O}")
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
        safe_print(f"{C}[+] Em Progresso: {track_name}{O}")
        tqdm_desc = f" {R}Segmented Download{O}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
        ncols = None
        dynamic_ncols = True

    async def fetch_segment_fluid(seg_num):
        if abort_event.is_set():
            return bytearray()
        url = url_template.replace("$SEGMENT$", str(seg_num))
        seg_data = bytearray()
        try:
            async with http.stream("GET", url, timeout=15) as r:
                r.raise_for_status()
                async for chunk in r.aiter_bytes(chunk_size=65536):
                    if abort_event.is_set():
                        return bytearray()
                    seg_data.extend(chunk)
                    bar.update(len(chunk))
        except Exception:
            pass
        return seg_data

    try:
        with open(tmp_fname, "wb") as file, tqdm(
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
                        raise ConnectionError(f"Cannot find segment UUID for {fname}")

                file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))

            if n_segments >= 2:
                semaphore = asyncio.Semaphore(workers)

                async def bounded_fetch(i):
                    async with semaphore:
                        return await fetch_segment_fluid(i)

                tasks_seg = [bounded_fetch(i) for i in range(2, n_segments + 1)]
                results = await asyncio.gather(*tasks_seg)

                for seg_data in results:
                    if not abort_event.is_set():
                        file.write(
                            _decrypt_qobuz_segment(seg_data, raw_key, segment_uuid)
                        )

        if abort_event.is_set():
            return
        if not is_parallel:
            safe_print(f" {G}  > Assembling the final FLAC file...{O}")

        remux = subprocess.run(
            [
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
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if remux.returncode != 0:
            raise ConnectionError(f"FFmpeg remux failed for {fname}")

        safe_print(f"{G}  L Completed: {track_name}{O}")

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
                    f"Falha ao fechar sessao HTTP no cleanup final (ignorado): {e}"
                )
        if is_parallel and position_pool:
            position_pool.release(position)


def _get_qobuz_segment_uuid(segment_data):
    pos = 0
    while pos + 24 <= len(segment_data):
        size = int.from_bytes(segment_data[pos: pos + 4], "big")
        if size <= 0 or pos + size > len(segment_data):
            break

        if bytes(segment_data[pos + 4: pos + 8]) == b"uuid":
            return bytes(segment_data[pos + 8: pos + 24])
        pos += size
    return None


def _decrypt_qobuz_segment(segment_data, raw_key, segment_uuid):
    if segment_uuid is None:
        return bytes(segment_data)

    buf = bytearray(segment_data)
    pos = 0
    while pos + 8 <= len(buf):
        size = int.from_bytes(buf[pos: pos + 4], "big")
        if size <= 0 or pos + size > len(buf):
            break

        if (
            bytes(buf[pos + 4: pos + 8]) == b"uuid" and
            bytes(buf[pos + 8: pos + 24]) == segment_uuid
        ):
            pointer = pos + 28
            data_end = pos + int.from_bytes(buf[pointer: pointer + 4], "big")
            pointer += 4
            counter_len = buf[pointer]
            pointer += 1
            frame_count = int.from_bytes(buf[pointer: pointer + 3], "big")
            pointer += 3

            for _ in range(frame_count):
                frame_len = int.from_bytes(buf[pointer: pointer + 4], "big")
                pointer += 6
                flags = int.from_bytes(buf[pointer: pointer + 2], "big")
                pointer += 2
                frame_start, data_end = data_end, data_end + frame_len

                if flags:
                    counter = bytes(buf[pointer: pointer + counter_len]) + (
                        b"\x00" * (16 - counter_len)
                    )
                    # modes.CTR(counter) da "cryptography" toma o bloco de
                    # 16 bytes INTEIRO como contador inicial e incrementa
                    # como inteiro big-endian a cada bloco -- exatamente
                    # equivalente ao Counter.new(128, initial_value=X,
                    # little_endian=False) do pycryptodome, so' que sem
                    # precisar montar o objeto Counter separado: o `counter`
                    # (bytes) IS o X em forma binaria. Mesmo esquema de DRM
                    # do Qobuz de antes, nada muda no resultado.
                    decryptor = Cipher(
                        algorithms.AES(raw_key), modes.CTR(counter)
                    ).decryptor()
                    plaintext = decryptor.update(
                        bytes(buf[frame_start:data_end])
                    ) + decryptor.finalize()
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
        logger.error(f"{RED}Error downloading goodies: {e}", exc_info=True)


def _clean_embed_art(dirn, settings=None):
    embed_file = os.path.join(dirn, EMB_COVER_NAME)
    if os.path.exists(embed_file):
        try:
            time.sleep(0.5)
            os.remove(embed_file)
        except OSError:
            pass
