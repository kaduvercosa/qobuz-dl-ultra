"""Testes adicionais para os lotes de URLs e playlists."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _done():
    return None


async def test_download_list_urls_invalidas_e_album(monkeypatch):
    calls = []
    app = SimpleNamespace(
        settings=SimpleNamespace(max_workers=1),
        delay=0,
        mark_url_done_in_file=lambda *args: None,
    )

    def fake_get_url_info(url):
        if "bad" in url:
            raise KeyError("inválida")
        return ("album", "a1")

    monkeypatch.setattr(core, "get_url_info", fake_get_url_info)

    async def handle_url(url):
        calls.append(url)
        return True

    app.handle_url = handle_url

    await core.QobuzDL.download_list_of_urls(
        app,
        ["bad", "https://play.qobuz.com/album/1"],
    )

    assert calls == ["bad", "https://play.qobuz.com/album/1"]


async def test_download_list_tracks_paralelo(monkeypatch):
    calls = []
    app = SimpleNamespace(
        settings=SimpleNamespace(
            max_workers=2,
            pl_success=0,
            pl_skipped=0,
            pl_failed=0,
        ),
        delay=0,
        mark_url_done_in_file=lambda *args: None,
    )

    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("track", url.rsplit("/", 1)[-1]),
    )
    monkeypatch.setattr(core.asyncio, "sleep", lambda *_: _done())
    monkeypatch.setattr(
        core.downloader,
        "print_download_header",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        core.downloader,
        "safe_print",
        lambda *args, **kwargs: None,
    )

    async def download_from_id(item_id, *args, **kwargs):
        calls.append(item_id)
        return True

    app.download_from_id = download_from_id

    await core.QobuzDL.download_list_of_urls(
        app,
        [
            "https://play.qobuz.com/track/1",
            "https://play.qobuz.com/track/2",
        ],
    )

    assert sorted(calls) == ["1", "2"]


async def test_download_playlist_paralelo(monkeypatch):
    settings = SimpleNamespace(
        multiple_disc_one_dir=False,
        max_workers=2,
        pl_success=0,
        pl_skipped=0,
        pl_failed=0,
    )
    calls = []

    async def get_track_ids_from_list(items):
        return ["1", "2"]

    async def download_from_id(track_id, **kwargs):
        calls.append(track_id)
        return True

    app = SimpleNamespace(
        client=SimpleNamespace(get_track_ids_from_list=get_track_ids_from_list),
        directory="/tmp",
        folder_format="original",
        playlist_as_albums=False,
        delay=0,
        settings=settings,
        download_from_id=download_from_id,
    )

    monkeypatch.setattr(
        "qobuz_dl.playlist_import.parse_playlist_file",
        lambda path: ["a", "b"],
    )
    monkeypatch.setattr(
        core.downloader,
        "print_download_header",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        core.downloader,
        "safe_print",
        lambda *args, **kwargs: None,
    )

    async def finalize_report(*args, **kwargs):
        return None

    monkeypatch.setattr(core.postprocess, "finalize_report", finalize_report)
    monkeypatch.setattr(core.asyncio, "sleep", lambda *_: _done())

    result = await core.QobuzDL.download_from_playlist_file(
        app,
        file_path="list.txt",
    )

    assert result is True
    assert sorted(calls) == ["1", "2"]
