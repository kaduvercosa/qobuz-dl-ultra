"""Testes dos caminhos de seleção e download em interactive()."""

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


def _metadata(self, item, item_type, mode_dict, fav_subtype=None):
    return {
        "id": item.get("id"),
        "title": item.get("title", "Unknown"),
        "artist": item.get("artist", {}).get("name", "Unknown"),
        "album": item.get("album", {}).get("title", "Unknown Album"),
        "type": "Track",
        "quality": "16b/44.1kHz",
        "duration": "--:--",
    }


def _app():
    app = SimpleNamespace(
        interactive_limit=10,
        quality=6,
        client=SimpleNamespace(),
        _is_interactive_session=False,
    )
    app._extract_rich_metadata = _metadata.__get__(app)
    return app


async def test_interactive_track_seleciona_e_nao_baixa(monkeypatch):
    app = _app()
    calls = []
    options = [{"meta": {"title": "Track", "id": "1"}, "url": "track-url"}]
    selections = iter([
        ("🎵 Tracks", 0),
        [(options[0], 0)],
        ("❌ Não", 1),
        ("🎚️ Lossless", 1),
    ])

    async def tui(*args, **kwargs):
        return next(selections)

    async def search_by_type(*args, **kwargs):
        return options

    async def download_list(urls):
        calls.append(urls)

    app.search_by_type = search_by_type
    app.download_list_of_urls = download_list
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt(["query"]))

    result = await core.QobuzDL.interactive(app, download=True)

    assert result == ["track-url"]
    assert calls == [["track-url"]]
    assert app.quality == 6


async def test_interactive_track_seleciona_sem_download(monkeypatch):
    app = _app()
    options = [{"meta": {"title": "Track", "id": "1"}, "url": "track-url"}]
    selections = iter([
        ("🎵 Tracks", 0),
        [(options[0], 0)],
        ("❌ Não", 1),
        ("🎚️ MP3", 0),
    ])

    async def tui(*args, **kwargs):
        return next(selections)

    async def search_by_type(*args, **kwargs):
        return options

    app.search_by_type = search_by_type
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt(["query"]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result == ["track-url"]
    assert app.quality == 5


async def test_interactive_artista_discografia(monkeypatch):
    app = _app()
    options = [{"meta": {"name": "Artista", "id": "a1"}, "url": "artist-url"}]
    selections = iter([
        ("🎤 Artists", 0),
        (options[0], 0),
        ("📥 Baixar Toda a Discografia", 3),
        ("❌ Não", 1),
        ("🎚️ Lossless", 1),
    ])

    async def tui(*args, **kwargs):
        return next(selections)

    async def get_artist_meta(item_id):
        yield {"albums": {"items": [{"id": "a1"}, {"id": "a2"}]}}

    async def search_by_type(*args, **kwargs):
        return options

    app.client.get_artist_meta = get_artist_meta
    app.search_by_type = search_by_type
    app.download_list_of_urls = _done
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt(["artist"]))

    result = await core.QobuzDL.interactive(app, download=True)

    assert result == [
        "https://play.qobuz.com/album/a1",
        "https://play.qobuz.com/album/a2",
    ]


async def test_interactive_artista_top_tracks(monkeypatch):
    app = _app()
    options = [{"meta": {"name": "Artista", "id": "a1"}, "url": "artist-url"}]
    selections = iter([
        ("🎤 Artists", 0),
        (options[0], 0),
        ("🔥 Explorar Top Tracks", 2),
        ("❌ Não", 1),
        ("🎚️ Lossless", 1),
    ])

    async def tui(title, values, **kwargs):
        if "Faixas" in title:
            return [
                ({"meta": {"title": "Hit", "id": "t1"}, "url": "track-url"}, 0),
            ]
        return next(selections)

    async def get_artist_meta(item_id):
        yield {"tracks": {"items": [{"id": "t1", "title": "Hit"}]}}

    async def search_by_type(*args, **kwargs):
        return options

    async def search_tracks(*args, **kwargs):
        return {"tracks": {"items": []}}

    app.client.get_artist_meta = get_artist_meta
    app.client.search_tracks = search_tracks
    app.search_by_type = search_by_type
    app.download_list_of_urls = _done
    monkeypatch.setattr(core, "_tui_select", tui)
    monkeypatch.setattr(core, "PromptSession", lambda: Prompt(["artist"]))

    result = await core.QobuzDL.interactive(app, download=False)

    assert result == ["track-url"]
