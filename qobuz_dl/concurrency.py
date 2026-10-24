"""Helpers para executar trabalho assíncrono com concorrência limitada."""

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import TypeVar, cast

T = TypeVar("T")
R = TypeVar("R")


async def run_bounded(
    items: Sequence[T],
    worker: Callable[[int, T], Awaitable[R]],
    *,
    limit: int,
) -> list[R | Exception]:
    """Execute ``worker`` para cada item com no máximo ``limit`` workers ativos.

    Os índices e resultados seguem a ordem de ``items``, independentemente da
    ordem em que as operações terminam. Exceções comuns são retornadas no
    lugar do resultado daquele item; cancelamentos propagam depois que todos
    os workers criados tiverem sido cancelados e aguardados.
    """
    if limit <= 0:
        raise ValueError("limit deve ser maior que zero")

    worker_count = min(limit, len(items))
    if worker_count == 0:
        return []

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
