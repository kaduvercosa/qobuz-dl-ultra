"""Testes do pool de threads compartilhado por todo o processo."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from qobuz_dl import executors

pytestmark = pytest.mark.unit


def test_heavy_executor_cria_um_pool_com_o_limite_configurado(monkeypatch):
    monkeypatch.setattr(executors, "_heavy", None)
    monkeypatch.setattr(executors, "_heavy_workers", None)

    pool = executors.heavy_executor(3)
    try:
        assert isinstance(pool, ThreadPoolExecutor)
        assert pool._max_workers == 3
        assert executors.heavy_executor_workers() == 3
    finally:
        pool.shutdown(wait=True)
        monkeypatch.setattr(executors, "_heavy", None)
        monkeypatch.setattr(executors, "_heavy_workers", None)


def test_heavy_executor_nao_cria_outro_pool_ao_mudar_o_limite(monkeypatch):
    monkeypatch.setattr(executors, "_heavy", None)
    monkeypatch.setattr(executors, "_heavy_workers", None)

    primeiro = executors.heavy_executor(2)
    segundo = executors.heavy_executor(8)
    try:
        assert segundo is primeiro
        assert segundo._max_workers == 2
        assert executors.heavy_executor_workers() == 2
    finally:
        primeiro.shutdown(wait=True)
        monkeypatch.setattr(executors, "_heavy", None)
        monkeypatch.setattr(executors, "_heavy_workers", None)
