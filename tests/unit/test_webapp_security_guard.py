"""Testes do middleware local_origin_guard (host, origem e cabeçalhos)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from qobuz_dl import webapp


@pytest.fixture(autouse=True)
def _config_isolada(tmp_path, monkeypatch):
    config_dir = tmp_path / "cfg"
    config_dir.mkdir()
    monkeypatch.setattr(
        webapp,
        "get_config_paths",
        lambda: {
            "config_path": str(config_dir),
            "config_file": str(config_dir / "config.ini"),
            "qobuz_db": str(config_dir / "downloads.db"),
        },
    )


def make_client(*, demo=False, allow_network=False) -> TestClient:
    app = webapp.create_app(demo=demo, allow_network=allow_network)
    return TestClient(app, base_url="http://localhost")


def get(client, headers=None):
    return client.get("/api/tools", headers=headers or {})


# --- modo local -----------------------------------------------------------------


@pytest.mark.parametrize(
    "host", ["localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:9999", "[::1]:8000"]
)
def test_hosts_loopback_sao_aceitos(host):
    assert get(make_client(), {"host": host}).status_code == 200


@pytest.mark.parametrize(
    "host", ["evil.com", "localhost.evil.com", "192.168.0.10", "testserver"]
)
def test_hosts_externos_sao_bloqueados(host):
    r = get(make_client(), {"host": host})
    assert r.status_code == 403
    assert "conexões locais" in r.text


def test_origin_externo_e_bloqueado():
    r = get(make_client(), {"host": "localhost", "origin": "https://evil.example.com"})
    assert r.status_code == 403
    assert "Origem" in r.text


@pytest.mark.parametrize(
    "origin", ["http://localhost:3000", "http://127.0.0.1:8000", "http://[::1]:8000"]
)
def test_origin_loopback_e_aceito(origin):
    r = get(make_client(), {"host": "localhost", "origin": origin})
    assert r.status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_sec_fetch_site_externo_e_bloqueado(site):
    r = get(make_client(), {"host": "localhost", "sec-fetch-site": site})
    assert r.status_code == 403


@pytest.mark.parametrize("site", ["same-origin", "none"])
def test_sec_fetch_site_proprio_e_aceito(site):
    r = get(make_client(), {"host": "localhost", "sec-fetch-site": site})
    assert r.status_code == 200


# --- allow_network --------------------------------------------------------------


def test_rede_aceita_qualquer_host():
    client = make_client(allow_network=True)
    assert get(client, {"host": "192.168.0.10:8000"}).status_code == 200


def test_rede_aceita_origin_igual_ao_host():
    client = make_client(allow_network=True)
    r = get(
        client,
        {"host": "192.168.0.10:8000", "origin": "http://192.168.0.10:8000"},
    )
    assert r.status_code == 200


def test_rede_bloqueia_origin_diferente_do_host():
    client = make_client(allow_network=True)
    r = get(
        client,
        {"host": "192.168.0.10:8000", "origin": "http://evil.example.com"},
    )
    assert r.status_code == 403


def test_rede_bloqueia_cross_site():
    client = make_client(allow_network=True)
    r = get(client, {"host": "192.168.0.10", "sec-fetch-site": "cross-site"})
    assert r.status_code == 403


# --- modo demo (comportamento atual: o guard é ignorado) -------------------------


def test_demo_ignora_o_guard():
    client = make_client(demo=True)
    r = get(client, {"host": "evil.com", "origin": "https://evil.example.com"})
    assert r.status_code == 200


# --- cabeçalhos de segurança ------------------------------------------------------


def test_respostas_ok_trazem_cabecalhos_de_seguranca():
    r = get(make_client(), {"host": "localhost"})
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp
    assert "script-src 'self'" in csp
