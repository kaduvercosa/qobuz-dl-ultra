"""Testes unitários adicionais para a API qopy.py."""

import pytest
from unittest.mock import AsyncMock, patch

from qobuz_dl.qopy import Client


@pytest.mark.asyncio
async def test_get_all_favorites_pagina_em_modo_estrito(monkeypatch):
    """The shared gateway returns every page and never swallows failures."""
    client = Client()
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

    async def get_favorites(fav_type, limit, offset, *, strict):
        return {"albums": {"items": [{"id": "0"}], "total": 3}}

    monkeypatch.setattr(client, "get_favorites", get_favorites)

    with pytest.raises(RuntimeError, match="incompleta"):
        await client.get_all_favorites("albums", page_size=1, max_pages=4)


@pytest.mark.asyncio
async def test_get_user_playlists_fallback_busca_ids_individuais(monkeypatch):
    """Use the ID fallback without exposing HTTP internals to callers."""
    client = Client()

    async def api_call(endpoint, **kwargs):
        """Simulate the two provider playlist discovery endpoints."""
        if endpoint == "playlist/getUserPlaylists":
            return {}
        if endpoint == "playlist/getUserPlaylistIds":
            return {"playlist_ids": ["7", "8"]}
        return {"id": kwargs["id"], "name": f"P{kwargs['id']}"}

    monkeypatch.setattr(client, "api_call", api_call)

    result = await client.get_user_playlists(limit=10)

    assert [item["id"] for item in result["playlists"]["items"]] == ["7", "8"]


@pytest.mark.asyncio
async def test_qopy_get_track_url_retries_and_parses():
    client = Client()
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
