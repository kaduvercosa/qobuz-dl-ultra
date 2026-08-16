from .lyrics_engine import LyricsEngine
import logging
import os
import sys
import time
import random
import subprocess
import re
import threading
import signal
from typing import Tuple
import asyncio
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from pathvalidate import sanitize_filename, sanitize_filepath
from tqdm import tqdm

import qobuz_dl.metadata as metadata
from qobuz_dl.color import OFF, GREEN, RED, YELLOW, CYAN
from qobuz_dl.exceptions import NonStreamable
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.utils import get_album_artist, clean_filename
from qobuz_dl.db import handle_download_id
from qobuz_dl.constants import DEFAULT_FOLDER, DEFAULT_TRACK, DEFAULT_MULTIPLE_DISC_TRACK, OK_MAX_CHARACTER_LENGTH

# UI Lock to prevent text scrambling during multithreading
print_lock = threading.Lock()

# Global Abort Event for graceful CTRL+C handling and file unlock
abort_event = threading.Event()

def safe_print(*args, **kwargs):
    """
    Thread-safe print function. Prevents UI glitches ("cursor wars") 
    when multiple download threads attempt to log to the terminal simultaneously.
    """
    with print_lock:
        text = " ".join(map(str, args))
        end = kwargs.get('end', '\n')
        tqdm.write(text, end=end)

# --- FIX ISSUE #216: Normalize Release Type ---
def format_release_type(release_type: str) -> str:
    """
    Normalizes the release type from Qobuz APIs.
    
    Converts raw API strings (e.g., 'ep' to 'EP', 'single' to 'Single', 'album' to 'Album').
    
    Args:
        release_type (str): The raw release type string from the API.

    Returns:
        str: The normalized release type, or 'Unknown' as a robust fallback.
    """
    if not release_type:
        return "Unknown"
    
    release_type = release_type.lower()
    if release_type == "ep":
        return "EP"
        
    return release_type.title()
# --------------------------------------------------------

def process_folder_format_with_subdirs(folder_format, attr_dict, path=None, legacy_charmap=False):
    """
    Parses and sanitizes the user's custom folder format string, generating a safe directory path.
    """
    path_parts = folder_format.split('/')
    cleaned_parts = []
    for part in path_parts:
        if part:
            try:
                formatted_part = part.format(**attr_dict)
                cleaned_part = sanitize_filepath(clean_filename(formatted_part, legacy_charmap=legacy_flag if 'legacy_flag' in locals() else legacy_charmap), replacement_text="_")
                
                if cleaned_part and len(cleaned_part) > 120:
                    start_f = cleaned_part[:60].rstrip(' ."-_\'')
                    end_f = cleaned_part[-50:].lstrip(' ."-_\'')
                    cleaned_part = f"{start_f}...{end_f}"
                    
                if cleaned_part:
                    cleaned_parts.append(cleaned_part)
            except KeyError as e:
                logger.warning(f"{YELLOW}Format error ({e}), using original text.{OFF}")
                cleaned_part = sanitize_filepath(clean_filename(part, legacy_charmap=legacy_charmap), replacement_text="_")
                
                if cleaned_part and len(cleaned_part) > 120:
                    start_f = cleaned_part[:60].rstrip(' ."-_\'')
                    end_f = cleaned_part[-50:].lstrip(' ."-_\'')
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

        # Sessao HTTP unica, compartilhada por TODAS as chamadas de rede
        # sincronas feitas por esta instancia de Download durante o ciclo de
        # vida de um item (faixas de audio, capas, goodies, letras do Qobuz
        # via CloudFront, e o fallback LRCLIB). Reaproveitar a sessao mantem
        # as conexoes TCP/TLS "quentes" (keep-alive/connection pooling) em
        # vez de renegociar handshake a cada request -- importante sobretudo
        # em redes moveis/instaveis (ex: A-Shell no iOS). E' seguro usar essa
        # mesma sessao a partir de multiplas worker threads simultaneas (via
        # run_in_executor) para fazer GETs concorrentes -- o pool de conexoes
        # do urllib3 por baixo do requests.Session e' thread-safe para isso.
        # Fechada de forma garantida em close_session(), chamado no finally
        # de download_id_by_type().
        self.http_session = requests.Session()
        self.http_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
            "Connection": "keep-alive",
        })

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
        self._original_multiple_disc_track_format = settings.multiple_disc_track_format if settings else DEFAULT_MULTIPLE_DISC_TRACK

    async def download_id_by_type(self, track=True):
        """Routes the download request based on the item type."""
        self.folder_format = self._original_folder_format
        self.track_format = self._original_track_format
        if self.settings:
            self.settings.multiple_disc_track_format = self._original_multiple_disc_track_format
        
        try:
            if not track:
                await self.download_release()
            else:
                await self.download_track()
        finally:
            # Garante que a sessao HTTP compartilhada seja fechada ao final
            # deste item, mesmo se download_release/download_track lancar
            # excecao, for interrompido (Ctrl+C -> KeyboardInterrupt) ou
            # simplesmente terminar normal. Sem isso, cada Download() que
            # falha no meio do caminho vazaria conexoes abertas.
            self.close_session()

    def close_session(self):
        """
        Fecha a sessao HTTP compartilhada desta instancia de Download (usada
        por tqdm_download, tqdm_download_segments, downloads de capa/goodies
        e busca de letras no Qobuz). Tambem fecha a sessao do LyricsEngine
        SE ela nao for a mesma instancia (nao deveria ser o caso aqui, ja que
        passamos self.http_session pra ele -- mas close() la' e' um no-op
        nesse caso, ver LyricsEngine.close()).
        Idempotente e silenciosa: pode ser chamada mais de uma vez sem erro.
        """
        if hasattr(self, "lyrics_engine"):
            try:
                self.lyrics_engine.close()
            except Exception:
                pass

        session = getattr(self, "http_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass

    async def download_release(self):
        """Handles the full download sequence for an album/release."""
        count = 0
        album_meta = await self.client.get_album_meta(self.item_id)

        if not album_meta.get("streamable"):
            raise NonStreamable("This release is not streamable")

        if self.albums_only and (
            album_meta.get("release_type") != "album"
            or album_meta.get("artist").get("name") == "Various Artists"
        ):
            logger.info(f'{OFF}Ignoring Single/EP/VA: {album_meta.get("title", "n/a")}')
            return

        album_title = _get_title(album_meta)
        url = album_meta.get("url", "")
        release_date = album_meta.get("release_date_original", "")

        format_info = await self._get_format(album_meta)
        file_format, quality_met, bit_depth, sampling_rate = format_info

        if not self.downgrade_quality and not quality_met:
            logger.info(f"{OFF}Skipping {album_title} as it doesn't meet quality requirement")
            return

        logger.info(
            f"\n{YELLOW}Downloading: {album_title}\nQuality: {file_format}"
            f" ({bit_depth}/{sampling_rate})\n{OFF}"
        )
        
        album_attr = self._get_album_attr(
            album_meta, album_title, file_format, bit_depth, sampling_rate
        )
        
        self._determine_formats(album_meta=album_meta, album_attr=album_attr, tracks_meta=album_meta["tracks"]["items"],
                                track_attr=None, is_track=False, file_format=file_format, settings=self.settings)
        
        legacy_flag = getattr(self.settings, 'legacy_charmap', False) if hasattr(self, 'settings') else False
        target_dirn = process_folder_format_with_subdirs(self.folder_format, album_attr, self.path, legacy_charmap=legacy_flag)
        base_path, folder_name = os.path.split(target_dirn)
        
        incomplete_dirn = os.path.join(base_path, f"[INCOMPLETE] {folder_name}")
        inprogress_dirn = os.path.join(base_path, f"[IN PROGRESS] {folder_name}")
        
        is_standard_album = not getattr(self, 'is_playlist', False)
        
        if is_standard_album:
            working_dirn = inprogress_dirn
            try:
                if os.path.exists(incomplete_dirn):
                    os.rename(incomplete_dirn, working_dirn)
                elif os.path.exists(target_dirn):
                    os.rename(target_dirn, working_dirn)
            except OSError as e:
                logger.warning(f"{YELLOW}[!] Could not rename existing folder to [IN PROGRESS]. Operating in standard mode. ({e}){OFF}")
                working_dirn = target_dirn
        else:
            working_dirn = target_dirn
            
        os.makedirs(working_dirn, exist_ok=True)
        dirn = working_dirn

        media_count = album_meta.get("media_count", 1)
        is_multiple = True if media_count > 1 else False
        
        delay_time = getattr(self.settings, 'delay', 0)
        if delay_time == 0 and '--delay' in sys.argv:
            try: delay_time = int(sys.argv[sys.argv.index('--delay') + 1])
            except: pass
            
        active_workers = int(getattr(self.settings, 'max_workers', 3))
        is_parallel = False
        
        if delay_time > 0:
            safe_print(f"{YELLOW}[*] Safety Delay active: Multithreading disabled (Sequential mode).{OFF}")
            active_workers = 1
        elif active_workers > 1:
            is_parallel = True
            safe_print(f"{YELLOW}[*] Multithreading Enabled ({active_workers} workers): UI optimized for clean parallel logging.{OFF}")
            
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
        except Exception:
            pass

        try:
            self._generate_tracklist(album_meta, dirn, album_title, file_format, bit_depth, sampling_rate)

            # Downloads de capa/goodies aqui rodam UMA vez por album (nao por
            # faixa), entao o impacto e' bem menor que o das faixas em si --
            # mas ainda sao chamadas sincronas/bloqueantes (_get_extra e'
            # tqdm_download por baixo dos panos) rodando direto numa
            # coroutine async, entao offload por consistencia: sem isso, o
            # inicio do download paralelo das faixas fica esperando a capa
            # baixar antes de sequer comecar.
            loop = asyncio.get_event_loop()

            if self.settings.no_cover:
                logger.info(f"{OFF}Skipping cover")
            else:
                await loop.run_in_executor(
                    None,
                    lambda: _get_extra(album_meta["image"]["large"], dirn, art_size=self.settings.saved_art_size, session=self.http_session),
                )

            if self.settings.embed_art:
                await loop.run_in_executor(
                    None,
                    lambda: _get_extra(album_meta["image"]["large"], dirn, extra=EMB_COVER_NAME, art_size=self.settings.embedded_art_size, session=self.http_session),
                )

            if "goodies" in album_meta:
                await loop.run_in_executor(None, lambda: _download_goodies(album_meta, dirn, session=self.http_session))
                
            if getattr(self, 'booklet_only', False):
                safe_print(f"{YELLOW}[*] --booklet-only flag active. Skipping audio tracks.{OFF}")
                if is_standard_album and working_dirn == inprogress_dirn:
                    try:
                        os.rename(working_dirn, incomplete_dirn)
                    except OSError as e:
                        logger.warning(f"{YELLOW}[!] Impossibile rinominare la cartella in [INCOMPLETE]. ({e}){OFF}")
                return
             
            semaphore = asyncio.Semaphore(active_workers)

            async def process_track(idx, i):
                nonlocal failed_tracks
                if abort_event.is_set():
                    return False
                async with semaphore:
                    try:
                        parse = await self.client.get_track_url(i["id"], fmt_id=self.quality)
                    except Exception as e:
                        safe_print(f"{RED}[!] API Error for track {i.get('track_number', 'unknown')} (ID: {i['id']}): {e}{OFF}")
                        safe_print(f"{YELLOW}[*] Skipping track and continuing with the album...{OFF}")
                        return False

                    if "sample" not in parse and parse.get("sampling_rate"):
                        is_mp3 = True if int(self.quality) == 5 else False
                        res = await self._download_and_tag(
                            dirn, idx, parse, i, album_meta, False,
                            is_mp3, i.get("media_number") if is_multiple else None, is_parallel=is_parallel
                        )
                        return res
                    else:
                        logger.info(f"{OFF}Demo. Skipping")
                        return False

            tasks = [process_track(idx, i) for idx, i in enumerate(album_meta["tracks"]["items"])]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            for res in results:
                if res is False or isinstance(res, Exception):
                    failed_tracks += 1
                    
            if not abort_event.is_set():
                _clean_embed_art(dirn, self.settings)
                if getattr(self, 'fetch_lyrics', False) and not self.no_credits:
                    self._append_lyrics_to_booklet(dirn, album_title)
                    
        except (KeyboardInterrupt, SystemExit):
            abort_event.set()
            aborted_by_user = True
            safe_print(f"\n{RED}[!] CTRL+C Intercepted: Securing files and folders...{OFF}")
            
        finally:
            try:
                if original_sigint:
                    signal.signal(signal.SIGINT, original_sigint)
            except Exception:
                pass
                
        if aborted_by_user:
            time.sleep(1.5)
            
        if is_standard_album and working_dirn == inprogress_dirn:
            final_dirn = target_dirn if (failed_tracks == 0 and not aborted_by_user) else incomplete_dirn
            try:
                os.rename(working_dirn, final_dirn)
            except OSError as e:
                logger.warning(f"{YELLOW}[!] Could not rename final folder state (OS Lock might still be active). ({e}){OFF}")
                final_dirn = working_dirn
            
            if aborted_by_user:
                safe_print(f"{YELLOW}[!] Download aborted. Folder successfully marked as [INCOMPLETE].{OFF}")
            elif failed_tracks > 0:
                safe_print(f"\n{YELLOW}[!] Album downloaded partially ({failed_tracks} tracks skipped). Folder marked as [INCOMPLETE].{OFF}")
        else:
            final_dirn = working_dirn
        
        if aborted_by_user:
            os._exit(1)
            
        db_artist = album_attr.get("album_artist", "Unknown")
        db_album = album_attr.get("album_title", "Unknown")
        
        handle_download_id(db_path=self.download_db, item_id=self.item_id, add_id=True, media_type="album",
                           quality=self.quality, file_format=file_format, quality_met=quality_met,
                           bit_depth=bit_depth, sampling_rate=sampling_rate, saved_path=final_dirn,
                           url=url, release_date=release_date, artist=db_artist, album=db_album)
        safe_print(f"{GREEN}Completed{OFF}")

    async def download_track(self):
        """Handles the download sequence for a standalone single track."""
        parse = await self.client.get_track_url(self.item_id, self.quality)
        if "sample" not in parse and parse.get("sampling_rate"):
            track_meta = await self.client.get_track_meta(self.item_id)
            
            if getattr(self, 'is_playlist', False) and not getattr(self, 'playlist_as_albums', False) and getattr(self, 'playlist_track_number', None):
                track_meta["track_number"] = self.playlist_track_number
            
            track_title = _get_title(track_meta)
            artist = _safe_get(track_meta, "performer", "name")
            logger.info(f"\n{YELLOW}Downloading: {artist} - {track_title}{OFF}")
            url = track_meta.get("album", {}).get("url", "")
            release_date = track_meta.get("release_date_original", "")
            format_info = await self._get_format(track_meta, is_track_id=True, track_url_dict=parse)
            file_format, quality_met, bit_depth, sampling_rate = format_info

            folder_format, track_format = _clean_format_str(self.folder_format, self.track_format, str(bit_depth))

            if not self.downgrade_quality and not quality_met:
                logger.info(f"{OFF}Skipping {track_title} as it doesn't meet quality requirement")
                return
                
            track_attr = self._get_track_attr(
                track_meta, track_title, bit_depth, sampling_rate, file_format
            )
            
            self._determine_formats(album_meta=track_meta.get("album", {}), album_attr=None, tracks_meta=[track_meta],
                                    track_attr=track_attr, is_track=True, file_format=file_format, settings=self.settings)
            
            legacy_flag = getattr(self.settings, 'legacy_charmap', False) if hasattr(self, 'settings') else False
            dirn = process_folder_format_with_subdirs(self.folder_format, track_attr, self.path, legacy_charmap=legacy_flag)
            os.makedirs(dirn, exist_ok=True)

            # Mesmo caso do download_release(): _get_extra() e' bloqueante,
            # offload por consistencia.
            loop = asyncio.get_event_loop()

            if getattr(self, 'is_playlist', False) and not getattr(self, 'playlist_as_albums', False):
                logger.info(f"{OFF}Skipping standard cover save to keep playlist folder clean")
            elif self.settings.no_cover:
                logger.info(f"{OFF}Skipping cover")
            else:
                await loop.run_in_executor(
                    None,
                    lambda: _get_extra(track_meta["album"]["image"]["large"], dirn, art_size=self.settings.saved_art_size, session=self.http_session),
                )

            if self.settings.embed_art:
                embed_path = os.path.join(dirn, EMB_COVER_NAME)
                if os.path.exists(embed_path):
                    try:
                        os.remove(embed_path)
                    except OSError:
                        pass
                
                await loop.run_in_executor(
                    None,
                    lambda: _get_extra(track_meta["album"]["image"]["large"], dirn, extra=EMB_COVER_NAME,
                                        art_size=self.settings.embedded_art_size, session=self.http_session),
                )
            else:
                logger.info(f"{OFF}Skipping embedded art")
                
            is_mp3 = True if int(self.quality) == 5 else False
            
            await self._download_and_tag(
                dirn,
                1,
                parse,
                track_meta,
                track_meta,
                True,
                is_mp3,
                False,
                is_parallel=False
            )
            
            _clean_embed_art(dirn, self.settings)
            
            db_artist = track_attr.get("artist", "Unknown")
            db_album = track_attr.get("album", "Unknown")
            
            handle_download_id(db_path=self.download_db, item_id=self.item_id, add_id=True, media_type="track",
                               quality=self.quality, file_format=file_format, quality_met=quality_met,
                               bit_depth=bit_depth, sampling_rate=sampling_rate, saved_path=dirn,
                               url=url, release_date=release_date, artist=db_artist, album=db_album)
        else:
            logger.info(f"{OFF}Demo. Skipping")
        logger.info(f"{GREEN}Completed{OFF}")

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
        is_parallel=False
    ) -> bool:
        """Coordinates the actual downloading, fallback mechanisms, and metadata tagging."""
        extension = ".mp3" if is_mp3 else ".flac"
        # Um unico loop reference, reutilizado em todos os run_in_executor()
        # dessa funcao -- ver comentario mais abaixo sobre por que essas
        # chamadas precisam ser offloaded pra nao travar o event loop
        # inteiro (e com isso, travar TODAS as outras faixas "paralelas").
        loop = asyncio.get_event_loop()

        track_artist = _safe_get(track_metadata, "performer", "name")
        filename_attr = self._get_filename_attr(
            track_artist,
            track_metadata,
            album_or_track_metadata.get("album", {}) if is_track else album_or_track_metadata
        )

        legacy_flag = getattr(self.settings, 'legacy_charmap', False) if hasattr(self, 'settings') else False
        
        if getattr(self, 'is_playlist', False) and not getattr(self, 'playlist_as_albums', False):
            clean_playlist_format = "{artist} - {track_title}"
            formatted_path = sanitize_filename(clean_filename(clean_playlist_format.format(**filename_attr), legacy_charmap=legacy_flag), replacement_text="_")
        elif multiple and self.settings.multiple_disc_one_dir:
            formatted_path = sanitize_filename(clean_filename(self.settings.multiple_disc_track_format.format(**filename_attr), legacy_charmap=legacy_flag), replacement_text="_")
        else:
            base_formatted = sanitize_filename(clean_filename(self.track_format.format(**filename_attr), legacy_charmap=legacy_flag), replacement_text="_")
            total_discs = album_or_track_metadata.get('media_count', 1)
            if multiple and total_discs > 1:
                try: d_num = int(multiple) if not isinstance(multiple, bool) else 1
                except (ValueError, TypeError): d_num = 1
                disc_folder = f"{self.settings.multiple_disc_prefix} {d_num:02}"
                formatted_path = os.path.join(disc_folder, base_formatted)
            else:
                formatted_path = base_formatted
            
        max_len = 180
        if len(formatted_path) > max_len:
            start_part = formatted_path[:110].rstrip(' ."-_\'')
            end_part = formatted_path[-60:].lstrip(' ."-_\'')
            formatted_path = f"{start_part}...{end_part}"
            
        final_file = os.path.join(root_dir, formatted_path) + extension

        if os.path.exists(final_file):
            safe_print(f"{CYAN}[*] Skipping: {os.path.basename(final_file)} (Already exists){OFF}")
            return True

        if abort_event.is_set():
            return False

        await asyncio.sleep(1)
        try:
            url = track_url_dict["url"]
        except KeyError:
            logger.info(f"{OFF}Track not available for download")
            return False

        total_discs = album_or_track_metadata.get('media_count', 1)
        if multiple and total_discs > 1 and (not self.settings.multiple_disc_one_dir):
            try:
                d_num = int(multiple) if not isinstance(multiple, bool) else 1
            except (ValueError, TypeError): 
                d_num = 1
            root_dir = os.path.join(root_dir, f"{self.settings.multiple_disc_prefix} {d_num:02}")
        
        if not os.path.exists(root_dir):
            os.makedirs(root_dir, exist_ok=True)

        filename = os.path.join(root_dir, f"~tmp_{tmp_count:02}.tmp")
        track_title = track_metadata.get("title")
        track_no = str(track_metadata.get('track_number', 0)).zfill(2)
        desc = f"{track_no}. {track_title}"

        FALLBACK_TIERS = [27, 7, 6, 5]
        TIER_NAMES = {27: "24-bit/>96kHz", 7: "24-bit/96kHz", 6: "16-bit/44.1kHz (CD)", 5: "MP3 320kbps"}
        
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
                safe_print(f"{YELLOW}[!] Automatic downgrade: Attempting to save in {TIER_NAMES[attempt_fmt]}...{OFF}")

            async def get_fresh_url(fmt=attempt_fmt, force_segments=False):
                return await self.client.get_track_url(track_metadata["id"], fmt_id=fmt, force_segments=force_segments)

            try:
                fresh_track_dict = await get_fresh_url(force_segments=False)
                
                if "url" in fresh_track_dict:
                    try:
                        # tqdm_download() e' 100% sincrona/bloqueante (requests
                        # + iter_content em loop). Chamada direta aqui
                        # travaria o event loop inteiro pela duracao do
                        # download -- ou seja, TODAS as outras faixas
                        # "paralelas" ficariam paradas esperando essa
                        # terminar, mesmo com max_workers > 1. run_in_executor
                        # joga isso numa thread separada, liberando o event
                        # loop pra de fato processar as outras faixas.
                        await loop.run_in_executor(
                            None,
                            lambda: tqdm_download(fresh_track_dict["url"], filename, desc, is_parallel=is_parallel, session=self.http_session),
                        )
                        success = True
                        final_fmt = attempt_fmt
                        break
                    except Exception as e:
                        if abort_event.is_set(): return False
                        safe_print(f"{YELLOW}[!] Akamai block detected. Activating fallback segmented download...{OFF}")
                        fresh_track_dict = await get_fresh_url(force_segments=True)
                
                if "url_template" in fresh_track_dict:
                    # Mesmo motivo: tqdm_download_segments() e' sincrona (usa
                    # seu proprio ThreadPoolExecutor por dentro, mas a funcao
                    # em si bloqueia ate' terminar) -- precisa rodar fora do
                    # event loop tambem.
                    await loop.run_in_executor(
                        None,
                        lambda: tqdm_download_segments(
                            fresh_track_dict, filename, desc, is_parallel=is_parallel,
                            session=self.http_session,
                            segment_workers=getattr(self.settings, 'segment_workers', None),
                        ),
                    )
                    success = True
                    final_fmt = attempt_fmt
                    break
                elif not success:
                    raise Exception("No valid format returned by the server.")

            except Exception as e:
                pass

        if not success and not abort_event.is_set():
            safe_print(f"\n{RED}[!] TRACK {track_no} DEFINITIVELY DISCARDED AFTER ALL DOWNGRADES.{OFF}")
            safe_print(f"{YELLOW}[!] Skipping to the next track...{OFF}\n")
            return False
            
        if abort_event.is_set():
            return False

        is_mp3 = True if final_fmt == 5 else False
        extension = ".mp3" if is_mp3 else ".flac"

        tag_function = metadata.tag_mp3 if is_mp3 else metadata.tag_flac
        try:
            # tag_function() faz I/O de arquivo sincrono (mutagen) -- rapido
            # na maioria dos casos, mas ainda bloqueante; offload por
            # consistencia com o resto do pipeline e pra nao segurar o
            # event loop durante escrita em disco lento (ex: iCloud Drive
            # sincronizando no A-Shell).
            await loop.run_in_executor(
                None,
                lambda: tag_function(
                    filename, root_dir, final_file, track_metadata,
                    album_or_track_metadata, is_track, self.embed_art, settings=self.settings,
                ),
            )
        except Exception as e:
            safe_print(f"{RED}[!] Error tagging: {e}{OFF}")

        if getattr(self, 'fetch_lyrics', False) and hasattr(self, 'lyrics_engine') and not abort_event.is_set():
            album_artist = _safe_get(track_metadata, "album", "artist", "name")
            performer_name = _safe_get(track_metadata, "performer", "name") or _safe_get(track_metadata, "artist", "name", default="Unknown")
            search_artist = performer_name if album_artist in [None, "Various Artists"] else album_artist

            search_album = _safe_get(track_metadata, "album", "title", default="")

            # Busca a letra original e, se configurado, a traducao -- cada uma
            # exige uma chamada separada a track/lyricsUrl (sem/com o parametro
            # "language"), pois o endpoint devolve apenas UM bloco por chamada
            # ("original" ou "translation", nunca os dois juntos). Ver
            # _fetch_qobuz_lyrics_json() para detalhes do formato.
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
                    # extract_qobuz_lyrics() espera o bloco "translation" em
                    # si (com "lines" no nivel raiz), nao o JSON inteiro.
                    qobuz_translation_response = translation_json.get("translation")

            # Aviso discreto (sem despejar erro/URL) para os dois casos em que a traducao nao vai ser mesclada, mas que NAO sao um problema: 
            
            # 1. a musica ja esta no idioma de traducao pedido (ex: letra original em "pt" e voce pediu traducao para "pt")
            # 2. a musica esta em outro idioma, mas o Qobuz ainda nao tem essa traducao cadastrada pra essa faixa
            # So mostramos isso se de fato tentamos buscar traducao (translation_lang setado) e conseguimos a letra original (pra nao confundir com o caso de "letra nenhuma encontrada").
            translation_note = None
            if translation_lang and not qobuz_translation_response and isinstance(qobuz_lyrics_response, dict):
                original_block = qobuz_lyrics_response.get("original")
                original_lang = original_block.get("lang") if isinstance(original_block, dict) else None
                if original_lang:
                    if original_lang.lower() == translation_lang.lower():
                        translation_note = (
                            f"    ℹ️  Lyrics already in {translation_lang.upper()} -- no translation needed."
                        )
                    else:
                        translation_note = (
                            f"    ℹ️  No {translation_lang.upper()} translation available on Qobuz yet for this track."
                        )

            def _inject_lyrics_and_print():
                # O print_lock precisa ser adquirido AQUI DENTRO (rodando
                # numa worker thread via run_in_executor), nao no thread do
                # event loop. Se fosse `with print_lock:` la' fora, envolvendo
                # o `await run_in_executor(...)`, o lock em si nao trava o
                # event loop -- mas se DUAS tasks tentassem essa aquisicao ao
                # mesmo tempo, a segunda faria um `with print_lock:` sincrono
                # dentro da coroutine, o que TRAVARIA o event loop inteiro
                # ate' a primeira task soltar o lock. Adquirindo dentro da
                # worker thread, um eventual bloqueio so' segura essa worker
                # thread (o event loop continua livre pra processar as
                # outras faixas normalmente).
                with print_lock:
                    self.lyrics_engine.fetch_and_inject(
                        file_path=final_file,
                        artist=search_artist,
                        track=track_title,
                        album=search_album,
                        save_lrc=not self.no_lrc_files,
                        embed_lyrics=getattr(self.settings, 'embed_lyrics', True),
                        qobuz_lyrics_response=qobuz_lyrics_response,
                        qobuz_translation_response=qobuz_translation_response,
                    )
                    if translation_note:
                        print(f"{CYAN}{translation_note}{OFF}")

            await loop.run_in_executor(None, _inject_lyrics_and_print)

        delay_time = getattr(self.settings, 'delay', 0)
        if delay_time == 0 and '--delay' in sys.argv:
            try: delay_time = int(sys.argv[sys.argv.index('--delay') + 1])
            except: pass
            
        if delay_time > 0 and not abort_event.is_set():
            safe_print(f"{YELLOW}[*] Sleeping for {delay_time} seconds to prevent rate limiting...{OFF}")
            await asyncio.sleep(delay_time)
            
        return True

    @staticmethod
    def _get_filename_attr(track_artist, track_metadata: dict, album_metadata: dict): 
        def _flatten_artists(artist_data):
            if isinstance(artist_data, list): return ", ".join(artist_data)
            return str(artist_data) if artist_data else ""
           
        album_artist_raw = get_album_artist(album_metadata)
        album_artist_str = _flatten_artists(album_artist_raw) if album_artist_raw else track_artist

        return {
            "artist": track_artist,
            "albumartist": album_artist_str,
            "tracktitle": _get_title(track_metadata),
            "album_title":  _get_title(album_metadata),
            "album_title_base": album_metadata.get("title"),
            "album_artist": album_artist_str,
            "track_id": track_metadata.get("id"),
            "track_artist": track_artist,
            "track_composer": _safe_get(track_metadata,"composer", "name"),
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
            if isinstance(artist_data, list): return ", ".join(artist_data)
            return str(artist_data) if artist_data else ""
            
        album_artist_raw = get_album_artist(album_meta)
        album_artist_str = _flatten_artists(album_artist_raw) if album_artist_raw else _safe_get(meta, "performer", "name")

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
            "label": re.sub(r'\s*[\;\/]\s*|\s+\-\s+',' ／ ', ' '.join(meta.get("label",{}).get("name", "").split())).strip(),
            "copyright": meta.get("copyright", ""),
            "upc": meta.get("upc", ""),
            "barcode": meta.get("upc", ""),
            "release_date": meta.get("release_date_original", ""),
            "year": meta.get("release_date_original", "").split("-")[0],
            "media_type": meta.get("product_type", "").capitalize(),
            "format": file_format,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "quality_tag": "MP3" if str(file_format).upper() == "MP3" else (f"{file_format} {bit_depth}" if bit_depth else file_format),
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
            if isinstance(artist_data, list): return ", ".join(artist_data)
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
            "label": re.sub(r'\s*[\;\/]\s*|\s+\-\s+',' ∕ ', ' '.join(meta.get("label",{}).get("name", "").split())).strip(),
            "copyright": meta.get("copyright", ""),
            "upc": meta.get("upc", ""),
            "barcode": meta.get("upc", ""),
            "release_date": meta.get("release_date_original", ""),
            "year": meta.get("release_date_original", "").split("-")[0],
            "media_type": meta.get("product_type", "").capitalize(),
            "format": file_format,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
            "quality_tag": "MP3" if str(file_format).upper() == "MP3" else (f"{file_format} {bit_depth}" if bit_depth else file_format),
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
                raise NonStreamable("This release has no tracks available (possibly region-locked or removed)")

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
                if any(restriction.get("code") == QL_DOWNGRADE for restriction in restrictions):
                    quality_met = False

            actual_format = "MP3" if int(self.quality) == 5 else "FLAC"

            return (actual_format, quality_met, new_track_dict["bit_depth"], new_track_dict["sampling_rate"])
            
        except (KeyError, requests.exceptions.HTTPError, Exception):
            return ("Unknown", quality_met, None, None)

    def _determine_formats(self, album_meta, album_attr, tracks_meta, track_attr, is_track, file_format, settings: QobuzDLSettings):
        format_combinations = [
            (self._original_folder_format, self._original_track_format, self._original_multiple_disc_track_format),
            (settings.fallback_folder_format, self._original_track_format, self._original_multiple_disc_track_format),
            (settings.fallback_folder_format, DEFAULT_TRACK, DEFAULT_MULTIPLE_DISC_TRACK),
            (DEFAULT_FOLDER, DEFAULT_TRACK, DEFAULT_MULTIPLE_DISC_TRACK)
        ]

        media_count = album_meta.get("media_count", 1)
        is_multiple = True if media_count > 1 else False
        extension = ".flac" if file_format.lower() == "flac" else ".mp3"
        
        legacy_flag = getattr(settings, 'legacy_charmap', False) if settings else False

        for folder_fmt, track_fmt, multi_disc_fmt in format_combinations:
            folder_fmt, track_fmt = _clean_format_str(folder_fmt, track_fmt, file_format)
            valid_combination = True
            
            try:
                if is_track:
                    root_dir = process_folder_format_with_subdirs(folder_fmt, track_attr, legacy_charmap=legacy_flag)
                else:
                    root_dir = process_folder_format_with_subdirs(folder_fmt, album_attr, legacy_charmap=legacy_flag)

                for track_metadata in tracks_meta:
                    track_artist = _safe_get(track_metadata, "performer", "name")
                    filename_attr = self._get_filename_attr(track_artist, track_metadata, album_meta)

                    curr_root_dir = root_dir
                    if is_multiple and self.settings.multiple_disc_one_dir:
                        track_path = sanitize_filename(clean_filename(multi_disc_fmt.format(**filename_attr), legacy_charmap=legacy_flag), replacement_text="_")
                    else:
                        if is_multiple and not self.settings.multiple_disc_one_dir:
                            disc_dir = f"{self.settings.multiple_disc_prefix} {track_metadata['media_number']:02}"
                            curr_root_dir = os.path.join(root_dir, disc_dir)
                        
                        track_path = sanitize_filename(clean_filename(track_fmt.format(**filename_attr), legacy_charmap=legacy_flag), replacement_text="_")

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

    def _generate_tracklist(self, meta, dirn, album_title, file_format, bit_depth, sampling_rate):
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
        genre = metadata.LOCAL_GENRE_MAP.get(raw_genre, raw_genre) if raw_genre != "Unknown Genre" else raw_genre
        release_date = meta.get("release_date_original", "Unknown Date")
        
        try:
            with open(tracklist_path, "w", encoding="utf-8") as f:
                explicit_tag = " [E]" if meta.get("parental_warning") else ""
                
                f.write("=" * 70 + "\n")
                f.write(f"ALBUM      : {album_title}{explicit_tag}\n")
                if composer != "N/A": f.write(f"COMPOSER   : {composer}\n")
                f.write(f"MAIN ART.  : {artist_name}\n")
                f.write(f"LABEL      : {label}\n")
                f.write(f"GENRE      : {genre}\n")
                f.write(f"RELEASE    : {release_date}\n")
                f.write(f"QUALITY    : {file_format} ({bit_depth}-Bit / {sampling_rate} kHz)\n")
                f.write("=" * 70 + "\n\n")
                
                tracks = meta.get("tracks", {}).get("items", [])
                total_discs = max((track.get("media_number", 1) for track in tracks), default=1)
                current_disc = None 
                
                for track in tracks:
                    disc_num = track.get("media_number", 1)
                    if total_discs > 1 and disc_num != current_disc:
                        if current_disc is not None: f.write("\n")
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
                        for line in re.split(r'\r?\n|\s+-\s+', str(performers_raw)):
                            if line.strip(): f.write(f"    * {line.strip()}\n")
                    else:
                        t_artist = _safe_get(track, "performer", "name", default=artist_name)
                        f.write(f"    {t_artist}\n")
                    f.write("\n")
                
                description = meta.get("description")
                if description:
                    f.write("\n" + "=" * 70 + "\nALBUM REVIEW / NOTES\n" + "=" * 70 + "\n\n")
                    clean_desc = re.sub(r'<[^<]+>', '', re.sub(r'<br\s*/?>', '\n', str(description)))
                    for p in clean_desc.split('\n'):
                        if p.strip():
                            f.write(textwrap.fill(p.strip(), width=70) + "\n\n")

            safe_print(f"{GREEN}  L Completed: Digital Booklet.txt (Credits & Review){OFF}")
        except Exception as e:
            safe_print(f"{RED}[!] Error creating booklet: {e}{OFF}")

    async def _fetch_qobuz_lyrics_json(self, track_id, language=None):
        """
        Chama track/lyricsUrl (opcionalmente com o parametro 'language', que
        faz o endpoint devolver o bloco 'translation' em vez de 'original')
        e resolve a URL assinada (CloudFront) retornada, devolvendo o JSON
        de nivel 2 que de fato contem as linhas sincronizadas.

        Sem 'language': o JSON devolvido tem a chave "original".
        Com 'language="pt"' (por ex.): o JSON devolvido tem a chave
        "translation" (mesmo formato de "original" -- type/lang/lines),
        SEM a chave "original" junto.

        Retorna None se a faixa nao tiver letra/traducao cadastrada nesse
        idioma no Qobuz, ou se qualquer uma das duas chamadas falhar --
        nunca levanta excecao, para nao travar o download.

        NOTA: usamos aqui a MESMA construcao manual de request (params +
        _modern_sig + client.session.request) que validamos no script de
        teste (test_language_pt_only.py) -- e nao self.client.api_call(),
        porque nao temos garantia de que api_call() repassa um kwarg extra
        como "language" pro calculo da assinatura da mesma forma. Usar
        api_call() aqui seria assumir um comportamento que nunca testamos
        de fato; isso pode ter sido a causa de a traducao nao aparecer.
        """
        try:
            params = {"track_id": track_id}
            if language:
                params["language"] = language
            params["request_ts"] = int(time.time())
            params["request_sig"] = self.client._modern_sig(
                "track/lyricsUrl", params, self.client.sec
            )

            async with self.client.session.request(
                "get", self.client.base + "track/lyricsUrl", params=params
            ) as r:
                if r.status != 200:
                    logger.debug(
                        f"{OFF}track/lyricsUrl returned status {r.status}"
                        f"{f' for language={language}' if language else ''}"
                    )
                    return None
                lyrics_url_meta = await r.json()

            lyrics_json_url = None
            if isinstance(lyrics_url_meta, dict):
                # A chave observada na pratica e' "lyrics_url"/"url", mas
                # procuramos qualquer chave que contenha "url" para nao
                # quebrar se a Qobuz renomear o campo.
                lyrics_json_url = lyrics_url_meta.get("url") or lyrics_url_meta.get("lyrics_url")
                if not lyrics_json_url:
                    for k, v in lyrics_url_meta.items():
                        if "url" in k.lower() and isinstance(v, str):
                            lyrics_json_url = v
                            break

            if not lyrics_json_url:
                logger.debug(
                    f"{OFF}track/lyricsUrl did not return a lyrics URL"
                    f"{f' for language={language}' if language else ''}"
                )
                return None

            loop = asyncio.get_event_loop()
            resp = await loop.run_in_executor(
                None,
                lambda: self.http_session.get(lyrics_json_url, timeout=12),
            )

            # 403/404 aqui e' o caso NORMAL de "essa faixa nao tem letra (ou
            # traducao pro idioma pedido) cadastrada no Qobuz" -- o CloudFront
            # simplesmente nao tem esse arquivo assinado. Isso acontece o
            # tempo todo (ex: musica ja em pt pedindo traducao pt) e nao e'
            # um problema real, entao registramos em nivel debug (nao aparece
            # no terminal por padrao) em vez de estourar a URL assinada
            # inteira no console.
            if resp.status_code in (403, 404):
                logger.debug(
                    f"{OFF}No {'translation (' + language + ')' if language else 'lyrics'} "
                    f"registered on Qobuz for this track (status {resp.status_code})"
                )
                return None

            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            # Aqui sim e' um erro fora do esperado (timeout, rede, JSON
            # invalido, etc.) -- mantemos em info pra ficar visivel, mas sem
            # despejar URLs assinadas gigantes: so' o tipo/mensagem da excecao.
            logger.info(
                f"{OFF}Error fetching Qobuz "
                f"{f'lyrics translation ({language})' if language else 'lyrics'} "
                f"for this track: {type(e).__name__}: {e}"
            )
            return None

    def _append_lyrics_to_booklet(self, dirn, album_title):
        if abort_event.is_set(): return
        
        safe_title = sanitize_filename(album_title)
        tracklist_path = os.path.join(dirn, f"{safe_title} - Tracklist.txt")
        if not os.path.isfile(tracklist_path): return
            
        audio_files = []
        for root, _, files in os.walk(dirn):
            for f in files:
                if f.lower().endswith(('.flac', '.mp3')):
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
                clean_lyrics = re.sub(r'\[[a-zA-Z]+:.*?\]\n?|\[\d{2,}:\d{2}\.\d{2,3}\]', '', raw_lyrics)
                clean_lines = [line.strip() for line in clean_lyrics.splitlines() if line.strip() or (lyrics_text and lyrics_text[-1] != "")]
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
                safe_print(f"{CYAN}[+] Lyrics cleanly formatted and appended to Digital Booklet.{OFF}")
            except Exception as e:
                logger.error(f"{RED}[!] Error appending lyrics to booklet: {e}{OFF}")

def _get_description(item: dict, track_title, multiple=None):
    downloading_title = f"{track_title} [{item.get('bit_depth', '')}/{item.get('sampling_rate', '')}]"
    if multiple:
        downloading_title = f"[CD {multiple}] {downloading_title}"
    return downloading_title

def tqdm_download(url_or_callable, fname, track_name, is_parallel=False, session=None):
    if abort_event.is_set(): return
    # Antes: codigos ANSI hardcoded ("\033[92m" etc, variantes BRIGHT),
    # duplicando (e divergindo visualmente d)o que qobuz_dl.color ja expoe.
    # Agora reaproveita os mesmos GREEN/YELLOW/CYAN/OFF usados no resto do
    # arquivo -- passam pelo colorama (ja importado e inicializado em
    # color.py com init(autoreset=True)), que sabe traduzir ANSI pra
    # terminais que nao suportam nativamente (ex: cmd.exe antigo no
    # Windows). Mantidos os nomes curtos G/Y/C/O so' pra nao precisar
    # reescrever todas as f-strings abaixo.
    G, Y, C, O = GREEN, YELLOW, CYAN, OFF

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "audio/webm,audio/ogg,audio/wav,audio/*;q=0.9,*/*;q=0.5",
        "Connection": "keep-alive"
    }

    if not is_parallel:
        safe_print(f"{C}[+] In progress: {track_name}{O}")
        tqdm_desc = f" {G}Downloading{O}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"
    else:
        tqdm_desc = ""
        b_format = ""

    downloaded_size = 0
    total_size = 0
    max_retries = 5
    backoff_delays = [2, 4, 8, 16, 32] 

    # Reaproveita a sessao compartilhada (session=Download.http_session) se
    # fornecida -- mantem a conexao TCP/TLS viva entre tentativas de retry
    # E entre faixas diferentes, em vez de abrir uma sessao nova a cada
    # `with requests.Session() as s:` (como era antes, inclusive a CADA
    # tentativa de retry). Se nenhuma sessao for passada (uso standalone,
    # ex: chamado fora do fluxo normal do Download), cria uma local e a
    # fecha sozinha ao final -- nunca fecha uma sessao que nao criou.
    owns_session = session is None
    http = session or requests.Session()

    try:
        for attempt in range(max_retries):
            if abort_event.is_set(): return
            try:
                url = url_or_callable() if callable(url_or_callable) else url_or_callable

                if downloaded_size > 0:
                    headers['Range'] = f'bytes={downloaded_size}-'
                    mode = 'ab'
                else:
                    headers['Range'] = 'bytes=0-'
                    mode = 'wb'

                r = http.get(url, allow_redirects=True, stream=True, headers=headers, timeout=(10, 60))

                if r.status_code == 416: return
                if r.status_code not in [200, 206]:
                    raise Exception(f"Status Server: {r.status_code}")

                if total_size == 0:
                    total_size = downloaded_size + int(r.headers.get('content-length', 0))

                if is_parallel and downloaded_size == 0 and attempt == 0:
                    size_mb = total_size / (1024 * 1024)
                    safe_print(f"{C}[+] In progress: {track_name} [{size_mb:.1f} MB]{O}")

                with open(fname, mode) as file, tqdm(
                    total=total_size, unit="iB", unit_scale=True, unit_divisor=1024,
                    desc=tqdm_desc, initial=downloaded_size, bar_format=b_format, leave=False, disable=is_parallel
                ) as bar:
                    for data in r.iter_content(chunk_size=65536):
                        if abort_event.is_set(): return
                        if data:
                            size = file.write(data)
                            downloaded_size += size
                            if not is_parallel:
                                bar.update(size)

                if downloaded_size >= total_size:
                    safe_print(f"{G}  L Completed: {track_name}{O}")
                    return

            except Exception as e:
                if "404" in str(e):
                    if os.path.exists(fname): os.remove(fname)
                    raise Exception("HTTP 404: File not found on server.")

                if attempt < max_retries - 1:
                    wait = backoff_delays[attempt]
                    safe_print(f"\n{Y}[!] Server block. Retrying in {wait}s ({attempt+1}/{max_retries}) | Error details: {e}{O}")
                    time.sleep(wait)
                else:
                    if os.path.exists(fname): os.remove(fname)
                    raise Exception(f"Definitive timeout after {max_retries} attempts. Last error: {e}")

        if downloaded_size < total_size and not abort_event.is_set():
            if os.path.exists(fname): os.remove(fname)
            raise Exception("Incomplete download")
    finally:
        if owns_session:
            try:
                http.close()
            except Exception:
                pass

def _get_title(item_dict):
    item_title = item_dict.get("title")
    version = item_dict.get("version")
    if version:
        item_title = f"{item_title} ({version})" if version.lower() not in item_title.lower() else item_title
    return item_title


def _get_extra(item, dirn, extra="cover.jpg", art_size=None, og_quality=False, session=None):
    if abort_event.is_set(): return
    extra_file = os.path.join(dirn, extra)
    if os.path.isfile(extra_file):
        logger.info(f"{OFF}{extra} was already downloaded")
        return
        
    if og_quality: art_size = "org"
    if art_size in ["50", "100", "150", "300", "600", "max", "org"]:
        item = item.replace("_600.", f"_{art_size}.")
        
    try:
        tqdm_download(item, extra_file, extra, is_parallel=False, session=session)
    except Exception as e:
        safe_print(f"  {YELLOW}[!] Skipping cover art '{extra}': URL unreachable ({e}){OFF}")

def _clean_format_str(folder: str, track: str, file_format: str) -> Tuple[str, str]:
    final = []
    for i, fs in enumerate((folder, track)):
        if fs.endswith(".mp3"): fs = fs[:-4]
        elif fs.endswith(".flac"): fs = fs[:-5]
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

def tqdm_download_segments(track_url_dict, fname, track_name, is_parallel=False, session=None, segment_workers=None):
    if abort_event.is_set(): return
    # Mesmo motivo do tqdm_download() acima: reaproveita as cores de
    # qobuz_dl.color em vez de codigos ANSI hardcoded duplicados.
    G, C, O = GREEN, CYAN, OFF
    
    tmp_fname = fname + ".mp4"
    n_segments = track_url_dict["n_segments"]
    url_template = track_url_dict["url_template"]
    raw_key = track_url_dict["raw_key"]

    # Antes hardcoded em 8, igual em qualquer dispositivo. Agora vem de
    # settings.segment_workers (auto-detectado por os.cpu_count() la' em
    # settings.py, ou configuravel via --segment-workers/config.ini). Se
    # chamado sem passar segment_workers (uso standalone), cai num default
    # conservador de 4 em vez do 8 fixo de antes.
    workers = segment_workers if segment_workers else 4

    # Mesmo raciocinio de tqdm_download(): reaproveita a sessao compartilhada
    # se fornecida (varias requests HEAD/GET de segmentos se beneficiam MUITO
    # de connection pooling, ja que sao dezenas de pequenas requisicoes pra
    # o mesmo host). Fecha por conta propria apenas se ninguem passou uma.
    owns_session = session is None
    http = session or requests.Session()

    def get_seg_size(seg_num):
        if abort_event.is_set(): return 0
        url = url_template.replace("$SEGMENT$", str(seg_num))
        try:
            r = http.head(url, timeout=5)
            return int(r.headers.get("content-length", 0))
        except: return 0

    total_size = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures_size = [ex.submit(get_seg_size, i) for i in range(n_segments + 1)]
        for f in futures_size:
            # Antes: `while not f.done(): time.sleep(0.1)` -- um busy-poll
            # que acorda 10x/segundo so' pra checar se terminou, gastando
            # ciclos de CPU (e bateria, em mobile) a toa. f.result(timeout=)
            # usa uma espera de verdade (threading.Condition), sem spin, e
            # so' "acorda" quando o future termina ou o timeout expira --
            # aqui usado apenas pra permitir checar abort_event
            # periodicamente sem CPU-spinning.
            while True:
                if abort_event.is_set(): return
                try:
                    total_size += f.result(timeout=1.0)
                    break
                except concurrent.futures.TimeoutError:
                    continue

    if is_parallel:
        size_mb = total_size / (1024 * 1024)
        safe_print(f"{C}[+] In progress: {track_name} [{size_mb:.1f} MB]{O}")
        tqdm_desc, b_format = "", ""
    else:
        safe_print(f"{C}[+] In progress: {track_name}{O}")
        tqdm_desc = f" {G}Segmented Download{O}"
        b_format = "{desc}: {percentage:3.0f}%|{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}]"

    def fetch_segment_fluid(seg_num):
        if abort_event.is_set(): return bytearray()
        url = url_template.replace("$SEGMENT$", str(seg_num))
        r = http.get(url, stream=True, timeout=15)
        r.raise_for_status()
        seg_data = bytearray()
        
        for chunk in r.iter_content(chunk_size=65536):
            if abort_event.is_set(): return bytearray()
            seg_data.extend(chunk)
            if not is_parallel:
                bar.update(len(chunk)) 
        return seg_data

    try:
        with open(tmp_fname, "wb") as file, tqdm(
            total=total_size, unit="iB", unit_scale=True, unit_divisor=1024,
            desc=tqdm_desc, bar_format=b_format, leave=False, disable=is_parallel
        ) as bar:

            segment_uuid = None
            for i in range(2):
                seg_data = fetch_segment_fluid(i)
                if abort_event.is_set(): return
                if i == 1:
                    segment_uuid = _get_qobuz_segment_uuid(seg_data)
                    if segment_uuid is None:
                        raise ConnectionError(f"Cannot find segment UUID for {fname}")

                file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))

            if n_segments >= 2:
                with ThreadPoolExecutor(max_workers=workers) as executor:
                    futures_seg = [executor.submit(fetch_segment_fluid, i) for i in range(2, n_segments + 1)]
                    # IMPORTANTE: precisamos manter a ORDEM de submissao ao
                    # escrever no arquivo (segmentos de audio tem que ser
                    # concatenados na sequencia certa) -- entao nao da' pra
                    # usar concurrent.futures.as_completed() aqui (que
                    # retorna na ordem de conclusao, nao de submissao).
                    # Trocamos so' o busy-poll (`while not f.done():
                    # time.sleep(0.2)`) por f.result(timeout=1.0) num loop,
                    # que espera de forma eficiente (sem CPU-spinning) e
                    # ainda permite checar abort_event periodicamente.
                    for f in futures_seg:
                        while True:
                            if abort_event.is_set(): return
                            try:
                                seg_data = f.result(timeout=1.0)
                                break
                            except concurrent.futures.TimeoutError:
                                continue
                        if not abort_event.is_set():
                            file.write(_decrypt_qobuz_segment(seg_data, raw_key, segment_uuid))

        if abort_event.is_set(): return
        if not is_parallel:
            safe_print(f" {G}  > Assembling the final FLAC file...{O}")
            
        remux = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", tmp_fname, "-c:a", "copy", "-f", "flac", fname], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if remux.returncode != 0:
            raise ConnectionError(f"FFmpeg remux failed for {fname}")
        
        safe_print(f"{G}  L Completed: {track_name}{O}")

    finally:
        if os.path.isfile(tmp_fname):
            try: os.remove(tmp_fname)
            except OSError: pass
        if owns_session:
            try: http.close()
            except Exception: pass



def _get_qobuz_segment_uuid(segment_data):
    pos = 0
    while pos + 24 <= len(segment_data):
        size = int.from_bytes(segment_data[pos : pos + 4], "big")
        if size <= 0 or pos + size > len(segment_data): break

        if bytes(segment_data[pos + 4 : pos + 8]) == b"uuid":
            return bytes(segment_data[pos + 8 : pos + 24])
        pos += size
    return None


def _decrypt_qobuz_segment(segment_data, raw_key, segment_uuid):
    if segment_uuid is None: return bytes(segment_data)

    buf = bytearray(segment_data)
    pos = 0
    while pos + 8 <= len(buf):
        size = int.from_bytes(buf[pos : pos + 4], "big")
        if size <= 0 or pos + size > len(buf): break

        if bytes(buf[pos + 4 : pos + 8]) == b"uuid" and bytes(buf[pos + 8 : pos + 24]) == segment_uuid:
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
                    counter = bytes(buf[pointer : pointer + counter_len]) + (b"\x00" * (16 - counter_len))
                    decryptor = Cipher(algorithms.AES(raw_key), modes.CTR(counter)).decryptor()
                    buf[frame_start:data_end] = decryptor.update(bytes(buf[frame_start:data_end])) + decryptor.finalize()
                pointer += counter_len
        pos += size
    return bytes(buf)

def _download_goodies(album_meta, dirn, session=None):
    if abort_event.is_set(): return
    try:
        for goody in album_meta.get("goodies", []):
            if abort_event.is_set(): break
            if not goody.get("url"): continue
            goody_name = sanitize_filename(clean_filename(f'{album_meta.get("title")} ({goody.get("id")}).pdf'))
            _get_extra(goody.get("url"), dirn, extra=goody_name, session=session)
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