"""Testes unitários para os caminhos interativos de core.py."""

from types import SimpleNamespace

import os
import shutil

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


class FakeSize:
    columns = 100
    rows = 40


class FakeOutput:
    def get_size(self):
        return FakeSize()


class FakeApp:
    def __init__(self, result=None, exception=None):
        self.output = FakeOutput()
        self.result = result
        self.exception = exception
        self.invalidated = False
        self.exited = None
        self.key_bindings = None

    def invalidate(self):
        self.invalidated = True

    def exit(self, result=None, exception=None):
        self.exited = (result, exception)


class FakeApplication:
    last = None

    def __init__(self, layout, key_bindings, full_screen, style, mouse_support):
        self.layout = layout
        self.key_bindings = key_bindings
        self.result = FakeApplication.result
        FakeApplication.last = self

    async def run_async(self):
        if self.result is Exception:
            raise KeyboardInterrupt
        return self.result


async def test_tui_select_lista_vazia_retorna_none(monkeypatch):
    FakeApplication.result = None
    monkeypatch.setattr(core, "Application", FakeApplication)

    result = await core._tui_select("Título", [], is_multi=False, item_category="filter")

    assert result is None


async def test_tui_select_retorna_selecao_simples(monkeypatch):
    FakeApplication.result = ("opção", 0)
    monkeypatch.setattr(core, "Application", FakeApplication)

    result = await core._tui_select(
        "Título",
        ["opção"],
        is_multi=False,
        item_category="filter",
    )

    assert result == ("opção", 0)


async def test_tui_select_retorna_selecao_multipla(monkeypatch):
    FakeApplication.result = [("a", 0), ("b", 1)]
    monkeypatch.setattr(core, "Application", FakeApplication)

    result = await core._tui_select(
        "Título",
        ["a", "b"],
        is_multi=True,
        item_category="filter",
    )

    assert result == [("a", 0), ("b", 1)]


async def test_tui_select_propaga_keyboard_interrupt(monkeypatch):
    FakeApplication.result = Exception
    monkeypatch.setattr(core, "Application", FakeApplication)

    with pytest.raises(KeyboardInterrupt):
        await core._tui_select("Título", ["a"], item_category="filter")


async def test_tui_renderiza_cabecalho_lista_e_rodape(monkeypatch):
    FakeApplication.result = None
    monkeypatch.setattr(core, "Application", FakeApplication)

    await core._tui_select(
        "Álbuns",
        [
            {"meta": {"title": "Disco", "artist": "Artista", "type": "Album", "year": "2024", "tracks_count": 10, "quality": "24b/96kHz"}},
        ],
        is_multi=True,
        item_category="album",
    )

    application = FakeApplication.last
    assert application is not None
    assert application.layout is not None
    assert application.key_bindings is not None


async def test_tui_renderiza_categorias_de_cartao(monkeypatch):
    FakeApplication.result = None
    monkeypatch.setattr(core, "Application", FakeApplication)

    for category, option in [
        ("album", {"meta": {"title": "A", "artist": "B", "type": "Album", "year": "2024", "tracks_count": 1, "quality": "16b/44.1kHz"}}),
        ("track", {"meta": {"title": "T", "artist": "B", "album": "A", "type": "Track", "duration": "03:00", "quality": "16b/44.1kHz"}}),
        ("playlist", {"meta": {"name": "P", "owner": "O", "count": 2, "duration": "04:00"}}),
        ("artist", {"meta": {"name": "Artista", "count": 3}}),
    ]:
        await core._tui_select(
            category,
            [option],
            is_multi=False,
            item_category=category,
        )

    assert FakeApplication.last is not None


async def test_tui_key_bindings_existem(monkeypatch):
    FakeApplication.result = None
    monkeypatch.setattr(core, "Application", FakeApplication)

    await core._tui_select("Título", ["a", "b"], is_multi=True, item_category="filter")

    bindings = FakeApplication.last.key_bindings
    assert bindings is not None
    assert len(bindings.bindings) >= 15


async def test_tui_header_fallback_terminal(monkeypatch):
    FakeApplication.result = None
    monkeypatch.setattr(core, "Application", FakeApplication)
    monkeypatch.setattr(core, "get_app", lambda: (_ for _ in ()).throw(RuntimeError("sem app")))
    monkeypatch.setattr(shutil, "get_terminal_size", lambda *args, **kwargs: os.terminal_size((80, 24)),)


    await core._tui_select("Título", ["a"], item_category="filter")
