"""Testes de rotas HTTP da webapp (FastAPI).

O middleware local_origin_guard rejeita requisições cujo cabeçalho Host
não seja loopback (localhost/127.0.0.1/::1).  O AsyncClient do httpx não
envia Host automaticamente, por isso todas as fixtures definem
headers={"host": "localhost"} — sem isso todas as rotas devolvem 403.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from qobuz_dl.webapp import create_app

# ---------------------------------------------------------------------------
# Fixture compartilhada
# ---------------------------------------------------------------------------

LOCAL_HEADERS = {"host": "localhost"}


@pytest_asyncio.fixture
async def client():
    """Cliente ASGI com cabeçalho Host correto para passar pelo guard."""
    app = create_app(demo=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://localhost",
        headers=LOCAL_HEADERS,
    ) as ac:
        yield ac


# ---------------------------------------------------------------------------
# /api/status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status_sem_config(client):
    r = await client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "configured" in data
    assert "demo" in data


@pytest.mark.asyncio
async def test_get_status_com_config(client):
    r = await client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    # Em modo demo, configured é sempre False
    assert data["demo"] is True
    assert data["configured"] is False


# ---------------------------------------------------------------------------
# /api/settings  GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_settings(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "quality" in data
    assert "directory" in data


# ---------------------------------------------------------------------------
# /api/settings  POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_settings_valido(tmp_path, monkeypatch):
    """POST /api/settings com payload válido deve retornar 200."""
    import qobuz_dl.webapp as webapp

    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    config_file = config_dir / "config.ini"

    monkeypatch.setattr(
        webapp,
        "get_config_paths",
        lambda: {
            "config_path": str(config_dir),
            "config_file": str(config_file),
            "qobuz_db": str(config_dir / "downloads.db"),
        },
    )
    music_dir = tmp_path / "Music"
    music_dir.mkdir()

    app = create_app(demo=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://localhost",
        headers=LOCAL_HEADERS,
    ) as ac:
        payload = {
            "directory": str(music_dir),
            "quality": 6,
            "embed_art": True,
            "fetch_lyrics": True,
            "lrc_files": True,
            "credits": True,
            "m3u": False,
            "quality_fallback": True,
            "playlist_as_albums": False,
            "verify_after_download": False,
            "max_workers": 2,
            "segment_workers": 4,
            "embedded_art_size": "org",
            "saved_art_size": "org",
        }
        r = await ac.post("/api/settings", json=payload)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_post_settings_quality_invalido(client):
    """quality fora do intervalo [5,27] deve retornar 422 (validação Pydantic)."""
    payload = {
        "directory": "/tmp",
        "quality": 99,  # inválido
        "embed_art": True,
        "fetch_lyrics": True,
        "lrc_files": True,
        "credits": True,
        "m3u": False,
        "quality_fallback": True,
        "playlist_as_albums": False,
        "verify_after_download": False,
        "max_workers": 1,
        "segment_workers": 4,
        "embedded_art_size": "org",
        "saved_art_size": "org",
    }
    r = await client.post("/api/settings", json=payload)
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_root_retorna_html(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "html" in r.headers.get("content-type", "").lower()


# ---------------------------------------------------------------------------
# /api/connect  POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_connect_demo_nao_conecta(client):
    """Em modo demo, /api/connect responde 200 mas connected=False."""
    r = await client.post("/api/connect")
    assert r.status_code == 200
    data = r.json()
    assert data.get("demo") is True
    assert data.get("connected") is False


# ---------------------------------------------------------------------------
# /api/search  GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_search_demo(client):
    r = await client.get("/api/search", params={"q": "blue", "kind": "all"})
    assert r.status_code == 200
    data = r.json()
    assert "tracks" in data
    assert "albums" in data


@pytest.mark.asyncio
async def test_get_search_query_curta(client):
    """Query com menos de 2 caracteres retorna listas vazias."""
    r = await client.get("/api/search", params={"q": "a"})
    assert r.status_code == 200
    data = r.json()
    assert data["tracks"] == []
    assert data["albums"] == []


# ---------------------------------------------------------------------------
# /api/tools  GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tools(client):
    r = await client.get("/api/tools")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert len(data["items"]) > 0


# ---------------------------------------------------------------------------
# /api/queue  GET
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_queue(client):
    r = await client.get("/api/queue")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert "busy" in data


# ---------------------------------------------------------------------------
# /api/favorites  GET  (demo)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_favorites_albums_demo(client):
    r = await client.get("/api/favorites", params={"kind": "albums"})
    assert r.status_code == 200
    assert "items" in r.json()


@pytest.mark.asyncio
async def test_get_favorites_kind_invalido(client):
    r = await client.get("/api/favorites", params={"kind": "playlists"})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# /api/download  POST  (demo — deve retornar 409)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_download_demo_bloqueado(client):
    payload = {"id": "12345678", "kind": "album", "title": "Test", "artist": "Test"}
    r = await client.post("/api/download", json=payload)
    assert r.status_code == 409


# ---------------------------------------------------------------------------
# Guard CSRF — origem cruzada bloqueada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cross_origin_bloqueado():
    """Requisição com Origin de domínio externo deve ser bloqueada (403)."""
    app = create_app(demo=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://localhost"
    ) as ac:
        r = await ac.get(
            "/api/status",
            headers={
                "host": "localhost",
                "origin": "https://evil.example.com",
            },
        )
    assert r.status_code == 403
