# ============================================================================
# playlist_links.py -- playlist x álbum sem faixas duplicadas.
#
# PROBLEMA: baixar uma playlist (faixas de vários álbuns) e depois um desses
# álbuns deixava a mesma música em duas pastas.
#
# SOLUÇÃO (três peças que trabalham juntas):
#   1. As playlists ficam em <downloads>/Playlist/<nome> (ver constants.py),
#      como Album/EP/Single já tinham a sua pasta.
#   2. Quando um álbum/faixa avulsa termina de baixar, a cópia que está numa
#      pasta de playlist é APAGADA (só se a do álbum tiver qualidade igual ou
#      melhor) e a playlist passa a apontar para o arquivo do álbum por um
#      "link": um arquivo oculto `.playlist_links.json` na pasta da playlist
#      (id da faixa -> caminho relativo). O .m3u é reescrito no mesmo lugar,
#      mantendo a ordem. Nada é copiado nem movido para a pasta da playlist.
#   3. Quem lê a pasta da playlist (make_m3u, sync-playlist, download de
#      playlist) também lê os links. Assim o sync não "rebaixa" a faixa e
#      nunca apaga o arquivo do álbum como se fosse órfão.
#
# Também grava no banco de downloads uma linha por faixa de cada álbum baixado
# (media_type='track'); sem isso, baixar a playlist DEPOIS do álbum duplicaria
# a faixa, porque o banco só conhecia o id do álbum.
#
# Tudo aqui é síncrono e usa só sqlite3/os da biblioteca padrão (mutagen só é
# importado na hora de ler tags), então roda via asyncio.to_thread e é
# testável com leitores de tag falsos.
# ============================================================================
import asyncio
import contextlib
import json
import logging
import os
import sqlite3

from qobuz_dl.constants import PLAYLIST_FOLDER

logger = logging.getLogger(__name__)

LINKS_FILENAME = ".playlist_links.json"
AUDIO_EXTS = (".flac", ".mp3")
_SQLITE_TIMEOUT = 30
_WEB_URL = "https://play.qobuz.com/"


# --------------------------------------------------------------------------
# Caminhos
# --------------------------------------------------------------------------
def playlist_root(directory):
    """<downloads>/Playlist"""
    return os.path.join(
        os.path.abspath(os.path.expanduser(str(directory))), PLAYLIST_FOLDER
    )


def _is_under(path, root):
    path, root = os.path.abspath(path), os.path.abspath(root)
    return path != root and os.path.commonpath([path, root]) == root


def playlist_dir_for(path, root):
    """Pasta da playlist (1º nível dentro de `root`) que contém `path`."""
    if not _is_under(path, root):
        return None
    rel = os.path.relpath(os.path.abspath(path), os.path.abspath(root))
    return os.path.join(os.path.abspath(root), rel.split(os.sep)[0])


# --------------------------------------------------------------------------
# Links (.playlist_links.json)
# --------------------------------------------------------------------------
def _links_path(pl_dir):
    return os.path.join(pl_dir, LINKS_FILENAME)


def load_links(pl_dir):
    """{id: caminho relativo à pasta da playlist}. Arquivo ilegível = vazio."""
    try:
        with open(_links_path(pl_dir), encoding="utf-8") as f:
            data = json.load(f)
        links = data.get("links", {}) if isinstance(data, dict) else {}
        return {str(k): str(v) for k, v in links.items()}
    except (OSError, ValueError):
        return {}


def save_links(pl_dir, links):
    path = _links_path(pl_dir)
    if not links:
        with contextlib.suppress(OSError):
            os.remove(path)
        return
    os.makedirs(pl_dir, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"links": links}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def add_link(pl_dir, track_id, target_path):
    links = load_links(pl_dir)
    links[str(track_id)] = os.path.relpath(
        os.path.abspath(target_path), os.path.abspath(pl_dir)
    )
    save_links(pl_dir, links)


def remove_link(pl_dir, track_id):
    links = load_links(pl_dir)
    if links.pop(str(track_id), None) is not None:
        save_links(pl_dir, links)
        return True
    return False


def valid_links(pl_dir):
    """{id: caminho absoluto} só dos links cujo arquivo ainda existe."""
    out = {}
    for tid, rel in load_links(pl_dir).items():
        p = os.path.normpath(os.path.join(pl_dir, rel))
        if os.path.isfile(p):
            out[tid] = p
    return out


def link_existing_track(pl_dir, track_id, existing_path):
    """Playlist pulou uma faixa porque ela já existe em outra pasta (álbum ou
    outra playlist): registra o link pra ela aparecer no .m3u. True se criou."""
    if not pl_dir or not existing_path or not os.path.isfile(existing_path):
        return False
    if os.path.abspath(existing_path) == os.path.abspath(pl_dir) or _is_under(
        existing_path, pl_dir
    ):
        return False  # já está dentro da própria playlist
    try:
        add_link(pl_dir, track_id, existing_path)
        return True
    except OSError as e:
        logger.debug("Falha ao gravar link de playlist: %s", e)
        return False


# --------------------------------------------------------------------------
# Tags
# --------------------------------------------------------------------------
def read_track_id(path):
    """ID Qobuz gravado na tag (QDL_TRACK_ID ou QOBUZTRACKID) ou None."""
    try:
        if path.lower().endswith(".flac"):
            from mutagen.flac import FLAC

            audio = FLAC(path)
            vals = audio.get("QDL_TRACK_ID") or audio.get("QOBUZTRACKID") or [None]
            return str(vals[0]) if vals[0] else None
        from mutagen.id3 import ID3

        audio = ID3(path)
        frame = (
            audio.get("TXXX:QDL_TRACK_ID")
            or audio.get("TXXX:qdl_track_id")
            or audio.get("TXXX:QOBUZTRACKID")
        )
        return str(frame.text[0]) if frame else None
    except Exception:
        return None


def audio_rank(path):
    """Tupla comparável de qualidade: FLAC > MP3; depois bits, taxa/bitrate.
    None se não der pra ler (nesse caso nada é apagado)."""
    try:
        from mutagen import File

        audio = File(path)
        info = getattr(audio, "info", None)
        if info is None:
            return None
        if path.lower().endswith(".flac"):
            return (
                1,
                int(getattr(info, "bits_per_sample", 0) or 0),
                int(getattr(info, "sample_rate", 0) or 0),
            )
        return (0, 0, int(getattr(info, "bitrate", 0) or 0))
    except Exception:
        return None


def scan_track_paths(directory, reader=read_track_id):
    """{id: [caminhos]} de todos os áudios sob `directory`."""
    out = {}
    for root, _dirs, files in os.walk(directory):
        for fname in files:
            if fname.lower().endswith(AUDIO_EXTS):
                p = os.path.join(root, fname)
                tid = reader(p)
                if tid:
                    out.setdefault(str(tid), []).append(os.path.abspath(p))
    return out


# --------------------------------------------------------------------------
# Banco de downloads (tabela `downloads`, ver db.py)
# --------------------------------------------------------------------------
@contextlib.contextmanager
def _conn(db_path):
    c = sqlite3.connect(db_path, timeout=_SQLITE_TIMEOUT)
    try:
        yield c
        c.commit()
    finally:
        c.close()


def _db_rows(db_path, ids):
    """[(id, saved_path)] das faixas (media_type='track') já registradas."""
    ids = [str(i) for i in ids]
    rows = []
    try:
        with _conn(db_path) as c:
            for i in range(0, len(ids), 400):
                chunk = ids[i : i + 400]
                q = ",".join("?" * len(chunk))
                rows.extend(
                    c.execute(
                        "SELECT id, saved_path FROM downloads "
                        f"WHERE media_type='track' AND id IN ({q})",
                        chunk,
                    ).fetchall()
                )
    except sqlite3.Error as e:
        logger.debug("Falha ao consultar downloads: %s", e)
    return [(str(a), b or "") for a, b in rows]


def _db_set_path(db_path, track_id, new_path):
    try:
        with _conn(db_path) as c:
            c.execute(
                "UPDATE downloads SET saved_path=? WHERE id=? AND media_type='track'",
                (new_path, str(track_id)),
            )
    except sqlite3.Error as e:
        logger.debug("Falha ao atualizar caminho no banco: %s", e)


def _db_register_tracks(db_path, files, reg):
    """Uma linha por faixa do álbum (INSERT OR IGNORE). Retorna quantas entraram."""
    n = 0
    try:
        with _conn(db_path) as c:
            for tid, path in files.items():
                cur = c.execute(
                    "INSERT OR IGNORE INTO downloads (id, media_type, quality, "
                    "file_format, quality_met, bit_depth, sampling_rate, "
                    "saved_path, url, release_date, status, artist, album) "
                    "VALUES (?, 'track', ?, ?, ?, ?, ?, ?, ?, ?, 'downloaded', ?, ?)",
                    (
                        str(tid),
                        reg.get("quality", 27),
                        reg.get("file_format", "FLAC"),
                        reg.get("quality_met", 0),
                        reg.get("bit_depth"),
                        reg.get("sampling_rate"),
                        os.path.abspath(path),
                        f"{_WEB_URL}track/{tid}",
                        reg.get("release_date", "") or "",
                        reg.get("artist", "") or "",
                        reg.get("album", "") or "",
                    ),
                )
                n += max(0, cur.rowcount)
    except sqlite3.Error as e:
        logger.debug("Falha ao registrar faixas no banco: %s", e)
    return n


# --------------------------------------------------------------------------
# .m3u e limpeza
# --------------------------------------------------------------------------
def _rewrite_m3u(pl_dir, mapping):
    """Troca, nos .m3u/.m3u8 da pasta, o caminho antigo pelo novo (mantém a
    ordem e os #EXTINF). `mapping`: {caminho_abs_antigo: caminho_abs_novo}."""
    try:
        names = [n for n in os.listdir(pl_dir) if n.lower().endswith((".m3u", ".m3u8"))]
    except OSError:
        return
    norm = {os.path.normpath(k): v for k, v in mapping.items()}
    for name in names:
        path = os.path.join(pl_dir, name)
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().split("\n")
        except (OSError, UnicodeDecodeError):
            logger.debug("Playlist ilegível, ignorada: %s", path, exc_info=True)
            continue
        changed = False
        for i, line in enumerate(lines):
            if not line or line.startswith("#"):
                continue
            new = norm.get(os.path.normpath(os.path.join(pl_dir, line)))
            if new:
                lines[i] = os.path.relpath(new, pl_dir)
                changed = True
        if changed:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))


def _move_or_drop_lrc(src, dst):
    """.lrc ao lado do arquivo apagado: vai pro álbum se lá não houver, senão some."""
    s = os.path.splitext(src)[0] + ".lrc"
    if not os.path.isfile(s):
        return
    d = os.path.splitext(dst)[0] + ".lrc"
    with contextlib.suppress(OSError):
        if os.path.exists(d):
            os.remove(s)
        else:
            os.replace(s, d)


def _prune_empty_dirs(start, stop):
    """Remove pastas vazias de `start` subindo até (sem incluir) `stop`."""
    cur = os.path.abspath(start)
    stop = os.path.abspath(stop)
    while cur != stop and _is_under(cur, stop):
        try:
            os.rmdir(cur)
        except OSError:
            break
        cur = os.path.dirname(cur)


# --------------------------------------------------------------------------
# Operação principal
# --------------------------------------------------------------------------
def absorb_playlist_copies_sync(
    library_root,
    db_path,
    dest_files,
    reg=None,
    reader=read_track_id,
    ranker=audio_rank,
):
    """Faz as faixas em `dest_files` ({id: caminho no álbum}) existirem só no
    álbum. Retorna {"absorbed": [(id, antigo, novo)], "kept": [caminhos],
    "registered": n}."""
    stats = {"absorbed": [], "kept": [], "registered": 0}
    dest = {str(k): os.path.abspath(v) for k, v in (dest_files or {}).items()}
    if not dest:
        return stats

    if db_path and reg is not None:
        stats["registered"] = _db_register_tracks(db_path, dest, reg)

    root = playlist_root(library_root)
    if not os.path.isdir(root):
        return stats

    # 1) onde estão as cópias de playlist dessas faixas
    copies = {}
    if db_path:
        for tid, p in _db_rows(db_path, dest):
            if os.path.isdir(p) and _is_under(p, root):
                for t2, paths in scan_track_paths(p, reader).items():
                    if t2 == tid:
                        copies.setdefault(tid, set()).update(paths)
            elif os.path.isfile(p) and _is_under(p, root):
                copies.setdefault(tid, set()).add(os.path.abspath(p))
    else:
        for tid, paths in scan_track_paths(root, reader).items():
            if tid in dest:
                copies.setdefault(tid, set()).update(paths)

    # 2) apaga a cópia da playlist se a do álbum for igual ou melhor
    absorbed = []
    for tid, paths in copies.items():
        album_file = dest[tid]
        for old in sorted(paths):
            if old == album_file or not os.path.isfile(old):
                continue
            r_new, r_old = ranker(album_file), ranker(old)
            if r_new is None or r_old is None or r_new < r_old:
                stats["kept"].append(old)
                continue
            _move_or_drop_lrc(old, album_file)
            try:
                os.remove(old)
            except OSError as e:
                logger.debug("Não removi %s: %s", old, e)
                stats["kept"].append(old)
                continue
            _prune_empty_dirs(os.path.dirname(old), playlist_dir_for(old, root) or root)
            absorbed.append((tid, old, album_file))
            if db_path:
                _db_set_path(db_path, tid, album_file)

    if not absorbed:
        return stats

    # 3) links + .m3u de TODAS as playlists (a mesma faixa pode estar em várias)
    mapping = {old: new for _t, old, new in absorbed}
    owners = {old: playlist_dir_for(old, root) for _t, old, _n in absorbed}
    try:
        pl_dirs = [
            os.path.join(root, d)
            for d in sorted(os.listdir(root))
            if os.path.isdir(os.path.join(root, d))
        ]
    except OSError:
        pl_dirs = []
    for pl in pl_dirs:
        links = load_links(pl)
        changed = False
        for tid, old, new in absorbed:
            if tid in links or owners.get(old) == pl:
                links[tid] = os.path.relpath(new, pl)
                changed = True
        if changed:
            save_links(pl, links)
        _rewrite_m3u(pl, mapping)

    stats["absorbed"] = absorbed
    return stats


async def absorb_playlist_copies(library_root, db_path, dest_files, reg=None):
    """Versão assíncrona (roda em thread): ver absorb_playlist_copies_sync."""
    return await asyncio.to_thread(
        absorb_playlist_copies_sync, library_root, db_path, dest_files, reg
    )


def scan_dest_folder(dest_dir, reader=read_track_id):
    """{id: caminho} do que está numa pasta de álbum (1º arquivo por id)."""
    return {tid: paths[0] for tid, paths in scan_track_paths(dest_dir, reader).items()}
