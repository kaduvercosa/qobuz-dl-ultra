import os
import configparser
import shutil
from colorama import Style, Fore, init

init(autoreset=True)

# STYLE
DF = Style.NORMAL
BG = Style.BRIGHT
RESET = Style.RESET_ALL
OFF = Style.DIM

# Cores fixas (nao personalizaveis)
RED = Fore.RED
BLUE = Fore.BLUE
GREEN = Fore.GREEN
YELLOW = Fore.YELLOW
MAGENTA = Fore.MAGENTA

ERROR = Fore.RED
SUCCESS = Fore.GREEN
WARNING = Fore.YELLOW
WARNING_SAFE = Fore.LIGHTRED_EX
MUTED = Style.DIM

# Cor de destaque padrao (azul aco) -- pode ser sobrescrita pelo usuario
# no wizard de configuracao (qobuz-dl -r). O valor e' lido do config.ini
# na importacao do modulo, entao vale pra toda a sessao sem precisar
# passar o objeto de settings por todo o codigo.
_DEFAULT_ACCENT = "\033[38;2;95;168;211m"

# Paleta de cores predefinidas exposta pro wizard. Cada entrada:
#   (nome_exibicao, codigo_rgb_string, escape_ansi)
# O campo rgb_string e' o que vai gravado em config.ini: "R;G;B"
ACCENT_PRESETS = [
    ("Azul Aço     (padrão)", "95;168;211", "\033[38;2;95;168;211m"),
    ("Roxo Lavanda", "180;140;255", "\033[38;2;180;140;255m"),
    ("Verde Menta", "100;220;160", "\033[38;2;100;220;160m"),
    ("Laranja Âmbar", "255;165;80", "\033[38;2;255;165;80m"),
    ("Rosa Pastel", "255;130;180", "\033[38;2;255;130;180m"),
    ("Teal Aqua", "64;196;192", "\033[38;2;64;196;192m"),
    ("Dourado", "220;180;60", "\033[38;2;220;180;60m"),
    ("Coral", "255;100;100", "\033[38;2;255;100;100m"),
    ("Personalizada (RGB)...", None, None),
]


def _find_config_file():
    """Localiza o config.ini usando a mesma logica de utils.get_config_paths()
    mas sem importar utils (evita importacao circular no boot do modulo)."""
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    config_dir = os.environ.get("CONFIG_DIR")

    if not config_dir:
        if ios_home:
            config_dir = ios_home
        elif os.name == "nt":
            config_dir = os.environ.get("APPDATA", "")
        else:
            home_dir = os.environ.get("HOME", "")
            if "Containers/Data/Application" in home_dir:
                config_dir = os.path.join(home_dir, "Documents")
            else:
                config_dir = os.path.join(home_dir, ".config")

    return os.path.join(config_dir, "qobuz-dl", "config.ini")


def _load_accent() -> str:
    """Le accent_color do config.ini e retorna o escape ANSI correto.
    Falha silenciosamente pro padrao se o arquivo nao existir ou o valor
    estiver invalido -- nunca deve crashar o boot do programa."""
    try:
        cfg_file = _find_config_file()
        if not os.path.exists(cfg_file):
            return _DEFAULT_ACCENT

        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(cfg_file, encoding="utf-8")
        rgb = cfg.get("qobuz", "accent_color", fallback="").strip()

        if not rgb:
            return _DEFAULT_ACCENT

        parts = [int(x.strip()) for x in rgb.split(";")]
        if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
            r, g, b = parts
            return f"\033[38;2;{r};{g};{b}m"
    except Exception:
        pass

    return _DEFAULT_ACCENT


# Cor de destaque ativa -- lida uma vez na importacao do modulo.
# Todos os outros arquivos que fazem `from qobuz_dl.color import INFO as CYAN`
# recebem este valor automaticamente.
_ACCENT = _load_accent()

HIGHLIGHT = _ACCENT
INFO = _ACCENT
PROGRESS = _ACCENT


def accent_preview(escape: str, label: str = "Texto de exemplo") -> str:
    """Retorna uma string com preview do `escape` em fundo escuro E claro.
    Adapta automaticamente para o tamanho da tela (celular vs iPad/PC)."""
    cols, _ = shutil.get_terminal_size()

    dark_bg = "\033[40m"      # fundo preto
    light_bg = "\033[47m"     # fundo cinza claro para bom contraste em terminal branco
    dark_fg = "\033[37m"      # texto branco base
    light_fg = "\033[30m"     # texto preto base

    if cols < 70:
        # Modo responsivo para telas estreitas (ex: celular na vertical)
        dark = f"{dark_bg}{dark_fg} {escape}[FX] {dark_fg}The Weeknd {RESET}"
        light = f"{light_bg}{light_fg} {escape}[FX] {light_fg}The Weeknd {RESET}"

        # Quebra a linha e adiciona os exatos 30 espaços do prefixo do cli.py
        # para alinhar o "Claro:" perfeitamente embaixo do "Escuro:"
        return f"Escuro: {dark}\n{' ' * 30}Claro:  {light}"
    else:
        # Modo largo para telas maiores
        dark = f"{dark_bg}{dark_fg} -- {escape}[FAIXA] {dark_fg}ARTISTA {escape}The Weeknd{RESET}"
        light = f"{light_bg}{light_fg} -- {escape}[FAIXA] {light_fg}ARTISTA {escape}The Weeknd{RESET}"

        return f"Escuro: {dark}     Claro: {light}"
