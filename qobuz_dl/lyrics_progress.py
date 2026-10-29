"""Progresso do comando `lyrics` por pasta: base do --resume e do --incremental.

Para cada arquivo processado guarda (assinatura, resultado). A assinatura é
mtime+tamanho DEPOIS de gravar a letra: se o arquivo mudar depois (ou for
trocado), ele volta a ser processado. Resultados ADIADO (o Qobuz recusou) e
FALHA valem nova tentativa mesmo já registrados.

Arquivo: <config>/lyrics_progress/<hash da pasta>.json. Ele é só uma conveniência:
se não puder ser lido ou gravado, o `lyrics` funciona do mesmo jeito.
"""

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Optional

logger = logging.getLogger(__name__)

PROGRESS_DIRNAME = "lyrics_progress"
RETRY_STATUSES = ("ADIADO", "FALHA")
_SAVE_EVERY = 50
_SAVE_SECONDS = 3.0


def _signature(path: str) -> Optional[str]:
    """mtime+tamanho: muda quando o arquivo é regravado ou trocado."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _progress_path(config_dir: str, root: str) -> str:
    digest = hashlib.sha256(os.path.abspath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(config_dir, PROGRESS_DIRNAME, digest + ".json")


def _atomic_write_json(path: str, payload: dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".~tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            # Limpeza best-effort: o arquivo pode já ter sumido.
            pass
        raise


class LyricsProgress:
    """Progresso de uma pasta (use ``LyricsProgress.open``)."""

    def __init__(self, path: str, root: str, data: dict[str, Any]) -> None:
        self.path = path
        self.root = os.path.abspath(root)
        files = data.get("files")
        self._files: dict[str, dict[str, str]] = (
            files if isinstance(files, dict) else {}
        )
        self._finished: bool = bool(data.get("finished", True))
        # A execução anterior começou e não chegou ao fim (CTRL+C, queda, erro).
        self.interrupted: bool = bool(data) and not self._finished
        self.skipping: bool = False
        self._dirty = False
        self._since_save = 0
        self._last_save = time.monotonic()
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()

    @classmethod
    def open(cls, config_dir: str, root: str) -> "LyricsProgress":
        path = _progress_path(config_dir, root)
        data: dict[str, Any] = {}
        try:
            with open(path, encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            logger.debug("Sem progresso anterior do lyrics em %s", path)
        return cls(path, root, data)

    def _rel(self, path: str) -> str:
        return os.path.relpath(os.path.abspath(path), self.root)

    def enable_skip(self) -> None:
        """Passa a pular o que já foi feito (--resume / --incremental)."""
        self.skipping = True

    def pending_count(self) -> int:
        """Quantas faixas ficaram ADIADAS ou com FALHA na execução anterior."""
        return sum(
            1 for item in self._files.values() if item.get("status") in RETRY_STATUSES
        )

    def status_of(self, path: str) -> Optional[str]:
        item = self._files.get(self._rel(path))
        return item.get("status") if item else None

    def should_skip(self, path: str) -> bool:
        item = self._files.get(self._rel(path))
        if not item or item.get("status") in RETRY_STATUSES:
            return False
        signature = _signature(path)
        return signature is not None and signature == item.get("sig")

    def filter_pending(self, files: list[str]) -> tuple[list[str], int]:
        """(arquivos a processar, quantos foram pulados). Sem skip, devolve tudo."""
        if not self.skipping:
            return list(files), 0
        pending = [path for path in files if not self.should_skip(path)]
        return pending, len(files) - len(pending)

    def record(self, path: str, status: str) -> None:
        """Anota o resultado do arquivo (chame DEPOIS de gravar a letra)."""
        signature = _signature(path)
        if signature is None:
            return
        with self._lock:
            self._files[self._rel(path)] = {"sig": signature, "status": status}
            self._finished = False
            self._dirty = True
            self._since_save += 1
            due = (
                self._since_save >= _SAVE_EVERY
                or time.monotonic() - self._last_save >= _SAVE_SECONDS
            )
        if due:
            self.save()

    def finish(self) -> None:
        """Marca a execução como concluída (não é mais 'interrompida')."""
        with self._lock:
            self._finished = True
            self._dirty = True
        self.save()

    def save(self) -> None:
        with self._io_lock:
            with self._lock:
                if not self._dirty:
                    return
                payload: dict[str, Any] = {
                    "root": self.root,
                    "finished": self._finished,
                    "files": {k: dict(v) for k, v in self._files.items()},
                }
                self._dirty = False
                self._since_save = 0
                self._last_save = time.monotonic()
            try:
                _atomic_write_json(self.path, payload)
            except OSError as exc:
                logger.debug("Não gravou o progresso do lyrics: %s", exc)
                with self._lock:
                    self._dirty = True
