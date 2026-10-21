import httpx
import pytest
import respx

from qobuz_dl.qopy import Client

pytestmark = pytest.mark.network


@pytest.mark.asyncio
@respx.mock
async def test_api_call_com_respx_intercepta_endpoint_async():
    client = Client()
    client.session = httpx.AsyncClient()
    client.base = "https://api.test/"
    client.id = "app123"
    client.sec = "secret"
    client.force_english = True
    client._modern_sig = lambda _endpoint, _params, _secret: "sig"

    route = respx.get("https://api.test/album/get").mock(
        return_value=httpx.Response(200, json={"title": "Teste"})
    )
    try:
        result = await client.api_call("album/get", id="42")
    finally:
        await client.session.aclose()

    assert result == {"title": "Teste"}
    assert route.called
    assert route.calls.last.request.url.params["album_id"] == "42"
