"""Testa `qopy.Client.api_call()` -- o método central que monta os
parâmetros/assinatura de CADA chamada à API do Qobuz e decide GET vs.
POST. Já existem testes pro comportamento de retry (test_qopy_parsing.py)
-- aqui o foco é a lógica de MONTAGEM DE PARÂMETROS por endpoint, que
não tinha nenhum teste direto apesar de ser a maior função do arquivo
(~210 linhas, um `if/elif` por endpoint).

`self` fake via SimpleNamespace (mesmo padrão usado em core.py/
downloader.py) -- `session.request` é substituído por um fake que só
grava o que foi chamado e devolve uma resposta controlada pelo teste.
"""

from types import SimpleNamespace

import httpx
import pytest

from qobuz_dl import qopy
from qobuz_dl.exceptions import (
    AuthenticationError,
    InvalidAppSecretError,
    InvalidQuality,
)

pytestmark = pytest.mark.unit


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"status {self.status_code}", request=SimpleNamespace(), response=self
            )


def _client(response=None, **overrides):
    captured = {}
    resp = response if response is not None else FakeResponse()

    async def request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["kwargs"] = kwargs
        return resp

    self = SimpleNamespace(
        session=SimpleNamespace(request=request),
        base="https://www.qobuz.com/api.json/0.2/",
        id="app123",
        sec="segredo",
        uat="token-abc",
        user_id="u1",
        force_english=True,
        _normalize_json_strings=lambda d: d,
        _modern_sig=lambda epoint, params, sec: "assinatura-fake",
    )
    for key, value in overrides.items():
        setattr(self, key, value)
    return self, captured


# --------------------------------------------------------------------
# user/login
# --------------------------------------------------------------------
async def test_login_com_token_usa_user_auth_token_nao_email_senha():
    self, captured = _client()

    await qopy.Client.api_call(
        self, "user/login", user_auth_token="tok123", email="x@x.com", pwd="senha"
    )

    assert captured["kwargs"]["data"] == {
        "user_auth_token": "tok123",
        "app_id": "app123",
    }
    assert captured["method"] == "post"


async def test_login_sem_token_usa_email_e_senha():
    self, captured = _client()

    await qopy.Client.api_call(self, "user/login", email="x@x.com", pwd="senha")

    assert captured["kwargs"]["data"] == {
        "email": "x@x.com",
        "password": "senha",
        "app_id": "app123",
    }


async def test_login_400_com_invalid_no_corpo_lanca_authentication_error():
    self, _ = _client(
        response=FakeResponse(status_code=400, text="Invalid credentials")
    )

    with pytest.raises(AuthenticationError):
        await qopy.Client.api_call(self, "user/login", email="x", pwd="y")


async def test_login_400_sem_invalid_ainda_assim_lanca_http_status_error():
    """[Comportamento atual] Um 400 sem "invalid" no corpo cai no
    `else: logger.info("Logged: OK")` -- mas o código continua e chama
    `resp.raise_for_status()` de qualquer forma logo em seguida, que
    ainda lança porque 400 é status de erro. Ou seja: TODO 400 em
    user/login acaba levantando alguma exceção, só muda qual."""
    self, _ = _client(response=FakeResponse(status_code=400, text="algo genérico"))

    with pytest.raises(httpx.HTTPStatusError):
        await qopy.Client.api_call(self, "user/login", email="x", pwd="y")


# --------------------------------------------------------------------
# track/getFileUrl e file/url -- faixas de qualidade diferentes
# --------------------------------------------------------------------
async def test_get_file_url_fmt_id_invalido_lanca_sem_chamar_rede():
    self, captured = _client()

    with pytest.raises(InvalidQuality):
        await qopy.Client.api_call(self, "track/getFileUrl", id="1", fmt_id=99)

    assert captured == {}  # nunca chegou a montar a requisição


async def test_get_file_url_fmt_id_valido_monta_assinatura_legada():
    self, captured = _client()

    await qopy.Client.api_call(self, "track/getFileUrl", id="1", fmt_id=27)

    params = captured["kwargs"]["params"]
    assert params["track_id"] == "1"
    assert params["format_id"] == 27
    assert params["intent"] == "stream"
    assert "request_sig" in params and "request_ts" in params


async def test_file_url_aceita_conjunto_diferente_de_qualidades():
    # file/url NÃO aceita fmt_id=5 (MP3) -- só 6/7/27, diferente de
    # track/getFileUrl que aceita 5/6/7/27.
    self, _ = _client()

    with pytest.raises(InvalidQuality):
        await qopy.Client.api_call(self, "file/url", id="1", fmt_id=5)

    self2, captured2 = _client()
    await qopy.Client.api_call(self2, "file/url", id="1", fmt_id=6)
    assert captured2["kwargs"]["params"]["intent"] == "import"


async def test_resposta_400_em_endpoint_assinado_lanca_invalid_app_secret():
    self, _ = _client(response=FakeResponse(status_code=400, json_data={"erro": "x"}))

    with pytest.raises(InvalidAppSecretError):
        await qopy.Client.api_call(self, "track/getFileUrl", id="1", fmt_id=27)


# --------------------------------------------------------------------
# user/get -- único endpoint que devolve {} num 400 em vez de lançar
# --------------------------------------------------------------------
async def test_user_get_400_devolve_dict_vazio_sem_lancar():
    self, _ = _client(response=FakeResponse(status_code=400))

    resultado = await qopy.Client.api_call(self, "user/get")

    assert resultado == {}


# --------------------------------------------------------------------
# Ramo genérico (album/get, track/get, playlist/get, etc.)
# --------------------------------------------------------------------
async def test_album_get_usa_album_id_e_extra_lang_quando_force_english():
    self, captured = _client()

    await qopy.Client.api_call(self, "album/get", id="777")

    params = captured["kwargs"]["params"]
    assert params["album_id"] == "777"
    assert params["lang"] == "en"
    assert params["locale"] == "en_US"


async def test_force_english_false_nao_adiciona_lang_locale():
    self, captured = _client(force_english=False)

    await qopy.Client.api_call(self, "album/get", id="777")

    params = captured["kwargs"]["params"]
    assert "lang" not in params
    assert "locale" not in params


async def test_playlist_get_inclui_extra_tracks():
    self, captured = _client()

    await qopy.Client.api_call(self, "playlist/get", id="42")

    params = captured["kwargs"]["params"]
    assert params["playlist_id"] == "42"
    assert params["extra"] == "tracks"


async def test_playlist_create_usa_uat_e_campos_de_criacao():
    self, captured = _client()

    await qopy.Client.api_call(
        self, "playlist/create", name="Minha Lista", description="desc", is_public=True
    )

    params = captured["kwargs"]["data"]
    assert params["user_auth_token"] == "token-abc"
    assert params["name"] == "Minha Lista"
    assert params["is_public"] == "1"
    assert captured["method"] == "post"


async def test_playlist_add_tracks_usa_uat_playlist_id_e_track_ids():
    self, captured = _client()

    await qopy.Client.api_call(
        self, "playlist/addTracks", playlist_id="42", track_ids="1,2,3"
    )

    params = captured["kwargs"]["data"]
    assert params["user_auth_token"] == "token-abc"
    assert params["playlist_id"] == "42"
    assert params["track_ids"] == "1,2,3"
