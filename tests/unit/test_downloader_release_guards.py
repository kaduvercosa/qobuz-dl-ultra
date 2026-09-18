"""Branches iniciais de download_release(), sem rede nem filesystem real."""

import pytest

from qobuz_dl import downloader
from qobuz_dl.exceptions import NonStreamable

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


class Client:
    async def get_album_meta(self, item_id):
        return {"streamable": False, "title": "Album"}


async def test_download_release_nao_streamable(tmp_path, no_http_session):
    obj = downloader.Download(Client(), "album-1", str(tmp_path), 6)

    with pytest.raises(NonStreamable, match="está disponível"):
        await obj.download_release()


async def test_download_release_albums_only_ignora_tipo(
    monkeypatch, tmp_path, no_http_session
):
    class AlbumClient:
        async def get_album_meta(self, item_id):
            return {
                "streamable": True,
                "release_type": "single",
                "artist": {"name": "Artist"},
                "title": "Single",
                "tracks": {"items": []},
            }

    obj = downloader.Download(
        AlbumClient(),
        "album-1",
        str(tmp_path),
        6,
        albums_only=True,
    )
    monkeypatch.setattr(
        obj,
        "_get_format",
        lambda *args, **kwargs: ("flac", True, 16, 44100),
    )

    result = await obj.download_release()

    assert result is None
