# ============================================================================
# fileio.py -- escrita atômica de arquivos, checksums e limpeza de temporários.
#
# POR QUE existe: hoje vários pontos do projeto gravam direto no nome final
# (cover.jpg, PDFs de booklet, Tracklist.txt, collection_index.jsonl...) ou
# usam um ".tmp" de nome fixo sem fsync (postprocess._atomic_write_json).
# Se o processo for morto (Ctrl+C forte, queda de energia, SIGKILL) no meio
# da escrita, sobra um arquivo PARCIAL com o nome final -- e, como o
# downloader pula tudo que "já existe" (os.path.isfile), o arquivo corrompido
# nunca mais é refeito.
#
# Regra deste módulo: o nome final só passa a existir quando o conteúdo está
# COMPLETO e já foi forçado ao disco (fsync). Tudo é feito com a stdlib.
#
# Funções que bloqueiam (I/O de disco) devem ser chamadas fora do event loop
# em código async:  `await asyncio.to_thread(atomic_write_json, path, data)`.
# ============================================================================
from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import logging
import os
import time
import uuid
from typing import Any, Iterator, Union

logger = logging.getLogger(__name__)

PathLike = Union[str, "os.PathLike[str]"]

# Sufixo do arquivo parcial de downloads que podem ser RETOMADOS (nome
# determinístico: "<destino>.part"). Escritas atômicas curtas usam nome único.
PART_SUFFIX = ".part"
_ATOMIC_TMP_SUFFIX = ".atomic.tmp"
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB

# Padrões de temporários que ESTE projeto cria (usados por cleanup_stale_parts).
STALE_PATTERNS = (
    "*.part",
    "*.atomic.tmp",
    "~tmp_*.tmp",
    "~tmp_*.tmp.mp4",
)

__all__ = [
    "PART_SUFFIX",
    "STALE_PATTERNS",
    "append_line",
    "atomic_write_bytes",
    "atomic_write_json",
    "atomic_write_text",
    "atomic_writer",
    "cleanup_stale_parts",
    "file_digest",
    "fsync_dir",
    "part_path",
    "replace_atomic",
    "safe_remove",
    "sha256_file",
    "verify_digest",
]


# ----------------------------------------------------------------------------
# Utilitários pequenos
# ----------------------------------------------------------------------------


def part_path(dest: PathLike) -> str:
    """Caminho do arquivo parcial retomável de ``dest`` ("<dest>.part")."""
    return os.fspath(dest) + PART_SUFFIX


def safe_remove(path: PathLike) -> bool:
    """Remove ``path`` sem levantar erro. Devolve True se removeu algo."""
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.debug("Não foi possível remover %s: %s", path, exc)
        return False


def fsync_dir(directory: PathLike) -> None:
    """fsync best-effort do diretório, para o rename sobreviver a uma queda.

    Não faz nada no Windows (não é possível abrir diretórios com os.open).
    """
    if os.name == "nt":
        return
    try:
        fd = os.open(os.fspath(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def replace_atomic(
    src: PathLike, dest: PathLike, *, retries: int = 5, delay: float = 0.05
) -> None:
    """``os.replace`` com algumas tentativas no Windows.

    No Windows o destino pode estar momentaneamente travado por antivírus ou
    indexador (PermissionError). Em POSIX o erro é propagado de imediato.
    """
    for attempt in range(retries):
        try:
            os.replace(src, dest)
            return
        except PermissionError:
            if os.name != "nt" or attempt == retries - 1:
                raise
            time.sleep(delay * (attempt + 1))


def _unique_tmp(directory: str) -> str:
    # Nome curto e sem relação com o destino: evita estourar o limite de 255
    # bytes do nome do arquivo e colisão entre processos/threads.
    return os.path.join(
        directory, f".{os.getpid()}-{uuid.uuid4().hex}{_ATOMIC_TMP_SUFFIX}"
    )


# ----------------------------------------------------------------------------
# Escrita atômica
# ----------------------------------------------------------------------------


@contextlib.contextmanager
def atomic_writer(
    dest: PathLike,
    mode: str = "wb",
    *,
    encoding: str = "utf-8",
    fsync: bool = True,
) -> Iterator[Any]:
    """Abre um arquivo temporário e só o promove a ``dest`` se tudo deu certo.

    Uso::

        with atomic_writer("pasta/cover.jpg") as fh:
            fh.write(dados)

    Se o bloco levantar qualquer exceção (inclusive KeyboardInterrupt), o
    temporário é apagado e ``dest`` fica exatamente como estava antes.
    ``mode`` aceita apenas "wb" (padrão) ou "w". No modo texto as quebras de
    linha são sempre "\\n" (sem conversão para CRLF no Windows).
    """
    if mode not in ("wb", "w"):
        raise ValueError(f"mode deve ser 'wb' ou 'w', recebido {mode!r}")

    dest = os.fspath(dest)
    directory = os.path.dirname(os.path.abspath(dest))
    os.makedirs(directory, exist_ok=True)
    tmp = _unique_tmp(directory)

    open_kwargs: dict[str, Any] = (
        {} if mode == "wb" else {"encoding": encoding, "newline": "\n"}
    )
    try:
        with open(tmp, "x" + mode[1:], **open_kwargs) as fh:
            yield fh
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
        replace_atomic(tmp, dest)
    except BaseException:
        safe_remove(tmp)
        raise
    if fsync:
        fsync_dir(directory)


def atomic_write_bytes(dest: PathLike, data: bytes, *, fsync: bool = True) -> None:
    """Grava ``data`` em ``dest`` de forma atômica."""
    with atomic_writer(dest, "wb", fsync=fsync) as fh:
        fh.write(data)


def atomic_write_text(
    dest: PathLike, text: str, *, encoding: str = "utf-8", fsync: bool = True
) -> None:
    """Grava ``text`` em ``dest`` de forma atômica (quebras de linha LF)."""
    with atomic_writer(dest, "w", encoding=encoding, fsync=fsync) as fh:
        fh.write(text)


def atomic_write_json(
    dest: PathLike,
    data: Any,
    *,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    sort_keys: bool = False,
    default: Any = None,
    fsync: bool = True,
) -> None:
    """Serializa ``data`` e grava em ``dest`` de forma atômica.

    A serialização acontece ANTES de qualquer arquivo ser tocado: um objeto
    não serializável levanta TypeError sem deixar rastro em disco.
    """
    text = json.dumps(
        data,
        ensure_ascii=ensure_ascii,
        indent=indent,
        sort_keys=sort_keys,
        default=default,
    )
    atomic_write_text(dest, text + "\n", fsync=fsync)


def append_line(
    path: PathLike, line: str, *, encoding: str = "utf-8", fsync: bool = False
) -> None:
    """Acrescenta UMA linha a ``path`` com um único ``write`` em O_APPEND.

    Pensado para arquivos .jsonl: como a linha inteira vai numa só chamada de
    sistema, duas instâncias gravando ao mesmo tempo não misturam o conteúdo
    no meio de uma linha. A quebra de linha final é adicionada aqui.
    """
    path = os.fspath(path)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    data = (line.rstrip("\r\n") + "\n").encode(encoding)
    flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o666)
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        if fsync:
            os.fsync(fd)
    finally:
        os.close(fd)


# ----------------------------------------------------------------------------
# Checksums
# ----------------------------------------------------------------------------


def file_digest(
    path: PathLike,
    algorithm: str = "sha256",
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> str:
    """Hex digest do arquivo, lido em blocos (memória constante)."""
    h = hashlib.new(algorithm)
    buf = bytearray(chunk_size)
    view = memoryview(buf)
    with open(path, "rb") as fh:
        while True:
            n = fh.readinto(buf)
            if not n:
                break
            h.update(view[:n])
    return h.hexdigest()


def sha256_file(path: PathLike, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Atalho para ``file_digest(path, "sha256")``."""
    return file_digest(path, "sha256", chunk_size=chunk_size)


def verify_digest(
    path: PathLike, expected: str, algorithm: str = "sha256"
) -> bool:
    """True se o digest atual do arquivo bate com ``expected``.

    Arquivo ausente ou ilegível conta como "não bate" (False).
    """
    if not expected:
        return False
    try:
        return file_digest(path, algorithm) == expected.strip().lower()
    except OSError:
        return False


# ----------------------------------------------------------------------------
# Limpeza de temporários órfãos
# ----------------------------------------------------------------------------


def cleanup_stale_parts(
    root: PathLike,
    *,
    max_age_seconds: float = 24 * 3600,
    patterns: tuple[str, ...] = STALE_PATTERNS,
    recursive: bool = True,
    dry_run: bool = True,
) -> list[str]:
    """Encontra (e opcionalmente apaga) temporários abandonados sob ``root``.

    Só considera nomes que batem com ``patterns`` e que NÃO foram modificados
    nas últimas ``max_age_seconds`` (assim um download em andamento, nesta ou
    em outra instância, nunca é apagado). Links simbólicos são ignorados.

    ``dry_run=True`` (padrão) apenas lista; passe ``dry_run=False`` para
    apagar. Devolve os caminhos removidos (ou que seriam removidos).
    """
    cutoff = time.time() - max_age_seconds
    found: list[str] = []
    for dirpath, dirnames, filenames in os.walk(os.fspath(root)):
        if not recursive:
            dirnames[:] = []
        for name in filenames:
            if not any(fnmatch.fnmatchcase(name, pat) for pat in patterns):
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.islink(full) or os.stat(full).st_mtime > cutoff:
                    continue
            except OSError:
                continue
            if dry_run or safe_remove(full):
                found.append(full)
    return found
