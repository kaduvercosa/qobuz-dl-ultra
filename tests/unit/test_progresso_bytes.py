"""Bytes por faixa que movem o anel do Atalho (progresso_bytes.py)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest import mock

import pytest

from qobuz_dl import downloader, progresso_bytes


def setup_function() -> None:
    progresso_bytes.reiniciar()


def teardown_function() -> None:
    progresso_bytes.reiniciar()


def test_fracao_com_total_conhecido() -> None:
    progresso_bytes.registrar("a", 50, 100)
    assert progresso_bytes.parcial() == pytest.approx(0.5)
    assert progresso_bytes.em_andamento() == 1


def test_total_desconhecido_cresce_sem_chegar_a_um() -> None:
    anterior = 0.0
    for baixado in (1, 10**6, 10**7, 10**8, 10**9):
        progresso_bytes.registrar("a", baixado, None)
        atual = progresso_bytes.parcial()
        assert anterior < atual < progresso_bytes.TETO_FRACAO
        anterior = atual
    progresso_bytes.registrar("a", 10**15, None)  # absurdo: encosta no teto, não passa
    assert progresso_bytes.parcial() <= progresso_bytes.TETO_FRACAO


def test_faixa_em_andamento_nunca_passa_do_teto() -> None:
    progresso_bytes.registrar("a", 100, 100)
    progresso_bytes.registrar("b", 500, 100)  # mais que o total anunciado
    assert progresso_bytes.parcial() == pytest.approx(2 * progresso_bytes.TETO_FRACAO)


def test_faixas_em_paralelo_somam() -> None:
    progresso_bytes.registrar("a", 50, 100)
    progresso_bytes.registrar("b", 25, 100)
    assert progresso_bytes.parcial() == pytest.approx(0.75)


def test_concluir_tira_da_conta_e_reiniciar_limpa() -> None:
    progresso_bytes.registrar("a", 50, 100)
    progresso_bytes.registrar("b", 50, 100)
    progresso_bytes.concluir("a")
    progresso_bytes.concluir("inexistente")  # não levanta
    assert progresso_bytes.parcial() == pytest.approx(0.5)
    progresso_bytes.reiniciar()
    assert progresso_bytes.parcial() == 0.0
    assert progresso_bytes.em_andamento() == 0


def test_dado_estranho_nunca_levanta() -> None:
    progresso_bytes.registrar("a", "abc", None)  # type: ignore[arg-type]
    assert progresso_bytes.em_andamento() == 0
    progresso_bytes.registrar("b", -5, 100)  # bytes negativos viram 0
    assert progresso_bytes.parcial() == 0.0
    progresso_bytes.registrar("c", 10, 0)  # total 0 = desconhecido
    assert 0.0 < progresso_bytes.parcial() < 1.0


def _baixar(fake_download_file) -> None:
    """Roda o download atômico de uma faixa com o transporte trocado por um falso."""
    antes = downloader.abort_event.is_set()
    downloader.abort_event.clear()
    try:
        with mock.patch.object(downloader, "download_file", fake_download_file):
            asyncio.run(
                downloader._tqdm_download_atomic(
                    "http://exemplo/faixa",
                    "/tmp/qobuz-teste/faixa.flac",
                    "Faixa de teste",
                    is_parallel=True,
                    session=object(),
                )
            )
    finally:
        if antes:
            downloader.abort_event.set()


def test_downloader_registra_os_bytes_e_limpa_ao_terminar() -> None:
    vistos: list[float] = []

    async def falso(http, url, fname, **kw):
        kw["on_progress"](50, 100)
        vistos.append(progresso_bytes.parcial())
        kw["on_progress"](100, 100)
        vistos.append(progresso_bytes.parcial())
        return SimpleNamespace(size=100)

    _baixar(falso)
    assert vistos[0] == pytest.approx(0.5)
    assert vistos[1] == pytest.approx(progresso_bytes.TETO_FRACAO)
    assert progresso_bytes.em_andamento() == 0  # saiu da conta ao terminar


def test_downloader_limpa_tambem_quando_o_download_falha() -> None:
    async def falso(http, url, fname, **kw):
        kw["on_progress"](10, 100)
        raise RuntimeError("falha de teste")

    with pytest.raises(RuntimeError):
        _baixar(falso)
    assert progresso_bytes.em_andamento() == 0
