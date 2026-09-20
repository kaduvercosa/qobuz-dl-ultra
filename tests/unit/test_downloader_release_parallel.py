"""Ramo paralelo de download_release(), sem downloads reais."""

from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


@pytest.fixture
def no_http_session(monkeypatch):
    class FakeSession:
        async def aclose(self):
            return None

    monkeypatch.setattr(
        downloader.httpx,
        "AsyncClient",
        lambda *args, **kwargs: FakeSession(),
    )


def album_data(tracks):
    return {
        "streamable": True,
        "title": "Album",
        "url": "album-url",
        "release_date_original": "2024-01-01",
        "artist": {"name": "Artist"},
        "image": {"large": "https://example.test/cover.jpg"},
        "tracks": {"items": tracks},
    }


async def test_download_release_paralelo(monkeypatch, tmp_path, no_http_session):
    tracks = [
        {"id": "t1", "title": "One", "track_number": 1, "media_number": 1},
        {"id": "t2", "title": "Two", "track_number": 2, "media_number": 1},
    ]
    obj = downloader.Download(SimpleNamespace(), "album-1", str(tmp_path), 6)
    obj.settings.max_workers = 2
    obj.client.get_album_meta = lambda item_id: album_data(tracks)

    async def get_album_meta(item_id):
        return album_data(tracks)

    obj.client.get_album_meta = get_album_meta

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    calls = []

    async def fake_process_track(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(obj, "_process_track", fake_process_track)

    result = await obj.download_release()

    assert result is True
    assert len(calls) == 2
    assert all(call["is_parallel"] is True for call in calls)
    assert all(call["position_pool"] is not None for call in calls)
