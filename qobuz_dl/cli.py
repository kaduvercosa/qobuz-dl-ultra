from qobuz_dl.utils import get_config_paths
import sys
# rapidfuzz no lugar de difflib: mesmo motivo do qopy.py (10-100x mais
# rapido, C++/Cython em vez de Python puro) -- aqui usado pra sugerir a
# variavel de formato correta quando o usuario digita uma errada no
# config.ini.
from rapidfuzz import process as fuzz_process, fuzz
import string
import configparser
import logging
import glob
import os
import send2trash
import signal
import shutil
import textwrap
import keyring
import httpx
import asyncio

from qobuz_dl.bundle import Bundle
# CYAN/YELLOW importados como INFO/WARNING renomeados: mesma cor de
# YELLOW (mantida por convencao), mas CYAN agora e' LIGHTBLUE_EX --
# visivel em terminal claro E escuro (CYAN puro quase some em fundo
# branco). Ver comentario completo em qobuz_dl/color.py. Zero mudanca
# de codigo neste arquivo: toda f-string que ja usa {CYAN}/{YELLOW}
# continua funcionando, so' a cor de fato renderizada muda.
from qobuz_dl.color import (
    GREEN, WARNING as YELLOW, RED, OFF, INFO as CYAN,
    RESET, BG, ACCENT_PRESETS, accent_preview,
)
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.settings import QobuzDLSettings

logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
)


logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

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
    using rapidfuzz to suggest typing corrections to the user.

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

    C_RED = RED
    C_YEL = YELLOW
    C_GRE = GREEN
    C_OFF = RESET

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

                    # process.extractOne() do rapidfuzz e' o equivalente ao
                    # difflib.get_close_matches(n=1, cutoff=0.6) -- cutoff
                    # aqui e' 0-100 (nao 0-1), por isso 60 em vez de 0.6.
                    # Retorna (match, score, index) ou None, em vez de uma
                    # lista.
                    best = fuzz_process.extractOne(
                        base_var, VALID_KEYS, scorer=fuzz.ratio, score_cutoff=60
                    )
                    if best:
                        print(
                            f"    {C_GRE}-> Did you mean '{{{
                                best[0]}}}'?{C_OFF}"
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


def _pick_accent_color() -> str:
    """
    Wizard interativo de escolha de cor de destaque.
    Mostra cada opcao com preview em fundo escuro E claro antes de confirmar.
    Retorna o valor RGB no formato "R;G;B" pronto pra gravar no config.ini.
    """
    print(f"\n{BG}[?] Cor de destaque do programa:{OFF}")
    print(f"    {OFF}Aparece em nomes de faixas, cabecalhos, barras e progresso.{OFF}")
    print()

    # Mostrar todas as opcoes com preview lado a lado
    for idx, (name, rgb, escape) in enumerate(ACCENT_PRESETS, 1):
        if escape:
            preview = accent_preview(escape, "━━ [FAIXA]  ARTISTA  The Weeknd")
            print(f"  {idx:2}. {name:<22} {preview}")
        else:
            print(f"  {idx:2}. {name}")

    print()
    while True:
        choice = input(
            f"Escolha (1-{len(ACCENT_PRESETS)}) [Enter = 1 padrao]: ").strip()
        if not choice:
            choice = "1"
        try:
            idx = int(choice)
            if 1 <= idx <= len(ACCENT_PRESETS):
                name, rgb, escape = ACCENT_PRESETS[idx - 1]
                break
        except ValueError:
            pass
        print(f"  Por favor escolha entre 1 e {len(ACCENT_PRESETS)}.")

    # Opcao de cor personalizada
    if rgb is None:
        print()
        print("  Digite os valores RGB separados por ponto e virgula.")
        print("  Exemplo: 255;100;50  (vermelho), 0;200;150  (teal)")
        print()
        while True:
            raw = input("  Codigo RGB (R;G;B): ").strip()
            # Aceita tanto "R;G;B" quanto "R,G,B" quanto "R G B"
            raw = raw.replace(",", ";").replace(" ", ";")
            parts_str = [x.strip() for x in raw.split(";") if x.strip()]
            try:
                parts = [int(x) for x in parts_str]
                if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                    rgb = ";".join(str(p) for p in parts)
                    escape = f"\033[38;2;{parts[0]};{parts[1]};{parts[2]}m"
                    break
            except ValueError:
                pass
            print("  Formato invalido. Use tres numeros de 0 a 255, ex: 150;80;220")

        # Mostrar preview da cor personalizada
        print()
        print(f"  Preview da sua cor:")
        print(accent_preview(escape, "━━ [FAIXA]  ARTISTA  The Weeknd"))
        print()
        confirm = input(
            "  Confirmar esta cor? (Enter = sim, n = escolher outra): ").strip().lower()
        if confirm in ("n", "nao", "no"):
            return _pick_accent_color()  # recomecar

    print(f"\n  {GREEN}Cor salva: {escape}━━ {name.strip()}{OFF}\n")
    return rgb


def _reset_config(config_file):
    """
    Interactive configuration wizard for initializing or resetting the config.ini file.
    """
    logging.info(f"\n{BG}[ QOBUZ-DL-ULTRA - CONFIGURAÇÃO INICIAL ]{OFF}")
    config = configparser.ConfigParser(interpolation=None)

    config["qobuz"] = {}

    # --- COR DE DESTAQUE ---
    accent_rgb = _pick_accent_color()
    config["qobuz"]["accent_color"] = accent_rgb

    C_ACCENT = f"\033[38;2;{accent_rgb}m"

    email = input("Enter your Qobuz email:\n- ").strip()
    config["qobuz"]["email"] = email

    print(
        f"\n{C_ACCENT}[!] ATENÇÃO: A API Qobuz bloqueou o login de senha direta para aplicativos de terceiros.{OFF}"
    )
    print(
        f"{C_ACCENT}[!] Você deve usar o Token de autenticação do seu navegador (F12 > Armazenamento > Armazenamento Local > usuário local > token).{OFF}"
    )

    auth_token = input("Cole o token do seu navegador aqui:\n- ").strip()

    config["qobuz"]["password"] = ""

    print(f"\n{C_ACCENT}[?] OS Keyring Security:{OFF}")
    print(f"    Por padrão, os tokens são criptografados no seu Gerenciador de Credenciais do Sistema Operacional.")
    print(f"    {OFF}Se você estiver em um Linux/NAS/Docker, isso pode falhar silenciosamente.{OFF}")
    disable_kr = (
        input(
            "    Desativar o Keyring do sistema operacional e salvar tokens no config.ini? (yes/no) [Padrão: no]\n- "
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
            "\nVocê quer baixar e injetar letras e tradução automaticamente? (yes/no) [Default: yes]\n- "
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
            f"\n{C_ACCENT}[!] Para usar o Genius como um fallback, insira seu Token de API. Deixe em branco para usar apenas LRCLIB (Free/No API).{OFF}"
        )
        genius_token = input("Genius API Token:\n- ").strip()

    if use_keyring and _keyring_save("genius_token", genius_token):
        config["qobuz"]["genius_token"] = ""
    else:
        config["qobuz"]["genius_token"] = genius_token

    config["qobuz"]["directory"] = (
        input("\nPasta de download (pressione Enter para 'Qobuz Downloads')\n- ") or
        "Qobuz Downloads"
    )

    config["qobuz"]["folder_format"] = (
        input(f"\nFormato da pasta (pressione Enter para '{DEFAULT_FOLDER}')\n- ") or
        DEFAULT_FOLDER
    )

    config["qobuz"]["default_quality"] = (
        input("\nQualidade do Download (5:MP3, 6:FLAC, 7:24b<96, 27:24b>96) [Padrão 27]\n- ") or
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

    logging.info(f"{C_ACCENT}Obtendo tokens. Por favor, aguarde...{OFF}")
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
        f"\n{GREEN}[+] Configuração salva com sucesso em {config_file}!{OFF}"
    )


def _remove_leftovers(directory):
    """Cleans up any partial or temporary files (~tmp_*.tmp) left behind after an interruption."""
    for pattern in [".*.tmp", "~tmp_*.tmp"]:
        search_dir = os.path.join(directory, "**", pattern)
        for i in glob.glob(search_dir, recursive=True):
            try:
                # send2trash em vez de os.remove: manda pra lixeira do SO
                # em vez de apagar de vez. Rede de seguranca barata -- se
                # um Ctrl+C for mal cronometrado e isto pegar um arquivo
                # que nao era pra pegar, ainda da pra recuperar.
                send2trash.send2trash(i)
            except Exception as e:
                logger.debug(f"Falha ao mover leftover '{i}' pra lixeira: {e}")


async def _handle_commands(qobuz, arguments):
    """Routes parsed command-line arguments to the appropriate QobuzDL core methods."""

    def sigint_handler(sig, frame):
        print(f"\n\n{RED}[!] Download forcibly interrupted by the user.{RESET}")
        print(
            f"{YELLOW}Partially downloaded files will be ignored or overwritten on the next run.{RESET}"
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
        fallback = f"{CYAN}{BG} QOBUZ-DL-ULTRA {OFF}"
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
        (
            "--find-duplicates [PATH]",
            "acha faixas duplicadas por fingerprint de audio (Chromaprint), nao so' tag",
        ),
        (
            "--watch [PATH]",
            "observa uma pasta e roda retro-tagging sozinho quando chegam arquivos novos",
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
    print(f"{CYAN}{BG}Uso: qobuz-dl <comando> [opcoes]{RESET}")
    print(f"     qobuz-dl <comando> --help   (lista todas as opcoes daquele comando)")
    print()

    print(f"{CYAN}{BG}Comandos:{RESET}\n")
    for name, aliases, desc in COMMANDS:
        label = name if not aliases else f"{name} ({aliases})"
        print(f"  {CYAN}{label}{OFF}")
        wrapped(desc, indent=4)
    print()

    print(f"{CYAN}{BG}Flags globais:{RESET} {OFF}(nao pertencem a nenhum comando especifico){OFF}\n")
    for flag, desc in FLAGS:
        print(f"  {CYAN}{flag}{OFF}")
        wrapped(desc, indent=4)
    rule("=")


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
    """Queries the GitHub API to notify the user of new Qobuz-DL Ultra Edition releases."""
    try:
        from qobuz_dl import __version__

        url = "https://api.github.com/repos/kaduvercosa/qobuz-dl-ultra/releases/latest"
        response = httpx.get(url, timeout=2)
        response.raise_for_status()

        latest_version_str = response.json().get("tag_name", "").replace("v", "")
        current_version_str = __version__

        latest_tuple = tuple(map(int, latest_version_str.split(".")))
        current_tuple = tuple(map(int, current_version_str.split(".")))

        if latest_tuple > current_tuple:
            print(
                f"\n{YELLOW}[*] UPDATE AVAILABLE: Ultra Edition v{latest_version_str} is out!{OFF}"
            )
            print(f"{YELLOW}    - PyPI: run 'pip install -U qobuz-dl-ultra'{OFF}")
            print(f"{YELLOW}    - Docker: pull the latest image{OFF}")

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
                f"\n\n{RED}[!] Radar manually interrupted by the user (CTRL+C).{RESET}"
            )
        sys.exit(0)
    # --------------------------------------------

    # --- STATS COMMAND INTEGRATION ---
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        from qobuz_dl.db import get_stats
        import shutil as _shutil

        s = get_stats(QOBUZ_DB)
        cols = min(_shutil.get_terminal_size((80, 24)).columns, 100)
        bar = "━" * cols
        div = "─" * cols

        def _row(label, value, label_w=30):
            if cols < 60:
                if label in ["Bit depths", "Sample rates"]:
                    # MODO CELULAR: Imprime o rótulo e coloca o valor embaixo, indetado
                    print(f"  {CYAN}{label}:{RESET}")
                    print(f"    {value}")
                else:
                    print(f"  {CYAN}{label}:{RESET} {value}")
            else:
                # MODO TABLET/PC: Lado a LAdo com alinhamento perfeito
                print(f"  {CYAN}{label:<{label_w}}{RESET}  {value}")

        print(f"\n{CYAN}{bar}{RESET}")
        print(f"{BG}{CYAN}{'  QOBUZ-DL-ULTRA  ·  STATISTICS':^{cols}}{RESET}")
        print(f"{CYAN}{bar}{RESET}\n")

        if not s or s.get("total", 0) == 0:
            print(
                f"  {YELLOW}Nenhum dado encontrado. Comece a baixar para popular as estatísticas!{RESET}")
            print(f"\n{CYAN}{bar}{RESET}\n")
            sys.exit(0)

        # --- Totais gerais ---
        print(f"  {BG}BIBLIOTECA{RESET}")
        _row("Total de downloads", str(s["total"]))
        _row("Álbuns", str(s["albums"]))
        _row("Faixas avulsas", str(s["tracks"]))
        _row("Artistas únicos", str(s["unique_artists"]))
        _row("Álbuns únicos", str(s["unique_albums"]))
        print()

        # --- Qualidade ---
        print(f"  {BG}QUALIDADE DE ÁUDIO{RESET}")
        total = s["total"] or 1
        hires_pct = s["hires"] * 100 // total
        met_pct = s["quality_met"] * 100 // total
        _row("Hi-Res (≥24bit)", f"{s['hires']}  ({hires_pct}%)")
        _row("Qualidade solicitada atingida", f"{s['quality_met']}  ({met_pct}%)")
        _row("Qualidade reduzida", f"{s['quality_not_met']}")

        if s["bit_depths"]:
            depths_str = "  /  ".join(
                f"{k}bit → {v}" for k, v in list(s["bit_depths"].items())[:5]
            )
            _row("Bit depths", depths_str)

        if s["sample_rates"]:
            rates_str = "  /  ".join(
                f"{k}kHz → {v}" for k, v in list(s["sample_rates"].items())[:5]
            )
            _row("Sample rates", rates_str)
        print()

        # --- Formatos ---
        print(f"  {BG}FORMATOS{RESET}")
        for fmt, cnt in s["formats"].items():
            pct = cnt * 100 // total
            _row(fmt, f"{cnt}  ({pct}%)")
        print()

        # --- Datas ---
        if s["oldest"] or s["newest"]:
            print(f"  {BG}PERÍODO{RESET}")

            def _fmt_date(d):
                if not d:
                    return "?"
                try:
                    y, m, day = d[:10].split("-")
                    return f"{day}/{m}/{y}"
                except Exception:
                    return d
            _row("Lançamento mais antigo", _fmt_date(s["oldest"]))
            _row("Lançamento mais recente", _fmt_date(s["newest"]))
            print()

        # --- Top artistas ---
        if s["top_artists"]:
            print(f"  {BG}TOP ARTISTAS{RESET}")
            top_cnt = s["top_artists"][0][1] or 1

            for rank, (artist, cnt) in enumerate(s["top_artists"], 1):
                if cols < 60:
                    # MODO CELULAR: Nome na primeira linha, barra recuada na linha de baixo
                    # Reduzimos o tamanho máximo da barra para 15 blocos para caber  com
                    # folga
                    max_blocks = 12
                    bar_len = cnt if top_cnt <= max_blocks else max(
                        1, cnt * max_blocks // top_cnt)
                    bar_vis = f"{CYAN}{('█|' * bar_len)[:-1]}{RESET}"
                    print(f"  {rank:>2}. {artist}")
                    print(f"      {bar_vis} {cnt}")
                else:
                    # MODO TABLET/PC: Tudo na mesma linha com alinhamento de 32 espaços
                    max_blocks = 20
                    bar_len = cnt if top_cnt <= max_blocks else max(
                        1, cnt * max_blocks // top_cnt)
                    bar_vis = f"{CYAN}{('█|' * bar_len)[:-1]}{RESET}"
                    print(f"  {rank:>2}. {artist:<32} {bar_vis} {cnt}")

        # --- Lista completa de artistas ---
        if len(sys.argv) > 2 and sys.argv[2] == "--artistas":
            print(f"  {BG}TODOS OS ARTISTAS ({s['unique_artists']}){RESET}")
            for a in s["artist_list"]:
                print(f"    · {a}")
            print()

        print(f"{CYAN}{bar}{RESET}\n")
        if len(sys.argv) <= 2 or sys.argv[2] != "--artistas":
            print(
                f"  {OFF}Dica: use  qobuz-dl stats --artistas  para ver a lista completa.{RESET}\n")
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
                    f"{YELLOW}[!] Notice: 'default_folder' in config.ini is deprecated. Please rename it to 'directory' for future updates.{RESET}"
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
            RED_C = RED
            YELLOW_C = YELLOW
            OFF_C = RESET
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

    # --- DUPLICATE DETECTION FEATURE (Audio Fingerprint) ---
    if getattr(arguments, "find_duplicates", None):
        from qobuz_dl.sync import find_duplicate_tracks

        dup_dir = (
            default_folder if arguments.find_duplicates == "DEFAULT" else arguments.find_duplicates
        )

        if os.name == "nt":
            dup_dir = os.path.abspath(dup_dir)
            if not dup_dir.startswith("\\\\?\\"):
                dup_dir = "\\\\?\\" + dup_dir

        await find_duplicate_tracks(dup_dir)
        sys.exit(0)
    # ----------------------------------------------

    # --- WATCH FOLDER FEATURE (retro-tagging automático) ---
    if getattr(arguments, "watch", None):
        from qobuz_dl.watcher import watch_directory
        from qobuz_dl.qopy import Client

        watch_dir = (
            default_folder if arguments.watch == "DEFAULT" else arguments.watch
        )
        watch_dir = os.path.expanduser(watch_dir)

        if os.name == "nt":
            watch_dir = os.path.abspath(watch_dir)
            if not watch_dir.startswith("\\\\?\\"):
                watch_dir = "\\\\?\\" + watch_dir

        lrc_pref = not config.getboolean(section, "no_lrc_files", fallback=False)
        embed_pref = config.getboolean(section, "embed_lyrics", fallback=True)
        trans_lang = config.get(section, "lyrics_translation_lang", fallback="pt")

        watch_settings = QobuzDLSettings(lrc_files=lrc_pref, embed_lyrics=embed_pref)
        watch_settings.lyrics_translation_lang = trans_lang
        watch_settings.default_folder = watch_dir

        # Client e' opcional aqui, igual no comando 'lyrics' -- sem ele, so
        # o fallback Genius/letras ja embutidas funciona, mas o watch nao
        # trava a inicializacao inteira por causa disso.
        watch_client = None
        try:
            watch_client = await Client.create(
                email,
                password,
                app_id,
                secrets,
                user_auth_token=token,
                force_english=force_english,
            )
        except Exception as e:
            logging.debug(f"Authentication warning for watch client: {e}")

        try:
            await watch_directory(
                watch_dir,
                client=watch_client,
                genius_token=genius_token,
                settings=watch_settings,
            )
        except KeyboardInterrupt:
            print(f"\n\n{RED}[!] Observação interrompida pelo usuário (CTRL+C).{RESET}")
        finally:
            if watch_client:
                await watch_client.close()
        sys.exit(0)
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
                f"\n\n{RED}[!] Operation manually interrupted by the user (CTRL+C).{RESET}"
            )
            print(f"{YELLOW}Already processed files are safe. Exiting...{RESET}")
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
