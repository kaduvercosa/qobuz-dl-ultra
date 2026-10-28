"""Pool único de threads para trabalho bloqueante e demorado.

O tamanho é definido pela primeira chamada usando ``max_workers``. O pool não
é substituído durante a execução: trocar o executor enquanto existem tarefas
em andamento permitiria que o pool antigo e o novo trabalhassem juntos e
ultrapassassem o limite configurado.

Uso::

    await loop.run_in_executor(heavy_executor(settings.max_workers), func, *args)

Não usar para ``shutil.rmtree``: em alguns ambientes iOS/a-Shell isso bloqueia
(veja ``tests/regression/test_sem_bloqueio_pesado_no_loop.py``).
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

_lock = threading.Lock()
_heavy: ThreadPoolExecutor | None = None
_heavy_workers: int | None = None
_lyrics: ThreadPoolExecutor | None = None
_lyrics_workers: int | None = None

# Os provedores de letra limitam requisições (429): mais de 3 buscas ao mesmo
# tempo só aumentam as recusas, sem baixar o tempo total.
MAX_LYRICS_WORKERS = 3


def _default_workers() -> int:
    """Limite conservador quando o chamador ainda não tem configuração."""
    return max(1, min(16, (os.cpu_count() or 2) * 2))


def heavy_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """Devolve o pool global sem criar pools concorrentes de tamanhos distintos.

    ``max_workers`` deve ser o valor de ``settings.max_workers``. Como as
    configurações são carregadas antes do trabalho, a primeira chamada fixa o
    tamanho durante a vida do processo. Chamadas posteriores devolvem o mesmo
    pool, mesmo que algum chamador passe um valor diferente; não se troca um
    pool ativo por outro, pois isso permitiria concorrência agregada acima do
    limite.
    """
    global _heavy, _heavy_workers
    requested = (
        max(1, int(max_workers)) if max_workers is not None else _default_workers()
    )
    if _heavy is None:
        with _lock:
            if _heavy is None:
                _heavy = ThreadPoolExecutor(
                    max_workers=requested,
                    thread_name_prefix="qobuz-heavy",
                )
                _heavy_workers = requested
    return _heavy


def heavy_executor_workers() -> int | None:
    """Retorna o tamanho efetivo do pool (principalmente útil para testes)."""
    return _heavy_workers


def lyrics_executor(max_workers: int | None = None) -> ThreadPoolExecutor:
    """Pool próprio (e pequeno) para buscar/gravar letras.

    Letras são trabalho de REDE com esperas (cooldown de 429, timeouts). Se
    dividissem o pool de ``heavy_executor`` com tags e verificação de
    integridade, algumas faixas lentas de letra ocupariam todas as threads e as
    demais faixas ficariam sem vaga para gravar tags. Aqui o teto é
    ``min(max_workers, MAX_LYRICS_WORKERS)``, fixado pela primeira chamada.
    """
    global _lyrics, _lyrics_workers
    requested = (
        max(1, int(max_workers)) if max_workers is not None else _default_workers()
    )
    workers = min(requested, MAX_LYRICS_WORKERS)
    if _lyrics is None:
        with _lock:
            if _lyrics is None:
                _lyrics = ThreadPoolExecutor(
                    max_workers=workers,
                    thread_name_prefix="qobuz-lyrics",
                )
                _lyrics_workers = workers
    return _lyrics


def lyrics_executor_workers() -> int | None:
    """Retorna o tamanho efetivo do pool de letras (principalmente para testes)."""
    return _lyrics_workers
