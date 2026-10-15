"""Progresso do comando `tags`: retomar execução interrompida e rodar só o que mudou.

O `tags` guarda, por pasta e por modo (tags / capas / ambos), a "assinatura"
(data de modificação + tamanho) de cada arquivo que já terminou de processar.
Com isso:

* ``--resume``: se a última execução foi interrompida (CTRL+C, queda de rede,
  bateria), continua de onde parou, pulando o que já estava pronto.
* ``--incremental``: pula tudo que já foi processado e continua igual desde
  então (só pega arquivos novos ou alterados por outro programa).

O estado fica em ``<pasta do config>/tags_progress.json``. Se o arquivo sumir ou
estiver corrompido, nada quebra: tudo é processado de novo.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from typing import Optional

logger = logging.getLogger(__name__)

FILE_NAME = "tags_progress.json"
VERSION = 1
# Grava o estado a cada N arquivos (e sempre no fim), para que uma queda
# repentina perca pouco trabalho sem regravar o JSON a cada faixa.
SAVE_EVERY = 25

# Resultados transitórios: o arquivo não é marcado e será tentado de novo.
_RETRY_OUTCOMES = frozenset({"download_failed", "embed_failed", "error"})


def _signature(path: str) -> Optional[list]:
    try:
        info = os.stat(path)
    except OSError:
        return None
    return [info.st_mtime_ns, info.st_size]


def _atomic_write(path: str, text: str) -> None:
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".~tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


class Progress:
    """Estado de uma pasta + modo. Crie com :meth:`open`."""

    def __init__(self, state_path: str, root: str, mode: str):
        self.state_path = state_path
        self.root = os.path.abspath(root)
        self.mode = mode
        self.files: dict = {}
        self.complete = True  # sem estado anterior = nada pendente
        self.known = False  # já havia estado guardado para esta pasta/modo
        self._dirty = 0
        self._skip_enabled = False

    # -- leitura -----------------------------------------------------------
    @classmethod
    def open(cls, base_dir: str, root: str, mode: str) -> "Progress":
        progress = cls(os.path.join(base_dir, FILE_NAME), root, mode)
        entry = progress._load().get("roots", {}).get(progress.root, {}).get(mode)
        if isinstance(entry, dict) and isinstance(entry.get("files"), dict):
            progress.files = {
                key: value
                for key, value in entry["files"].items()
                if isinstance(value, list) and len(value) == 2
            }
            progress.complete = bool(entry.get("complete", False))
            progress.known = True
        return progress

    def _load(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict) or data.get("version") != VERSION:
            return {}
        return data

    # -- consulta ----------------------------------------------------------
    def _key(self, path: str) -> str:
        try:
            return os.path.relpath(os.path.abspath(path), self.root)
        except ValueError:  # outro drive no Windows
            return os.path.abspath(path)

    @property
    def interrupted(self) -> bool:
        """True se a última execução desta pasta/modo não chegou ao fim."""
        return self.known and not self.complete

    def enable_skip(self) -> None:
        self._skip_enabled = True

    def is_done(self, path: str) -> bool:
        if not self._skip_enabled:
            return False
        saved = self.files.get(self._key(path))
        return saved is not None and saved == _signature(path)

    # -- gravação ----------------------------------------------------------
    def start(self, keep_done: bool) -> None:
        """Começa uma execução: marca como não concluída.

        keep_done=False (execução completa) zera a lista, para que um
        ``--resume`` depois só considere o que ESTA execução terminou.
        """
        if not keep_done:
            self.files = {}
        self.complete = False
        self.known = True
        self.save()

    def mark(self, path: str, outcome: Optional[str] = None) -> None:
        """Marca o arquivo como pronto (a menos que o resultado seja transitório)."""
        if outcome in _RETRY_OUTCOMES:
            return
        signature = _signature(path)
        if signature is None:
            return
        self.files[self._key(path)] = signature
        self._dirty += 1
        if self._dirty >= SAVE_EVERY:
            self.save()

    def finish(self) -> None:
        self.complete = True
        self.save()

    def save(self) -> None:
        data = self._load() or {"version": VERSION, "roots": {}}
        roots = data.setdefault("roots", {})
        roots.setdefault(self.root, {})[self.mode] = {
            "complete": self.complete,
            "updated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": self.files,
        }
        try:
            _atomic_write(self.state_path, json.dumps(data, ensure_ascii=False))
            self._dirty = 0
        except OSError as exc:
            logger.warning("Não foi possível salvar o progresso do tags: %s", exc)
