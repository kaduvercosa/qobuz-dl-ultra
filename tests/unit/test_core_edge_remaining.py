"""Testes direcionados para os últimos ramos pequenos de core.py."""

from types import SimpleNamespace

import httpx
import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def test_download_from_id_http_sem_response(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=0,
        settings=SimpleNamespace(pl_failed=0, pl_skipped=0),
    )

    async def handled(*args, **kwargs):
        return False

    class Download:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise httpx.HTTPStatusError(
                "error",
                request=SimpleNamespace(),
                response=None,
            )

    monkeypatch.setattr(core, "handle_download_id", handled)
    monkeypatch.setattr(core.downloader, "Download", Download)

    result = await core.QobuzDL.download_from_id(app, "x", is_playlist=True)
    assert result is False
    assert app.settings.pl_failed == 1


async def test_download_list_url_open_normalizada(monkeypatch):
    called = []

    async def handle(url):
        called.append(url)
        return True

    app = SimpleNamespace(
        settings=SimpleNamespace(max_workers=1),
        delay=0,
        handle_url=handle,
        mark_url_done_in_file=lambda *args: None,
    )

    monkeypatch.setattr(core, "get_url_info", lambda url: ("album", "a1"))

    await core.QobuzDL.download_list_of_urls(
        app,
        ["https://open.qobuz.com/album/a1"],
    )

    assert called == ["https://play.qobuz.com/album/a1"]


async def test_search_subfilter_limite_expandido(monkeypatch):
    async def search_albums(query, limit):
        assert limit == 6
        return {"albums": {"items": []}}

    async def placeholder(*args, **kwargs):
        return None

    client = SimpleNamespace(
        search_albums=search_albums,
        search_artists=placeholder,
        search_tracks=placeholder,
        search_playlists=placeholder,
        get_favorites=placeholder,
    )
    app = SimpleNamespace(client=client)

    result = await core.QobuzDL.search_by_type(
        app,
        "query",
        "album",
        limit=2,
        sub_filter=["album"],
    )

    assert result == []


async def test_download_from_playlist_file_falha_no_finalize(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        multiple_disc_one_dir=False,
        max_workers=1,
        pl_success=0,
        pl_skipped=0,
        pl_failed=0,
    )

    async def get_ids(items):
        return ["t1"]

    async def download(*args, **kwargs):
        settings.pl_failed = 1
        return False

    async def finalize(*args, **kwargs):
        raise RuntimeError("report error")

    app = SimpleNamespace(
        client=SimpleNamespace(get_track_ids_from_list=get_ids),
        directory=str(tmp_path),
        folder_format="original",
        playlist_as_albums=False,
        delay=0,
        settings=settings,
        download_from_id=download,
    )

    monkeypatch.setattr("qobuz_dl.playlist_import.parse_playlist_file", lambda p: ["x"])
    monkeypatch.setattr(core.downloader, "print_download_header", lambda *a, **k: None)
    monkeypatch.setattr(core.downloader, "safe_print", lambda *a, **k: None)
    monkeypatch.setattr(core.postprocess, "finalize_report", finalize)

    with pytest.raises(RuntimeError, match="report error"):
        await core.QobuzDL.download_from_playlist_file(app, file_path="list.txt")
