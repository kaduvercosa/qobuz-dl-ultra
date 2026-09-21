"""Sincronização de favoritos: conta Qobuz ⇄ catálogo local (``library.db``).

ORIGEM DA IDEIA
---------------
Portado do ``SyncService`` do libsync (``backend/services/sync.py``): puxa os
álbuns favoritos da conta, faz o *diff* contra o catálogo local (novos /
removidos), guarda o histórico de execuções e, opcionalmente, baixa o que está
faltando. No libsync isso rodava num servidor com WebSocket; aqui é um comando
de terminal (``qobuz-dl sync-favorites``), com modo ``--watch`` para rodar em
loop num NAS/servidor.

PONTO DE SEGURANÇA (aprendido do libsync)
-----------------------------------------
A leitura paginada e estrita vive em ``qopy.Client.get_all_favorites``. Uma
queda de rede nunca pode virar "você removeu todos os favoritos": este módulo
só marca remoções quando a paginação foi *comprovadamente completa* (itens
coletados == ``total`` da API).

Este módulo não imprime nada: devolve dicts. A camada de terminal é
``qobuz_dl.library_cmd``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from typing import Any, Awaitable, Callable, Iterable, Optional

from qobuz_dl.library_db import (
    STATUS_COMPLETE,
    STATUS_DOWNLOADING,
    STATUS_FAILED,
    STATUS_NOT_DOWNLOADED,
    STATUS_QUEUED,
    LibraryDB,
)
from qobuz_dl.library_scan import mark_album_downloaded

logger = logging.getLogger(__name__)

SOURCE = "qobuz"
PAGE_SIZE = 500  # máximo aceito por getUserFavorites
MAX_PAGES = 400  # trava de segurança contra loop infinito de paginação


class FavoritesFetchError(RuntimeError):
    """Falha ao listar favoritos (rede, token, segredo inválido...)."""


# ---------------------------------------------------------------------------
# Leitura da API
# ---------------------------------------------------------------------------


async def fetch_all_favorite_albums(
    client: Any, *, page_size: int = PAGE_SIZE
) -> tuple[list[dict], Optional[int]]:
    """Pagina ``favorite/getUserFavorites`` (albums). Devolve ``(itens, total)``.

    Levanta ``FavoritesFetchError`` em qualquer falha -- NUNCA devolve lista
    parcial disfarçada de completa.
    """
    try:
        return await client.get_all_favorites(
            "albums", page_size=page_size, max_pages=MAX_PAGES
        )
    except Exception as exc:
        raise FavoritesFetchError(f"falha ao listar favoritos: {exc}") from exc


def _name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or "")
    return str(value or "")


def extract_album_data(item: dict) -> Optional[dict]:
    """Converte um item de favorito do Qobuz nos campos do catálogo.

    Devolve None se faltar o ID. O título inclui a *versão* quando ela não está
    no título (mesma regra do downloader), para o nome bater com o da pasta.
    """
    album_id = item.get("id")
    if album_id in (None, ""):
        return None
    title = str(item.get("title") or "").strip()
    version = str(item.get("version") or "").strip()
    if version and version.lower() not in title.lower():
        title = f"{title} ({version})"
    artist = _name(item.get("artist")) or _name(item.get("performer")) or "Unknown"
    image = item.get("image") if isinstance(item.get("image"), dict) else {}
    return {
        "source_album_id": str(album_id),
        "title": title or "Unknown",
        "artist": artist,
        "track_count": item.get("tracks_count") or None,
        "bit_depth": item.get("maximum_bit_depth") or None,
        "sample_rate": item.get("maximum_sampling_rate") or None,
        "release_date": str(
            item.get("release_date_original") or item.get("release_date") or ""
        )
        or None,
        "label": _name(item.get("label")) or None,
        "genre": _name(item.get("genre")) or None,
        "upc": str(item.get("upc") or "") or None,
        "duration_seconds": item.get("duration") or None,
        "cover_url": image.get("large") or image.get("small") or None,
    }


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


async def refresh_library(
    lib: LibraryDB, client: Any, *, source: str = SOURCE, dry_run: bool = False
) -> dict:
    """Baixa a lista de favoritos e atualiza o catálogo (ou só simula).

    Retorna ``{"total", "new", "new_ids", "new_albums", "removed", "removed_albums",
    "complete"}``. ``removed`` só é calculado se a paginação foi completa.
    """
    items, api_total = await fetch_all_favorite_albums(client)
    parsed = [p for p in (extract_album_data(i) for i in items) if p]
    ids = [p["source_album_id"] for p in parsed]
    complete = api_total is not None and len(items) >= api_total

    new_albums: list[dict] = []
    for p in parsed:
        existing = await asyncio.to_thread(
            lib.get_album_by_source_id, source, p["source_album_id"]
        )
        if existing is None:
            new_albums.append(p)
        if not dry_run:
            fields = {
                k: v
                for k, v in p.items()
                if k not in ("source_album_id", "title", "artist")
            }
            await asyncio.to_thread(
                lib.upsert_album,
                source,
                p["source_album_id"],
                p["title"],
                p["artist"],
                **fields,
            )

    removed: list[dict] = []
    if complete:
        if dry_run:
            present = set(ids)
            removed = [
                a
                for a in await asyncio.to_thread(
                    lib.get_albums, source, include_removed=False
                )
                if a["source_album_id"] not in present
            ]
        else:
            removed = await asyncio.to_thread(lib.mark_removed, source, ids)

    return {
        "total": len(parsed),
        "new": len(new_albums),
        "new_ids": [a["source_album_id"] for a in new_albums],
        "new_albums": new_albums,
        "removed": len(removed),
        "removed_albums": removed,
        "complete": complete,
    }


def lookup_saved_path(db_path: Optional[str], album_id: Any) -> Optional[str]:
    """Caminho salvo pelo downloader (``qobuz_dl.db``) para um álbum baixado."""
    if not db_path or not os.path.isfile(db_path):
        return None
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT saved_path FROM downloads WHERE id=? AND media_type='album' "
                "AND saved_path != '' ORDER BY rowid DESC LIMIT 1",
                (str(album_id),),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return row[0] if row else None


DownloadFn = Callable[[str], Awaitable[bool]]
ProgressFn = Callable[[int, int, dict], None]


def pick_download_targets(
    lib: LibraryDB,
    *,
    source: str = SOURCE,
    only_ids: Optional[Iterable[str]] = None,
    missing: bool = False,
    limit: Any = None,
) -> list[dict]:
    """Escolhe o que baixar: só ``only_ids`` (novos) ou todos os não completos."""
    limit_int: Optional[int] = None
    if limit is not None:
        try:
            limit_int = int(limit)
            if limit_int < 0:
                limit_int = None
        except (ValueError, TypeError):
            limit_int = None

    if only_ids is not None:
        wanted = set(only_ids)
        rows = [
            a
            for a in lib.get_albums(source, include_removed=False)
            if a["source_album_id"] in wanted
        ]
    elif missing:
        rows = [
            a
            for a in lib.get_albums(source, include_removed=False)
            if a["download_status"] != STATUS_COMPLETE
        ]
    else:
        rows = []
    return rows[:limit_int] if limit_int is not None else rows


async def download_albums(
    lib: LibraryDB,
    albums: list[dict],
    download_fn: DownloadFn,
    *,
    downloads_db: Optional[str] = None,
    sentinel_enabled: bool = True,
    progress: Optional[ProgressFn] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> dict:
    """Baixa ``albums`` em sequência via ``download_fn(album_id) -> bool``.

    Mantém ``download_status`` coerente (queued → downloading → complete/failed)
    e grava o caminho local a partir do ``qobuz_dl.db``. A sentinela rica
    (com lista de faixas) é escrita pelo próprio downloader; aqui só criamos
    uma se ela ainda não existir.
    """
    done = failed = 0
    failures: list[dict] = []
    for a in albums:
        await asyncio.to_thread(lib.update_status, a["id"], STATUS_QUEUED)

    for i, a in enumerate(albums, start=1):
        if stop_event is not None and stop_event.is_set():
            await asyncio.to_thread(lib.update_status, a["id"], STATUS_NOT_DOWNLOADED)
            continue
        if progress:
            progress(i, len(albums), a)
        await asyncio.to_thread(lib.update_status, a["id"], STATUS_DOWNLOADING)
        try:
            ok = bool(await download_fn(a["source_album_id"]))
        except Exception as exc:
            logger.exception("sync: falha baixando %s", a["source_album_id"])
            ok = False
            failures.append(
                {"album_id": a["id"], "title": a["title"], "error": str(exc)}
            )
        if ok:
            current = await asyncio.to_thread(lib.get_album, a["id"])
            if current and current["download_status"] == STATUS_COMPLETE:
                # O downloader normal já finalizou o mesmo library.db.
                # Não repita escrita de estado/sentinela aqui.
                done += 1
                continue
            folder = lookup_saved_path(downloads_db, a["source_album_id"])
            if folder and os.path.isdir(folder):
                await asyncio.to_thread(
                    mark_album_downloaded,
                    lib,
                    a["id"],
                    folder=folder,
                    sentinel_enabled=sentinel_enabled,
                )
            else:
                await asyncio.to_thread(
                    lib.set_download_state,
                    a["id"],
                    downloaded=True,
                    status=STATUS_COMPLETE,
                )
            done += 1
        else:
            await asyncio.to_thread(lib.update_status, a["id"], STATUS_FAILED)
            failed += 1
    return {"downloaded": done, "failed": failed, "failures": failures}


async def run_sync(
    lib: LibraryDB,
    client: Any,
    *,
    source: str = SOURCE,
    download_new: bool = False,
    download_missing: bool = False,
    download_fn: Optional[DownloadFn] = None,
    downloads_db: Optional[str] = None,
    sentinel_enabled: bool = True,
    limit: Optional[int] = None,
    dry_run: bool = False,
    progress: Optional[ProgressFn] = None,
    confirm: Optional[Callable[[list[dict]], Awaitable[bool]]] = None,
    stop_event: Optional[asyncio.Event] = None,
) -> dict:
    """Roda uma sincronização completa e registra no histórico."""
    run_id: Optional[int] = None
    if not dry_run:
        run_id = await asyncio.to_thread(lib.create_sync_run, source)
    try:
        refresh = await refresh_library(lib, client, source=source, dry_run=dry_run)

        targets: list[dict] = []
        if not dry_run and download_fn is not None:
            if download_missing:
                targets = pick_download_targets(
                    lib, source=source, missing=True, limit=limit
                )
            elif download_new:
                targets = pick_download_targets(
                    lib, source=source, only_ids=refresh["new_ids"], limit=limit
                )

        dl = {"downloaded": 0, "failed": 0, "failures": []}
        if targets:
            if confirm is not None and not await confirm(targets):
                targets = []
            else:
                dl = await download_albums(
                    lib,
                    targets,
                    download_fn,  # type: ignore[arg-type]
                    downloads_db=downloads_db,
                    sentinel_enabled=sentinel_enabled,
                    progress=progress,
                    stop_event=stop_event,
                )

        if run_id is not None:
            await asyncio.to_thread(
                lib.complete_sync_run,
                run_id,
                albums_found=refresh["total"],
                albums_new=refresh["new"],
                albums_removed=refresh["removed"],
                albums_downloaded=dl["downloaded"],
            )
        return {
            "status": "complete",
            "run_id": run_id,
            "dry_run": dry_run,
            "refresh": refresh,
            "targets": len(targets),
            "download": dl,
        }
    except asyncio.CancelledError:
        if run_id is not None:
            await asyncio.to_thread(lib.interrupt_sync_run, run_id)
        raise
    except Exception:
        if run_id is not None:
            await asyncio.to_thread(lib.fail_sync_run, run_id)
        raise
