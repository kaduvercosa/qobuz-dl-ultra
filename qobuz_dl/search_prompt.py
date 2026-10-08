# ============================================================================
# search_prompt.py -- tela de busca do modo interativo ("NOVA PESQUISA").
#
# O campo de texto fica dentro de um retângulo arredondado:
#
#   ╭─ 🔎 NOVA PESQUISA ───────────────────────────────╮
#   │ O que você deseja ouvir?  [Ctrl + C para cancelar]│
#   │ Buscando em: Faixas                                │
#   │  ❯ _                                               │
#   ╰────────────────────────────────────────────────────╯
#
# Se qualquer coisa der errado ao montar/desenhar a caixa (versão antiga do
# prompt_toolkit, terminal exótico...), ask_query() volta sozinho ao prompt
# simples de antes: a busca nunca deixa de funcionar por causa do visual.
# ============================================================================
from __future__ import annotations

import logging
import os
import shutil
import sys

logger = logging.getLogger(__name__)

# Defina QOBUZ_DL_SIMPLE_PROMPT=1 para voltar ao prompt simples (sem caixa).
SIMPLE_PROMPT_ENV = "QOBUZ_DL_SIMPLE_PROMPT"

MIN_BOX_WIDTH = 30
HORIZONTAL_MARGIN = 2

# Rótulo (como vem do menu de tipo) -> texto em português para a caixa.
_KIND_LABELS = {
    "Tracks": "Faixas",
    "Albums": "Álbuns",
    "Singles": "Singles",
    "Artists": "Artistas",
    "Playlists": "Playlists",
    "Favorites": "Favoritos",
}

_warned = False


def _warn_once(message: str) -> None:
    """Mostra o motivo do fallback uma única vez por execução."""
    global _warned
    if not _warned:
        _warned = True
        logger.warning(message)


# Histórico das buscas desta sessão (↑ / ↓ no campo recuperam buscas antigas).
_history = None


def kind_label(raw) -> str:
    """'Tracks' -> 'Faixas' (rótulos desconhecidos passam como vieram)."""
    text = str(raw or "").strip()
    # o menu pode trazer emoji na frente: "🎵 Tracks"
    word = text.split(" ", 1)[-1] if " " in text else text
    return _KIND_LABELS.get(word, word)


def box_enabled(environ=None) -> bool:
    """A caixa fica ligada, exceto quando o usuário pede o prompt simples
    (QOBUZ_DL_SIMPLE_PROMPT=1) ou durante os testes (pytest define
    PYTEST_CURRENT_TEST e os testes trocam o PromptSession por um falso)."""
    env = os.environ if environ is None else environ
    if str(env.get(SIMPLE_PROMPT_ENV, "")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return False
    return "PYTEST_CURRENT_TEST" not in env


def box_width(columns: int) -> int:
    """Largura da caixa usando todo o espaço útil do terminal"""
    columns = max(1, int(columns))
    usable_width = columns - (HORIZONTAL_MARGIN * 2)
    return max(MIN_BOX_WIDTH, usable_width)


def _get_history():
    global _history
    if _history is None:
        from prompt_toolkit.history import InMemoryHistory

        _history = InMemoryHistory()
    return _history


def _build_application(kind: str):
    """Monta a Application com a caixa. Imports aqui dentro: este módulo pode
    ser importado (e testado) sem o prompt_toolkit instalado."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import FormattedText
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import Layout
    from prompt_toolkit.layout.containers import HSplit, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.styles import Style, merge_styles
    from prompt_toolkit.widgets import Frame, TextArea
    from prompt_toolkit.widgets.base import Border

    from qobuz_dl.interactive_ui import _hex_accent, prompt_style

    width = box_width(shutil.get_terminal_size((80, 24)).columns)
    app_holder = {}

    def _accept(buffer):
        app_holder["app"].exit(result=buffer.text)
        return True  # mantém o texto no campo (fica na tela depois do Enter)

    field = TextArea(
        multiline=False,
        wrap_lines=False,
        height=1,
        history=_get_history(),
        accept_handler=_accept,
        prompt=FormattedText([("class:prompt_cursor", " ❯ ")]),
        style="class:search.input",
    )

    title_line = Window(
        FormattedTextControl(
            FormattedText(
                [
                    ("class:prompt_text", " O que você deseja ouvir? "),
                    ("class:prompt_hint", "[Ctrl + C para cancelar]"),
                ]
            )
        ),
        height=1,
    )
    kind_line = Window(
        FormattedTextControl(
            FormattedText([("class:prompt_hint", f" Buscando em: {kind}")])
        ),
        height=1,
    )

    # Cantos arredondados só enquanto o Frame é construído (os caracteres de
    # borda são lidos na construção); depois restauramos o padrão da biblioteca.
    corners = {
        "TOP_LEFT": "╭",
        "TOP_RIGHT": "╮",
        "BOTTOM_LEFT": "╰",
        "BOTTOM_RIGHT": "╯",
    }
    original = {name: getattr(Border, name, None) for name in corners}
    try:
        for name, char in corners.items():
            if original[name] is not None:
                setattr(Border, name, char)
        frame = Frame(
            HSplit([title_line, kind_line, field]),
            title=" 🔎 NOVA PESQUISA ",
            style="class:search.frame",
        )
    finally:
        for name, char in original.items():
            if char is not None:
                setattr(Border, name, char)

    box = HSplit(
        [frame],
        width=Dimension(preferred=width, max=width),
    )
    root = HSplit(
        [
            Window(height=Dimension.exact(0)),
            box,
        ]
    )

    bindings = KeyBindings()

    @bindings.add("c-c")
    def _cancel(event):
        event.app.exit(exception=KeyboardInterrupt)

    @bindings.add("c-d")
    def _eof(event):
        if not field.text:
            event.app.exit(exception=EOFError)

    box_style = Style.from_dict(
        {
            # Fundo neutron e legível, independente do tema padrão do a-shell
            "search.frame": "bg:default fg:default",
            "search.input": "bg:default fg:default",
            "prompt_text": "bg:default fg:default",
            "prompt_hint": "bg:default fg:#808080",
            "prompt_cursor": f"bg:default fg:{_hex_accent} bold",
            # A cor escolhida no config.ini fica apenas na moldura e título
            "frame.border": f"bg:default fg:{_hex_accent} bold",
            "frame.label": f"bg:default fg:{_hex_accent} bold",
        }
    )
    app = Application(
        layout=Layout(root, focused_element=field),
        key_bindings=bindings,
        style=merge_styles([prompt_style, box_style]),
        full_screen=False,
        mouse_support=False,
        erase_when_done=False,
    )
    app_holder["app"] = app
    return app


async def ask_query(kind_raw, fallback, build_app=None):
    """Pergunta o termo de busca numa caixa. Devolve o texto digitado.

    Ctrl+C levanta KeyboardInterrupt e Ctrl+D (campo vazio) levanta EOFError,
    como o prompt antigo. Qualquer outro erro vira um aviso em debug e a
    pergunta é refeita com `fallback()` (corrotina com o prompt simples).
    """
    if build_app is None and not box_enabled():
        return await fallback()
    try:
        app = (build_app or _build_application)(kind_label(kind_raw))
        sys.stdout.write("\n")
        sys.stdout.flush()
        return await app.run_async()
    except (KeyboardInterrupt, EOFError):
        raise
    except Exception as exc:
        _warn_once(
            "Caixa de busca indisponível "
            f"({type(exc).__name__}: {exc}); usando o prompt simples."
        )
        return await fallback()
