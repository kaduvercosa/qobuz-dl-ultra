import logging
import os
import sys
import time
import asyncio
import shutil

import requests
from pathvalidate import sanitize_filename

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import Window, ScrollOffsets
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.styles import Style
    from prompt_toolkit import PromptSession
except ImportError:
    sys.exit(
        "Erro: Por favor, instale o prompt_toolkit executando: pip install prompt_toolkit"
    )

from qobuz_dl.bundle import Bundle
from qobuz_dl import downloader, qopy
from qobuz_dl.color import CYAN, OFF, RED, YELLOW, RESET
from qobuz_dl.exceptions import NonStreamable
from qobuz_dl.db import create_db, handle_download_id
from qobuz_dl.utils import (
    get_url_info,
    make_m3u,
    smart_discography_filter,
    format_duration,
    create_and_return_dir,
)
from qobuz_dl.settings import QobuzDLSettings

# --- UI STYLE FOR PROMPT_TOOLKIT (100% CONTRAST FIX) ---
pt_style = Style.from_dict(
    {
        "title": "ansicyan bold",
        "pointer": "ansiyellow bold",
        "checkbox": "ansigreen",
        "hovered": "bg:#cccccc fg:#000000 bold",  # Fundo Cinza Claro e Texto Preto! Impossível de não ver no claro ou escuro.
        "meta": "",
        "highlight": "ansicyan bold",
        "footer": "ansiyellow",
    }
)


def _align_text(text, width):
    """Truncates or pads text for table alignment."""
    text = str(text)
    if len(text) > width:
        return text[: width - 3] + "..."
    return text.ljust(width)


# --- PROMPT_TOOLKIT CUSTOM APPLICATION COM ROLAGEM ESTÁVEL ---
async def _tui_select(title, options_dicts, is_multi=False, item_category="album"):
    """
    Motor interativo customizado usando Prompt_Toolkit.
    Garante rolagem suave sem perder o foco ou o destaque no final da lista.
    """
    bindings = KeyBindings()
    selected_indices = set()
    cursor_pos = 0

    @bindings.add("up")
    def _(event):
        nonlocal cursor_pos
        cursor_pos = max(0, cursor_pos - 1)

    @bindings.add("down")
    def _(event):
        nonlocal cursor_pos
        cursor_pos = min(len(options_dicts) - 1, cursor_pos + 1)

    if is_multi:

        @bindings.add("space")
        def _(event):
            if cursor_pos in selected_indices:
                selected_indices.remove(cursor_pos)
            else:
                selected_indices.add(cursor_pos)

        @bindings.add("t")
        def _(event):
            if len(selected_indices) == len(options_dicts):
                selected_indices.clear()
            else:
                selected_indices.update(range(len(options_dicts)))

    @bindings.add("enter")
    def _(event):
        if is_multi:
            if not selected_indices:
                selected_indices.add(cursor_pos)
            event.app.exit(
                result=[(options_dicts[i], i) for i in sorted(list(selected_indices))]
            )
        else:
            event.app.exit(result=(options_dicts[cursor_pos], cursor_pos))

    @bindings.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    def get_text():
        columns, lines = shutil.get_terminal_size((80, 24))
        is_table = columns >= 105
        is_table_simple = columns >= 75

        res = []
        res.append(("class:title", f"=== {title} ===\n\n"))

        if item_category == "album" and is_table:
            res.append(
                (
                    "class:meta",
                    f"       {'ARTISTA'.ljust(20)} | {'ÁLBUM'.ljust(35)} | {'TIPO'.ljust(8)} | {'ANO'.ljust(4)} | FAIXAS | DURAÇÃO | QUALIDADE\n",
                )
            )
            res.append(("class:meta", f"       {'-' * 110}\n"))
        elif item_category == "track" and is_table:
            res.append(
                (
                    "class:meta",
                    f"       {'ARTISTA'.ljust(20)} | {'FAIXA'.ljust(35)} | {'ÁLBUM'.ljust(25)} | DURAÇÃO | QUALIDADE\n",
                )
            )
            res.append(("class:meta", f"       {'-' * 100}\n"))
        elif item_category == "playlist" and is_table_simple:
            res.append(
                (
                    "class:meta",
                    f"       {'NOME DA PLAYLIST'.ljust(40)} | {'CRIADOR'.ljust(20)} | FAIXAS | DURAÇÃO\n",
                )
            )
            res.append(("class:meta", f"       {'-' * 85}\n"))
        elif item_category == "artist" and is_table_simple:
            res.append(
                ("class:meta", f"       {'NOME DO ARTISTA'.ljust(50)} | LANÇAMENTOS\n")
            )
            res.append(("class:meta", f"       {'-' * 65}\n"))

        for i, opt in enumerate(options_dicts):
            hovered = i == cursor_pos
            checked = i in selected_indices

            style = "class:hovered" if hovered else ""
            title_style = "class:hovered" if hovered else "class:highlight"

            if hovered:
                # Fragmento invisível ("zero-width") que diz ao prompt_toolkit
                # onde fica o "cursor" lógico da lista neste render. É o
                # próprio motor da Window que usa essa marca para rolar
                # automaticamente e manter o item em destaque visível,
                # respeitando o ScrollOffsets(top=1, bottom=1) lá embaixo.
                # Diferente do scroll manual antigo (cursor_pos - 1), isso
                # funciona corretamente com cabeçalho de tabela, itens que
                # ocupam várias linhas (modo cartão) e quebra automática de
                # linha em telas estreitas (iPhone/iSH) -- porque ele opera
                # sobre as linhas já renderizadas, não sobre o índice do item.
                res.append(("[SetCursorPosition]", ""))

            ptr = ">" if hovered else " "
            chk = "[x]" if checked else "[ ]"
            if not is_multi:
                chk = ""

            prefix = f" {ptr} {chk} " if is_multi else f" {ptr} "
            res.append((style, prefix))

            if isinstance(opt, str):
                res.append((style, f"{opt}\n"))
                continue

            meta = opt.get("meta", {})

            if item_category == "album":
                if is_table:
                    art = _align_text(meta.get("artist", ""), 20)
                    tit = _align_text(meta.get("title", ""), 35)
                    typ = _align_text(meta.get("type", ""), 8)
                    yr = _align_text(meta.get("year", ""), 4)
                    fx = str(meta.get("tracks_count", "")).ljust(6)
                    dur = str(meta.get("duration", "")).ljust(7)
                    ql = meta.get("quality", "")
                    res.append(
                        (style, f"{art} | {tit} | {typ} | {yr} | {fx} | {dur} | {ql}\n")
                    )
                else:
                    res.append((title_style, f"{meta.get('title', '')}\n"))
                    res.append(
                        (
                            style,
                            f"       👤 Artista: {meta.get('artist', '')}  |  💿 {meta.get('type', '')}  |  📅 {meta.get('year', '')}\n",
                        )
                    )
                    res.append(
                        (
                            style,
                            f"       🎧 {meta.get('quality', '')}  |  🎶 {meta.get('tracks_count', 0)} faixas  |  ⏱️ {meta.get('duration', '--:--')}\n",
                        )
                    )
                    gnr = meta.get("genre", "")
                    lbl = meta.get("label", "")
                    if gnr or lbl:
                        res.append((style, f"       🎵 {gnr}  |  🏷️ {lbl}\n"))
                    res.append((style, f"       {'-'*30}\n"))

            elif item_category == "track":
                if is_table:
                    art = _align_text(meta.get("artist", ""), 20)
                    tit = _align_text(meta.get("title", ""), 35)
                    alb = _align_text(meta.get("album", ""), 25)
                    dur = str(meta.get("duration", "")).ljust(7)
                    ql = meta.get("quality", "")
                    res.append((style, f"{art} | {tit} | {alb} | {dur} | {ql}\n"))
                else:
                    res.append((title_style, f"🎶 {meta.get('title', '')}\n"))
                    res.append(
                        (style, f"       👤 Artista: {meta.get('artist', '')}\n")
                    )
                    res.append(
                        (
                            style,
                            f"       💿 Álbum: {meta.get('album', '')}  |  ⏱️ Duração: {meta.get('duration', '--:--')}\n",
                        )
                    )
                    res.append(
                        (style, f"       🎧 Qualidade: {meta.get('quality', '')}\n")
                    )
                    res.append((style, f"       {'-'*30}\n"))

            elif item_category == "playlist":
                if is_table_simple:
                    n = _align_text(meta.get("name", ""), 40)
                    o = _align_text(meta.get("owner", ""), 20)
                    c = str(meta.get("count", "")).ljust(6)
                    dur = str(meta.get("duration", ""))
                    res.append((style, f"{n} | {o} | {c} | {dur}\n"))
                else:
                    res.append((title_style, f"📋 {meta.get('name', '')}\n"))
                    res.append((style, f"       👤 Criador: {meta.get('owner', '')}\n"))
                    res.append(
                        (
                            style,
                            f"       🎶 Total de faixas: {meta.get('count', 0)}  |  ⏱️ Duração total: {meta.get('duration', '--:--')}\n",
                        )
                    )
                    res.append((style, f"       {'-'*30}\n"))

            elif item_category == "artist":
                if is_table_simple:
                    n = _align_text(meta.get("name", ""), 50)
                    c = meta.get("count", "")
                    res.append((style, f"{n} | {c} álbuns\n"))
                else:
                    res.append((title_style, f"🎤 {meta.get('name', '')}\n"))
                    res.append(
                        (
                            style,
                            f"       📦 Lançamentos listados: {meta.get('count', '')}\n",
                        )
                    )
                    res.append((style, f"       {'-'*30}\n"))
            elif item_category == "filter":
                res.append((style, f"{opt}\n"))

        res.append(("", "\n"))
        if is_multi:
            res.append(
                ("class:checkbox", f" ✓ Selecionados: {len(selected_indices)}\n")
            )
            res.append(
                (
                    "class:footer",
                    f" [↑ ↓] Mover   [Espaço] Selecionar   [t] Selecionar Todos   [Enter] Confirmar",
                )
            )
        else:
            res.append(("class:footer", f" [↑ ↓] Mover   [Enter] Confirmar"))

        return res

    window = Window(
        # focusable=True garante que esta Window seja o alvo do foco do
        # Application e, por consequência, que a rolagem automática baseada
        # no [SetCursorPosition] acima realmente seja aplicada.
        content=FormattedTextControl(text=get_text, focusable=True),
        scroll_offsets=ScrollOffsets(top=1, bottom=1),
        wrap_lines=True,
    )

    layout = Layout(window)
    app = Application(
        layout=layout, key_bindings=bindings, full_screen=True, style=pt_style
    )

    # A rolagem agora é 100% responsabilidade do prompt_toolkit (via
    # [SetCursorPosition] + ScrollOffsets), então não precisamos mais de uma
    # task em background recalculando `window.vertical_scroll` a cada 30ms.
    # Essa task antiga era a origem do bug: o cálculo `cursor_pos - 1`
    # ignorava o cabeçalho da lista, os itens de várias linhas (modo cartão)
    # e a quebra automática de linha em telas estreitas -- então, conforme
    # você descia, a rolagem real ficava cada vez mais dessincronizada da
    # posição do item, até sumir o destaque e não sobrar nada visível.
    res = await app.run_async()
    if isinstance(res, Exception):
        raise res
    return res


# ----------------------------------

WEB_URL = "https://play.qobuz.com/"
ARTISTS_SELECTOR = "td.chartlist-artist > a"
TITLE_SELECTOR = "td.chartlist-name > a"
QUALITIES = {
    5: "5 - MP3",
    6: "6 - 16 bit, 44.1kHz",
    7: "7 - 24 bit, <96kHz",
    27: "27 - 24 bit, >96kHz",
}

logger = logging.getLogger(__name__)


class QobuzDL:
    """
    The main orchestrator class for Qobuz-DL Ultimate Edition.
    """

    def __init__(
        self,
        directory="QobuzDownloads",
        quality=6,
        embed_art=False,
        lucky_limit=1,
        lucky_type="album",
        interactive_limit=20,
        ignore_singles_eps=False,
        no_m3u_for_playlists=False,
        quality_fallback=True,
        cover_og_quality=False,
        no_cover=False,
        downloads_db=None,
        folder_format="{artist} - {album} ({year}) [{bit_depth}B-"
        "{sampling_rate}kHz]",
        track_format="{track_number} - {track_title}",
        smart_discography=False,
        fetch_lyrics=False,
        no_lrc_files=False,
        genius_token=None,
        force_english=True,
        no_credits=False,
        settings: QobuzDLSettings = None,
        booklet_only: bool = False,
        blacklist=None,
        playlist_as_albums: bool = False,
    ):
        self.directory = create_and_return_dir(directory)
        self.quality = quality
        self.embed_art = embed_art
        self.lucky_limit = lucky_limit
        self.lucky_type = lucky_type
        self.interactive_limit = interactive_limit
        self.ignore_singles_eps = ignore_singles_eps
        self.no_m3u_for_playlists = no_m3u_for_playlists
        self.quality_fallback = quality_fallback
        self.cover_og_quality = cover_og_quality
        self.no_cover = no_cover
        self.downloads_db = create_db(downloads_db) if downloads_db else None
        self.folder_format = folder_format
        self.track_format = track_format
        self.smart_discography = smart_discography
        self.fetch_lyrics = fetch_lyrics
        self.no_lrc_files = no_lrc_files
        self.genius_token = genius_token
        self.force_english = force_english
        self.no_credits = no_credits
        self.settings = settings or QobuzDLSettings()
        self.booklet_only = booklet_only
        self.playlist_as_albums = playlist_as_albums

        self.blacklist_patterns = []
        if blacklist and os.path.isfile(blacklist):
            try:
                with open(blacklist, "r", encoding="utf-8") as f:
                    self.blacklist_patterns = [
                        line.strip().lower()
                        for line in f
                        if line.strip() and not line.startswith("#")
                    ]
                logger.info(
                    f"{YELLOW}[*] Blacklist loaded: {len(self.blacklist_patterns)} patterns active.{OFF}"
                )
            except Exception as e:
                logger.error(f"{RED}[!] Failed to load blacklist: {e}{OFF}")

    async def initialize_client(self, email, pwd, app_id, secrets):
        self.client = await qopy.Client.create(
            email,
            pwd,
            app_id,
            secrets,
            self.settings.user_auth_token,
            force_english=self.force_english,
        )
        logger.info(f"{YELLOW}Set max quality: {QUALITIES[int(self.quality)]}\n")

    def get_tokens(self):
        bundle = Bundle()
        self.app_id = bundle.get_app_id()
        self.secrets = [secret for secret in bundle.get_secrets().values() if secret]

    async def download_from_id(
        self,
        item_id,
        album=True,
        alt_path=None,
        is_playlist=False,
        playlist_index=None,
        is_parallel=False,
        position_pool=None,
    ):
        if handle_download_id(
            self.downloads_db, item_id, add_id=False, quality=self.quality
        ):
            logger.info(
                f"{OFF}This release ID ({item_id}) was already downloaded "
                "according to the local database.\nUse the '--no-db' flag "
                "to bypass this."
            )
            return
        try:
            dloader = downloader.Download(
                self.client,
                item_id,
                alt_path or self.directory,
                int(self.quality),
                self.embed_art,
                self.ignore_singles_eps,
                self.quality_fallback,
                self.cover_og_quality,
                self.no_cover,
                self.folder_format,
                self.track_format,
                self.fetch_lyrics,
                self.no_lrc_files,
                self.genius_token,
                self.no_credits,
                self.settings,
                self.downloads_db,
                is_playlist=is_playlist,
                playlist_track_number=playlist_index,
                booklet_only=self.booklet_only,
                playlist_as_albums=self.playlist_as_albums,
            )
            await dloader.download_id_by_type(
                not album, is_parallel=is_parallel, position_pool=position_pool
            )
        except (requests.exceptions.RequestException, NonStreamable) as e:
            logger.error(f"{RED}Error getting release: {e}. Skipping...")

        if getattr(self, "delay", 0) > 0:
            logger.info(
                f"{YELLOW}[*] Sleeping for {self.delay} seconds to prevent rate limiting...{OFF}"
            )
            await asyncio.sleep(self.delay)

    async def handle_url(self, url):
        possibles = {
            "playlist": {
                "func": self.client.get_plist_meta,
                "iterable_key": "tracks",
            },
            "artist": {
                "func": self.client.get_artist_meta,
                "iterable_key": "albums",
            },
            "label": {
                "func": self.client.get_label_meta,
                "iterable_key": "albums",
            },
            "album": {"album": True, "func": None, "iterable_key": None},
            "track": {"album": False, "func": None, "iterable_key": None},
        }
        try:
            url_type, item_id = get_url_info(url)
            type_dict = possibles[url_type]
        except (KeyError, IndexError):
            logger.info(
                f'{RED}Invalid url: "{url}". Use urls from ' "https://play.qobuz.com!"
            )
            return

        if type_dict["func"]:
            content = []
            async for chunk in type_dict["func"](item_id):
                content.append(chunk)

            if not content:
                logger.warning(
                    f"{YELLOW}[!] Skipped URL: Content empty or unavailable (Geo-blocked/Removed). URL: {url}{OFF}"
                )
                return
            content_name = content[0]["name"]
            logger.info(
                f"{YELLOW}Downloading all the music from {content_name} "
                f"({url_type})!"
            )
            new_path = create_and_return_dir(
                os.path.join(self.directory, sanitize_filename(content_name))
            )

            if self.smart_discography and url_type == "artist":
                items = smart_discography_filter(
                    content,
                    save_space=True,
                    skip_extras=True,
                )
            else:
                items = []
                for chunk in content:
                    batch = chunk.get(type_dict["iterable_key"], {}).get("items", [])
                    items.extend(batch)

            if getattr(self, "_is_interactive_session", False) and url_type == "artist":
                options = ["Album", "EP", "Single", "Live", "Compilation"]
                title_text = f"Encontrados {len(items)} lançamentos para {content_name}. Filtre por tipo:"

                sel_res = await _tui_select(
                    title_text, options, is_multi=True, item_category="filter"
                )
                selected_types_raw = sel_res if sel_res else []

                if selected_types_raw:
                    self.allowed_release_types = [
                        opt[0].lower() for opt in selected_types_raw
                    ]
                else:
                    self.allowed_release_types = []
                    items = []
            else:
                self.allowed_release_types = None

            logger.debug(f"Number of chunks: {len(content)}")
            if content:
                logger.debug(
                    f"Items in first chunk: {len(content[0].get(type_dict['iterable_key'], {}).get('items', []))}"
                )
            if getattr(self, "allowed_release_types", None) is not None:
                logger.info(
                    f"{YELLOW}[*] Evaluating {len(items)} releases (unwanted types will be skipped silently)...{OFF}"
                )
            else:
                logger.info(f"{YELLOW}{len(items)} downloads in queue{OFF}")

            is_playlist = url_type == "playlist"
            if is_playlist and not getattr(self, "playlist_as_albums", False):
                original_folder_format = self.folder_format
                original_multi_disc_setting = self.settings.multiple_disc_one_dir

                self.folder_format = "."
                self.settings.multiple_disc_one_dir = True

            is_track_batch = type_dict["iterable_key"] == "tracks"
            batch_workers = int(getattr(self.settings, "max_workers", 3))
            can_parallelize = (
                is_track_batch and batch_workers > 1 and getattr(self, "delay", 0) <= 0
            )
            position_pool = (
                downloader._PositionPool(batch_workers) if can_parallelize else None
            )
            semaphore = asyncio.Semaphore(batch_workers) if can_parallelize else None
            pending_tasks = []

            if can_parallelize:
                logger.info(
                    f"{YELLOW}[*] Multithreading Enabled ({batch_workers} workers).{OFF}"
                )

            for idx, item in enumerate(items, start=1):
                if (
                    getattr(self, "allowed_release_types", None)
                    and url_type == "artist"
                ):
                    try:
                        r_type = "unknown"

                        full_meta = None
                        if hasattr(self.client, "get_album_meta"):
                            full_meta = await self.client.get_album_meta(item["id"])
                        elif hasattr(self.client, "get_album"):
                            full_meta = await self.client.get_album(item["id"])

                        if full_meta:
                            r_type = (
                                full_meta.get("release_type")
                                or full_meta.get("product_type")
                                or "unknown"
                            ).lower()

                        base_title = str(item.get("title", "")).lower()
                        version_tag = str(item.get("version", "")).lower()
                        t_count = item.get("tracks_count", 0)

                        if (
                            "live" in version_tag
                            or "(live" in base_title
                            or "- live" in base_title
                        ):
                            r_type = "live"
                        elif any(
                            kw in base_title or kw in version_tag
                            for kw in [
                                "best of",
                                "greatest hits",
                                "anthology",
                                "collection",
                                "compilation",
                            ]
                        ):
                            r_type = "compilation"
                        elif " ep" in base_title or version_tag == "ep":
                            r_type = "ep"

                        elif r_type == "single" and t_count >= 4:
                            r_type = "ep"
                        elif r_type == "ep" and 1 <= t_count <= 3:
                            r_type = "single"
                        elif r_type == "album" and 1 <= t_count <= 3:
                            r_type = "single"

                        elif r_type == "unknown":
                            if 1 <= t_count <= 3:
                                r_type = "single"
                            elif 4 <= t_count <= 6:
                                r_type = "ep"
                            else:
                                r_type = "album"

                        if r_type not in self.allowed_release_types:
                            continue

                    except Exception:
                        pass

                if getattr(self, "blacklist_patterns", None):
                    base_title = item.get("title") or item.get("name") or ""
                    version_tag = item.get("version") or ""

                    display_name = (
                        f"{base_title} ({version_tag})" if version_tag else base_title
                    )

                    if any(
                        pattern in display_name.lower()
                        for pattern in self.blacklist_patterns
                    ):
                        logger.info(
                            f"{YELLOW}[!] Skipped (Blacklisted): {display_name}{OFF}"
                        )
                        continue

                if can_parallelize:
                    item_id_captured = item["id"]
                    idx_captured = idx

                    async def _bounded_track_download(
                        item_id=item_id_captured, idx=idx_captured
                    ):
                        async with semaphore:
                            await self.download_from_id(
                                item_id,
                                False,
                                new_path,
                                is_playlist=is_playlist,
                                playlist_index=idx,
                                is_parallel=True,
                                position_pool=position_pool,
                            )

                    pending_tasks.append(_bounded_track_download())
                else:
                    await self.download_from_id(
                        item["id"],
                        True if type_dict["iterable_key"] == "albums" else False,
                        new_path,
                        is_playlist=is_playlist,
                        playlist_index=idx,
                    )

            if pending_tasks:
                await asyncio.gather(*pending_tasks)

            if is_playlist and not getattr(self, "playlist_as_albums", False):
                self.folder_format = original_folder_format
                self.settings.multiple_disc_one_dir = original_multi_disc_setting

            if url_type == "playlist" and not self.no_m3u_for_playlists:
                make_m3u(new_path)
        else:
            await self.download_from_id(item_id, type_dict["album"])

    def mark_url_done_in_file(self, txt_file, url_to_mark):
        if not txt_file or not os.path.isfile(txt_file):
            return
        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                lines = f.readlines()

            with open(txt_file, "w", encoding="utf-8") as f:
                for line in lines:
                    if line.strip() == url_to_mark.strip():
                        f.write(f"{line.rstrip()} [DONE]\n")
                    else:
                        f.write(line)
        except Exception as e:
            logger.error(f"{RED}Failed to update text file status: {e}{OFF}")

    async def download_list_of_urls(self, urls, txt_file=None):
        if not urls or not isinstance(urls, list):
            logger.info(f"{OFF}Nothing to download")
            return

        batch_workers = int(getattr(self.settings, "max_workers", 3))
        can_parallelize = batch_workers > 1 and getattr(self, "delay", 0) <= 0

        track_urls = []
        other_urls = []

        if can_parallelize:
            for i, url in enumerate(urls):
                probe_url = url.replace("open.qobuz.com", "play.qobuz.com")
                if "last.fm" in probe_url or os.path.isfile(probe_url):
                    other_urls.append((i, url))
                    continue
                try:
                    url_type, item_id = get_url_info(probe_url)
                except (KeyError, IndexError):
                    other_urls.append((i, url))
                    continue
                if url_type == "track":
                    track_urls.append((i, url, item_id))
                else:
                    other_urls.append((i, url))
        else:
            other_urls = list(enumerate(urls))

        if track_urls:
            logger.info(
                f"{YELLOW}[*] Multithreading Enabled ({batch_workers} workers).{OFF}"
            )
            position_pool = downloader._PositionPool(batch_workers)
            semaphore = asyncio.Semaphore(batch_workers)

            async def _bounded_track_url(original_url, item_id):
                async with semaphore:
                    await self.download_from_id(
                        item_id,
                        False,
                        is_parallel=True,
                        position_pool=position_pool,
                    )
                self.mark_url_done_in_file(txt_file, original_url)

            await asyncio.gather(
                *[
                    _bounded_track_url(original_url, item_id)
                    for _, original_url, item_id in track_urls
                ]
            )

        for _, url in other_urls:
            original_url = url
            url = url.replace("open.qobuz.com", "play.qobuz.com")

            if "last.fm" in url:
                await self.download_lastfm_pl(url)
                self.mark_url_done_in_file(txt_file, original_url)
            elif os.path.isfile(url):
                await self.download_from_txt_file(url)
            else:
                await self.handle_url(url)
                self.mark_url_done_in_file(txt_file, original_url)

    async def download_from_txt_file(self, txt_file):
        try:
            valid_urls = []
            with open(txt_file, "r", encoding="utf-8") as txt:
                for line in txt:
                    line = line.strip()
                    if not line or line.startswith("#") or "[DONE]" in line:
                        continue

                    if "last.fm" in line:
                        valid_urls.append(line)
                    else:
                        try:
                            get_url_info(line)
                            valid_urls.append(line)
                        except (KeyError, IndexError, AttributeError):
                            logger.debug(f"Skipping invalid URL line: {line}")

        except Exception as e:
            logger.error(f"{RED}Invalid text file: {e}{OFF}")
            return

        if not valid_urls:
            logger.info(f"{OFF}No new valid URLs found in file: {txt_file}")
            return

        logger.info(
            f"{YELLOW}qobuz-dl will download {len(valid_urls)}"
            f" urls from file: {txt_file}{OFF}"
        )
        await self.download_list_of_urls(valid_urls, txt_file=txt_file)

    async def lucky_mode(self, query, download=True):
        if len(query) < 3:
            logger.info(f"{RED}Your search query is too short or invalid")
            return

        logger.info(
            f'{YELLOW}Searching {self.lucky_type}s for "{query}".\n'
            f"{YELLOW}qobuz-dl will attempt to download the first "
            f"{self.lucky_limit} results."
        )
        results = await self.search_by_type(
            query, self.lucky_type, self.lucky_limit, True
        )

        if download:
            await self.download_list_of_urls(results)

        return results

    def _extract_rich_metadata(self, i, item_type, mode_dict, fav_subtype=None):
        """Extrai metadados super detalhados para exibição."""
        meta_data = {}
        duration = i.get("duration", 0)
        fmt_duration = format_duration(duration) if duration else "--:--"

        if mode_dict.get("requires_extra") or item_type in ["album", "track"]:
            artist = (
                i.get("artist", {}).get("name")
                or i.get("performer", {}).get("name")
                or "Unknown"
            )
            title = i.get("title") or i.get("name") or "Unknown"
            if i.get("version"):
                title = f"{title} ({i.get('version')})"
            if i.get("parental_warning"):
                title = f"{title} [E]"

            year = str(
                i.get("release_date_original") or i.get("release_date") or "    "
            )[:4]
            t_count = i.get("tracks_count", 0)

            gnr = (
                i.get("genre", {}).get("name", "")
                if isinstance(i.get("genre"), dict)
                else i.get("genre", "")
            )
            lbl = (
                i.get("label", {}).get("name", "")
                if isinstance(i.get("label"), dict)
                else i.get("label", "")
            )

            raw_type = i.get("release_type") or i.get("product_type")
            if not raw_type:
                if item_type == "album" and (t_count or duration):
                    if duration >= 1740 or t_count >= 7:
                        raw_type = "Album"
                    elif t_count == 1:
                        raw_type = "Single"
                    else:
                        raw_type = "EP"
                else:
                    raw_type = item_type

            rel_type = "EP" if raw_type.lower() == "ep" else raw_type.title()

            if i.get("hires_streamable"):
                bit_depth = i.get("maximum_bit_depth", 24)
                sampling_rate = i.get("maximum_sampling_rate", 96.0)
                quality = f"[HI-RES] {bit_depth}b/{sampling_rate}kHz"
            else:
                quality = "[ CD ] 16b/44.1kHz"

            album_name = (
                i.get("album", {}).get("title", "Unknown Album")
                if isinstance(i.get("album"), dict)
                else "Unknown Album"
            )

            meta_data = {
                "artist": artist,
                "title": title,
                "album": album_name,
                "type": rel_type,
                "year": year,
                "quality": quality,
                "duration": fmt_duration,
                "tracks_count": t_count,
                "genre": gnr,
                "label": lbl,
                "id": i.get("id"),
            }
        else:
            name = i.get("name", "Unknown")
            count = (
                i.get("albums_count")
                if "albums_count" in i
                else i.get("tracks_count", 0)
            )

            if item_type == "playlist" or fav_subtype == "playlists":
                owner = i.get("owner", {}).get("name", "Unknown")
                meta_data = {
                    "name": name,
                    "owner": owner,
                    "count": count,
                    "duration": fmt_duration,
                    "id": i.get("id"),
                }
            else:
                meta_data = {"name": name, "count": count, "id": i.get("id")}
        return meta_data

    async def search_by_type(
        self, query, item_type, limit=10, lucky=False, fav_subtype=None
    ):
        if item_type != "favorites" and (not query or len(query) < 3):
            logger.info(f"{RED}Your search query is too short or invalid")
            return

        possibles = {
            "album": {
                "func": self.client.search_albums,
                "album": True,
                "key": "albums",
                "requires_extra": True,
            },
            "artist": {
                "func": self.client.search_artists,
                "album": True,
                "key": "artists",
                "requires_extra": False,
            },
            "track": {
                "func": self.client.search_tracks,
                "album": False,
                "key": "tracks",
                "requires_extra": True,
            },
            "playlist": {
                "func": self.client.search_playlists,
                "album": False,
                "key": "playlists",
                "requires_extra": False,
            },
            "favorites": {
                "func": self.client.get_favorites,
                "album": True,
                "key": "favorites",
                "requires_extra": True,
            },
        }

        try:
            mode_dict = possibles[item_type]

            if item_type == "favorites":
                if fav_subtype == "playlists":
                    iterable = []
                    user_id = getattr(self.client, "user_id", None)
                    if (
                        not user_id
                        and hasattr(self.client, "user")
                        and isinstance(self.client.user, dict)
                    ):
                        user_id = self.client.user.get("id")

                    params = {"limit": limit}
                    if user_id:
                        params["user_id"] = user_id

                    try:
                        p1 = params.copy()
                        p1["request_ts"] = int(time.time())
                        sig = self.client._modern_sig(
                            "playlist/getUserPlaylists", p1, self.client.sec
                        )
                        p1["request_sig"] = sig
                        async with self.client.session.request(
                            "get",
                            self.client.base + "playlist/getUserPlaylists",
                            params=p1,
                        ) as r1:
                            res1 = await r1.json()

                        if "playlists" in res1 and "items" in res1["playlists"]:
                            iterable = res1["playlists"]["items"]
                        else:
                            p2 = params.copy()
                            p2["request_ts"] = int(time.time())
                            sig2 = self.client._modern_sig(
                                "playlist/getUserPlaylistIds", p2, self.client.sec
                            )
                            p2["request_sig"] = sig2
                            async with self.client.session.request(
                                "get",
                                self.client.base + "playlist/getUserPlaylistIds",
                                params=p2,
                            ) as r2:
                                res2 = await r2.json()

                            ids = (
                                res2.get("playlist_ids", [])
                                if isinstance(res2, dict)
                                else []
                            )
                            for p_id in ids:
                                try:
                                    p_params = {"playlist_id": p_id, "extra": "tracks"}
                                    p_params["request_ts"] = int(time.time())
                                    p_sig = self.client._modern_sig(
                                        "playlist/get", p_params, self.client.sec
                                    )
                                    p_params["request_sig"] = p_sig
                                    async with self.client.session.request(
                                        "get",
                                        self.client.base + "playlist/get",
                                        params=p_params,
                                    ) as rp:
                                        p_data = await rp.json()
                                        if "id" in p_data:
                                            iterable.append(p_data)
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.error(f"{RED}Erro ao buscar playlists: {e}{OFF}")

                    mode_dict["requires_extra"] = False
                else:
                    results = await mode_dict["func"](fav_type=fav_subtype, limit=limit)
                    iterable = (
                        results.get(fav_subtype, {}).get("items", [])
                        if isinstance(results, dict)
                        else []
                    )
                    mode_dict["requires_extra"] = fav_subtype not in [
                        "artists",
                        "playlists",
                    ]
            else:
                results = await mode_dict["func"](query, limit)
                iterable = (
                    results.get(mode_dict["key"], {}).get("items", [])
                    if isinstance(results, dict)
                    else []
                )

            item_list = []

            for i in iterable:
                if not isinstance(i, dict):
                    continue
                meta_data = self._extract_rich_metadata(
                    i, item_type, mode_dict, fav_subtype
                )

                url_category = (
                    fav_subtype[:-1]
                    if (item_type == "favorites" and fav_subtype)
                    else item_type
                )
                url = "{}{}/{}".format(WEB_URL, url_category, i.get("id", ""))

                item_list.append({"meta": meta_data, "url": url} if not lucky else url)

            return item_list

        except Exception as e:
            logger.info(f"{RED}Erro na busca: {e}{OFF}")
            return []

    async def interactive(self, download=True):
        self._is_interactive_session = True

        qualities = [
            {"q_string": "320", "q": 5},
            {"q_string": "Lossless", "q": 6},
            {"q_string": "Hi-res =< 96kHz", "q": 7},
            {"q_string": "Hi-Res > 96 kHz", "q": 27},
        ]

        try:
            item_types = ["Albums", "Tracks", "Artists", "Playlists", "Favorites"]
            scelta_res = await _tui_select(
                "O que você deseja buscar?",
                item_types,
                is_multi=False,
                item_category="filter",
            )
            if not scelta_res:
                return
            scelta_raw, _ = scelta_res

            if scelta_raw == "Favorites":
                selected_type = "favorites"
            else:
                selected_type = scelta_raw[:-1].lower()

            final_url_list = []
            session = PromptSession()

            while True:
                selected_fav = None
                if selected_type == "favorites":
                    fav_types = ["Albums", "Tracks", "Artists", "Playlists"]
                    fav_res = await _tui_select(
                        "Quais favoritos deseja explorar?",
                        fav_types,
                        is_multi=False,
                        item_category="filter",
                    )
                    if not fav_res:
                        break
                    selected_fav, _ = fav_res
                    selected_fav = selected_fav.lower()

                    logger.info(
                        f"{YELLOW}Buscando seus favoritos ({selected_fav})...{RESET}"
                    )
                    options = await self.search_by_type(
                        None,
                        selected_type,
                        limit=self.interactive_limit,
                        fav_subtype=selected_fav,
                    )
                    query_title = f"Meus Favoritos ({selected_fav.title()})"
                    display_cat = selected_fav[:-1]
                else:
                    sys.stdout.write("\033[2J\033[H")
                    sys.stdout.flush()

                    query = await session.prompt_async(
                        "Digite sua busca: [Ctrl + C para sair]\n> "
                    )
                    if not query.strip():
                        continue

                    logger.info(f"{YELLOW}Pesquisando...{RESET}")
                    options = await self.search_by_type(
                        query, selected_type, self.interactive_limit
                    )
                    query_title = query.title()
                    display_cat = selected_type

                if not options:
                    logger.info(f"{OFF}Nada encontrado.{RESET}")
                    if selected_type == "favorites":
                        break
                    continue

                title = f'RESULTADOS PARA "{query_title}"'
                selected_items = await _tui_select(
                    title, options, is_multi=True, item_category=display_cat
                )

                if selected_items and len(selected_items) > 0:

                    # --- SUBSELEÇÃO (DRILL-DOWN) INTELIGENTE PARA ARTISTAS ---
                    if display_cat == "artist":
                        action_res = await _tui_select(
                            "O que você deseja explorar deste artista?",
                            [
                                "Explorar Álbuns/Lançamentos (Com filtro manual)",
                                "Baixar Toda a Discografia (Sem filtro)",
                                "Explorar Faixas Mais Populares (Top Tracks)",
                            ],
                            is_multi=False,
                            item_category="filter",
                        )

                        if not action_res:
                            continue
                        action, _ = action_res

                        for item in selected_items:
                            art_id = item[0]["meta"]["id"]
                            art_name = item[0]["meta"]["name"]

                            if "Top Tracks" in action:
                                logger.info(
                                    f"{YELLOW}Buscando faixas populares de {art_name}...{RESET}"
                                )
                                top_tracks = []
                                async for chunk in self.client.get_artist_meta(art_id):
                                    tracks_data = chunk.get("tracks", {}).get(
                                        "items", []
                                    )
                                    top_tracks.extend(tracks_data)

                                if not top_tracks:
                                    res_tracks = await self.client.search_tracks(
                                        art_name, limit=20
                                    )
                                    top_tracks = res_tracks.get("tracks", {}).get(
                                        "items", []
                                    )

                                if not top_tracks:
                                    logger.info(
                                        f"{RED}Nenhuma faixa encontrada para {art_name}.{OFF}"
                                    )
                                    continue

                                track_options = []
                                for t in top_tracks:
                                    meta_data = self._extract_rich_metadata(
                                        t, "track", {"requires_extra": True}
                                    )
                                    url = f"{WEB_URL}track/{t.get('id')}"
                                    track_options.append(
                                        {"meta": meta_data, "url": url}
                                    )

                                track_selected = await _tui_select(
                                    f"Faixas de {art_name}",
                                    track_options,
                                    is_multi=True,
                                    item_category="track",
                                )
                                if track_selected:
                                    [
                                        final_url_list.append(t[0]["url"])
                                        for t in track_selected
                                    ]

                            else:
                                logger.info(
                                    f"{YELLOW}Buscando catálogo de {art_name}...{RESET}"
                                )
                                content = []
                                async for chunk in self.client.get_artist_meta(art_id):
                                    content.extend(
                                        chunk.get("albums", {}).get("items", [])
                                    )

                                if not content:
                                    logger.info(
                                        f"{RED}Nenhum álbum encontrado para {art_name}.{OFF}"
                                    )
                                    continue

                                if "Com filtro" in action:
                                    filter_opts = [
                                        "Album",
                                        "EP",
                                        "Single",
                                        "Live",
                                        "Compilation",
                                    ]
                                    selected_types_raw = await _tui_select(
                                        f"Filtros para {art_name}",
                                        filter_opts,
                                        is_multi=True,
                                        item_category="filter",
                                    )
                                    allowed = (
                                        [opt[0].lower() for opt in selected_types_raw]
                                        if selected_types_raw
                                        else []
                                    )

                                    art_options = []
                                    for a in content:
                                        r_type = (
                                            a.get("release_type") or "album"
                                        ).lower()
                                        if allowed and r_type not in allowed:
                                            continue
                                        meta_data = self._extract_rich_metadata(
                                            a, "album", {"requires_extra": True}
                                        )
                                        if meta_data["artist"] == "Unknown":
                                            meta_data["artist"] = art_name
                                        url = f"{WEB_URL}album/{a.get('id')}"
                                        art_options.append(
                                            {"meta": meta_data, "url": url}
                                        )

                                    art_selected = await _tui_select(
                                        f"Álbuns de {art_name}",
                                        art_options,
                                        is_multi=True,
                                        item_category="album",
                                    )
                                    if art_selected:
                                        [
                                            final_url_list.append(a[0]["url"])
                                            for a in art_selected
                                        ]
                                else:
                                    for a in content:
                                        url = f"{WEB_URL}album/{a.get('id')}"
                                        final_url_list.append(url)
                    else:
                        [
                            final_url_list.append(item[0]["url"])
                            for item in selected_items
                        ]

                    yn_res = await _tui_select(
                        "Itens adicionados à fila. Deseja buscar mais?",
                        ["Sim", "Não"],
                        is_multi=False,
                        item_category="filter",
                    )
                    if not yn_res:
                        break
                    y_n, _ = yn_res
                    if y_n == "Não":
                        break
                else:
                    logger.info(f"{YELLOW}Ok, vamos tentar de novo...{RESET}")
                    if selected_type == "favorites":
                        break
                    continue

            if final_url_list:
                qualities_texts = [q.get("q_string") for q in qualities]
                qual_res = await _tui_select(
                    "Selecione a qualidade máxima do download",
                    qualities_texts,
                    is_multi=False,
                    item_category="filter",
                )
                if not qual_res:
                    return
                selected_quality, sq_idx = qual_res
                self.quality = qualities[sq_idx]["q"]

                if download:
                    await self.download_list_of_urls(final_url_list)

                return final_url_list

        except KeyboardInterrupt:
            sys.stdout.write("\033[2J\033[H")
            logger.info(f"{YELLOW}Operação cancelada pelo usuário. Tchau!{OFF}")
            return

    async def download_lastfm_pl(self, playlist_url):
        from qobuz_dl.lastfm_parser import fetch_lastfm_playlist

        logger.info(
            f"{CYAN}[*] Last.fm URL detected! Initiating Last.fm integration...{OFF}"
        )

        loop = asyncio.get_event_loop()
        tracks_list = await loop.run_in_executor(
            None, fetch_lastfm_playlist, playlist_url
        )

        if not tracks_list:
            logger.info(f"{YELLOW}[!] Last.fm processing aborted (no tracks).{OFF}")
            return

        pl_id = playlist_url.rstrip("/").split("/")[-1]
        pl_title = sanitize_filename(f"LastFM_Playlist_{pl_id}")
        pl_directory = os.path.join(self.directory, pl_title)

        logger.info(
            f"{YELLOW}Downloading playlist: {pl_title} ({len(tracks_list)} tracks){RESET}"
        )

        track_ids = await self.client.get_track_ids_from_list(tracks_list)

        if not track_ids:
            logger.info(f"{RED}[!] No matching tracks found on Qobuz. Aborting.{OFF}")
            return

        original_folder_format = self.folder_format
        original_multi_disc_setting = self.settings.multiple_disc_one_dir

        if not getattr(self, "playlist_as_albums", False):
            self.folder_format = "."
            self.settings.multiple_disc_one_dir = True

        batch_workers = int(getattr(self.settings, "max_workers", 3))
        can_parallelize = batch_workers > 1 and getattr(self, "delay", 0) <= 0
        position_pool = (
            downloader._PositionPool(batch_workers) if can_parallelize else None
        )
        semaphore = asyncio.Semaphore(batch_workers) if can_parallelize else None
        pending_tasks = []

        if can_parallelize:
            logger.info(
                f"{YELLOW}[*] Multithreading Enabled ({batch_workers} workers).{OFF}"
            )

        for idx, t_id in enumerate(track_ids, start=1):
            if can_parallelize:
                t_id_captured = t_id
                idx_captured = idx

                async def _bounded_track_download(t_id=t_id_captured, idx=idx_captured):
                    async with semaphore:
                        try:
                            await self.download_from_id(
                                t_id,
                                False,
                                pl_directory,
                                is_playlist=True,
                                playlist_index=idx,
                                is_parallel=True,
                                position_pool=position_pool,
                            )
                        except Exception as e:
                            logger.error(
                                f"{RED}[!] Failed to queue track ID {t_id}: {e}{OFF}"
                            )

                pending_tasks.append(_bounded_track_download())
            else:
                try:
                    await self.download_from_id(
                        t_id, False, pl_directory, is_playlist=True, playlist_index=idx
                    )
                except Exception as e:
                    logger.error(f"{RED}[!] Failed to queue track ID {t_id}: {e}{OFF}")

        if pending_tasks:
            await asyncio.gather(*pending_tasks)

        if not getattr(self, "playlist_as_albums", False):
            self.folder_format = original_folder_format
            self.settings.multiple_disc_one_dir = original_multi_disc_setting

        if not self.no_m3u_for_playlists:
            make_m3u(pl_directory)
