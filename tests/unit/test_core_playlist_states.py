"""Testes de estados de sucesso, falha e pulos em playlists."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def test_playlist_importada_com_falha_e_pulo(monkeypatch, tmp_path):
    settings = SimpleNamespace(
        multiple_disc_one_dir=False,
        max_workers=1,
        pl_success=0,
        pl_skipped=0,
        pl_failed=0,
    )
    outcomes = iter([True, False])

    async def get_ids(items):
        return ["ok", "fail"]

    async def download(track_id, **kwargs):
        result = next(outcomes)
        if result:
            settings.pl_success += 1
        else:
            settings.pl_failed += 1
        return result

    async def finalize(*args, **kwargs):
        return None

    app = SimpleNamespace(
        client=SimpleNamespace(get_track_ids_from_list=get_ids),
        directory=str(tmp_path),
        folder_format="original",
        playlist_as_albums=False,
        delay=0,
        settings=settings,
        download_from_id=download,
    )

    monkeypatch.setattr("qobuz_dl.playlist_import.parse_playlist_file", lambda path: ["a", "b"])
    monkeypatch.setattr(core.downloader, "print_download_header", lambda *a, **k: None)
    monkeypatch.setattr(core.downloader, "safe_print", lambda *a, **k: None)
    monkeypatch.setattr(core.postprocess, "finalize_report", finalize)

    result = await core.QobuzDL.download_from_playlist_file(app, file_path="list.txt")

    assert result is False
    assert settings.pl_success == 1
    assert settings.pl_failed == 1


async def test_handle_url_playlist_m3u_e_resumo(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "get_url_info", lambda url: ("playlist", "p1"))
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)
    m3u_calls = []

    async def get_playlist(item_id):
        yield {
            "name": "Playlist",
            "owner": {"name": "Owner"},
            "duration": 10,
            "tracks": {"items": [{"id": "t1"}]},
        }

    async def placeholder(*args, **kwargs):
        return None

    async def download(track_id, album, *args, **kwargs):
        return True

    monkeypatch.setattr(core, "make_m3u", lambda path: m3u_calls.append(path))
    monkeypatch.setattr(core.downloader, "print_download_header", lambda *a, **k: None)
    monkeypatch.setattr(core.downloader, "safe_print", lambda *a, **k: None)

    client = SimpleNamespace(
        get_plist_meta=get_playlist,
        get_artist_meta=get_playlist,
        get_label_meta=get_playlist,
        get_album_meta=placeholder,
    )
    app = SimpleNamespace(
        client=client,
        directory=str(tmp_path),
        smart_discography=False,
        no_m3u_for_playlists=False,
        blacklist_patterns=None,
        playlist_as_albums=False,
        delay=0,
        folder_format="original",
        settings=SimpleNamespace(
            max_workers=1,
            pl_success=0,
            pl_skipped=0,
            pl_failed=0,
            multiple_disc_one_dir=False,
        ),
        download_from_id=download,
    )

    result = await core.QobuzDL.handle_url(app, "playlist")

    assert result is True
    assert m3u_calls
