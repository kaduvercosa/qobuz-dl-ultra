"""Registro para DESFAZER o comando `lyrics` (letra embutida e arquivos .lrc).

Reaproveita o `tags_journal` para o BLOCO DE METADADOS do áudio (onde fica a letra
embutida) e acrescenta, na mesma pasta da execução, o `lrc.jsonl`: para cada
.lrc criado ou alterado, guarda o conteúdo anterior (ou "não existia").

Layout em <config>/lyrics/tags_undo/<AAAAMMDD_HHMMSS>/:
  meta.json, journal.jsonl, blobs/  -- do tags_journal (metadados do áudio)
  lrc.jsonl                         -- um registro por .lrc alterado

Seguro em paralelo: ``snapshot`` e ``record`` não guardam estado entre as chamadas.
"""

import base64
import hashlib
import json
import logging
import os
import shutil
import tempfile
import threading
import zlib
from dataclasses import dataclass
from typing import Any, Callable, Optional

from qobuz_dl import tags_journal

logger = logging.getLogger(__name__)

LYRICS_SUBDIR = "lyrics"
LRC_LOG = "lrc.jsonl"

_Lrc = tuple[bool, Optional[bytes]]  # (existe, conteúdo)


def _base(config_dir: str) -> str:
    return os.path.join(config_dir, LYRICS_SUBDIR)


def _lrc_path(audio_path: str) -> str:
    return os.path.splitext(audio_path)[0] + ".lrc"


def _read_lrc(lrc_path: str) -> Optional[_Lrc]:
    """(existe, conteúdo); None quando não deu para ler (não registrar às cegas)."""
    try:
        with open(lrc_path, "rb") as handle:
            return True, handle.read()
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        logger.debug("Não foi possível ler %s: %s", lrc_path, exc)
        return None


def _pack(data: bytes) -> str:
    return base64.b64encode(zlib.compress(data)).decode("ascii")


def _unpack(text: str) -> bytes:
    return zlib.decompress(base64.b64decode(text))


def _sha(data: Optional[bytes]) -> str:
    return hashlib.sha256(data).hexdigest() if data is not None else ""


def _write_bytes_atomic(path: str, data: bytes) -> None:
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".~undo_")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        try:
            shutil.copymode(path, tmp)
        except OSError:
            # O arquivo atual pode ter sumido: fica com a permissão padrão.
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            # Limpeza best-effort: o arquivo pode já ter sumido.
            pass
        raise


@dataclass
class Snapshot:
    """Estado de um arquivo de áudio (e do seu .lrc) ANTES de gravar a letra."""

    head: Optional[tags_journal.Head]
    lrc: Optional[_Lrc]


class LyricsJournal:
    """Registro de uma execução do `lyrics` (use ``LyricsJournal.create``)."""

    def __init__(self, journal: tags_journal.Journal) -> None:
        self.journal = journal
        self.lrc_recorded = 0
        self._lock = threading.Lock()

    @classmethod
    def create(
        cls, config_dir: str, root: str, keep: int = tags_journal.KEEP_RUNS
    ) -> "LyricsJournal":
        base = _base(config_dir)
        os.makedirs(base, exist_ok=True)
        return cls(tags_journal.Journal.create(base, "lyrics", root, keep=keep))

    @property
    def run_id(self) -> str:
        return self.journal.run_id

    @property
    def recorded(self) -> int:
        """Arquivos de áudio + .lrc que a execução alterou."""
        return self.journal.recorded + self.lrc_recorded

    def snapshot(self, audio_path: str) -> Snapshot:
        """Guarda o estado de ``audio_path`` e do .lrc dele, antes de gravar."""
        return Snapshot(
            self.journal.snapshot(audio_path), _read_lrc(_lrc_path(audio_path))
        )

    def record(self, audio_path: str, snap: Snapshot) -> bool:
        """Depois de gravar: registra o que mudou. True se algo foi alterado."""
        changed = False
        if snap.head is not None:
            changed = self.journal.record_change(audio_path, snap.head)
        if snap.lrc is not None:
            lrc_path = _lrc_path(audio_path)
            after = _read_lrc(lrc_path)
            if after is not None and after != snap.lrc:
                self._commit_lrc(lrc_path, snap.lrc, after)
                changed = True
        return changed

    def _commit_lrc(self, lrc_path: str, before: _Lrc, after: _Lrc) -> None:
        existed, before_data = before
        entry = {
            "path": os.path.abspath(lrc_path),
            "existed": existed,
            "before": _pack(before_data) if before_data is not None else None,
            "after_hash": _sha(after[1]),
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            with open(
                os.path.join(self.journal.run_dir, LRC_LOG), "a", encoding="utf-8"
            ) as handle:
                handle.write(line)
            self.lrc_recorded += 1


def _read_entries(run_dir: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    try:
        with open(os.path.join(run_dir, LRC_LOG), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    entries.append(json.loads(line))
    except FileNotFoundError:
        # Execução sem nenhum .lrc alterado.
        pass
    except (OSError, ValueError):
        logger.warning(
            "Não foi possível ler o %s de %s", LRC_LOG, run_dir, exc_info=True
        )
    return entries


def list_runs(config_dir: str) -> list[dict[str, Any]]:
    """Execuções guardadas, da mais recente à mais antiga (com a contagem de .lrc)."""
    base = _base(config_dir)
    runs: list[dict[str, Any]] = tags_journal.list_runs(base)
    for run in runs:
        run_dir = os.path.join(base, tags_journal.JOURNAL_DIRNAME, run["id"])
        run["lrc"] = len(_read_entries(run_dir))
    return runs


def undo(
    config_dir: str,
    run_id: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    on_file: Optional[Callable[[str, str], None]] = None,
) -> Optional[dict[str, Any]]:
    """Restaura o estado anterior de uma execução do `lyrics`.

    run_id=None usa a mais recente. Cada arquivo só é restaurado se ainda estiver
    como o `lyrics` o deixou, a menos que ``force``. Devolve contadores (os do
    áudio, vindos do tags_journal, mais ``lrc_*``) ou None se a execução não
    existir. ``on_file(status, caminho)`` recebe cada resultado.
    """
    base = _base(config_dir)
    result = tags_journal.undo_run(
        base, run_id=run_id, force=force, dry_run=dry_run, on_file=on_file
    )
    if result is None:
        return None
    run_dir = os.path.join(base, tags_journal.JOURNAL_DIRNAME, result["run"])
    counts = {
        "lrc_restored": 0,
        "lrc_would_restore": 0,
        "lrc_changed_since": 0,
        "lrc_missing": 0,
        "lrc_errors": 0,
    }

    def report(status: str, path: str) -> None:
        if on_file:
            on_file(status, path)

    for entry in reversed(_read_entries(run_dir)):
        path = entry["path"]
        try:
            current = _read_lrc(path)
            if current is None:
                counts["lrc_errors"] += 1
                report("error", path)
                continue
            exists, data = current
            if not exists:
                if entry["existed"]:
                    counts["lrc_missing"] += 1
                    report("missing", path)
                continue  # já estava ausente antes: nada a fazer
            if _sha(data) != entry["after_hash"] and not force:
                counts["lrc_changed_since"] += 1
                report("changed_since", path)
                continue
            if dry_run:
                counts["lrc_would_restore"] += 1
                report("would_restore", path)
                continue
            if entry["existed"]:
                _write_bytes_atomic(path, _unpack(entry["before"] or _pack(b"")))
            else:
                os.remove(path)
            counts["lrc_restored"] += 1
            report("restored", path)
        except Exception as exc:  # noqa: BLE001 -- um arquivo ruim não para os outros
            logger.debug("Falha ao restaurar %s: %s", path, exc, exc_info=True)
            counts["lrc_errors"] += 1
            report("error", path)
    result.update(counts)
    return result
