"""Pool de threads dedicado ao trabalho bloqueante e demorado.

POR QUE EXISTE
--------------
`loop.run_in_executor(None, ...)` e `asyncio.to_thread(...)` usam o pool padrão
do asyncio, que tem poucas threads (min(32, CPUs + 4); em aparelho com 2
núcleos, 6). Esse mesmo pool também grava os chunks dos downloads. Tarefas
longas (ffmpeg de verificação de integridade, com até 180 s; gravação de tags;
busca de letras por HTTP síncrono) ocupavam as threads e atrasavam a gravação
dos chunks dos outros downloads em paralelo.

Com este pool separado, o trabalho pesado nunca disputa thread com a gravação
de chunks. Uso:

    from qobuz_dl.executors import heavy_executor

    await loop.run_in_executor(heavy_executor(), funcao, *args)

NÃO usar para `shutil.rmtree`: dentro de thread ele trava no a-Shell/iOS (ver
tests/regression/test_sem_bloqueio_pesado_no_loop.py).
"""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor

_lock = threading.Lock()
_heavy: ThreadPoolExecutor | None = None


def _default_workers() -> int:
    # As tarefas são quase todas espera (subprocesso, rede), então mais threads
    # que núcleos é barato; o teto evita exagero em máquinas grandes.
    return max(4, min(16, (os.cpu_count() or 2) * 2))


def heavy_executor() -> ThreadPoolExecutor:
    """Devolve o pool dedicado, criando-o na primeira chamada."""
    global _heavy
    if _heavy is None:
        with _lock:
            if _heavy is None:
                _heavy = ThreadPoolExecutor(
                    max_workers=_default_workers(),
                    thread_name_prefix="qobuz-heavy",
                )
    return _heavy
