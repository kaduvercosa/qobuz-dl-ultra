"""Testes unitários de QobuzDL.search_by_type()."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _placeholder(*args, **kwargs):
    return None


def _client(**overrides):
    methods = {
        "search_albums": _placeholder,
        "search_artists": _placeholder,
        "search_tracks": _placeholder,
        "search_playlists": _placeholder,
        "get_favorites": _placeholder,
    }
    methods.update(overrides)
    return SimpleNamespace(**methods)


def _metadata(self, item, item_type, mode_dict, fav_subtype=None):
    title = item.get("title") or item.get("name") or "Unknown"
    tracks_count = int(item.get("tracks_count") or 0)

    if item_type in {"album", "track"} or mode_dict.get("requires_extra"):
        if tracks_count <= 1:
            release_type = "single"
        elif tracks_count <= 6:
            release_type = "ep"
        else:
            release_type = "album"

        if "ep" in title.lower():
            release_type = "ep"

        return {
            "id": item.get("id"),
            "title": title,
            "type": release_type.title() if release_type != "ep" else "EP",
            "artist": "Unknown",
            "album": "Unknown Album",
            "quality": "16b/44.1kHz",
            "duration": "--:--",
            "tracks_count": tracks_count,
        }

    count = item.get("albums_count", item.get("tracks_count", 0))
    if item_type == "playlist" or fav_subtype == "playlists":
        return {
            "id": item.get("id"),
            "name": item.get("name", "Unknown"),
            "owner": item.get("owner", {}).get("name", "Unknown"),
            "count": count,
            "duration": "--:--",
        }

    return {
        "id": item.get("id"),
        "name": item.get("name", "Unknown"),
        "count": count,
    }


def _build_app(client):
    app = SimpleNamespace(client=client)
    app._extract_rich_metadata = _metadata.__get__(app)
    return app


async def test_query_muito_curta_devolve_lista_vazia():
    app = _build_app(_client())

    result = await core.QobuzDL.search_by_type(app, "a", "album")

    assert result == []


async def test_busca_basica_monta_meta_e_url():
    async def search_albums(query, limit):
        return {
            "albums": {
                "items": [
                    {"id": "1", "title": "X", "tracks_count": 10},
                ],
            },
        }

    app = _build_app(_client(search_albums=search_albums))

    result = await core.QobuzDL.search_by_type(app, "query valida", "album")

    assert len(result) == 1
    assert result[0]["url"] == "https://play.qobuz.com/album/1"
    assert result[0]["meta"]["title"] == "X"


async def test_lucky_devolve_apenas_urls_sem_meta():
    async def search_tracks(query, limit):
        return {"tracks": {"items": [{"id": "9"}]}}

    app = _build_app(_client(search_tracks=search_tracks))

    result = await core.QobuzDL.search_by_type(
        app,
        "query valida",
        "track",
        lucky=True,
    )

    assert result == ["https://play.qobuz.com/track/9"]


async def test_limit_trunca_resultados():
    async def search_albums(query, limit):
        return {
            "albums": {
                "items": [
                    {"id": str(i), "title": "X", "tracks_count": 1} for i in range(5)
                ],
            },
        }

    app = _build_app(_client(search_albums=search_albums))

    result = await core.QobuzDL.search_by_type(
        app,
        "query valida",
        "album",
        limit=2,
    )

    assert len(result) == 2


async def test_sub_filter_remove_tipos_nao_desejados():
    async def search_albums(query, limit):
        return {
            "albums": {
                "items": [
                    {"id": "1", "title": "EP Curto", "tracks_count": 4},
                    {"id": "2", "title": "Album Longo", "tracks_count": 12},
                ],
            },
        }

    app = _build_app(_client(search_albums=search_albums))

    result = await core.QobuzDL.search_by_type(
        app,
        "query valida",
        "album",
        sub_filter=["ep"],
    )

    assert len(result) == 1
    assert result[0]["url"] == "https://play.qobuz.com/album/1"


async def test_excecao_na_busca_e_capturada_devolve_lista_vazia():
    async def search_albums(query, limit):
        raise RuntimeError("timeout de rede")

    app = _build_app(_client(search_albums=search_albums))

    result = await core.QobuzDL.search_by_type(
        app,
        "query valida",
        "album",
    )

    assert result == []


async def test_favoritos_com_subtype_generico_usa_get_favorites():
    async def get_favorites(fav_type, limit):
        assert fav_type == "artists"
        return {"artists": {"items": [{"id": "1", "name": "Um Artista"}]}}

    app = _build_app(_client(get_favorites=get_favorites))

    result = await core.QobuzDL.search_by_type(
        app,
        "",
        "favorites",
        fav_subtype="artists",
    )

    assert len(result) == 1
    assert result[0]["url"] == "https://play.qobuz.com/artist/1"


async def test_favoritos_playlists_usa_endpoint_interno_getuserplaylists():
    class FakeResp:
        def __init__(self, data):
            self._data = data

        def json(self):
            return self._data

    async def request(method, url, params):
        return FakeResp(
            {
                "playlists": {
                    "items": [
                        {"id": "77", "name": "P", "owner": {}},
                    ],
                },
            },
        )

    client = _client()
    client.session = SimpleNamespace(request=request)
    client.base = "https://www.qobuz.com/api.json/0.2/"
    client.sec = "segredo"
    client._modern_sig = lambda endpoint, params, sec: "assinatura-fake"
    client.user_id = "u1"
    app = _build_app(client)

    result = await core.QobuzDL.search_by_type(
        app,
        "",
        "favorites",
        fav_subtype="playlists",
    )

    assert len(result) == 1
    assert result[0]["url"] == "https://play.qobuz.com/playlist/77"
