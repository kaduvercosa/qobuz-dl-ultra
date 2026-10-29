"""Limite de arquivos abertos do processo.

No iOS/a-Shell o limite padrão (soft) costuma ser baixo. Com vários downloads em
paralelo, cada um com socket + arquivo .part (e os segmentos do fallback), o
processo chega a "Too many open files" (EMFILE). Aqui o limite soft sobe até o
hard (no máximo ``TARGET_SOFT_LIMIT``) logo na partida.
"""

import logging

logger = logging.getLogger(__name__)

TARGET_SOFT_LIMIT = 4096


def raise_open_file_limit(target: int = TARGET_SOFT_LIMIT) -> int | None:
    """Sobe o limite soft de arquivos abertos até ``min(target, hard)``.

    Devolve o limite soft resultante, ou None quando não dá para consultar ou
    mudar (Windows, ambiente restrito). Nunca levanta: é só uma proteção.
    """
    try:
        import resource
    except ImportError:  # Windows não tem o módulo
        return None
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        wanted = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft == resource.RLIM_INFINITY or soft >= wanted:
            return soft
        resource.setrlimit(resource.RLIMIT_NOFILE, (wanted, hard))
        return wanted
    except (ValueError, OSError) as exc:
        logger.debug("Não foi possível subir o limite de arquivos abertos: %s", exc)
        return None
