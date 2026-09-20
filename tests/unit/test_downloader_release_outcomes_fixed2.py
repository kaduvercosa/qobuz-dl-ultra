"""Consolidação de resultados das faixas em download_release()."""

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


@pytest.mark.parametrize(
    "outcomes, expected",
    [
        ([True, True], True),
        ([True, "skipped"], False),
        ([True, False], False),
        (["skipped", "skipped"], False),
    ],
)
async def test_download_release_consolida_resultados(
    monkeypatch, tmp_path, no_http_session, outcomes, expected
):
    tracks = [
        {"id": "t1", "title": "One", "track_number": 1, "media_number": 1},
        {"id": "t2", "title": "Two", "track_number": 2, "media_number": 1},
    ]
    obj = downloader.Download(SimpleNamespace(), "album-1", str(tmp_path), 6)

    async def get_album_meta(item_id):
        return album_data(tracks)

    obj.client.get_album_meta = get_album_meta

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    results = iter(outcomes)

    async def fake_process_track(*args, **kwargs):
        return next(results)

    monkeypatch.setattr(obj, "_process_track", fake_process_track)

    result = await obj.download_release()

    assert result is expected
