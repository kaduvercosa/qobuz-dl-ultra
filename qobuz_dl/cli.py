from qobuz_dl.utils import get_config_paths
import sys
import difflib
import string
import configparser
import logging
import glob
import os
import signal
import shutil
import textwrap
import keyring
import requests
import asyncio

from qobuz_dl.bundle import Bundle
# CYAN/YELLOW importados como INFO/WARNING renomeados: mesma cor de
# YELLOW (mantida por convencao), mas CYAN agora e' LIGHTBLUE_EX --
# visivel em terminal claro E escuro (CYAN puro quase some em fundo
# branco). Ver comentario completo em qobuz_dl/color.py. Zero mudanca
# de codigo neste arquivo: toda f-string que ja usa {CYAN}/{YELLOW}
# continua funcionando, so' a cor de fato renderizada muda.
from qobuz_dl.color import GREEN, WARNING as YELLOW, OFF, INFO as CYAN
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.settings import QobuzDLSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)

# --- iOS / a-Shell support ---
# Deteccao cross-platform centralizada em utils.get_config_paths() -- ver
# la' os detalhes de cada plataforma (Windows, Linux/macOS, iOS/a-Shell).
# radar.py usa a mesma funcao, entao os dois ficam sempre sincronizados.

_config_paths = get_config_paths()
CONFIG_DIR = _config_paths["config_dir"]
CONFIG_PATH = _config_paths["config_path"]
CONFIG_FILE = _config_paths["config_file"]
QOBUZ_DB = _config_paths["qobuz_db"]
IOS_HOME = os.environ.get("QOBUZ_DL_IOS_HOME")

KEYRING_SERVICE = "qobuz-dl"


def _keyring_save(key, value):
    """
    Securely stores a credential in the operating system's native Credential Manager.

    Args:
        key (str): The credential identifier (e.g., 'auth_token').
        value (str): The sensitive token or password to store.

    Returns:
        bool: True if the credential was successfully saved, False otherwise.
    """
    if not value:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception:
        return False


def _keyring_load(key):
    """
    Retrieves a stored credential from the OS Credential Manager.

    Args:
        key (str): The credential identifier to retrieve.

    Returns:
        str or None: The stored credential, or None if not found or on error.
    """
    try:
        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception:
        return None


def validate_config_formats(formats_to_check):
    """
    Pre-Flight Config Validation.

    Scans the configuration format strings for unknown variables to prevent
    silent KeyErrors during the download process. Implements a heuristic engine
    using difflib to suggest typing corrections to the user.

    Args:
        formats_to_check (dict): A dictionary mapping format setting names to their string values.
    """
    VALID_KEYS = {
        "artist",
        "album",
        "album_id",
        "album_url",
        "album_title",
        "album_title_base",
        "album_artist",
        "album_genre",
        "album_composer",
        "label",
        "copyright",
        "upc",
        "barcode",
        "release_date",
        "year",
        "media_type",
        "format",
        "bit_depth",
        "sampling_rate",
        "album_version",
        "version_tag",
        "disc_count",
        "track_count",
        "ExplicitFlag",
        "explicit",
        "release_type",
        "tracktitle",
        "track_title",
        "track_title_base",
        "track_id",
        "track_artist",
        "track_composer",
        "track_number",
        "isrc",
        "version",
        "disc_number",
    }

    has_errors = False

    C_RED = "\033[91m"
    C_YEL = "\033[93m"
    C_GRE = "\033[92m"
    C_OFF = "\033[0m"

    for config_name, format_string in formats_to_check.items():
        if not format_string:
            continue

        try:
            parsed_vars = [
                tup[1]
                for tup in string.Formatter().parse(str(format_string))
                if tup[1] is not None
            ]

            for var in parsed_vars:
                base_var = var.split(":")[0].split("!")[0]

                if base_var not in VALID_KEYS:
                    print(
                        f"{C_YEL}[!] Config Warning: Unknown variable '{{{base_var}}}' detected in '{config_name}'.{C_OFF}"
                    )

                    similar_keys = difflib.get_close_matches(
                        base_var, VALID_KEYS, n=1, cutoff=0.6
                    )
                    if similar_keys:
                        print(
                            f"    {C_GRE}-> Did you mean '{{{
                                similar_keys[0]}}}'?{C_OFF}"
                        )

                    print(
                        f"    {C_RED}-> This will cause the entire format string to be discarded during download.{C_OFF}"
                    )
                    has_errors = True

        except ValueError as e:
            print(
                f"{C_RED}[!] Config Error: Syntax error in '{config_name}' -> {e}{C_OFF}"
            )
            has_errors = True

    if has_errors:
        print(
            f"\n{C_YEL}[*] Tip: Please check your config.ini file or your command line arguments and fix any typos before downloading.{C_OFF}\n"
        )
        sys.exit(1)


def _reset_config(config_file):
    """
    Interactive configuration wizard for initializing or resetting the config.ini file.
    """
    logging.info(f"\n{YELLOW}--- QOBUZ-DL CONFIGURATION WIZARD (2026 Update) ---{OFF}")
    config = configparser.ConfigParser(interpolation=None)

    config["qobuz"] = {}

    email = input("Enter your Qobuz email:\n- ").strip()
    config["qobuz"]["email"] = email

    print(
        f"\n{YELLOW}[!] ATTENTION: Qobuz API blocked direct password login for 3rd party apps.{OFF}"
    )
    print(
        f"{YELLOW}[!] You must use your browser Auth Token (F12 > Storage > Local Storage > localuser > token).{OFF}"
    )

    auth_token = input("Paste your browser token here:\n- ").strip()

    config["qobuz"]["password"] = ""

    print(f"\n{YELLOW}[?] OS Keyring Security:{OFF}")
    print("    By default, tokens are encrypted in your OS Credential Manager.")
    print("    If you are on a headless Linux/NAS/Docker, this might fail silently.")
    disable_kr = (
        input(
            "    Disable OS Keyring and save tokens in config.ini? (yes/no) [Default: no]\n- "
        )
        .strip()
        .lower()
    )

    use_keyring = False if disable_kr in ["yes", "y", "true"] else True
    config["qobuz"]["disable_keyring"] = "true" if not use_keyring else "false"

    if use_keyring and _keyring_save("auth_token", auth_token):
        config["qobuz"]["auth_token"] = ""
    else:
        config["qobuz"]["auth_token"] = auth_token

    fetch_lyrics = (
        input(
            "\nDo you want to automatically download and inject lyrics? (yes/no) [Default: yes]\n- "
        )
        .strip()
        .lower()
    )
    config["qobuz"]["fetch_lyrics"] = (
        "false" if fetch_lyrics in ["no", "n", "false"] else "true"
    )

    genius_token = ""
    if config["qobuz"]["fetch_lyrics"] == "true":
        print(
            f"{YELLOW}[!] To use Genius as a fallback, enter your API Token. Leave blank to only use LRCLIB (Free/No API).{OFF}"
        )
        genius_token = input("Genius API Token:\n- ").strip()

    if use_keyring and _keyring_save("genius_token", genius_token):
        config["qobuz"]["genius_token"] = ""
    else:
        config["qobuz"]["genius_token"] = genius_token

    config["qobuz"]["directory"] = (
        input("Download folder (press Enter for 'Qobuz Downloads')\n- ") or
        "Qobuz Downloads"
    )

    config["qobuz"]["folder_format"] = (
        input(f"Folder format (press Enter for '{DEFAULT_FOLDER}')\n- ") or
        DEFAULT_FOLDER
    )

    config["qobuz"]["default_quality"] = (
        input("Download quality (5:MP3, 6:FLAC, 7:24b<96, 27:24b>96) [Default 27]\n- ") or
        "27"
    )

    config["qobuz"]["default_limit"] = "500"
    config["qobuz"]["no_m3u"] = "false"
    config["qobuz"]["albums_only"] = "false"
    config["qobuz"]["no_fallback"] = "false"
    config["qobuz"]["og_cover"] = "true"
    config["qobuz"]["embed_art"] = "true"
    config["qobuz"]["no_cover"] = "false"
    config["qobuz"]["no_database"] = "false"
    config["qobuz"]["no_lrc_files"] = "true"
    config["qobuz"]["embed_lyrics"] = "true"
    config["qobuz"]["multi_value_tags"] = "false"
    config["qobuz"]["legacy_charmap"] = "false"
    config["qobuz"]["blacklist"] = "blacklist.txt"
    config["qobuz"]["lyrics_translation_lang"] = "pt"

    logging.info(f"{YELLOW}Getting tokens. Please wait...{OFF}")
    bundle = Bundle()
    config["qobuz"]["app_id"] = str(bundle.get_app_id())
    config["qobuz"]["secrets"] = ",".join(bundle.get_secrets().values())

    config["qobuz"]["track_format"] = "{track_number} - {track_title}"
    config["qobuz"]["fallback_folder_format"] = "{album_artist} - {album_title}"
    config["qobuz"]["smart_discography"] = "false"

    config["qobuz"]["no_album_artist_tag"] = "false"
    config["qobuz"]["no_album_title_tag"] = "false"
    config["qobuz"]["no_track_artist_tag"] = "false"
    config["qobuz"]["no_track_title_tag"] = "false"
    config["qobuz"]["no_release_date_tag"] = "false"
    config["qobuz"]["no_media_type_tag"] = "false"
    config["qobuz"]["no_genre_tag"] = "false"
    config["qobuz"]["no_track_number_tag"] = "false"
    config["qobuz"]["no_track_total_tag"] = "false"
    config["qobuz"]["no_disc_number_tag"] = "false"
    config["qobuz"]["no_disc_total_tag"] = "false"
    config["qobuz"]["no_composer_tag"] = "false"
    config["qobuz"]["no_replaygain_tag"] = "false"
    config["qobuz"]["no_album_url_tag"] = "false"

    config["qobuz"]["no_explicit_tag"] = "false"
    config["qobuz"]["no_copyright_tag"] = "false"
    config["qobuz"]["no_label_tag"] = "false"

    config["qobuz"]["no_credits"] = "false"

    config["qobuz"]["no_upc_tag"] = "false"
    config["qobuz"]["no_isrc_tag"] = "false"

    config["qobuz"]["embedded_art_size"] = "org"
    config["qobuz"]["saved_art_size"] = "org"

    config["qobuz"]["multiple_disc_prefix"] = "CD"
    config["qobuz"]["multiple_disc_one_dir"] = "false"
    config["qobuz"][
        "multiple_disc_track_format"
    ] = "{disc_number}.{track_number} - {track_title}"

    config["qobuz"]["max_workers"] = "1"
    config["qobuz"]["user_auth_token"] = ""

    with open(config_file, "w") as configfile:
        config.write(configfile)

    logging.info(
        f"\n{GREEN}[+] Configuration successfully saved in {config_file}!{OFF}"
    )


def _remove_leftovers(directory):
    """Cleans up any partial or temporary files (~tmp_*.tmp) left behind after an interruption."""
    for pattern in [".*.tmp", "~tmp_*.tmp"]:
        search_dir = os.path.join(directory, "**", pattern)
        for i in glob.glob(search_dir, recursive=True):
            try:
                os.remove(i)
            except:  # noqa
                pass


async def _handle_commands(qobuz, arguments):
    """Routes parsed command-line arguments to the appropriate QobuzDL core methods."""

    def sigint_handler(sig, frame):
        print(f"\n\n\033[91m[!] Download forcibly interrupted by the user.\033[0m")
        print(
            f"\033[93mPartially downloaded files will be ignored or overwritten on the next run.\033[0m"
        )
        try:
            _remove_leftovers(qobuz.directory)
        except Exception:
            pass
        sys.exit(1)

    signal.signal(signal.SIGINT, sigint_handler)

    try:
        if arguments.command == "dl":
            await qobuz.download_list_of_urls(arguments.SOURCE)
        elif arguments.command in ("sync-playlist", "sp"):
            from qobuz_dl.sync_playlist import sync_playlist

            await sync_playlist(
                qobuz,
                arguments.URL,
                qobuz.directory,
                auto_confirm=arguments.yes,
            )
        elif arguments.command == "lucky":
            query = " ".join(arguments.QUERY)
            qobuz.lucky_type = arguments.type
            qobuz.lucky_limit = arguments.number
            await qobuz.lucky_mode(query)
        else:
            qobuz.interactive_limit = arguments.limit
            await qobuz.interactive()

    except KeyboardInterrupt:
        pass
    finally:
        _remove_leftovers(qobuz.directory)


# Fonte bitmap 5x5 propria (nao depende de nenhuma lib externa tipo
# pyfiglet) usada so' pelas letras que aparecem em "QOBUZ-DL" / "ULTRA".
# Desenhada e testada a mao pra garantir alinhamento perfeito -- cada
# glifo tem exatamente 5 colunas de largura, sem excecao, entao toda
# linha da logo sai com a MESMA largura total, garantindo que o bloco
# nunca fica torto na hora de centralizar.
_LOGO_FONT = {
    "A": ["01110", "10001", "11111", "10001", "10001"],
    "B": ["11110", "10001", "11110", "10001", "11110"],
    "D": ["11110", "10001", "10001", "10001", "11110"],
    "L": ["10000", "10000", "10000", "10000", "11111"],
    "O": ["01110", "10001", "10001", "10001", "01110"],
    "Q": ["01110", "10001", "10101", "10011", "01111"],
    "R": ["11110", "10001", "11110", "10100", "10010"],
    "T": ["11111", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "01110"],
    "Z": ["11111", "00010", "00100", "01000", "11111"],
    "-": ["00000", "00000", "11111", "00000", "00000"],
}
_LOGO_BLOCK = "\u2588"


def _render_logo_word(word):
    """Retorna as 5 linhas (strings) da palavra em blocos, todas com a
    mesma largura -- 1 espaco separa cada letra, sem espaco sobrando
    depois da ultima."""
    letters = list(word)
    rows = ["" for _ in range(5)]
    for i, ch in enumerate(letters):
        glyph = _LOGO_FONT[ch]
        for r in range(5):
            rows[r] += "".join(_LOGO_BLOCK if px == "1" else " " for px in glyph[r])
            if i != len(letters) - 1:
                rows[r] += " "
    return rows


def _print_logo(cols):
    """
    Imprime a logo QOBUZ-DL-ULTRA, sempre centralizada em `cols`.
    Acima de ~52 colunas usa a arte em blocos (2 linhas: "QOBUZ-DL" tem
    47 colunas de largura, a mais larga das duas -- por isso o corte).
    Abaixo disso (telas bem estreitas) cai pra uma unica linha de texto
    simples, que cabe ate' no minimo de 32 colunas que o resto da tela
    ja' garante.
    """
    line1 = _render_logo_word("QOBUZ-DL")
    line2 = _render_logo_word("ULTRA")
    art_width = len(line1[0])  # 47 -- a mais larga das duas palavras

    if cols >= art_width + 5:
        pad1 = " " * max((cols - art_width) // 2, 0)
        pad2 = " " * max((cols - len(line2[0])) // 2, 0)
        for row in line1:
            print(f"{CYAN}{pad1}{row}{OFF}")
        for row in line2:
            print(f"{CYAN}{pad2}{row}{OFF}")
    else:
        fallback = "\u266a QOBUZ-DL-ULTRA \u266a"
        pad = " " * max((cols - len(fallback)) // 2, 0)
        print(f"{CYAN}{pad}{fallback}{OFF}")


def _print_welcome_screen():
    """
    Tela inicial mostrada quando `qobuz-dl` roda sem nenhum argumento.
    Troca o print_help() cru do argparse (que ignora a largura real do
    terminal e derrama linha gigante em telas estreitas -- iPad em Split
    View, a-Shell mini, etc.) por um layout que se adapta a QUALQUER
    largura de terminal, igual o `_get_safe_ncols()` que o downloader.py
    ja' usa pras barras de progresso.
    """
    from qobuz_dl import __version__

    # Nunca deixa a largura ficar ridiculamente pequena (ex: terminal
    # relatando 0 colunas em alguns pipes/CI) nem gigante demais pra
    # leitura confortavel numa tela grande.
    cols = max(min(shutil.get_terminal_size(fallback=(80, 24)).columns, 100), 32)
    body_width = cols - 2  # 1 char de respiro em cada margem

    # (comando, aliases, descricao breve)
    COMMANDS = [
        (
            "dl",
            None,
            "Baixa por URL de album, faixa, artista, label, playlist ou playlist do last.fm.",
        ),
        (
            "interactive",
            "i, fun",
            "Busca interativa: procura faixas/albuns e escolhe o que baixar na hora.",
        ),
        (
            "lucky",
            None,
            "Baixa os N primeiros resultados de uma busca no Qobuz, sem passar URL.",
        ),
        (
            "lyrics",
            None,
            "Varre uma pasta ja' baixada e injeta letras/traducoes que estejam faltando.",
        ),
        (
            "sync-playlist",
            "sp",
            "Sincroniza uma pasta local com uma playlist do Qobuz (baixa o que falta, remove o que saiu).",
        ),
    ]

    FLAGS = [
        ("-r, --reset", "cria/reseta o arquivo de configuracao"),
        ("-p, --purge", "apaga o banco de downloads-ja-feitos"),
        (
            "--sync-db [PATH]",
            "escaneia uma pasta local pra recuperar IDs do Qobuz perdidos no banco",
        ),
        ("-sc, --show-config", "mostra a configuracao atual"),
    ]

    def rule(ch="-"):
        print(ch * cols)

    def wrapped(text, indent):
        pad = " " * indent
        for line in textwrap.wrap(text, width=body_width - indent) or [""]:
            print(f"{pad}{line}")

    print()
    _print_logo(cols)
    version_line = f"v{__version__}"
    print(f"{OFF}{version_line.center(cols)}{OFF}")
    print()
    rule("=")
    print(f"{YELLOW}Uso:{OFF} qobuz-dl <comando> [opcoes]")
    print(f"     qobuz-dl <comando> --help   (lista todas as opcoes daquele comando)")
    print()

    print(f"{YELLOW}Comandos:{OFF}")
    for name, aliases, desc in COMMANDS:
        label = name if not aliases else f"{name} ({aliases})"
        print(f"  {GREEN}{label}{OFF}")
        wrapped(desc, indent=4)
    print()

    print(f"{YELLOW}Flags globais:{OFF} (nao pertencem a nenhum comando especifico)")
    for flag, desc in FLAGS:
        print(f"  {CYAN}{flag}{OFF}")
        wrapped(desc, indent=4)
    print()

    rule()


def _initial_checks():
    """Verifies the existence of the configuration file and basic CLI inputs."""
    if not os.path.isdir(CONFIG_PATH) or not os.path.isfile(CONFIG_FILE):
        os.makedirs(CONFIG_PATH, exist_ok=True)
        if "-r" not in sys.argv and "--reset" not in sys.argv:
            _reset_config(CONFIG_FILE)

    if len(sys.argv) < 2:
        _print_welcome_screen()
        sys.exit(0)


def check_for_updates():
    """Queries the GitHub API to notify the user of new Qobuz-DL Ultimate Edition releases."""
    try:
        from qobuz_dl import __version__

        url = "https://api.github.com/repos/Sei969/qobuz-dl/releases/latest"
        response = requests.get(url, timeout=2)
        response.raise_for_status()

        latest_version_str = response.json().get("tag_name", "").replace("v", "")
        current_version_str = __version__

        latest_tuple = tuple(map(int, latest_version_str.split(".")))
        current_tuple = tuple(map(int, current_version_str.split(".")))

        if latest_tuple > current_tuple:
            print(
                f"\n{YELLOW}[*] UPDATE AVAILABLE: Ultimate Edition v{latest_version_str} is out!{OFF}"
            )
            print(f"{YELLOW}    - PyPI: run 'pip install -U qobuz-dl-ultimate'{OFF}")
            print(f"{YELLOW}    - Docker: pull the latest image{OFF}")
            print(
                f"{YELLOW}    - Standalone: download the new release from GitHub{OFF}\n"
            )

    except Exception:
        pass


async def async_main():
    """The main asynchronous entry point for the CLI logic."""
    _initial_checks()

    # Dispara a checagem de atualizacao em background como coroutine
    # compativel com Python 3.13
    async def _async_check_updates():
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, check_for_updates)
        except Exception:
            pass

    asyncio.create_task(_async_check_updates())

    # --- RADAR FEATURE (Standalone Intercept) ---
    if len(sys.argv) > 1 and sys.argv[1] == "radar":
        from qobuz_dl.radar import run_radar

        try:
            await run_radar()
        except KeyboardInterrupt:
            print(
                "\n\n\033[91m[!] Radar manually interrupted by the user (CTRL+C).\033[0m"
            )
        sys.exit(0)
    # --------------------------------------------

    # --- STATS COMMAND INTEGRATION ---
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        from qobuz_dl.db import get_stats

        artists = get_stats(QOBUZ_DB)

        print(f"\n{CYAN}[ QOBUZ-DL-ULTRA - STATISTICS ]{OFF}")
        if not artists:
            print(
                f"{YELLOW}No artist data found yet. Start downloading to populate your stats!{OFF}"
            )
        else:
            print(f"Total Unique Artists Downloaded: {len(artists)}\n")
            for artist in artists:
                print(f" - {artist}")
        print(f"{CYAN}-------------------------------------{OFF}\n")
        sys.exit(0)
    # ---------------------------------

    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_FILE)

    try:
        section = "qobuz" if config.has_section("qobuz") else "DEFAULT"

        email = config.get(section, "email")

        # --- INIZIO PATCH KEYRING BYPASS ---
        ini_token = config.get(section, "auth_token", fallback="")
        ini_genius = config.get(section, "genius_token", fallback="")
        disable_keyring = str(
            config.get(section, "disable_keyring", fallback="false")
        ).strip().lower() in ["true", "yes", "y", "1"]

        if disable_keyring:
            ini_password = config.get(section, "password", fallback="")
            token = ini_token if ini_token else ini_password
            genius_token = ini_genius
            password = ini_password
        else:
            token = _keyring_load("auth_token") or ini_token
            password = token if token else config.get(section, "password", fallback="")
            genius_token = _keyring_load("genius_token") or ini_genius

            # Safe Migration Block
            migrated = False
            for k, v in (("auth_token", ini_token), ("genius_token", ini_genius)):
                if v and _keyring_save(k, v):
                    config.set(section, k, "")
                    migrated = True
            if migrated:
                try:
                    with open(CONFIG_FILE, "w") as f:
                        config.write(f)
                except OSError:
                    pass
        # --- FINE PATCH KEYRING BYPASS ---

        fetch_lyrics = config.getboolean(section, "fetch_lyrics", fallback=False)

        # --- FIX: Backward compatibility for default_folder ---
        directory_val = config.get(section, "directory", fallback=None)
        if directory_val is not None:
            default_folder = directory_val
        else:
            legacy_val = config.get(section, "default_folder", fallback=None)
            if legacy_val is not None:
                print(
                    f"\033[93m[!] Notice: 'default_folder' in config.ini is deprecated. Please rename it to 'directory' for future updates.\033[0m"
                )
                default_folder = legacy_val
            else:
                default_folder = "Qobuz Downloads"
        if IOS_HOME and not os.path.isabs(default_folder):
            default_folder = os.path.join(IOS_HOME, default_folder)
        # ------------------------------------------------------
        default_limit = config.get(section, "default_limit")
        default_quality = config.get(section, "default_quality")

        no_m3u = config.getboolean(section, "no_m3u", fallback=False)
        no_lrc_files_config = config.getboolean(section, "no_lrc_files", fallback=False)
        albums_only = config.getboolean(section, "albums_only", fallback=False)
        no_fallback = config.getboolean(section, "no_fallback", fallback=False)
        og_cover = config.getboolean(section, "og_cover", fallback=True)
        embed_art = config.getboolean(section, "embed_art", fallback=True)
        no_cover = config.getboolean(section, "no_cover", fallback=False)
        no_database = config.getboolean(section, "no_database", fallback=False)
        legacy_charmap = config.getboolean(section, "legacy_charmap", fallback=False)

        no_credits_config = config.getboolean(section, "no_credits", fallback=False)
        blacklist_config = config.get(section, "blacklist", fallback="blacklist.txt")
        playlist_as_albums_config = config.getboolean(
            section, "playlist_as_albums", fallback=False
        )

        app_id = config.get(section, "app_id")
        secrets = [s for s in config.get(section, "secrets").split(",") if s]

        smart_discography = config.getboolean(
            section, "smart_discography", fallback=False
        )
        folder_format = config.get(section, "folder_format", fallback=DEFAULT_FOLDER)
        track_format = config.get(section, "track_format", fallback=DEFAULT_TRACK)

        arguments = qobuz_dl_args(
            default_quality, default_limit, default_folder
        ).parse_args()

        if getattr(arguments, "no_lyrics", False):
            fetch_lyrics = False

        force_english = not getattr(arguments, "native_lang", False)
        # FIX: --with-credits existia no argparse (commands.py) e no README
        # ("overrides config.ini"), mas nunca era lido aqui -- essa linha so'
        # olhava --no-credits e o config.ini, entao se no_credits=true
        # estivesse salvo no config.ini, NENHUMA flag de CLI conseguia
        # reverter e o Digital Booklet.txt nunca era gerado. Agora
        # --with-credits tem prioridade e forca no_credits_flag=False,
        # exatamente como o help text sempre prometeu.
        with_credits_flag = getattr(arguments, "with_credits", False)
        no_credits_flag = (
            False
            if with_credits_flag
            else (getattr(arguments, "no_credits", False) or no_credits_config)
        )

    except (configparser.Error, KeyError) as error:
        arguments = qobuz_dl_args().parse_args()
        if not arguments.reset:
            RED_C = "\033[91m"
            YELLOW_C = "\033[93m"
            OFF_C = "\033[0m"
            sys.exit(
                f"{RED_C}Invalid or corrupted configuration ({error}).\n{OFF_C}"
                f"{YELLOW_C}Run 'python -m qobuz_dl -r' to fix this.{OFF_C}"
            )

    if arguments.reset:
        sys.exit(_reset_config(CONFIG_FILE))

    if arguments.show_config:
        print(f"Configuration: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        with open(CONFIG_FILE, "r") as f:
            print(f.read())
        sys.exit()

    if arguments.purge:
        try:
            os.remove(QOBUZ_DB)
        except FileNotFoundError:
            pass
        sys.exit(f"{GREEN}Database has been purged.{OFF}")

    # --- NEW DB SYNC FEATURE (Lightweight Mode) ---
    if getattr(arguments, "sync_db", None):
        from qobuz_dl.sync import sync_database
        from qobuz_dl.qopy import Client

        sync_client = await Client.create(
            email,
            password,
            app_id,
            secrets,
            user_auth_token=token,
            force_english=force_english,
        )

        sync_dir = (
            default_folder if arguments.sync_db == "DEFAULT" else arguments.sync_db
        )

        if os.name == "nt":
            sync_dir = os.path.abspath(sync_dir)
            if not sync_dir.startswith("\\\\?\\"):
                sync_dir = "\\\\?\\" + sync_dir

        await sync_database(sync_dir, QOBUZ_DB, sync_client)
        sys.exit(f"\n{GREEN}Database synchronization finished successfully.{OFF}")
    # ----------------------------------------------

    # --- RETRO LYRICS FEATURE (Standalone Mode) ---
    if arguments.command == "lyrics":
        from qobuz_dl.retro_tagger import inject_lyrics_retroactively
        from qobuz_dl.qopy import Client

        # 1. Se nenhum diretório foi digitado, usa a pasta raiz do config.ini
        # (default_folder)
        target_dir = getattr(arguments, "DIR", None) or default_folder
        target_dir = os.path.expanduser(target_dir)

        # --- IOS DOCUMENTS PRISON (A-SHELL FIX) ---
        home_dir = os.environ.get("HOME", "")
        if "Containers/Data/Application" in home_dir:
            docs_dir = os.path.join(home_dir, "Documents")
            if not target_dir.startswith(docs_dir):
                base_name = os.path.basename(target_dir.rstrip("/\\"))
                target_dir = os.path.join(
                    docs_dir, base_name if base_name else "Qobuz Downloads"
                )

        # --- WINDOWS LONG PATH BYPASS ---
        if os.name == "nt":
            target_dir = os.path.abspath(target_dir)
            if not target_dir.startswith("\\\\?\\"):
                target_dir = "\\\\?\\" + target_dir

        lrc_pref = not config.getboolean(section, "no_lrc_files", fallback=False)
        embed_pref = config.getboolean(section, "embed_lyrics", fallback=True)
        trans_lang = config.get(section, "lyrics_translation_lang", fallback="pt")

        local_settings = QobuzDLSettings(lrc_files=lrc_pref, embed_lyrics=embed_pref)
        local_settings.lyrics_translation_lang = trans_lang
        local_settings.default_folder = target_dir

        # 2. Inicializa o cliente Qobuz para consulta de letras e traduções
        lyrics_client = None
        try:
            lyrics_client = await Client.create(
                email,
                password,
                app_id,
                secrets,
                user_auth_token=token,
                force_english=force_english,
            )
        except Exception as e:
            logging.debug(f"Authentication warning for lyrics client: {e}")

        try:
            await inject_lyrics_retroactively(
                target_dir,
                client=lyrics_client,
                genius_token=genius_token,
                settings=local_settings,
            )
        except KeyboardInterrupt:
            print(
                "\n\n\033[91m[!] Operation manually interrupted by the user (CTRL+C).\033[0m"
            )
            print("\033[93mAlready processed files are safe. Exiting...\033[0m")
        finally:
            if lyrics_client:
                await lyrics_client.close()
        sys.exit(0)
    # ----------------------------------------------

    directory_to_use = (
        arguments.directory
        if hasattr(arguments, "directory") and arguments.directory
        else default_folder
    )
    directory_to_use = os.path.expanduser(directory_to_use)

    # --- IOS DOCUMENTS PRISON (A-SHELL FIX) ---
    home_dir = os.environ.get("HOME", "")
    if "Containers/Data/Application" in home_dir:
        docs_dir = os.path.join(home_dir, "Documents")
        if not directory_to_use.startswith(docs_dir):
            base_name = os.path.basename(directory_to_use.rstrip("/\\"))
            directory_to_use = os.path.join(
                docs_dir, base_name if base_name else "Qobuz Downloads"
            )

    # --- WINDOWS LONG PATH BYPASS ---
    if os.name == "nt":
        directory_to_use = os.path.abspath(directory_to_use)
        if not directory_to_use.startswith("\\\\?\\"):
            directory_to_use = "\\\\?\\" + directory_to_use
    # --------------------------------

    settings = QobuzDLSettings.from_arguments_configparser(arguments, config)
    settings.legacy_charmap = legacy_charmap

    # --- PRE-FLIGHT CONFIG CHECK ---
    formats_to_validate = {
        "folder_format": arguments.folder_format or folder_format,
        "track_format": arguments.track_format or track_format,
        "fallback_folder_format": config.get(
            section, "fallback_folder_format", fallback="{artist} - {album}"
        ),
        "multiple_disc_track_format": config.get(
            section,
            "multiple_disc_track_format",
            fallback="{disc_number}.{track_number} - {track_title}",
        ),
    }
    validate_config_formats(formats_to_validate)
    # -------------------------------

    qobuz = QobuzDL(
        directory_to_use,
        arguments.quality,
        arguments.embed_art or embed_art,
        ignore_singles_eps=arguments.albums_only or albums_only,
        no_m3u_for_playlists=arguments.no_m3u or no_m3u,
        quality_fallback=not arguments.no_fallback or not no_fallback,
        cover_og_quality=arguments.og_cover or og_cover,
        no_cover=arguments.no_cover or no_cover,
        downloads_db=None if no_database or arguments.no_db else QOBUZ_DB,
        folder_format=arguments.folder_format or folder_format,
        track_format=arguments.track_format or track_format,
        smart_discography=arguments.smart_discography or smart_discography,
        fetch_lyrics=fetch_lyrics,
        no_lrc_files=("--no-lrc-files" in sys.argv) or no_lrc_files_config,
        genius_token=genius_token,
        force_english=force_english,
        no_credits=no_credits_flag,
        settings=settings,
        booklet_only=getattr(arguments, "booklet_only", False),
        blacklist=getattr(arguments, "blacklist", None) or blacklist_config,
        playlist_as_albums=getattr(arguments, "playlist_as_albums", False) or
        playlist_as_albums_config,
    )

    await qobuz.initialize_client(email, password, app_id, secrets)

    try:
        await _handle_commands(qobuz, arguments)
    finally:
        if hasattr(qobuz, "client") and qobuz.client:
            await qobuz.client.close()


def main():
    """
    The synchronous wrapper that kicks off the async process.
    """
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
