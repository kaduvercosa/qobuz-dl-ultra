"""`search_*` do cliente: falha da API vira "sem resultados", mas com aviso.

Antes o erro era engolido sem rastro: um token vencido ou a rede fora apareciam
como busca vazia. O retorno continua `{}` (os chamadores dependem disso); o que
mudou é o aviso com a causa.
"""

import logging

import pytest

from qobuz_dl.exceptions import AuthenticationError
from qobuz_dl.qopy import Client

pytestmark = pytest.mark.unit

TIPOS = [
    ("search_albums", "albums"),
    ("search_tracks", "tracks"),
    ("search_playlists", "playlists"),
    ("search_artists", "artists"),
]


class _Api:
    def __init__(self, resposta):
        self.resposta = resposta
        self.chamadas = []

    async def __call__(self, epoint, **kw):
        self.chamadas.append((epoint, kw))
        if isinstance(self.resposta, Exception):
            raise self.resposta
        return self.resposta


@pytest.fixture
def cliente(monkeypatch):
    def montar(resposta):
        c = Client()
        api = _Api(resposta)
        monkeypatch.setattr(c, "api_call", api)
        return c, api

    return montar


@pytest.mark.parametrize("metodo, tipo", TIPOS)
async def test_busca_repassa_tipo_consulta_e_limite(cliente, metodo, tipo):
    c, api = cliente({"ok": True})
    assert await getattr(c, metodo)("daft punk", limit=7) == {"ok": True}
    assert api.chamadas == [
        ("catalog/search", {"query": "daft punk", "type": tipo, "limit": 7})
    ]


@pytest.mark.parametrize("metodo, tipo", TIPOS)
async def test_falha_devolve_vazio_e_avisa_a_causa(cliente, caplog, metodo, tipo):
    c, _ = cliente(AuthenticationError("token vencido"))
    with caplog.at_level(logging.WARNING, logger="qobuz_dl.qopy"):
        assert await getattr(c, metodo)("x") == {}
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(avisos) == 1
    texto = avisos[0].getMessage()
    assert tipo in texto and "AuthenticationError" in texto and "token vencido" in texto


async def test_detalhe_da_falha_fica_no_nivel_debug(cliente, caplog):
    c, _ = cliente(RuntimeError("boom"))
    with caplog.at_level(logging.DEBUG, logger="qobuz_dl.qopy"):
        await c.search_albums("x")
    debug = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert debug and debug[0].exc_info is not None


async def test_sucesso_nao_gera_aviso(cliente, caplog):
    c, _ = cliente({"albums": {"items": []}})
    with caplog.at_level(logging.WARNING, logger="qobuz_dl.qopy"):
        await c.search_albums("x")
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_auth_repassa_token_rejeitado_no_user_get(monkeypatch):
    cliente = Client()

    async def rejeitar(*args, **kwargs):
        raise AuthenticationError("token expirado")

    monkeypatch.setattr(cliente, "api_call", rejeitar)
    with pytest.raises(AuthenticationError):
        await cliente.auth("conta@example.com", "", "token-expirado")
