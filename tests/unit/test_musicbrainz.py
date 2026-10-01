"""Testes de cobertura total para qobuz_dl/musicbrainz.py."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import qobuz_dl.musicbrainz as mb


# ---------------------------------------------------------------------------
# Fixture: limpa estado global entre testes
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def limpa_estado_global():
    """Garante isolamento: cache e semaphore zerados antes de cada teste."""
    mb._MB_CACHE.clear()
    mb._MB_SEM = None
    yield
    mb._MB_CACHE.clear()
    mb._MB_SEM = None


# ---------------------------------------------------------------------------
# _get_sem
# ---------------------------------------------------------------------------

def test_get_sem_cria_semaphore_na_primeira_chamada():
    async def _run():
        sem = mb._get_sem()
        assert isinstance(sem, asyncio.Semaphore)
    asyncio.run(_run())


def test_get_sem_retorna_mesma_instancia():
    async def _run():
        sem1 = mb._get_sem()
        sem2 = mb._get_sem()
        assert sem1 is sem2
    asyncio.run(_run())


# ---------------------------------------------------------------------------
# lookup_by_isrc -- ISRC vazio
# ---------------------------------------------------------------------------

def test_isrc_vazio_retorna_nones():
    resultado = asyncio.run(mb.lookup_by_isrc(""))
    assert resultado == (None, None, None)


# ---------------------------------------------------------------------------
# lookup_by_isrc -- cache hit
# ---------------------------------------------------------------------------

def test_cache_hit_retorna_sem_requisicao():
    mb._MB_CACHE["ISRC123"] = ("track", "album", "artist")
    resultado = asyncio.run(mb.lookup_by_isrc("isrc123"))  # normalizado para upper
    assert resultado == ("track", "album", "artist")


# ---------------------------------------------------------------------------
# Helpers para mock de resposta HTTP
# ---------------------------------------------------------------------------

def _make_response(data: dict, status_code: int = 200) -> MagicMock:
    """Cria um mock de httpx.Response com .json() e raise_for_status()."""
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = data
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "erro", request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


def _make_client(response_data: dict, status_code: int = 200) -> AsyncMock:
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.return_value = _make_response(response_data, status_code)
    client.aclose = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# lookup_by_isrc -- resposta com recordings
# ---------------------------------------------------------------------------

@pytest.fixture()
def payload_completo():
    return {
        "recordings": [
            {
                "id": "track-mbid-001",
                "releases": [{"id": "album-mbid-001"}],
                "artist-credit": [{"artist": {"id": "artist-mbid-001"}}],
            }
        ]
    }


def test_lookup_retorna_ids_corretos(payload_completo):
    client = _make_client(payload_completo)
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        resultado = asyncio.run(mb.lookup_by_isrc("GBAYE0000001", session=client))
    assert resultado == ("track-mbid-001", "album-mbid-001", "artist-mbid-001")


def test_lookup_armazena_no_cache(payload_completo):
    client = _make_client(payload_completo)
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(mb.lookup_by_isrc("GBAYE0000002", session=client))
    assert "GBAYE0000002" in mb._MB_CACHE
    assert mb._MB_CACHE["GBAYE0000002"][0] == "track-mbid-001"


# ---------------------------------------------------------------------------
# lookup_by_isrc -- sem releases (album_mbid None)
# ---------------------------------------------------------------------------

def test_sem_releases_album_mbid_e_none():
    payload = {
        "recordings": [
            {
                "id": "track-001",
                "releases": [],
                "artist-credit": [{"artist": {"id": "artist-001"}}],
            }
        ]
    }
    client = _make_client(payload)
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        track, album, artist = asyncio.run(mb.lookup_by_isrc("ISRCX01", session=client))
    assert album is None
    assert track == "track-001"
    assert artist == "artist-001"


# ---------------------------------------------------------------------------
# lookup_by_isrc -- artist-credit sem chave "artist"
# ---------------------------------------------------------------------------

def test_artist_credit_sem_artist_retorna_none():
    payload = {
        "recordings": [
            {
                "id": "track-002",
                "releases": [{"id": "album-002"}],
                "artist-credit": ["string-nao-dict"],
            }
        ]
    }
    client = _make_client(payload)
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        track, album, artist = asyncio.run(mb.lookup_by_isrc("ISRCX02", session=client))
    assert artist is None


# ---------------------------------------------------------------------------
# lookup_by_isrc -- sem recordings
# ---------------------------------------------------------------------------

def test_sem_recordings_retorna_nones_e_cacheia():
    client = _make_client({"recordings": []})
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        resultado = asyncio.run(mb.lookup_by_isrc("ISRCX03", session=client))
    assert resultado == (None, None, None)
    assert mb._MB_CACHE["ISRCX03"] == (None, None, None)


# ---------------------------------------------------------------------------
# lookup_by_isrc -- HTTPStatusError nao cacheia
# ---------------------------------------------------------------------------

def test_http_status_error_nao_cacheia():
    client = _make_client({}, status_code=500)
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        resultado = asyncio.run(mb.lookup_by_isrc("ISRCX04", session=client))
    assert resultado == (None, None, None)
    assert "ISRCX04" not in mb._MB_CACHE


# ---------------------------------------------------------------------------
# lookup_by_isrc -- excecao generica nao cacheia
# ---------------------------------------------------------------------------

def test_excecao_generica_nao_cacheia():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get.side_effect = RuntimeError("timeout")
    client.aclose = AsyncMock()
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        resultado = asyncio.run(mb.lookup_by_isrc("ISRCX05", session=client))
    assert resultado == (None, None, None)
    assert "ISRCX05" not in mb._MB_CACHE


# ---------------------------------------------------------------------------
# lookup_by_isrc -- sem session (cria client proprio)
# ---------------------------------------------------------------------------

def test_cria_client_proprio_quando_session_e_none(payload_completo):
    mock_client = _make_client(payload_completo)
    with (
        patch("qobuz_dl.musicbrainz.httpx.AsyncClient", return_value=mock_client),
        patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock),
    ):
        resultado = asyncio.run(mb.lookup_by_isrc("ISRCX06"))
    assert resultado[0] == "track-mbid-001"
    mock_client.aclose.assert_awaited_once()


# ---------------------------------------------------------------------------
# lookup_by_isrc -- ISRC normalizado para UPPER
# ---------------------------------------------------------------------------

def test_isrc_normalizado_para_upper(payload_completo):
    client = _make_client(payload_completo)
    with patch("qobuz_dl.musicbrainz.asyncio.sleep", new_callable=AsyncMock):
        asyncio.run(mb.lookup_by_isrc("gbaye0000999", session=client))
    assert "GBAYE0000999" in mb._MB_CACHE


# ---------------------------------------------------------------------------
# lookup_by_isrc -- cache hit apos semaphore (segunda verificacao)
# ---------------------------------------------------------------------------

def test_cache_preenchido_enquanto_aguarda_semaphore(payload_completo):
    """Simula outra task preenchendo cache enquanto esta esperava o semaphore."""
    isrc = "ISRCX07"

    async def _run():
        # Preenche cache ANTES de o semaphore ser liberado
        async def preenche_e_consulta():
            mb._MB_CACHE[isrc] = ("t", "a", "ar")
            return await mb.lookup_by_isrc(isrc)

        return await preenche_e_consulta()

    resultado = asyncio.run(_run())
    # Deve retornar do cache sem fazer requisicao
    assert resultado == ("t", "a", "ar")


# ---------------------------------------------------------------------------
# Headers e constantes
# ---------------------------------------------------------------------------

def test_mb_base_url():
    assert mb._MB_BASE == "https://musicbrainz.org/ws/2"


def test_headers_contem_user_agent():
    assert "User-Agent" in mb._MB_HEADERS
    assert "qobuz-dl-ultra" in mb._MB_HEADERS["User-Agent"]


def test_headers_aceitam_json():
    assert mb._MB_HEADERS["Accept"] == "application/json"
