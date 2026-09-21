"""``qobuz-dl doctor``: checagem de saúde do ambiente, do config e dos bancos.

ORIGEM DA IDEIA
---------------
Portado dos scripts de diagnóstico do libsync (``env-doctor.sh``,
``check-sentinels.sh``, ``album-status-report.sh``, ``check-dedup.sh``),
unificados num comando só. Regras herdadas:

  * SOMENTE LEITURA: não instala nada, não faz rede, não grava nada.
  * Níveis PASS / WARN / FAIL; só FAIL derruba o código de saída.
  * Nunca imprime segredo (tokens/senhas): só diz se existem.

As checagens recebem caminhos por parâmetro (nada de globais), então rodam
igual em teste e em produção -- e funcionam mesmo com o config.ini quebrado,
que é justamente quando alguém roda o ``doctor``.
"""

from __future__ import annotations

import configparser
import importlib
import os
import shutil
import sqlite3
import stat
import sys
from dataclasses import asdict, dataclass
from typing import Optional

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"

REQUIRED_MODULES = [
    "pathvalidate",
    "httpx",
    "aiosqlite",
    "send2trash",
    "humanize",
    "aiofiles",
    "tenacity",
    "platformdirs",
    "charset_normalizer",
    "mutagen",
    "tqdm",
    "prompt_toolkit",
    "packaging",
    "colorama",
    "cryptography",
    "keyring",
]
OPTIONAL_MODULES = {
    "rapidfuzz": "match fuzzy mais rápido",
    "brotli": "respostas HTTP comprimidas",
    "PIL": "redimensionar capas (extra 'covers')",
    "watchdog": "--watch",
    "acoustid": "--find-duplicates",
    "lyricsgenius": "letras via Genius",
    "numpy": "análise espectral no inspect",
}


@dataclass
class Check:
    level: str
    name: str
    detail: str = ""


def check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 10):
        return Check(FAIL, "Python", f"{ver}: o projeto exige >= 3.10")
    return Check(PASS, "Python", ver)


def check_modules() -> list[Check]:
    out: list[Check] = []
    missing = []
    for mod in REQUIRED_MODULES:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append(mod)
    if missing:
        out.append(
            Check(
                FAIL,
                "Dependências",
                "faltando: " + ", ".join(missing) + " (pip install -U qobuz-dl-ultra)",
            )
        )
    else:
        out.append(Check(PASS, "Dependências", "todas as obrigatórias importam"))
    for mod, why in OPTIONAL_MODULES.items():
        try:
            importlib.import_module(mod)
            out.append(Check(PASS, f"Opcional: {mod}", why))
        except Exception:
            out.append(Check(WARN, f"Opcional: {mod}", f"ausente ({why})"))
    return out


def check_binaries() -> list[Check]:
    out = []
    for name, why, level in (
        ("ffmpeg", "verificação de integridade", WARN),
        ("ffprobe", "análise de arquivos", WARN),
        ("fpcalc", "--find-duplicates (Chromaprint)", WARN),
    ):
        path = shutil.which(name)
        out.append(
            Check(PASS, name, path)
            if path
            else Check(level, name, f"não está no PATH ({why})")
        )
    return out


def check_config(config_file: str) -> list[Check]:
    out: list[Check] = []
    if not os.path.isfile(config_file):
        return [
            Check(
                FAIL,
                "config.ini",
                f"não encontrado: {config_file} (rode `qobuz-dl -r`)",
            )
        ]
    out.append(Check(PASS, "config.ini", config_file))
    if os.name == "posix":
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        if mode & 0o077:
            out.append(
                Check(
                    WARN,
                    "Permissão do config",
                    f"{oct(mode)}: outros usuários podem ler (chmod 600)",
                )
            )
        else:
            out.append(Check(PASS, "Permissão do config", oct(mode)))
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(config_file, encoding="utf-8")
    except configparser.Error as exc:
        return out + [Check(FAIL, "Sintaxe do config", str(exc))]
    section = "qobuz" if cfg.has_section("qobuz") else "DEFAULT"
    for key in ("email", "app_id", "secrets", "default_quality"):
        if not cfg.get(section, key, fallback=""):
            out.append(
                Check(FAIL, f"config: {key}", "vazio ou ausente (rode `qobuz-dl -r`)")
            )
    disable_kr = cfg.get(
        section, "disable_keyring", fallback="false"
    ).strip().lower() in ("true", "1", "yes", "y")
    has_inline = bool(
        cfg.get(section, "auth_token", fallback="")
        or cfg.get(section, "password", fallback="")
    )
    if disable_kr and has_inline:
        out.append(
            Check(WARN, "Token", "em texto puro no config.ini (disable_keyring=true)")
        )
    elif not disable_kr:
        try:
            import keyring

            backend = keyring.get_keyring()
            name = f"{backend.__class__.__module__}.{backend.__class__.__name__}"
            if "fail" in name.lower() or "null" in name.lower():
                out.append(
                    Check(
                        WARN,
                        "Keyring",
                        f"sem backend seguro ({name}); use disable_keyring=true em servidores",
                    )
                )
            else:
                out.append(Check(PASS, "Keyring", name))
        except Exception as exc:
            out.append(Check(WARN, "Keyring", f"indisponível: {exc}"))
    return out


def check_directory(directory: Optional[str]) -> list[Check]:
    if not directory:
        return [Check(WARN, "Pasta de downloads", "não definida")]
    directory = os.path.expanduser(directory)
    if not os.path.isdir(directory):
        return [Check(WARN, "Pasta de downloads", f"ainda não existe: {directory}")]
    out = [Check(PASS, "Pasta de downloads", directory)]
    if not os.access(directory, os.W_OK):
        out.append(Check(FAIL, "Escrita na pasta", "sem permissão de escrita"))
    leftovers = stuck = 0
    for _, dirs, files in os.walk(directory):
        leftovers += sum(1 for f in files if f.startswith("~tmp_"))
        stuck += sum(1 for d in dirs if d.startswith("[IN PROGRESS]"))
    if leftovers:
        out.append(
            Check(
                WARN,
                "Temporários",
                f"{leftovers} arquivo(s) ~tmp_ de downloads interrompidos",
            )
        )
    if stuck:
        out.append(
            Check(
                WARN,
                "Pastas [IN PROGRESS]",
                f"{stuck} (execução interrompida; rode o download de novo)",
            )
        )
    return out


def _ro(path: str) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def check_downloads_db(db_path: str) -> list[Check]:
    if not os.path.isfile(db_path):
        return [
            Check(WARN, "qobuz_dl.db", "ainda não existe (criado no primeiro download)")
        ]
    try:
        conn = _ro(db_path)
        try:
            integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integ != "ok":
                return [Check(FAIL, "qobuz_dl.db", f"integrity_check: {integ}")]
            n = conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0]
            paths = conn.execute(
                "SELECT saved_path FROM downloads WHERE media_type='album' AND saved_path != ''"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [Check(FAIL, "qobuz_dl.db", str(exc))]
    out = [Check(PASS, "qobuz_dl.db", f"{n} registro(s), integridade ok")]
    stale = sum(1 for (p,) in paths if not os.path.exists(p))
    if stale:
        out.append(
            Check(
                WARN,
                "Registros obsoletos",
                f"{stale} álbum(ns) apontam para pasta inexistente "
                "(o downloader os descarta sozinho ao tentar baixar)",
            )
        )
    return out


def check_library_db(lib_path: str) -> list[Check]:
    if not os.path.isfile(lib_path):
        return [
            Check(
                WARN, "library.db", "ainda não existe (rode `qobuz-dl sync-favorites`)"
            )
        ]
    try:
        conn = _ro(lib_path)
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute("SELECT COUNT(*) FROM albums").fetchone()[0]
            stuck = conn.execute(
                "SELECT COUNT(*) FROM albums WHERE download_status IN ('queued','downloading')"
            ).fetchone()[0]
            nofolder = conn.execute(
                "SELECT COUNT(*) FROM albums WHERE download_status='complete' "
                "AND (local_folder_path IS NULL OR local_folder_path='')"
            ).fetchone()[0]
            gone = [
                r["local_folder_path"]
                for r in conn.execute(
                    "SELECT local_folder_path FROM albums WHERE download_status='complete' "
                    "AND local_folder_path IS NOT NULL AND local_folder_path != ''"
                )
            ]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [Check(FAIL, "library.db", str(exc))]
    out = [Check(PASS, "library.db", f"{total} álbum(ns)")]
    if stuck:
        out.append(
            Check(
                WARN,
                "Álbuns presos",
                f"{stuck} em queued/downloading (`qobuz-dl library reset-stuck`)",
            )
        )
    if nofolder:
        out.append(
            Check(
                WARN,
                "Sem pasta registrada",
                f"{nofolder} 'complete' sem local_folder_path (`qobuz-dl scan`)",
            )
        )
    missing = sum(1 for p in gone if not os.path.isdir(p))
    if missing:
        out.append(
            Check(
                WARN,
                "Pasta sumiu",
                f"{missing} álbum(ns) 'complete' sem pasta (`qobuz-dl library reconcile --fix`)",
            )
        )
    return out


def check_sentinels(directory: Optional[str]) -> list[Check]:
    if not directory or not os.path.isdir(os.path.expanduser(directory)):
        return []
    from qobuz_dl.sentinel import discover_sentinels, sentinel_identity, validate_folder

    records, failures, _ = discover_sentinels(os.path.expanduser(directory))
    bad = len(failures)
    for r in records:
        try:
            sentinel_identity(r.payload)
            if validate_folder(r.folder, r.payload):
                bad += 1
        except ValueError:
            bad += 1
    if bad:
        return [
            Check(
                WARN,
                "Sentinelas",
                f"{len(records)} válida(s) lidas, {bad} com problema (`qobuz-dl library reconcile`)",
            )
        ]
    return [
        Check(PASS, "Sentinelas", f"{len(records)} álbum(ns) com sentinela consistente")
    ]


def run_checks(
    *, config_file: str, downloads_db: str, library_db: str, directory: Optional[str]
) -> list[Check]:
    results: list[Check] = [check_python()]
    results += check_modules()
    results += check_binaries()
    results += check_config(config_file)
    results += check_directory(directory)
    results += check_downloads_db(downloads_db)
    results += check_library_db(library_db)
    results += check_sentinels(directory)
    return results


def resolve_directory(config_file: str) -> Optional[str]:
    """Lê ``directory`` (ou o legado ``default_folder``) sem exigir config válido."""
    cfg = configparser.ConfigParser(interpolation=None)
    try:
        cfg.read(config_file, encoding="utf-8")
    except configparser.Error:
        return None
    section = "qobuz" if cfg.has_section("qobuz") else "DEFAULT"
    return cfg.get(section, "directory", fallback=None) or cfg.get(
        section, "default_folder", fallback=None
    )


def render(results: list[Check]) -> int:
    """Imprime pela camada ``ui`` e devolve o código de saída (1 se houve FAIL)."""
    from qobuz_dl import ui

    ui.banner("QOBUZ-DL-ULTRA  ·  DOCTOR")
    for r in results:
        line = f"{r.level:<4}  {r.name}" + (f": {r.detail}" if r.detail else "")
        if r.level == FAIL:
            ui.error(line)
        elif r.level == WARN:
            ui.warn(line)
        else:
            ui.emit_always(f"  {line}")
    fails = sum(1 for r in results if r.level == FAIL)
    warns = sum(1 for r in results if r.level == WARN)
    ui.blank()
    if fails:
        ui.error(f"{fails} verificação(ões) FALHARAM, {warns} aviso(s).")
    else:
        ui.ok(f"Nenhuma falha. {warns} aviso(s) informativo(s).")
    return 1 if fails else 0


def to_json(results: list[Check]) -> list[dict]:
    return [asdict(r) for r in results]
