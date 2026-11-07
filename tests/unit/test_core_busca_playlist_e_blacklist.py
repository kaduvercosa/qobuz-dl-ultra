"""Testes direcionados para ramos restantes de core.py."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _empty_async(*args, **kwargs):
    return None


async def test_search_favoritos_playlists_erro_no_endpoint(monkeypatch):
    async def request(*args, **kwargs):
        raise RuntimeError("endpoint indisponível")

    async def placeholder(*args, **kwargs):
        return None

    client = SimpleNamespace(
        search_albums=placeholder,
        search_artists=placeholder,
        search_tracks=placeholder,
        search_playlists=placeholder,
        get_favorites=placeholder,
        session=SimpleNamespace(request=request),
        base="https://api/",
        sec="secret",
        user_id="u1",
        _modern_sig=lambda *args: "sig",
    )
    app = SimpleNamespace(client=client)

    result = await core.QobuzDL.search_by_type(
        app,
        "",
        "favorites",
        fav_subtype="playlists",
    )

    assert result == []


async def test_search_ignora_item_nao_dict():
    async def search_albums(query, limit):
        return {"albums": {"items": [None, "texto"]}}

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

    result = await core.QobuzDL.search_by_type(app, "álbum", "album")

    assert result == []


async def test_download_list_track_paralelo_marca_somente_sucesso(
    monkeypatch, tmp_path
):
    marked = []
    calls = []
    settings = SimpleNamespace(
        max_workers=2,
        pl_success=0,
        pl_skipped=0,
        pl_failed=0,
    )

    def mark(path, url):
        marked.append(url)

    async def download_from_id(item_id, *args, **kwargs):
        calls.append(item_id)
        return item_id == "1"

    app = SimpleNamespace(
        settings=settings,
        delay=0,
        download_from_id=download_from_id,
        mark_url_done_in_file=mark,
    )

    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("track", url.rsplit("/", 1)[-1]),
    )
    monkeypatch.setattr(core.asyncio, "sleep", lambda *_: _none())
    monkeypatch.setattr(core.downloader, "print_download_header", lambda *a, **k: None)
    monkeypatch.setattr(core.downloader, "safe_print", lambda *a, **k: None)

    await core.QobuzDL.download_list_of_urls(
        app,
        [
            "https://play.qobuz.com/track/1",
            "https://play.qobuz.com/track/2",
        ],
        txt_file=str(tmp_path / "urls.txt"),
    )

    assert sorted(calls) == ["1", "2"]
    assert marked == ["https://play.qobuz.com/track/1"]


async def test_import_playlist_nao_cria_playlist_sem_id(monkeypatch):
    import sys
    import types

    module = types.ModuleType("qobuz_dl.platform_fetcher")

    async def fetch(source):
        return {"platform": "spotify", "name": "P", "tracks": ["t"]}

    module.fetch_playlist_from_url = fetch
    sys.modules["qobuz_dl.platform_fetcher"] = module

    monkeypatch.setattr(core.ui, "emit", lambda *a, **k: None)
    monkeypatch.setattr("builtins.input", lambda *_: "2")

    async def get_ids(items):
        return ["t1"]

    async def create_playlist(**kwargs):
        return None

    app = SimpleNamespace(
        client=SimpleNamespace(
            get_track_ids_from_list=get_ids,
            create_qobuz_playlist=create_playlist,
        ),
    )

    await core.QobuzDL.import_playlist_from_url_or_file(
        app,
        "https://spotify.test/p",
    )


async def test_handle_url_blacklist_regex_invalida(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "get_url_info", lambda url: ("label", "l1"))
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_label(item_id):
        yield {
            "name": "Label",
            "albums": {"items": [{"id": "a1", "title": "Álbum"}]},
        }

    calls = []

    async def download(item_id, *args, **kwargs):
        calls.append(item_id)
        return True

    client = SimpleNamespace(
        get_plist_meta=get_label,
        get_artist_meta=get_label,
        get_label_meta=get_label,
    )
    app = SimpleNamespace(
        client=client,
        directory=str(tmp_path),
        smart_discography=False,
        blacklist_patterns=["[regex inválida"],
        playlist_as_albums=False,
        delay=0,
        folder_format="x",
        settings=SimpleNamespace(max_workers=1),
        download_from_id=download,
    )

    result = await core.QobuzDL.handle_url(app, "label")

    assert result is True
    assert calls == ["a1"]


async def _none(*args, **kwargs):
    return None
