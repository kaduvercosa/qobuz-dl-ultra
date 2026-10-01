"""Testa Bundle.__init__ e Bundle.create de qobuz_dl/bundle.py com
httpx.MockTransport: nenhuma requisição de rede real é feita.
"""

from functools import partial

import httpx
import pytest

from qobuz_dl import bundle as bundle_mod
from qobuz_dl.bundle import Bundle

pytestmark = pytest.mark.unit

LOGIN_HTML = '<script src="/resources/9.9.9-b001/bundle.js"></script>'
BUNDLE_JS = "var x = 1;"


def _handler(login_html=LOGIN_HTML, status=200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/login"):
            return httpx.Response(status, text=login_html)
        if request.url.path.endswith("bundle.js"):
            return httpx.Response(200, text=BUNDLE_JS)
        return httpx.Response(404)

    return handler


def _patch(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        bundle_mod.httpx, "Client", partial(httpx.Client, transport=transport)
    )
    monkeypatch.setattr(
        bundle_mod.httpx,
        "AsyncClient",
        partial(httpx.AsyncClient, transport=transport),
    )


def test_init_baixa_o_bundle_js(monkeypatch):
    _patch(monkeypatch, _handler())
    assert Bundle()._bundle == BUNDLE_JS


def test_init_sem_script_levanta_not_implemented(monkeypatch):
    _patch(monkeypatch, _handler(login_html="<html></html>"))
    with pytest.raises(NotImplementedError):
        Bundle()


def test_init_propaga_erro_http_do_login(monkeypatch):
    _patch(monkeypatch, _handler(status=500))
    with pytest.raises(httpx.HTTPStatusError):
        Bundle()


async def test_create_baixa_o_bundle_js(monkeypatch):
    _patch(monkeypatch, _handler())
    assert (await Bundle.create())._bundle == BUNDLE_JS


async def test_create_sem_script_guarda_html_do_login(monkeypatch):
    _patch(monkeypatch, _handler(login_html="<html>sem bundle</html>"))
    assert (await Bundle.create())._bundle == "<html>sem bundle</html>"
