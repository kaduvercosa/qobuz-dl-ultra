"""Testes dos fluxos interativos de QobuzDL.interactive()."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


class PromptFake:
    def __init__(self, values):
        self.values = iter(values)

    async def prompt_async(self, *args, **kwargs):
        try:
            return next(self.values)
        except StopIteration:
            raise KeyboardInterrupt


def _base_app():
    return SimpleNamespace(
        interactive_limit=5,
        quality=6,
        client=SimpleNamespace(),
        download_list_of_urls=None,
        _is_interactive_session=False,
    )


async def _empty_search(*args, **kwargs):
    return []


async def test_interactive_cancela_na_primeira_selecao(monkeypatch):
    app = _base_app()

    async def tui(*args, **kwargs):
        return None

    monkeypatch.setattr(core, "_tui_select", tui)

    result = await core.QobuzDL.interactive(app, download=False)

    assert result is None
    assert app._is_interactive_session is True


async def test_interactive_pesquisa_sem_resultados_volta(monkeypatch):
    app = _base_app()
    selections = iter([
        ("🎵 Tracks", 0),
        None,
    ])

    async def tui(*args, **kwargs):
        return next(selections)

    app.search_by_type = _empty_search
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: PromptFake(["query"]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result is None


async def test_interactive_busca_e_cancela_apos_resultados(monkeypatch):
    app = _base_app()
    selections = iter([
        ("🎵 Tracks", 0),
        None,
    ])

    async def tui(*args, **kwargs):
        return next(selections)

    app.search_by_type = _empty_search
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: PromptFake(["query"]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result is None


async def test_interactive_favoritos_sem_resultados(monkeypatch):
    app = _base_app()
    selections = iter([
        ("⭐ Favorites", 0),
        ("🎵 Tracks", 0),
        None,
    ])

    async def tui(*args, **kwargs):
        return next(selections)

    app.search_by_type = _empty_search
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: PromptFake([]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result is None


async def test_interactive_keyboard_interrupt_retorna_none(monkeypatch):
    app = _base_app()

    async def tui(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(core, "_tui_select", tui)

    result = await core.QobuzDL.interactive(app, download=False)

    assert result is None
