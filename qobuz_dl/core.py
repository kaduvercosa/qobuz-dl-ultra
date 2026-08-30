# ==============================================================================
# MÓDULO: core.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: "Motor" principal do programa. Contém:
#   1. A interface de seleção interativa em tela cheia (TUI, feita com
#      prompt_toolkit) usada nos modos "interactive" e ao explorar um artista.
#   2. A classe QobuzDL: orquestra buscas, downloads (sequenciais e
#      paralelos), importação de playlists externas e o modo interativo.
#
# ONDE PROCURAR quando precisar mexer em algo:
#   TUI (tela de seleção com setas/espaço/enter):
#   - Layout de colunas da tabela (larguras, cabeçalhos) -> _get_table_layout()
#   - Trunca texto que não cabe na tela -> _align_text()
#   - Tema de cores do prompt_toolkit (deriva do accent do color.py) -> pt_style/prompt_style
#   - A tela de seleção em si (teclas, cabeçalho, lista, rodapé) -> _tui_select()
#     - Atalhos de teclado (↑ ↓ espaço t enter ctrl-c) -> bindings dentro de _tui_select
#     - Desenho de cada linha/cartão (título, tipo, qualidade, ícones) -> get_list_text()
#       (é a função mais longa e mais repetitiva do arquivo: tem um bloco
#       quase idêntico por item_category: album/track/playlist/artist)
#
#   Classe QobuzDL (motor de download):
#   - Construtor / todas as opções aceitas -> QobuzDL.__init__
#   - Login na API do Qobuz -> initialize_client()
#   - Baixar 1 item (álbum/faixa) já sabendo o ID -> download_from_id()
#   - Processar uma URL do Qobuz (álbum/faixa/artista/label/playlist),
#     incluindo filtro de tipo de lançamento e paralelismo -> handle_url()
#     (é a função mais longa e mais crítica do arquivo)
#   - Baixar uma lista de URLs/arquivo .txt (usado pelo comando "dl") ->
#     download_list_of_urls() / download_from_txt_file()
#   - Modo "lucky" (busca automática, pega os N primeiros) -> lucky_mode()
#   - Monta o dicionário de metadados exibido na TUI a partir da resposta
#     da API -> _extract_rich_metadata() (é aqui que fica a heurística que
#     classifica álbum/EP/single quando a API não informa isso claramente)
#   - Busca por tipo (álbum/faixa/artista/playlist/favoritos) -> search_by_type()
#   - MODO INTERATIVO completo (menus, prompt de busca, navegação por
#     artista) -> interactive()
#   - Importar playlist de outra plataforma (Spotify/Deezer/etc.) ou
#     arquivo (TXT/CSV/JSON) -> import_playlist_from_url_or_file() /
#     download_from_playlist_file()
#
# PADRÃO REPETIDO NESTE ARQUIVO -- download paralelo:
#   Em handle_url(), download_list_of_urls() e download_from_playlist_file()
#   existe o MESMO padrão: se max_workers > 1, cria um asyncio.Semaphore do
#   tamanho de max_workers + uma função interna "_bounded_track_download"
#   (ou "_bounded_track_url") que espera sua vez (semaphore), aplica um
#   pequeno atraso escalonado (HEADER_STAGGER_DELAY) só nos primeiros itens
#   pra evitar que todos os cabeçalhos de progresso apareçam ao mesmo tempo,
#   e então chama download_from_id(). Se for mexer no paralelismo, mexer
#   nos 3 lugares.
#
# CHANGELOG desta revisão (melhorias de uso em iPad/celular + correções):
#   - _tui_select(): + atalhos j/k (equivalentes a ↑/↓), esc (equivalente a
#     Ctrl+C), pageup/pagedown (pula 10 itens), g/G (topo/fim), dígitos 1-9
#     (pula direto pro item daquela posição), r (força redesenho da tela).
#   - Application(...) agora com mouse_support=True (permite rolar a lista
#     com mouse/trackpad/scroll de dois dedos; NÃO inclui "tocar pra
#     selecionar" item por item -- ver comentário no local).
#   - get_footer_text(): + indicador "Item X de Y" (serve de indicador de
#     scroll em telas touch, que não têm scrollbar visível).
#   - get_tokens() agora é `async def` e usa `await Bundle.create()` em vez
#     de `Bundle()` síncrono -- evita travar o event loop. Qualquer chamada
#     existente a `.get_tokens()` fora deste arquivo PRECISA ganhar `await`.
#   - Removida chamada duplicada de make_m3u(new_path) em handle_url().
#   - Nova função `_classify_release_type()` (nível de módulo, antes da
#     classe) substitui as DUAS cópias quase idênticas da heurística de
#     Album/EP/Single/Live/Compilation que existiam em handle_url() e em
#     _extract_rich_metadata() -- agora é um só lugar pra ajustar a regra.
#   - Excludes silenciosos (`except Exception: pass`) na classificação de
#     tipo de lançamento em handle_url() agora logam em debug.
# ==============================================================================

import logging
import os
import sys
import time
import asyncio
import shutil
import re

import httpx
from pathvalidate import sanitize_filename

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import Window, ScrollOffsets, HSplit
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
    from prompt_toolkit.styles import Style
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application.current import get_app
    from prompt_toolkit.utils import get_cwidth
    from prompt_toolkit.formatted_text import FormattedText
except ImportError:
    sys.exit(
        "Erro: Por favor, instale o prompt_toolkit executando: pip install prompt_toolkit"
    )

from qobuz_dl.bundle import Bundle
from qobuz_dl import downloader, qopy
from qobuz_dl import ui

from qobuz_dl.color import (
    INFO as CYAN,
    OFF,
    RED,
    GREEN,
    WARNING as YELLOW,
    RESET,
    _ACCENT,
)
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

HEADER_STAGGER_DELAY = 1.5

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
# desligada / --no-color), o regex não casa e os hex fixos acima são usados.
_match = re.search(r"\033\[38;2;(\d+);(\d+);(\d+)m", _ACCENT)
if _match:
    _r, _g, _b = map(int, _match.groups())
    _hex_accent = f"#{_r:02x}{_g:02x}{_b:02x}"
    _darker_accent = f"#{int(_r * 0.8):02x}{int(_g * 0.8):02x}{int(_b * 0.8):02x}"

    def _shade(f):
        # Clareia (f > 0, mistura com branco) ou escurece (f < 0, mistura
        # com preto) a cor de destaque, usado pra diferenciar os "tipos"
        # de lançamento (álbum/EP/single/etc.) na TUI sem cadastrar uma
        # cor fixa pra cada tipo.
        if f > 0:
            return f"#{int(_r + (255 - _r) * f):02x}{int(_g + (255 - _g) * f):02x}{int(_b + (255 - _b) * f):02x}"
        else:
            return f"#{int(_r * (1 + f)):02x}{int(_g * (1 + f)):02x}{int(_b * (1 + f)):02x}"

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
    safe_columns = columns - prefix_len - 6

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

    top_border = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    mid_border = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot_border = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"

    return (
        True,
        widths,
        headers,
        {"top": top_border, "mid": mid_border, "bot": bot_border},
    )


async def _tui_select(title, options_dicts, is_multi=False, item_category="album"):
    """Tela de seleção em tela cheia (prompt_toolkit), usada por TODOS os
    menus interativos do programa: escolher tipo de busca, resultados de
    busca, ações sobre um artista, sim/não, escolha de qualidade, etc.

    Args:
        title: texto do cabeçalho.
        options_dicts: lista de opções. Pode ser lista de strings simples
            (menus tipo "filter") ou lista de dicts {"meta": {...}, "url": ...}
            (resultados de busca reais, ver _extract_rich_metadata()).
        is_multi: se True, permite selecionar vários itens com [espaço] e
            confirmar com [Enter]; se False, [Enter] já seleciona o item
            sob o cursor.
        item_category: "album" | "track" | "playlist" | "artist" | "filter"
            -- controla como cada linha/cartão é desenhado (ver get_list_text).

    Atalhos de teclado disponíveis:
        ↑/↓ ou j/k    -- move o cursor
        PageUp/PageDown -- pula 10 itens
        g / G          -- vai pro primeiro / último item
        1-9            -- pula direto pro item daquela posição
        r              -- força redesenho da tela (útil após girar
                          iPad/iPhone, se o terminal não reagir sozinho)
        espaço (multi) -- marca/desmarca o item sob o cursor
        t (multi)      -- marca/desmarca todos
        Enter          -- confirma
        Ctrl+C ou Esc  -- cancela (propaga KeyboardInterrupt)

    Retorna:
        - is_multi=True:  lista de tuplas (item, índice_original)
        - is_multi=False: tupla única (item, índice_original)
        - Ctrl+C/Esc: propaga KeyboardInterrupt (capturado no chamador)
    """
    bindings = KeyBindings()
    selected_indices = set()
    cursor_pos = 0

    # --- Navegação vertical: setas ↑/↓ + alternativas "vim-style" j/k ---
    # As teclas j/k existem em QUALQUER teclado (não exigem trocar de layout
    # nem apertar um modificador), o que ajuda bastante em apps SSH de
    # iPad/iPhone onde as setas às vezes ficam numa barra extra escondida.
    def _move_up(event):
        nonlocal cursor_pos
        cursor_pos = max(0, cursor_pos - 1)

    def _move_down(event):
        nonlocal cursor_pos
        if options_dicts:
            cursor_pos = min(len(options_dicts) - 1, cursor_pos + 1)

    bindings.add("up")(_move_up)
    bindings.add("k")(_move_up)
    bindings.add("down")(_move_down)
    bindings.add("j")(_move_down)

    # --- Navegação rápida: PageUp/PageDown pulam de 10 em 10, g/G vão
    # direto pro topo/fim. Em listas de 20-50 resultados isso evita ter
    # que ir item por item numa tela touch. ---
    _PAGE_JUMP = 10

    @bindings.add("pageup")
    def _(event):
        nonlocal cursor_pos
        cursor_pos = max(0, cursor_pos - _PAGE_JUMP)

    @bindings.add("pagedown")
    def _(event):
        nonlocal cursor_pos
        if options_dicts:
            cursor_pos = min(len(options_dicts) - 1, cursor_pos + _PAGE_JUMP)

    @bindings.add("g")
    def _(event):
        nonlocal cursor_pos
        cursor_pos = 0

    @bindings.add("G")
    def _(event):
        nonlocal cursor_pos
        if options_dicts:
            cursor_pos = len(options_dicts) - 1

    # --- Atalho numérico: dígitos 1-9 pulam DIRETO pro item daquela
    # posição (1 = primeiro item, 9 = nono). Útil em telas touch pra não
    # precisar "andar" o cursor manualmente até um item visível. ---
    def _make_digit_jump(n):
        def _jump(event):
            nonlocal cursor_pos
            idx = n - 1
            if options_dicts and idx < len(options_dicts):
                cursor_pos = idx

        return _jump

    for _digit in range(1, 10):
        bindings.add(str(_digit))(_make_digit_jump(_digit))

    # --- Força um redesenho manual da tela. Útil quando o terminal do
    # iPad/iPhone não avisa corretamente sobre mudança de tamanho (ex. ao
    # girar a tela ou entrar/sair do modo Split View) e a UI fica com um
    # layout desatualizado até a próxima tecla ser pressionada. ---
    @bindings.add("r")
    def _(event):
        event.app.invalidate()

    if is_multi:

        @bindings.add("space")
        def _(event):
            # Alterna seleção do item sob o cursor (não confirma nada ainda)
            if not options_dicts:
                return
            if cursor_pos in selected_indices:
                selected_indices.remove(cursor_pos)
            else:
                selected_indices.add(cursor_pos)

        @bindings.add("t")
        def _(event):
            # "Selecionar/desmarcar todos" -- alterna com base no estado atual
            if not options_dicts:
                return
            if len(selected_indices) == len(options_dicts):
                selected_indices.clear()
            else:
                selected_indices.update(range(len(options_dicts)))

    @bindings.add("enter")
    def _(event):
        # Em modo multi, se nada foi marcado com [espaço], confirma pelo
        # menos o item sob o cursor (evita "confirmar vazio" sem querer).
        if not options_dicts:
            return
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

    # "esc" como alternativa ao Ctrl+C: em vários apps de terminal do iOS
    # (a-Shell, Termius) Ctrl exige um toque extra num modificador virtual,
    # enquanto Esc costuma ter uma tecla dedicada na barra de atalhos.
    @bindings.add("escape")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    def get_header_text():
        # Desenha o título + (se a tela for larga o bastante) o cabeçalho
        # da tabela com as bordas superiores ┌─┬─┐. Recalculado a cada
        # redesenho da tela (redimensionar terminal, mover cursor, etc.),
        # por isso mede a largura do terminal toda vez.
        try:
            columns = get_app().output.get_size().columns
        except Exception:
            columns, _ = shutil.get_terminal_size((80, 24))

        is_table, widths, headers, borders = _get_table_layout(
            columns, is_multi, item_category
        )

        prefix_len = 5 if is_multi else 3
        hdr_pref = " " * prefix_len

        res = [("class:title", f"\n === {title} ===\n\n")]

        if is_table:
            res.append(("class:meta", hdr_pref + borders["top"] + "\n"))

            res.append(("class:meta", hdr_pref + "│ "))
            for idx, (h, w) in enumerate(zip(headers, widths)):
                res.append(("class:table_header", _align_text(h, w)))
                if idx < len(headers) - 1:
                    res.append(("class:meta", " │ "))
                else:
                    res.append(("class:meta", " │\n"))

            res.append(("class:meta", hdr_pref + borders["mid"]))

        return res

    def get_list_text():
        # ------------------------------------------------------------
        # Desenha CADA linha/cartão da lista de opções.
        #
        # Duas "sub-rotinas" de baixo nível fazem o trabalho pesado de
        # truncar texto que não cabe e preencher o resto da linha com
        # espaço/cor de fundo (pra dar efeito de "linha inteira destacada"
        # quando o cursor está em cima):
        #   - add_line(): usada no modo TABELA (colunas alinhadas)
        #   - add_card_line(): usada no modo CARTÃO (bordas ╭─╮ ao redor
        #     de cada item, mais legível em tela estreita)
        #
        # Depois disso vem um loop `for i, opt in enumerate(options_dicts)`
        # com um bloco quase idêntico por item_category (album/track/
        # playlist/artist/filter) -- cada bloco só decide QUAIS campos de
        # `meta` mostrar e em que ordem. Se for adicionar uma nova
        # categoria de item na TUI, é aqui (e em _get_table_layout) que
        # entra o novo bloco, seguindo o padrão dos existentes.
        # ------------------------------------------------------------
        try:
            columns = get_app().output.get_size().columns
        except Exception:
            columns, _ = shutil.get_terminal_size((80, 24))

        is_table, widths, headers, borders = _get_table_layout(
            columns, is_multi, item_category
        )
        res = []

        def add_line(fragments, fill_bg=False):
            final_fragments = []
            current_w = 0

            for st, txt in fragments:
                txt_w = get_cwidth(txt)
                if current_w + txt_w > columns:
                    allowed = max(0, columns - current_w)
                    trunc_txt = ""
                    temp_w = 0
                    for char in txt:
                        cw = get_cwidth(char)
                        if temp_w + cw > allowed:
                            break
                        trunc_txt += char
                        temp_w += cw

                    if fill_bg:
                        final_fragments.append(("class:hovered", trunc_txt))
                    else:
                        final_fragments.append((st, trunc_txt))
                    current_w += temp_w
                    break
                else:
                    if fill_bg:
                        final_fragments.append(("class:hovered", txt))
                    else:
                        final_fragments.append((st, txt))
                    current_w += txt_w

            padding = max(0, columns - current_w)
            if padding > 0:
                pad_st = "class:hovered" if fill_bg else ""
                final_fragments.append((pad_st, " " * padding))

            final_fragments.append(("", "\n"))
            res.extend(final_fragments)

        inner_w = max(10, columns - 6)

        def add_card_line(
            fragments, hovered=False, is_checked=False, border_style="class:meta"
        ):
            final_fragments = []
            current_w = 0

            row_st = (
                "class:hovered"
                if hovered
                else ("class:highlight" if is_checked else "")
            )
            border_st = "class:hovered" if hovered else border_style

            # Caractere físico mais grosso (Heavy Box Drawing) se estiver marcado
            vt = "┃" if is_checked else "│"

            final_fragments.append((border_st, f" {vt} "))

            for st, txt in fragments:
                txt_w = get_cwidth(txt)
                if current_w + txt_w > inner_w:
                    allowed = max(0, inner_w - current_w)
                    trunc_txt = ""
                    temp_w = 0
                    for char in txt:
                        cw = get_cwidth(char)
                        if temp_w + cw > allowed:
                            break
                        trunc_txt += char
                        temp_w += cw

                    st_use = (
                        "class:hovered"
                        if hovered
                        else ("class:highlight" if is_checked else st)
                    )
                    final_fragments.append((st_use, trunc_txt))
                    current_w += temp_w
                    break
                else:
                    st_use = (
                        "class:hovered"
                        if hovered
                        else ("class:highlight" if is_checked else st)
                    )
                    final_fragments.append((st_use, txt))
                    current_w += txt_w

            padding = max(0, inner_w - current_w)
            if padding > 0:
                pad_st = (
                    "class:hovered"
                    if hovered
                    else ("class:highlight" if is_checked else "")
                )
                final_fragments.append((pad_st, " " * padding))

            final_fragments.append((border_st, f" {vt}\n"))
            res.extend(final_fragments)

        for i, opt in enumerate(options_dicts):
            hovered = i == cursor_pos
            checked = i in selected_indices

            if hovered:
                res.append(("[SetCursorPosition]", ""))

            style = "class:highlight" if checked else ""
            title_style = "class:highlight"

            ptr = ">" if hovered else " "
            if is_multi:
                chk = "✓" if checked else "○"
                prefix = f" {ptr} {chk} "
            else:
                prefix = f" {ptr} "

            row_st = "class:hovered" if hovered else style
            tit_st = (
                "class:hovered"
                if hovered
                else ("class:highlight" if checked else "class:item_title")
            )
            border_st = (
                "class:hovered"
                if hovered
                else ("class:highlight" if checked else "class:meta")
            )

            # Borda superior e quinas desenhadas com linhas grossas se marcado
            if not is_table:
                top_l = "┏" if checked else "╭"
                top_r = "┓" if checked else "╮"
                hz = "━" if checked else "─"
                res.append((border_st, f" {top_l}{hz * (inner_w + 2)}{top_r}\n"))

            if item_category == "filter" and isinstance(opt, str):
                if is_table:
                    add_line([(tit_st, f"{prefix}{opt}")], fill_bg=hovered)
                else:
                    add_card_line(
                        [(tit_st, f"{prefix}{opt}")],
                        hovered=hovered,
                        is_checked=checked,
                        border_style=border_st,
                    )

            elif isinstance(opt, str):
                if is_table:
                    add_line([(row_st, f"{prefix}{opt}")], fill_bg=hovered)
                else:
                    add_card_line(
                        [(row_st, f"{prefix}{opt}")],
                        hovered=hovered,
                        is_checked=checked,
                        border_style=border_st,
                    )

            else:
                meta = opt.get("meta", {})
                ql = meta.get("quality", "")
                typ = meta.get("type", "")

                ql_color = "fg:#c59b27 bold" if "24b" in ql else f"fg:{_hex_accent}"
                ql_st = (
                    "class:hovered"
                    if hovered
                    else ("class:highlight" if checked else ql_color)
                )

                raw_typ = typ.strip().lower()
                if raw_typ == "album":
                    typ_st = "class:type_album"
                elif raw_typ == "ep":
                    typ_st = "class:type_ep"
                elif raw_typ == "single":
                    typ_st = "class:type_single"
                elif raw_typ == "track":
                    typ_st = "class:type_track"
                elif raw_typ == "compilation":
                    typ_st = "class:type_comp"
                else:
                    typ_st = "class:type_other"

                if hovered:
                    typ_st = "class:hovered"
                elif checked:
                    typ_st = "class:highlight"

                if item_category == "album":
                    tit_str = meta.get("title", "")
                    art = meta.get("artist", "")
                    yr = meta.get("year", "")
                    fx = str(meta.get("tracks_count", ""))

                    if is_table:
                        tit_align = _align_text(tit_str, widths[0])
                        art_align = _align_text(art, widths[1])
                        typ_align = _align_text(typ, widths[2])
                        yr_align = _align_text(yr, widths[3])
                        fx_align = _align_text(fx, widths[4])
                        ql_align = _align_text(ql, widths[5])

                        if hovered:
                            p1 = f"│ {tit_align} │ {art_align} │ {typ_align} │ {yr_align} │ {fx_align} │ "
                            add_line(
                                [
                                    (style, prefix),
                                    (row_st, p1),
                                    (ql_st, ql_align),
                                    (row_st, " │"),
                                ],
                                fill_bg=False,
                            )
                        else:
                            add_line(
                                [
                                    (style, prefix),
                                    (style, "│ "),
                                    (tit_st, tit_align),
                                    (style, " │ "),
                                    (style, art_align),
                                    (style, " │ "),
                                    (typ_st, typ_align),
                                    (style, " │ "),
                                    (style, yr_align),
                                    (style, " │ "),
                                    (style, fx_align),
                                    (style, " │ "),
                                    (ql_st, ql_align),
                                    (style, " │"),
                                ],
                                fill_bg=False,
                            )
                    else:
                        l1 = [(tit_st, f"{prefix}{tit_str}")]
                        ql_str = f"[{ql}]"
                        pad_len = inner_w - get_cwidth(l1[0][1]) - get_cwidth(ql_str)
                        if pad_len > 0:
                            l1.append(("", " " * pad_len))
                            l1.append((ql_st, ql_str))
                        add_card_line(
                            l1,
                            hovered=hovered,
                            is_checked=checked,
                            border_style=border_st,
                        )

                        if hovered:
                            add_card_line(
                                [(row_st, f"   👤 {art}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   💿 {typ} · {yr}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   🎵 {fx} faixas")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   🎚 {ql}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                        else:
                            add_card_line(
                                [
                                    (style, f"   👤 {art} · "),
                                    (typ_st, typ),
                                    (style, f" · {yr}"),
                                ],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )

                elif item_category == "track":
                    tit_str = meta.get("title", "")
                    art = meta.get("artist", "")
                    alb = meta.get("album", "")
                    dur = meta.get("duration", "")

                    if is_table:
                        tit_align = _align_text(tit_str, widths[0])
                        art_align = _align_text(art, widths[1])
                        alb_align = _align_text(alb, widths[2])
                        typ_align = _align_text(typ, widths[3])
                        dur_align = _align_text(dur, widths[4])
                        ql_align = _align_text(ql, widths[5])

                        if hovered:
                            p1 = f"│ {tit_align} │ {art_align} │ {alb_align} │ {typ_align} │ {dur_align} │ "
                            add_line(
                                [
                                    (style, prefix),
                                    (row_st, p1),
                                    (ql_st, ql_align),
                                    (row_st, " │"),
                                ],
                                fill_bg=False,
                            )
                        else:
                            add_line(
                                [
                                    (style, prefix),
                                    (style, "│ "),
                                    (tit_st, tit_align),
                                    (style, " │ "),
                                    (style, art_align),
                                    (style, " │ "),
                                    (style, alb_align),
                                    (style, " │ "),
                                    (typ_st, typ_align),
                                    (style, " │ "),
                                    (style, dur_align),
                                    (style, " │ "),
                                    (ql_st, ql_align),
                                    (style, " │"),
                                ],
                                fill_bg=False,
                            )
                    else:
                        l1 = [(tit_st, f"{prefix}{tit_str}")]
                        ql_str = f"[{ql}]"
                        pad_len = inner_w - get_cwidth(l1[0][1]) - get_cwidth(ql_str)
                        if pad_len > 0:
                            l1.append(("", " " * pad_len))
                            l1.append((ql_st, ql_str))
                        add_card_line(
                            l1,
                            hovered=hovered,
                            is_checked=checked,
                            border_style=border_st,
                        )

                        if hovered:
                            add_card_line(
                                [(row_st, f"   👤 {art}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   💿 {alb}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   📀 {typ} · ⏱ {dur}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   🎚 {ql}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                        else:
                            add_card_line(
                                [
                                    (style, f"   👤 {art} · "),
                                    (typ_st, typ),
                                    (style, f" · ⏱ {dur}"),
                                ],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )

                elif item_category == "playlist":
                    n = meta.get("name", "")
                    o = meta.get("owner", "")
                    c = str(meta.get("count", 0))
                    dur = meta.get("duration", "--:--")

                    if is_table:
                        n_align = _align_text(n, widths[0])
                        o_align = _align_text(o, widths[1])
                        c_align = _align_text(c, widths[2])
                        dur_align = _align_text(dur, widths[3])

                        if hovered:
                            p1 = f"│ {n_align} │ {o_align} │ {c_align} │ {dur_align} │"
                            add_line([(style, prefix), (row_st, p1)], fill_bg=False)
                        else:
                            add_line(
                                [
                                    (style, prefix),
                                    (style, "│ "),
                                    (tit_st, n_align),
                                    (style, " │ "),
                                    (style, o_align),
                                    (style, " │ "),
                                    (style, c_align),
                                    (style, " │ "),
                                    (style, dur_align),
                                    (style, " │"),
                                ],
                                fill_bg=False,
                            )
                    else:
                        add_card_line(
                            [(tit_st, f"{prefix}{n}")],
                            hovered=hovered,
                            is_checked=checked,
                            border_style=border_st,
                        )
                        if hovered:
                            add_card_line(
                                [(row_st, f"   👤 {o}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   🎵 {c} faixas")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   ⏱ {dur}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                        else:
                            add_card_line(
                                [(style, f"   👤 {o} · 🎵 {c} · ⏱ {dur}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )

                elif item_category == "artist":
                    n = meta.get("name", "")
                    c_str = f"{meta.get('count', '')} lançamentos"

                    if is_table:
                        n_align = _align_text(n, widths[0])
                        c_align = _align_text(c_str, widths[1])

                        if hovered:
                            p1 = f"│ {n_align} │ {c_align} │"
                            add_line([(style, prefix), (row_st, p1)], fill_bg=False)
                        else:
                            add_line(
                                [
                                    (style, prefix),
                                    (style, "│ "),
                                    (tit_st, n_align),
                                    (style, " │ "),
                                    (style, c_align),
                                    (style, " │"),
                                ],
                                fill_bg=False,
                            )
                    else:
                        add_card_line(
                            [(tit_st, f"{prefix}👤 {n}")],
                            hovered=hovered,
                            is_checked=checked,
                            border_style=border_st,
                        )
                        if hovered:
                            add_card_line(
                                [(row_st, f"   🎵 {c_str}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                            add_card_line(
                                [(row_st, f"   [Enter] para abrir opções")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )
                        else:
                            add_card_line(
                                [(style, f"   🎵 {c_str}")],
                                hovered=hovered,
                                is_checked=checked,
                                border_style=border_st,
                            )

            # Borda inferior e quinas desenhadas com linhas grossas se marcado
            if not is_table:
                bot_l = "┗" if checked else "╰"
                bot_r = "┛" if checked else "╯"
                hz = "━" if checked else "─"
                res.append((border_st, f" {bot_l}{hz * (inner_w + 2)}{bot_r}\n"))
            else:
                if i < len(options_dicts) - 1:
                    empty_prefix = " " * len(prefix)
                    add_line(
                        [("class:meta", empty_prefix + borders["mid"])], fill_bg=False
                    )

        if not is_table:
            res.append(("", " \n" * 8))

        if res and res[-1][1].endswith("\n"):
            res[-1] = (res[-1][0], res[-1][1][:-1])

        return res

    def get_footer_text():
        # Rodapé: borda inferior da tabela (se aplicável), contador de
        # selecionados (modo multi) e a dica de atalhos de teclado.
        try:
            columns = get_app().output.get_size().columns
        except Exception:
            columns, _ = shutil.get_terminal_size((80, 24))

        is_table, widths, headers, borders = _get_table_layout(
            columns, is_multi, item_category
        )
        res = []

        if is_table:
            prefix_len = 5 if is_multi else 3
            hdr_pref = " " * prefix_len
            res.append(("class:meta", hdr_pref + borders["bot"] + "\n"))

        res.append(("", "\n"))

        # Indicador de posição "Item X de Y": em telas touch (sem mouse
        # wheel/scrollbar visível), é a forma mais simples de o usuário
        # perceber que a lista continua além do que está visível na tela
        # -- se cursor_pos+1 < total, ainda tem mais itens abaixo.
        if options_dicts:
            res.append(
                ("class:meta", f" Item {cursor_pos + 1} de {len(options_dicts)}\n")
            )

        if is_multi:
            res.append(
                ("class:checkbox", f" ✓ Selecionados: {len(selected_indices)}\n")
            )
            footer_msg = " [↑↓/jk] Mover   [Espaço] Selecionar   [t] Todos   [1-9] Ir para   [Enter] Confirmar"
        elif item_category == "artist":
            footer_msg = " [↑↓/jk] Mover   [1-9] Ir para   [Enter] Abrir artista"
        else:
            footer_msg = " [↑↓/jk] Mover   [1-9] Ir para   [Enter] Confirmar"

        if get_cwidth(footer_msg) > columns:
            trunc_msg = ""
            w = 0
            for char in footer_msg:
                cw = get_cwidth(char)
                if w + cw > columns - 1:
                    break
                trunc_msg += char
                w += cw
            res.append(("class:footer", trunc_msg + "\n"))
        else:
            res.append(("class:footer", footer_msg + "\n"))

        return res

    header_window = Window(
        content=FormattedTextControl(text=get_header_text), dont_extend_height=True
    )

    # Offset bottom=8 força a câmera a subir toda a expansão do cartão
    list_window = Window(
        content=FormattedTextControl(text=get_list_text, focusable=True),
        scroll_offsets=ScrollOffsets(top=2, bottom=8),
        wrap_lines=False,
    )

    footer_window = Window(
        content=FormattedTextControl(text=get_footer_text), dont_extend_height=True
    )

    # Monta a tela cheia (header + lista rolável + rodapé) e bloqueia
    # aqui até o usuário confirmar (Enter) ou cancelar (Ctrl+C).
    layout = Layout(HSplit([header_window, list_window, footer_window]))
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=True,
        style=pt_style,
        # mouse_support habilita rolagem com mouse/trackpad/scroll de duas
        # dedos (útil com Magic Keyboard/trackpad no iPad, e em terminais
        # desktop). NOTA: isso NÃO adiciona "toque pra selecionar" em cada
        # linha -- pra isso seria preciso anexar um mouse_handler a cada
        # fragmento de texto em get_list_text(), o que não foi feito aqui
        # (mudança maior, deixada de fora por enquanto pra não arriscar
        # quebrar o desenho das linhas). Selecionar continua sendo por
        # teclado: setas/j-k, dígitos 1-9, espaço, enter.
        mouse_support=True,
    )

    res = await app.run_async()
    if isinstance(res, Exception):
        raise res
    return res


WEB_URL = "https://play.qobuz.com/"
ARTISTS_SELECTOR = "td.chartlist-artist > a"
TITLE_SELECTOR = "td.chartlist-name > a"
# Mapa código-de-qualidade -> texto legível (mesmos códigos usados em
# commands.py: -q/--quality). Se o Qobuz adicionar uma nova qualidade,
# precisa espelhar aqui E em commands.py (choices=[5, 6, 7, 27]).
QUALITIES = {
    5: "5 - MP3",
    6: "6 - 16 bit, 44.1kHz",
    7: "7 - 24 bit, <96kHz",
    27: "27 - 24 bit, >96kHz",
}

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------------
# Heurística ÚNICA de classificação de tipo de lançamento (Album/EP/Single/
# Live/Compilation), usada em dois lugares que antes tinham cada um sua
# própria cópia quase idêntica desta lógica: _extract_rich_metadata() (pra
# exibir na TUI de busca) e handle_url() (pro filtro de tipo ao explorar
# um artista por URL). Antes, se alguém ajustasse a regra num lugar e
# esquecesse do outro, os dois caminhos podiam divergir silenciosamente.
#
# A API do Qobuz nem sempre informa release_type/product_type de forma
# confiável, então isso combina: (1) o valor da API quando disponível,
# (2) palavras-chave no título/versão ("live", "best of", " ep"), e
# (3) contagem de faixas / duração total como último recurso.
# ------------------------------------------------------------------------
def _classify_release_type(
    title,
    version,
    track_count,
    duration_seconds=0,
    api_release_type=None,
    item_type="album",
):
    base_title = (title or "").lower()
    version_tag = (version or "").lower()
    r_type = (api_release_type or "unknown").lower()
    track_count = int(track_count or 0)
    duration_seconds = int(duration_seconds or 0)

    if "live" in version_tag or "(live" in base_title or "- live" in base_title:
        return "live"

    if any(
        kw in base_title or kw in version_tag
        for kw in ["best of", "greatest hits", "anthology", "collection", "compilation"]
    ):
        return "compilation"

    if " ep" in base_title or version_tag == "ep":
        return "ep"
    elif r_type == "single" and track_count >= 4:
        return "ep"
    elif r_type == "ep" and 1 <= track_count <= 3:
        return "single"
    elif r_type == "album" and 1 <= track_count <= 3:
        return "single"
    elif r_type == "unknown":
        if item_type == "album":
            if duration_seconds >= 1740 or track_count >= 7:
                return "album"
            elif 1 <= track_count <= 3:
                return "single"
            elif 4 <= track_count <= 6:
                return "ep"
            else:
                return "album"
        else:
            return item_type

    return r_type


# ==============================================================================
# CLASSE QobuzDL -- motor principal: busca, download, importação de playlists
# e o modo interativo. Uma instância é criada 1x por execução (em cli.py) e
# guarda todas as preferências de download (qualidade, formatos, tags, etc.)
# como atributos de instância, usados por todos os métodos abaixo.
# ==============================================================================
class QobuzDL:
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
        """Faz login na API do Qobuz (qopy.Client) usando as credenciais
        vindas do config.ini/keyring. Chamado 1x logo depois de instanciar
        QobuzDL (exceto no comando "auth", que não precisa de sessão)."""
        self.client = await qopy.Client.create(
            email,
            pwd,
            app_id,
            secrets,
            self.settings.user_auth_token,
            force_english=self.force_english,
        )
        logger.info(f"{YELLOW}Set max quality: {QUALITIES[int(self.quality)]}")

    async def get_tokens(self):
        """Busca App ID + secrets "na hora" via bundle.py (scraping do
        bundle.js). Não é chamado no fluxo normal (que lê app_id/secrets já
        salvos no config.ini) -- serve como forma alternativa de obter
        credenciais de API atualizadas sem passar pelo wizard -r.

        ⚠️ MUDANÇA: este método era `def` (síncrono) e chamava `Bundle()`
        (que faz requisições HTTP síncronas com httpx.Client). Rodar isso
        dentro de um app assíncrono TRAVA o event loop inteiro durante o
        download do bundle.js. Agora usa `await Bundle.create()` (versão
        assíncrona, ver bundle.py) para não bloquear.
        SE ALGO FORA DESTE ARQUIVO CHAMA `qobuz_dl_instance.get_tokens()`
        sem `await`, essa chamada vai parar de funcionar (vira coroutine
        não executada) -- procure por "get_tokens(" em cli.py e outros
        arquivos e adicione `await` na chamada.
        """
        bundle = await Bundle.create()
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
        suppress_header=False,
    ):
        """Baixa UM item (álbum ou faixa) já sabendo o `item_id`. É o
        "menor" ponto de entrada de download -- handle_url(),
        download_list_of_urls() e download_from_playlist_file() todos
        convergem pra cá no final. Cuida de: checar se já foi baixado
        (banco local), instanciar o downloader.Download de fato, tratar
        erros sem derrubar o programa inteiro (loga e segue pro próximo
        item), e aplicar o --delay entre downloads se configurado."""
        if await handle_download_id(
            self.downloads_db, item_id, add_id=False, quality=self.quality
        ):
            logger.info(
                f"{OFF}This release ID ({item_id}) was already downloaded "
                "according to the local database.\nUse the '--no-db' flag "
                "to bypass this."
            )
            if is_playlist:
                self.settings.pl_skipped = getattr(self.settings, "pl_skipped", 0) + 1
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
                not album,
                is_parallel=is_parallel,
                position_pool=position_pool,
                suppress_header=suppress_header,
            )
        except (httpx.RequestError, NonStreamable) as e:
            logger.error(f"{RED}Erro na liberação: {e}. Pulando...")
            if is_playlist:
                self.settings.pl_failed = getattr(self.settings, "pl_failed", 0) + 1
        except Exception as e:
            logger.error(
                f"{RED}Erro inesperado baixando item {item_id}: {e} (Pulando...){OFF}"
            )
            logger.debug("Detalhes do erro inesperado:", exc_info=True)
            if is_playlist:
                self.settings.pl_failed = getattr(self.settings, "pl_failed", 0) + 1

        if getattr(self, "delay", 0) > 0:
            logger.info(
                f"{YELLOW}[*] Sleeping for {self.delay} seconds to prevent rate limiting...{OFF}"
            )
            await asyncio.sleep(self.delay)

    async def handle_url(self, url):
        """Processa UMA URL do Qobuz. É o coração do comando `dl`.

        Fluxo:
        1. Identifica o tipo da URL (álbum/faixa/artista/label/playlist)
           via get_url_info() e escolhe a função de busca certa em `possibles`.
        2. Se for um "container" (artista/label/playlist), busca todos os
           itens dele; se for artista E estiver em sessão interativa, abre
           um filtro extra pra escolher Album/EP/Single/Live/Compilation.
        3. Aplica smart_discography (filtra spam) e/ou blacklist.
        4. Decide se baixa em paralelo (max_workers > 1) ou sequencial, e
           dispara download_from_id() para cada item.
        5. No final, gera o .m3u da playlist (se aplicável) e imprime um
           resumo de sucesso/pulados/falhas.

        ⚠️ Se for mexer na geração do .m3u, note que há uma chamada
        DUPLICADA de make_m3u(new_path) logo abaixo (dois `if` idênticos
        em sequência) -- parece bug residual, mas como make_m3u
        provavelmente é idempotente (só reescreve o arquivo), não chega a
        quebrar nada; ainda assim, ao mexer nessa área, considere remover
        a duplicata.
        """
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
                options = [
                    "💿 Album",
                    "📀 EP",
                    "🎵 Single",
                    "🎤 Live",
                    "🗂️ Compilation",
                ]
                title_text = (
                    f"Encontrados {len(items)} lançamentos "
                    f"para {content_name}. Filtre por tipo:"
                )

                sel_res = await _tui_select(
                    title_text, options, is_multi=True, item_category="filter"
                )
                selected_types_raw = sel_res if sel_res else []

                if selected_types_raw:
                    self.allowed_release_types = [
                        opt[0].split(" ", 1)[1].lower() for opt in selected_types_raw
                    ]
                else:
                    self.allowed_release_types = []
                    items = []
            else:
                self.allowed_release_types = None

            logger.debug(f"Number of chunks: {len(content)}")
            if content:
                _first_items = (
                    content[0].get(type_dict["iterable_key"], {}).get("items", [])
                )
                logger.debug(f"Items in first chunk: {len(_first_items)}")
            if getattr(self, "allowed_release_types", None) is not None:
                logger.info(
                    f"{YELLOW}[*] Evaluating {len(items)} releases "
                    f"(unwanted types will be skipped silently)...{OFF}"
                )

            is_playlist = url_type == "playlist"
            if is_playlist:
                self.settings.pl_success = 0
                self.settings.pl_skipped = 0
                self.settings.pl_failed = 0

            if is_playlist and not getattr(self, "playlist_as_albums", False):
                original_folder_format = self.folder_format
                original_multi_disc_setting = self.settings.multiple_disc_one_dir

                self.folder_format = "."
                self.settings.multiple_disc_one_dir = True

            is_track_batch = type_dict["iterable_key"] == "tracks"
            batch_workers = int(getattr(self.settings, "max_workers", 1))
            can_parallelize = (
                is_track_batch
                and batch_workers > 1
                and len(items) > 1
                and getattr(self, "delay", 0) <= 0
            )
            position_pool = (
                downloader._PositionPool(batch_workers) if can_parallelize else None
            )
            semaphore = asyncio.Semaphore(batch_workers) if can_parallelize else None
            pending_tasks = []

            mode_label = (
                f"Paralelo ({batch_workers} workers)"
                if can_parallelize
                else "Sequencial"
            )

            if is_playlist:
                from qobuz_dl.downloader import print_download_header
                from qobuz_dl.utils import format_duration

                p_name = content_name
                p_owner = (
                    content[0].get("owner", {}).get("name", "Unknown")
                    if content
                    else "Unknown"
                )
                p_count = str(len(items))
                dur_raw = content[0].get("duration", 0) if content else 0
                p_dur = format_duration(dur_raw) if dur_raw else "--:--"

                print_download_header(
                    "PLAYLIST",
                    [
                        ("Nome", p_name),
                        ("Criador", p_owner),
                        ("Faixas", p_count),
                        ("Duração", p_dur),
                        ("Modo", mode_label),
                    ],
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

                        # Classificação unificada (ver _classify_release_type
                        # no topo do arquivo) -- mesma regra usada na tela
                        # de busca, pra não divergir entre os dois lugares.
                        r_type = _classify_release_type(
                            title=item.get("title", ""),
                            version=item.get("version", ""),
                            track_count=item.get("tracks_count"),
                            duration_seconds=item.get("duration"),
                            api_release_type=r_type,
                            item_type="album",
                        )

                        if r_type not in self.allowed_release_types:
                            continue

                    except Exception as e:
                        logger.debug(
                            f"Falha ao classificar tipo de lançamento do item "
                            f"{item.get('id')}: {e}"
                        )

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
                        stagger_index = idx - 1
                        if stagger_index < batch_workers:
                            await asyncio.sleep(stagger_index * HEADER_STAGGER_DELAY)
                        async with semaphore:
                            await self.download_from_id(
                                item_id,
                                False,
                                new_path,
                                is_playlist=is_playlist,
                                playlist_index=idx,
                                is_parallel=True,
                                position_pool=position_pool,
                                suppress_header=is_playlist,
                            )

                    pending_tasks.append(_bounded_track_download())
                else:
                    await self.download_from_id(
                        item["id"],
                        True if type_dict["iterable_key"] == "albums" else False,
                        new_path,
                        is_playlist=is_playlist,
                        playlist_index=idx,
                        suppress_header=is_playlist,
                    )

            if pending_tasks:
                await asyncio.gather(*pending_tasks)

            if is_playlist and not getattr(self, "playlist_as_albums", False):
                self.folder_format = original_folder_format
                self.settings.multiple_disc_one_dir = original_multi_disc_setting

            if url_type == "playlist" and not self.no_m3u_for_playlists:
                # ✅ Removida a chamada duplicada que existia aqui (mesmo
                # `if` + make_m3u(new_path) repetido duas vezes seguidas).
                make_m3u(new_path)

            if is_playlist:
                succ = getattr(self.settings, "pl_success", 0)
                skip = getattr(self.settings, "pl_skipped", 0)
                fail = getattr(self.settings, "pl_failed", 0)

                from qobuz_dl.downloader import safe_print

                safe_print(f"\n{CYAN}{'━' * 40}{RESET}")
                safe_print(f"  📊 {GREEN}RESUMO DA PLAYLIST:{RESET} {content_name}")
                safe_print(f"   • Sucesso : {GREEN}{succ}/{len(items)}{RESET}")
                if skip > 0:
                    safe_print(
                        f"   • Puladas : {YELLOW}{skip}{RESET} (Já baixado/Demo)"
                    )
                if fail > 0:
                    safe_print(f"   • Falhas  : {RED}{fail}{RESET}")
                safe_print(f"{CYAN}{'━' * 40}{RESET}\n")

        else:
            await self.download_from_id(item_id, type_dict["album"])

    def mark_url_done_in_file(self, txt_file, url_to_mark):
        """Quando o download veio de um arquivo .txt de URLs (qobuz-dl dl
        lista.txt), marca a linha correspondente com "[DONE]" depois que
        aquela URL termina de baixar -- assim, se o processo for
        interrompido e rodado de novo, dá pra saber (visualmente) o que já
        foi processado. Não impede reprocessar; quem evita reprocessar é o
        banco de dados de downloads (--no-db desativa isso)."""
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
        """Ponto de entrada do comando `dl`: recebe uma lista de URLs (ou
        de caminhos de arquivo .txt, que caem em download_from_txt_file).

        Otimização importante: separa as URLs de FAIXA AVULSA (track_urls)
        das demais (other_urls -- álbuns/artistas/playlists/arquivos), porque
        só as faixas avulsas conseguem ser baixadas em paralelo direto aqui
        (ver o bloco `if track_urls:` mais abaixo, mesmo padrão de semáforo
        descrito no cabeçalho do arquivo). Álbuns/playlists/artistas mantêm
        seu próprio paralelismo interno dentro de handle_url()."""
        if not urls or not isinstance(urls, list):
            logger.info(f"{OFF}Nothing to download")
            return

        batch_workers = int(getattr(self.settings, "max_workers", 1))
        parallel_allowed = batch_workers > 1 and getattr(self, "delay", 0) <= 0

        track_urls = []
        other_urls = []

        for i, url in enumerate(urls):
            probe_url = url.replace("open.qobuz.com", "play.qobuz.com")
            if os.path.isfile(probe_url):
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

        total_track_urls = len(track_urls)

        use_parallel = parallel_allowed and total_track_urls > 1
        if not use_parallel and track_urls:
            other_urls.extend([(i, u) for i, u, _ in track_urls])
            other_urls.sort(key=lambda pair: pair[0])
            track_urls = []

        mode_label = (
            f"Paralelo ({batch_workers} workers)" if use_parallel else "Sequencial"
        )

        if total_track_urls > 1:
            from qobuz_dl.downloader import print_download_header

            print_download_header(
                "LOTE DE FAIXAS",
                [
                    ("Total de Faixas", str(total_track_urls)),
                    ("Modo", mode_label),
                ],
            )
            self.settings.pl_success = 0
            self.settings.pl_skipped = 0
            self.settings.pl_failed = 0

        if track_urls:
            position_pool = downloader._PositionPool(batch_workers)
            semaphore = asyncio.Semaphore(batch_workers)

            async def _bounded_track_url(original_url, item_id, stagger_index=0):
                if stagger_index < batch_workers:
                    await asyncio.sleep(stagger_index * HEADER_STAGGER_DELAY)
                async with semaphore:
                    await self.download_from_id(
                        item_id,
                        False,
                        is_parallel=True,
                        position_pool=position_pool,
                        suppress_header=(total_track_urls > 1),
                        is_playlist=(total_track_urls > 1),
                    )
                self.mark_url_done_in_file(txt_file, original_url)

            await asyncio.gather(
                *[
                    _bounded_track_url(original_url, item_id, stagger_index=i)
                    for i, (_, original_url, item_id) in enumerate(track_urls)
                ]
            )

            if total_track_urls > 1:
                succ = getattr(self.settings, "pl_success", 0)
                skip = getattr(self.settings, "pl_skipped", 0)
                fail = getattr(self.settings, "pl_failed", 0)

                from qobuz_dl.downloader import safe_print

                safe_print(f"\n{CYAN}{'━' * 44}{RESET}")
                safe_print(f"📊 {GREEN}RESUMO DO LOTE DE FAIXAS:{RESET}")
                safe_print(f"   • Sucesso : {GREEN}{succ}/{total_track_urls}{RESET}")
                if skip > 0:
                    safe_print(
                        f"   • Puladas : {YELLOW}{skip}{RESET} (Já baixado/Demo)"
                    )
                if fail > 0:
                    safe_print(f"   • Falhas  : {RED}{fail}{RESET}")
                safe_print(f"{CYAN}{'━' * 44}{RESET}\n")

        for _, url in other_urls:
            original_url = url
            url = url.replace("open.qobuz.com", "play.qobuz.com")

            if os.path.isfile(url):
                await self.download_from_txt_file(url)
            else:
                await self.handle_url(url)
                self.mark_url_done_in_file(txt_file, original_url)

    async def download_from_txt_file(self, txt_file):
        """Lê um arquivo .txt de URLs (uma por linha), ignora linhas vazias,
        comentários (#) e URLs já marcadas com [DONE] por
        mark_url_done_in_file(), valida cada URL com get_url_info() e
        repassa a lista limpa pra download_list_of_urls()."""
        try:
            valid_urls = []
            with open(txt_file, "r", encoding="utf-8") as txt:
                for line in txt:
                    line = line.strip()
                    if not line or line.startswith("#") or "[DONE]" in line:
                        continue

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
        """Comando `lucky`: busca `query` do tipo self.lucky_type
        (album/track/artist/playlist) e baixa os primeiros
        self.lucky_limit resultados, sem passar por nenhuma seleção manual."""
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
        """Converte o JSON bruto da API do Qobuz (item `i`) num dict
        "meta" enxuto e pronto pra exibir na TUI (ver get_list_text em
        _tui_select). Aqui mora a HEURÍSTICA que classifica um álbum como
        Album/EP/Single/Compilation/Live quando o campo release_type da
        API vem "unknown" ou ausente -- baseada em número de faixas
        (t_count), duração total e palavras-chave no título/versão
        (ex. "live", "best of", " ep"). Se os lançamentos estiverem sendo
        classificados errado na tela de busca, é AQUI que se ajusta essa
        lógica (e o bloco irmão dela dentro de handle_url(), que faz uma
        classificação parecida pro filtro de artista)."""
        meta_data = {}
        duration = int(i.get("duration") or 0)
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
            t_count = int(i.get("tracks_count") or 0)

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

            raw_type = (
                i.get("release_type") or i.get("product_type") or "unknown"
            ).lower()

            if raw_type == "unknown" and isinstance(i.get("album"), dict):
                raw_type = (
                    i["album"].get("release_type")
                    or i["album"].get("product_type")
                    or "unknown"
                ).lower()

            # Classificação unificada (ver _classify_release_type no topo
            # do arquivo) -- mesma regra usada no filtro de artista em
            # handle_url(), pra não divergir entre os dois lugares.
            raw_type = _classify_release_type(
                title=i.get("title") or i.get("name"),
                version=i.get("version"),
                track_count=i.get("tracks_count"),
                duration_seconds=i.get("duration"),
                api_release_type=raw_type,
                item_type=item_type,
            )

            rel_type = "EP" if raw_type.lower() == "ep" else raw_type.title()

            if i.get("hires_streamable"):
                bit_depth = i.get("maximum_bit_depth", 24)
                sampling_rate = i.get("maximum_sampling_rate", 96.0)
                quality = f"{bit_depth}b/{sampling_rate}kHz"
            else:
                quality = "16b/44.1kHz"

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
        self, query, item_type, limit=10, lucky=False, fav_subtype=None, sub_filter=None
    ):
        """Busca genérica na API do Qobuz, usada tanto pelo modo interativo
        quanto pelo `lucky`. `possibles` mapeia cada item_type pro método
        certo do client (search_albums/search_artists/etc.).

        Caso especial `item_type == "favorites"` com `fav_subtype ==
        "playlists"`: a API pública não tem um endpoint direto e
        documentado pra "minhas playlists", então esse bloco tenta 2
        chamadas internas da API (playlist/getUserPlaylists, com fallback
        pra getUserPlaylistIds + busca individual de cada playlist) -- é a
        parte mais frágil desta função porque depende de endpoints não
        oficiais; se "favoritos > playlists" parar de funcionar, é aqui
        que revisar primeiro.

        Retorna: lista de {"meta": ..., "url": ...} (ou lista de URLs puras
        se lucky=True, usado direto por download_list_of_urls)."""
        limit = int(limit)

        if item_type != "favorites" and (not query or len(query) < 2):
            logger.info(f"{RED}A pesquisa deve ter pelo menos 2 caracteres.{OFF}")
            return []

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

            fetch_limit = min(limit * 3, 50) if sub_filter else limit

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

                    params = {"limit": fetch_limit}
                    if user_id:
                        params["user_id"] = user_id

                    try:
                        p1 = params.copy()
                        p1["request_ts"] = int(time.time())
                        sig = self.client._modern_sig(
                            "playlist/getUserPlaylists", p1, self.client.sec
                        )
                        p1["request_sig"] = sig

                        r1 = await self.client.session.request(
                            "get",
                            self.client.base + "playlist/getUserPlaylists",
                            params=p1,
                        )
                        res1 = r1.json()

                        if "playlists" in res1 and "items" in res1["playlists"]:
                            iterable = res1["playlists"]["items"]
                        else:
                            p2 = params.copy()
                            p2["request_ts"] = int(time.time())
                            sig2 = self.client._modern_sig(
                                "playlist/getUserPlaylistIds", p2, self.client.sec
                            )
                            p2["request_sig"] = sig2

                            r2 = await self.client.session.request(
                                "get",
                                self.client.base + "playlist/getUserPlaylistIds",
                                params=p2,
                            )
                            res2 = r2.json()

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

                                    rp = await self.client.session.request(
                                        "get",
                                        self.client.base + "playlist/get",
                                        params=p_params,
                                    )
                                    p_data = rp.json()
                                    if "id" in p_data:
                                        iterable.append(p_data)
                                except Exception:
                                    pass
                    except Exception as e:
                        logger.error(f"{RED}Erro ao buscar playlists: {e}{OFF}")

                    mode_dict["requires_extra"] = False
                else:
                    results = await mode_dict["func"](
                        fav_type=fav_subtype, limit=fetch_limit
                    )
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
                results = await mode_dict["func"](query, limit=fetch_limit)
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

                if sub_filter:
                    typ_lower = meta_data.get("type", "").lower()
                    if typ_lower not in sub_filter:
                        continue

                url_category = (
                    fav_subtype[:-1]
                    if (item_type == "favorites" and fav_subtype)
                    else item_type
                )
                url = "{}{}/{}".format(WEB_URL, url_category, i.get("id", ""))

                item_list.append({"meta": meta_data, "url": url} if not lucky else url)

                if not lucky and len(item_list) >= limit:
                    break

            return item_list

        except Exception as e:
            logger.info(f"{RED}Erro na busca: {e}{OFF}")
            return []

    async def interactive(self, download=True):
        """Modo interativo completo (comando `interactive`/`i`/`fun`, ou
        padrão quando nenhum subcomando é passado). Fluxo geral:
        1. Pergunta o QUE buscar (faixas/álbuns/singles/artistas/
           playlists/favoritos) via _tui_select.
        2. Em loop: pede o termo de busca (ou lista favoritos direto),
           mostra os resultados na TUI, deixa selecionar 1+ itens.
           - Se for artista: abre um submenu de ações (ver álbuns/EPs,
             singles, top tracks, ou discografia inteira).
        3. Acumula tudo em `final_url_list` e pergunta se quer buscar mais.
        4. No final, pergunta a qualidade máxima e chama
           download_list_of_urls() com tudo que foi selecionado.

        `self._is_interactive_session = True` é usado por handle_url()
        para saber se deve oferecer o filtro extra de tipo de lançamento
        ao explorar um artista por URL (mesmo fora deste método)."""
        self._is_interactive_session = True

        qualities = [
            {"q_string": "320", "q": 5},
            {"q_string": "Lossless", "q": 6},
            {"q_string": "Hi-res =< 96kHz", "q": 7},
            {"q_string": "Hi-Res > 96 kHz", "q": 27},
        ]

        try:
            item_types = [
                "🎵 Tracks",
                "💿 Albums",
                "📀 Singles",
                "🎤 Artists",
                "📋 Playlists",
                "⭐ Favorites",
            ]

            scelta_res = await _tui_select(
                "O que você deseja buscar?",
                item_types,
                is_multi=False,
                item_category="filter",
            )
            if not scelta_res:
                return

            scelta_raw_visual, _ = scelta_res
            scelta_raw = scelta_raw_visual.split(" ", 1)[1]

            sub_filter = None
            if scelta_raw == "Favorites":
                selected_type = "favorites"
            elif scelta_raw == "Singles":
                selected_type = "album"
                sub_filter = ["single"]
            elif scelta_raw == "Albums":
                selected_type = "album"
                sub_filter = ["album", "ep", "compilation", "live"]
            else:
                selected_type = scelta_raw[:-1].lower()

            final_url_list = []
            session = PromptSession()

            while True:
                selected_fav = None
                if selected_type == "favorites":
                    fav_types = [
                        "🎵 Tracks",
                        "💿 Albums",
                        "📀 Singles",
                        "🎤 Artists",
                        "📋 Playlists",
                    ]
                    fav_res = await _tui_select(
                        "Quais favoritos deseja explorar?",
                        fav_types,
                        is_multi=False,
                        item_category="filter",
                    )
                    if not fav_res:
                        break

                    selected_fav_visual, _ = fav_res
                    selected_fav_raw = selected_fav_visual.split(" ", 1)[1]

                    if selected_fav_raw == "Singles":
                        selected_fav = "albums"
                        sub_filter = ["single"]
                        display_name = "Singles"
                    elif selected_fav_raw == "Albums":
                        selected_fav = "albums"
                        sub_filter = ["album", "ep", "compilation", "live"]
                        display_name = "Albums"
                    else:
                        selected_fav = selected_fav_raw.lower()
                        sub_filter = None
                        display_name = selected_fav_raw

                    logger.info(
                        f"{YELLOW}Buscando seus favoritos ({display_name})...{RESET}"
                    )
                    options = await self.search_by_type(
                        None,
                        selected_type,
                        limit=self.interactive_limit,
                        fav_subtype=selected_fav,
                        sub_filter=sub_filter,
                    )
                    query_title = f"Meus Favoritos ({display_name})"
                    display_cat = selected_fav[:-1]
                else:
                    sys.stdout.write("\033[2J\033[H")
                    sys.stdout.flush()

                    cols = min(shutil.get_terminal_size((80, 24)).columns, 80)
                    bar = "━" * cols
                    ui.emit(f"\n{CYAN}{bar}{RESET}")
                    ui.emit(f"{CYAN}  🔎 {GREEN}NOVA PESQUISA{RESET}")
                    ui.emit(f"{CYAN}{bar}{RESET}\n")

                    prompt_message = FormattedText(
                        [
                            ("class:prompt_text", " O que você deseja ouvir? "),
                            ("class:prompt_hint", "[Ctrl + C para cancelar]\n"),
                            ("class:prompt_cursor", " ❯ "),
                        ]
                    )

                    try:
                        query = await session.prompt_async(
                            prompt_message, style=prompt_style
                        )
                    except (EOFError, KeyboardInterrupt):
                        break

                    if not query.strip():
                        continue

                    ui.emit(f"\n{YELLOW}Pesquisando por '{query}'...{RESET}")
                    options = await self.search_by_type(
                        query,
                        selected_type,
                        self.interactive_limit,
                        sub_filter=sub_filter,
                    )
                    query_title = query.title()
                    display_cat = selected_type

                if not options:
                    ui.emit(
                        f"\n{RED}Nenhum resultado válido encontrado ou erro na busca.{RESET}"
                    )
                    if selected_type == "favorites":
                        await session.prompt_async(
                            FormattedText(
                                [
                                    (
                                        "class:prompt_hint",
                                        "Pressione [Enter] para voltar...",
                                    )
                                ]
                            )
                        )
                        break
                    await session.prompt_async(
                        FormattedText(
                            [
                                (
                                    "class:prompt_hint",
                                    "Pressione [Enter] para tentar novamente...",
                                )
                            ]
                        )
                    )
                    continue

                title = f'RESULTADOS PARA "{query_title}"'

                selected_items = await _tui_select(
                    title,
                    options,
                    is_multi=(display_cat != "artist"),
                    item_category=display_cat,
                )

                if selected_items:
                    if display_cat == "artist":
                        selected_items = [selected_items]

                    if len(selected_items) > 0:
                        if display_cat == "artist":
                            action_res = await _tui_select(
                                "O que você deseja explorar deste artista?",
                                [
                                    "💿 Explorar Álbuns e EPs",
                                    "📀 Explorar Singles",
                                    "🔥 Explorar Top Tracks",
                                    "📥 Baixar Toda a Discografia",
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
                                    async for chunk in self.client.get_artist_meta(
                                        art_id
                                    ):
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

                                elif "Discografia" in action:
                                    logger.info(
                                        f"{YELLOW}Buscando toda a discografia de {art_name}...{RESET}"
                                    )
                                    async for chunk in self.client.get_artist_meta(
                                        art_id
                                    ):
                                        for a in chunk.get("albums", {}).get(
                                            "items", []
                                        ):
                                            final_url_list.append(
                                                f"{WEB_URL}album/{a.get('id')}"
                                            )

                                else:
                                    logger.info(
                                        f"{YELLOW}Buscando catálogo de {art_name}...{RESET}"
                                    )
                                    content = []
                                    async for chunk in self.client.get_artist_meta(
                                        art_id
                                    ):
                                        content.extend(
                                            chunk.get("albums", {}).get("items", [])
                                        )

                                    if not content:
                                        logger.info(
                                            f"{RED}Nenhum álbum encontrado para {art_name}.{OFF}"
                                        )
                                        continue

                                    allowed_filter = (
                                        ["album", "ep", "compilation", "live"]
                                        if "Álbuns" in action
                                        else ["single"]
                                    )

                                    art_options = []
                                    for a in content:
                                        r_type = (
                                            a.get("release_type") or "album"
                                        ).lower()
                                        if r_type not in allowed_filter:
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

                                    if art_options:
                                        art_selected = await _tui_select(
                                            f"Lançamentos de {art_name}",
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
                                        logger.info(
                                            f"{YELLOW}Nenhum lançamento desse tipo encontrado.{OFF}"
                                        )
                        else:
                            [
                                final_url_list.append(item[0]["url"])
                                for item in selected_items
                            ]

                        yn_res = await _tui_select(
                            "Itens adicionados à fila. Deseja buscar mais?",
                            ["✅ Sim", "❌ Não"],
                            is_multi=False,
                            item_category="filter",
                        )
                        if not yn_res:
                            break
                        y_n, _ = yn_res
                        if "Não" in y_n:
                            break
                else:
                    logger.info(f"{YELLOW}Ok, vamos tentar de novo...{RESET}")
                    if selected_type == "favorites":
                        break
                    continue

            if final_url_list:
                qualities_texts = [f"🎚️ {q.get('q_string')}" for q in qualities]
                qual_res = await _tui_select(
                    "Selecione a qualidade máxima do download",
                    qualities_texts,
                    is_multi=False,
                    item_category="filter",
                )
                if not qual_res:
                    return
                selected_quality_visual, sq_idx = qual_res
                self.quality = qualities[sq_idx]["q"]

                if download:
                    sys.stdout.write("\033[2J\033[H")
                    sys.stdout.flush()
                    await self.download_list_of_urls(final_url_list)

                return final_url_list

        except KeyboardInterrupt:
            sys.stdout.write("\033[2J\033[H")
            logger.info(f"{YELLOW}Operação cancelada pelo usuário.{OFF}")
            return

    # O RESTANTE DAS FUNCÕES PODE SER MANTIDO IGUAL AO ANTERIOR...
    async def import_playlist_from_url_or_file(
        self,
        source: str,
        name: str = None,
        auto: bool = False,
    ):
        """Comando `import-playlist`/`ip`: importa uma playlist de outra
        plataforma (URL do Spotify/Deezer/Apple Music) ou de um arquivo
        exportado (TXT/CSV/JSON). Faz o "matching" das faixas no Qobuz e
        então pergunta ao usuário o que fazer: (1) baixar, (2) criar a
        mesma playlist na conta Qobuz, ou (3) as duas coisas.

        Delega a extração de faixas para:
          - platform_fetcher.fetch_playlist_from_url()  (se `source` é URL)
          - playlist_import.parse_playlist_file()       (se é arquivo local)
        """
        import shutil as _shutil

        is_url = source.startswith("http://") or source.startswith("https://")
        platform_label = "arquivo"
        tracks_list = []
        playlist_name = name or "Playlist"

        if is_url:
            from qobuz_dl.platform_fetcher import fetch_playlist_from_url

            logger.info(f"{CYAN}[*] Buscando playlist de: {source}{OFF}")
            try:
                playlist_data = await fetch_playlist_from_url(source)
            except ValueError as e:
                logger.info(f"{RED}[!] {e}{OFF}")
                return
            platform_label = playlist_data["platform"].replace("_", " ").title()
            tracks_list = playlist_data["tracks"]
            playlist_name = name or playlist_data["name"]
            logger.info(
                f"{GREEN}[+] Playlist encontrada ({platform_label}): "
                f"'{playlist_name}' -- {len(tracks_list)} faixas{OFF}"
            )
        else:
            from qobuz_dl.playlist_import import parse_playlist_file

            logger.info(f"{CYAN}[*] Lendo arquivo: {source}{OFF}")
            try:
                tracks_list = parse_playlist_file(source)
            except (FileNotFoundError, ValueError) as e:
                logger.info(f"{RED}[!] {e}{OFF}")
                return
            playlist_name = name or os.path.splitext(os.path.basename(source))[0]
            logger.info(f"{GREEN}[+] {len(tracks_list)} faixas encontradas.{OFF}")

        if not tracks_list:
            logger.info(f"{YELLOW}[!] Nenhuma faixa encontrada.{OFF}")
            return

        cols = min(_shutil.get_terminal_size((70, 24)).columns, 90)
        bar = "━" * cols
        ui.emit(f"\n{CYAN}{bar}{OFF}")
        ui.emit(f"\033[1m{CYAN}{'  O QUE DESEJA FAZER?':^{cols}}{OFF}")
        ui.emit(f"{CYAN}{bar}{OFF}\n")
        ui.emit(
            f"  {CYAN}[1]{OFF} Baixar faixas             (matching Qobuz → download)"
        )
        ui.emit(
            f"  {CYAN}[2]{OFF} Copiar para o Qobuz       (cria playlist na sua conta)"
        )
        ui.emit(
            f"  {CYAN}[3]{OFF} As duas                   (baixar + criar playlist no Qobuz)"
        )
        ui.emit(f"  {CYAN}[0]{OFF} Cancelar")
        ui.emit()

        while True:
            choice = input("  Escolha (0-3): ").strip()
            if choice in ("0", "1", "2", "3"):
                break
            ui.emit(f"  {YELLOW}Por favor, escolha entre 0 e 3.{OFF}")

        if choice == "0":
            logger.info(f"{YELLOW}[*] Cancelado.{OFF}")
            return

        do_download = choice in ("1", "3")
        do_copy_qobuz = choice in ("2", "3")

        logger.info(
            f"\n{CYAN}[*] Fazendo matching de {len(tracks_list)} faixas no Qobuz...{OFF}"
        )
        track_ids = await self.client.get_track_ids_from_list(tracks_list)

        if not track_ids:
            logger.info(f"{RED}[!] Nenhuma faixa encontrada no Qobuz. Encerrando.{OFF}")
            return

        logger.info(
            f"{GREEN}[+] {len(track_ids)} de {len(tracks_list)} "
            f"faixas encontradas no Qobuz.{OFF}"
        )

        if do_download:
            await self.download_from_playlist_file(
                file_path=source if not is_url else None,
                name=playlist_name,
                auto=auto,
                _preloaded_track_ids=track_ids,
            )

        if do_copy_qobuz:
            logger.info(
                f"\n{CYAN}[*] Criando playlist '{playlist_name}' no Qobuz...{OFF}"
            )
            pl_id = await self.client.create_qobuz_playlist(
                name=playlist_name,
                description=f"Importada de {platform_label} via qobuz-dl-ultra",
                is_public=False,
            )
            if pl_id:
                ok = await self.client.add_tracks_to_qobuz_playlist(pl_id, track_ids)
                if ok:
                    logger.info(
                        f"{GREEN}[+] {len(track_ids)} faixas adicionadas à playlist "
                        f"'{playlist_name}' no Qobuz!{OFF}"
                    )
                else:
                    logger.info(
                        f"{YELLOW}[!] Algumas faixas podem não ter sido adicionadas.{OFF}"
                    )

    async def download_from_playlist_file(
        self,
        file_path: str = None,
        name: str = None,
        auto: bool = False,
        _preloaded_track_ids: list = None,
    ):
        """Baixa as faixas já "matcheadas" no Qobuz (track_ids) numa pasta
        própria da playlist. Pode ser chamado com um arquivo ainda não
        processado (`file_path`, faz o parsing+matching aqui mesmo) ou já
        com os IDs prontos (`_preloaded_track_ids`, usado por
        import_playlist_from_url_or_file() quando a opção "1. Baixar
        faixas" foi escolhida, pra não repetir o matching).

        Usa o mesmo padrão de paralelismo (semáforo + stagger delay)
        descrito no cabeçalho do arquivo, igual handle_url() e
        download_list_of_urls()."""
        from qobuz_dl.playlist_import import parse_playlist_file

        if _preloaded_track_ids is not None:
            track_ids = _preloaded_track_ids
            playlist_name = name or "Playlist"
            pl_directory = os.path.join(
                self.directory, sanitize_filename(playlist_name)
            )
        else:
            logger.info(f"{CYAN}[*] Importando playlist: {file_path}{OFF}")
            try:
                tracks_list = parse_playlist_file(file_path)
            except (FileNotFoundError, ValueError) as e:
                logger.info(f"{RED}[!] Erro ao ler o arquivo: {e}{OFF}")
                return
            if not tracks_list:
                logger.info(f"{YELLOW}[!] Nenhuma faixa encontrada no arquivo.{OFF}")
                return
            logger.info(
                f"{CYAN}[*] {len(tracks_list)} entradas encontradas. "
                f"Iniciando matching no Qobuz...{OFF}"
            )
            playlist_name = name or os.path.splitext(os.path.basename(file_path))[0]
            pl_directory = os.path.join(
                self.directory, sanitize_filename(playlist_name)
            )
            track_ids = await self.client.get_track_ids_from_list(tracks_list)

        if not track_ids:
            logger.info(f"{RED}[!] Nenhuma faixa encontrada no Qobuz. Encerrando.{OFF}")
            return

        logger.info(
            f"{GREEN}[+] {len(track_ids)} de {len(tracks_list)} faixas "
            f"encontradas no Qobuz.{OFF}"
        )

        original_folder_format = self.folder_format
        original_multi_disc_setting = self.settings.multiple_disc_one_dir

        if not getattr(self, "playlist_as_albums", False):
            self.folder_format = "."
            self.settings.multiple_disc_one_dir = True

        batch_workers = int(getattr(self.settings, "max_workers", 1))
        can_parallelize = (
            batch_workers > 1 and len(track_ids) > 1 and getattr(self, "delay", 0) <= 0
        )
        position_pool = (
            downloader._PositionPool(batch_workers) if can_parallelize else None
        )
        semaphore = asyncio.Semaphore(batch_workers) if can_parallelize else None
        pending_tasks = []

        from qobuz_dl.downloader import print_download_header

        mode_label = (
            f"Paralelo ({batch_workers} workers)" if can_parallelize else "Sequencial"
        )
        print_download_header(
            "PLAYLIST IMPORTADA",
            [
                ("Nome", playlist_name),
                ("Faixas", str(len(track_ids))),
                ("Modo", mode_label),
            ],
        )

        for idx, track_id in enumerate(track_ids):
            if can_parallelize:
                track_id_captured = track_id
                idx_captured = idx

                async def _bounded_track_download(
                    t_id=track_id_captured, t_idx=idx_captured
                ):
                    if t_idx < batch_workers:
                        await asyncio.sleep(t_idx * HEADER_STAGGER_DELAY)
                    async with semaphore:
                        await self.download_from_id(
                            t_id,
                            album=False,
                            alt_path=pl_directory,
                            is_playlist=True,
                            playlist_index=t_idx,
                            is_parallel=True,
                            position_pool=position_pool,
                            suppress_header=True,
                        )

                pending_tasks.append(_bounded_track_download())
            else:
                await self.download_from_id(
                    track_id,
                    album=False,
                    alt_path=pl_directory,
                    is_playlist=True,
                    playlist_index=idx,
                    is_parallel=False,
                    position_pool=None,
                    suppress_header=True,
                )

        if pending_tasks:
            await asyncio.gather(*pending_tasks)

        self.folder_format = original_folder_format
        self.settings.multiple_disc_one_dir = original_multi_disc_setting

        succ = getattr(self.settings, "pl_success", 0)
        skip = getattr(self.settings, "pl_skipped", 0)
        fail = getattr(self.settings, "pl_failed", 0)

        from qobuz_dl.downloader import safe_print

        safe_print(f"\n{CYAN}{'━' * 44}{RESET}")
        safe_print(f"  📊 {GREEN}RESUMO DA PLAYLIST IMPORTADA:{RESET} {playlist_name}")
        safe_print(f"   • Sucesso : {GREEN}{succ}/{len(track_ids)}{RESET}")
        if skip > 0:
            safe_print(f"   • Puladas : {YELLOW}{skip}{RESET} (Já baixado/Demo)")
        if fail > 0:
            safe_print(f"   • Falhas  : {RED}{fail}{RESET}")
        safe_print(f"{CYAN}{'━' * 44}{RESET}\n")
