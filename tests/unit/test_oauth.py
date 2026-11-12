"""Tests for the optional Qobuz OAuth login flow."""

from urllib.parse import parse_qs, urlparse

import pytest

from qobuz_dl import oauth


def test_build_oauth_url_uses_desktop_app_and_local_callback():
    url = oauth.build_oauth_url(12345)
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "www.qobuz.com"
    assert parsed.path == "/signin/oauth"
    assert params["ext_app_id"] == [oauth.OAUTH_APP_ID]
    assert params["redirect_url"] == ["http://localhost:12345/callback"]


def test_extract_code_from_qobuz_redirect_url():
    code = oauth.extract_code_from_url(
        "http://localhost:11111/callback?code_autorisation=one-time-code&state=abc"
    )

    assert code == "one-time-code"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a URL",
        "http://localhost:11111/callback?state=abc",
        "http://localhost:11111/callback?code_autorisation=",
    ],
)
def test_extract_code_rejects_missing_or_empty_code(url):
    with pytest.raises(ValueError):
        oauth.extract_code_from_url(url)


@pytest.mark.asyncio
async def test_exchange_code_requests_token_and_validates_profile(monkeypatch):
    import httpx

    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.path.endswith("/oauth/callback"):
            assert request.url.params["code"] == "temporary-code"
            assert request.url.params["private_key"]
            assert request.headers["X-App-Id"] == oauth.OAUTH_APP_ID
            return httpx.Response(200, json={"token": "user-token", "user_id": "123"})
        assert request.url.path.endswith("/user/login")
        assert request.headers["X-User-Auth-Token"] == "user-token"
        return httpx.Response(
            200,
            json={"user": {"display_name": "Test User", "email": "test@example.com"}},
        )

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        return original_client(transport=transport, **kwargs)

    monkeypatch.setattr(oauth.httpx, "AsyncClient", client_factory)

    result = await oauth.exchange_code("temporary-code")

    assert result["app_id"] == oauth.OAUTH_APP_ID
    assert result["user_auth_token"] == "user-token"
    assert result["user_id"] == "123"
    assert result["display_name"] == "Test User"
    assert result["email"] == "test@example.com"
    assert len(calls) == 2
