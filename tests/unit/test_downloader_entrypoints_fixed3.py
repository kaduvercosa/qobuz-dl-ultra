"""Testes de entrypoint sem abrir conexões reais do httpx."""

from types import SimpleNamespace

import pytest

from qobuz_dl import downloader
from qobuz_dl.exceptions import NonStreamable

pytestmark = pytest.mark.unit


class FakeClient:
    async def get_album_meta(self, item_id):
        return {"streamable": False, "title": "Unavailable"}


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


async def test_download_id_by_type_release_non_streamable(tmp_path, no_http_session):
    settings = SimpleNamespace(multiple_disc_track_format="{track_number}")
    obj = downloader.Download(
        FakeClient(),
        "album-1",
        str(tmp_path),
        6,
        settings=settings,
    )

    with pytest.raises(NonStreamable, match="está disponível"):
        await obj.download_id_by_type(track=False)

    assert obj.folder_format == downloader.DEFAULT_FOLDER
    assert obj.track_format == downloader.DEFAULT_TRACK


async def test_download_id_by_type_track_delega_para_download_track(
    monkeypatch, tmp_path, no_http_session
):
    settings = SimpleNamespace(multiple_disc_track_format="{track_number}")
    obj = downloader.Download(
        SimpleNamespace(),
        "track-1",
        str(tmp_path),
        6,
        settings=settings,
    )
    called = {}

    async def fake_download_track(**kwargs):
        called.update(kwargs)
        return True

    monkeypatch.setattr(obj, "download_track", fake_download_track)

    result = await obj.download_id_by_type(
        track=True,
        is_parallel=True,
        position_pool="pool",
        suppress_header=True,
    )

    assert result is True
    assert called == {
        "is_parallel": True,
        "position_pool": "pool",
        "suppress_header": True,
    }
