"""Testes do helper genérico de concorrência limitada."""

import asyncio

import pytest

from qobuz_dl.concurrency import run_bounded

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("limit", [0, -1])
async def test_run_bounded_rejeita_limite_nao_positivo(limit: int):
    async def worker(index: int, item: int) -> int:
        return item

    with pytest.raises(ValueError):
        await run_bounded([1], worker, limit=limit)


async def test_run_bounded_preserva_ordem_original():
    primeiro_terminou = asyncio.Event()
    liberar_primeiro = asyncio.Event()

    async def worker(index: int, item: str) -> str:
        if index == 0:
            await liberar_primeiro.wait()
            primeiro_terminou.set()
        else:
            primeiro_terminou.set()
        return item.upper()

    task = asyncio.create_task(run_bounded(["primeiro", "segundo"], worker, limit=2))
    await primeiro_terminou.wait()
    liberar_primeiro.set()

    assert await task == ["PRIMEIRO", "SEGUNDO"]


async def test_run_bounded_respeita_teto_real_de_concorrencia():
    limit = 3
    started = 0
    active = 0
    peak = 0
    limit_reached = asyncio.Event()
    liberar_workers = asyncio.Event()

    async def worker(index: int, item: int) -> int:
        nonlocal started, active, peak
        started += 1
        active += 1
        peak = max(peak, active)
        if started == limit:
            limit_reached.set()
        try:
            await liberar_workers.wait()
            return item
        finally:
            active -= 1

    task = asyncio.create_task(run_bounded(list(range(9)), worker, limit=limit))
    await limit_reached.wait()

    # Enquanto os três primeiros worker aguardam a barreira, nenhum quarto
    # worker pode ter começado.
    assert started == limit
    assert active == limit
    assert peak == limit

    liberar_workers.set()
    assert await task == list(range(9))
    assert peak == limit
    assert active == 0


async def test_run_bounded_devolve_excecao_e_processa_demais_itens():
    processados: list[int] = []

    async def worker(index: int, item: int) -> int:
        processados.append(item)
        if item == 2:
            raise ValueError("falha isolada")
        return item * 10

    resultados = await run_bounded([0, 1, 2, 3], worker, limit=2)

    assert resultados[0:2] == [0, 10]
    assert isinstance(resultados[2], ValueError)
    assert str(resultados[2]) == "falha isolada"
    assert resultados[3] == 30
    assert sorted(processados) == [0, 1, 2, 3]


async def test_run_bounded_cancelamento_aguarda_todos_os_workers():
    started = 0
    finalized = 0
    enough_started = asyncio.Event()
    nunca_liberar = asyncio.Event()

    async def worker(index: int, item: int) -> int:
        nonlocal started, finalized
        started += 1
        if started == 2:
            enough_started.set()
        try:
            await nunca_liberar.wait()
            return item
        finally:
            finalized += 1

    task = asyncio.create_task(run_bounded([1, 2, 3, 4], worker, limit=2))
    await enough_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert started == 2
    assert finalized == 2


async def test_run_bounded_compartilha_limite_entre_chamadas_concorrentes():
    limit = 2
    active = 0
    peak = 0
    started = 0
    enough_started = asyncio.Event()
    liberar = asyncio.Event()

    async def worker(index: int, item: int) -> int:
        nonlocal active, peak, started
        active += 1
        started += 1
        peak = max(peak, active)
        if started >= limit:
            enough_started.set()
        try:
            await liberar.wait()
            return item
        finally:
            active -= 1

    first = asyncio.create_task(run_bounded([1, 2, 3], worker, limit=limit))
    second = asyncio.create_task(run_bounded([4, 5, 6], worker, limit=limit))
    await enough_started.wait()
    await asyncio.sleep(0)
    assert active == limit
    assert peak == limit

    liberar.set()
    first_results, second_results = await asyncio.gather(first, second)
    assert first_results == [1, 2, 3]
    assert second_results == [4, 5, 6]
    assert peak == limit
    assert active == 0
