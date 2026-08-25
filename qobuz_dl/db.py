import logging
import sqlite3
import aiosqlite

# CYAN/YELLOW importados como INFO/WARNING renomeados: mesma cor de
# YELLOW (mantida por convencao), mas CYAN agora e' LIGHTBLUE_EX --
# visivel em terminal claro E escuro (CYAN puro quase some em fundo
# branco). Ver comentario completo em qobuz_dl/color.py. Zero mudanca
# de codigo neste arquivo: toda f-string que ja usa {CYAN}/{YELLOW}
# continua funcionando, so' a cor de fato renderizada muda.
from qobuz_dl.color import WARNING as YELLOW, RED, OFF

logger = logging.getLogger(__name__)


def create_db(db_path):
    """
    Initializes or upgrades the SQLite database used for the Smart Reverse Lookup feature.

    Handles legacy migrations (e.g., v1 to v2, adding quality/format columns)
    and recent schema upgrades (e.g., adding artist and album columns).

    Args:
        db_path (str): The file path where the SQLite database is or will be stored.

    Returns:
        str: The path to the successfully initialized database.
    """
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # Check if the table already exists
        cursor.execute(
            "SELECT count(name) FROM sqlite_master WHERE type='table' AND name='downloads'"
        )

        if cursor.fetchone()[0] == 1:
            # Table exists. Read current columns
            cursor.execute("PRAGMA table_info(downloads)")
            columns = [info[1] for info in cursor.fetchall()]

            # Legacy migration (v1 to v2)
            if "quality" not in columns:
                logger.info(f"{YELLOW}Migrating old database to the new format...{OFF}")

                # Rename the old table
                conn.execute("ALTER TABLE downloads RENAME TO downloads_old")

                # Create the new table with updated schema including artist and album
                conn.execute("""
                CREATE TABLE downloads (
                  "id" text NOT NULL,
                  "media_type" text NOT NULL DEFAULT 'album',
                  "quality" integer NOT NULL DEFAULT 27,
                  "file_format" text NOT NULL DEFAULT 'FLAC',
                  "quality_met" integer NOT NULL DEFAULT 0,
                  "bit_depth" text,
                  "sampling_rate" text,
                  "saved_path" text NOT NULL DEFAULT '',
                  "status" text NOT NULL DEFAULT 'downloaded',
                  "url" text NOT NULL DEFAULT '',
                  "release_date" text NOT NULL DEFAULT '',
                  "artist" text NOT NULL DEFAULT '',
                  "album" text NOT NULL DEFAULT '',
                  PRIMARY KEY ("id", "quality")
                );
                """)

                # Copy old historical IDs
                try:
                    conn.execute(
                        "INSERT INTO downloads (id) SELECT id FROM downloads_old"
                    )
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to migrate old data: {e}{OFF}")

                # Drop the temporary old table
                conn.execute("DROP TABLE downloads_old")
                logger.info(f"{YELLOW}Database successfully updated!{OFF}")

            # New Migration (v2 to v2.1.4): Add artist and album if missing
            elif "artist" not in columns:
                logger.info(
                    f"{YELLOW}Upgrading database schema: Adding artist and album columns...{OFF}"
                )
                try:
                    conn.execute(
                        "ALTER TABLE downloads ADD COLUMN artist text NOT NULL DEFAULT ''"
                    )
                    conn.execute(
                        "ALTER TABLE downloads ADD COLUMN album text NOT NULL DEFAULT ''"
                    )
                    logger.info(f"{YELLOW}Schema upgrade complete!{OFF}")
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to add new columns: {e}{OFF}")

        else:
            # Table does not exist, create it from scratch
            try:
                conn.execute("""
                CREATE TABLE downloads (
                  "id" text NOT NULL,
                  "media_type" text NOT NULL DEFAULT 'album',
                  "quality" integer NOT NULL DEFAULT 27,
                  "file_format" text NOT NULL DEFAULT 'FLAC',
                  "quality_met" integer NOT NULL DEFAULT 0,
                  "bit_depth" text,
                  "sampling_rate" text,
                  "saved_path" text NOT NULL DEFAULT '',
                  "status" text NOT NULL DEFAULT 'downloaded',
                  "url" text NOT NULL DEFAULT '',
                  "release_date" text NOT NULL DEFAULT '',
                  "artist" text NOT NULL DEFAULT '',
                  "album" text NOT NULL DEFAULT '',
                  PRIMARY KEY ("id", "quality")
                );
                """)
                logger.info(f"{YELLOW}Download-IDs database created{OFF}")
            except sqlite3.OperationalError:
                pass

        return db_path


async def handle_download_id(
    db_path,
    item_id,
    add_id=False,
    media_type="album",
    quality=27,
    file_format="FLAC",
    quality_met=0,
    bit_depth=None,
    sampling_rate=None,
    saved_path="",
    status="downloaded",
    url="",
    release_date="",
    artist="",
    album="",
):
    """
    Checks for existing downloads or inserts new completed downloads into the database.

    Convertido para async usando aiosqlite (antes: sqlite3.connect sincrono).
    Essa e' a funcao chamada uma vez POR FAIXA/ALBUM baixado -- com sqlite3
    sincrono, cada escrita travava o event loop por uma fracao de segundo;
    isoladamente pouco, mas em discografias grandes rodando varios downloads
    em paralelo (asyncio.gather em core.py/downloader.py) isso se acumulava.
    aiosqlite usa um thread dedicado por baixo dos panos e devolve o
    controle pro event loop enquanto a escrita acontece, em vez de bloquear
    tudo.

    Args:
        db_path (str): The file path to the SQLite database.
        item_id (str): The unique Qobuz ID of the track or album.
        add_id (bool, optional): If True, inserts a new record. If False, queries for existence. Defaults to False.
        media_type (str, optional): 'album' or 'track'. Defaults to 'album'.
        quality (int, optional): The requested audio quality ID. Defaults to 27.
        file_format (str, optional): The actual downloaded format (e.g., 'FLAC', 'MP3'). Defaults to 'FLAC'.
        quality_met (int, optional): Flag indicating if the requested quality was achieved (1 or 0). Defaults to 0.
        bit_depth (str, optional): Audio bit depth. Defaults to None.
        sampling_rate (str, optional): Audio sampling rate. Defaults to None.
        saved_path (str, optional): The local file system path where the item was saved. Defaults to ''.
        status (str, optional): The download status. Defaults to 'downloaded'.
        url (str, optional): The original Qobuz URL. Defaults to ''.
        release_date (str, optional): The item's release date. Defaults to ''.
        artist (str, optional): The main artist's name. Defaults to ''.
        album (str, optional): The album's title. Defaults to ''.

    Returns:
        tuple or None: If add_id is False, returns a tuple containing the ID if found, otherwise None.
    """
    if not db_path:
        return

    async with aiosqlite.connect(db_path) as conn:
        if add_id:
            try:
                # Inject artist and album dynamically into the database
                await conn.execute(
                    """
                    INSERT INTO downloads (id, media_type, quality, file_format, quality_met, bit_depth,
                    sampling_rate, saved_path, url, release_date, status, artist, album) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        item_id,
                        media_type,
                        quality,
                        file_format,
                        quality_met,
                        bit_depth,
                        sampling_rate,
                        saved_path,
                        url,
                        release_date,
                        status,
                        artist,
                        album,
                    ),
                )
                await conn.commit()
            except sqlite3.IntegrityError:
                # Provide clean visual feedback instead of an error
                logger.info(f"{YELLOW}[i] Already in database, skipping.{OFF}")
            except sqlite3.Error as e:
                logger.error(f"{RED}Unexpected DB error: {e}{OFF}")
        else:
            cursor = await conn.execute(
                "SELECT id FROM downloads WHERE id=? AND quality=?",
                (item_id, quality),
            )
            return await cursor.fetchone()


def get_stats(db_path):
    """
    Retorna um dicionario rico com todas as estatisticas do banco de downloads.
    Usado pelo comando `qobuz-dl stats` pra exibir um painel completo.
    """
    if not db_path:
        return {}

    empty = {
        "total": 0,
        "albums": 0,
        "tracks": 0,
        "hires": 0,
        "flac": 0,
        "mp3": 0,
        "quality_met": 0,
        "quality_not_met": 0,
        "unique_artists": 0,
        "unique_albums": 0,
        "top_artists": [],
        "formats": {},
        "bit_depths": {},
        "sample_rates": {},
        "oldest": None,
        "newest": None,
        "artist_list": [],
    }

    try:
        with sqlite3.connect(db_path) as conn:
            c = conn.cursor()

            # totais gerais
            c.execute("SELECT COUNT(*) FROM downloads")
            total = c.fetchone()[0]
            if total == 0:
                return empty

            c.execute("SELECT COUNT(*) FROM downloads WHERE media_type='album'")
            albums = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM downloads WHERE media_type='track'")
            tracks = c.fetchone()[0]

            # hi-res: bit_depth >= 24
            c.execute(
                "SELECT COUNT(*) FROM downloads WHERE CAST(bit_depth AS INTEGER) >= 24"
            )
            hires = c.fetchone()[0]

            # formatos
            c.execute(
                "SELECT file_format, COUNT(*) FROM downloads GROUP BY file_format ORDER BY COUNT(*) DESC"
            )
            formats = {row[0]: row[1] for row in c.fetchall()}
            flac_count = formats.get("FLAC", 0)
            mp3_count = formats.get("MP3", 0)

            # qualidade atingida
            c.execute("SELECT COUNT(*) FROM downloads WHERE quality_met=1")
            qmet = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM downloads WHERE quality_met=0")
            qnotmet = c.fetchone()[0]

            # artistas e albums unicos
            c.execute("SELECT COUNT(DISTINCT artist) FROM downloads WHERE artist != ''")
            unique_artists = c.fetchone()[0]

            c.execute("SELECT COUNT(DISTINCT album) FROM downloads WHERE album != ''")
            unique_albums = c.fetchone()[0]

            # top 10 artistas por numero de downloads
            c.execute("""
                SELECT artist, COUNT(*) as cnt
                FROM downloads
                WHERE artist != ''
                GROUP BY artist
                ORDER BY cnt DESC
                LIMIT 10
            """)
            top_artists = c.fetchall()  # lista de (nome, count)

            # distribuicao de bit depth
            c.execute("""
                SELECT bit_depth, COUNT(*) FROM downloads
                WHERE bit_depth IS NOT NULL AND bit_depth != ''
                GROUP BY bit_depth ORDER BY CAST(bit_depth AS INTEGER) DESC
            """)
            bit_depths = {row[0]: row[1] for row in c.fetchall()}

            # distribuicao de sample rate
            c.execute("""
                SELECT sampling_rate, COUNT(*) FROM downloads
                WHERE sampling_rate IS NOT NULL AND sampling_rate != ''
                GROUP BY sampling_rate ORDER BY CAST(sampling_rate AS REAL) DESC
            """)
            sample_rates = {row[0]: row[1] for row in c.fetchall()}

            # datas extremas
            c.execute(
                "SELECT MIN(release_date), MAX(release_date) FROM downloads WHERE release_date != ''"
            )
            dates = c.fetchone()
            oldest = dates[0] if dates else None
            newest = dates[1] if dates else None

            # lista completa de artistas
            c.execute(
                "SELECT DISTINCT artist FROM downloads WHERE artist != '' ORDER BY artist COLLATE NOCASE ASC"
            )
            artist_list = [row[0] for row in c.fetchall()]

            return {
                "total": total,
                "albums": albums,
                "tracks": tracks,
                "hires": hires,
                "flac": flac_count,
                "mp3": mp3_count,
                "quality_met": qmet,
                "quality_not_met": qnotmet,
                "unique_artists": unique_artists,
                "unique_albums": unique_albums,
                "top_artists": top_artists,
                "formats": formats,
                "bit_depths": bit_depths,
                "sample_rates": sample_rates,
                "oldest": oldest,
                "newest": newest,
                "artist_list": artist_list,
            }

    except sqlite3.Error:
        return empty
