#!/usr/bin/env python3
"""Sincronize snapshots dos artistas seguidos no Qobuz e prepare a Nota do iOS.

Uso típico (a-Shell / Atalhos):
    python3 qobuz_notes_sync.py --nota ~/Documents/qobuz-nota.txt --imprimir linhas

Sem argumentos, consulta `qobuz-dl artists --json`. Também aceita um JSON explícito:
    python3 qobuz_notes_sync.py ~/Documents/qobuz-artists.json

Com `--nota ARQUIVO` (texto exportado da Nota), a Nota passa a ser a fonte da verdade:
só entram em "a adicionar" os artistas cujo `[QOBUZ_ID:...]` ainda não está nela.

Não modifica o Apple Notes nem estados de revisão; prepara arquivos para conferência.
"""

from __future__ import annotations
import argparse
import contextlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ID_RE = re.compile(r"\[QOBUZ_ID:\s*([^\]\s]+)\s*\]")
MAX_NOMES_NO_RESUMO = 15


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def normalize_artists(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("artists"), list):
        raise ValueError('JSON inválido: esperado um objeto com a lista "artists"')
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload["artists"]:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id", "")).strip()
        name = str(item.get("name", "")).strip()
        if not ident or not name:
            raise ValueError("artista sem ID ou nome; nenhuma atualização foi gravada")
        if ident in seen:
            continue
        seen.add(ident)
        url = str(item.get("url") or f"https://open.qobuz.com/artist/{ident}").strip()
        out.append({"id": ident, "name": name, "url": url})
    return out


def atomic_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def atomic_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def checklist_line(a: dict[str, str]) -> str:
    # Avoid breaking the one-line checklist format.
    name = re.sub(r"[\r\n]+", " ", a["name"]).strip()
    return f"- [ ] {name} - [QOBUZ_ID:{a['id']}] - {a['url']}"


def extract_payload(text: str) -> dict | None:
    """Acha o objeto ``{"artists": [...]}`` numa saída que pode ter banners antes/depois.

    O `artists --json` imprime o JSON numa linha, mas o login/assinatura podem
    imprimir texto na mesma saída; por isso procuramos o JSON linha a linha.
    """
    text = text or ""
    candidates = [text.strip()]
    for line in reversed(text.splitlines()):
        line = line.strip()
        candidates.append(line)
        start, end = line.find("{"), line.rfind("}")
        if start >= 0 and end > start:
            candidates.append(line[start : end + 1])
    for cand in candidates:
        if not cand:
            continue
        try:
            value = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict) and isinstance(value.get("artists"), list):
            return value
    return None


def ids_in_note(text: str) -> set[str]:
    return set(ID_RE.findall(text or ""))


def _fetch_in_process() -> tuple[dict | None, str]:
    """Roda `artists --json` dentro deste processo (funciona onde subprocess não existe)."""
    try:
        from qobuz_dl import cli
    except Exception as exc:  # pacote não instalado/importável
        return None, f"não foi possível importar qobuz_dl: {exc}"
    out, err = io.StringIO(), io.StringIO()
    old_argv = sys.argv
    sys.argv = ["qobuz-dl", "artists", "--json"]
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            cli.main()
    except SystemExit:
        pass
    except KeyboardInterrupt:
        return None, "interrompido"
    except Exception as exc:
        err.write(str(exc))
    finally:
        sys.argv = old_argv
    payload = extract_payload(out.getvalue())
    detail = (err.getvalue() or out.getvalue()).strip()
    return payload, detail


def _fetch_subprocess() -> tuple[dict | None, str]:
    detail = ""
    for cmd in (
        [sys.executable or "python3", "-m", "qobuz_dl", "artists", "--json"],
        ["qobuz-dl", "artists", "--json"],
    ):
        try:
            result = subprocess.run(
                cmd, check=False, capture_output=True, text=True, encoding="utf-8"
            )
        except (OSError, ValueError) as exc:
            detail = str(exc)
            continue
        payload = extract_payload(result.stdout)
        if payload is not None:
            return payload, ""
        detail = (result.stderr or result.stdout).strip() or detail
    return None, detail


def fetch_payload() -> dict:
    payload, detail = _fetch_in_process()
    if payload is not None:
        return payload
    payload2, detail2 = _fetch_subprocess()
    if payload2 is not None:
        return payload2
    raise RuntimeError(
        "não foi possível obter `qobuz-dl artists --json`. Confirme que o CLI está "
        "instalado/autenticado e rode esse comando separadamente para conferir. "
        + (detail2 or detail)[-400:]
    )


def format_summary(stamp: str, stats: dict, new_now: list, removed_now: list) -> str:
    def names(items: list[dict[str, str]]) -> str:
        shown = [a["name"] for a in items[:MAX_NOMES_NO_RESUMO]]
        extra = len(items) - len(shown)
        return ", ".join(shown) + (f" (+{extra})" if extra > 0 else "")

    lines = [
        f"Qobuz — verificação de {stamp}",
        f"Seguidos: {stats['current_count']} | Novos: {stats['new_this_check']} | "
        f"Removidos: {stats['removed_this_check']} | A adicionar na Nota: {stats['to_append']}",
    ]
    if new_now:
        lines.append(f"Novos: {names(new_now)}")
    if removed_now:
        lines.append(f"Removidos: {names(removed_now)}")
    if not new_now and not removed_now and not stats["to_append"]:
        lines.append("Nenhuma mudança.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "input_json",
        nargs="?",
        help="JSON opcional produzido por `qobuz-dl artists --json`; omitido, consulta o Qobuz automaticamente",
    )
    ap.add_argument(
        "--dir",
        default="~/Documents/qobuz-dl/artist-sync",
        help="pasta de saída/histórico",
    )
    ap.add_argument(
        "--nota",
        metavar="ARQUIVO",
        help="texto exportado da Nota; só entram em 'a adicionar' os IDs que ainda não estão nela",
    )
    ap.add_argument(
        "--permitir-nota-vazia",
        action="store_true",
        help="aceita --nota sem nenhum [QOBUZ_ID:...] (padrão: recusa, pois pode ser uma exportação falha)",
    )
    ap.add_argument(
        "--imprimir",
        choices=("resumo", "linhas"),
        default="resumo",
        help="resumo: JSON com contagens (padrão); linhas: só as linhas da checklist a adicionar na Nota",
    )
    args = ap.parse_args(argv)
    outdir = Path(os.path.expanduser(args.dir))
    now = datetime.now().astimezone()
    try:
        outdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 1
    try:
        note_ids: set[str] | None = None
        if args.nota:
            note_path = Path(os.path.expanduser(args.nota))
            if not note_path.is_file():
                raise FileNotFoundError(f"arquivo da Nota não encontrado: {note_path}")
            note_ids = ids_in_note(
                note_path.read_text(encoding="utf-8", errors="replace")
            )
            if not note_ids and not args.permitir_nota_vazia:
                raise ValueError(
                    "a Nota exportada não tem nenhum [QOBUZ_ID:...]; recusando para não "
                    "duplicar a checklist inteira. Use --permitir-nota-vazia se a Nota é nova."
                )
        if args.input_json:
            source = Path(os.path.expanduser(args.input_json))
            if not source.is_file():
                print(
                    f"ERRO: arquivo de entrada não encontrado: {source}",
                    file=sys.stderr,
                )
                return 2
            raw = source.read_text(encoding="utf-8", errors="replace")
            payload = extract_payload(raw)
            if payload is None:
                raise ValueError(
                    f"{source} não contém o JSON de artistas; refaça `artists --json`"
                )
        else:
            payload = fetch_payload()
        current = normalize_artists(payload)
        if not current:
            raise ValueError(
                "a lista de artistas veio vazia; recusando atualizar o snapshot"
            )
        current_path = outdir / "artists-current.json"
        previous_path = outdir / "artists-previous.json"
        pending_path = outdir / "artists-pending.json"
        old_payload = load_json(current_path, None)
        old_artists = normalize_artists(old_payload) if old_payload else []
        old_by_id = {a["id"]: a for a in old_artists}
        cur_by_id = {a["id"]: a for a in current}
        new_now = [a for a in current if a["id"] not in old_by_id]
        removed_now = [a for a in old_artists if a["id"] not in cur_by_id]
        pending_old_payload = load_json(pending_path, {"artists": []})
        pending_old = (
            normalize_artists(pending_old_payload) if pending_old_payload else []
        )
        pending_by_id = {a["id"]: a for a in pending_old}
        pending_by_id.update({a["id"]: a for a in new_now})
        if note_ids is not None:
            # A Nota manda: quem já está nela sai da pendência.
            pending_by_id = {
                k: v for k, v in pending_by_id.items() if k not in note_ids
            }
        pending = sorted(pending_by_id.values(), key=lambda a: a["name"].casefold())
        if note_ids is not None:
            # Independe do snapshot: tudo que o Qobuz segue e a Nota ainda não tem.
            to_append = sorted(
                (a for a in current if a["id"] not in note_ids),
                key=lambda a: a["name"].casefold(),
            )
        else:
            to_append = pending
        stamp = now.strftime("%Y%m%d-%H%M%S%z")
        history = outdir / "history"
        history.mkdir(exist_ok=True)
        # Write historical records first. The old current snapshot is retained before replacement.
        if old_payload is not None:
            atomic_json(previous_path, old_payload)
        atomic_json(
            history / f"snapshot-{stamp}.json",
            {"checked_at": now.isoformat(), "artists": current},
        )
        atomic_json(
            outdir / "artists-new.json",
            {"checked_at": now.isoformat(), "artists": new_now},
        )
        atomic_json(
            outdir / "artists-removed.json",
            {"checked_at": now.isoformat(), "artists": removed_now},
        )
        atomic_json(pending_path, {"artists": pending})
        # Human-readable pending file; Shortcuts can also use the JSON IDs to filter Notes.
        atomic_text(
            outdir / "artists-pending.txt",
            "\n".join(checklist_line(a) for a in pending) + ("\n" if pending else ""),
        )
        append_text = "\n".join(checklist_line(a) for a in to_append) + (
            "\n" if to_append else ""
        )
        atomic_text(outdir / "artists-append.txt", append_text)
        atomic_json(current_path, {"checked_at": now.isoformat(), "artists": current})
        stats = {
            "current_count": len(current),
            "new_this_check": len(new_now),
            "removed_this_check": len(removed_now),
            "pending_total": len(pending),
            "to_append": len(to_append),
        }
        atomic_text(
            outdir / "ultimo-resumo.txt",
            format_summary(now.strftime("%d/%m/%Y %H:%M"), stats, new_now, removed_now),
        )
        if args.imprimir == "linhas":
            sys.stdout.write(append_text)
            return 0
        summary = {
            "ok": True,
            **stats,
            "output_dir": str(outdir),
            "files": [
                "artists-current.json",
                "artists-previous.json",
                "artists-new.json",
                "artists-removed.json",
                "artists-pending.json",
                "artists-pending.txt",
                "artists-append.txt",
                "ultimo-resumo.txt",
                "history/",
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        # Em --imprimir linhas o stdout vai direto para a Nota: nunca imprimir erro nele.
        print(f"ERRO: {exc}", file=sys.stderr)
        with contextlib.suppress(OSError):
            atomic_text(outdir / "ultimo-resumo.txt", f"ERRO: {exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
