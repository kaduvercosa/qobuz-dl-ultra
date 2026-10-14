# ============================================================================
# tags_journal.py -- registro para DESFAZER o comando `tags`.
# ============================================================================
# O `tags` reescreve muitos arquivos de uma vez. Para que uma regravação ruim
# (ou um bug de tag) não estrague a biblioteca sem volta, antes de gravar cada
# arquivo guardamos o BLOCO DE METADADOS original, byte a byte:
#
#   - FLAC: os blocos de metadados (STREAMINFO, VORBIS_COMMENT, PICTURE...),
#     tudo entre "fLaC" e o primeiro quadro de áudio;
#   - MP3 : o cabeçalho ID3v2 inteiro (quadros de texto e capa).
#
# O ÁUDIO em si nunca é copiado. Como o registro trabalha direto nos bytes do
# contêiner (Python puro, sem mutagen), a restauração é exata e não depende de
# como cada versão do mutagen interpreta as tags.
#
# Layout em <config>/tags_undo/<AAAAMMDD_HHMMSS>/:
#   meta.json     -- modo, pasta raiz e data da execução
#   journal.jsonl -- uma linha por arquivo REALMENTE alterado
#   blobs/        -- capas e blocos (sem repetição: o mesmo conteúdo é guardado
#                    uma vez só, o que importa para capas de álbum)
#   pending.json  -- estado do arquivo que está sendo gravado AGORA; se o
#                    programa for interrompido no meio da gravação, o
#                    `--undo` recupera esse arquivo também.
# ============================================================================

import base64
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
import zlib
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

JOURNAL_DIRNAME = "tags_undo"
KEEP_RUNS = 5
MAX_HEAD_BYTES = 256 * 1024 * 1024  # defesa: nunca guarda um "cabeçalho" absurdo
_FLAC_PICTURE = 6
_CHUNK = 1024 * 1024


# --------------------------------------------------------------------------
# Leitura do bloco de metadados (bytes crus)
# --------------------------------------------------------------------------
@dataclass
class Head:
    """Estado cru dos metadados de um arquivo, num instante."""

    kind: str  # "flac" | "mp3"
    data: bytes  # do início do arquivo até o fim dos metadados
    mtime_ns: int
    atime_ns: int

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.data).hexdigest()


def _syncsafe(raw: bytes) -> int:
    return (raw[0] << 21) | (raw[1] << 14) | (raw[2] << 7) | raw[3]


def read_head(path: str) -> Optional[Head]:
    """Lê o bloco de metadados cru de um FLAC ou MP3; None se não reconhecer."""
    try:
        stat = os.stat(path)
        with open(path, "rb") as handle:
            start = handle.read(10)
            if start[:4] == b"fLaC":
                handle.seek(4)
                parts = [b"fLaC"]
                total = 4
                while True:
                    header = handle.read(4)
                    if len(header) < 4:
                        return None
                    length = int.from_bytes(header[1:4], "big")
                    total += 4 + length
                    if total > MAX_HEAD_BYTES:
                        return None
                    body = handle.read(length)
                    if len(body) < length:
                        return None
                    parts += [header, body]
                    if header[0] & 0x80:
                        break
                return Head("flac", b"".join(parts), stat.st_mtime_ns, stat.st_atime_ns)
            if start[:3] == b"ID3" and len(start) == 10:
                size = _syncsafe(start[6:10])
                total = 10 + size + (10 if start[5] & 0x10 else 0)
                if total > MAX_HEAD_BYTES:
                    return None
                handle.seek(0)
                data = handle.read(total)
                if len(data) < total:
                    return None
                return Head("mp3", data, stat.st_mtime_ns, stat.st_atime_ns)
    except OSError as exc:
        logger.debug("Falha ao ler metadados crus de %s: %s", path, exc)
    return None


def _split_flac_blocks(data: bytes) -> list:
    """[(tipo, corpo), ...] a partir do bloco de metadados cru de um FLAC."""
    blocks, pos = [], 4
    while pos + 4 <= len(data):
        header = data[pos : pos + 4]
        length = int.from_bytes(header[1:4], "big")
        blocks.append((header[0] & 0x7F, data[pos + 4 : pos + 4 + length]))
        pos += 4 + length
        if header[0] & 0x80:
            break
    return blocks


def _join_flac_blocks(blocks: list) -> bytes:
    out = [b"fLaC"]
    for index, (kind, body) in enumerate(blocks):
        flag = 0x80 if index == len(blocks) - 1 else 0
        out.append(bytes([flag | kind]) + len(body).to_bytes(3, "big") + body)
    return b"".join(out)


def _b64(data: bytes) -> str:
    return base64.b64encode(zlib.compress(data)).decode("ascii")


def _unb64(text: str) -> bytes:
    return zlib.decompress(base64.b64decode(text))


def _atomic_write_text(path: str, text: str) -> None:
    folder = os.path.dirname(path)
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


# --------------------------------------------------------------------------
# Gravação do registro
# --------------------------------------------------------------------------
class Journal:
    """Registro de uma execução do `tags` (use ``Journal.create``)."""

    def __init__(self, run_dir: str):
        self.run_dir = run_dir
        self.blobs_dir = os.path.join(run_dir, "blobs")
        self.journal_path = os.path.join(run_dir, "journal.jsonl")
        self.pending_path = os.path.join(run_dir, "pending.json")
        self._pre: Optional[Head] = None
        self.recorded = 0
        self.unbacked = 0  # arquivos que não puderam ser guardados (formato estranho)

    @property
    def run_id(self) -> str:
        return os.path.basename(self.run_dir)

    @classmethod
    def create(cls, base_dir: str, mode: str, root: str, keep: int = KEEP_RUNS):
        """Cria a pasta da execução e apaga as mais antigas (mantém ``keep``)."""
        base = os.path.join(base_dir, JOURNAL_DIRNAME)
        os.makedirs(base, exist_ok=True)
        name = time.strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(base, name)
        suffix = 1
        while os.path.exists(run_dir):  # duas execuções no mesmo segundo
            suffix += 1
            run_dir = os.path.join(base, f"{name}_{suffix}")
        os.makedirs(os.path.join(run_dir, "blobs"))
        _atomic_write_text(
            os.path.join(run_dir, "meta.json"),
            json.dumps(
                {"mode": mode, "root": root, "started": time.time()},
                ensure_ascii=False,
            ),
        )
        prune(base_dir, keep=keep)
        return cls(run_dir)

    # -- blobs sem repetição -------------------------------------------------
    def _store_blob(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        target = os.path.join(self.blobs_dir, digest + ".z")
        if not os.path.exists(target):
            with open(target, "wb") as handle:
                handle.write(zlib.compress(data, 6))
        return digest

    # -- ciclo por arquivo ---------------------------------------------------
    def begin(self, path: str) -> bool:
        """Guarda o estado atual de ``path`` ANTES de gravar. False = sem backup."""
        head = read_head(path)
        self._pre = head
        if head is None:
            self.unbacked += 1
            return False
        _atomic_write_text(
            self.pending_path,
            json.dumps(
                {
                    "path": os.path.abspath(path),
                    "kind": head.kind,
                    "head": _b64(head.data),
                    "mtime_ns": head.mtime_ns,
                    "atime_ns": head.atime_ns,
                }
            ),
        )
        return True

    def finish(self, path: str) -> bool:
        """Depois de gravar: se os metadados mudaram, registra a linha do arquivo."""
        pre, self._pre = self._pre, None
        if pre is None:
            return False
        changed = False
        current = read_head(path)
        if current is None or current.digest != pre.digest:
            self._commit(os.path.abspath(path), pre, current.digest if current else "")
            changed = True
        self._clear_pending()
        return changed

    def _clear_pending(self) -> None:
        try:
            os.remove(self.pending_path)
        except OSError:
            pass

    def _commit(self, path: str, pre: Head, after_digest: str) -> None:
        if pre.kind == "flac":
            blocks = []
            for kind, body in _split_flac_blocks(pre.data):
                if kind == _FLAC_PICTURE:
                    blocks.append({"t": kind, "blob": self._store_blob(body)})
                else:
                    blocks.append({"t": kind, "inline": _b64(body)})
            before = {"blocks": blocks}
        else:
            before = {"blob": self._store_blob(pre.data)}
        entry = {
            "path": path,
            "kind": pre.kind,
            "before": before,
            "before_hash": pre.digest,
            "after_hash": after_digest,
            "mtime_ns": pre.mtime_ns,
            "atime_ns": pre.atime_ns,
        }
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        self.recorded += 1


# --------------------------------------------------------------------------
# Execuções guardadas
# --------------------------------------------------------------------------
def _runs_base(base_dir: str) -> str:
    return os.path.join(base_dir, JOURNAL_DIRNAME)


def prune(base_dir: str, keep: int = KEEP_RUNS) -> list:
    """Apaga as execuções mais antigas, mantendo as ``keep`` mais recentes."""
    base = _runs_base(base_dir)
    if not os.path.isdir(base):
        return []
    runs = sorted(
        name for name in os.listdir(base) if os.path.isdir(os.path.join(base, name))
    )
    removed = []
    for name in runs[: max(0, len(runs) - keep)]:
        shutil.rmtree(os.path.join(base, name), ignore_errors=True)
        removed.append(name)
    return removed


def list_runs(base_dir: str) -> list:
    """Execuções guardadas, da mais recente para a mais antiga."""
    base = _runs_base(base_dir)
    if not os.path.isdir(base):
        return []
    runs = []
    for name in sorted(os.listdir(base), reverse=True):
        run_dir = os.path.join(base, name)
        if not os.path.isdir(run_dir):
            continue
        meta = {}
        try:
            with open(os.path.join(run_dir, "meta.json"), encoding="utf-8") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            pass
        count = 0
        try:
            with open(
                os.path.join(run_dir, "journal.jsonl"), encoding="utf-8"
            ) as handle:
                count = sum(1 for line in handle if line.strip())
        except OSError:
            pass
        runs.append(
            {
                "id": name,
                "mode": meta.get("mode", "?"),
                "root": meta.get("root", "?"),
                "files": count,
                "pending": os.path.isfile(os.path.join(run_dir, "pending.json")),
            }
        )
    return runs


def _recover_pending(run_dir: str) -> bool:
    """Se a execução foi interrompida no meio de uma gravação, guarda esse arquivo."""
    pending_path = os.path.join(run_dir, "pending.json")
    if not os.path.isfile(pending_path):
        return False
    try:
        with open(pending_path, encoding="utf-8") as handle:
            data = json.load(handle)
        pre = Head(
            data["kind"],
            _unb64(data["head"]),
            int(data.get("mtime_ns", 0)),
            int(data.get("atime_ns", 0)),
        )
        journal = Journal(run_dir)
        current = read_head(data["path"])
        if current is not None and current.digest != pre.digest:
            journal._commit(data["path"], pre, current.digest)
            return True
    except (OSError, ValueError, KeyError) as exc:
        logger.debug("Não foi possível recuperar o pending de %s: %s", run_dir, exc)
    finally:
        try:
            os.remove(pending_path)
        except OSError:
            pass
    return False


# --------------------------------------------------------------------------
# Restauração
# --------------------------------------------------------------------------
def _rebuild_head(entry: dict, blobs_dir: str) -> bytes:
    def blob(digest: str) -> bytes:
        with open(os.path.join(blobs_dir, digest + ".z"), "rb") as handle:
            return zlib.decompress(handle.read())

    before = entry["before"]
    if entry["kind"] == "flac":
        blocks = []
        for item in before["blocks"]:
            body = blob(item["blob"]) if "blob" in item else _unb64(item["inline"])
            blocks.append((item["t"], body))
        return _join_flac_blocks(blocks)
    return blob(before["blob"])


def _write_with_new_head(path: str, new_head: bytes, old_head_len: int) -> None:
    folder = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".~undo_")
    try:
        with os.fdopen(fd, "wb") as out, open(path, "rb") as src:
            out.write(new_head)
            src.seek(old_head_len)
            while True:
                chunk = src.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
        shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def undo_run(
    base_dir: str,
    run_id: Optional[str] = None,
    force: bool = False,
    dry_run: bool = False,
    on_file: Optional[Callable[[str, str], None]] = None,
) -> Optional[dict]:
    """Restaura os metadados originais de uma execução do `tags`.

    run_id=None usa a execução mais recente. Cada arquivo só é restaurado se
    ainda estiver como o `tags` o deixou (``after_hash``); se foi alterado
    depois por outra coisa, é pulado, a menos que ``force``. Devolve contadores
    ou None se a execução não existir. ``on_file(status, caminho)`` recebe cada
    resultado (restored | would_restore | changed_since | missing | verify_failed
    | error).
    """
    runs = list_runs(base_dir)
    if not runs:
        return None
    chosen = None
    if run_id in (None, "", "last"):
        chosen = runs[0]["id"]
    else:
        for run in runs:
            if run["id"] == run_id:
                chosen = run_id
        if chosen is None:
            return None

    run_dir = os.path.join(_runs_base(base_dir), chosen)
    if not dry_run:
        _recover_pending(run_dir)
    blobs_dir = os.path.join(run_dir, "blobs")
    result = {
        "run": chosen,
        "restored": 0,
        "would_restore": 0,
        "changed_since": 0,
        "missing": 0,
        "verify_failed": 0,
        "errors": 0,
    }

    entries = []
    try:
        with open(os.path.join(run_dir, "journal.jsonl"), encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    entries.append(json.loads(line))
    except OSError:
        pass

    def report(status: str, path: str) -> None:
        if on_file:
            on_file(status, path)

    for entry in reversed(entries):
        path = entry["path"]
        try:
            current = read_head(path)
            if current is None:
                result["missing"] += 1
                report("missing", path)
                continue
            if current.digest != entry["after_hash"] and not force:
                result["changed_since"] += 1
                report("changed_since", path)
                continue
            if current.digest == entry["before_hash"]:
                continue  # já está como antes
            if dry_run:
                result["would_restore"] += 1
                report("would_restore", path)
                continue
            _write_with_new_head(
                path, _rebuild_head(entry, blobs_dir), len(current.data)
            )
            try:
                os.utime(path, ns=(entry["atime_ns"], entry["mtime_ns"]))
            except OSError:
                pass
            restored = read_head(path)
            if restored is None or restored.digest != entry["before_hash"]:
                result["verify_failed"] += 1
                report("verify_failed", path)
            else:
                result["restored"] += 1
                report("restored", path)
        except Exception as exc:  # noqa: BLE001 -- um arquivo ruim não para os outros
            logger.debug("Falha ao restaurar %s: %s", path, exc, exc_info=True)
            result["errors"] += 1
            report("error", path)
    return result
