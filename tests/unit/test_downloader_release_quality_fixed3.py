"""Branches de qualidade em download_release()."""

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


class QualityClient:
    def __init__(self, album):
        self.album = album

    async def get_album_meta(self, item_id):
        return self.album


def album_data():
    return {
        "streamable": True,
        "title": "Album",
        "url": "album-url",
        "release_date_original": "2024-01-01",
        "artist": {"name": "Artist"},
        "image": {"large": "https://example.test/cover.jpg"},
        "tracks": {"items": []},
    }


async def test_download_release_pula_qualidade_insuficiente(
    monkeypatch, tmp_path, no_http_session
):
    obj = downloader.Download(
        QualityClient(album_data()),
        "album-1",
        str(tmp_path),
        27,
        downgrade_quality=False,
    )

    async def get_format(*args, **kwargs):
        return ("flac", False, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    result = await obj.download_release()
    assert result is None


async def test_download_release_continua_com_downgrade(
    monkeypatch, tmp_path, no_http_session
):
    obj = downloader.Download(
        QualityClient(album_data()),
        "album-1",
        str(tmp_path),
        27,
        downgrade_quality=True,
    )

    async def get_format(*args, **kwargs):
        return ("flac", False, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    result = await obj.download_release()
    assert result is False
