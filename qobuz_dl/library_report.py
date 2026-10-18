"""Relatório da biblioteca: aponta o que está faltando ou estranho nas tags.

`tags --report [ARQUIVO]` varre a pasta (só leitura, sem login, sem rede) e
gera um relatório HTML (padrão) ou CSV (se o nome terminar em ``.csv``) com os
problemas por álbum e por faixa: sem capa, capa pequena ou retangular, sem
ISRC, sem ou com ID Qobuz inválido, sem artista do álbum, sem data, sem número
da faixa. O objetivo é saber ONDE rodar o `tags` antes de rodá-lo.

Nada é gravado nos arquivos de música; o relatório é um arquivo novo.
"""

from __future__ import annotations

import csv
import html
import io
import logging
import os
import re
import time
from collections import Counter, defaultdict
from typing import Callable, Optional

from qobuz_dl.retro_tags import AUDIO_EXTENSIONS, extract_album_id, find_audio_files
from qobuz_dl.retro_tagger import extract_track_id

logger = logging.getLogger(__name__)

# Menor lado (px) abaixo do qual a capa embutida é considerada pequena.
MIN_COVER_SIDE = 500

# código -> (rótulo curto, explicação / o que fazer)
ISSUES = {
    "unreadable": ("Arquivo ilegível", "Não foi possível ler as tags do arquivo."),
    "no_cover": (
        "Sem capa",
        "Sem imagem embutida. `tags --covers-only` pode resolver.",
    ),
    "small_cover": (
        f"Capa pequena (< {MIN_COVER_SIDE}px)",
        "A capa embutida é de baixa resolução; a da Apple/Qobuz costuma ser maior.",
    ),
    "bad_shape_cover": ("Capa não quadrada", "A capa embutida não é quadrada."),
    "no_track_id": (
        "Sem ID Qobuz da faixa",
        "O `tags` não consegue corrigir sozinho arquivos sem ID.",
    ),
    "bad_track_id": (
        "ID da faixa inválido",
        "O ID Qobuz da faixa deveria ser um número.",
    ),
    "no_album_id": (
        "Sem ID Qobuz do álbum",
        "Sem o ID do álbum o `tags` consulta faixa por faixa (mais lento).",
    ),
    "bad_album_id": (
        "ID do álbum inválido",
        "O ID do álbum tem caracteres que não existem num ID Qobuz.",
    ),
    "no_isrc": ("Sem ISRC", "Falta o código ISRC; o `tags` regrava a partir do Qobuz."),
    "no_albumartist": ("Sem artista do álbum", "Falta ALBUMARTIST."),
    "no_date": ("Sem data", "Falta a data/ano de lançamento."),
    "no_track_number": ("Sem número da faixa", "Falta TRACKNUMBER."),
    "no_title": ("Sem título", "Falta o título da faixa."),
    "no_artist": ("Sem artista", "Falta o artista da faixa."),
}

_TRACK_ID = re.compile(r"^\d+$")
_ALBUM_ID = re.compile(r"^[0-9A-Za-z]+$")

CSV_COLUMNS = [
    "pasta",
    "arquivo",
    "artista",
    "album",
    "titulo",
    "capa_largura",
    "capa_altura",
    "id_faixa",
    "id_album",
    "problemas",
]


# --------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------
def _first(values) -> str:
    if not values:
        return ""
    value = values[0] if isinstance(values, (list, tuple)) else values
    return str(value).strip()


def _read_flac(path: str):
    from mutagen.flac import FLAC

    audio = FLAC(path)

    def get(*keys):
        for key in keys:
            value = _first(audio.get(key))
            if value:
                return value
        return ""

    fields = {
        "artist": get("ARTIST"),
        "albumartist": get("ALBUMARTIST", "ALBUM ARTIST"),
        "album": get("ALBUM"),
        "title": get("TITLE"),
        "date": get("DATE", "YEAR", "ORIGINALDATE"),
        "tracknumber": get("TRACKNUMBER"),
        "isrc": get("ISRC"),
    }
    covers = [bytes(picture.data) for picture in audio.pictures]
    return fields, covers


def _read_mp3(path: str):
    import mutagen.id3 as id3

    try:
        tags = id3.ID3(path)
    except id3.ID3NoHeaderError:
        tags = None

    def text(frame_id):
        if tags is None:
            return ""
        frame = tags.get(frame_id)
        return _first(frame.text) if frame is not None and frame.text else ""

    isrc = text("TSRC")
    fields = {
        "artist": text("TPE1"),
        "albumartist": text("TPE2"),
        "album": text("TALB"),
        "title": text("TIT2"),
        "date": text("TDRC") or text("TYER") or text("TDOR"),
        "tracknumber": text("TRCK"),
        "isrc": isrc,
    }
    covers = [bytes(frame.data) for frame in tags.getall("APIC")] if tags else []
    return fields, covers


def _cover_size(covers: list):
    """(largura, altura) da maior capa embutida, ou None se nenhuma é legível."""
    from qobuz_dl.metadata import _image_dimensions

    best = None
    for data in covers:
        size = _image_dimensions(data)
        if size and (best is None or size[0] * size[1] > best[0] * best[1]):
            best = size
    return best


def diagnose(
    fields: dict,
    track_id: str,
    album_id: str,
    has_cover: bool,
    cover_size: Optional[tuple],
) -> list:
    """Códigos de ISSUES para os campos de UMA faixa (compartilhado com `inspect`).

    fields: artist, albumartist, album, title, date, tracknumber, isrc (textos,
    vazio = ausente). cover_size: (largura, altura) da maior capa ou None.
    """
    issues = []
    if not has_cover:
        issues.append("no_cover")
    elif cover_size:
        if min(cover_size) < MIN_COVER_SIDE:
            issues.append("small_cover")
        if abs(cover_size[0] - cover_size[1]) > 0.03 * max(cover_size):
            issues.append("bad_shape_cover")
    if not track_id:
        issues.append("no_track_id")
    elif not _TRACK_ID.match(track_id):
        issues.append("bad_track_id")
    if not album_id:
        issues.append("no_album_id")
    elif not _ALBUM_ID.match(album_id):
        issues.append("bad_album_id")
    for key, code in (
        ("isrc", "no_isrc"),
        ("albumartist", "no_albumartist"),
        ("date", "no_date"),
        ("tracknumber", "no_track_number"),
        ("title", "no_title"),
        ("artist", "no_artist"),
    ):
        if not fields.get(key):
            issues.append(code)
    return issues


def inspect_file(path: str) -> dict:
    """Lê um arquivo e devolve seus campos e a lista de problemas (códigos)."""
    folder = os.path.dirname(path)
    record = {
        "path": path,
        "folder": folder,
        "file": os.path.basename(path),
        "artist": "",
        "album": "",
        "title": "",
        "cover": None,
        "track_id": "",
        "album_id": "",
        "issues": [],
    }
    extension = os.path.splitext(path)[1].lower()
    try:
        fields, covers = _read_flac(path) if extension == ".flac" else _read_mp3(path)
    except Exception as exc:  # noqa: BLE001 -- um arquivo ruim não derruba o relatório
        logger.debug("Não foi possível ler %s: %s", path, exc)
        record["issues"].append("unreadable")
        return record

    track_id = extract_track_id(path) or ""
    album_id = extract_album_id(path) or ""
    record.update(
        artist=fields["albumartist"] or fields["artist"],
        album=fields["album"],
        title=fields["title"],
        track_id=track_id,
        album_id=album_id,
    )

    size = _cover_size(covers) if covers else None
    record["cover"] = size
    record["issues"] += diagnose(fields, track_id, album_id, bool(covers), size)
    return record


def scan_directory(
    directory: str, progress: Optional[Callable[[int, int], None]] = None
) -> list:
    """`inspect_file` em todos os FLAC/MP3 da pasta."""
    files = [
        p for p in find_audio_files(directory) if p.lower().endswith(AUDIO_EXTENSIONS)
    ]
    records = []
    for index, path in enumerate(files, 1):
        records.append(inspect_file(path))
        if progress and (index % 100 == 0 or index == len(files)):
            progress(index, len(files))
    return records


# --------------------------------------------------------------------------
# Agregação
# --------------------------------------------------------------------------
def summarize(records: list) -> dict:
    """Totais por problema e por álbum (pasta), do pior para o melhor."""
    per_issue = Counter()
    albums = defaultdict(list)
    for record in records:
        per_issue.update(record["issues"])
        albums[record["folder"]].append(record)

    rows = []
    for folder, items in albums.items():
        counts = Counter()
        for item in items:
            counts.update(item["issues"])
        first = items[0]
        rows.append(
            {
                "folder": folder,
                "artist": first["artist"],
                "album": first["album"],
                "tracks": len(items),
                "with_issues": sum(1 for item in items if item["issues"]),
                "counts": counts,
                "records": items,
            }
        )
    rows.sort(
        key=lambda row: (
            -row["with_issues"],
            -sum(row["counts"].values()),
            row["folder"],
        )
    )
    return {
        "total": len(records),
        "with_issues": sum(1 for record in records if record["issues"]),
        "per_issue": per_issue,
        "albums": rows,
    }


# --------------------------------------------------------------------------
# Saída
# --------------------------------------------------------------------------
def _issue_labels(codes: list) -> str:
    return "; ".join(ISSUES[code][0] for code in codes)


def to_csv(records: list, root: str = "") -> str:
    """CSV (uma linha por arquivo COM problema), com cabeçalho."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for record in records:
        if not record["issues"]:
            continue
        cover = record["cover"] or ("", "")
        folder = os.path.relpath(record["folder"], root) if root else record["folder"]
        writer.writerow(
            [
                folder,
                record["file"],
                record["artist"],
                record["album"],
                record["title"],
                cover[0],
                cover[1],
                record["track_id"],
                record["album_id"],
                _issue_labels(record["issues"]),
            ]
        )
    return buffer.getvalue()


_CSS = """
:root{--bg:#fff;--fg:#1c1c1e;--muted:#6b6b70;--card:#f4f4f6;--line:#dcdce0;--bad:#c0392b;--ok:#1e8449;--warn:#b9770e}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#eee;--muted:#9a9aa0;--card:#232326;--line:#38383c;--bad:#ff7b6b;--ok:#5fd08a;--warn:#f0b24a}}
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif;max-width:1100px;margin-inline:auto}
h1{font-size:1.4rem;margin:.2rem 0}h2{font-size:1.1rem;margin:1.6rem 0 .6rem}
.muted{color:var(--muted)}.cards{display:flex;flex-wrap:wrap;gap:10px}
.card{background:var(--card);border-radius:10px;padding:10px 14px;min-width:150px}
.card b{display:block;font-size:1.5rem}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.8rem;text-transform:uppercase;color:var(--muted)}td.n{text-align:right;white-space:nowrap}
.bad{color:var(--bad)}.ok{color:var(--ok)}.tag{display:inline-block;background:var(--card);border-radius:6px;padding:1px 7px;margin:1px 3px 1px 0;font-size:.8rem}
details{margin:.3rem 0}summary{cursor:pointer}input{width:100%;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);margin:.5rem 0}
.path{word-break:break-all;font-size:.8rem}
"""

_JS = """
const q=document.getElementById('q');q&&q.addEventListener('input',()=>{
const t=q.value.toLowerCase();document.querySelectorAll('[data-album]').forEach(e=>{
e.style.display=e.dataset.album.includes(t)?'':'none'})});
"""


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def to_html(records: list, root: str = "") -> str:
    """Relatório HTML autossuficiente (CSS/JS embutidos, sem rede)."""
    summary = summarize(records)
    total, bad = summary["total"], summary["with_issues"]
    parts = [
        "<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>Relatório da biblioteca</title>",
        f"<style>{_CSS}</style></head><body>",
        "<h1>Relatório da biblioteca</h1>",
        f"<p class='muted'>{_esc(root)} · gerado em "
        f"{_esc(time.strftime('%d/%m/%Y %H:%M'))}</p>",
        "<div class='cards'>",
        f"<div class='card'><b>{total}</b>faixas analisadas</div>",
        f"<div class='card'><b class='{'bad' if bad else 'ok'}'>{bad}</b>faixas com problemas</div>",
        f"<div class='card'><b>{total - bad}</b>faixas sem problemas</div>",
        f"<div class='card'><b>{len(summary['albums'])}</b>pastas/álbuns</div>",
        "</div>",
    ]

    parts.append("<h2>Problemas encontrados</h2>")
    if summary["per_issue"]:
        parts.append(
            "<table><tr><th>Problema</th><th>Faixas</th><th>O que fazer</th></tr>"
        )
        for code, count in summary["per_issue"].most_common():
            label, hint = ISSUES[code]
            parts.append(
                f"<tr><td>{_esc(label)}</td><td class='n bad'>{count}</td>"
                f"<td class='muted'>{_esc(hint)}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p class='ok'>Nenhum problema encontrado.</p>")

    parts.append("<h2>Por álbum</h2>")
    parts.append(
        "<input id='q' type='search' placeholder='Filtrar por artista, álbum ou pasta…'>"
    )
    for row in summary["albums"]:
        name = (
            " — ".join(part for part in (row["artist"], row["album"]) if part)
            or "(sem tags)"
        )
        folder = os.path.relpath(row["folder"], root) if root else row["folder"]
        key = f"{name} {folder}".lower()
        state = "ok" if not row["with_issues"] else "bad"
        parts.append(f"<details data-album='{_esc(key)}'><summary>")
        parts.append(
            f"<b>{_esc(name)}</b> "
            f"<span class='{state}'>{row['with_issues']}/{row['tracks']} faixas com problema</span>"
        )
        for code, count in row["counts"].most_common():
            parts.append(f"<span class='tag'>{_esc(ISSUES[code][0])} ×{count}</span>")
        parts.append("</summary>")
        parts.append(f"<div class='path muted'>{_esc(folder)}</div>")
        if row["with_issues"]:
            parts.append("<table><tr><th>Arquivo</th><th>Problemas</th></tr>")
            for record in row["records"]:
                if record["issues"]:
                    parts.append(
                        f"<tr><td class='path'>{_esc(record['file'])}</td>"
                        f"<td>{_esc(_issue_labels(record['issues']))}</td></tr>"
                    )
            parts.append("</table>")
        parts.append("</details>")
    parts.append(f"<script>{_JS}</script></body></html>")
    return "".join(parts)


def write_report(records: list, destination: str, root: str = "") -> str:
    """Grava o relatório em `destination` (CSV se terminar em .csv, senão HTML)."""
    destination = os.path.abspath(os.path.expanduser(destination))
    folder = os.path.dirname(destination)
    os.makedirs(folder, exist_ok=True)
    as_csv = destination.lower().endswith(".csv")
    content = to_csv(records, root) if as_csv else to_html(records, root)
    # utf-8-sig no CSV: o Excel reconhece os acentos.
    with open(
        destination, "w", encoding="utf-8-sig" if as_csv else "utf-8", newline=""
    ) as handle:
        handle.write(content)
    return destination
