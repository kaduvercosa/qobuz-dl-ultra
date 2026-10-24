"""Revisão manual de artistas seguidos.

MÓDULO: sincroniza artistas e lançamentos conhecidos no ``library.db`` e permite
que a pessoa marque o que já conferiu diretamente no Qobuz. A criação da
playlist via Atalhos e o estado de conferência são etapas independentes; a
checklist do Apple Notas pode espelhar os estados no banco local.
ONDE PROCURAR: parser em ``commands.py``, roteamento em ``cli.py``, a tela com
checkbox em ``artist_checklist.py`` e persistência em ``library_db.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import shlex
import sys
from typing import Any, Callable, Optional
import webbrowser

from prompt_toolkit import PromptSession

from qobuz_dl import ui
from qobuz_dl.artist_checklist import (
    ACTION_CONFIRM,
    ChecklistState,
    release_lines,
    roster_lines,
    row_from_artist,
    run_checklist,
)
from qobuz_dl.color import GRAY, HIGHLIGHT, RESET, SUCCESS, WARNING
from qobuz_dl.library_db import LibraryDB

SOURCE = "qobuz"
PAGE_SIZE = 500
MAX_PAGES = 400

logger = logging.getLogger(__name__)


def _read_checklist(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return handle.read()


def parse_notes_checklist(text: str, *, allow_empty: bool = False) -> list[dict]:
    """Parseia itens do Apple Notas no formato ``- [x] Nome - [QOBUZ_ID:123] - URL``.

    Linhas sem checkbox/QOBUZ_ID são ignoradas; IDs duplicados com estados
    conflitantes são rejeitados para evitar uma sincronização ambígua.
    """
    parsed: dict[str, dict] = {}
    pattern = re.compile(
        r"^\s*-\s*\[([ xX])\]\s*(.*?)\s*-\s*"
        r"\[QOBUZ_ID:(\d+)\](?:\s*-\s*(https?://\S+))?\s*$"
    )
    for line_number, line in enumerate(text.splitlines(), start=1):
        match = pattern.match(line)
        if not match:
            continue
        marker, name, artist_id, url = match.groups()
        artist_id = str(int(artist_id))
        item = {
            "source_artist_id": artist_id,
            "name": name.strip(),
            "verified": marker.lower() == "x",
            "url": url or f"https://open.qobuz.com/artist/{artist_id}",
            "line": line_number,
        }
        previous = parsed.get(artist_id)
        if previous and previous["verified"] != item["verified"]:
            raise ValueError(
                f"QOBUZ_ID {artist_id} aparece marcado e desmarcado "
                f"nas linhas {previous['line']} e {line_number}"
            )
        parsed[artist_id] = item
    if not parsed and not allow_empty:
        raise ValueError(
            "nenhum item válido com [QOBUZ_ID:...] foi encontrado na checklist"
        )
    return list(parsed.values())


def _note_line(artist: dict, verified: bool) -> str:
    """Linha da Nota para um artista: ``- [x] Nome - [QOBUZ_ID:1] - URL``."""
    artist_id = str(artist["source_artist_id"])
    name = str(artist.get("name") or "Unknown")
    url = f"https://open.qobuz.com/artist/{artist_id}"
    return f"- [{'x' if verified else ' '}] {name} - [QOBUZ_ID:{artist_id}] - {url}"


def _normalize_name(name: Any) -> str:
    """Nome sem diferença de caixa/espaços, para comparar com a Nota."""
    return " ".join(str(name or "").split()).casefold()


def compute_new_followed_missing(
    qobuz_artists: list[dict], note_items: list[dict]
) -> list[dict]:
    """Artistas que estão no Qobuz mas não constam na Nota.

    Compara por ``QOBUZ_ID`` e, como reforço, pelo nome normalizado (para não
    sugerir de novo quem já está na Nota com o ID diferente/ausente). Não
    depende de o artista ser "novo" no banco local. Cada item traz a ``line``
    pronta para colar na Nota, em ordem alfabética.
    """
    note_ids = {str(item["source_artist_id"]) for item in note_items}
    note_names = {_normalize_name(item.get("name")) for item in note_items}
    found: list[dict] = []
    seen: set[str] = set()
    for artist in qobuz_artists:
        artist_id = str(artist["source_artist_id"])
        name = str(artist.get("name") or "Unknown")
        if artist_id in seen or artist_id in note_ids:
            continue
        if _normalize_name(name) in note_names:
            continue
        seen.add(artist_id)
        found.append(
            {
                "source_artist_id": artist_id,
                "name": name,
                "verified": False,
                "line": _note_line(
                    {"source_artist_id": artist_id, "name": name}, False
                ),
            }
        )
    found.sort(key=lambda row: row["name"].casefold())
    return found


async def sync_notes_checklist(
    text: str,
    lib: LibraryDB,
    *,
    source: str = SOURCE,
    mirror: bool = False,
    allow_empty: bool = False,
) -> dict:
    """Traz para o banco local o que foi marcado na checklist do Apple Notas.

    Usa a mesma regra da tela: conferido = tem data de conferência E nenhum
    lançamento novo pendente. Por padrão só MARCA (``[x]`` na Nota e ainda não
    conferido no banco); o que foi conferido no a-Shell e continua ``[ ]`` na
    Nota é mantido e listado em ``kept_verified``. Com ``mirror=True`` a Nota
    manda e esses artistas são reabertos. ``[x]`` na Nota de quem ganhou
    lançamento novo não é remarcado (apagaria as novidades sem você ver):
    vai em ``needs_review``. ``missing`` traz as linhas, prontas para colar na
    Nota, dos seguidos que ainda não estão nela.

    Não cria playlists nem acessa a API do Qobuz. IDs fora da lista local de
    seguidos são reportados em ``unknown`` e ignorados.
    """
    items = parse_notes_checklist(text, allow_empty=allow_empty)
    followed = await asyncio.to_thread(lib.get_followed_artists, source)
    by_id = {str(row["source_artist_id"]): row for row in followed}
    marked: list[dict] = []
    unmarked: list[dict] = []
    unchanged: list[dict] = []
    unknown: list[dict] = []
    verified: list[dict] = []
    pending: list[dict] = []
    kept_verified: list[dict] = []
    needs_review: list[dict] = []
    for item in items:
        artist_id = item["source_artist_id"]
        artist = by_id.get(artist_id)
        if artist is None:
            unknown.append(item)
            continue
        new_count = int(artist.get("pending_count") or 0)
        item = {**item, "pending_count": new_count}
        reviewed = bool(artist.get("last_reviewed_at"))
        if item["verified"]:
            if not reviewed:
                ok = await asyncio.to_thread(
                    lib.mark_artist_reviewed, source, artist_id
                )
                if not ok:
                    unknown.append(item)
                    continue
                marked.append(item)
                verified.append(item)
            elif new_count:
                needs_review.append(item)
                unchanged.append(item)
                pending.append(item)
            else:
                unchanged.append(item)
                verified.append(item)
        elif reviewed and not new_count:
            if mirror:
                ok = await asyncio.to_thread(
                    lib.unmark_artist_reviewed, source, artist_id
                )
                if not ok:
                    unknown.append(item)
                    continue
                unmarked.append(item)
                pending.append(item)
            else:
                kept_verified.append(item)
                unchanged.append(item)
                verified.append(item)
        else:
            unchanged.append(item)
            pending.append(item)
    in_note = {item["source_artist_id"] for item in items}
    missing = []
    for artist_id, row in by_id.items():
        if artist_id in in_note:
            continue
        is_done = bool(row.get("last_reviewed_at")) and not int(
            row.get("pending_count") or 0
        )
        missing.append(
            {
                "source_artist_id": artist_id,
                "name": str(row.get("name") or "Unknown"),
                "verified": is_done,
                "line": _note_line(row, is_done),
            }
        )
    missing.sort(key=lambda row: row["name"].casefold())
    return {
        "ok": True,
        "mirror": mirror,
        "parsed": len(items),
        "verified": verified,
        "pending": pending,
        "marked": marked,
        "unmarked": unmarked,
        "unchanged": unchanged,
        "unknown": unknown,
        "kept_verified": kept_verified,
        "needs_review": needs_review,
        "missing": missing,
        "playlists_created": 0,
    }


def _artist_data(item: Any) -> Optional[dict]:
    """Normaliza um artista retornado pelo endpoint de favoritos."""
    if not isinstance(item, dict):
        return None
    artist = item.get("artist") if isinstance(item.get("artist"), dict) else item
    artist_id = artist.get("id")
    if artist_id in (None, ""):
        return None
    return {
        "source_artist_id": str(artist_id),
        "name": str(artist.get("name") or artist.get("artist") or "Unknown"),
    }


def _release_data(item: Any) -> Optional[dict]:
    if not isinstance(item, dict) or item.get("id") in (None, ""):
        return None
    return {
        "source_release_id": str(item["id"]),
        "title": str(item.get("title") or "Unknown"),
        "release_date": str(
            item.get("release_date_original") or item.get("release_date") or ""
        )
        or None,
        "release_type": str(
            item.get("release_type") or item.get("album_type") or item.get("type") or ""
        )
        or None,
    }


async def _fetch_followed_artists(client: Any) -> list[dict]:
    """Busca e normaliza os artistas seguidos, sem consultar discografias."""
    items, api_total = await client.get_all_favorites(
        "artists", page_size=PAGE_SIZE, max_pages=MAX_PAGES
    )
    artists: list[dict] = []
    invalid = 0
    seen: set[str] = set()
    for item in items:
        artist = _artist_data(item)
        if artist is None:
            invalid += 1
            continue
        if artist["source_artist_id"] not in seen:
            seen.add(artist["source_artist_id"])
            artists.append(artist)
    if api_total is not None and len(items) < api_total:
        raise RuntimeError(
            f"lista de artistas incompleta: {len(items)} de {api_total} recebidos"
        )
    if invalid:
        raise RuntimeError(
            f"resposta de favoritos contém {invalid} artista(s) sem ID; "
            "o catálogo não foi alterado"
        )
    return artists


async def refresh_artist_catalog(
    client: Any,
    lib: LibraryDB,
    *,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """Sincroniza favoritos de artistas e as discografias consultadas com sucesso.

    O endpoint de favoritos é paginado estritamente pelo cliente. Uma consulta
    individual de discografia que falhar não apaga lançamentos existentes nem
    atualiza a data de sincronização daquele artista.
    """
    if progress:
        progress(
            "Consultando artistas seguidos no Qobuz; a primeira resposta pode demorar..."
        )
    artists = await _fetch_followed_artists(client)

    await asyncio.to_thread(lib.sync_followed_artists, SOURCE, artists)
    if progress:
        progress(
            f"{len(artists)} artista(s) encontrado(s); sincronizando discografias..."
        )
    releases_new = 0
    failures: list[dict] = []
    for index, artist in enumerate(artists, start=1):
        if progress:
            progress(
                f"Discografia {index}/{len(artists)}: {artist['name']} "
                "(a consulta pode levar alguns segundos)..."
            )
        artist_releases: list[dict] = []
        try:
            async for chunk in client.get_artist_meta(artist["source_artist_id"]):
                if not isinstance(chunk, dict):
                    raise ValueError("resposta de discografia inválida")
                album_block = chunk.get("albums")
                if album_block is None:
                    continue
                if not isinstance(album_block, dict):
                    raise ValueError("bloco de álbuns inválido na discografia")
                page = album_block.get("items") or []
                if not isinstance(page, list):
                    raise ValueError("itens inválidos na discografia")
                artist_releases.extend(
                    parsed
                    for parsed in (_release_data(item) for item in page)
                    if parsed is not None
                )
            new_count = await asyncio.to_thread(
                lib.record_artist_releases,
                SOURCE,
                artist["source_artist_id"],
                artist_releases,
            )
            releases_new += new_count
        except Exception as exc:
            logger.warning(
                "Não foi possível atualizar a discografia de %s (%s): %s",
                artist["name"],
                artist["source_artist_id"],
                exc,
            )
            failures.append(
                {
                    "artist_id": artist["source_artist_id"],
                    "name": artist["name"],
                    "error": str(exc),
                }
            )
    return {
        "artists": len(artists),
        "releases_new": releases_new,
        "failures": failures,
    }


def _status(artist: dict) -> str:
    cutoff = artist.get("last_reviewed_at") or artist.get("tracking_since_at")
    cutoff_date = str(cutoff or "")[:10]
    if int(artist.get("pending_count") or 0):
        since = f" desde {cutoff_date}" if cutoff_date else ""
        return f"[ ] NOVOS: {artist['pending_count']}{since}"
    if artist.get("last_reviewed_at"):
        reviewed_date = str(artist["last_reviewed_at"])[:10]
        return f"[x] CONFERIDO em {reviewed_date}"
    if cutoff_date:
        return f"[ ] NÃO VERIFICADO desde {cutoff_date}"
    return "[ ] NÃO VERIFICADO"


def _open_profile_url(url: str) -> bool:
    """Abre um perfil; no a-Shell, delega ao comando nativo ``open``."""
    try:
        if sys.platform == "ios":
            return os.system(f"open {shlex.quote(url)}") == 0
        return bool(webbrowser.open(url))
    except Exception:
        logger.exception("Não foi possível abrir o perfil Qobuz: %s", url)
        return False


def _hint(text: str, columns: int) -> list[str]:
    """Dica em cinza, quebrada para caber na tela."""
    c, rst = ui.c(GRAY), ui.c(RESET)
    return [f"{c}{part}{rst}" for part in ui._wrap_lines(text, max(10, columns - 1))]


def _show_roster(artists: list[dict]) -> None:
    if not artists:
        ui.info("Nenhum artista seguido foi encontrado na conta Qobuz.")
        return
    rows = [row_from_artist(artist) for artist in artists]
    columns = ui.raw_width()
    done = sum(1 for row in rows if row.verified)
    title = f"=== ARTISTAS SEGUIDOS · {done}/{len(rows)} conferidos ==="
    lines = ["", f"{ui.c(HIGHLIGHT)}{ui.truncate(title, columns - 1)}{ui.c(RESET)}"]
    lines.extend(roster_lines(rows, columns))
    lines.extend(
        _hint("o N abre o perfil · m 1,3 confirma e cria a playlist · q sai", columns)
    )
    ui.emit("\n".join(lines))


def _status_line(artist: dict) -> str:
    """Estado do artista, colorido: verde conferido, amarelo novos, cinza o resto."""
    text = _status(artist)
    if int(artist.get("pending_count") or 0):
        color = WARNING
    elif artist.get("last_reviewed_at"):
        color = SUCCESS
    else:
        color = GRAY
    return f"{ui.c(color)}{text}{ui.c(RESET)}"


def _show_artist(artist: dict, releases: list[dict]) -> None:
    columns = ui.raw_width()
    c, rst = ui.c, ui.c(RESET)
    url = f"open.qobuz.com/artist/{artist['source_artist_id']}"
    lines = [
        "",
        f"{c(HIGHLIGHT)}=== ARTISTA SORTEADO ==={rst}",
        f"{c(HIGHLIGHT)}{ui.truncate(artist['name'], columns - 1)}{rst}",
        _status_line(artist),
        f"{c(GRAY)}Perfil: {url}{rst}",
        "",
    ]
    lines.extend(release_lines(releases, columns))
    lines.extend(
        _hint("o abre o perfil · m confirma e cria a playlist · v volta", columns)
    )
    ui.emit("\n".join(lines))


def playlist_name_for(artist_name: Any) -> str:
    """Nome da playlist de controle: o do artista em MAIÚSCULAS."""
    return " ".join(str(artist_name or "").split()).upper() or "UNKNOWN"


async def _existing_playlists(client: Any) -> dict:
    """Playlists da conta: por nome (sem diferenciar caixa) e por id.

    ``ok`` é False quando a listagem falhou; nesse caso quem chama não deve
    concluir que uma playlist deixou de existir.
    """
    found: dict = {"by_name": {}, "ids": set(), "ok": False}
    try:
        response = await client.get_user_playlists(limit=500)
        items = response["playlists"]["items"]
    except Exception as exc:
        logger.warning("Não foi possível listar as playlists existentes: %s", exc)
        return found
    found["ok"] = True
    for item in items:
        if isinstance(item, dict) and item.get("id") not in (None, ""):
            key = str(item.get("name") or "").strip().casefold()
            found["by_name"].setdefault(key, str(item["id"]))
            found["ids"].add(str(item["id"]))
    return found


async def ensure_artist_playlist(
    client: Any,
    name: str,
    *,
    stored_id: Any = None,
    existing: Optional[dict] = None,
) -> tuple[str, bool]:
    """Devolve ``(id_da_playlist, reaproveitada)`` para o artista.

    Ordem: id guardado no banco (se a playlist ainda existe), playlist de mesmo
    nome na conta e, por último, cria uma nova (vazia e privada). Levanta
    ``RuntimeError`` se o Qobuz não confirmar a criação.
    """
    existing = existing if existing is not None else await _existing_playlists(client)
    stored = str(stored_id) if stored_id not in (None, "") else ""
    # Se a listagem falhou, confia no id guardado em vez de arriscar duplicar.
    if stored and (stored in existing["ids"] or not existing["ok"]):
        return stored, True
    found = existing["by_name"].get(name.casefold())
    if found:
        return found, True
    playlist_id = await client.create_qobuz_playlist(
        name=name, description="", is_public=False
    )
    if not playlist_id:
        raise RuntimeError("o Qobuz não confirmou a criação da playlist")
    existing["by_name"][name.casefold()] = str(playlist_id)
    existing["ids"].add(str(playlist_id))
    return str(playlist_id), False


async def confirm_artists(
    artists: list[dict],
    lib: LibraryDB,
    client: Any,
    *,
    source: str = SOURCE,
    progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """Confirma artistas: garante a playlist em maiúsculas e grava no banco.

    A conferência só é gravada depois que a playlist existe (criada agora ou já
    presente na conta); o id da playlist fica guardado no banco. Se criar a
    playlist falhar, o artista continua pendente.
    """
    existing = await _existing_playlists(client) if artists else {}
    confirmed: list[dict] = []
    failed: list[dict] = []
    created = reused = 0
    for index, artist in enumerate(artists, start=1):
        name = playlist_name_for(artist.get("name"))
        if progress:
            progress(f"Playlist {index}/{len(artists)}: {name}...")
        try:
            playlist_id, was_reused = await ensure_artist_playlist(
                client,
                name,
                stored_id=artist.get("review_playlist_id"),
                existing=existing,
            )
        except Exception as exc:
            error = str(exc) or "falha ao criar a playlist"
            failed.append({"artist": artist, "error": error})
            continue
        if was_reused:
            reused += 1
        else:
            created += 1
        artist_id = artist["source_artist_id"]
        await asyncio.to_thread(
            lib.set_artist_review_playlist, source, artist_id, playlist_id
        )
        ok = await asyncio.to_thread(lib.mark_artist_reviewed, source, artist_id)
        if ok:
            confirmed.append({**artist, "review_playlist_id": playlist_id})
        else:
            failed.append(
                {"artist": artist, "error": "o artista não consta mais como seguido"}
            )
    return {
        "confirmed": confirmed,
        "failed": failed,
        "created": created,
        "reused": reused,
    }


async def undo_artists(
    artists: list[dict], lib: LibraryDB, *, source: str = SOURCE
) -> dict:
    """Desfaz a conferência dos artistas; as playlists no Qobuz não são apagadas."""
    undone: list[dict] = []
    failed: list[dict] = []
    for artist in artists:
        ok = await asyncio.to_thread(
            lib.unmark_artist_reviewed, source, artist["source_artist_id"]
        )
        if ok:
            undone.append(artist)
        else:
            failed.append({"artist": artist, "error": "o artista não está conferido"})
    return {"undone": undone, "failed": failed}


def _summary_line(result: dict, undo: Optional[dict] = None) -> str:
    parts = []
    if result["confirmed"] or not undo:
        text = f"Conferidos: {len(result['confirmed'])}"
        extra = []
        if result["created"]:
            extra.append(f"{result['created']} playlist(s) criada(s)")
        if result["reused"]:
            extra.append(f"{result['reused']} já existia(m)")
        if extra:
            text += " (" + ", ".join(extra) + ")"
        parts.append(text)
    if undo and undo["undone"]:
        parts.append(f"Desfeitos: {len(undo['undone'])}")
    failures = list(result["failed"]) + (list(undo["failed"]) if undo else [])
    if failures:
        names = ", ".join(item["artist"]["name"] for item in failures)
        parts.append(f"Falhou: {names}")
    return ". ".join(parts) + "."


def _report(result: dict, undo: Optional[dict] = None) -> None:
    """Resultado de uma confirmação: verde se tudo certo, aviso por falha."""
    if result["confirmed"]:
        ui.ok(_summary_line({**result, "failed": []}))
    if undo and undo["undone"]:
        ui.ok(f"Desfeitos: {len(undo['undone'])} (as playlists continuam no Qobuz).")
    for item in list(result["failed"]) + (list(undo["failed"]) if undo else []):
        ui.warn(f"{item['artist']['name']}: {item['error']}.")


class _SelectionStore:
    """Guarda em arquivo as marcações ainda não confirmadas.

    No iOS o a-Shell pode ser encerrado quando você abre o Safari para conferir
    um perfil; sem isso, as marcações feitas até ali seriam perdidas.
    """

    def __init__(self, path: Optional[str]) -> None:
        self.path = path

    def load(self) -> list[str]:
        if not self.path:
            return []
        try:
            with open(self.path, encoding="utf-8") as handle:
                data = json.load(handle)
            return [str(item) for item in data.get("selected", [])]
        except (OSError, ValueError, AttributeError, TypeError):
            return []

    def save(self, ids: list[str]) -> None:
        if not self.path:
            return
        try:
            if not ids:
                if os.path.exists(self.path):
                    os.remove(self.path)
                return
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"selected": list(ids)}, handle)
            os.replace(tmp, self.path)
        except OSError as exc:
            logger.debug("Não foi possível salvar as marcações: %s", exc)


def _selection_path(lib: LibraryDB) -> Optional[str]:
    path = getattr(lib, "path", None)
    if not path:
        return None
    folder = os.path.dirname(os.path.abspath(path))
    return os.path.join(folder, "artist_review_selection.json")


async def _review_one(
    artist: dict,
    lib: LibraryDB,
    session: Any,
    *,
    client: Any = None,
    source: str = SOURCE,
    url_opener: Callable[[str], bool] = _open_profile_url,
) -> bool:
    releases = await asyncio.to_thread(
        lib.get_artist_releases,
        source,
        artist["source_artist_id"],
        pending_only=True,
    )
    _show_artist(artist, releases)
    profile_url = f"https://open.qobuz.com/artist/{artist['source_artist_id']}"
    while True:
        try:
            answer = (await session.prompt_async("Sua escolha: ")).strip().casefold()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in {"o", "abrir"}:
            opened = await asyncio.to_thread(url_opener, profile_url)
            if opened:
                ui.ok(f"Perfil aberto: {artist['name']}.")
            else:
                ui.warn(f"Não foi possível abrir o perfil: {profile_url}")
            continue
        if answer in {"m", "marcar", "s", "sim", "r", "revisado"}:
            result = await confirm_artists([artist], lib, client, source=source)
            if result["confirmed"]:
                playlist = playlist_name_for(artist["name"])
                ui.ok(f"Conferido: {artist['name']} (playlist {playlist}).")
                return True
            ui.warn(f"Não foi possível confirmar: {result['failed'][0]['error']}.")
            return False
        if answer in {"v", "voltar", "q", "sair"}:
            return False
        ui.warn("Escolha o, m ou v.")


async def _load_pending_releases(
    lib: LibraryDB, artists: list[dict]
) -> dict[str, list[dict]]:
    """Lançamentos pendentes por artista, para a tela de detalhe."""
    found: dict[str, list[dict]] = {}
    for artist in artists:
        if int(artist.get("pending_count") or 0) > 0:
            found[str(artist["source_artist_id"])] = await asyncio.to_thread(
                lib.get_artist_releases,
                SOURCE,
                artist["source_artist_id"],
                pending_only=True,
            )
    return found


async def _checklist_loop(
    artists: list[dict],
    lib: LibraryDB,
    qobuz: Any,
    runner: Callable[..., Any],
    url_opener: Callable[[str], bool],
) -> bool:
    """Roda a tabela com checkbox até a pessoa sair. False = tela indisponível."""
    state = ChecklistState(artists, releases=await _load_pending_releases(lib, artists))
    store = _SelectionStore(_selection_path(lib))
    restored = state.restore_selected(store.load())
    if restored:
        state.message = f"{restored} marcação(ões) restaurada(s) da última sessão."
        store.save(state.selected_ids())
    state.on_change = lambda current: store.save(current.selected_ids())
    empty = {"confirmed": [], "failed": [], "created": 0, "reused": 0}
    while True:
        try:
            action, ids = await runner(state, url_opener=url_opener)
        except (KeyboardInterrupt, EOFError):
            return True
        except Exception as exc:
            logger.warning("Lista com checkbox indisponível: %s", exc)
            ui.warn(
                f"Lista com checkbox indisponível ({type(exc).__name__}: {exc}); "
                "usando o prompt de texto."
            )
            return False
        if action != ACTION_CONFIRM:
            store.save([])  # saiu de propósito: descarta o que não foi confirmado
            return True
        wanted, to_undo = set(ids), set(state.undo_ids())
        chosen = [a for a in artists if str(a["source_artist_id"]) in wanted]
        reopen = [a for a in artists if str(a["source_artist_id"]) in to_undo]
        result = (
            await confirm_artists(chosen, lib, qobuz.client, progress=ui.step_inline)
            if chosen
            else empty
        )
        undo = await undo_artists(reopen, lib) if reopen else None
        failed_ids = {
            str(item["artist"]["source_artist_id"]) for item in result["failed"]
        }
        undo_failed = {
            str(item["artist"]["source_artist_id"])
            for item in (undo or {}).get("failed", [])
        }
        artists = await asyncio.to_thread(lib.get_followed_artists, SOURCE)
        state.load(
            artists,
            keep_selected=failed_ids,
            keep_undo=undo_failed,
            releases=await _load_pending_releases(lib, artists),
        )
        state.message = _summary_line(result, undo)
        store.save(state.selected_ids())


async def _create_empty_artist_playlist(
    qobuz: Any, artist_id: str, lib: Optional[LibraryDB] = None
) -> dict:
    """Garante a playlist vazia do artista sem marcar a conferência.

    A playlist e o estado de revisão são etapas independentes: a pessoa cria a
    playlist pelo Atalhos e só depois marca [x] na checklist do Apple Notas.
    """
    if not artist_id.isdecimal():
        raise ValueError("o ID do artista precisa conter somente números")

    artist_result = await qobuz.client.api_call("artist/get", id=artist_id)
    if not isinstance(artist_result, dict):
        raise ValueError("a API do Qobuz não retornou os dados do artista")
    artist = artist_result.get("artist")
    if not isinstance(artist, dict):
        artist = artist_result
    artist_name = str(artist.get("name") or "").strip()
    if not artist_name:
        raise ValueError("não foi possível obter o nome do artista pelo ID informado")

    playlist_name = playlist_name_for(artist_name)
    followed_row = None
    if lib is not None:
        followed = await asyncio.to_thread(lib.get_followed_artists, SOURCE)
        followed_row = next(
            (a for a in followed if str(a["source_artist_id"]) == artist_id), None
        )
    playlist_id, reused = await ensure_artist_playlist(
        qobuz.client,
        playlist_name,
        stored_id=(followed_row or {}).get("review_playlist_id"),
    )
    if followed_row is not None:
        await asyncio.to_thread(
            lib.set_artist_review_playlist, SOURCE, artist_id, playlist_id
        )
    reviewed = False

    return {
        "ok": True,
        "artist": {
            "id": artist_id,
            "name": artist_name,
            "url": f"https://open.qobuz.com/artist/{artist_id}",
        },
        "playlist": {
            "id": str(playlist_id),
            "name": playlist_name,
            "url": f"https://play.qobuz.com/playlist/{playlist_id}",
            # Reaproveitada pode já ter músicas: só afirma o que criou agora.
            "is_empty": None if reused else True,
            "is_public": None if reused else False,
            "reused": reused,
        },
        "reviewed": reviewed,
    }


async def cmd_artist_review(
    arguments: Any,
    qobuz: Any,
    *,
    lib: Optional[LibraryDB] = None,
    session: Any = None,
    chooser: Callable[[list[dict]], dict] = random.choice,
    url_opener: Optional[Callable[[str], bool]] = None,
    checklist: Optional[Callable[..., Any]] = None,
) -> int:
    """Executa os modos alfabético ou sorteio da revisão manual.

    No modo alfabético usa a lista com checkbox (``checklist``) quando há um
    terminal interativo; sem terminal (ou se a tela falhar) cai no prompt de texto.
    A importação da checklist do Notas apenas espelha estados locais e nunca cria
    playlists; a criação via Atalhos também não marca a conferência.
    """
    interactive = session is None and sys.stdin.isatty() and sys.stdout.isatty()
    if lib is None:
        from qobuz_dl.library_cmd import library_db_path

        lib = LibraryDB(library_db_path())
    checklist_option = getattr(arguments, "sincronizar_checklist", False)
    if checklist_option:
        try:
            # ``--sincronizar-checklist`` sozinho procura tracklist-artists.txt ao
            # lado do config.ini; ``--sincronizar-checklist ARQUIVO`` usa o caminho.
            if isinstance(checklist_option, (str, os.PathLike)):
                checklist_path = os.path.expanduser(os.fspath(checklist_option))
            else:
                from qobuz_dl.utils import get_config_paths

                config_paths = get_config_paths()
                config_file = config_paths.get("config_file")
                if not config_file:
                    raise RuntimeError(
                        "não foi possível determinar o caminho do config.ini"
                    )
                checklist_path = os.path.join(
                    os.path.dirname(os.path.abspath(os.path.expanduser(config_file))),
                    "tracklist-artists.txt",
                )
            checklist_missing = not os.path.isfile(checklist_path)

            if checklist_missing:
                if not getattr(arguments, "atualizar_seguidos", False):
                    raise FileNotFoundError(
                        f"checklist não encontrada: {checklist_path}. "
                        "Use --atualizar-seguidos para inicializar a consulta."
                    )
                checklist_text = ""
            else:
                checklist_text = await asyncio.to_thread(
                    _read_checklist, checklist_path
                )

            # Lista atual do Qobuz (só consultada com --atualizar-seguidos).
            followed_now: list[dict] = []
            if getattr(arguments, "atualizar_seguidos", False):
                followed_now = await _fetch_followed_artists(qobuz.client)
                await asyncio.to_thread(lib.sync_followed_artists, SOURCE, followed_now)

            # Com --atualizar-seguidos a Nota pode estar vazia (ex.: foi recriada):
            # nesse caso todos os seguidos voltam em new_followed_missing. Sem
            # itens nada é marcado/desmarcado, então não há risco para o banco.
            allow_empty_note = checklist_missing or bool(
                getattr(arguments, "atualizar_seguidos", False)
            )
            result = await sync_notes_checklist(
                checklist_text,
                lib,
                mirror=bool(getattr(arguments, "espelhar", False)),
                allow_empty=allow_empty_note,
            )

            # Todo artista do Qobuz que não consta na Nota (não só os "novos").
            note_items = parse_notes_checklist(
                checklist_text,
                allow_empty=allow_empty_note,
            )
            qobuz_artists = followed_now or [
                {"source_artist_id": row["source_artist_id"], "name": row["name"]}
                for row in result["missing"]
            ]
            result["new_followed_missing"] = compute_new_followed_missing(
                qobuz_artists, note_items
            )

            ui.emit_always(json.dumps(result, ensure_ascii=False))
            return 0 if result["ok"] else 1
        except Exception as exc:
            ui.emit_always(
                json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
            )
            return 1

    if getattr(arguments, "criar_playlist_vazia", False):
        artist_id = str(getattr(arguments, "id_artista", "") or "").strip()
        try:
            if not artist_id:
                raise ValueError("informe --id-artista para criar a playlist")
            payload = await _create_empty_artist_playlist(qobuz, artist_id, lib)
            if getattr(arguments, "abrir_perfil", False):
                opener = url_opener or _open_profile_url
                payload["profile_opened"] = await asyncio.to_thread(
                    opener, payload["artist"]["url"]
                )
            ui.emit_always(json.dumps(payload, ensure_ascii=False))
            return 0
        except Exception as exc:
            ui.emit_always(
                json.dumps(
                    {"ok": False, "error": str(exc)},
                    ensure_ascii=False,
                )
            )
            return 1

    if getattr(arguments, "json", False):
        try:
            if getattr(arguments, "sem_sincronizar", False):
                artists = await asyncio.to_thread(lib.get_followed_artists, SOURCE)
            else:
                fetched = await _fetch_followed_artists(qobuz.client)
                await asyncio.to_thread(lib.sync_followed_artists, SOURCE, fetched)
                artists = await asyncio.to_thread(lib.get_followed_artists, SOURCE)
        except Exception as exc:
            ui.error(f"Falha ao obter artistas seguidos: {exc}")
            return 1
        payload = {
            "artists": [
                {
                    "id": artist["source_artist_id"],
                    "name": artist["name"],
                    "url": f"https://open.qobuz.com/artist/{artist['source_artist_id']}",
                }
                for artist in artists
            ]
        }
        ui.emit_always(json.dumps(payload, ensure_ascii=False))
        return 0

    if session is None:
        session = PromptSession()
    profile_opener = url_opener or _open_profile_url
    runner = checklist or (run_checklist if interactive else None)

    if not getattr(arguments, "sem_sincronizar", False):
        try:
            summary = await refresh_artist_catalog(
                qobuz.client,
                lib,
                progress=ui.step_inline,
            )
        except Exception as exc:
            ui.error(f"Falha ao sincronizar os artistas seguidos: {exc}")
            return 1
        ui.ok(
            f"Artistas seguidos: {summary['artists']}; "
            f"lançamentos novos detectados: {summary['releases_new']}."
        )
        if summary["failures"]:
            ui.warn(
                f"{len(summary['failures'])} discografia(s) não puderam ser atualizadas; "
                "a última revisão dessas não foi alterada."
            )

    artists = await asyncio.to_thread(lib.get_followed_artists, SOURCE)
    if not artists:
        ui.info(
            "Nenhum artista disponível. Sincronize os artistas seguidos no Qobuz primeiro."
        )
        return 0

    mode = getattr(arguments, "modo", "alfabetica")
    if mode == "sorteio":
        pending = [a for a in artists if int(a.get("pending_count") or 0) > 0]
        candidates = pending or artists
        chosen = chooser(candidates)
        await _review_one(
            chosen, lib, session, client=qobuz.client, url_opener=profile_opener
        )
        return 0

    if runner is not None:
        if await _checklist_loop(artists, lib, qobuz, runner, profile_opener):
            return 0

    _show_roster(artists)
    while True:
        try:
            answer = (await session.prompt_async("Marcar: ")).strip().casefold()
        except (EOFError, KeyboardInterrupt):
            return 0
        if answer in {"q", "sair", ""}:
            return 0
        if answer in {"o", "abrir"}:
            ui.warn("Informe o número do artista, por exemplo: o 4.")
            continue
        if answer.startswith("o ") or answer.startswith("abrir "):
            try:
                _action, index_text = answer.split(maxsplit=1)
                if not index_text.isdigit():
                    raise ValueError
                index = int(index_text) - 1
                if index < 0 or index >= len(artists):
                    ui.warn("Esse número não corresponde a um artista da lista.")
                    continue
            except ValueError:
                ui.warn("Use o seguido de um número, por exemplo: o 4.")
                continue
            artist = artists[index]
            url = f"https://open.qobuz.com/artist/{artist['source_artist_id']}"
            opened = await asyncio.to_thread(profile_opener, url)
            if opened:
                ui.ok(f"Perfil aberto: {artist['name']}.")
            else:
                ui.warn(f"Não foi possível abrir o perfil: {url}")
            continue
        if answer.startswith("m ") or answer.startswith("marcar "):
            answer = answer.split(maxsplit=1)[1]
        indices_text = answer.replace(",", " ").split()
        if not indices_text or any(not item.isdigit() for item in indices_text):
            ui.warn("Use m 1,3 para marcar os artistas conferidos, ou q para sair.")
            continue
        selected = sorted({int(item) - 1 for item in indices_text})
        if any(index < 0 or index >= len(artists) for index in selected):
            ui.warn("Um dos números informados não corresponde a um artista da lista.")
            continue
        result = await confirm_artists(
            [artists[index] for index in selected],
            lib,
            qobuz.client,
            progress=ui.step_inline,
        )
        _report(result)
        artists = await asyncio.to_thread(lib.get_followed_artists, SOURCE)
        _show_roster(artists)
