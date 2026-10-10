# ============================================================================
# http_download.py -- download de arquivo por HTTP, retomável e verificado.
#
# Substitui o miolo de `downloader.tqdm_download` para o caminho de download
# direto (capas, PDFs, áudio). NÃO mexe no caminho segmentado/criptografado.
#
# PROBLEMAS do código atual que este módulo resolve:
#
#  1. Resposta que termina ANTES do tamanho esperado, sem lançar exceção:
#     o laço de retentativas considera "sucesso" (nenhuma exceção), sai, e só
#     depois lança "Download Incompleto" FORA do retry -> o arquivo é apagado
#     e nada é retomado. Aqui o tamanho é conferido dentro de cada tentativa
#     e a tentativa seguinte retoma de onde parou (Range).
#  2. Sem `content-length` (total = 0), `downloaded >= total` é sempre
#     verdadeiro: até um arquivo VAZIO era dado como concluído. Aqui resposta
#     vazia é erro, e o resultado informa se o tamanho pôde ser verificado.
#  3. Gravação direta no nome final: um kill no meio deixa arquivo parcial que
#     depois é pulado como "já existe". Aqui escreve em "<destino>.part" e só
#     promove (os.replace + fsync) quando o conteúdo está completo.
#  4. Retomar sobre um parcial de OUTRA origem (ex.: outro tier de qualidade
#     que reutiliza o mesmo nome temporário) emenda conteúdos diferentes.
#     Aqui nenhum parcial é reaproveitado entre chamadas, e dentro da mesma
#     chamada a retomada é validada (Content-Range, tamanho total, If-Range).
#  5. Escrita de disco síncrona no event loop: um disco lento travava os
#     outros downloads paralelos. Aqui toda escrita vai para um executor.
#
# O módulo não imprime nada e não conhece tqdm: o progresso sai por callback
# (`on_progress`), o que também o deixa utilizável pela GUI/--progress-json.
# ============================================================================
from __future__ import annotations

import asyncio
import importlib.util
import inspect
import logging
import os
import random
import re
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Mapping, Optional, Union

import httpx

from qobuz_dl import fileio

logger = logging.getLogger(__name__)

DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB
DEFAULT_TIMEOUT = httpx.Timeout(60.0, connect=10.0)

# Aceita str, função que devolve str (URL "fresca" a cada tentativa, útil
# para links assinados que expiram) ou corrotina que devolve str.
UrlSource = Union[str, Callable[[], Any], Callable[[], Awaitable[str]]]

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "ChecksumMismatchError",
    "DownloadAborted",
    "DownloadError",
    "DownloadResult",
    "PermanentDownloadError",
    "SizeMismatchError",
    "download_file",
    "make_client",
    "make_sync_client",
]


# ----------------------------------------------------------------------------
# Erros
# ----------------------------------------------------------------------------


class DownloadError(Exception):
    """Falha de download (tentativas esgotadas ou erro não retentável)."""


class PermanentDownloadError(DownloadError):
    """Não adianta tentar de novo: 401/403/404/451 e demais 4xx definitivos.

    Em ``downloader.py`` use ``_PermanentDownloadError = PermanentDownloadError``
    para manter os ``except`` existentes funcionando.
    """


class SizeMismatchError(DownloadError):
    """O tamanho informado pelo servidor difere do tamanho esperado."""


class ChecksumMismatchError(DownloadError):
    """O SHA-256 do arquivo baixado difere do esperado."""


class DownloadAborted(DownloadError):
    """``should_abort()`` pediu para interromper (ex.: Ctrl+C)."""


class _Retry(Exception):
    """Interno: esta tentativa falhou, mas outra pode dar certo."""

    def __init__(self, reason: str, *, retry_after: float | None = None):
        super().__init__(reason)
        self.retry_after = retry_after


# ----------------------------------------------------------------------------
# Resultado e estado
# ----------------------------------------------------------------------------


@dataclass(frozen=True)
class DownloadResult:
    path: str
    size: int
    attempts: int
    # True quando o tamanho final foi comparado com um total conhecido
    # (Content-Range/Content-Length/expected_size). False = o transporte não
    # tem como garantir que o arquivo está inteiro; rode uma verificação de
    # áudio (ex.: verify_audio_integrity) antes de confiar nele.
    size_verified: bool
    sha256: str | None = None


@dataclass
class _State:
    """O que foi observado nas tentativas anteriores desta MESMA chamada."""

    total: int | None = None
    validator: str | None = None  # ETag forte ou Last-Modified (If-Range)

    def reset(self) -> None:
        self.total = None
        self.validator = None


# ----------------------------------------------------------------------------
# Cliente HTTP compartilhado
# ----------------------------------------------------------------------------


def _create_compatible_client(client_class, kwargs: dict[str, Any]):
    """Cria clientes HTTP respeitando fábricas substituídas em testes.

    httpx aceita estes parâmetros, mas alguns testes/integradores substituem
    AsyncClient/Client por fábricas simples com assinatura reduzida. Filtrar
    somente nesses casos mantém a configuração completa no httpx real e evita
    quebrar essas fábricas compatíveis.
    """
    try:
        signature = inspect.signature(client_class)
    except (TypeError, ValueError):
        return client_class(**kwargs)
    parameters = signature.parameters.values()
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
        return client_class(**kwargs)
    accepted = signature.parameters
    filtered = {key: value for key, value in kwargs.items() if key in accepted}
    return client_class(**filtered)


def make_client(
    *,
    http2: bool | None = None,
    max_connections: int = 32,
    max_keepalive_connections: int = 16,
    keepalive_expiry: float = 30.0,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Cria UM ``AsyncClient`` para ser reutilizado por todo o programa.

    Hoje cada ``Download`` cria (e fecha) o próprio cliente, então cada
    álbum refaz DNS/TLS. Um cliente único mantém as conexões vivas entre
    faixas e álbuns. ``http2=None`` ativa HTTP/2 só se o pacote ``h2`` estiver
    instalado (``pip install "httpx[http2]"``); sem ele cai em HTTP/1.1.
    """
    h2_disponivel = importlib.util.find_spec("h2") is not None
    if http2 is None:
        http2 = h2_disponivel
    elif http2 and not h2_disponivel:
        logger.debug(
            "http2 pedido mas o pacote 'h2' não está instalado; usando HTTP/1.1"
        )
        http2 = False

    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        keepalive_expiry=keepalive_expiry,
    )
    return _create_compatible_client(
        httpx.AsyncClient,
        {
            "http2": http2,
            "limits": limits,
            "timeout": timeout or DEFAULT_TIMEOUT,
            "headers": dict(headers) if headers else None,
            "follow_redirects": follow_redirects,
        },
    )


def make_sync_client(
    *,
    http2: bool | None = None,
    max_connections: int = 32,
    max_keepalive_connections: int = 16,
    keepalive_expiry: float = 30.0,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | float | None = None,
    follow_redirects: bool = True,
) -> httpx.Client:
    """Cria um cliente HTTP síncrono com a mesma política de HTTP/2.

    Mantém HTTP/1.1 como fallback automático quando o extra `httpx[http2]`
    não estiver instalado. Use um cliente por operação/sessão e feche-o ao fim.
    """
    h2_disponivel = importlib.util.find_spec("h2") is not None
    if http2 is None:
        http2 = h2_disponivel
    elif http2 and not h2_disponivel:
        logger.debug(
            "http2 pedido mas o pacote 'h2' não está instalado; usando HTTP/1.1"
        )
        http2 = False

    limits = httpx.Limits(
        max_connections=max_connections,
        max_keepalive_connections=max_keepalive_connections,
        keepalive_expiry=keepalive_expiry,
    )
    return _create_compatible_client(
        httpx.Client,
        {
            "http2": http2,
            "limits": limits,
            "timeout": timeout if timeout is not None else 30.0,
            "headers": dict(headers) if headers else None,
            "follow_redirects": follow_redirects,
        },
    )


# ----------------------------------------------------------------------------
# Auxiliares de HTTP
# ----------------------------------------------------------------------------

_RANGE_RE = re.compile(r"^\s*bytes\s+(\d+)-(\d+)/(\d+|\*)\s*$", re.IGNORECASE)
_UNSATISFIED_RE = re.compile(r"^\s*bytes\s+\*/(\d+)\s*$", re.IGNORECASE)


def _parse_content_range(value: str | None) -> tuple[int, int, int | None] | None:
    """``bytes 100-199/1000`` -> (100, 199, 1000); total ``*`` -> None."""
    match = _RANGE_RE.match(value or "")
    if not match:
        return None
    total = None if match.group(3) == "*" else int(match.group(3))
    return int(match.group(1)), int(match.group(2)), total


def _parse_unsatisfied_total(value: str | None) -> int | None:
    match = _UNSATISFIED_RE.match(value or "")
    return int(match.group(1)) if match else None


def _int_header(headers: Mapping[str, str], name: str) -> int | None:
    try:
        value = int(headers.get(name, ""))
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _retry_after(headers: Mapping[str, str]) -> float | None:
    try:
        seconds = float(headers.get("retry-after", ""))
    except (TypeError, ValueError):
        return None  # data HTTP: ignora e usa o backoff normal
    return min(max(seconds, 0.0), 120.0)


def _check_status(response: Any) -> None:
    """Converte status != 200/206 em erro retentável ou permanente."""
    status = response.status_code
    if status in (200, 206):
        return
    if status in (408, 425, 429) or status >= 500:
        raise _Retry(f"HTTP {status}", retry_after=_retry_after(response.headers))
    if status == 404:
        raise PermanentDownloadError("HTTP 404: arquivo não encontrado no servidor.")
    if status in (401, 403, 451):
        raise PermanentDownloadError(
            f"HTTP {status}: faixa indisponível (bloqueio de região, direitos "
            "autorais ou sessão expirada)."
        )
    raise PermanentDownloadError(f"HTTP {status}: resposta inesperada do servidor.")


def _check_abort(should_abort: Callable[[], bool] | None) -> None:
    if should_abort is not None and should_abort():
        raise DownloadAborted("download interrompido")


def _backoff(attempt: int, base: float, cap: float) -> float:
    if base <= 0:
        return 0.0
    delay = min(cap, base * (2 ** (attempt - 1)))
    return delay + random.uniform(0, delay * 0.25)


# ----------------------------------------------------------------------------
# Download
# ----------------------------------------------------------------------------


async def download_file(
    client: httpx.AsyncClient,
    url: UrlSource,
    dest: Union[str, "os.PathLike[str]"],
    *,
    expected_size: int | None = None,
    expected_sha256: str | None = None,
    compute_sha256: bool = False,
    headers: Mapping[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    max_attempts: int = 5,
    backoff_base: float = 2.0,
    backoff_max: float = 32.0,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    on_progress: Optional[Callable[[int, Optional[int]], None]] = None,
    should_abort: Optional[Callable[[], bool]] = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> DownloadResult:
    """Baixa ``url`` para ``dest`` (escrita atômica via ``dest + ".part"``).

    - ``url`` pode ser uma função/corrotina: ela é chamada a CADA tentativa,
      para obter uma URL assinada nova se a anterior expirou.
    - ``on_progress(baixado, total_ou_None)`` recebe o valor ABSOLUTO já
      baixado (inclusive após retomadas/reinícios), então basta o chamador
      fazer ``bar.update(baixado - bar.n)``.
    - ``expected_size``: tamanho conhecido de outra fonte (ex.: metadados).
      Se o servidor informar outro total, lança ``SizeMismatchError``.
    - Em qualquer falha final o ``.part`` é apagado; ``dest`` só passa a
      existir com o conteúdo completo.
    - Erros de disco (ENOSPC, permissão) NÃO são retentados: sobem direto.

    Levanta ``PermanentDownloadError``, ``SizeMismatchError``,
    ``ChecksumMismatchError``, ``DownloadAborted`` ou ``DownloadError``
    (tentativas esgotadas, com a última causa na mensagem).
    """
    if max_attempts < 1:
        raise ValueError("max_attempts deve ser >= 1")

    dest = os.fspath(dest)
    part = fileio.part_path(dest)
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)

    # Nunca retoma um parcial de uma chamada anterior: o nome temporário pode
    # ter sido usado por outra faixa/qualidade e emendar conteúdos distintos.
    fileio.safe_remove(part)

    state = _State()
    last_reason = ""
    try:
        for attempt in range(1, max_attempts + 1):
            _check_abort(should_abort)
            retry_after: float | None = None
            try:
                result = await _attempt(
                    client,
                    url,
                    dest,
                    part,
                    state,
                    expected_size=expected_size,
                    expected_sha256=expected_sha256,
                    compute_sha256=compute_sha256,
                    headers=headers,
                    timeout=timeout,
                    chunk_size=chunk_size,
                    on_progress=on_progress,
                    should_abort=should_abort,
                )
                return replace(result, attempts=attempt)
            except _Retry as exc:
                last_reason, retry_after = str(exc), exc.retry_after
            except httpx.TransportError as exc:
                last_reason = f"{type(exc).__name__}: {exc}"

            if attempt == max_attempts:
                break
            delay = (
                retry_after
                if retry_after is not None
                else _backoff(attempt, backoff_base, backoff_max)
            )
            logger.debug(
                "download %s: tentativa %d/%d falhou (%s); nova tentativa em %.1fs",
                dest,
                attempt,
                max_attempts,
                last_reason,
                delay,
            )
            await sleep(delay)

        raise DownloadError(
            f"Falha após {max_attempts} tentativas. Último erro: {last_reason}"
        )
    except BaseException:
        fileio.safe_remove(part)
        raise


async def _attempt(
    client: httpx.AsyncClient,
    url_source: UrlSource,
    dest: str,
    part: str,
    state: _State,
    *,
    expected_size: int | None,
    expected_sha256: str | None,
    compute_sha256: bool,
    headers: Mapping[str, str] | None,
    timeout: httpx.Timeout | None,
    chunk_size: int,
    on_progress: Optional[Callable[[int, Optional[int]], None]],
    should_abort: Optional[Callable[[], bool]],
) -> DownloadResult:
    """Uma tentativa: retoma de ``part`` se existir e valida tudo."""
    loop = asyncio.get_running_loop()

    url = url_source() if callable(url_source) else url_source
    if inspect.isawaitable(url):
        url = await url

    offset = os.path.getsize(part) if os.path.exists(part) else 0

    req_headers = dict(headers or {})
    # Com Content-Length conferido, o corpo precisa vir sem compressão.
    req_headers.setdefault("Accept-Encoding", "identity")
    req_headers["Range"] = f"bytes={offset}-"
    if offset and state.validator:
        # Se o arquivo mudou no servidor, ele responde 200 com o conteúdo
        # inteiro (e não 206), e abaixo recomeçamos do zero.
        req_headers["If-Range"] = state.validator

    already_complete = False
    written = 0
    target: int | None = None
    verified = False

    async with client.stream(
        "GET", url, headers=req_headers, timeout=timeout or DEFAULT_TIMEOUT
    ) as response:
        status = response.status_code

        if status == 416:
            total = _parse_unsatisfied_total(response.headers.get("content-range"))
            if offset and total is not None and offset == total:
                already_complete = True  # o parcial já é o arquivo inteiro
                target, written, verified = total, offset, True
            else:
                fileio.safe_remove(part)
                state.reset()
                raise _Retry("HTTP 416 com parcial incompatível; recomeçando do zero")
        else:
            _check_status(response)

            if status == 206:
                crange = _parse_content_range(response.headers.get("content-range"))
                if crange is None or crange[0] != offset:
                    fileio.safe_remove(part)
                    state.reset()
                    raise _Retry("Content-Range incompatível com o parcial local")
                total = crange[2]
                if total is None:
                    length = _int_header(response.headers, "content-length")
                    total = offset + length if length is not None else None
                mode = "ab" if offset else "wb"
            else:
                # 200: o servidor ignorou o Range (ou o arquivo mudou).
                total = _int_header(response.headers, "content-length")
                offset = 0
                mode = "wb"

            encoding = response.headers.get("content-encoding", "identity").lower()
            if encoding not in ("", "identity"):
                total = None  # Content-Length descreve o corpo comprimido

            if total is not None:
                if state.total is not None and state.total != total:
                    fileio.safe_remove(part)
                    state.reset()
                    raise _Retry("tamanho total mudou entre tentativas")
                if expected_size is not None and expected_size != total:
                    raise SizeMismatchError(
                        f"Servidor informa {total} bytes, esperado {expected_size}."
                    )
                state.total = total
            target = total if total is not None else expected_size
            verified = target is not None

            if state.validator is None:
                etag = response.headers.get("etag", "")
                last_modified = response.headers.get("last-modified", "")
                if etag and not etag.startswith("W/"):
                    state.validator = etag
                elif last_modified:
                    state.validator = last_modified

            written = offset
            overrun = False
            with open(part, mode) as fh:
                async for chunk in response.aiter_bytes(chunk_size):
                    _check_abort(should_abort)
                    if not chunk:
                        continue
                    await loop.run_in_executor(None, fh.write, chunk)
                    written += len(chunk)
                    if target is not None and written > target:
                        overrun = True
                        break
                    if on_progress is not None:
                        on_progress(written, target)
                await loop.run_in_executor(None, _flush_and_fsync, fh)

            if overrun:
                fileio.safe_remove(part)
                state.reset()
                raise _Retry("recebeu mais bytes do que o tamanho esperado")
            if written == 0:
                raise _Retry("resposta vazia")
            if target is not None and written < target:
                # O parcial é mantido: a próxima tentativa retoma por Range.
                raise _Retry(f"download incompleto ({written}/{target} bytes)")

    digest = await loop.run_in_executor(
        None, _finalize, part, dest, expected_sha256, compute_sha256
    )
    if already_complete and on_progress is not None:
        on_progress(written, target)
    return DownloadResult(
        path=dest, size=written, attempts=0, size_verified=verified, sha256=digest
    )


def _flush_and_fsync(fh: Any) -> None:
    fh.flush()
    os.fsync(fh.fileno())


def _finalize(
    part: str, dest: str, expected_sha256: str | None, compute_sha256: bool
) -> str | None:
    """Confere o checksum (se pedido) e promove ``part`` -> ``dest``."""
    digest = None
    if expected_sha256 or compute_sha256:
        digest = fileio.sha256_file(part)
        if expected_sha256 and digest != expected_sha256.strip().lower():
            raise ChecksumMismatchError(
                f"SHA-256 difere do esperado (obtido {digest[:12]}…)."
            )
    fileio.replace_atomic(part, dest)
    fileio.fsync_dir(os.path.dirname(os.path.abspath(dest)))
    return digest
