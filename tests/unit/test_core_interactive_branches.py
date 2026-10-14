"""Testes de ramos interativos ainda não cobertos em core.py."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


class Prompt:
    def __init__(self, values):
        self.values = iter(values)

    async def prompt_async(self, *args, **kwargs):
        try:
            return next(self.values)
        except StopIteration:
            raise KeyboardInterrupt from None


async def _done(*args, **kwargs):
    return None


def _app():
    return SimpleNamespace(
        interactive_limit=10,
        quality=6,
        client=SimpleNamespace(),
        _is_interactive_session=False,
        download_list_of_urls=_done,
    )


async def test_interactive_favoritos_albums(monkeypatch):
    app = _app()
    options = [
        {"meta": {"title": "Album", "id": "a1", "type": "Album"}, "url": "album-url"}
    ]
    selections = iter(
        [
            ("⭐ Favorites", 0),
            ("💿 Albums", 1),
            [(options[0], 0)],
            ("❌ Não", 1),
            ("🎚️ Lossless", 1),
        ]
    )

    async def tui(*args, **kwargs):
        return next(selections)

    async def search_by_type(*args, **kwargs):
        return options

    app.search_by_type = search_by_type
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt([]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result == ["album-url"]


async def test_interactive_favoritos_playlists(monkeypatch):
    app = _app()
    options = [{"meta": {"name": "Playlist", "id": "p1"}, "url": "playlist-url"}]
    selections = iter(
        [
            ("⭐ Favorites", 0),
            ("📋 Playlists", 4),
            [(options[0], 0)],
            ("❌ Não", 1),
            ("🎚️ MP3", 0),
        ]
    )

    async def tui(*args, **kwargs):
        return next(selections)

    async def search_by_type(*args, **kwargs):
        return options

    app.search_by_type = search_by_type
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt([]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result == ["playlist-url"]


async def test_interactive_artista_singles(monkeypatch):
    app = _app()
    artist = [{"meta": {"name": "Artist", "id": "a1"}, "url": "artist-url"}]
    selections = iter(
        [
            ("🎤 Artists", 0),
            (artist[0], 0),
            ("📀 Explorar Singles", 1),
            ("❌ Não", 1),
            ("🎚️ MP3", 0),
        ]
    )

    async def tui(*args, **kwargs):
        # A seleção de lançamentos precisa devolver None para simular
        # cancelamento/nenhum item selecionado, sem encerrar o método
        # interactive antes do fluxo de qualidade.
        if kwargs.get("item_category") == "album":
            return None
        return next(selections)

    async def search_by_type(*args, **kwargs):
        return artist

    async def get_artist_meta(item_id):
        yield {
            "albums": {
                "items": [
                    {"id": "s1", "title": "Single", "tracks_count": 1},
                ],
            },
        }

    def metadata(self, item, item_type, mode_dict, fav_subtype=None):
        return {
            "id": item.get("id"),
            "title": item.get("title", ""),
            "artist": "Unknown",
            "type": "Single",
        }

    app.client.get_artist_meta = get_artist_meta
    app.search_by_type = search_by_type
    app._extract_rich_metadata = metadata.__get__(app)
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt(["artist"]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result is None
