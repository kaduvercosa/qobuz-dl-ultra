"""Helpers para executar trabalho assíncrono com concorrência limitada.

Chamadas simultâneas a ``run_bounded`` que usam o mesmo ``limit`` compartilham
um semáforo por event loop. Assim, dois fluxos paralelos independentes não
multiplicam o teto configurado em ``max_workers``.
"""

import asyncio
import contextvars
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


# Limites cujo slot compartilhado a tarefa atual JÁ segura. Serve para detectar
# um ``run_bounded`` aninhado com o mesmo ``limit``: o de dentro esperaria um
# slot que só o de fora (que está esperando por ele) poderia liberar -- trava
# para sempre. Cada worker enxerga a própria cópia do contexto, então um worker
# não "vaza" o marcador para os vizinhos.
_slots_seguros: contextvars.ContextVar[frozenset[int]] = contextvars.ContextVar(
    "run_bounded_slots_seguros", default=frozenset()
)


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

    Chamada aninhada com o mesmo ``limit`` (feita de dentro de um worker de
    outro ``run_bounded``): o worker de fora já segura um slot, então a de
    dentro roda os itens um de cada vez, aproveitando esse slot. Assim não há
    espera circular (travamento) e o teto total continua respeitado.
    """
    if limit <= 0:
        raise ValueError("limit deve ser maior que zero")

    aninhado = limit in _slots_seguros.get()
    worker_count = 1 if aninhado else min(limit, len(items))
    worker_count = min(worker_count, len(items))
    if worker_count == 0:
        return []

    semaphore = None if aninhado else _global_semaphore(limit)
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
                if semaphore is None:
                    results[index] = await worker(index, item)
                else:
                    async with semaphore:
                        marcador = _slots_seguros.set(_slots_seguros.get() | {limit})
                        try:
                            results[index] = await worker(index, item)
                        finally:
                            _slots_seguros.reset(marcador)
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
