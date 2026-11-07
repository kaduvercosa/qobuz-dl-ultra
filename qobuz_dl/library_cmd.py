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

from prompt_toolkit import PromptSession

from qobuz_dl import ui
from qobuz_dl.favorites_sync import SOURCE, FavoritesFetchError, run_sync
from qobuz_dl.library_db import LibraryDB
from qobuz_dl.library_scan import (
    apply_review_choice,
    reconcile_sentinels,
    run_scan,
    unmark_album_downloaded,
)
from qobuz_dl.paths import resolve_library_directory


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
    """Read a response through the async terminal UI, returning ``q`` on cancel."""
    session: PromptSession[str] = PromptSession()

    try:
        response = await session.prompt_async(prompt)
        return response.strip().lower()
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


def _gravar_json(caminho, dados):
    """Grava o relatório em JSON (chamado em thread pelo `scan --json`)."""
    with open(caminho, "w", encoding="utf-8") as fh:
        json.dump(dados, fh, ensure_ascii=False, indent=2, default=str)


async def cmd_scan(
    args: Any,
    *,
    directory: str,
    quality: int = 27,
    downloads_db: Optional[str] = None,
    lib: Optional[LibraryDB] = None,
) -> int:
    root = resolve_library_directory(getattr(args, "DIR", None), directory)
    lib = lib or LibraryDB(library_db_path())
    dry = bool(getattr(args, "dry_run", False))

    cabecalho = ui.Painel("🔍 SCAN DA BIBLIOTECA")
    cabecalho.kv("Pasta", root)
    cabecalho.kv("Modo", "SIMULAÇÃO (nada será gravado)" if dry else "aplicar")
    if not lib.get_albums(SOURCE):
        cabecalho.warn(
            "O catálogo está vazio. Rode `qobuz-dl sync-favorites` antes para o scan "
            "poder casar pastas com seus favoritos (pastas com tag QOBUZALBUMID "
            "ainda serão adotadas)."
        )
    cabecalho.emit()

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

    resultado = ui.Painel("📊 RESULTADO")
    resultado.kv("Pastas analisadas", report["scanned"])
    resultado.kv("Já tinham sentinela", report["sentinel_skipped"])
    resultado.kv("Marcadas (match certo)", len(report["auto_matched"]))
    resultado.kv("Adotadas (tag de ID)", len(report["adopted"]))
    resultado.kv("Incompletas", len(report["incomplete"]))
    resultado.kv("Para revisar", len(report["review"]))
    resultado.kv("Sem match", len(report["unmatched"]))
    resultado.kv("Falhas", len(report["failed"]))
    if report["incomplete"] or report["failed"]:
        resultado.rule()
    for item in report["incomplete"]:
        resultado.warn(
            f"Incompleto: {item['artist']} - {item['title']}  ({item['folder']})"
        )
    for f in report["failed"]:
        resultado.error(f"{f['folder']}: {f['error']}")
    resultado.emit()

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
        await asyncio.to_thread(_gravar_json, json_path, report)
        ui.ok(f"Relatório salvo em {json_path}")
    elif report["review"] and not _interactive():
        ui.info("Dica: use --json ARQUIVO para salvar os itens de revisão.")
    return 0


async def _review_loop(lib: LibraryDB, report: dict, *, sentinel: bool) -> None:
    total = len(report["review"])
    ui.Painel(f"🧐 REVISÃO MANUAL ({total} pastas)").info(
        "número = confirmar candidato · s = pular · q = sair da revisão"
    ).emit(lead=False)
    for n, item in enumerate(report["review"], start=1):
        pasta = ui.Painel(f"PASTA {n}/{total}")
        pasta.kv("Pasta", item["folder"])
        pasta.kv(
            "Local",
            f"{item['local_artist'] or '?'} - {item['local_album']} "
            f"({item['local_track_count']} faixas)",
        )
        if item["candidates"]:
            pasta.rule()
        for i, c in enumerate(item["candidates"], start=1):
            pasta.text(f"{i}) {c['artist']} - {c['title']}  [{c['reason']}]")
        pasta.emit(lead=False)
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
    como_json = bool(getattr(args, "json", False))

    if como_json and action in ("reset-stuck", "unmark"):
        ui.error(f"--json não se aplica a '{action}' (ele altera o catálogo).")
        return 2

    if action == "status":
        if como_json:
            ui.emit_json(
                {
                    "contagens": [
                        {
                            "origem": r["source"],
                            "estado": r["download_status"],
                            "total": r["cnt"],
                        }
                        for r in lib.status_counts()
                    ],
                    "presos": len(lib.stuck_albums()),
                    "completos_sem_pasta": len(lib.complete_without_folder()),
                }
            )
            return 0
        painel = ui.Painel("📚 BIBLIOTECA")
        rows = lib.status_counts()
        if not rows:
            painel.warn("Catálogo vazio. Rode `qobuz-dl sync-favorites`.").emit()
            return 0
        for r in rows:
            painel.kv(f"{r['source']} / {r['download_status']}", r["cnt"])
        stuck = lib.stuck_albums()
        nofolder = lib.complete_without_folder()
        if stuck or nofolder:
            painel.rule()
        if stuck:
            painel.warn(
                f"{len(stuck)} álbum(ns) presos em queued/downloading (resto de uma "
                "execução interrompida). Use `qobuz-dl library reset-stuck`."
            )
        if nofolder:
            painel.info(f"{len(nofolder)} álbum(ns) 'complete' sem pasta registrada.")
        painel.emit()
        return 0

    if action in ("missing", "list"):
        albums = lib.get_albums(include_removed=(action == "list"))
        if action == "missing":
            albums = [a for a in albums if a["download_status"] != "complete"]
        limit = getattr(args, "limit", None)
        if limit:
            albums = albums[: int(limit)]
        if como_json:
            ui.emit_json(
                [
                    {
                        "id": a["source_album_id"],
                        "artista": a["artist"],
                        "titulo": a["title"],
                        "estado": a["download_status"],
                        "bit_depth": a.get("bit_depth"),
                        "sample_rate": a.get("sample_rate"),
                        "removido_da_conta": bool(a["removed_from_service"]),
                    }
                    for a in albums
                ]
            )
            return 0
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
        if como_json:
            ui.emit_json(
                [
                    {
                        "id": h["id"],
                        "inicio": h["started_at"],
                        "estado": h["status"],
                        "achados": h["albums_found"],
                        "novos": h["albums_new"],
                        "removidos": h["albums_removed"],
                        "baixados": h["albums_downloaded"],
                    }
                    for h in hist
                ]
            )
            return 0
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
        root = resolve_library_directory(getattr(args, "TARGET", None), directory)
        rep = await asyncio.to_thread(
            reconcile_sentinels,
            lib,
            root,
            apply=not getattr(args, "dry_run", False),
            fix_missing=bool(getattr(args, "fix", False)),
        )
        if como_json:
            ui.emit_json(
                {
                    "sentinelas_vistas": rep["scanned"],
                    "reconciliadas": len(rep["reconciled"]),
                    "invalidas": rep["invalid"],
                    "sem_pasta": rep["missing_on_disk"],
                }
            )
            return 1 if rep["invalid"] else 0
        painel = ui.Painel("🔁 RECONCILIAÇÃO")
        painel.kv("Sentinelas vistas", rep["scanned"])
        painel.kv("Reconciliadas", len(rep["reconciled"]))
        painel.kv("Inválidas", len(rep["invalid"]))
        painel.kv("No catálogo, mas sem pasta", len(rep["missing_on_disk"]))
        if rep["invalid"] or rep["missing_on_disk"]:
            painel.rule()
        for i in rep["invalid"]:
            painel.warn(f"{i['folder']}: {i['error']}")
        for m in rep["missing_on_disk"]:
            painel.warn(
                f"Sumiu do disco: {m['artist']} - {m['title']} "
                f"({m['folder'] or 'sem pasta'})"
            )
        if rep["missing_on_disk"] and not getattr(args, "fix", False):
            painel.info("Use --fix para devolvê-los a not_downloaded.")
        painel.emit()
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


_LISTA_MAX = 20  # álbuns listados por painel; o resto vira "... e mais N"


def _painel_resumo_download(resultado: dict) -> ui.Painel:
    """Painel "RESUMO DO DOWNLOAD" (baixados e falhas) de um lote de favoritos."""
    painel = ui.Painel("📥 RESUMO DO DOWNLOAD")
    painel.kv("Baixados com sucesso", resultado["downloaded"])
    painel.kv("Falhas", resultado["failed"])
    return painel


async def cmd_sync_favorites(
    args: Any,
    qobuz: Any,
    *,
    lib: Optional[LibraryDB] = None,
    sleep=asyncio.sleep,
) -> int:
    """Synchronize Qobuz favorites and optionally download pending albums."""
    lib = lib or LibraryDB(library_db_path())
    download_new = bool(getattr(args, "download_new", False))
    download_missing = bool(getattr(args, "download_missing", False))
    dry = bool(getattr(args, "dry_run", False))
    auto_yes = bool(getattr(args, "yes", False))
    watch = getattr(args, "every", None)

    async def download_fn(album_id: str) -> bool:
        """Download one favorite album through the active Qobuz client."""
        return bool(await qobuz.download_from_id(album_id, album=True))

    async def confirm(targets: list[dict]) -> bool:
        """Confirm an album batch unless the current mode permits it automatically."""
        ui.warn(f"{len(targets)} álbum(ns) serão baixados.")
        if auto_yes or watch or not _interactive():
            return True
        return (await _ask("  Continuar? [s/N] ")) in ("s", "sim", "y", "yes")

    def progress(i, total, album):
        """Display progress for the current favorite-album download."""
        ui.step(f"[{i}/{total}] {_label(album)}")

    downloads_db = getattr(qobuz, "downloads_db", None)
    sentinel_enabled = getattr(getattr(qobuz, "settings", None), "write_sentinel", True)

    async def once() -> int:
        """Run and report one favorites synchronization cycle."""
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
        sync = ui.Painel("⭐ SINCRONIZAÇÃO DE FAVORITOS")
        sync.kv("Total de favoritos na conta", r["total"])
        sync.kv("Novos adicionados nesta sync", r["new"])
        sync.kv(
            "Removidos da conta Qobuz",
            r["removed"] if r["complete"] else "n/d (paginação incompleta)",
        )
        sync.emit()

        if r["new_albums"]:
            novos = ui.Painel(f"✨ ÁLBUNS NOVOS ADICIONADOS ({len(r['new_albums'])})")
            for a in r["new_albums"][:_LISTA_MAX]:
                novos.ok(_label(a))
            if len(r["new_albums"]) > _LISTA_MAX:
                novos.detail(f"... e mais {len(r['new_albums']) - _LISTA_MAX} álbuns.")
            novos.emit(lead=False)

        if r["removed_albums"]:
            removidos = ui.Painel(
                f"🗑️  ÁLBUNS REMOVIDOS DA CONTA ({len(r['removed_albums'])})"
            )
            for a in r["removed_albums"][:_LISTA_MAX]:
                removidos.warn(_label(a))
            if len(r["removed_albums"]) > _LISTA_MAX:
                removidos.detail(
                    f"... e mais {len(r['removed_albums']) - _LISTA_MAX} álbuns."
                )
            removidos.emit(lead=False)

        if res["targets"]:
            _painel_resumo_download(res["download"]).emit(lead=False)

        if dry:
            ui.Painel("🧪 SIMULAÇÃO").info(
                "Modo simulação: nenhuma alteração foi gravada."
            ).emit(lead=False)
            return 0

        # Se o usuário NÃO especificou nenhuma flag de download (--download-new / --download-missing)
        # e estamos em um terminal interativo, perguntar o que fazer.
        if not download_new and not download_missing and _interactive() and not watch:
            from qobuz_dl.favorites_sync import download_albums, pick_download_targets

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
                menu = ui.Painel("⬇️  OPÇÕES DE DOWNLOAD DOS FAVORITOS")
                menu.text("Deseja realizar o download agora?")
                menu.rule()
                if new_targets:
                    menu.text(
                        f"[1] Baixar apenas os {len(new_targets)} álbuns NOVOS",
                        indent=1,
                    )
                else:
                    menu.detail("[1] (Nenhum álbum novo para baixar)", indent=1)
                if missing_targets:
                    menu.text(
                        f"[2] Baixar TODOS os {len(missing_targets)} álbuns "
                        "FALTANTES no disco",
                        indent=1,
                    )
                else:
                    menu.detail(
                        "[2] (Todos os favoritos já estão completos no disco)",
                        indent=1,
                    )
                menu.text("[3] Apenas sincronizar o catálogo e sair [Padrão]", indent=1)
                menu.emit(lead=False)

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
                        _painel_resumo_download(dl_res).emit(lead=False)
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
