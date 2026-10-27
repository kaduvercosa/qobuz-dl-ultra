"""Retry HTTP seletivo para tarefas paralelas, sem atrasar respostas saudáveis.

Somente GET/HEAD e falhas transitórias são repetidos. Um 429/503 publica um
cooldown por host compartilhado entre threads; chamadas normais não recebem
pausas artificiais. O módulo não cria clientes nem altera max_connections.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import threading
import time
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)
_MAX_ATTEMPTS = 4
_MAX_RETRY_AFTER = 60.0
_lock = threading.Lock()
_cooldowns: dict[str, float] = {}


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _retry_after(headers: Mapping[str, str]) -> float | None:
    value = headers.get("Retry-After") or headers.get("retry-after")
    if not value:
        return None
    try:
        return min(_MAX_RETRY_AFTER, max(0.0, float(value)))
    except (TypeError, ValueError):
        try:
            date = email.utils.parsedate_to_datetime(value).timestamp()
            return min(_MAX_RETRY_AFTER, max(0.0, date - time.time()))
        except (TypeError, ValueError, OverflowError):
            return None


def _delay(attempt: int, retry_after: float | None) -> float:
    if retry_after is not None:
        return retry_after
    base = min(8.0, 0.5 * (2 ** (attempt - 1)))
    return base + random.uniform(0.0, base * 0.2)


def _publish_cooldown(host: str, delay: float) -> None:
    if not host or delay <= 0:
        return
    until = time.monotonic() + delay
    with _lock:
        _cooldowns[host] = max(_cooldowns.get(host, 0.0), until)


def _wait_host(host: str) -> None:
    with _lock:
        delay = max(0.0, _cooldowns.get(host, 0.0) - time.monotonic())
    if delay:
        time.sleep(delay)


async def _await_host(host: str) -> None:
    with _lock:
        delay = max(0.0, _cooldowns.get(host, 0.0) - time.monotonic())
    if delay:
        await asyncio.sleep(delay)


def request_sync(client: httpx.Client, method: str, url: str, **kwargs):
    """Executa request síncrona resiliente; falhas finais são propagadas."""
    method = method.upper()
    host = _host(url)
    attempts = _MAX_ATTEMPTS if method in {"GET", "HEAD"} else 1
    for attempt in range(1, attempts + 1):
        _wait_host(host)
        try:
            response = getattr(client, method.lower())(url, **kwargs)
            status = getattr(response, "status_code", 200)
            retryable = isinstance(status, int) and (
                status in {408, 425, 429} or status >= 500
            )
            if not retryable:
                return response
            if attempt == attempts:
                logger.warning(
                    "HTTP %s %s continuou retornando %s após %d tentativas",
                    method,
                    host,
                    response.status_code,
                    attempts,
                )
                return response
            delay = _delay(attempt, _retry_after(response.headers))
            response.close()
            shared_cooldown = response.status_code in {429, 503}
            if shared_cooldown:
                _publish_cooldown(host, delay)
            logger.debug(
                "HTTP %s %s retornou %s; retry %d/%d em %.2fs",
                method,
                host,
                response.status_code,
                attempt,
                attempts,
                delay,
            )
            if not shared_cooldown:
                time.sleep(delay)
        except httpx.TransportError:
            if attempt == attempts:
                raise
            delay = _delay(attempt, None)
            time.sleep(delay)
    raise RuntimeError("fluxo de retry HTTP inesperado")


async def request_async(client: httpx.AsyncClient, method: str, url: str, **kwargs):
    """Executa request assíncrona resiliente sem bloquear o event loop."""
    method = method.upper()
    host = _host(url)
    attempts = _MAX_ATTEMPTS if method in {"GET", "HEAD"} else 1
    for attempt in range(1, attempts + 1):
        await _await_host(host)
        try:
            # O endpoint assinado do Qobuz historicamente usa .request() no projeto;
            # manter essa interface também preserva clientes e mocks compatíveis.
            if "track/lyricsUrl" in url:
                response = await client.request(method.lower(), url, **kwargs)
            else:
                response = await getattr(client, method.lower())(url, **kwargs)
            status = getattr(response, "status_code", 200)
            retryable = isinstance(status, int) and (
                status in {408, 425, 429} or status >= 500
            )
            if not retryable:
                return response
            if attempt == attempts:
                logger.warning(
                    "HTTP %s %s continuou retornando %s após %d tentativas",
                    method,
                    host,
                    response.status_code,
                    attempts,
                )
                return response
            delay = _delay(attempt, _retry_after(response.headers))
            await response.aclose()
            shared_cooldown = response.status_code in {429, 503}
            if shared_cooldown:
                _publish_cooldown(host, delay)
            logger.debug(
                "HTTP %s %s retornou %s; retry %d/%d em %.2fs",
                method,
                host,
                response.status_code,
                attempt,
                attempts,
                delay,
            )
            if not shared_cooldown:
                await asyncio.sleep(delay)
        except httpx.TransportError:
            if attempt == attempts:
                raise
            await asyncio.sleep(_delay(attempt, None))
    raise RuntimeError("fluxo de retry HTTP inesperado")


def _reset_state_for_tests() -> None:
    """Limpa cooldowns compartilhados; uso exclusivo dos testes."""
    with _lock:
        _cooldowns.clear()


def bounded_worker_count(value, *, maximum: int = 16) -> int:
    """Normaliza max_workers sem o antigo teto artificial de quatro letras."""
    try:
        workers = int(value or 1)
    except (TypeError, ValueError):
        workers = 1
    return max(1, min(workers, max(1, int(maximum))))
