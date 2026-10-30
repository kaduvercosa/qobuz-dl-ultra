"""Pipeline de download: a rede e o pós-processamento se sobrepõem.

Sem pipeline, a vaga (slot) de download de uma faixa fica presa durante a faixa
INTEIRA: baixar, gravar tags, buscar letras, verificar a integridade. Enquanto a
faixa grava tags ou espera uma letra, a rede daquela vaga fica parada.

Com pipeline a faixa tem dois estágios:

  1. rede -- obter a URL e baixar; ocupa a vaga de download;
  2. pós  -- tags, capa, letras, verificação; ocupa a vaga do PÓS.

Ao terminar o download a faixa libera a vaga de rede (a próxima já começa) e
segue sozinha, numa tarefa, até o fim. O teto de downloads simultâneos continua
sendo ``max_workers``: o pipeline só tira o tempo ocioso. Faixas em andamento
(baixando + pós) ficam limitadas por ``max_workers + vagas do pós``, então a
fila do pós nunca cresce sem limite.

Como a faixa avisa que terminou de baixar sem mudar nenhuma assinatura: um
ContextVar (``current_handoff``) é preenchido por ``Pipeline.run`` e lido dentro
de ``Download._download_and_tag_impl``.

Concorrência adaptativa (opcional, ``adaptive_workers``): ``AdaptiveLimiter``
faz o papel do semáforo de download e ajusta o teto -- cai pela metade quando há
falha de rede/limite do servidor e sobe 1 a cada ``grow_after`` downloads bons
(AIMD). Nunca passa do ``max_workers`` configurado.
"""

from __future__ import annotations

import asyncio
import collections
import contextvars
import time
import weakref
from collections.abc import Awaitable, Callable
from typing import Any, Optional

# Faixas no pós-processamento ao mesmo tempo (tags, capa, letras, verificação).
MAX_POST_WORKERS = 4

OnChange = Callable[[int, int, str], None]

# Preenchido por Pipeline.run; lido por Download._download_and_tag_impl.
current_handoff: contextvars.ContextVar[Optional["PipelineHandoff"]] = (
    contextvars.ContextVar("qobuz_pipeline_handoff", default=None)
)

# Um semáforo por event loop, tipo e limite (mesma ideia do run_bounded).
_shared: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, dict[tuple[str, int], asyncio.Semaphore]
] = weakref.WeakKeyDictionary()


def _shared_semaphore(kind: str, limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    per_loop = _shared.setdefault(loop, {})
    key = (kind, limit)
    semaphore = per_loop.get(key)
    if semaphore is None:
        semaphore = asyncio.Semaphore(limit)
        per_loop[key] = semaphore
    return semaphore


def post_limit(max_workers: int) -> int:
    """Vagas do estágio pós: ``max_workers``, no máximo ``MAX_POST_WORKERS``."""
    return max(1, min(int(max_workers), MAX_POST_WORKERS))


def pipeline_enabled(settings: Any) -> bool:
    """Ligado só quando as configurações dizem (o padrão do projeto é ligado)."""
    return bool(getattr(settings, "pipeline", False))


def adaptive_enabled(settings: Any) -> bool:
    return bool(getattr(settings, "adaptive_workers", False))


class AdaptiveLimiter:
    """Semáforo cujo teto sobe e desce (AIMD), usado como vaga de download.

    Começa no máximo configurado. ``on_throttle`` corta o teto pela metade (no
    mínimo ``min_limit``), no máximo uma vez a cada ``cooldown`` segundos, para
    uma rajada de falhas simultâneas não derrubar o teto até o mínimo.
    ``on_success`` sobe 1 a cada ``grow_after`` sucessos seguidos, até o máximo.
    Vagas já em uso não são tiradas: o corte vale para as próximas.
    """

    def __init__(
        self,
        max_limit: int,
        *,
        min_limit: int = 1,
        grow_after: int = 4,
        cooldown: float = 5.0,
        on_change: Optional[OnChange] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_limit = max(1, int(max_limit))
        self.min_limit = max(1, min(int(min_limit), self.max_limit))
        self._limit = self.max_limit
        self._grow_after = max(1, int(grow_after))
        self._cooldown = cooldown
        self._on_change = on_change
        self._clock = clock
        self._in_use = 0
        self._streak = 0
        self._last_cut: Optional[float] = None
        self._waiters: collections.deque[asyncio.Future[None]] = collections.deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        return self._in_use

    def _wake(self) -> None:
        while self._waiters and self._in_use < self._limit:
            waiter = self._waiters.popleft()
            if waiter.done():  # cancelado enquanto esperava
                continue
            self._in_use += 1
            waiter.set_result(None)

    async def acquire(self) -> None:
        if self._in_use < self._limit and not self._waiters:
            self._in_use += 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # Já tinha sido acordado: devolve a vaga que recebeu.
                self.release()
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    # Já tinha saído da fila: nada a remover.
                    pass
            raise

    def release(self) -> None:
        self._in_use = max(0, self._in_use - 1)
        self._wake()

    async def __aenter__(self) -> "AdaptiveLimiter":
        await self.acquire()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.release()

    def _set_limit(self, new: int, reason: str) -> None:
        old = self._limit
        if new == old:
            return
        self._limit = new
        if self._on_change is not None:
            self._on_change(old, new, reason)
        self._wake()

    def on_success(self) -> None:
        self._streak += 1
        if self._streak >= self._grow_after and self._limit < self.max_limit:
            self._streak = 0
            self._set_limit(self._limit + 1, "rede estável")

    def on_throttle(
        self, reason: str = "instabilidade da rede ou limite do servidor"
    ) -> None:
        self._streak = 0
        now = self._clock()
        if self._last_cut is not None and now - self._last_cut < self._cooldown:
            return
        new = max(self.min_limit, self._limit // 2)
        if new < self._limit:
            self._last_cut = now
            self._set_limit(new, reason)


class PipelineHandoff:
    """Elo entre a faixa e o pipeline (um por faixa)."""

    def __init__(
        self,
        post: asyncio.Semaphore,
        limiter: Optional[AdaptiveLimiter] = None,
    ) -> None:
        self._post = post
        self._limiter = limiter
        self._event = asyncio.Event()
        self._held = False

    async def downloaded(self) -> None:
        """O ARQUIVO terminou de baixar: libera a vaga de rede e entra no pós.

        Chamado uma vez pela faixa (as seguintes não fazem nada). A vaga de rede
        é liberada ANTES de esperar a vaga do pós, para a próxima faixa já
        começar enquanto esta espera a sua vez.
        """
        if self._event.is_set():
            return
        self._event.set()
        if self._limiter is not None:
            self._limiter.on_success()
        await self._post.acquire()
        self._held = True

    def report_throttle(self, reason: str = "falha no download") -> None:
        if self._limiter is not None:
            self._limiter.on_throttle(reason)

    def release(self) -> None:
        """Devolve a vaga do pós (seguro chamar de novo)."""
        if self._held:
            self._held = False
            self._post.release()

    async def wait_downloaded(self, task: "asyncio.Task[Any]") -> None:
        """Espera a faixa terminar de baixar OU a tarefa acabar (o que vier antes)."""
        waiter = asyncio.ensure_future(self._event.wait())
        try:
            await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            waiter.cancel()


class Pipeline:
    """Agenda faixas em dois estágios. Crie dentro de uma corrotina."""

    def __init__(
        self, max_workers: int, *, limiter: Optional[AdaptiveLimiter] = None
    ) -> None:
        workers = max(1, int(max_workers))
        self.post_limit = post_limit(workers)
        self._post = _shared_semaphore("post", self.post_limit)
        self._inflight = _shared_semaphore("inflight", workers + self.post_limit)
        self.limiter = limiter
        self._tasks: set[asyncio.Task[Any]] = set()

    async def run(self, make: Callable[[], Awaitable[Any]]) -> Any:
        """Roda ``make()`` (a faixa inteira) e volta quando o DOWNLOAD termina.

        Devolve o resultado direto se a faixa acabou antes de baixar (pulada,
        falha) ou a TAREFA ainda em andamento (pós-processamento); quem chamou
        resolve depois com ``resolve_pending``. Exceções da faixa que acabou
        cedo sobem normalmente.
        """
        await self._inflight.acquire()
        handoff = PipelineHandoff(self._post, self.limiter)

        async def body() -> Any:
            try:
                return await make()
            finally:
                handoff.release()
                self._inflight.release()

        token = current_handoff.set(handoff)
        try:
            task = asyncio.create_task(body())
        except BaseException:
            self._inflight.release()
            raise
        finally:
            current_handoff.reset(token)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        try:
            await handoff.wait_downloaded(task)
        except asyncio.CancelledError:
            task.cancel()
            raise
        if task.done():
            return task.result()
        return task

    async def cancel_all(self) -> None:
        """Cancela as faixas ainda no pós (interrupção do usuário)."""
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


async def resolve_pending(results: list[Any]) -> list[Any]:
    """Aguarda as faixas que ficaram no pós e devolve o resultado final de cada uma.

    Tarefas que falharam entram como a exceção (igual ao ``run_bounded``).
    Cancelamento cancela as tarefas restantes antes de propagar.
    """
    pending = [(i, r) for i, r in enumerate(results) if isinstance(r, asyncio.Task)]
    if not pending:
        return results
    try:
        done = await asyncio.gather(*(t for _, t in pending), return_exceptions=True)
    except BaseException:
        for _, task in pending:
            task.cancel()
        await asyncio.gather(*(t for _, t in pending), return_exceptions=True)
        raise
    resolved = list(results)
    for (index, _), value in zip(pending, done):
        resolved[index] = value
    return resolved


async def run_in_pipeline(
    pipeline: Optional[Pipeline], make: Callable[[], Awaitable[Any]]
) -> Any:
    """``await make()``, ou o pipeline em duas etapas quando há um."""
    if pipeline is None:
        return await make()
    return await pipeline.run(make)


def report_limit_change(old: int, new: int, reason: str) -> None:
    """Avisa no terminal quando o limite adaptativo muda."""
    from qobuz_dl import ui

    if new < old:
        ui.warn(f"Reduzindo downloads simultâneos: {old} -> {new} ({reason})")
    else:
        ui.step(f"Aumentando downloads simultâneos: {old} -> {new} ({reason})")


def build_slots(
    workers: int,
    settings: Any,
    *,
    parallel: bool,
    on_change: Optional[OnChange] = report_limit_change,
) -> tuple[Any, Optional[Pipeline]]:
    """(vagas de download, pipeline ou None) para um lote com ``workers`` ao mesmo tempo.

    Sem paralelismo, ou com ``pipeline`` desligado, é o semáforo de sempre.
    """
    workers = max(1, int(workers))
    if not (parallel and workers > 1 and pipeline_enabled(settings)):
        return asyncio.Semaphore(workers), None
    limiter: Optional[AdaptiveLimiter] = None
    if adaptive_enabled(settings):
        limiter = AdaptiveLimiter(workers, on_change=on_change)
    slots: Any = limiter if limiter is not None else asyncio.Semaphore(workers)
    return slots, Pipeline(workers, limiter=limiter)
