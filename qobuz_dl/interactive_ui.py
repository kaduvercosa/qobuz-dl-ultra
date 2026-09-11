# ============================================================================
# interactive_ui.py -- tema visual e helpers de layout puros da TUI de
# seleção em tela cheia (prompt_toolkit), extraídos de core.py.
#
# Passo 2 da quebra de core.py em módulos menores (core.py tinha 1873
# linhas). Escopo desta extração: os pedaços puros e já testados
# (_shade, _align_text, _get_table_layout) mais o tema (pt_style,
# prompt_style) e as constantes de cor que eles derivam -- tudo que roda
# sem estado externo e sem depender da classe QobuzDL.
#
# `_tui_select` (a função async que roda a tela em si -- key bindings,
# navegação, estado mutável, 800+ linhas) fica em core.py de propósito:
# ainda não tem cobertura de teste, e mover código de UI interativa sem
# rede de segurança é exatamente o tipo de risco que essa extração toda
# está tentando evitar. Fica pra um passo futuro, depois de escrever
# testes pra ela (bem mais trabalhoso que os três helpers daqui).
#
# `core.py` reexporta tudo daqui (import direto no topo do arquivo) --
# qualquer código que já fazia `from qobuz_dl.core import pt_style` (ou
# qualquer um dos outros nomes abaixo) continua funcionando sem mudar
# nada, incluindo os testes já escritos pra _shade/_align_text/
# _get_table_layout.
# ============================================================================
import re

from prompt_toolkit.styles import Style
from prompt_toolkit.utils import get_cwidth

from qobuz_dl.color import _ACCENT

# --------------------------------------------------------------------------
# Deriva o tema visual do prompt_toolkit (pt_style/prompt_style) a partir da
# MESMA cor de destaque escolhida pelo usuário no wizard (color.py -> _ACCENT).
# Assim a TUI de seleção fica com a cor consistente com o resto do programa,
# sem precisar duplicar a escolha de cor em outro lugar.
# --------------------------------------------------------------------------
_hex_accent = "#5fa8d3"
_darker_accent = "#4c86a8"

# Converte o escape ANSI TrueColor (\033[38;2;R;G;Bm) de volta pra hexadecimal
# (formato que o prompt_toolkit entende). Se _ACCENT vier vazio (cor
# desligada / --no-color / NO_COLOR=1), o regex não casa e os valores padrão
# de _r/_g/_b (derivados do hex fixo acima) são usados no lugar.
#
# BUGFIX: _hex_item_title e os _hex_type_* só eram definidos DENTRO do
# `if _match:` -- com cor desligada (ex.: qualquer teste, que roda com
# NO_COLOR=1) o regex nunca casava, essas variáveis nunca existiam, e o
# `Style.from_dict()` alguns parágrafos abaixo quebrava com
# `NameError: name '_hex_item_title' is not defined` -- ou seja, o
# programa inteiro não importava (nem para --no-color, nem em ambientes
# sem suporte a cor) por causa da tela de seleção interativa sequer
# tentar montar seu tema visual. Agora _r/_g/_b sempre têm um valor (do
# match ou do hex fixo default), e _shade()/_hex_item_title/_hex_type_*
# são calculados incondicionalmente.
_match = re.search(r"\033\[38;2;(\d+);(\d+);(\d+)m", _ACCENT)
if _match:
    _r, _g, _b = map(int, _match.groups())
    _hex_accent = f"#{_r:02x}{_g:02x}{_b:02x}"
    _darker_accent = f"#{int(_r * 0.8):02x}{int(_g * 0.8):02x}{int(_b * 0.8):02x}"
else:
    _r, _g, _b = 0x5F, 0xA8, 0xD3


def _shade(f):
    """Lighten (f>0) or darken (f<0) the accent color by mixing with white or black."""
    # Clareia (f > 0, mistura com branco) ou escurece (f < 0, mistura
    # com preto) a cor de destaque, usado pra diferenciar os "tipos"
    # de lançamento (álbum/EP/single/etc.) na TUI sem cadastrar uma
    # cor fixa pra cada tipo.
    #
    # Os únicos usos hoje ficam em [-0.3, 0.3] (ver constantes logo
    # abaixo), então esse clamp nunca dispara na prática -- mas sem ele,
    # um fator fora de [-1, 1] (ex.: 2.0) produz canal > 255, e
    # `f"{415:02x}"` vira "19f" (3 dígitos) em vez de 2, gerando uma
    # string de cor hex malformada tipo "#19f...". Clampar deixa a
    # função segura pra qualquer fator, não só os que o código chama hoje.
    if f > 0:
        r = _r + (255 - _r) * f
        g = _g + (255 - _g) * f
        b = _b + (255 - _b) * f
    else:
        r = _r * (1 + f)
        g = _g * (1 + f)
        b = _b * (1 + f)
    r, g, b = (max(0, min(255, int(c))) for c in (r, g, b))
    return f"#{r:02x}{g:02x}{b:02x}"


_hex_item_title = _hex_accent
_hex_type_album = _hex_accent
_hex_type_ep = _shade(0.2)
_hex_type_single = _shade(-0.2)
_hex_type_track = _shade(0.3)
_hex_type_comp = _shade(-0.3)

pt_style = Style.from_dict(
    {
        "title": f"fg:{_hex_accent} bold",
        "pointer": "ansiyellow bold",
        "checkbox": f"fg:{_hex_accent}",
        "hovered": f"bg:{_darker_accent} fg:#ffffff bold",
        "meta": "",
        "highlight": f"fg:{_hex_accent} bold",
        "footer": "ansiyellow",
        "table_header": "bold",
        "item_title": f"fg:{_hex_item_title} bold",
        "type_album": f"fg:{_hex_type_album}",
        "type_ep": f"fg:{_hex_type_ep}",
        "type_single": f"fg:{_hex_type_single}",
        "type_track": f"fg:{_hex_type_track}",
        "type_comp": f"fg:{_hex_type_comp}",
        "type_other": f"fg:{_hex_type_track}",
    }
)

prompt_style = Style.from_dict(
    {
        "prompt_text": "fg:#ffffff bold",
        "prompt_hint": "fg:#888888",
        "prompt_cursor": f"fg:{_hex_accent} bold",
    }
)


def _align_text(text, width):
    """Corta o texto (respeitando largura visual de emojis/acentos via
    get_cwidth) se ele não couber em `width`, adicionando "...", ou
    completa com espaços se sobrar espaço. Usado em toda coluna de tabela
    da TUI para manter as colunas alinhadas."""
    text = str(text) if text is not None else ""
    current_w = get_cwidth(text)
    if current_w > width:
        res = ""
        w = 0
        for char in text:
            cw = get_cwidth(char)
            if w + cw > width - 3:
                return res + "..."
            res += char
            w += cw
        return res
    return text + " " * (width - current_w)


def _get_table_layout(columns, is_multi, item_category):
    """Decide se a tela é larga o bastante pra mostrar uma TABELA (>=78
    colunas) ou se deve cair no modo "cartão" (mais compacto, usado em
    telas estreitas/celular). Também calcula a largura de cada coluna com
    base no espaço disponível e desenha as bordas ┌─┬─┐ / ├─┼─┤ / └─┴─┘.

    Retorna: (is_table, larguras_das_colunas, cabeçalhos, bordas_prontas)
    Se a tela for estreita ou item_category == "filter" (menus simples de
    sim/não), retorna is_table=False e o restante vazio.

    Para adicionar uma nova categoria de item na TUI: seguir o padrão dos
    blocos elif abaixo (album/track/playlist/artist), definindo larguras
    fixas + "flex" pra coluna de texto livre (título/nome).
    """
    is_table = columns >= 78
    if not is_table or item_category == "filter":
        return False, [], [], {}

    prefix_len = 5 if is_multi else 3
    # Overhead fixo por linha (fora do prefixo e das larguras de coluna):
    # "│ " no início + " │" no fim = 4 caracteres. Antes este valor estava
    # como 6, dois a mais que o real, fazendo a tabela inteira (bordas e
    # linhas) terminar 2 colunas antes do fim do terminal. Como add_line()
    # sempre preenche o resto da linha até `columns` com o estilo de
    # destaque quando a linha está "hovered", essas 2 colunas sobrando
    # ficavam pintadas com a cor de seleção *depois* do "│" direito,
    # dando a impressão de que a borda da tabela "vazava" ou terminava
    # no lugar errado.
    safe_columns = columns - prefix_len - 4

    if item_category == "album":
        fixed_cols_w = 12 + 4 + 6 + 12
        separators = 5 * 3
        fixed = fixed_cols_w + separators
        flex = max(10, safe_columns - fixed)
        w_tit = int(flex * 0.55)
        w_art = flex - w_tit
        widths = [w_tit, w_art, 12, 4, 6, 12]
        headers = ["ÁLBUM", "ARTISTA", "TIPO", "ANO", "FAIXAS", "QUALIDADE"]

    elif item_category == "track":
        fixed_cols_w = 12 + 10 + 12
        separators = 5 * 3
        fixed = fixed_cols_w + separators
        flex = max(15, safe_columns - fixed)
        w_tit = int(flex * 0.40)
        w_art = int(flex * 0.30)
        w_alb = flex - w_tit - w_art
        widths = [w_tit, w_art, w_alb, 12, 10, 12]
        headers = ["FAIXA", "ARTISTA", "ÁLBUM", "TIPO", "DURAÇÃO", "QUALIDADE"]

    elif item_category == "playlist":
        fixed_cols_w = 6 + 10
        separators = 3 * 3
        fixed = fixed_cols_w + separators
        flex = max(10, safe_columns - fixed)
        w_nom = int(flex * 0.60)
        w_own = flex - w_nom
        widths = [w_nom, w_own, 6, 10]
        headers = ["NOME DA PLAYLIST", "CRIADOR", "FAIXAS", "DURAÇÃO"]

    elif item_category == "artist":
        fixed_cols_w = 15
        separators = 1 * 3
        fixed = fixed_cols_w + separators
        flex = max(10, safe_columns - fixed)
        widths = [flex, 15]
        headers = ["NOME DO ARTISTA", "LANÇAMENTOS"]

    else:
        return False, [], [], {}

    top_border = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    mid_border = "+-" + "-+-".join("-" * w for w in widths) + "-+"
    bot_border = "+-" + "-+-".join("-" * w for w in widths) + "-+"

    return (
        True,
        widths,
        headers,
        {"top": top_border, "mid": mid_border, "bot": bot_border},
    )
