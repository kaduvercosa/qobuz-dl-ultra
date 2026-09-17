"""Testa qobuz_dl/db.py: o banco SQLite que sustenta a feature de "Smart
Reverse Lookup" -- decide se um item já foi baixado antes (pra pular) e
guarda as estatísticas mostradas em `qobuz-dl stats`.

Por que este módulo importa tanto: ao contrário de report_viewer.py ou
stats_view.py (só exibição), db.py é ESTADO -- um bug aqui não mostra um
número errado na tela, ele faz o programa baixar de novo algo que já
tinha, ou (pior, embora não seja o comportamento observado aqui) achar
que já tem algo que na verdade nunca foi baixado.

As três frentes cobertas:
  1. create_db() -- criação do zero E as duas migrações de schema
     (v1 sem colunas extras -> v2; v2 sem artist/album -> v2.1.4).
  2. handle_download_id() -- insert/lookup assíncrono, incluindo o caso
     de PRIMARY KEY duplicada (mesmo id+quality) não derrubar o programa.
  3. get_stats() -- agregações usadas pelo comando `stats`.
"""

import contextlib
import sqlite3

import pytest

from qobuz_dl import db as db_module
from qobuz_dl.db import create_db, get_stats, handle_download_id

pytestmark = pytest.mark.unit


class _ConexaoComFalhaSimulada:
    """Encapsula uma sqlite3.Connection real, mas faz `execute()` levantar
    um erro específico quando o SQL bate com um predicado -- usado pra
    forçar caminhos de erro (race conditions, ALTER TABLE que falha) que
    não dá pra provocar só manipulando o conteúdo do banco."""

    def __init__(self, real, quando, erro):
        self._real = real
        self._quando = quando
        self._erro = erro

    def execute(self, sql, *args, **kwargs):
        if self._quando(sql):
            raise self._erro
        return self._real.execute(sql, *args, **kwargs)

    def cursor(self):
        return self._real.cursor()

    def close(self):
        return self._real.close()

    def __enter__(self):
        self._real.__enter__()
        return self

    def __exit__(self, *exc):
        return self._real.__exit__(*exc)


def _patch_connect_com_falha(monkeypatch, quando, erro):
    """Faz db.sqlite3.connect(...) devolver uma conexão real encapsulada
    por _ConexaoComFalhaSimulada, sem trocar o motor sqlite3 de verdade
    (só intercepta o SQL específico que o teste quer fazer falhar)."""
    original_connect = sqlite3.connect

    def _fake_connect(caminho, *args, **kwargs):
        real_conn = original_connect(caminho, *args, **kwargs)
        return _ConexaoComFalhaSimulada(real_conn, quando, erro)

    monkeypatch.setattr(db_module.sqlite3, "connect", _fake_connect)


@contextlib.contextmanager
def _connect(caminho):
    """sqlite3.connect() cujo `with conn:` embutido só cuida de
    commit/rollback da transação -- ele NUNCA fecha a conexão (armadilha
    bem conhecida do módulo sqlite3: `Connection.__exit__` não chama
    `close()`). Este arquivo abria 16 conexões desse jeito sem fechar
    nenhuma, contribuindo file descriptors vazados pra suíte inteira até
    o ponto de outro arquivo, bem mais adiante, quebrar com
    `OSError: Too many open files` num ambiente com ulimit apertado
    (a-Shell/iOS) -- ver a docstring de tests/unit/test_lyrics_engine_unit.py
    pra o sintoma. Este helper garante o close() de verdade, preservando o
    mesmo commit/rollback automático que `with conn:` já dava.
    """
    conn = sqlite3.connect(caminho)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# create_db()
# ---------------------------------------------------------------------------
class TestCreateDbDoZero:
    def test_devolve_o_proprio_caminho(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        assert create_db(caminho) == caminho

    def test_cria_a_tabela_com_o_schema_atual(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        with _connect(caminho) as conn:
            colunas = {info[1] for info in conn.execute("PRAGMA table_info(downloads)")}

        esperadas = {
            "id",
            "media_type",
            "quality",
            "file_format",
            "quality_met",
            "bit_depth",
            "sampling_rate",
            "saved_path",
            "status",
            "url",
            "release_date",
            "artist",
            "album",
        }
        assert esperadas <= colunas

    def test_chamar_duas_vezes_nao_quebra(self, tmp_path):
        """create_db() roda toda inicialização do programa -- tem que ser
        idempotente, nunca falhar só porque o banco já existe."""
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        create_db(caminho)  # não pode levantar exceção

        with _connect(caminho) as conn:
            n_tabelas = conn.execute(
                "SELECT count(name) FROM sqlite_master WHERE type='table' AND name='downloads'"
            ).fetchone()[0]
        assert n_tabelas == 1

    def test_create_table_com_operational_error_e_absorvido(
        self, tmp_path, monkeypatch
    ):
        """Simula a corrida entre processos citada no comentário do código:
        outro processo cria a tabela entre a checagem inicial (PASSO 1) e
        o CREATE TABLE deste. sqlite3.OperationalError nesse ponto
        específico tem que ser ignorado, não propagado."""
        caminho = str(tmp_path / "downloads.db")
        _patch_connect_com_falha(
            monkeypatch,
            quando=lambda sql: "CREATE TABLE downloads (" in sql,
            erro=sqlite3.OperationalError("table downloads already exists"),
        )

        resultado = create_db(caminho)  # não pode levantar

        assert resultado == caminho


class TestMigracaoV1ParaV2:
    """Banco antigo (v1) só tinha a coluna "id" -- sem quality, sem
    file_format, sem nada. create_db() precisa detectar isso e migrar sem
    perder os IDs já gravados."""

    def _criar_banco_v1(self, caminho, ids):
        with _connect(caminho) as conn:
            conn.execute('CREATE TABLE downloads ("id" text NOT NULL PRIMARY KEY)')
            for item_id in ids:
                conn.execute("INSERT INTO downloads (id) VALUES (?)", (item_id,))

    def test_preserva_os_ids_antigos(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v1(caminho, ["abc123", "def456"])

        create_db(caminho)

        with _connect(caminho) as conn:
            ids = {row[0] for row in conn.execute("SELECT id FROM downloads")}
        assert ids == {"abc123", "def456"}

    def test_colunas_novas_ganham_os_defaults_documentados(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v1(caminho, ["abc123"])

        create_db(caminho)

        with _connect(caminho) as conn:
            linha = conn.execute(
                "SELECT media_type, quality, file_format, quality_met, saved_path, "
                "status, artist, album FROM downloads WHERE id='abc123'"
            ).fetchone()
        assert linha == ("album", 27, "FLAC", 0, "", "downloaded", "", "")

    def test_tabela_antiga_temporaria_nao_sobra(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v1(caminho, ["abc123"])

        create_db(caminho)

        with _connect(caminho) as conn:
            existe = conn.execute(
                "SELECT count(name) FROM sqlite_master WHERE type='table' AND name='downloads_old'"
            ).fetchone()[0]
        assert existe == 0

    def test_banco_legado_malformado_com_ids_repetidos_e_deduplicado(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        with _connect(caminho) as conn:
            conn.execute('CREATE TABLE downloads ("id" text NOT NULL)')
            conn.executemany(
                "INSERT INTO downloads (id) VALUES (?)",
                [("abc123",), ("abc123",), ("def456",)],
            )

        create_db(caminho)

        with _connect(caminho) as conn:
            ids = [
                row[0] for row in conn.execute("SELECT id FROM downloads ORDER BY id")
            ]
        assert ids == ["abc123", "def456"]

    def test_contagem_divergente_apos_copiar_ids_aborta_a_migracao(
        self, tmp_path, monkeypatch
    ):
        """Guarda de integridade pós-cópia (comentário no código: "contagem
        divergente após copiar os IDs legados"). Faz a contagem final
        VIR ERRADA (sem a query explodir sozinha) pra exercitar o próprio
        `raise sqlite3.DatabaseError(...)` -- e não só o `except` genérico
        que capturaria qualquer outro erro de SQLite. Nunca observado na
        prática com SQLite saudável, mas é a rede de segurança do código:
        ROLLBACK do savepoint, log de erro e re-raise."""
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v1(caminho, ["abc123", "def456"])

        class _CursorComContagemFalsa:
            def fetchone(self):
                return (999,)  # força old_count(2) != new_count(999)

        original_connect = sqlite3.connect

        def _fake_connect(caminho_db, *args, **kwargs):
            real_conn = original_connect(caminho_db, *args, **kwargs)

            class _ConexaoComContagemFalsa:
                def execute(self, sql, *a, **k):
                    if sql.strip() == "SELECT COUNT(*) FROM downloads":
                        return _CursorComContagemFalsa()
                    return real_conn.execute(sql, *a, **k)

                def cursor(self):
                    return real_conn.cursor()

                def close(self):
                    return real_conn.close()

                def __enter__(self):
                    real_conn.__enter__()
                    return self

                def __exit__(self, *exc):
                    return real_conn.__exit__(*exc)

            return _ConexaoComContagemFalsa()

        monkeypatch.setattr(db_module.sqlite3, "connect", _fake_connect)

        with pytest.raises(sqlite3.DatabaseError, match="contagem divergente"):
            create_db(caminho)


class TestMigracaoV2ParaV2_1_4:
    """Banco intermediário: já tem "quality" e companhia, mas ainda não
    tem "artist"/"album" (adicionadas numa versão mais recente)."""

    def _criar_banco_v2_sem_artista(self, caminho):
        with _connect(caminho) as conn:
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
                  PRIMARY KEY ("id", "quality")
                )
            """)
            conn.execute("INSERT INTO downloads (id, quality) VALUES ('xyz789', 27)")

    def test_adiciona_artist_e_album_sem_perder_dados(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v2_sem_artista(caminho)

        create_db(caminho)

        with _connect(caminho) as conn:
            colunas = {info[1] for info in conn.execute("PRAGMA table_info(downloads)")}
            linha = conn.execute(
                "SELECT id, artist, album FROM downloads WHERE id='xyz789'"
            ).fetchone()

        assert {"artist", "album"} <= colunas
        assert linha == ("xyz789", "", "")

    def test_banco_ja_atualizado_nao_e_mexido(self, tmp_path):
        """Se "artist" já existe, create_db() não deve fazer nada -- só
        uma checagem de sanidade de que a branch certa (nenhuma migração)
        é tomada sem erro."""
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)  # cria já no schema atual
        create_db(caminho)  # roda de novo -- não deve migrar nem quebrar

        with _connect(caminho) as conn:
            colunas = {info[1] for info in conn.execute("PRAGMA table_info(downloads)")}
        assert "artist" in colunas and "album" in colunas

    def _criar_banco_v2_com_uma_das_duas(self, caminho, presente):
        """Banco intermediário com só UMA das duas colunas novas -- cada
        `if` de ALTER TABLE (artist/album) precisa ser exercitado nos dois
        sentidos (True e False) independentemente um do outro."""
        with _connect(caminho) as conn:
            conn.execute(f"""
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
                  "{presente}" text NOT NULL DEFAULT '',
                  PRIMARY KEY ("id", "quality")
                )
            """)

    def test_so_falta_album_pula_o_alter_de_artist(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v2_com_uma_das_duas(caminho, presente="artist")

        create_db(caminho)

        with _connect(caminho) as conn:
            colunas = {info[1] for info in conn.execute("PRAGMA table_info(downloads)")}
        assert {"artist", "album"} <= colunas

    def test_so_falta_artist_pula_o_alter_de_album(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v2_com_uma_das_duas(caminho, presente="album")

        create_db(caminho)

        with _connect(caminho) as conn:
            colunas = {info[1] for info in conn.execute("PRAGMA table_info(downloads)")}
        assert {"artist", "album"} <= colunas

    def test_falha_no_alter_table_e_logada_sem_propagar(self, tmp_path, monkeypatch):
        """Se o ALTER TABLE falhar (ex.: banco bloqueado por outro
        processo), create_db() loga o erro e segue -- não derruba a
        inicialização do programa inteiro por causa de uma coluna que não
        deu pra adicionar agora."""
        caminho = str(tmp_path / "downloads.db")
        self._criar_banco_v2_com_uma_das_duas(caminho, presente="album")
        _patch_connect_com_falha(
            monkeypatch,
            quando=lambda sql: "ADD COLUMN artist" in sql,
            erro=sqlite3.OperationalError("duplicate column name: artist"),
        )

        resultado = create_db(caminho)  # não pode levantar

        assert resultado == caminho
        with _connect(caminho) as conn:
            colunas = {info[1] for info in conn.execute("PRAGMA table_info(downloads)")}
        # a tentativa falhou de propósito -- "artist" continua ausente
        assert "artist" not in colunas


# ---------------------------------------------------------------------------
# handle_download_id()
# ---------------------------------------------------------------------------
class TestHandleDownloadId:
    async def test_sem_db_path_e_no_op(self):
        """Feature desligada (sem --database) -> não pode tentar abrir
        arquivo nenhum, só devolve None."""
        resultado = await handle_download_id(None, "algum-id", add_id=True)
        assert resultado is None

    async def test_insere_e_depois_acha_por_lookup(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        await handle_download_id(caminho, "id-1", add_id=True, quality=27)
        encontrado = await handle_download_id(caminho, "id-1", add_id=False, quality=27)

        assert encontrado is not None
        assert encontrado[0] == "id-1"

    async def test_lookup_de_algo_que_nunca_foi_baixado_devolve_none(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        assert await handle_download_id(caminho, "nunca-existiu", add_id=False) is None

    async def test_lookup_remove_registro_cujo_arquivo_sumiu(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        await handle_download_id(
            caminho,
            "stale",
            add_id=True,
            quality=27,
            saved_path=str(tmp_path / "ausente.flac"),
        )

        assert await handle_download_id(caminho, "stale", quality=27) is None
        with _connect(caminho) as conn:
            assert conn.execute("SELECT COUNT(*) FROM downloads").fetchone()[0] == 0

    async def test_mesma_qualidade_duas_vezes_nao_derruba_o_programa(self, tmp_path):
        """PRIMARY KEY (id, quality) -- inserir a mesma combinação de
        novo violaria a constraint. Isso é esperado (ex.: duas execuções
        concorrentes) e tem que ser absorvido, não propagado como
        exceção."""
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        await handle_download_id(caminho, "id-1", add_id=True, quality=27)
        await handle_download_id(
            caminho, "id-1", add_id=True, quality=27
        )  # não deve lançar

        with _connect(caminho) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE id='id-1'"
            ).fetchone()[0]
        assert n == 1  # só uma linha, o segundo insert foi ignorado

    async def test_mesmo_id_qualidade_diferente_sao_dois_registros(self, tmp_path):
        """O mesmo item baixado em MP3 e depois em Hi-Res -- não é
        duplicata, a PRIMARY KEY composta permite os dois."""
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        await handle_download_id(caminho, "id-1", add_id=True, quality=5)
        await handle_download_id(caminho, "id-1", add_id=True, quality=27)

        with _connect(caminho) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM downloads WHERE id='id-1'"
            ).fetchone()[0]
        assert n == 2

    async def test_todos_os_campos_sao_gravados_de_verdade(self, tmp_path):
        """Round-trip completo -- grava com todos os campos preenchidos e
        confere que nenhum se perdeu ou trocou de lugar na tupla de
        parâmetros do INSERT (erro fácil de cometer nessa lista longa)."""
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        await handle_download_id(
            caminho,
            "id-completo",
            add_id=True,
            media_type="track",
            quality=27,
            file_format="FLAC",
            quality_met=1,
            bit_depth="24",
            sampling_rate="96",
            saved_path="/musica/Artista/Album/01 Faixa.flac",
            status="downloaded",
            url="https://open.qobuz.com/track/123",
            release_date="2024-01-01",
            artist="Artista Exemplo",
            album="Album Exemplo",
        )

        with _connect(caminho) as conn:
            conn.row_factory = sqlite3.Row
            linha = conn.execute(
                "SELECT * FROM downloads WHERE id='id-completo'"
            ).fetchone()

        assert linha["media_type"] == "track"
        assert linha["quality_met"] == 1
        assert linha["bit_depth"] == "24"
        assert linha["sampling_rate"] == "96"
        assert linha["saved_path"] == "/musica/Artista/Album/01 Faixa.flac"
        assert linha["url"] == "https://open.qobuz.com/track/123"
        assert linha["release_date"] == "2024-01-01"
        assert linha["artist"] == "Artista Exemplo"
        assert linha["album"] == "Album Exemplo"

    async def test_erro_de_banco_diferente_de_integrity_e_logado_sem_propagar(
        self, tmp_path, monkeypatch
    ):
        """O except sqlite3.IntegrityError (chave duplicada) já tinha
        teste; faltava o `except sqlite3.Error` genérico logo abaixo --
        outro tipo de erro de banco (disco cheio, arquivo bloqueado por
        outro processo etc.) que também não pode derrubar o programa,
        só logar e seguir."""
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        class _ConexaoComErroGenerico:
            async def execute(self, sql, params=()):
                raise sqlite3.OperationalError("disco cheio")

            async def commit(self):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

        monkeypatch.setattr(
            db_module.aiosqlite, "connect", lambda *a, **k: _ConexaoComErroGenerico()
        )

        resultado = await handle_download_id(caminho, "id-x", add_id=True, quality=27)

        assert resultado is None


# ---------------------------------------------------------------------------
# get_stats()
# ---------------------------------------------------------------------------
class TestGetStats:
    def test_sem_db_path_devolve_dict_vazio(self):
        assert get_stats(None) == {}

    def test_banco_sem_nenhum_registro_devolve_tudo_zerado(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)

        stats = get_stats(caminho)

        assert stats["total"] == 0
        assert stats["top_artists"] == []
        assert stats["artist_list"] == []

    def test_banco_invalido_nao_quebra(self, tmp_path):
        """Arquivo que existe mas não é um banco SQLite válido -- tem que
        cair no `except sqlite3.Error` e devolver o dict vazio, não
        propagar a exceção pro comando `stats` inteiro."""
        caminho = tmp_path / "nao_e_sqlite.db"
        caminho.write_text("isto nao e' um banco sqlite")

        stats = get_stats(str(caminho))

        assert stats["total"] == 0

    def _popular(self, caminho, registros):
        """Insere registros crus direto via SQL -- não depende de
        handle_download_id() pra manter este teste isolado do outro."""
        with _connect(caminho) as conn:
            for r in registros:
                conn.execute(
                    """INSERT INTO downloads
                    (id, media_type, quality, file_format, quality_met, bit_depth,
                     sampling_rate, artist, album, release_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        r["id"],
                        r.get("media_type", "album"),
                        r.get("quality", 27),
                        r.get("file_format", "FLAC"),
                        r.get("quality_met", 0),
                        r.get("bit_depth"),
                        r.get("sampling_rate"),
                        r.get("artist", ""),
                        r.get("album", ""),
                        r.get("release_date", ""),
                    ),
                )

    def test_totais_gerais(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "media_type": "album"},
                {"id": "2", "media_type": "album"},
                {"id": "3", "media_type": "track"},
            ],
        )

        stats = get_stats(caminho)

        assert stats["total"] == 3
        assert stats["albums"] == 2
        assert stats["tracks"] == 1

    def test_hires_conta_bit_depth_24_ou_mais(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "bit_depth": "16"},
                {"id": "2", "bit_depth": "24"},
                {"id": "3", "bit_depth": "32"},
            ],
        )

        assert get_stats(caminho)["hires"] == 2

    def test_distribuicao_por_formato(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "file_format": "FLAC"},
                {"id": "2", "file_format": "FLAC"},
                {"id": "3", "file_format": "MP3"},
            ],
        )

        stats = get_stats(caminho)
        assert stats["flac"] == 2
        assert stats["mp3"] == 1
        assert stats["formats"] == {"FLAC": 2, "MP3": 1}

    def test_quality_met_e_not_met(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "quality_met": 1},
                {"id": "2", "quality_met": 1},
                {"id": "3", "quality_met": 0},
            ],
        )

        stats = get_stats(caminho)
        assert stats["quality_met"] == 2
        assert stats["quality_not_met"] == 1

    def test_artistas_e_albuns_unicos_ignoram_string_vazia(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "artist": "Artista A", "album": "Album X"},
                {"id": "2", "artist": "Artista A", "album": "Album Y"},
                {"id": "3", "artist": "", "album": ""},  # sem metadados
            ],
        )

        stats = get_stats(caminho)
        assert stats["unique_artists"] == 1
        assert stats["unique_albums"] == 2

    def test_top_artists_ordenado_por_contagem_decrescente(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "artist": "Menos Popular"},
                {"id": "2", "artist": "Mais Popular"},
                {"id": "3", "artist": "Mais Popular"},
                {"id": "4", "artist": "Mais Popular"},
            ],
        )

        top = get_stats(caminho)["top_artists"]
        assert top[0] == ("Mais Popular", 3)
        assert ("Menos Popular", 1) in top

    def test_artist_list_ordenada_alfabeticamente_sem_case(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "artist": "zebra"},
                {"id": "2", "artist": "Abelha"},
                {"id": "3", "artist": "Meio"},
            ],
        )

        assert get_stats(caminho)["artist_list"] == ["Abelha", "Meio", "zebra"]

    def test_oldest_e_newest_release_date(self, tmp_path):
        caminho = str(tmp_path / "downloads.db")
        create_db(caminho)
        self._popular(
            caminho,
            [
                {"id": "1", "release_date": "2020-05-01"},
                {"id": "2", "release_date": "1999-01-01"},
                {"id": "3", "release_date": "2023-12-31"},
            ],
        )

        stats = get_stats(caminho)
        assert stats["oldest"] == "1999-01-01"
        assert stats["newest"] == "2023-12-31"
