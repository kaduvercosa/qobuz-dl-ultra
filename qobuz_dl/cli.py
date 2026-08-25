from qobuz_dl.utils import get_config_paths, checar_binarios_externos
import sys
import argparse

# BUGFIX: era `from rapidfuzz import process as fuzz_process, fuzz`, um import
# no topo do arquivo. Como o rapidfuzz e' um pacote compilado, isso tornava o
# CLI inteiro impossivel de importar em ambientes que so' aceitam pacotes
# Python puros (a-Shell no iPad). Nem `--help` rodava. Agora passa pelo
# qobuz_dl.fuzzy, que usa rapidfuzz quando existe e difflib quando nao.
from qobuz_dl import fuzzy

# Comparacao de versao conforme a especificacao da PyPA (ver check de update
# mais abaixo). Pacote Python puro, sem dependencias proprias.
from packaging.version import Version
import string
import configparser
import logging
import glob
import os
import send2trash
import signal
import time
import keyring
import httpx
import asyncio

from qobuz_dl.bundle import Bundle
from qobuz_dl.color import (
    GREEN,
    WARNING as YELLOW,
    RED,
    OFF,
    INFO as CYAN,
    RESET,
    BG,
    ACCENT_PRESETS,
    accent_preview,
    HIGHLIGHT as ACCENT,
)
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl import ui

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


def _bootstrap_ui():
    """Configura a camada de saída antes de o argparse rodar.

    As flags ``--quiet/--verbose/--no-color`` precisam valer já na tela
    inicial e nas mensagens de erro de configuração, que acontecem ANTES do
    ``parse_args()``. Por isso este único ponto olha ``sys.argv`` direto --
    e só para decidir verbosidade/cor, nunca para despachar comandos.
    """
    argv = sys.argv[1:]
    ui.configure(
        quiet="--quiet" in argv,
        verbose=("--verbose" in argv or "-v" in argv),
        color=False if "--no-color" in argv else None,
    )
    ui.install_logging()


# REFATORACAO: antes era um `logging.basicConfig(level=INFO, ...)` cru, cujo
# StreamHandler escrevia direto em stderr -- por cima das barras de progresso
# do tqdm sempre que core.py/sync*.py logavam algo durante um download.
# Agora todo o logging passa pelo `ui.TqdmLoggingHandler`, ou seja pelo mesmo
# lock e pelo mesmo `tqdm.write` usados pelo downloader.
_bootstrap_ui()


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
    (ver qobuz_dl.fuzzy) to suggest typing corrections to the user.

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

                    best = fuzzy.melhor_match(base_var, VALID_KEYS, corte=0.6)
                    if best:
                        print(f"    {C_GRE}-> Você quis dizer '{{{best}}}'?{C_OFF}")

                    print(
                        f"    {C_RED}-> Isto fará com que toda a string de formato seja descartada durante o download.{C_OFF}"
                    )
                    has_errors = True

        except ValueError as e:
            print(
                f"{C_RED}[!] Erro de Configuração: erro de sintaxe em '{config_name}' -> {e}{C_OFF}"
            )
            has_errors = True

    if has_errors:
        print(
            f"\n{C_YEL}[*] Dica: Verifique seu arquivo config.ini ou seus argumentos de linha de comando e corrija quaisquer erros de digitação antes de baixar.{C_OFF}\n"
        )
        sys.exit(1)


def _pick_accent_color() -> str:
    """
    Wizard interativo de escolha de cor de destaque.
    Mostra cada opcao com preview em fundo escuro E claro antes de confirmar.
    Retorna o valor RGB no formato "R;G;B" pronto pra gravar no config.ini.
    """
    print(f"\n{BG}[?] Cor de destaque do programa:{OFF}")
    # BUGFIX (tela estreita): esta explicacao era um literal de 63 colunas
    # impresso cru, estourando em qualquer terminal abaixo disso. Agora
    # quebra na largura real, como o resto da interface.
    ui.wrapped(
        "Aparece em nomes de faixas, cabecalhos, barras e progresso.", indent=4
    )
    print()

    # Mostrar todas as opcoes com preview lado a lado
    for idx, (name, _rgb, escape) in enumerate(ACCENT_PRESETS, 1):
        if escape:
            preview = accent_preview(escape, "━━ [FAIXA]  ARTISTA  The Weeknd")
            print(f"  {idx:2}. {name:<22} {preview}")
        else:
            print(f"  {idx:2}. {name}")

    print()
    while True:
        # BUGFIX (tela estreita): o prompt tinha 34 colunas fixas e estourava
        # em 32. Em tela apertada usa a forma curta -- prompt de `input()` NAO
        # pode ser quebrado em varias linhas sem separar a pergunta do cursor.
        _n = len(ACCENT_PRESETS)
        prompt = f"Escolha (1-{_n}) [Enter = 1 padrao]: "
        if len(prompt) > ui.width():
            prompt = f"Escolha 1-{_n}: "
        choice = input(prompt).strip()
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
        print("  Preview da sua cor:")
        print(accent_preview(escape, "━━ [FAIXA]  ARTISTA  The Weeknd"))
        print()
        confirm = (
            input("  Confirmar esta cor? (Enter = sim, n = escolher outra): ")
            .strip()
            .lower()
        )
        if confirm in ("n", "nao", "no"):
            return _pick_accent_color()  # recomecar

    print(f"\n  {GREEN}Cor salva: {escape}━━ {name.strip()}{OFF}\n")
    return rgb


def _reset_config(config_file):
    """
    Interactive configuration wizard for initializing or resetting the config.ini file.
    """
    # BUGFIX (tela estreita): titulo fixo de 41 colunas. Em tela apertada cai
    # para a forma curta em vez de vazar para a linha de baixo.
    if ui.width() >= 41:
        logging.info(f"\n{BG}[ QOBUZ-DL-ULTRA - CONFIGURAÇÃO INICIAL ]{OFF}")
    else:
        logging.info(f"\n{BG}[ CONFIGURAÇÃO INICIAL ]{OFF}")
    config = configparser.ConfigParser(interpolation=None)

    config["qobuz"] = {}

    # --- COR DE DESTAQUE ---
    accent_rgb = _pick_accent_color()
    config["qobuz"]["accent_color"] = accent_rgb

    C_ACCENT = f"\033[38;2;{accent_rgb}m" if accent_rgb else YELLOW

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
    print(
        "    Por padrão, os tokens são criptografados no seu Gerenciador de Credenciais do Sistema Operacional."
    )
    print(
        f"    {OFF}Se você estiver em um Linux/NAS/Docker, isso pode falhar silenciosamente.{OFF}"
    )
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
        input(
            "\nQualidade do Download (5:MP3, 6:FLAC, 7:24b<96, 27:24b>96) [Padrão 27]\n- "
        ) or
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

    logging.info(f"\n{C_ACCENT}Obtendo tokens. Por favor, aguarde...{OFF}")
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

    with open(config_file, "w", encoding="utf-8") as configfile:
        config.write(configfile)

    logging.info(f"\n{GREEN}[+] Configuração salva com sucesso em {config_file}!{OFF}")

    global ACCENT, CYAN
    if accent_rgb:
        nova_cor = f"\033[38;2;{accent_rgb}m"
        ACCENT = nova_cor
        CYAN = nova_cor

    # BUGFIX: faltava o prefixo `f`, então o terminal imprimia literalmente
    # "{ACCENT}...{OFF}" em vez de aplicar as cores.
    print(f"{ACCENT}\n [*] Atualizando interface...{OFF}", end="", flush=True)

    time.sleep(2.0)

    print("\r\033[K", end="")

    _print_welcome_screen()


def _remove_leftovers(directory):
    """Cleans up any partial or temporary files (~tmp_*.tmp) left behind after an interruption."""
    for pattern in [".*.tmp", "~tmp_*.tmp"]:
        search_dir = os.path.join(directory, "**", pattern)
        for i in glob.glob(search_dir, recursive=True):
            try:

                send2trash.send2trash(i)
            except Exception as e:
                logger.debug(f"Falha ao mover leftover '{i}' pra lixeira: {e}")


async def _handle_commands(qobuz, arguments):
    """Routes parsed command-line arguments to the appropriate QobuzDL core methods."""

    def sigint_handler(sig, frame):
        print(f"\n\n{RED}[!] Download interrompido à força pelo usuário.{RESET}")
        print(
            f"{YELLOW}Arquivos parcialmente baixados serão ignorados ou substituídos na próxima execução.{RESET}"
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
        elif arguments.command in ("import-playlist", "ip"):
            await qobuz.import_playlist_from_url_or_file(
                source=arguments.SOURCE,
                name=getattr(arguments, "name", None),
                auto=getattr(arguments, "auto", False),
            )
        else:
            qobuz.interactive_limit = arguments.limit
            await qobuz.interactive()

    except KeyboardInterrupt:
        pass
    finally:
        _remove_leftovers(qobuz.directory)


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

        fallback_text = " QOBUZ-DL-ULTRA "
        pad = " " * max((cols - len(fallback_text)) // 2, 0)
        print(f"{pad}{ACCENT}{BG}{fallback_text}{OFF}")


def _extract_subcommands(parser):
    """
    Le os subcomandos DIRETO do parser real (commands.py) -- nome
    principal, aliases e o texto de help que foi passado pra
    add_parser(). Isso garante que a tela inicial nunca fica desatualizada
    silenciosamente: se um comando existe no parser, ele SEMPRE aparece
    aqui, mesmo que ninguem lembre de atualizar uma lista escrita a mao
    em algum outro lugar (foi exatamente isso que aconteceu com
    --find-duplicates/--watch antes desta funcao existir).

    Retorna: [(nome_principal, "alias1, alias2" ou None, help_text), ...]
    na ordem de registro.
    """
    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparsers_action = action
            break
    if subparsers_action is None:
        return []

    result = []

    for choice_action in subparsers_action._choices_actions:
        primary = choice_action.dest
        subparser = subparsers_action.choices[primary]

        aliases = [
            name
            for name, sp in subparsers_action.choices.items()
            if sp is subparser and name != primary
        ]
        alias_str = ", ".join(aliases) if aliases else None
        result.append((primary, alias_str, choice_action.help))
    return result


def _extract_global_flags(parser):
    """
    Le as flags globais (fora de qualquer subcomando) DIRETO do parser
    real. Retorna [(flag_str, dest, help_text), ...] na ordem de
    registro. flag_str ja' inclui o hint "[METAVAR]" quando a opcao
    aceita um valor opcional (ex.: "--watch [PATH]"), igual o estilo que
    a tela ja usava antes.
    """
    result = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        flag_str = ", ".join(action.option_strings)
        if action.nargs == "?" and action.metavar:
            flag_str += f" [{action.metavar}]"
        result.append((flag_str, action.dest, action.help))
    return result


_COMMAND_DESCRIPTIONS_PT = {
    "dl": "Baixa por URL de álbum, faixa, artista, gravadora ou playlist do Qobuz, ou um arquivo de texto com uma lista dessas URLs.",
    "interactive": "Busca interativa: procura faixas/álbuns e escolhe o que baixar na hora.",
    "lucky": "Baixa os N primeiros resultados de uma busca no Qobuz, sem passar URL.",
    "lyrics": "Varre uma pasta já baixada e injeta letras/traduções que estejam faltando.",
    "sync-playlist": "Sincroniza uma pasta local com uma playlist do Qobuz (baixa o que falta, remove o que saiu).",
    "import-playlist": "Importa um arquivo de playlist (TXT, CSV, JSON) de qualquer plataforma para download.",
    "radar": "Monitora e intercepta links copiados para download automático.",
    "stats": "Mostra estatísticas detalhadas sobre sua biblioteca e downloads efetuados.",
}

_FLAG_DESCRIPTIONS_PT = {
    "reset": "cria/reseta o arquivo de configuração",
    "purge": "apaga o banco de downloads-já-feitos",
    "sync_db": "escaneia uma pasta local pra recuperar IDs do Qobuz perdidos no banco",
    "find_duplicates": "acha faixas duplicadas por fingerprint de áudio (Chromaprint), não só tag",
    "watch": "observa uma pasta e roda retro-tagging sozinho quando chegam arquivos novos",
    "show_config": "mostra a configuração atual",
}


def _print_welcome_screen():
    """
    Tela inicial mostrada quando `qobuz-dl` roda sem nenhum argumento.
    Troca o print_help() cru do argparse (que ignora a largura real do
    terminal e derrama linha gigante em telas estreitas -- iPad em Split
    View, a-Shell mini, etc.) por um layout que se adapta a QUALQUER
    largura de terminal, igual o `_get_safe_ncols()` que o downloader.py
    ja' usa pras barras de progresso.

    Comandos e flags sao lidos DIRETO do parser real (ver
    _extract_subcommands/_extract_global_flags) em vez de mantidos numa
    lista separada -- evita a tela ficar desatualizada silenciosamente
    quando um comando/flag novo e' adicionado em commands.py.
    """
    from qobuz_dl import __version__

    # REFATORACAO: esta funcao calculava a propria largura e definia os
    # proprios `rule()`/`wrapped()` locais -- um dos 7 lugares do projeto que
    # faziam isso com limites diferentes. Agora usa a camada unica ui.py.
    cols = ui.width()

    def rule(ch="-"):
        ui.rule(ch)

    def wrapped(text, indent):
        ui.wrapped(text, indent=indent)

    print()
    _print_logo(cols)
    version_line = f"v{__version__}"
    # A versao fica em texto limpo: quem carrega a identidade visual e' a
    # logo logo acima. Antes saia esmaecida por causa do `OFF`/`Style.DIM`.
    print(f"{RESET}{version_line.center(cols)}")
    print()
    rule("=")
    # Titulos de secao usam SO' negrito, sem cor: na tela inicial a cor fica
    # reservada para a logo e para os comandos/flags -- o que o usuario
    # precisa localizar e digitar. Negrito ja' da' a hierarquia.
    print(f"{BG}Uso: qobuz-dl <comando> [opções]{OFF}")
    # BUGFIX: estas duas linhas eram literais fixos e estouravam a tela em
    # terminais estreitos (a de `--help` tem 71 caracteres). Agora quebram
    # na largura real.
    ui.wrapped("qobuz-dl <comando> --help  (lista todas as opções daquele comando)", indent=5)
    print()

    parser = qobuz_dl_args()

    print(f"{BG}Comandos:{OFF}\n")
    for name, aliases, help_text in _extract_subcommands(parser):
        label = name if not aliases else f"{name} ({aliases})"
        desc = _COMMAND_DESCRIPTIONS_PT.get(name)
        if desc is None:
            logger.debug(
                f"Comando '{name}' sem descrição PT-BR cadastrada na tela "
                f"inicial, usando help do argparse como fallback."
            )
            desc = help_text or ""
        print(f"  {ACCENT}{label}{OFF}")
        wrapped(desc, indent=4)
    print()

    if cols >= 62:
        print(
            f"{BG}Flags globais:{RESET} "
            f"{OFF}(não pertencem a nenhum comando específico){OFF}\n"
        )
    else:
        # Em telas estreitas o parênteses explicativo ia para a linha de baixo
        # sem recuo; melhor omiti-lo e manter só o título da seção.
        print(f"{BG}Flags globais:{RESET}\n")
    for flag_str, dest, help_text in _extract_global_flags(parser):
        desc = _FLAG_DESCRIPTIONS_PT.get(dest)
        if desc is None:
            logger.debug(
                f"Flag '{flag_str}' sem descrição PT-BR cadastrada na tela "
                f"inicial, usando help do argparse como fallback."
            )
            desc = help_text or ""
        print(f"  {ACCENT}{flag_str}{OFF}")
        wrapped(desc, indent=4)
    print()

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

        latest_version_str = response.json().get("tag_name", "").lstrip("vV")
        current_version_str = __version__

        # BUGFIX: era `tuple(map(int, s.split(".")))` nas duas versoes. Isso
        # levanta ValueError em qualquer tag que nao seja puramente numerica
        # -- "2.5.0-rc1", "2.5.0b1", "2.5.0.post1" -- e como a funcao inteira
        # estava dentro de um `except Exception: pass`, o erro era engolido e
        # o aviso de atualizacao simplesmente nunca aparecia, sem pista
        # nenhuma de que tinha falhado. Testado: 3 de 7 tags plausiveis
        # quebravam.
        #
        # `packaging.version.Version` implementa a especificacao de versao da
        # PyPA (a mesma que o pip usa), entende pre/post/dev-release e ordena
        # corretamente. Tambem trocado `.replace("v", "")` por `.lstrip("vV")`:
        # o replace removia TODO "v" da string, entao uma tag como "2.5.0-dev"
        # continuava certa mas "v2.5.0-preview" virava "2.5.0-preiew".
        versao_remota = Version(latest_version_str)
        versao_local = Version(current_version_str)

        if versao_remota > versao_local:
            print(
                f"\n{YELLOW}[*] ATUALIZAÇÃO DISPONÍVEL: Ultra Edition v{latest_version_str} está disponível!{OFF}"
            )
            print(f"{YELLOW}    - PyPI: rode 'pip install -U qobuz-dl-ultra'{OFF}")
            print(f"{YELLOW}    - Docker: puxe a imagem mais recente{OFF}")

    except Exception as e:
        # Continua sem incomodar quem so' quer baixar musica (sem rede, atras
        # de firewall, GitHub fora do ar -- nada disso deve poluir a saida).
        # Mas nao engole mais em silencio: com --verbose o motivo aparece.
        # Erro silencioso e' bug que nunca se descobre; foi exatamente assim
        # que o ValueError acima passou despercebido.
        logger.debug("Checagem de atualizacao falhou: %s: %s", type(e).__name__, e)


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

    # --- Comandos que nao precisam de login no Qobuz ---
    # REFATORACAO: `radar` e `stats` eram despachados por sniffing de
    # `sys.argv[1]`, e `--artistas` por `sys.argv[2]`. Consequencias reais:
    #   * `qobuz-dl --quiet stats` NAO caia no atalho (argv[1] era "--quiet"),
    #     seguia para o fluxo normal e tentava logar no Qobuz -- estourando
    #     AuthenticationError num comando que so' le' o banco local;
    #   * qualquer flag antes do subcomando quebrava os dois comandos;
    #   * `--artistas` so' funcionava se fosse exatamente o 2o argumento.
    # Agora o argparse resolve o nome do comando (ele ja' conhece os aliases)
    # e o despacho usa `arguments.command`.
    offline_args, _unknown = qobuz_dl_args().parse_known_args()
    offline_command = getattr(offline_args, "command", None)

    if offline_command == "radar":
        from qobuz_dl.radar import run_radar

        try:
            await run_radar()
        except KeyboardInterrupt:
            ui.blank()
            ui.error("Radar interrompido manualmente pelo usuário (CTRL+C).")
        sys.exit(0)

    if offline_command == "stats":
        from qobuz_dl.stats_view import render_stats

        sys.exit(
            render_stats(
                QOBUZ_DB,
                show_all_artists=getattr(offline_args, "artistas", False),
            )
        )
    # ---------------------------------------------------

    config = configparser.ConfigParser(interpolation=None)
    # BUGFIX: sem encoding explicito o configparser usa a codificacao local
    # (cp1252 no Windows), o que estoura UnicodeDecodeError em qualquer
    # config.ini com acento (ex.: diretorio "C:\Musica\Coleção").
    config.read(CONFIG_FILE, encoding="utf-8")

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
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
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
                    f"{YELLOW}[!] Aviso: 'default_folder' em config.ini está obsoleto. Por favor, renomeie-o para 'directory' para atualizações futuras.{RESET}"
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
        # NOTA: `no_lrc_files` do config.ini nao e' lido aqui de proposito.
        # Antes existia um `no_lrc_files_config` que era atribuido e nunca
        # usado -- a resolucao CLI+config e' feita por QobuzDLSettings
        # (settings.lrc_files), que e' a unica fonte de verdade. Ler o valor
        # duas vezes era justamente o que causava o bug de `--no-lrc-files`
        # nao conseguir sobrepor o config.
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
                f"{RED_C}Configuração inválida ou corrompida ({error}).\n{OFF_C}"
                f"{YELLOW_C}Rode 'python -m qobuz_dl -r' para consertar isto.{OFF_C}"
            )

    if arguments.reset:
        # BUGFIX: `_reset_config()` retorna None, e `sys.exit(None)` funciona
        # por acidente. Deixar explicito que o codigo de saida e' 0.
        _reset_config(CONFIG_FILE)
        sys.exit(0)

    if arguments.show_config:
        print(f"Configuração: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            print(f.read())
        sys.exit(0)

    if arguments.purge:
        try:
            os.remove(QOBUZ_DB)
        except FileNotFoundError:
            pass
        sys.exit(f"{GREEN}O banco de dados foi deletado.{OFF}")

    # Checagem de pre-voo dos executaveis que o pip NAO instala: o ffmpeg
    # (integridade do audio) e, apenas quando --find-duplicates foi pedido, o
    # fpcalc/Chromaprint. Avisa UMA vez, com a instrucao de instalacao.
    #
    # ANTES: a ausencia do ffmpeg so' aparecia arquivo por arquivo, dentro de
    # verify_audio_integrity(), como FileNotFoundError -- um album de 14
    # faixas gerava 14 mensagens que pareciam 14 arquivos corrompidos.
    #
    # A POSICAO AQUI E' DELIBERADA e foi corrigida: esta chamada estava mais
    # abaixo, depois do bloco de --find-duplicates. Como aquele bloco termina
    # em sys.exit(), o aviso de fpcalc nunca era alcancado por quem usava
    # justamente a feature que precisa dele. Tem que vir ANTES de todos os
    # branches que saem do programa (--sync-db, --find-duplicates, --watch).
    checar_binarios_externos(
        precisa_fpcalc=bool(getattr(arguments, "find_duplicates", None))
    )

    # --- NEW DB SYNC FEATURE (Lightweight Mode) ---
    if getattr(arguments, "sync_db", None):
        from qobuz_dl.sync import sync_database
        from qobuz_dl.qopy import Client
        from qobuz_dl.db import create_db  # <-- ADICIONE ESTA LINHA

        create_db(QOBUZ_DB)

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
        sys.exit(
            f"\n{GREEN}Sincronização do banco de dados concluída com sucesso.{OFF}"
        )
    # ----------------------------------------------

    # --- DUPLICATE DETECTION FEATURE (Audio Fingerprint) ---
    if getattr(arguments, "find_duplicates", None):
        # Import com mensagem: o pyacoustid e' extra opcional (`[duplicates]`)
        # porque so' serve pra esta feature. Antes um ImportError cru vazava
        # como traceback, sem dizer o que instalar.
        try:
            from qobuz_dl.sync import find_duplicate_tracks
        except ImportError as e:
            sys.exit(
                f"{RED}[!] --find-duplicates precisa do extra 'duplicates'.{RESET}\n"
                f"    Instale com: pip install 'qobuz-dl-ultra[duplicates]'\n"
                f"    E o binario Chromaprint: apt install libchromaprint-tools\n"
                f"    (detalhe tecnico: {e})"
            )

        dup_dir = (
            default_folder
            if arguments.find_duplicates == "DEFAULT"
            else arguments.find_duplicates
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
        # Mesma ideia do --find-duplicates: o watchdog e' extra `[watch]`.
        try:
            from qobuz_dl.watcher import watch_directory
        except ImportError as e:
            sys.exit(
                f"{RED}[!] --watch precisa do extra 'watch'.{RESET}\n"
                f"    Instale com: pip install 'qobuz-dl-ultra[watch]'\n"
                f"    (detalhe tecnico: {e})"
            )
        from qobuz_dl.qopy import Client

        watch_dir = default_folder if arguments.watch == "DEFAULT" else arguments.watch
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
            logging.debug(f"Aviso de autenticação para o cliente de letras: {e}")

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
            logging.debug(f"Aviso de autenticação para o cliente de letras: {e}")

        try:
            await inject_lyrics_retroactively(
                target_dir,
                client=lyrics_client,
                genius_token=genius_token,
                settings=local_settings,
            )
        except KeyboardInterrupt:
            print(
                f"\n\n{RED}[!] Operação interrompida manualmente pelo usuário (CTRL+C).{RESET}"
            )
            print(f"{YELLOW}Os arquivos já processados estão seguros. Saindo...{RESET}")
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
        "folder_format": getattr(arguments, "folder_format", None) or folder_format,
        "track_format": getattr(arguments, "track_format", None) or track_format,
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
        getattr(arguments, "quality", None) or default_quality,
        getattr(arguments, "embed_art", None) or embed_art,
        ignore_singles_eps=getattr(arguments, "albums_only", False) or albums_only,
        no_m3u_for_playlists=getattr(arguments, "no_m3u", False) or no_m3u,
        # BUGFIX: era `not arg or not cfg`, o que resulta em True sempre que
        # apenas um dos dois está ligado -- ou seja, `--no-fallback` e a opção
        # `no_fallback` do config.ini NUNCA desligavam o fallback de qualidade.
        quality_fallback=not (getattr(arguments, "no_fallback", False) or no_fallback),
        cover_og_quality=getattr(arguments, "og_cover", None) or og_cover,
        no_cover=getattr(arguments, "no_cover", False) or no_cover,
        downloads_db=(
            None if no_database or getattr(arguments, "no_db", False) else QOBUZ_DB
        ),
        folder_format=getattr(arguments, "folder_format", None) or folder_format,
        track_format=getattr(arguments, "track_format", None) or track_format,
        smart_discography=getattr(arguments, "smart_discography", False) or
        smart_discography,
        fetch_lyrics=fetch_lyrics,
        # BUGFIX: antes fazia sniffing manual de `"--no-lrc-files" in sys.argv`
        # porque o merge em settings.py estava quebrado. Agora que
        # `settings.lrc_files` resolve CLI+config corretamente, usamos ele.
        no_lrc_files=not settings.lrc_files,
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
