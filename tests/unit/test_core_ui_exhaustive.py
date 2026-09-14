"""Exercita combinações adicionais dos renderizadores da TUI."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


class Size:
    columns = 120
    rows = 40


class Output:
    def get_size(self):
        return Size()


class App:
    last = None

    def __init__(self, layout, key_bindings, **kwargs):
        self.output = Output()
        self.layout = layout
        self.key_bindings = key_bindings
        App.last = self

    async def run_async(self):
        return None


def _callbacks():
    layout = App.last.layout
    children = getattr(layout.container, "children", [])
    result = []
    for window in children:
        control = getattr(window, "content", None)
        callback = getattr(control, "text", None)
        if callable(callback):
            result.append(callback)
    return result


async def test_album_table_checked_and_hovered(monkeypatch):
    monkeypatch.setattr(core, "Application", App)
    options = [
        {"meta": {"title": "A", "artist": "B", "type": "Album", "year": "2024", "tracks_count": 4, "quality": "24b/96kHz"}},
        {"meta": {"title": "C", "artist": "D", "type": "EP", "year": "2023", "tracks_count": 2, "quality": "16b/44.1kHz"}},
    ]
    await core._tui_select("Álbuns", options, is_multi=True, item_category="album")
    callbacks = _callbacks()
    for callback in callbacks:
        value = callback()
        assert isinstance(value, list)


async def test_track_table_checked_and_hovered(monkeypatch):
    monkeypatch.setattr(core, "Application", App)
    options = [
        {"meta": {"title": "Track", "artist": "Artist", "album": "Album", "type": "Track", "duration": "03:00", "quality": "24b/96kHz"}},
    ]
    await core._tui_select("Tracks", options, is_multi=True, item_category="track")
    for callback in _callbacks():
        assert isinstance(callback(), list)


async def test_playlist_table_checked_and_hovered(monkeypatch):
    monkeypatch.setattr(core, "Application", App)
    options = [
        {"meta": {"name": "Playlist", "owner": "Owner", "count": 10, "duration": "01:00:00"}},
    ]
    await core._tui_select("Playlists", options, is_multi=True, item_category="playlist")
    for callback in _callbacks():
        assert isinstance(callback(), list)


async def test_artist_table_checked_and_hovered(monkeypatch):
    monkeypatch.setattr(core, "Application", App)
    options = [
        {"meta": {"name": "Artist", "count": 12}},
    ]
    await core._tui_select("Artists", options, is_multi=True, item_category="artist")
    for callback in _callbacks():
        assert isinstance(callback(), list)


async def test_generic_string_cards(monkeypatch):
    monkeypatch.setattr(core, "Application", App)
    await core._tui_select("Filtros", ["A", "B"], is_multi=True, item_category="album")
    for callback in _callbacks():
        assert isinstance(callback(), list)
