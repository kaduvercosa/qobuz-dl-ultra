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
_DEFAULT_ACCENT_RGB = (95, 168, 211)

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


def _rgb_escape(r, g, b) -> str:
    return f"\033[38;2;{r};{g};{b}m"


def _darken(rgb: tuple, factor: float = 0.55) -> tuple:
    """Escurece um RGB multiplicando cada canal por 'factor' (0-1). Usado
    pra derivar uma variante mais escura da cor de destaque escolhida no
    wizard (qobuz-dl -r), sem precisar de uma segunda cor cadastrada a
    parte -- 1 escolha do usuario, 2 tons derivados automaticamente."""
    r, g, b = rgb
    return (
        max(0, min(255, int(r * factor))),
        max(0, min(255, int(g * factor))),
        max(0, min(255, int(b * factor))),
    )


def _load_accent_rgb() -> tuple:
    """Le accent_color do config.ini e retorna a tupla (r, g, b).
    Falha silenciosamente pro padrao se o arquivo nao existir ou o valor
    estiver invalido -- nunca deve crashar o boot do programa."""
    try:
        cfg_file = _find_config_file()
        if not os.path.exists(cfg_file):
            return _DEFAULT_ACCENT_RGB

        cfg = configparser.ConfigParser(interpolation=None)
        cfg.read(cfg_file, encoding="utf-8")
        rgb = cfg.get("qobuz", "accent_color", fallback="").strip()

        if not rgb:
            return _DEFAULT_ACCENT_RGB

        parts = [int(x.strip()) for x in rgb.split(";")]
        if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
            return tuple(parts)
    except Exception:
        pass

    return _DEFAULT_ACCENT_RGB


_ACCENT_RGB = _load_accent_rgb()
_ACCENT = _rgb_escape(*_ACCENT_RGB)


ACCENT_DARK = _rgb_escape(*_darken(_ACCENT_RGB))

HIGHLIGHT = _ACCENT
INFO = _ACCENT
PROGRESS = _ACCENT


def accent_preview(escape: str, label: str = "") -> str:
    """Retorna uma string com preview do `escape` em fundo escuro E claro.
    Adapta automaticamente para o tamanho da tela (celular vs iPad/PC)."""
    cols, _ = shutil.get_terminal_size((80, 24))

    dark_bg = "\033[40m"       # Fundo Preto
    light_bg = "\033[107m"     # Fundo Branco Brilhante
    dark_fg = "\033[97m"       # Texto Branco Brilhante
    light_fg = "\033[30m"      # Texto Preto

    # Aumentado para 115 colunas para acionar o modo escadinha no tablet em pé
    if cols < 115:
        # Modo responsivo: Quebra 2 linhas (\n\n) pra sair da frente do nome da cor.
        # Adiciona margem exata e \n no final para separar da próxima opção da lista.
        dark = f"{dark_bg} {escape}[FX] {dark_fg}The Weeknd {RESET}"
        light = f"{light_bg} {escape}[FX] {light_fg}The Weeknd {RESET}"

        return f"\n\n          Escuro:  {dark}\n          Claro:   {light}\n"
    else:
        # Modo largo (iPad na horizontal ou PC)
        dark = f"{dark_bg} {escape}[FAIXA] {dark_fg}ARTISTA {escape}The Weeknd {RESET}"
        light = f"{light_bg} {escape}[FAIXA] {light_fg}ARTISTA {escape}The Weeknd {RESET}"

        return f"  Escuro: {dark}    Claro: {light}"
