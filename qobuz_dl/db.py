import logging
import sqlite3

import aiosqlite

# CYAN/YELLOW importados como INFO/WARNING renomeados: mesma cor de
# YELLOW (mantida por convencao), mas CYAN agora e' LIGHTBLUE_EX --
# visivel em terminal claro E escuro (CYAN puro quase some em fundo
# branco). Ver comentario completo em qobuz_dl/color.py. Zero mudanca
# de codigo neste arquivo: toda f-string que ja usa {CYAN}/{YELLOW}
# continua funcionando, so' a cor de fato renderizada muda.
from qobuz_dl.color import OFF, RED
from qobuz_dl.color import WARNING as YELLOW

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
    # Conexao sincrona (sqlite3) porque essa funcao roda uma unica vez
    # na inicializacao do programa -- nao ha necessidade de async aqui,
    # diferente de handle_download_id() que roda por faixa/album.
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # PASSO 1: verifica se a tabela "downloads" ja existe no banco
        cursor.execute(
            "SELECT count(name) FROM sqlite_master WHERE type='table' AND name='downloads'"
        )

        if cursor.fetchone()[0] == 1:
            # Tabela existe -> le as colunas atuais pra decidir se precisa migrar
            cursor.execute("PRAGMA table_info(downloads)")
            columns = [info[1] for info in cursor.fetchall()]

            # MIGRACAO LEGADA (v1 -> v2): banco antigo so tinha coluna "id",
            # sem quality/file_format/etc. Se "quality" nao existe, e' banco v1.
            if "quality" not in columns:
                logger.info(f"{YELLOW}Migrating old database to the new format...{OFF}")

                # Renomeia a tabela antiga pra nao perder os dados
                conn.execute("ALTER TABLE downloads RENAME TO downloads_old")

                # Cria a tabela nova ja no schema atual (com artist/album inclusos)
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

                # Copia so os IDs antigos (unico dado confiavel que a tabela v1 tinha);
                # o resto das colunas fica com os valores DEFAULT definidos acima
                try:
                    conn.execute(
                        "INSERT INTO downloads (id) SELECT id FROM downloads_old"
                    )
                except sqlite3.Error as e:
                    logger.error(f"{RED}Failed to migrate old data: {e}{OFF}")

                # Remove a tabela temporaria depois de copiar os dados
                conn.execute("DROP TABLE downloads_old")
                logger.info(f"{YELLOW}Database successfully updated!{OFF}")

            # MIGRACAO NOVA (v2 -> v2.1.4): banco ja tem "quality" mas ainda
            # nao tem "artist"/"album" (adicionados numa versao mais recente).
            # ALTER TABLE ADD COLUMN aqui em vez de recriar a tabela toda,
            # porque so' precisa adicionar 2 colunas, sem quebrar nada existente.
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
            # se "artist" ja existe -> banco esta na versao mais atual, nao faz nada

        else:
            # Tabela nao existe -> primeira execucao, cria do zero ja no schema atual
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
                # ja existe (corrida entre processos, por exemplo) -> ignora
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
    # Se nao foi passado db_path, feature de reverse lookup esta desligada -> no-op
    if not db_path:
        return

    # Chave PRIMARY KEY e' (id, quality) -> mesmo item_id pode existir
    # varias vezes com qualidades diferentes (ex: baixou em MP3 e depois em Hi-Res)
    async with aiosqlite.connect(db_path) as conn:
        if add_id:
            # MODO INSERT: grava um novo download concluido
            try:
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
                # violou PRIMARY KEY (id, quality) -> ja foi baixado nessa mesma
                # qualidade antes; nao e' erro real, so' avisa e segue
                logger.info(f"{YELLOW}[i] Already in database, skipping.{OFF}")
            except sqlite3.Error as e:
                logger.error(f"{RED}Unexpected DB error: {e}{OFF}")
        else:
            # MODO CONSULTA (lookup): so' checa se (id, quality) ja foi baixado antes,
            # usado pra decidir se pula o download (feature de Smart Reverse Lookup)
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
    # Sem banco configurado -> nada pra mostrar
    if not db_path:
        return {}

    # Dicionario "vazio" usado como valor de retorno padrao quando o banco
    # nao tem nenhum registro ainda, ou em caso de erro -- assim quem chama
    # get_stats() nunca precisa checar None, so' checar total == 0
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

            # --- totais gerais ---
            c.execute("SELECT COUNT(*) FROM downloads")
            total = c.fetchone()[0]
            if total == 0:
                return empty

            c.execute("SELECT COUNT(*) FROM downloads WHERE media_type='album'")
            albums = c.fetchone()[0]

            c.execute("SELECT COUNT(*) FROM downloads WHERE media_type='track'")
            tracks = c.fetchone()[0]

            # --- hi-res: considera hi-res qualquer item com bit_depth >= 24 bits ---
            c.execute(
                "SELECT COUNT(*) FROM downloads WHERE CAST(bit_depth AS INTEGER) >= 24"
            )
            hires = c.fetchone()[0]

            # --- distribuicao por formato de arquivo (FLAC, MP3, etc) ---
            c.execute(
                "SELECT file_format, COUNT(*) FROM downloads GROUP BY file_format ORDER BY COUNT(*) DESC"
            )
            formats = {row[0]: row[1] for row in c.fetchall()}
            flac_count = formats.get("FLAC", 0)
            mp3_count = formats.get("MP3", 0)

            # --- quantos downloads bateram a qualidade pedida vs nao bateram ---
            c.execute("SELECT COUNT(*) FROM downloads WHERE quality_met=1")
            qmet = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM downloads WHERE quality_met=0")
            qnotmet = c.fetchone()[0]

            # --- contagem de artistas/albuns unicos (ignora string vazia) ---
            c.execute("SELECT COUNT(DISTINCT artist) FROM downloads WHERE artist != ''")
            unique_artists = c.fetchone()[0]

            c.execute("SELECT COUNT(DISTINCT album) FROM downloads WHERE album != ''")
            unique_albums = c.fetchone()[0]

            # --- top 10 artistas com mais downloads ---
            c.execute("""
                SELECT artist, COUNT(*) as cnt
                FROM downloads
                WHERE artist != ''
                GROUP BY artist
                ORDER BY cnt DESC
                LIMIT 10
            """)
            top_artists = c.fetchall()  # lista de tuplas (nome, count)

            # --- distribuicao por bit depth (16, 24, etc), ordenada da maior pra menor ---
            c.execute("""
                SELECT bit_depth, COUNT(*) FROM downloads
                WHERE bit_depth IS NOT NULL AND bit_depth != ''
                GROUP BY bit_depth ORDER BY CAST(bit_depth AS INTEGER) DESC
            """)
            bit_depths = {row[0]: row[1] for row in c.fetchall()}

            # --- distribuicao por sample rate (44.1, 96, 192 kHz, etc) ---
            c.execute("""
                SELECT sampling_rate, COUNT(*) FROM downloads
                WHERE sampling_rate IS NOT NULL AND sampling_rate != ''
                GROUP BY sampling_rate ORDER BY CAST(sampling_rate AS REAL) DESC
            """)
            sample_rates = {row[0]: row[1] for row in c.fetchall()}

            # --- data de lancamento mais antiga e mais recente da biblioteca baixada ---
            c.execute(
                "SELECT MIN(release_date), MAX(release_date) FROM downloads WHERE release_date != ''"
            )
            dates = c.fetchone()
            oldest = dates[0] if dates else None
            newest = dates[1] if dates else None

            # --- lista completa de artistas, ordenada alfabeticamente (case-insensitive) ---
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
        # qualquer erro de SQL/conexao -> devolve o dict vazio em vez de quebrar
        return empty
