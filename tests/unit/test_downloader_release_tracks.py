"""Processamento de faixas em download_release(), sem download real."""

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


async def test_download_release_processa_faixa_sequencial(
    monkeypatch, tmp_path, no_http_session
):
    track = {
        "id": "track-1",
        "title": "Track",
        "track_number": 1,
        "media_number": 1,
        "streamable": True,
    }
    obj = downloader.Download(
        SimpleNamespace(get_album_meta=lambda item_id: album_data([track])),
        "album-1",
        str(tmp_path),
        6,
    )

    async def get_album_meta(item_id):
        return album_data([track])

    obj.client.get_album_meta = get_album_meta

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    calls = []

    async def fake_process_track(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(obj, "_process_track", fake_process_track)

    result = await obj.download_release()

    assert result is True
    assert len(calls) == 1
    assert calls[0][0][1]["id"] == "track-1"


async def test_download_release_sem_faixas_retorna_false(
    monkeypatch, tmp_path, no_http_session
):
    obj = downloader.Download(
        SimpleNamespace(), "album-1", str(tmp_path), 6
    )

    async def get_album_meta(item_id):
        return album_data([])

    obj.client.get_album_meta = get_album_meta

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)

    result = await obj.download_release()

    assert result is False
