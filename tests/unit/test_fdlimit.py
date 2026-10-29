"""Limite de arquivos abertos: sobe o soft até o hard (no máx. o alvo)."""

import pytest

from qobuz_dl.fdlimit import TARGET_SOFT_LIMIT, raise_open_file_limit

resource = pytest.importorskip("resource")

pytestmark = pytest.mark.unit


def _falso(monkeypatch, soft, hard, falha=None):
    chamadas = []

    def set_(which, limites):
        if falha:
            raise falha
        chamadas.append(limites)

    monkeypatch.setattr(resource, "getrlimit", lambda which: (soft, hard))
    monkeypatch.setattr(resource, "setrlimit", set_)
    return chamadas


def test_sobe_ate_o_alvo_quando_o_hard_e_infinito(monkeypatch):
    chamadas = _falso(monkeypatch, 256, resource.RLIM_INFINITY)
    assert raise_open_file_limit() == TARGET_SOFT_LIMIT
    assert chamadas == [(TARGET_SOFT_LIMIT, resource.RLIM_INFINITY)]


def test_respeita_o_hard(monkeypatch):
    chamadas = _falso(monkeypatch, 256, 1024)
    assert raise_open_file_limit() == 1024
    assert chamadas == [(1024, 1024)]


def test_limite_ja_alto_nao_e_mexido(monkeypatch):
    chamadas = _falso(monkeypatch, 8192, resource.RLIM_INFINITY)
    assert raise_open_file_limit() == 8192
    assert chamadas == []


def test_soft_infinito_nao_e_mexido(monkeypatch):
    chamadas = _falso(monkeypatch, resource.RLIM_INFINITY, resource.RLIM_INFINITY)
    assert raise_open_file_limit() == resource.RLIM_INFINITY
    assert chamadas == []


@pytest.mark.parametrize("erro", [ValueError("x"), OSError("y")])
def test_falha_do_sistema_nao_derruba(monkeypatch, erro):
    _falso(monkeypatch, 256, resource.RLIM_INFINITY, falha=erro)
    assert raise_open_file_limit() is None
