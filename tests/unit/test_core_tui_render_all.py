"""Exercita os renderizadores internos de _tui_select em todos os modos."""

import os
import shutil

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


class Size:
    columns = 40
    rows = 20


class Output:
    def get_size(self):
        return Size()


class FakeApplication:
    last = None

    def __init__(self, layout, key_bindings, **kwargs):
        self.output = Output()
        self.layout = layout
        self.key_bindings = key_bindings
        FakeApplication.last = self

    async def run_async(self):
        return None


def _render_windows():
    layout = FakeApplication.last.layout
    children = getattr(layout.container, "children", [])
    return children


@pytest.mark.parametrize(
    "category,options",
    [
        ("filter", ["Filtro", "Outro"]),
        (
            "album",
            [
                {
                    "meta": {
                        "title": "A",
                        "artist": "B",
                        "type": "Album",
                        "year": "2024",
                        "tracks_count": 4,
                        "quality": "16b/44.1kHz",
                    }
                }
            ],
        ),
        (
            "track",
            [
                {
                    "meta": {
                        "title": "T",
                        "artist": "B",
                        "album": "A",
                        "type": "Track",
                        "duration": "03:00",
                        "quality": "16b/44.1kHz",
                    }
                }
            ],
        ),
        (
            "playlist",
            [{"meta": {"name": "P", "owner": "O", "count": 2, "duration": "04:00"}}],
        ),
        ("artist", [{"meta": {"name": "Artist", "count": 2}}]),
    ],
)
async def test_renderers_for_all_categories(monkeypatch, category, options):
    monkeypatch.setattr(core, "Application", FakeApplication)
    await core._tui_select("Título", options, is_multi=True, item_category=category)

    assert FakeApplication.last is not None
    for window in _render_windows():
        control = getattr(window, "content", None)
        callback = getattr(control, "text", None)
        if callable(callback):
            rendered = callback()
            assert isinstance(rendered, list)


async def test_renderers_fallback_largura_e_texto_longo(monkeypatch):
    monkeypatch.setattr(core, "Application", FakeApplication)
    monkeypatch.setattr(
        core, "get_app", lambda: (_ for _ in ()).throw(RuntimeError("no app"))
    )
    monkeypatch.setattr(
        shutil,
        "get_terminal_size",
        lambda *args, **kwargs: os.terminal_size((20, 10)),
    )

    await core._tui_select(
        "Título muito longo",
        [
            {
                "meta": {
                    "title": "X" * 200,
                    "artist": "Y" * 200,
                    "type": "Album",
                    "year": "2024",
                    "tracks_count": 99,
                    "quality": "24b/96kHz",
                }
            }
        ],
        is_multi=False,
        item_category="album",
    )

    assert FakeApplication.last is not None


@pytest.mark.parametrize(
    ("category", "sub_filter", "expected"),
    [
        ("track", None, 'FAIXAS · RESULTADOS PARA "Baco Exu"'),
        ("album", None, 'ÁLBUNS · RESULTADOS PARA "Baco Exu"'),
        ("album", ["single"], 'SINGLES · RESULTADOS PARA "Baco Exu"'),
        ("artist", None, 'ARTISTAS · RESULTADOS PARA "Baco Exu"'),
        ("playlist", None, 'PLAYLISTS · RESULTADOS PARA "Baco Exu"'),
    ],
)
def test_titulo_de_resultados_identifica_categoria(category, sub_filter, expected):
    assert core._search_results_title("Baco Exu", category, sub_filter) == expected


@pytest.mark.parametrize("columns", [40, 60])
async def test_legenda_compacta_explica_atalhos_sem_exceder_largura(
    monkeypatch, columns
):
    monkeypatch.setattr(Size, "columns", columns)
    monkeypatch.setattr(core, "Application", FakeApplication)
    monkeypatch.setattr(core, "get_app", lambda: FakeApplication.last)
    options = [{"meta": {"title": "A", "artist": "B", "type": "Album"}}]

    await core._tui_select("Álbuns", options, is_multi=True, item_category="album")

    footer = _render_windows()[-2]
    rendered = footer.content.text()
    text = "".join(value for _style, value in rendered)
    for label in (
        "Item 1/1",
        "Sel.:",
        "mover",
        "marcar",
        "todos",
        "A-Z",
        "ano",
        "nova",
        "artista",
        "menu",
        "confirmar",
    ):
        assert label in text
    for _style, line in rendered:
        for visual_line in line.splitlines():
            assert core.get_cwidth(visual_line) <= columns
    footer_dimension = footer.height()
    assert footer_dimension.preferred == text.count("\n")
