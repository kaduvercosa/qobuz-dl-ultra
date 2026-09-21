"""Camada de terminal dos comandos ``sync-favorites``, ``scan`` e ``library``.

A lógica vive em ``favorites_sync`` / ``library_scan`` / ``library_db`` (puras e
testáveis); aqui só há argumentos, impressão pela camada ``ui`` e prompts.
Cada ``cmd_*`` devolve o código de saída (0 = ok) em vez de chamar
``sys.exit``, para ser testável.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any, Optional

from qobuz_dl import ui
from qobuz_dl.favorites_sync import SOURCE, FavoritesFetchError, run_sync
from qobuz_dl.library_db import LibraryDB
from qobuz_dl.library_scan import (
    apply_review_choice,
    reconcile_sentinels,
    run_scan,
    unmark_album_downloaded,
)


def library_db_path(config_path: Optional[str] = None) -> str:
    """``library.db`` mora ao lado do config.ini (``$CONFIG_DIR/qobuz-dl``)."""
    if config_path is None:
        from qobuz_dl.utils import get_config_paths

        config_path = get_config_paths()["config_path"]
    return os.path.join(config_path, "library.db")


def _label(a: dict) -> str:
    return f"{a['artist']} - {a['title']}"


def _fmt_quality(a: dict) -> str:
    bd, sr = a.get("bit_depth"), a.get("sample_rate")
    if not bd:
        return ""
    return f"{bd}bit/{float(sr):g}kHz" if sr else f"{bd}bit"


def _interactive() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


async def _ask(prompt: str) -> str:
    try:
        return (await asyncio.to_thread(input, prompt)).strip().lower()
    except (EOFError, KeyboardInterrupt):
        return "q"


# ---------------------------------------------------------------------------
# scan
# ---------------------------------------------------------------------------


def _make_dedup_writer(downloads_db: Optional[str], quality: int):
    """Registra o álbum no ``qobuz_dl.db`` para o downloader não baixar de novo."""
    if not downloads_db:
        return None

    async def writer(album: dict, folder, meta) -> None:
        from qobuz_dl.db import handle_download_id

        aid = str(album["source_album_id"])
        if await handle_download_id(downloads_db, aid, add_id=False, quality=quality):
            return
        fmt = "FLAC"
        if meta.bit_depth is None and str(folder).lower().endswith(".mp3"):
            fmt = "MP3"
        await handle_download_id(
            db_path=downloads_db,
            item_id=aid,
            add_id=True,
            media_type="album",
            quality=quality,
            file_format=fmt,
            quality_met=1,
            bit_depth=str(meta.bit_depth) if meta.bit_depth else None,
            sampling_rate=str(meta.sample_rate) if meta.sample_rate else None,
            saved_path=str(folder),
            release_date=str(album.get("release_date") or ""),
            artist=album["artist"],
            album=album["title"],
        )

    return writer


async def cmd_scan(
    args: Any,
    *,
    directory: str,
    quality: int = 27,
    downloads_db: Optional[str] = None,
    lib: Optional[LibraryDB] = None,
) -> int:
    root = os.path.expanduser(getattr(args, "DIR", None) or directory)
    lib = lib or LibraryDB(library_db_path())
    dry = bool(getattr(args, "dry_run", False))

    ui.banner("QOBUZ-DL-ULTRA  ·  SCAN DA BIBLIOTECA")
    ui.kv("Pasta", root)
    ui.kv("Modo", "SIMULAÇÃO (nada será gravado)" if dry else "aplicar")
    if not lib.get_albums(SOURCE):
        ui.warn(
            "O catálogo está vazio. Rode `qobuz-dl sync-favorites` antes para o scan "
            "poder casar pastas com seus favoritos (pastas com tag QOBUZALBUMID "
            "ainda serão adotadas)."
        )

    def progress(i, total, folder):
        if i % 25 == 0 or i == total:
            ui.step(f"{i}/{total} pastas analisadas")

    report = await run_scan(
        lib,
        root,
        apply=not dry,
        sentinel_enabled=not getattr(args, "no_sentinel", False),
        adopt_unknown=not getattr(args, "no_adopt", False),
        rescan=bool(getattr(args, "rescan", False)),
        max_depth=int(getattr(args, "max_depth", 4) or 4),
        fuzzy_threshold=float(getattr(args, "fuzzy_threshold", 0.85) or 0.85),
        dedup_writer=None if dry else _make_dedup_writer(downloads_db, quality),
        progress=progress,
    )
    if report["status"] == "root_not_found":
        ui.error(f"Pasta não encontrada: {root}")
        return 1

    ui.blank()
    ui.section("RESULTADO")
    ui.kv("Pastas analisadas", report["scanned"])
    ui.kv("Já tinham sentinela", report["sentinel_skipped"])
    ui.kv("Marcadas (match certo)", len(report["auto_matched"]))
    ui.kv("Adotadas (tag de ID)", len(report["adopted"]))
    ui.kv("Incompletas", len(report["incomplete"]))
    ui.kv("Para revisar", len(report["review"]))
    ui.kv("Sem match", len(report["unmatched"]))
    ui.kv("Falhas", len(report["failed"]))
    for item in report["incomplete"]:
        ui.warn(f"Incompleto: {item['artist']} - {item['title']}  ({item['folder']})")
    for f in report["failed"]:
        ui.error(f"{f['folder']}: {f['error']}")

    if (
        report["review"]
        and not dry
        and _interactive()
        and not getattr(args, "no_review", False)
    ):
        await _review_loop(
            lib, report, sentinel=not getattr(args, "no_sentinel", False)
        )

    json_path = getattr(args, "json", None)
    if json_path:
        with open(json_path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
        ui.ok(f"Relatório salvo em {json_path}")
    elif report["review"] and not _interactive():
        ui.info("Dica: use --json ARQUIVO para salvar os itens de revisão.")
    return 0


async def _review_loop(lib: LibraryDB, report: dict, *, sentinel: bool) -> None:
    ui.blank()
    ui.section(f"REVISÃO MANUAL ({len(report['review'])} pastas)")
    ui.info("  número = confirmar candidato · s = pular · q = sair da revisão")
    for n, item in enumerate(report["review"], start=1):
        ui.blank()
        ui.info(f"[{n}/{len(report['review'])}] {item['folder']}")
        ui.detail(
            f"local: {item['local_artist'] or '?'} - {item['local_album']} "
            f"({item['local_track_count']} faixas)"
        )
        for i, c in enumerate(item["candidates"], start=1):
            ui.detail(f"{i}) {c['artist']} - {c['title']}  [{c['reason']}]")
        while True:
            ans = await _ask("  > ")
            if ans in ("q", "quit", "sair"):
                return
            if ans in ("", "s", "skip", "pular"):
                break
            if ans.isdigit() and 1 <= int(ans) <= len(item["candidates"]):
                cand = item["candidates"][int(ans) - 1]
                await asyncio.to_thread(
                    apply_review_choice,
                    lib,
                    item,
                    cand["album_id"],
                    sentinel_enabled=sentinel,
                )
                ui.ok(f"Marcado: {cand['artist']} - {cand['title']}")
                break
            ui.warn("Opção inválida.")


# ---------------------------------------------------------------------------
# library
# ---------------------------------------------------------------------------


async def cmd_library(
    args: Any, *, directory: str, lib: Optional[LibraryDB] = None
) -> int:
    lib = lib or LibraryDB(library_db_path())
    action = getattr(args, "action", "status") or "status"

    if action == "status":
        ui.banner("QOBUZ-DL-ULTRA  ·  BIBLIOTECA")
        rows = lib.status_counts()
        if not rows:
            ui.warn("Catálogo vazio. Rode `qobuz-dl sync-favorites`.")
            return 0
        for r in rows:
            ui.kv(f"{r['source']} / {r['download_status']}", r["cnt"])
        stuck = lib.stuck_albums()
        if stuck:
            ui.warn(
                f"{len(stuck)} álbum(ns) presos em queued/downloading (resto de uma "
                "execução interrompida). Use `qobuz-dl library reset-stuck`."
            )
        nofolder = lib.complete_without_folder()
        if nofolder:
            ui.info(f"{len(nofolder)} álbum(ns) 'complete' sem pasta registrada.")
        return 0

    if action in ("missing", "list"):
        albums = lib.get_albums(include_removed=(action == "list"))
        if action == "missing":
            albums = [a for a in albums if a["download_status"] != "complete"]
        limit = getattr(args, "limit", None)
        if limit:
            albums = albums[: int(limit)]
        if not albums:
            ui.ok("Nada para listar.")
            return 0
        for a in albums:
            extra = " (removido da conta)" if a["removed_from_service"] else ""
            ui.emit_always(
                f"{a['download_status']:<14} {a['source_album_id']:<16} "
                f"{_label(a)} {_fmt_quality(a)}{extra}"
            )
        ui.info(f"\n{len(albums)} álbum(ns).")
        return 0

    if action == "history":
        hist = lib.get_sync_history(
            SOURCE, limit=int(getattr(args, "limit", None) or 10)
        )
        if not hist:
            ui.info("Nenhuma sincronização registrada.")
        for h in hist:
            ui.emit_always(
                f"#{h['id']:<4} {h['started_at']}  {h['status']:<11} "
                f"achados={h['albums_found']} novos={h['albums_new']} "
                f"removidos={h['albums_removed']} baixados={h['albums_downloaded']}"
            )
        return 0

    if action == "reset-stuck":
        n = lib.reset_stuck()
        ui.ok(f"{n} álbum(ns) devolvidos a not_downloaded.")
        return 0

    if action == "reconcile":
        root = os.path.expanduser(getattr(args, "TARGET", None) or directory)
        rep = await asyncio.to_thread(
            reconcile_sentinels,
            lib,
            root,
            apply=not getattr(args, "dry_run", False),
            fix_missing=bool(getattr(args, "fix", False)),
        )
        ui.banner("QOBUZ-DL-ULTRA  ·  RECONCILIAÇÃO")
        ui.kv("Sentinelas vistas", rep["scanned"])
        ui.kv("Reconciliadas", len(rep["reconciled"]))
        ui.kv("Inválidas", len(rep["invalid"]))
        ui.kv("No catálogo, mas sem pasta", len(rep["missing_on_disk"]))
        for i in rep["invalid"]:
            ui.warn(f"{i['folder']}: {i['error']}")
        for m in rep["missing_on_disk"]:
            ui.warn(
                f"Sumiu do disco: {m['artist']} - {m['title']} ({m['folder'] or 'sem pasta'})"
            )
        if rep["missing_on_disk"] and not getattr(args, "fix", False):
            ui.info("Use --fix para devolvê-los a not_downloaded.")
        return 1 if rep["invalid"] else 0

    if action == "unmark":
        ident = getattr(args, "TARGET", None)
        album = lib.get_album_by_source_id(SOURCE, ident) if ident else None
        if album is None:
            ui.error("Informe o ID do álbum Qobuz: qobuz-dl library unmark <ID>")
            return 1
        unmark_album_downloaded(lib, album["id"])
        ui.ok(f"Desmarcado: {_label(album)}")
        return 0

    ui.error(f"Ação desconhecida: {action}")
    return 2


# ---------------------------------------------------------------------------
# sync-favorites
# ---------------------------------------------------------------------------


async def cmd_sync_favorites(
    args: Any,
    qobuz: Any,
    *,
    lib: Optional[LibraryDB] = None,
    sleep=asyncio.sleep,
) -> int:
    lib = lib or LibraryDB(library_db_path())
    download_new = bool(getattr(args, "download_new", False))
    download_missing = bool(getattr(args, "download_missing", False))
    dry = bool(getattr(args, "dry_run", False))
    auto_yes = bool(getattr(args, "yes", False))
    watch = getattr(args, "every", None)

    async def download_fn(album_id: str) -> bool:
        return bool(await qobuz.download_from_id(album_id, album=True))

    async def confirm(targets: list[dict]) -> bool:
        ui.warn(f"{len(targets)} álbum(ns) serão baixados.")
        if auto_yes or watch or not _interactive():
            return True
        return (await _ask("  Continuar? [s/N] ")) in ("s", "sim", "y", "yes")

    def progress(i, total, album):
        ui.step(f"[{i}/{total}] {_label(album)}")

    downloads_db = getattr(qobuz, "downloads_db", None)
    sentinel_enabled = getattr(getattr(qobuz, "settings", None), "write_sentinel", True)

    async def once() -> int:
        try:
            res = await run_sync(
                lib,
                qobuz.client,
                download_new=download_new,
                download_missing=download_missing,
                download_fn=download_fn,
                downloads_db=downloads_db,
                sentinel_enabled=sentinel_enabled,
                limit=getattr(args, "limit", None),
                dry_run=dry,
                progress=progress,
                confirm=confirm,
            )
        except FavoritesFetchError as exc:
            ui.error(str(exc))
            return 1

        r = res["refresh"]
        ui.banner("QOBUZ-DL-ULTRA  ·  SINCRONIZAÇÃO DE FAVORITOS")
        ui.kv("Total de favoritos na conta", r["total"])
        ui.kv("Novos adicionados nesta sync", r["new"])
        ui.kv(
            "Removidos da conta Qobuz",
            r["removed"] if r["complete"] else "n/d (paginação incompleta)",
        )

        if r["new_albums"]:
            ui.blank()
            ui.section(f"✨ ÁLBUNS NOVOS ADICIONADOS ({len(r['new_albums'])})")
            for a in r["new_albums"][:20]:
                ui.ok(f"  + {_label(a)}")
            if len(r["new_albums"]) > 20:
                ui.detail(f"  ... e mais {len(r['new_albums']) - 20} álbuns.")

        if r["removed_albums"]:
            ui.blank()
            ui.section(f"🗑️  ÁLBUNS REMOVIDOS DA CONTA ({len(r['removed_albums'])})")
            for a in r["removed_albums"][:20]:
                ui.warn(f"  - {_label(a)}")
            if len(r["removed_albums"]) > 20:
                ui.detail(f"  ... e mais {len(r['removed_albums']) - 20} álbuns.")

        if res["targets"]:
            ui.blank()
            ui.section("RESUMO DO DOWNLOAD")
            ui.kv("Baixados com sucesso", res["download"]["downloaded"])
            ui.kv("Falhas", res["download"]["failed"])

        if dry:
            ui.blank()
            ui.info("Modo simulação: nenhuma alteração foi gravada.")
            return 0

        # Se o usuário NÃO especificou nenhuma flag de download (--download-new / --download-missing)
        # e estamos em um terminal interativo, perguntar o que fazer.
        if not download_new and not download_missing and _interactive() and not watch:
            from qobuz_dl.favorites_sync import pick_download_targets, download_albums

            missing_targets = pick_download_targets(
                lib, source=SOURCE, missing=True, limit=getattr(args, "limit", None)
            )
            new_targets = pick_download_targets(
                lib,
                source=SOURCE,
                only_ids=r["new_ids"],
                limit=getattr(args, "limit", None),
            )

            if missing_targets or new_targets:
                ui.blank()
                ui.section("OPÇÕES DE DOWNLOAD DOS FAVORITOS")
                ui.emit("  Deseja realizar o download agora?")
                if new_targets:
                    ui.emit(f"   [1] Baixar apenas os {len(new_targets)} álbuns NOVOS")
                else:
                    ui.emit("   [1] (Nenhum álbum novo para baixar)")

                if missing_targets:
                    ui.emit(
                        f"   [2] Baixar TODOS os {len(missing_targets)} álbuns FALTANTES no disco"
                    )
                else:
                    ui.emit("   [2] (Todos os favoritos já estão completos no disco)")

                ui.emit("   [3] Apenas sincronizar o catálogo e sair [Padrão]")

                ans = await _ask("  Escolha uma opção (1-3): ")
                chosen_targets = []
                if ans == "1" and new_targets:
                    chosen_targets = new_targets
                elif ans == "2" and missing_targets:
                    chosen_targets = missing_targets

                if chosen_targets:
                    if await confirm(chosen_targets):
                        dl_res = await download_albums(
                            lib,
                            chosen_targets,
                            download_fn,
                            downloads_db=downloads_db,
                            sentinel_enabled=sentinel_enabled,
                            progress=progress,
                        )
                        if res["run_id"] is not None:
                            await asyncio.to_thread(
                                lib.update_sync_run_downloaded,
                                res["run_id"],
                                dl_res["downloaded"],
                            )
                        ui.blank()
                        ui.section("RESUMO DO DOWNLOAD")
                        ui.kv("Baixados com sucesso", dl_res["downloaded"])
                        ui.kv("Falhas", dl_res["failed"])
                        return 1 if dl_res["failed"] else 0

        return 1 if res["download"]["failed"] else 0

    if not watch:
        return await once()

    minutes = max(1, int(watch))
    ui.info(
        f"Modo contínuo (--every): sincronizando a cada {minutes} min. CTRL+C para sair."
    )
    while True:
        await once()
        await sleep(minutes * 60)
