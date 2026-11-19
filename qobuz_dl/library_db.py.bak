"""Catálogo local (``library.db``): favoritos, downloads e revisão de artistas.

ORIGEM DA IDEIA
---------------
Portado do modelo de dados do libsync (``backend/models/database.py``): uma
tabela ``albums`` com o que existe no serviço (favoritos) e o estado local de
cada álbum, mais ``sync_runs`` (histórico de sincronizações). É a base dos
comandos ``sync-favorites``, ``scan`` e ``library``.

POR QUE UM BANCO SEPARADO DO ``qobuz_dl.db``?
---------------------------------------------
O ``qobuz_dl.db`` (tabela ``downloads``) é o dedup por (id, qualidade) do
downloader e tem formato/contrato próprio (com migrações e testes). Este banco
responde a outra pergunta -- "o que eu TENHO na conta vs. o que eu TENHO no
disco?" -- e pode ser apagado a qualquer momento sem perder nada além do cache
(é reconstruído por ``sync-favorites`` + ``scan``).

Decisões herdadas de bugs reais do libsync:
  * ``upsert_album`` é um UPSERT *estreito*: campos omitidos (None) NUNCA
    sobrescrevem um valor já gravado. (No libsync, um upsert ingênuo apagava a
    capa de todo álbum assim que o download terminava.)
  * Estados "em voo" (``queued``/``downloading``) não sobrevivem a um
    reinício -- ``reset_stuck`` os devolve a ``not_downloaded``.

Só usa ``sqlite3`` da biblioteca padrão (uma conexão curta por operação, então
é seguro chamar via ``asyncio.to_thread``).
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

# Estados possíveis de ``albums.download_status``.
STATUS_NOT_DOWNLOADED = "not_downloaded"
STATUS_QUEUED = "queued"
STATUS_DOWNLOADING = "downloading"
STATUS_COMPLETE = "complete"
STATUS_INCOMPLETE = "incomplete"
STATUS_FAILED = "failed"
IN_FLIGHT_STATUSES = (STATUS_QUEUED, STATUS_DOWNLOADING)

_ALBUM_COLUMNS = (
    "title",
    "artist",
    "track_count",
    "bit_depth",
    "sample_rate",
    "release_date",
    "label",
    "genre",
    "upc",
    "duration_seconds",
    "cover_url",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _release_is_after_cutoff(release_date: Any, seen_at: str, cutoff: str) -> bool:
    """Compara datas Qobuz de precisão variável com a data-base do artista."""
    value = str(release_date or "").strip()
    baseline = str(cutoff or "")
    if value:
        date_match = re.match(r"^(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", value)
        cutoff_match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", baseline)
        if date_match and cutoff_match:
            year, month, day = date_match.groups()
            base_year, base_month, base_day = cutoff_match.groups()
            if day:
                return (year, month, day) >= (base_year, base_month, base_day)
            if month:
                return (year, month) > (base_year, base_month)
            return year > base_year
    # Se o serviço não fornecer uma data utilizável, a primeira observação
    # posterior ao baseline é o único sinal disponível de novidade.
    return bool(baseline and str(seen_at) > baseline)


class AlbumNotFoundError(LookupError):
    """Álbum inexistente no catálogo local."""


class LibraryDB:
    """Acesso ao ``library.db``. Uma conexão curta por chamada."""

    def __init__(self, path: str | os.PathLike):
        self.path = os.fspath(path)
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_schema()

    # -- infraestrutura ----------------------------------------------------

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
            except sqlite3.DatabaseError:
                logger.debug("WAL indisponível neste armazenamento", exc_info=True)
            with conn:  # commit/rollback automático
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version >= SCHEMA_VERSION:
                return
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS albums (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    source_album_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artist TEXT NOT NULL,
                    track_count INTEGER,
                    bit_depth INTEGER,
                    sample_rate REAL,
                    release_date TEXT,
                    label TEXT,
                    genre TEXT,
                    upc TEXT,
                    duration_seconds INTEGER,
                    cover_url TEXT,
                    download_status TEXT NOT NULL DEFAULT 'not_downloaded',
                    downloaded_at TEXT,
                    local_folder_path TEXT,
                    removed_from_service INTEGER NOT NULL DEFAULT 0,
                    added_to_library_at TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    UNIQUE (source, source_album_id)
                );
                CREATE INDEX IF NOT EXISTS idx_albums_status
                    ON albums (source, download_status);
                CREATE INDEX IF NOT EXISTS idx_albums_artist_title
                    ON albums (artist COLLATE NOCASE, title COLLATE NOCASE);
                CREATE INDEX IF NOT EXISTS idx_albums_removed
                    ON albums (source, removed_from_service);
                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL DEFAULT 'running',
                    albums_found INTEGER NOT NULL DEFAULT 0,
                    albums_new INTEGER NOT NULL DEFAULT 0,
                    albums_removed INTEGER NOT NULL DEFAULT 0,
                    albums_downloaded INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS followed_artists (
                    source TEXT NOT NULL,
                    source_artist_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    is_followed INTEGER NOT NULL DEFAULT 1,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    tracking_since_at TEXT,
                    last_reviewed_at TEXT,
                    last_catalog_sync_at TEXT,
                    review_playlist_id TEXT,
                    undo_available INTEGER NOT NULL DEFAULT 0,
                    undo_reviewed_at TEXT,
                    undo_tracking_since_at TEXT,
                    PRIMARY KEY (source, source_artist_id)
                );
                CREATE INDEX IF NOT EXISTS idx_followed_artists_name
                    ON followed_artists (name COLLATE NOCASE);
                CREATE TABLE IF NOT EXISTS artist_releases (
                    source TEXT NOT NULL,
                    source_artist_id TEXT NOT NULL,
                    source_release_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    release_date TEXT,
                    release_type TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    reviewed_at TEXT,
                    pending_review INTEGER NOT NULL DEFAULT 0,
                    undo_pending INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (source, source_artist_id, source_release_id),
                    FOREIGN KEY (source, source_artist_id)
                        REFERENCES followed_artists(source, source_artist_id)
                );
                CREATE INDEX IF NOT EXISTS idx_artist_releases_pending
                    ON artist_releases (source, source_artist_id, reviewed_at);
                """
            )
            artist_columns = {
                row["name"] for row in c.execute("PRAGMA table_info(followed_artists)")
            }
            if "tracking_since_at" not in artist_columns:
                c.execute(
                    "ALTER TABLE followed_artists ADD COLUMN tracking_since_at TEXT"
                )
            for column, ddl in (
                ("review_playlist_id", "TEXT"),
                ("undo_available", "INTEGER NOT NULL DEFAULT 0"),
                ("undo_reviewed_at", "TEXT"),
                ("undo_tracking_since_at", "TEXT"),
            ):
                if column not in artist_columns:
                    c.execute(f"ALTER TABLE followed_artists ADD COLUMN {column} {ddl}")
            release_columns = {
                row["name"] for row in c.execute("PRAGMA table_info(artist_releases)")
            }
            if "undo_pending" not in release_columns:
                c.execute(
                    "ALTER TABLE artist_releases ADD COLUMN undo_pending "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "pending_review" not in release_columns:
                c.execute(
                    "ALTER TABLE artist_releases ADD COLUMN pending_review "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            # A migração não converte o histórico v3 em novidades: usa a última
            # sincronização/revisão como baseline e deixa os registros antigos fora da fila.
            c.execute(
                "UPDATE followed_artists SET tracking_since_at="
                "COALESCE(last_reviewed_at, last_catalog_sync_at, first_seen_at) "
                "WHERE tracking_since_at IS NULL"
            )
            unreviewed_releases = c.execute(
                "SELECT r.source, r.source_artist_id, r.source_release_id, "
                "r.release_date, r.first_seen_at, a.last_reviewed_at "
                "FROM artist_releases r JOIN followed_artists a "
                "ON a.source=r.source AND a.source_artist_id=r.source_artist_id "
                "WHERE r.reviewed_at IS NULL AND r.pending_review=0 "
                "AND a.last_reviewed_at IS NOT NULL"
            ).fetchall()
            for release in unreviewed_releases:
                if _release_is_after_cutoff(
                    release["release_date"],
                    release["first_seen_at"],
                    release["last_reviewed_at"],
                ):
                    c.execute(
                        "UPDATE artist_releases SET pending_review=1 "
                        "WHERE source=? AND source_artist_id=? AND source_release_id=?",
                        (
                            release["source"],
                            release["source_artist_id"],
                            release["source_release_id"],
                        ),
                    )
            c.execute(
                "CREATE INDEX IF NOT EXISTS idx_artist_releases_pending_state "
                "ON artist_releases (source, source_artist_id, pending_review)"
            )
            c.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    # -- álbuns ------------------------------------------------------------

    def upsert_album(
        self,
        source: str,
        source_album_id: Any,
        title: str,
        artist: str,
        **fields: Any,
    ) -> int:
        """Insere/atualiza um álbum e devolve o ``id`` local.

        UPSERT *estreito*: só grava campos NÃO-None e nunca mexe em
        ``download_status``/``local_folder_path``/``downloaded_at`` de um álbum
        já existente. Reaparecer na conta zera ``removed_from_service``.
        """
        source_album_id = str(source_album_id)
        now = _now()
        extras = {
            k: v for k, v in fields.items() if k in _ALBUM_COLUMNS and v is not None
        }
        added_at = fields.get("added_to_library_at")
        with self._conn() as c:
            row = c.execute(
                "SELECT id FROM albums WHERE source=? AND source_album_id=?",
                (source, source_album_id),
            ).fetchone()
            if row is None:
                cols = [
                    "source",
                    "source_album_id",
                    "title",
                    "artist",
                    "first_seen_at",
                    "last_seen_at",
                    "added_to_library_at",
                ]
                vals: list[Any] = [
                    source,
                    source_album_id,
                    title or "",
                    artist or "",
                    now,
                    now,
                    added_at or now,
                ]
                for k, v in extras.items():
                    if k in ("title", "artist"):
                        continue
                    cols.append(k)
                    vals.append(v)
                marks = ",".join("?" for _ in cols)
                cur = c.execute(
                    f"INSERT INTO albums ({','.join(cols)}) VALUES ({marks})", vals
                )
                return int(cur.lastrowid or 0)
            sets = ["last_seen_at=?", "removed_from_service=0"]
            vals = [now]
            if title:
                sets.append("title=?")
                vals.append(title)
            if artist:
                sets.append("artist=?")
                vals.append(artist)
            for k, v in extras.items():
                if k in ("title", "artist"):
                    continue
                sets.append(f"{k}=?")
                vals.append(v)
            vals.append(row["id"])
            c.execute(f"UPDATE albums SET {', '.join(sets)} WHERE id=?", vals)
            return int(row["id"])

    def get_album(self, album_id: int) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute("SELECT * FROM albums WHERE id=?", (album_id,)).fetchone()
        return dict(row) if row else None

    def get_album_by_source_id(
        self, source: str, source_album_id: Any
    ) -> Optional[dict]:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM albums WHERE source=? AND source_album_id=?",
                (source, str(source_album_id)),
            ).fetchone()
        return dict(row) if row else None

    def get_albums(
        self,
        source: Optional[str] = None,
        status: Optional[str] = None,
        *,
        include_removed: bool = True,
        limit: Optional[int] = None,
    ) -> list[dict]:
        sql = "SELECT * FROM albums WHERE 1=1"
        args: list[Any] = []
        if source:
            sql += " AND source=?"
            args.append(source)
        if status:
            sql += " AND download_status=?"
            args.append(status)
        if not include_removed:
            sql += " AND removed_from_service=0"
        sql += " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE"
        if limit:
            sql += " LIMIT ?"
            args.append(int(limit))
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def get_all_albums_for_index(self, source: Optional[str] = None) -> list[dict]:
        """Linhas leves usadas para montar o índice de matching do ``scan``."""
        return self.get_albums(source, include_removed=True)

    # -- artistas seguidos e revisão de lançamentos ------------------------

    def sync_followed_artists(self, source: str, artists: Iterable[dict]) -> int:
        """Atualiza artistas seguidos; só deve ser chamado após paginação completa."""
        now = _now()
        parsed = {
            str(a["source_artist_id"]): str(a.get("name") or "Unknown")
            for a in artists
            if a.get("source_artist_id") not in (None, "")
        }
        with self._conn() as c:
            for artist_id, name in parsed.items():
                c.execute(
                    "INSERT INTO followed_artists "
                    "(source, source_artist_id, name, is_followed, first_seen_at, last_seen_at) "
                    "VALUES (?, ?, ?, 1, ?, ?) "
                    "ON CONFLICT(source, source_artist_id) DO UPDATE SET "
                    "name=excluded.name, is_followed=1, last_seen_at=excluded.last_seen_at",
                    (source, artist_id, name, now, now),
                )
            rows = c.execute(
                "SELECT source_artist_id FROM followed_artists "
                "WHERE source=? AND is_followed=1",
                (source,),
            ).fetchall()
            for row in rows:
                if row["source_artist_id"] not in parsed:
                    c.execute(
                        "UPDATE followed_artists SET is_followed=0 "
                        "WHERE source=? AND source_artist_id=?",
                        (source, row["source_artist_id"]),
                    )
        return len(parsed)

    def record_artist_releases(
        self, source: str, artist_id: Any, releases: Iterable[dict]
    ) -> int:
        """Registra lançamentos e retorna quantos novos entraram na fila de revisão."""
        artist_id = str(artist_id)
        now = _now()
        newly_pending = 0
        with self._conn() as c:
            artist = c.execute(
                "SELECT tracking_since_at, last_reviewed_at, last_catalog_sync_at "
                "FROM followed_artists WHERE source=? AND source_artist_id=?",
                (source, artist_id),
            ).fetchone()
            if artist is None:
                return 0
            cutoff = (
                artist["tracking_since_at"]
                or artist["last_reviewed_at"]
                or artist["last_catalog_sync_at"]
            )
            first_catalog = cutoff is None
            if first_catalog:
                cutoff = now
                c.execute(
                    "UPDATE followed_artists SET tracking_since_at=? "
                    "WHERE source=? AND source_artist_id=?",
                    (cutoff, source, artist_id),
                )
            for release in releases:
                release_id = release.get("source_release_id")
                if release_id in (None, ""):
                    continue
                pending = (
                    0
                    if first_catalog
                    else int(
                        _release_is_after_cutoff(
                            release.get("release_date"), now, str(cutoff)
                        )
                    )
                )
                cur = c.execute(
                    "INSERT OR IGNORE INTO artist_releases "
                    "(source, source_artist_id, source_release_id, title, release_date, "
                    "release_type, first_seen_at, last_seen_at, pending_review) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        source,
                        artist_id,
                        str(release_id),
                        str(release.get("title") or "Unknown"),
                        release.get("release_date"),
                        release.get("release_type"),
                        now,
                        now,
                        pending,
                    ),
                )
                if cur.rowcount > 0 and pending:
                    newly_pending += 1
                c.execute(
                    "UPDATE artist_releases SET title=?, "
                    "release_date=COALESCE(?, release_date), "
                    "release_type=COALESCE(?, release_type), last_seen_at=? "
                    "WHERE source=? AND source_artist_id=? AND source_release_id=?",
                    (
                        str(release.get("title") or "Unknown"),
                        release.get("release_date"),
                        release.get("release_type"),
                        now,
                        source,
                        artist_id,
                        str(release_id),
                    ),
                )
            c.execute(
                "UPDATE followed_artists SET last_catalog_sync_at=? "
                "WHERE source=? AND source_artist_id=?",
                (now, source, artist_id),
            )
        return newly_pending

    def get_followed_artists(self, source: Optional[str] = None) -> list[dict]:
        sql = (
            "SELECT a.*, (SELECT COUNT(*) FROM artist_releases r "
            "WHERE r.source=a.source AND r.source_artist_id=a.source_artist_id "
            "AND r.pending_review=1) AS pending_count "
            "FROM followed_artists a WHERE a.is_followed=1"
        )
        args: list[Any] = []
        if source:
            sql += " AND a.source=?"
            args.append(source)
        sql += " ORDER BY a.name COLLATE NOCASE, a.source_artist_id"
        with self._conn() as c:
            artists = [dict(row) for row in c.execute(sql, args).fetchall()]

        def sort_key(artist: dict) -> tuple[str, str, str]:
            name = str(artist.get("name") or "")
            folded = unicodedata.normalize("NFKD", name.casefold())
            plain = "".join(ch for ch in folded if not unicodedata.combining(ch))
            return plain, name.casefold(), str(artist.get("source_artist_id") or "")

        return sorted(artists, key=sort_key)

    def get_artist_releases(
        self, source: str, artist_id: Any, *, pending_only: bool = False
    ) -> list[dict]:
        sql = "SELECT * FROM artist_releases WHERE source=? AND source_artist_id=?"
        args: list[Any] = [source, str(artist_id)]
        if pending_only:
            sql += " AND pending_review=1"
        sql += " ORDER BY COALESCE(release_date, '') DESC, title COLLATE NOCASE"
        with self._conn() as c:
            return [dict(row) for row in c.execute(sql, args).fetchall()]

    def mark_artist_reviewed(self, source: str, artist_id: Any) -> bool:
        """Marca como conferidos os lançamentos pendentes encontrados até agora.

        Guarda o estado anterior (um nível) para ``unmark_artist_reviewed``
        poder desfazer, inclusive quais lançamentos estavam pendentes.
        """
        now = _now()
        with self._conn() as c:
            previous = c.execute(
                "SELECT last_reviewed_at, tracking_since_at FROM followed_artists "
                "WHERE source=? AND source_artist_id=? AND is_followed=1",
                (source, str(artist_id)),
            ).fetchone()
            if previous is None:
                return False
            c.execute(
                "UPDATE followed_artists SET last_reviewed_at=?, tracking_since_at=?, "
                "undo_available=1, undo_reviewed_at=?, undo_tracking_since_at=? "
                "WHERE source=? AND source_artist_id=?",
                (
                    now,
                    now,
                    previous["last_reviewed_at"],
                    previous["tracking_since_at"],
                    source,
                    str(artist_id),
                ),
            )
            c.execute(
                "UPDATE artist_releases SET undo_pending=pending_review "
                "WHERE source=? AND source_artist_id=?",
                (source, str(artist_id)),
            )
            c.execute(
                "UPDATE artist_releases SET reviewed_at=?, pending_review=0 "
                "WHERE source=? AND source_artist_id=?",
                (now, source, str(artist_id)),
            )
        return True

    def unmark_artist_reviewed(self, source: str, artist_id: Any) -> bool:
        """Desfaz a última conferência do artista (a playlist não é tocada).

        Restaura a data da conferência anterior e os lançamentos que estavam
        pendentes. Sem estado guardado (conferido antes deste recurso), só
        limpa a data da conferência. Devolve False se o artista não estiver
        seguido ou não estiver conferido.
        """
        with self._conn() as c:
            row = c.execute(
                "SELECT last_reviewed_at, undo_available, undo_reviewed_at, "
                "undo_tracking_since_at FROM followed_artists "
                "WHERE source=? AND source_artist_id=? AND is_followed=1",
                (source, str(artist_id)),
            ).fetchone()
            if row is None or not row["last_reviewed_at"]:
                return False
            if row["undo_available"]:
                restored = c.execute(
                    "UPDATE artist_releases SET pending_review=1, reviewed_at=NULL, "
                    "undo_pending=0 WHERE source=? AND source_artist_id=? "
                    "AND undo_pending=1",
                    (source, str(artist_id)),
                ).rowcount
                # Sem lançamentos de volta à fila, o artista tem de aparecer como
                # não conferido; com eles, volta a data da conferência anterior.
                c.execute(
                    "UPDATE followed_artists SET last_reviewed_at=?, "
                    "tracking_since_at=COALESCE(?, tracking_since_at), "
                    "undo_available=0, undo_reviewed_at=NULL, "
                    "undo_tracking_since_at=NULL "
                    "WHERE source=? AND source_artist_id=?",
                    (
                        row["undo_reviewed_at"] if restored else None,
                        row["undo_tracking_since_at"],
                        source,
                        str(artist_id),
                    ),
                )
            else:
                c.execute(
                    "UPDATE followed_artists SET last_reviewed_at=NULL "
                    "WHERE source=? AND source_artist_id=?",
                    (source, str(artist_id)),
                )
        return True

    def set_artist_review_playlist(
        self, source: str, artist_id: Any, playlist_id: Any
    ) -> bool:
        """Guarda o id da playlist de controle do artista (None limpa)."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE followed_artists SET review_playlist_id=? "
                "WHERE source=? AND source_artist_id=?",
                (
                    None if playlist_id in (None, "") else str(playlist_id),
                    source,
                    str(artist_id),
                ),
            )
        return bool(cur.rowcount)

    # -- estado de download ------------------------------------------------

    def update_status(self, album_id: int, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE albums SET download_status=? WHERE id=?", (status, album_id)
            )

    def set_download_state(
        self,
        album_id: int,
        *,
        downloaded: bool,
        downloaded_at: Optional[str] = None,
        local_folder_path: Optional[str] = None,
        status: Optional[str] = None,
    ) -> dict:
        """Marca (ou desmarca) um álbum como baixado. Devolve a linha anterior.

        ``local_folder_path=None`` preserva o caminho já gravado ao MARCAR; ao
        desmarcar, o caminho é limpo.
        """
        old = self.get_album(album_id)
        if old is None:
            raise AlbumNotFoundError(f"Álbum {album_id} não encontrado")
        with self._conn() as c:
            if downloaded:
                c.execute(
                    "UPDATE albums SET download_status=?, downloaded_at=?, "
                    "local_folder_path=COALESCE(?, local_folder_path) WHERE id=?",
                    (
                        status or STATUS_COMPLETE,
                        downloaded_at or _now(),
                        local_folder_path,
                        album_id,
                    ),
                )
            else:
                c.execute(
                    "UPDATE albums SET download_status=?, downloaded_at=NULL, "
                    "local_folder_path=NULL WHERE id=?",
                    (status or STATUS_NOT_DOWNLOADED, album_id),
                )
        return old

    def set_local_folder(self, album_id: int, path: Optional[str]) -> None:
        """Grava só o caminho local (sem mexer no status)."""
        with self._conn() as c:
            c.execute(
                "UPDATE albums SET local_folder_path=? WHERE id=?", (path, album_id)
            )

    def reset_stuck(self, source: Optional[str] = None) -> int:
        """Devolve ``queued``/``downloading`` a ``not_downloaded`` (artefato de reinício)."""
        sql = "UPDATE albums SET download_status=? WHERE download_status IN (?, ?)"
        args: list[Any] = [STATUS_NOT_DOWNLOADED, *IN_FLIGHT_STATUSES]
        if source:
            sql += " AND source=?"
            args.append(source)
        with self._conn() as c:
            return c.execute(sql, args).rowcount

    def mark_removed(self, source: str, present_ids: Iterable[Any]) -> list[dict]:
        """Marca como ``removed_from_service`` tudo que não está em ``present_ids``.

        Devolve as linhas *recém* marcadas (as que estavam ativas antes).
        """
        present = {str(i) for i in present_ids}
        removed: list[dict] = []
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM albums WHERE source=? AND removed_from_service=0",
                (source,),
            ).fetchall()
            for r in rows:
                if r["source_album_id"] not in present:
                    removed.append(dict(r))
                    c.execute(
                        "UPDATE albums SET removed_from_service=1 WHERE id=?",
                        (r["id"],),
                    )
        return removed

    # -- relatórios --------------------------------------------------------

    def status_counts(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT source, download_status, COUNT(*) AS cnt FROM albums "
                "GROUP BY source, download_status ORDER BY source, download_status"
            ).fetchall()
        return [dict(r) for r in rows]

    def stuck_albums(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM albums WHERE download_status IN (?, ?) ORDER BY id",
                IN_FLIGHT_STATUSES,
            ).fetchall()
        return [dict(r) for r in rows]

    def complete_without_folder(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM albums WHERE download_status=? "
                "AND (local_folder_path IS NULL OR local_folder_path='')",
                (STATUS_COMPLETE,),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- histórico de sync -------------------------------------------------

    def create_sync_run(self, source: str) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO sync_runs (source, started_at) VALUES (?, ?)",
                (source, _now()),
            )
            return int(cur.lastrowid or 0)

    def complete_sync_run(
        self,
        run_id: int,
        *,
        albums_found: int,
        albums_new: int,
        albums_removed: int = 0,
        albums_downloaded: int = 0,
    ) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE sync_runs SET status='complete', completed_at=?, albums_found=?, "
                "albums_new=?, albums_removed=?, albums_downloaded=? WHERE id=?",
                (
                    _now(),
                    albums_found,
                    albums_new,
                    albums_removed,
                    albums_downloaded,
                    run_id,
                ),
            )

    def update_sync_run_downloaded(self, run_id: int, albums_downloaded: int) -> None:
        """Update the downloaded-album count for a synchronization run."""
        with self._conn() as c:
            c.execute(
                "UPDATE sync_runs SET albums_downloaded=? WHERE id=?",
                (albums_downloaded, run_id),
            )

    def _finish_run(self, run_id: int, status: str) -> None:
        with self._conn() as c:
            c.execute(
                "UPDATE sync_runs SET status=?, completed_at=? WHERE id=?",
                (status, _now(), run_id),
            )

    def fail_sync_run(self, run_id: int) -> None:
        self._finish_run(run_id, "failed")

    def interrupt_sync_run(self, run_id: int) -> None:
        self._finish_run(run_id, "interrupted")

    def get_sync_history(
        self, source: Optional[str] = None, limit: int = 10
    ) -> list[dict]:
        sql = "SELECT * FROM sync_runs"
        args: list[Any] = []
        if source:
            sql += " WHERE source=?"
            args.append(source)
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit))
        with self._conn() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]
