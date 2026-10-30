"""Testes unitários adicionais para a API qopy.py."""

from unittest.mock import AsyncMock, patch

import pytest

from qobuz_dl.qopy import Client


@pytest.mark.asyncio
async def test_get_all_favorites_pagina_em_modo_estrito(monkeypatch):
    """The shared gateway returns every page and never swallows failures."""
    client = Client()
    client.user_id = "42"
    calls = []

    async def get_favorites(fav_type, limit, offset, *, strict):
        """Return deterministic pages while recording strict-mode usage."""
        calls.append((fav_type, limit, offset, strict))
        albums = [{"id": str(i)} for i in range(5)]
        return {"albums": {"items": albums[offset : offset + limit], "total": 5}}

    monkeypatch.setattr(client, "get_favorites", get_favorites)

    items, total = await client.get_all_favorites("albums", page_size=2)

    assert [item["id"] for item in items] == ["0", "1", "2", "3", "4"]
    assert total == 5
    assert calls == [
        ("albums", 2, 0, True),
        ("albums", 2, 2, True),
        ("albums", 2, 4, True),
    ]


@pytest.mark.asyncio
async def test_get_all_favorites_remove_ids_duplicados_em_paginas_sobrepostas(
    monkeypatch,
):
    client = Client()
    client.user_id = "42"

    async def get_favorites(fav_type, limit, offset, *, strict):
        pages = {
            0: [{"id": "0"}, {"id": "1"}],
            2: [{"id": "1"}, {"id": "2"}],
            4: [{"id": "3"}],
        }
        return {"albums": {"items": pages.get(offset, []), "total": None}}

    monkeypatch.setattr(client, "get_favorites", get_favorites)

    items, total = await client.get_all_favorites("albums", page_size=2)

    assert [item["id"] for item in items] == ["0", "1", "2", "3"]
    assert total is None


@pytest.mark.asyncio
async def test_get_all_favorites_rejeita_paginacao_incompleta(monkeypatch):
    client = Client()
    client.user_id = "42"

    async def get_favorites(fav_type, limit, offset, *, strict):
        return {"albums": {"items": [{"id": "0"}], "total": 3}}

    monkeypatch.setattr(client, "get_favorites", get_favorites)

    with pytest.raises(RuntimeError, match="incompleta"):
        await client.get_all_favorites("albums", page_size=1, max_pages=4)


@pytest.mark.asyncio
async def test_get_user_playlists_fallback_busca_ids_individuais(monkeypatch):
    """Use the ID fallback without exposing HTTP internals to callers."""
    client = Client()
    client.user_id = "42"

    async def api_call(endpoint, **kwargs):
        """Simulate the owner playlist discovery endpoints."""
        if endpoint == "playlist/getUserPlaylists":
            assert kwargs["type"] == "owner"
            return {}
        if endpoint == "playlist/getUserPlaylistIds":
            return {"playlist_ids": ["7", "8"]}
        return {"id": kwargs["id"], "name": f"P{kwargs['id']}", "owner": {"id": 42}}

    monkeypatch.setattr(client, "api_call", api_call)

    result = await client.get_user_playlists(limit=10, playlist_type="owner")

    assert [item["id"] for item in result["playlists"]["items"]] == ["7", "8"]


@pytest.mark.asyncio
async def test_qopy_get_track_url_retries_and_parses():
    client = Client()
    client.user_id = "42"
    client.id = "user123"
    client.base = "https://api.qobuz.com/api.json/0.2/"
    client.uat = "valid_token"
    client.app_secret = "secret"
    client.sec = "sec"

    fake_response = {
        "url": "https://stream.qobuz.com/track.flac",
        "bit_depth": 24,
        "sampling_rate": 96.0,
    }

    async def mock_api_call(endpoint, **kwargs):
        if endpoint == "track/getFileUrl":
            return fake_response
        return {}

    with patch.object(client, "check_subscription", return_value={"is_active": True}):
        with patch.object(client, "api_call", new=AsyncMock(side_effect=mock_api_call)):
            res = await client.get_track_url("102030", fmt_id=7)
            assert res["url"] == "https://stream.qobuz.com/track.flac"
            assert res["bit_depth"] == 24


@pytest.mark.asyncio
async def test_get_artist_tracks_usa_extra_tracks_e_nao_albums(monkeypatch):
    client = Client()
    client.user_id = "42"
    calls = []

    async def api_call(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return {"tracks": {"items": [{"id": "t1"}, {"id": "t2"}]}}

    monkeypatch.setattr(client, "api_call", api_call)

    result = await client.get_artist_tracks("artist-1", limit=20)

    assert [track["id"] for track in result] == ["t1", "t2"]
    assert calls == [
        (
            "artist/get",
            {"id": "artist-1", "extra": "tracks", "limit": 20, "offset": 0},
        )
    ]


@pytest.mark.asyncio
async def test_get_user_playlists_separa_owner_de_subscriber(monkeypatch):
    client = Client()
    client.user_id = "42"
    calls = []

    async def api_call(endpoint, **kwargs):
        calls.append((endpoint, kwargs))
        return {
            "playlists": {
                "items": [
                    {
                        "id": kwargs["type"],
                        "owner": {"id": 42 if kwargs["type"] == "owner" else 99},
                    }
                ],
                "total": 1,
            }
        }

    monkeypatch.setattr(client, "api_call", api_call)

    owned = await client.get_user_playlists(playlist_type="owner")
    followed = await client.get_user_playlists(playlist_type="subscriber")

    assert owned["playlists"]["items"][0]["id"] == "owner"
    assert followed["playlists"]["items"][0]["id"] == "subscriber"
    assert [kwargs["type"] for _, kwargs in calls] == ["owner", "subscriber"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relationship,expected", [("owner", ["mine"]), ("subscriber", ["other"])]
)
async def test_playlists_filtra_por_id_nao_por_nome(
    monkeypatch, relationship, expected
):
    client = Client()
    client.user_id = "42"

    async def api_call(endpoint, **kwargs):
        return {
            "playlists": {
                "items": [
                    {"id": "mine", "owner": {"id": 42, "name": "edu"}},
                    {"id": "other", "owner": {"id": 99, "name": "edu"}},
                    {"id": "unknown", "owner": {}},
                ],
                "total": 3,
            }
        }

    monkeypatch.setattr(client, "api_call", api_call)
    result = await client.get_user_playlists(playlist_type=relationship)
    assert [item["id"] for item in result["playlists"]["items"]] == expected


@pytest.mark.asyncio
async def test_playlists_pagina_mesmo_com_pagina_filtrada_vazia(monkeypatch):
    client = Client()
    client.user = {"id": 42}
    offsets = []

    async def api_call(endpoint, **kwargs):
        offsets.append(kwargs["offset"])
        offset = kwargs["offset"]
        return {
            "playlists": {
                "items": [
                    {"id": str(offset), "owner": {"id": 99 if offset == 0 else 42}},
                ],
                "total": 2,
            }
        }

    monkeypatch.setattr(client, "api_call", api_call)
    items, total = await client.get_all_user_playlists(page_size=1)
    assert [item["id"] for item in items] == ["1"]
    assert total == 1
    assert offsets == [0, 1]


@pytest.mark.asyncio
async def test_playlists_subscriber_nao_usa_fallback(monkeypatch):
    client = Client()
    client.user_id = 42
    calls = []

    async def api_call(endpoint, **kwargs):
        calls.append(endpoint)
        return {}

    monkeypatch.setattr(client, "api_call", api_call)
    with pytest.raises(RuntimeError, match="favoritadas"):
        await client.get_user_playlists(playlist_type="subscriber")
    assert calls == ["playlist/getUserPlaylists"]


@pytest.mark.asyncio
async def test_playlists_fallback_filtra_e_pagina_uma_vez(monkeypatch):
    client = Client()
    client.user_id = 42

    async def api_call(endpoint, **kwargs):
        if endpoint == "playlist/getUserPlaylists":
            return {}
        if endpoint == "playlist/getUserPlaylistIds":
            return {"playlist_ids": ["other", "deleted", "mine"]}
        if kwargs["id"] == "deleted":
            raise RuntimeError("not found")
        return {
            "id": kwargs["id"],
            "owner": {"id": 42 if kwargs["id"] == "mine" else 99},
        }

    monkeypatch.setattr(client, "api_call", api_call)
    items, total = await client.get_all_user_playlists(page_size=1)
    assert [item["id"] for item in items] == ["mine"]
    assert total == 1


@pytest.mark.asyncio
async def test_playlists_sem_identidade_falha_explicitamente():
    client = Client()
    client.user_id = None
    client.user = {}
    with pytest.raises(RuntimeError, match="Identidade"):
        await client.get_user_playlists()


@pytest.mark.parametrize(
    "endpoint",
    [
        "playlist/get",
        "playlist/getUserPlaylists",
        "playlist/getUserPlaylistIds",
    ],
)
def test_playlists_mutaveis_nao_usam_cache(endpoint):
    assert endpoint not in Client._CACHEABLE_ENDPOINTS
