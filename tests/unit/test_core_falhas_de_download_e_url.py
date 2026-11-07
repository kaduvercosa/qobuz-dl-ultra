"""Testes de exceções e ramos de falha adicionais de core.py."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _empty_async_generator(*args, **kwargs):
    if False:
        yield None


async def test_handle_url_url_invalida(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: (_ for _ in ()).throw(KeyError("tipo")),
    )
    client = SimpleNamespace(
        get_plist_meta=_empty_async_generator,
        get_artist_meta=_empty_async_generator,
        get_label_meta=_empty_async_generator,
    )
    result = await core.QobuzDL.handle_url(
        SimpleNamespace(client=client),
        "bad",
    )
    assert result is False


async def test_handle_url_container_com_chunk_sem_itens(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "get_url_info", lambda url: ("label", "l1"))
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_label_meta(item_id):
        yield {"name": "Label", "albums": {}}

    client = SimpleNamespace(
        get_plist_meta=get_label_meta,
        get_artist_meta=get_label_meta,
        get_label_meta=get_label_meta,
    )
    app = SimpleNamespace(
        client=client,
        directory=str(tmp_path),
        smart_discography=False,
        blacklist_patterns=None,
        playlist_as_albums=False,
        delay=0,
        folder_format="x",
        settings=SimpleNamespace(max_workers=1),
    )

    result = await core.QobuzDL.handle_url(app, "label")
    assert result is False


async def test_download_from_id_excecao_generica(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=0,
        settings=SimpleNamespace(pl_failed=0, pl_skipped=0),
    )

    async def handle_download_id(*args, **kwargs):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise RuntimeError("erro inesperado")

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)

    result = await core.QobuzDL.download_from_id(app, "x", is_playlist=True)
    assert result is False
    assert app.settings.pl_failed == 1


async def test_download_from_id_delay_em_erro(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=2,
        settings=SimpleNamespace(pl_failed=0, pl_skipped=0),
    )
    slept = []

    async def fake_sleep(value):
        slept.append(value)

    async def handle_download_id(*args, **kwargs):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            return False

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)
    monkeypatch.setattr(core.asyncio, "sleep", fake_sleep)

    result = await core.QobuzDL.download_from_id(app, "x")
    assert result is False
    assert slept == [2]


async def test_download_from_playlist_file_matching_vazio(monkeypatch, tmp_path):
    async def get_track_ids_from_list(items):
        return []

    app = SimpleNamespace(
        directory=str(tmp_path),
        playlist_as_albums=False,
        folder_format="original",
        delay=0,
        settings=SimpleNamespace(
            multiple_disc_one_dir=False,
            max_workers=1,
            pl_success=0,
            pl_skipped=0,
            pl_failed=0,
        ),
        client=SimpleNamespace(get_track_ids_from_list=get_track_ids_from_list),
    )
    monkeypatch.setattr(
        "qobuz_dl.playlist_import.parse_playlist_file",
        lambda path: ["track"],
    )

    result = await core.QobuzDL.download_from_playlist_file(
        app,
        file_path="playlist.txt",
    )
    assert result is None


async def test_import_playlist_url_sucesso(monkeypatch):
    import sys
    import types

    module = types.ModuleType("qobuz_dl.platform_fetcher")

    async def fetch_playlist_from_url(source):
        return {
            "platform": "spotify",
            "name": "Minha Playlist",
            "tracks": ["track"],
        }

    module.fetch_playlist_from_url = fetch_playlist_from_url
    sys.modules["qobuz_dl.platform_fetcher"] = module

    monkeypatch.setattr(core.ui, "emit", lambda *args, **kwargs: None)
    monkeypatch.setattr("builtins.input", lambda *_: "2")

    async def get_track_ids_from_list(items):
        return ["t1"]

    async def create_qobuz_playlist(**kwargs):
        return "p1"

    async def add_tracks_to_qobuz_playlist(*args, **kwargs):
        return True

    app = SimpleNamespace(
        client=SimpleNamespace(
            get_track_ids_from_list=get_track_ids_from_list,
            create_qobuz_playlist=create_qobuz_playlist,
            add_tracks_to_qobuz_playlist=add_tracks_to_qobuz_playlist,
        ),
    )

    await core.QobuzDL.import_playlist_from_url_or_file(
        app,
        "https://spotify.test/list",
    )
