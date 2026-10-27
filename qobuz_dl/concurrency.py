"""Helpers para executar trabalho assíncrono com concorrência limitada.

Chamadas simultâneas a ``run_bounded`` que usam o mesmo ``limit`` compartilham
um semáforo por event loop. Assim, dois fluxos paralelos independentes não
multiplicam o teto configurado em ``max_workers``.
"""

import asyncio
import weakref
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")

# Um semáforo por event loop e limite: não compartilha objetos asyncio entre
# loops diferentes e permite que testes/consumidores usem limites distintos.
_semaphores_por_loop: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[int, asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def _global_semaphore(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    por_limite = _semaphores_por_loop.get(loop)
    if por_limite is None:
        por_limite = {}
        _semaphores_por_loop[loop] = por_limite
    semaphore = por_limite.get(limit)
    if semaphore is None:
        semaphore = asyncio.Semaphore(limit)
        por_limite[limit] = semaphore
    return semaphore


async def run_bounded(
    items: Sequence[T],
    worker: Callable[[int, T], Awaitable[R]],
    *,
    limit: int,
) -> list[R | Exception]:
    """Execute itens preservando a ordem e respeitando o teto compartilhado.

    ``limit`` limita simultaneamente os workers desta chamada e o conjunto de
    chamadas ``run_bounded`` com o mesmo limite no mesmo event loop.
    Exceções comuns são retornadas no lugar do resultado daquele item;
    cancelamentos propagam depois que os workers forem cancelados e aguardados.
    """
    if limit <= 0:
        raise ValueError("limit deve ser maior que zero")

    worker_count = min(limit, len(items))
    if worker_count == 0:
        return []

    semaphore = _global_semaphore(limit)
    queue: asyncio.Queue[tuple[int, T] | None] = asyncio.Queue()
    for index, item in enumerate(items):
        queue.put_nowait((index, item))
    for _ in range(worker_count):
        queue.put_nowait(None)

    results: list[R | Exception | None] = [None] * len(items)

    async def run_worker() -> None:
        while True:
            entry = await queue.get()
            if entry is None:
                return
            index, item = entry
            try:
                async with semaphore:
                    results[index] = await worker(index, item)
            except Exception as exc:
                results[index] = exc

    tasks = [
        asyncio.create_task(run_worker(), name=f"run_bounded_worker_{index}")
        for index in range(worker_count)
    ]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise

    return cast(list[R | Exception], results)
