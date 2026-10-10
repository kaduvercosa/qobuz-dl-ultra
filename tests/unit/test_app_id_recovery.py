"""Testes de regressão para o App ID padrão e recuperação sob demanda.

Estes testes não fazem chamadas reais à API do Qobuz; todas as operações de
rede e autenticação são simuladas.
"""

import asyncio

import httpx
import pytest

from qobuz_dl import qopy
from qobuz_dl.exceptions import AuthenticationError


class FakeSession:
    def __init__(self, headers=None):
        self.headers = dict(headers or {})
        self.closed = False

    async def aclose(self):
        self.closed = True


class FakeBundle:
    calls = 0
    app_id = "987654321"
    secrets = {"utc": "fresh-secret"}

    @classmethod
    async def create(cls):
        cls.calls += 1
        return cls()

    def get_app_id(self):
        return self.app_id

    def get_secrets(self):
        return self.secrets


def http_error(status=401, body="invalid app_id"):
    request = httpx.Request("POST", "https://www.qobuz.com/api.json/0.2/user/login")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("rejected", request=request, response=response)


def setup_client_mocks(monkeypatch):
    monkeypatch.setattr(
        qopy, "make_client", lambda **kwargs: FakeSession(kwargs.get("headers"))
    )
    monkeypatch.setattr(qopy, "Bundle", FakeBundle)
    FakeBundle.calls = 0
    FakeBundle.app_id = "987654321"
    FakeBundle.secrets = {"utc": "fresh-secret"}

    async def cfg_setup(self):
        self.sec = "valid-secret"
        self._secret_validation_all_invalid = False

    monkeypatch.setattr(qopy.Client, "cfg_setup", cfg_setup)


def test_uses_default_app_id_without_bundle_on_normal_startup(monkeypatch):
    setup_client_mocks(monkeypatch)
    seen_ids = []

    async def auth(self, email, pwd, user_auth_token=None):
        seen_ids.append(self.id)

    monkeypatch.setattr(qopy.Client, "auth", auth)
    client = asyncio.run(
        qopy.Client.create("user@example.com", "password", None, ["local-secret"])
    )
    try:
        assert client.id == "798273057"
        assert seen_ids == ["798273057"]
        assert FakeBundle.calls == 0
    finally:
        asyncio.run(client.close())


def test_rejected_app_id_refreshes_bundle_and_retries_once(monkeypatch):
    setup_client_mocks(monkeypatch)
    seen_ids = []

    async def auth(self, email, pwd, user_auth_token=None):
        seen_ids.append(self.id)
        if len(seen_ids) == 1:
            raise http_error(401, "invalid app_id")

    monkeypatch.setattr(qopy.Client, "auth", auth)
    client = asyncio.run(
        qopy.Client.create("user@example.com", "password", "798273057", ["old-secret"])
    )
    try:
        assert seen_ids == ["798273057", "987654321"]
        assert client.id == "987654321"
        assert client.session.headers["X-App-Id"] == "987654321"
        assert client.secrets == ["fresh-secret"]
        assert FakeBundle.calls == 1
    finally:
        asyncio.run(client.close())


def test_bad_credentials_do_not_trigger_bundle_refresh(monkeypatch):
    setup_client_mocks(monkeypatch)

    async def auth(self, email, pwd, user_auth_token=None):
        raise AuthenticationError("Invalid email or password.")

    monkeypatch.setattr(qopy.Client, "auth", auth)
    with pytest.raises(AuthenticationError):
        asyncio.run(
            qopy.Client.create(
                "user@example.com", "wrong-password", "798273057", ["local-secret"]
            )
        )
    assert FakeBundle.calls == 0


def test_failed_retry_does_not_loop_or_fetch_bundle_twice(monkeypatch):
    setup_client_mocks(monkeypatch)
    calls = 0

    async def auth(self, email, pwd, user_auth_token=None):
        nonlocal calls
        calls += 1
        raise http_error(401, "invalid app_id")

    monkeypatch.setattr(qopy.Client, "auth", auth)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            qopy.Client.create(
                "user@example.com", "password", "798273057", ["old-secret"]
            )
        )
    assert calls == 2
    assert FakeBundle.calls == 1


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (400, "Bad request"),
        (401, "Unauthorized"),
        (403, "Forbidden"),
        (500, "Internal server error"),
    ],
)
def test_generic_http_error_does_not_trigger_bundle_refresh(monkeypatch, status, body):
    """Status HTTP genérico não prova que o App ID está inválido."""
    setup_client_mocks(monkeypatch)
    calls = 0

    async def auth(self, email, pwd, user_auth_token=None):
        nonlocal calls
        calls += 1
        raise http_error(status, body)

    monkeypatch.setattr(qopy.Client, "auth", auth)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            qopy.Client.create(
                "user@example.com", "password", "798273057", ["local-secret"]
            )
        )

    assert calls == 1
    assert FakeBundle.calls == 0


def test_same_bundle_configuration_does_not_retry_auth(monkeypatch):
    """Se o bundle retornar a mesma configuração, não repetir a autenticação."""
    setup_client_mocks(monkeypatch)
    FakeBundle.app_id = "798273057"
    FakeBundle.secrets = {"utc": "local-secret"}
    calls = 0

    async def auth(self, email, pwd, user_auth_token=None):
        nonlocal calls
        calls += 1
        raise http_error(401, "invalid app_id")

    monkeypatch.setattr(qopy.Client, "auth", auth)
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(
            qopy.Client.create(
                "user@example.com", "password", "798273057", ["local-secret"]
            )
        )

    assert calls == 1
    assert FakeBundle.calls == 1


def test_force_bundle_refresh_is_opt_in(monkeypatch):
    """A atualização manual consulta o bundle antes de tentar autenticar."""
    setup_client_mocks(monkeypatch)
    monkeypatch.setenv("QOBUZ_DL_FORCE_BUNDLE_REFRESH", "1")
    seen_ids = []

    async def auth(self, email, pwd, user_auth_token=None):
        seen_ids.append(self.id)

    monkeypatch.setattr(qopy.Client, "auth", auth)
    client = asyncio.run(
        qopy.Client.create(
            "user@example.com", "password", "798273057", ["local-secret"]
        )
    )
    try:
        assert FakeBundle.calls == 1
        assert seen_ids == ["987654321"]
        assert client.session.headers["X-App-Id"] == "987654321"
    finally:
        asyncio.run(client.close())
