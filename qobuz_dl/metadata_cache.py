"""Cache local, pequeno e assíncrono para respostas de metadados Qobuz.

Nunca deve receber tokens, credenciais ou URLs assinadas. O cliente só chama
este módulo para endpoints explicitamente permitidos e grava JSON normalizado.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import aiosqlite

_DEFAULT_TTL = 12 * 60 * 60
_DEFAULT_MAX_ENTRIES = 512


class MetadataCache:
    """Cache SQLite com TTL e limite aproximado de entradas."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        ttl: int = _DEFAULT_TTL,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.path = os.fspath(path)
        self.ttl = max(0, int(ttl))
        self.max_entries = max(32, int(max_entries))
        self._initialized = False
        self._init_lock: Any = None

    async def _ensure_schema(self) -> None:
        if self._initialized:
            return
        if self._init_lock is None:
            import asyncio

            self._init_lock = asyncio.Lock()
        async with self._init_lock:
            await self._initialize_schema()

    async def _initialize_schema(self) -> None:
        # Recheck sob o lock: outra coroutine pode ter criado o schema enquanto
        # esta aguardava a seção crítica.
        if self._initialized:
            return
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path, timeout=10) as conn:
            await conn.execute("PRAGMA busy_timeout=5000")
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata_cache (
                    cache_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    accessed_at REAL NOT NULL
                )
                """
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_metadata_cache_accessed "
                "ON metadata_cache(accessed_at)"
            )
            await conn.commit()
        self._initialized = True

    async def get(self, key: str) -> dict[str, Any] | None:
        if self.ttl == 0:
            return None
        try:
            await self._ensure_schema()
            now = time.time()
            async with aiosqlite.connect(self.path, timeout=10) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                cursor = await conn.execute(
                    "SELECT payload, created_at FROM metadata_cache WHERE cache_key=?",
                    (key,),
                )
                row = await cursor.fetchone()
                if not row or now - float(row[1]) > self.ttl:
                    if row:
                        await conn.execute(
                            "DELETE FROM metadata_cache WHERE cache_key=?", (key,)
                        )
                        await conn.commit()
                    return None
                await conn.execute(
                    "UPDATE metadata_cache SET accessed_at=? WHERE cache_key=?",
                    (now, key),
                )
                await conn.commit()
                value = json.loads(row[0])
                return value if isinstance(value, dict) else None
        except (OSError, aiosqlite.Error, json.JSONDecodeError, TypeError, ValueError):
            return None

    async def set(self, key: str, value: dict[str, Any]) -> None:
        if self.ttl == 0:
            return
        try:
            await self._ensure_schema()
            now = time.time()
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            async with aiosqlite.connect(self.path, timeout=10) as conn:
                await conn.execute("PRAGMA busy_timeout=5000")
                await conn.execute(
                    """
                    INSERT INTO metadata_cache(cache_key, payload, created_at, accessed_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        payload=excluded.payload,
                        created_at=excluded.created_at,
                        accessed_at=excluded.accessed_at
                    """,
                    (key, payload, now, now),
                )
                await conn.execute(
                    """
                    DELETE FROM metadata_cache
                    WHERE cache_key IN (
                        SELECT cache_key FROM metadata_cache
                        ORDER BY accessed_at ASC
                        LIMIT MAX(0, (SELECT COUNT(*) FROM metadata_cache) - ?)
                    )
                    """,
                    (self.max_entries,),
                )
                await conn.commit()
        except (OSError, aiosqlite.Error, TypeError, ValueError):
            return

    async def clear(self) -> None:
        try:
            await self._ensure_schema()
            async with aiosqlite.connect(self.path, timeout=10) as conn:
                await conn.execute("DELETE FROM metadata_cache")
                await conn.commit()
        except (OSError, aiosqlite.Error):
            return


def cache_key(endpoint: str, params: dict[str, Any]) -> str:
    """Gera uma chave estável sem incluir assinatura, timestamp ou segredos."""
    safe = {
        key: value
        for key, value in params.items()
        if key not in {"request_ts", "request_sig", "user_auth_token", "password"}
    }
    raw = json.dumps(
        {"endpoint": endpoint, "params": safe},
        sort_keys=True,
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def default_cache_path() -> str:
    """Resolve o caminho sem expor o cache dentro da pasta de músicas."""
    override = os.environ.get("QOBUZ_DL_METADATA_CACHE", "").strip()
    if override:
        return override
    try:
        import platformdirs

        root = platformdirs.user_cache_dir("qobuz-dl-ultra")
    except Exception:
        root = os.path.join(os.path.expanduser("~"), ".cache", "qobuz-dl-ultra")
    return os.path.join(root, "metadata.sqlite3")


def cache_settings() -> tuple[int, int]:
    def integer(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return default

    return integer("QOBUZ_DL_METADATA_CACHE_TTL", _DEFAULT_TTL), integer(
        "QOBUZ_DL_METADATA_CACHE_MAX", _DEFAULT_MAX_ENTRIES
    )
