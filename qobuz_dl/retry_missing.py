# ==============================================================================
# retry_missing.py
# ------------------------------------------------------------------------------
# Comando `qobuz-dl retry [PASTA]`: repete as faixas que ficaram pendentes em
# álbuns já baixados (puladas por indisponibilidade/erro de API, ou que falharam).
#
# De onde vem a lista: do `.report.json` que cada álbum guarda na própria pasta
# (ver postprocess.py). Ele tem o ID do álbum e a situação de cada faixa, então
# não é preciso gravar nada novo nos `.missing.txt`. Pastas sem `.report.json`
# (baixadas por versões antigas) ficam de fora.
#
# O download em si é o de álbum de sempre (QobuzDL.retry_album): ele grava na
# mesma pasta, mexe só nas faixas pendentes e não imprime o resumo do álbum.
# Aqui ficam só a descoberta das pendências e o relatório do que mudou.
# ==============================================================================

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from qobuz_dl import postprocess, ui
from qobuz_dl.download_utils import remove_missing_placeholder

if TYPE_CHECKING:
    from qobuz_dl.core import QobuzDL

# Pastas que estão sendo baixadas agora: não mexer.
_IN_PROGRESS_PREFIX = "[IN PROGRESS]"
_INCOMPLETE_PREFIX = "[INCOMPLETE] "


@dataclass
class PendingTrack:
    id: str
    numero: str
    titulo: str
    situacao: str  # "pulada" | "falha" | "pendente"
    motivo: str = ""


@dataclass
class PendingAlbum:
    folder: str
    album_id: str
    title: str
    tracks: list[PendingTrack] = field(default_factory=list)


def read_pending(folder: str) -> PendingAlbum | None:
    """Pendências do álbum cuja pasta é `folder`; None se não houver nenhuma.

    Lê o `.report.json` da pasta. Ignora relatórios que não são de álbum (as
    playlists têm o delas) e faixas já concluídas.
    """
    report_path = os.path.join(folder, postprocess.REPORT_FILENAME)
    if not os.path.isfile(report_path):
        return None
    report = postprocess._load_report(report_path)
    # `_load_report` sempre devolve dict (vazio se ilegivel).
    if report.get("tipo") != "album":
        return None
    ident = report.get("identificacao")
    ident = ident if isinstance(ident, dict) else {}
    album_id = postprocess._norm_id(ident.get("id"))
    faixas = report.get("faixas")
    if not album_id or not isinstance(faixas, list):
        return None

    pendentes: list[PendingTrack] = []
    for faixa in faixas:
        if not isinstance(faixa, dict):
            continue
        download = faixa.get("download")
        download = download if isinstance(download, dict) else {}
        situacao = postprocess._normalizar_status(download.get("situacao", ""))
        track_id = postprocess._norm_id(faixa.get("id"))
        if situacao == "concluido" or not track_id:
            continue
        faixa_ident = faixa.get("identificacao")
        faixa_ident = faixa_ident if isinstance(faixa_ident, dict) else {}
        pendentes.append(
            PendingTrack(
                id=track_id,
                numero=str(faixa.get("numero") or ""),
                titulo=str(faixa_ident.get("titulo") or "Faixa"),
                situacao=str(situacao),
                motivo=str(download.get("motivo") or ""),
            )
        )
    if not pendentes:
        return None
    return PendingAlbum(
        folder=folder,
        album_id=album_id,
        title=str(ident.get("titulo") or os.path.basename(folder)),
        tracks=pendentes,
    )


def find_pending(root: str) -> list[PendingAlbum]:
    """Todos os álbuns com faixas pendentes dentro de `root` (recursivo)."""
    found: list[PendingAlbum] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(_IN_PROGRESS_PREFIX))
        if postprocess.REPORT_FILENAME not in files:
            continue
        pending = read_pending(current)
        if pending is not None:
            found.append(pending)
    return found


def album_folder_for_retry(folder: str) -> str:
    """Nome de pasta "limpo" do álbum (sem o prefixo [INCOMPLETE]).

    O downloader renomeia a pasta para [IN PROGRESS] durante o trabalho e a
    devolve ao nome normal no fim; ele parte do nome sem prefixo.
    """
    base_path, name = os.path.split(folder.rstrip("/\\"))
    if name.startswith(_INCOMPLETE_PREFIX):
        name = name[len(_INCOMPLETE_PREFIX) :]
    return os.path.join(base_path, name)


_STATUS_LABEL = {
    "pulada": "indisponível",
    "falha": "falhou",
    "pendente": "pendente",
}


async def retry_album_folder(app: QobuzDL, pending: PendingAlbum) -> dict[str, int]:
    """Repete as faixas pendentes de UM álbum e mostra o resultado.

    Devolve contagens: baixadas, indisponiveis (seguem puladas), falhas.
    """
    started = time.monotonic()
    ui.header(
        "REPETIR PULADAS",
        [
            ("Pasta", os.path.basename(pending.folder.rstrip("/\\"))),
            ("Pendentes", str(len(pending.tracks))),
        ],
    )
    ids = [t.id for t in pending.tracks]
    await app.retry_album(pending.album_id, album_folder_for_retry(pending.folder), ids)

    # A pasta pode ter mudado de nome ([INCOMPLETE] -> nome final): relê pelo
    # caminho limpo e, se não achar, pelo original.
    clean = album_folder_for_retry(pending.folder)
    folder_now = clean if os.path.isdir(clean) else pending.folder
    after = await asyncio.to_thread(read_pending, folder_now)
    still = {t.id: t for t in (after.tracks if after else [])}

    counts = {"baixadas": 0, "indisponiveis": 0, "falhas": 0}
    for track in pending.tracks:
        label = f"{track.numero}. {track.titulo}".strip(". ")
        remaining = still.get(track.id)
        if remaining is None:
            counts["baixadas"] += 1
            await asyncio.to_thread(
                remove_missing_placeholder, folder_now, track.titulo
            )
            ui.ok(f"{label} (baixada)")
        elif remaining.situacao == "falha":
            counts["falhas"] += 1
            ui.error(f"{label} ({_STATUS_LABEL['falha']})")
        else:
            counts["indisponiveis"] += 1
            ui.skip(f"{label} ({_STATUS_LABEL.get(remaining.situacao, 'pendente')})")

    rows = [
        (
            "Baixadas",
            f"{counts['baixadas']}/{len(pending.tracks)}",
            "ok" if counts["baixadas"] else ("err" if counts["falhas"] else "info"),
        )
    ]
    if counts["indisponiveis"]:
        rows.append(("Seguem indisponíveis", str(counts["indisponiveis"]), "warn"))
    if counts["falhas"]:
        rows.append(("Falhas", str(counts["falhas"]), "err"))
    ui.summary(
        "RESUMO DAS PENDENTES",
        rows,
        subtitle=pending.title,
        elapsed=time.monotonic() - started,
    )
    return counts


async def run_retry(app: QobuzDL, root: str) -> int:
    """Executa o `retry` em `root`. Código de saída: 0 sem falhas, 1 com falhas."""
    root = os.path.expanduser(root)
    if not os.path.isdir(root):
        ui.error(f"Pasta não encontrada: {root}")
        return 2
    albums = await asyncio.to_thread(find_pending, root)
    if not albums:
        ui.ok("Nenhuma faixa pendente encontrada nesta pasta.")
        return 0
    total_falhas = 0
    for pending in albums:
        counts = await retry_album_folder(app, pending)
        total_falhas += counts["falhas"]
    return 1 if total_falhas else 0
