"""Testes adicionais dos ramos de despacho e lote em core.py."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _empty_async_generator(*args, **kwargs):
    if False:
        yield None


async def _placeholder(*args, **kwargs):
    return None


def _client(**overrides):
    client = SimpleNamespace(
        get_plist_meta=_empty_async_generator,
        get_artist_meta=_empty_async_generator,
        get_label_meta=_empty_async_generator,
        get_album_meta=_placeholder,
        get_album=_placeholder,
    )
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


def _download_spy(called, result=True):
    async def download_from_id(*args, **kwargs):
        called.append((args, kwargs))
        return result

    return download_from_id


async def test_handle_url_label_com_conteudo(monkeypatch):
    monkeypatch.setattr(core, "get_url_info", lambda url: ("label", "l1"))
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_label_meta(item_id):
        yield {"name": "Label", "albums": {"items": [{"id": "a1"}]}}

    called = []
    app = SimpleNamespace(
        client=_client(get_label_meta=get_label_meta),
        directory="/tmp",
        smart_discography=False,
        blacklist_patterns=None,
        playlist_as_albums=False,
        delay=0,
        folder_format="x",
        settings=SimpleNamespace(max_workers=1),
        download_from_id=_download_spy(called),
    )

    result = await core.QobuzDL.handle_url(app, "label")

    assert result is True
    assert called[0][0][0] == "a1"
    assert called[0][0][1] is True


async def test_handle_url_artista_com_smart_discography(monkeypatch):
    monkeypatch.setattr(core, "get_url_info", lambda url: ("artist", "a1"))
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_artist_meta(item_id):
        yield {"name": "Artista", "albums": {"items": [{"id": "x"}]}}

    monkeypatch.setattr(
        core,
        "smart_discography_filter",
        lambda content, **kwargs: [{"id": "filtered"}],
    )

    called = []
    app = SimpleNamespace(
        client=_client(get_artist_meta=get_artist_meta),
        directory="/tmp",
        smart_discography=True,
        blacklist_patterns=None,
        playlist_as_albums=False,
        delay=0,
        folder_format="x",
        settings=SimpleNamespace(max_workers=1),
        download_from_id=_download_spy(called),
    )

    result = await core.QobuzDL.handle_url(app, "artist")

    assert result is True
    assert called[0][0][0] == "filtered"
    assert called[0][0][1] is True


async def test_handle_url_artista_metadado_com_api_release_type(monkeypatch):
    monkeypatch.setattr(core, "get_url_info", lambda url: ("artist", "a1"))
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)
    monkeypatch.setattr(core, "_classify_release_type", lambda **kwargs: "album")

    async def get_artist_meta(item_id):
        yield {"name": "Artista", "albums": {"items": [{"id": "x", "title": "X"}]}}

    async def get_album_meta(item_id):
        return {"release_type": "album"}

    async def fake_tui(*args, **kwargs):
        return [("💿 Album", 0)]

    monkeypatch.setattr(core, "_tui_select", fake_tui)

    called = []
    app = SimpleNamespace(
        client=_client(
            get_artist_meta=get_artist_meta,
            get_album_meta=get_album_meta,
        ),
        directory="/tmp",
        smart_discography=False,
        blacklist_patterns=None,
        playlist_as_albums=False,
        delay=0,
        folder_format="x",
        settings=SimpleNamespace(max_workers=1),
        download_from_id=_download_spy(called),
        _is_interactive_session=True,
    )

    result = await core.QobuzDL.handle_url(app, "artist")

    assert result is True
    assert called[0][0][0] == "x"
    assert called[0][0][1] is True


async def test_download_from_playlist_file_sem_ids_retorna():
    result = await core.QobuzDL.download_from_playlist_file(SimpleNamespace())
    assert result is None


async def test_download_from_playlist_file_arquivo_vazio(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "qobuz_dl.playlist_import.parse_playlist_file",
        lambda path: [],
    )

    path = tmp_path / "empty.txt"
    path.write_text("", encoding="utf-8")

    result = await core.QobuzDL.download_from_playlist_file(
        SimpleNamespace(),
        file_path=str(path),
    )

    assert result is None


async def test_download_from_playlist_file_playlist_as_albums(monkeypatch):
    settings = SimpleNamespace(
        multiple_disc_one_dir=False,
        max_workers=1,
        pl_success=0,
        pl_skipped=0,
        pl_failed=0,
    )
    calls = []

    async def get_track_ids_from_list(items):
        return ["t1"]

    async def download_from_id(track_id, **kwargs):
        calls.append(kwargs)
        return True

    app = SimpleNamespace(
        client=SimpleNamespace(get_track_ids_from_list=get_track_ids_from_list),
        directory="/tmp",
        folder_format="original",
        settings=settings,
        playlist_as_albums=True,
        delay=0,
        download_from_id=download_from_id,
    )

    monkeypatch.setattr(
        "qobuz_dl.playlist_import.parse_playlist_file",
        lambda path: ["track"],
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

    result = await core.QobuzDL.download_from_playlist_file(
        app,
        file_path="playlist.txt",
    )

    assert result is True
    assert calls[0]["is_playlist"] is True
    assert app.folder_format == "original"
