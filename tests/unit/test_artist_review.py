"""Testes da revisão manual de artistas seguidos."""

import asyncio
import json
import sqlite3
from types import SimpleNamespace

from qobuz_dl.artist_review import (
    _open_profile_url,
    _SelectionStore,
    cmd_artist_review,
    confirm_artists,
    playlist_name_for,
    refresh_artist_catalog,
    undo_artists,
)
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.library_db import LibraryDB


class FakeClient:
    def __init__(self, artists, releases_by_artist):
        self.artists = artists
        self.releases_by_artist = releases_by_artist

    async def get_all_favorites(self, fav_type, *, page_size, max_pages):
        assert fav_type == "artists"
        return self.artists, len(self.artists)

    async def get_artist_meta(self, artist_id):
        yield {"albums": {"items": self.releases_by_artist.get(str(artist_id), [])}}


class FakePlaylistClient:
    """Cliente falso: lista e cria playlists sem tocar no Qobuz."""

    def __init__(self, existing=(), fail=()):
        self.existing = list(existing)
        self.fail = set(fail)
        self.created = []

    async def get_user_playlists(self, limit=100):
        items = [{"id": 900 + i, "name": n} for i, n in enumerate(self.existing)]
        # como no Qobuz de verdade, o que foi criado passa a aparecer na listagem
        items += [{"id": 101 + i, "name": n} for i, n in enumerate(self.created)]
        return {"playlists": {"items": items, "total": len(items)}}

    async def create_qobuz_playlist(self, name, description="", is_public=False):
        if name in self.fail:
            return None
        self.created.append(name)
        return str(100 + len(self.created))


class FakeSession:
    def __init__(self, *answers):
        self.answers = iter(answers)

    async def prompt_async(self, _prompt):
        return next(self.answers)


def test_catalogo_persiste_revisao_e_novo_lancamento(tmp_path):
    lib = LibraryDB(tmp_path / "library.db")
    artists = [{"source_artist_id": "10", "name": "Álvaro"}]
    assert lib.sync_followed_artists("qobuz", artists) == 1

    release = {
        "source_release_id": "101",
        "title": "Primeiro lançamento",
        "release_date": "2026-10-01",
        "release_type": "Album",
    }
    # A discografia já existente no primeiro scan define o baseline, não vira
    # uma lista inteira de lançamentos novos.
    assert lib.record_artist_releases("qobuz", "10", [release]) == 0
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 0
    assert lib.mark_artist_reviewed("qobuz", "10") is True
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 0
    assert lib.get_artist_releases("qobuz", "10")[0]["reviewed_at"] is not None

    old_release = {
        "source_release_id": "100",
        "title": "Catálogo antigo descoberto depois",
        "release_date": "2020-01-01",
    }
    assert lib.record_artist_releases("qobuz", "10", [old_release]) == 0
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 0

    # A sincronização repetida do mesmo ID não o torna pendente outra vez.
    assert lib.record_artist_releases("qobuz", "10", [release]) == 0
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 0

    new_release = {
        "source_release_id": "102",
        "title": "Novo single",
        "release_date": "2099-01-01",
    }
    assert lib.record_artist_releases("qobuz", "10", [new_release]) == 1
    artist = lib.get_followed_artists("qobuz")[0]
    assert artist["pending_count"] == 1
    assert artist["last_reviewed_at"] is not None


def test_catalogo_lista_artistas_em_ordem_alfabetica(tmp_path):
    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz",
        [
            {"source_artist_id": "3", "name": "Zeca"},
            {"source_artist_id": "1", "name": "ana"},
            {"source_artist_id": "2", "name": "Bia"},
            {"source_artist_id": "4", "name": "Álvaro"},
        ],
    )
    assert [a["name"] for a in lib.get_followed_artists("qobuz")] == [
        "Álvaro",
        "ana",
        "Bia",
        "Zeca",
    ]


def test_banco_versao_3_migra_para_baseline_e_pendencias(tmp_path):
    path = tmp_path / "library.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE followed_artists (
                source TEXT NOT NULL,
                source_artist_id TEXT NOT NULL,
                name TEXT NOT NULL,
                is_followed INTEGER NOT NULL DEFAULT 1,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                last_reviewed_at TEXT,
                last_catalog_sync_at TEXT,
                PRIMARY KEY (source, source_artist_id)
            );
            CREATE TABLE artist_releases (
                source TEXT NOT NULL,
                source_artist_id TEXT NOT NULL,
                source_release_id TEXT NOT NULL,
                title TEXT NOT NULL,
                release_date TEXT,
                release_type TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                reviewed_at TEXT,
                PRIMARY KEY (source, source_artist_id, source_release_id)
            );
            INSERT INTO followed_artists
                (source, source_artist_id, name, first_seen_at, last_seen_at,
                 last_reviewed_at, last_catalog_sync_at)
            VALUES ('qobuz', '50', 'Artista conferido', '2025-01-01', '2026-10-10',
                    '2026-10-10T00:00:00+00:00', '2026-10-10T00:00:00+00:00');
            INSERT INTO artist_releases
                (source, source_artist_id, source_release_id, title, release_date,
                 first_seen_at, last_seen_at)
            VALUES ('qobuz', '50', '500', 'Álbum antigo', '2025-01-01',
                    '2026-10-10', '2026-10-10');
            INSERT INTO artist_releases
                (source, source_artist_id, source_release_id, title, release_date,
                 first_seen_at, last_seen_at)
            VALUES ('qobuz', '50', '501', 'Lançamento posterior', '2026-10-11',
                   '2026-10-11', '2026-10-11');
            INSERT INTO followed_artists
                (source, source_artist_id, name, first_seen_at, last_seen_at,
                 last_catalog_sync_at)
            VALUES ('qobuz', '51', 'Artista sem revisão', '2025-01-01', '2026-10-10',
                    '2026-10-10T00:00:00+00:00');
            INSERT INTO artist_releases
                (source, source_artist_id, source_release_id, title, release_date,
                 first_seen_at, last_seen_at)
            VALUES ('qobuz', '51', '510', 'Catálogo histórico', '2025-01-01',
                    '2026-10-10', '2026-10-10');
            PRAGMA user_version=3;
            """
        )

    migrated = LibraryDB(path)
    with sqlite3.connect(path) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        artist_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(followed_artists)")
        }
        release_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(artist_releases)")
        }
    assert version == 5
    assert "tracking_since_at" in artist_columns
    assert "pending_review" in release_columns
    assert {"review_playlist_id", "undo_available"} <= artist_columns
    assert "undo_pending" in release_columns
    artists = {
        artist["source_artist_id"]: artist
        for artist in migrated.get_followed_artists("qobuz")
    }
    assert artists["50"]["tracking_since_at"] == "2026-10-10T00:00:00+00:00"
    assert artists["50"]["pending_count"] == 1
    assert artists["51"]["pending_count"] == 0
    assert [
        release["title"]
        for release in migrated.get_artist_releases("qobuz", "50", pending_only=True)
    ] == ["Lançamento posterior"]


def test_refresh_sincroniza_artistas_e_novos_lancamentos(tmp_path):
    lib = LibraryDB(tmp_path / "library.db")
    client = FakeClient(
        [{"id": 7, "name": "Artista"}],
        {"7": [{"id": 70, "title": "EP novo", "release_date": "2026-10-20"}]},
    )
    result = asyncio.run(refresh_artist_catalog(client, lib))
    assert result["artists"] == 1
    assert result["releases_new"] == 0
    artist = lib.get_followed_artists("qobuz")[0]
    assert artist["source_artist_id"] == "7"
    assert artist["pending_count"] == 0

    assert lib.mark_artist_reviewed("qobuz", "7") is True
    client.releases_by_artist["7"].append(
        {"id": 71, "title": "Single pós-revisão", "release_date": "2099-01-01"}
    )

    result = asyncio.run(refresh_artist_catalog(client, lib))
    assert result["releases_new"] == 1
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 1


def test_refresh_emite_progresso_das_etapas(tmp_path):
    lib = LibraryDB(tmp_path / "library.db")
    client = FakeClient([{"id": 9, "name": "Progresso"}], {"9": []})
    messages = []
    result = asyncio.run(refresh_artist_catalog(client, lib, progress=messages.append))

    assert result["artists"] == 1
    assert "Consultando artistas seguidos" in messages[0]
    assert "1 artista(s) encontrado(s)" in messages[1]
    assert "Discografia 1/1: Progresso" in messages[2]


def test_lista_mostra_urls_e_marca_varios_artistas(tmp_path, monkeypatch):
    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz",
        [
            {"source_artist_id": "2", "name": "Bia"},
            {"source_artist_id": "1", "name": "Ana"},
        ],
    )
    messages = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", messages.append)
    args = SimpleNamespace(modo="alfabetica", sem_sincronizar=True)
    client = FakePlaylistClient()
    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession("m 1,2", "q"),
        )
    )
    assert client.created == ["ANA", "BIA"]

    output = "\n".join(messages)
    assert "0/2 conferidos" in output
    assert "ARTISTA" in output and "STATUS" in output  # cabeçalho da tabela
    assert "┌" in output and "└" in output  # bordas da tabela
    assert "Ana" in output and "Bia" in output and "PENDENTE" in output
    assert "o N abre o perfil" in output
    assert all(a["last_reviewed_at"] for a in lib.get_followed_artists("qobuz"))


def test_lista_de_texto_fica_alinhada_na_largura_da_tela():
    from qobuz_dl import ui
    from qobuz_dl.artist_checklist import roster_lines, row_from_artist

    artists = [
        {"source_artist_id": "1", "name": "Ana", "pending_count": 2},
        {
            "source_artist_id": "2",
            "name": "宇多田ヒカル" * 8,
            "last_reviewed_at": "2026-10-01T10:00:00",
            "pending_count": 0,
        },
    ]
    rows = [row_from_artist(a) for a in artists]
    for columns in (40, 46, 60, 80, 120):
        lines = roster_lines(rows, columns)
        for line in lines:
            assert ui._cell_width(line) <= columns - 1, (columns, line)
    wide = "\n".join(roster_lines(rows, 100))
    assert "COM NOVOS" in wide and "CONFERIDO" in wide and "2026-10-01" in wide
    narrow = "\n".join(roster_lines(rows, 40))
    assert "┌" not in narrow and "[x]" in narrow  # lista compacta, sem tabela


def test_sorteio_mostra_tabela_de_lancamentos_sem_link_dentro_de_caixa(
    tmp_path, monkeypatch
):
    lib = _lib_com(tmp_path, "Pendente")
    lib.record_artist_releases("qobuz", "1", [])
    lib.record_artist_releases(
        "qobuz",
        "1",
        [
            {
                "source_release_id": "9",
                "title": "Disco Novo",
                "release_date": "2099-01-01",
                "release_type": "album",
            }
        ],
    )
    messages = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", messages.append)
    args = SimpleNamespace(modo="sorteio", sem_sincronizar=True)
    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=FakePlaylistClient()),
            lib=lib,
            session=FakeSession("v"),
            chooser=lambda c: c[0],
        )
    )
    output = "\n".join(messages)
    assert "ARTISTA SORTEADO" in output and "Pendente" in output
    assert "open.qobuz.com/artist/1" in output
    assert "\x1b]8;" not in output  # a-Shell não entende hiperlink OSC 8
    assert "LANÇAMENTO" in output and "Disco Novo" in output
    assert "album" in output and "2099-01-01" in output
    assert "NOVOS: 1" in output


def test_lista_abre_perfil_por_numero_sem_marcar(tmp_path, monkeypatch):
    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz",
        [
            {"source_artist_id": "2", "name": "Bia"},
            {"source_artist_id": "1", "name": "Ana"},
        ],
    )
    messages = []
    opened = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", messages.append)
    args = SimpleNamespace(modo="alfabetica", sem_sincronizar=True)

    client = FakePlaylistClient()
    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession("o 2", "m 1", "q"),
            url_opener=lambda url: opened.append(url) or True,
        )
    )

    assert opened == ["https://open.qobuz.com/artist/2"]
    assert client.created == ["ANA"]
    artists = {a["source_artist_id"]: a for a in lib.get_followed_artists("qobuz")}
    assert artists["1"]["last_reviewed_at"] is not None
    assert artists["2"]["last_reviewed_at"] is None
    assert any("Perfil aberto: Bia" in message for message in messages)


def test_abertura_no_ios_usa_comando_open_nativo(monkeypatch):
    import qobuz_dl.artist_review as artist_review

    commands = []
    monkeypatch.setattr(artist_review.sys, "platform", "ios")
    monkeypatch.setattr(
        artist_review.os, "system", lambda command: commands.append(command) or 0
    )

    assert _open_profile_url("https://open.qobuz.com/artist/42") is True
    assert commands == ["open https://open.qobuz.com/artist/42"]


def test_falha_de_discografia_nao_marca_catalogo_atualizado(tmp_path):
    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists("qobuz", [{"source_artist_id": "8", "name": "Artista"}])
    lib.record_artist_releases(
        "qobuz", "8", [{"source_release_id": "80", "title": "Existente"}]
    )
    lib.mark_artist_reviewed("qobuz", "8")
    lib.record_artist_releases(
        "qobuz",
        "8",
        [
            {
                "source_release_id": "81",
                "title": "Pendente",
                "release_date": "2099-01-01",
            }
        ],
    )
    before = lib.get_followed_artists("qobuz")[0]["last_catalog_sync_at"]

    class FailingClient(FakeClient):
        async def get_artist_meta(self, artist_id):
            raise RuntimeError("falha simulada")
            yield  # mantém esta função como gerador assíncrono

    result = asyncio.run(
        refresh_artist_catalog(FailingClient([{"id": 8, "name": "Artista"}], {}), lib)
    )
    current = lib.get_followed_artists("qobuz")[0]
    assert len(result["failures"]) == 1
    assert current["last_catalog_sync_at"] == before
    assert current["pending_count"] == 1


def test_sorteio_prioriza_artistas_com_pendencias(tmp_path):
    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz",
        [
            {"source_artist_id": "1", "name": "Já conferido"},
            {"source_artist_id": "2", "name": "Pendente"},
        ],
    )
    lib.record_artist_releases(
        "qobuz", "1", [{"source_release_id": "11", "title": "Antigo"}]
    )
    lib.mark_artist_reviewed("qobuz", "1")
    lib.record_artist_releases("qobuz", "2", [])
    lib.record_artist_releases(
        "qobuz",
        "2",
        [
            {
                "source_release_id": "22",
                "title": "Novo",
                "release_date": "2099-01-01",
            }
        ],
    )
    chosen = []
    opened = []

    def chooser(candidates):
        chosen.extend(candidates)
        return candidates[0]

    args = SimpleNamespace(modo="sorteio", sem_sincronizar=True)
    client = FakePlaylistClient()
    qobuz = SimpleNamespace(client=client)
    asyncio.run(
        cmd_artist_review(
            args,
            qobuz,
            lib=lib,
            session=FakeSession("o", "m"),
            chooser=chooser,
            url_opener=lambda url: opened.append(url) or True,
        )
    )
    assert [a["source_artist_id"] for a in chosen] == ["2"]
    assert opened == ["https://open.qobuz.com/artist/2"]
    assert client.created == ["PENDENTE"]
    assert lib.get_followed_artists("qobuz")[1]["pending_count"] == 0


def test_parser_expõe_modo_sorteio():
    args = qobuz_dl_args().parse_args(["artists", "--modo", "sorteio"])
    assert args.command == "artists"
    assert args.modo == "sorteio"
    assert args.sem_sincronizar is False


def _lib_com(tmp_path, *names):
    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz",
        [{"source_artist_id": str(i), "name": n} for i, n in enumerate(names, 1)],
    )
    return lib


def test_nome_da_playlist_em_maiusculas_com_acentos():
    assert playlist_name_for("Beyoncé") == "BEYONCÉ"
    assert playlist_name_for("  Sigur   Rós ") == "SIGUR RÓS"
    assert playlist_name_for("") == "UNKNOWN"


def test_confirmar_cria_playlist_em_maiusculas_e_grava_no_banco(tmp_path):
    lib = _lib_com(tmp_path, "Selena Gomez")
    client = FakePlaylistClient()
    artist = lib.get_followed_artists("qobuz")[0]

    result = asyncio.run(confirm_artists([artist], lib, client))

    assert client.created == ["SELENA GOMEZ"]
    assert result["created"] == 1 and result["reused"] == 0
    assert [a["name"] for a in result["confirmed"]] == ["Selena Gomez"]
    assert lib.get_followed_artists("qobuz")[0]["last_reviewed_at"] is not None


def test_confirmar_reaproveita_playlist_existente_sem_diferenciar_caixa(tmp_path):
    lib = _lib_com(tmp_path, "Ana")
    client = FakePlaylistClient(existing=["Ana"])
    artist = lib.get_followed_artists("qobuz")[0]

    result = asyncio.run(confirm_artists([artist], lib, client))

    assert client.created == []
    assert result["reused"] == 1 and result["created"] == 0
    assert lib.get_followed_artists("qobuz")[0]["last_reviewed_at"] is not None


def test_falha_ao_criar_playlist_nao_marca_o_artista(tmp_path):
    lib = _lib_com(tmp_path, "Ana", "Bia")
    client = FakePlaylistClient(fail={"ANA"})
    artists = lib.get_followed_artists("qobuz")

    result = asyncio.run(confirm_artists(artists, lib, client))

    assert [a["name"] for a in result["confirmed"]] == ["Bia"]
    assert [i["artist"]["name"] for i in result["failed"]] == ["Ana"]
    by_name = {a["name"]: a for a in lib.get_followed_artists("qobuz")}
    assert by_name["Ana"]["last_reviewed_at"] is None
    assert by_name["Bia"]["last_reviewed_at"] is not None


def test_checklist_confirma_marcados_e_sai(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana", "Bia", "Caio")
    client = FakePlaylistClient()
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", lambda *a, **k: None)
    monkeypatch.setattr("qobuz_dl.artist_review.ui.step_inline", lambda *a, **k: None)
    rodadas = []

    async def runner(state, *, url_opener):
        rodadas.append([r.artist_id for r in state.rows if r.verified])
        if len(rodadas) == 1:
            state.toggle(0)  # Ana
            state.toggle(2)  # Caio
            return "confirm", state.selected_ids()
        assert state.message.startswith("Conferidos: 2")
        return "quit", []

    args = SimpleNamespace(modo="alfabetica", sem_sincronizar=True)
    rc = asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession(),
            checklist=runner,
        )
    )

    assert rc == 0
    assert client.created == ["ANA", "CAIO"]
    # na 2ª rodada, Ana e Caio já aparecem conferidos
    assert rodadas == [[], ["1", "3"]]
    by_name = {a["name"]: a for a in lib.get_followed_artists("qobuz")}
    assert by_name["Bia"]["last_reviewed_at"] is None


def test_checklist_com_falha_mantem_o_artista_marcado(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana", "Bia")
    client = FakePlaylistClient(fail={"BIA"})
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", lambda *a, **k: None)
    monkeypatch.setattr("qobuz_dl.artist_review.ui.step_inline", lambda *a, **k: None)
    vistos = []

    async def runner(state, *, url_opener):
        if not vistos:
            vistos.append("1ª")
            state.toggle(0)
            state.toggle(1)
            return "confirm", state.selected_ids()
        vistos.append(state.selected_ids())
        assert "Falhou: Bia" in state.message
        return "quit", []

    args = SimpleNamespace(modo="alfabetica", sem_sincronizar=True)
    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession(),
            checklist=runner,
        )
    )

    assert vistos[1] == ["2"]  # só a Bia continua marcada, para tentar de novo


def test_tela_indisponivel_cai_no_prompt_de_texto(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana")
    client = FakePlaylistClient()
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", lambda *a, **k: None)
    monkeypatch.setattr("qobuz_dl.artist_review.ui.warn", lambda *a, **k: None)
    monkeypatch.setattr("qobuz_dl.artist_review.ui.step_inline", lambda *a, **k: None)

    async def quebrada(state, *, url_opener):
        raise RuntimeError("terminal sem suporte")

    args = SimpleNamespace(modo="alfabetica", sem_sincronizar=True)
    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession("m 1", "q"),
            checklist=quebrada,
        )
    )

    assert client.created == ["ANA"]


def test_json_exporta_artistas_ordenados_sem_consultar_discografias(
    tmp_path, monkeypatch
):
    class FavoritesOnlyClient(FakeClient):
        async def get_artist_meta(self, artist_id):
            raise AssertionError("o modo JSON não deve consultar discografias")

    client = FavoritesOnlyClient(
        [
            {"id": 2, "name": "Bia"},
            {"id": 1, "name": "Ana"},
            {"id": 2, "name": "Bia duplicada"},
        ],
        {},
    )
    lib = LibraryDB(tmp_path / "library.db")
    output = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit_always", output.append)
    args = SimpleNamespace(json=True, sem_sincronizar=False)

    result = asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession(),
        )
    )

    assert result == 0
    assert len(output) == 1
    payload = json.loads(output[0])
    assert payload == {
        "artists": [
            {
                "id": "1",
                "name": "Ana",
                "url": "https://open.qobuz.com/artist/1",
            },
            {
                "id": "2",
                "name": "Bia",
                "url": "https://open.qobuz.com/artist/2",
            },
        ]
    }


def test_parser_expoe_json_para_atalhos():
    args = qobuz_dl_args().parse_args(["--quiet", "artists", "--json"])
    assert args.command == "artists"
    assert args.quiet is True
    assert args.json is True


def test_cria_playlist_vazia_com_nome_em_maiusculas(tmp_path, monkeypatch):
    class PlaylistClient:
        def __init__(self):
            self.create_args = None

        async def get_user_playlists(self, limit=500):
            # O cliente real tem esse método; sem ele o código avisa via
            # logger.warning, e se outro teste deixou o handler do ui no
            # logger raiz, o aviso cai em ui.emit_always antes do JSON.
            return {"playlists": {"items": []}}

        async def api_call(self, endpoint, *, id):
            assert endpoint == "artist/get"
            assert id == "123"
            return {"id": 123, "name": "Björk"}

        async def create_qobuz_playlist(self, **kwargs):
            self.create_args = kwargs
            return "987"

    client = PlaylistClient()
    output = []
    opened_urls = []

    def capture_open(url):
        assert client.create_args is not None
        opened_urls.append(url)
        return True

    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit_always", output.append)
    result = asyncio.run(
        cmd_artist_review(
            SimpleNamespace(
                criar_playlist_vazia=True,
                id_artista="123",
                abrir_perfil=True,
            ),
            SimpleNamespace(client=client),
            lib=LibraryDB(tmp_path / "library.db"),
            url_opener=capture_open,
        )
    )

    assert result == 0
    assert client.create_args == {
        "name": "BJÖRK",
        "description": "",
        "is_public": False,
    }
    assert opened_urls == ["https://open.qobuz.com/artist/123"]
    assert json.loads(output[0]) == {
        "ok": True,
        "artist": {
            "id": "123",
            "name": "Björk",
            "url": "https://open.qobuz.com/artist/123",
        },
        "playlist": {
            "id": "987",
            "name": "BJÖRK",
            "url": "https://play.qobuz.com/playlist/987",
            "is_empty": True,
            "is_public": False,
            "reused": False,
        },
        "reviewed": False,
        "profile_opened": True,
    }


def test_criar_playlist_rejeita_id_nao_numerico(tmp_path, monkeypatch):
    class NoNetworkClient:
        async def api_call(self, *_args, **_kwargs):
            raise AssertionError("ID inválido não deve gerar chamada de rede")

    output = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit_always", output.append)
    result = asyncio.run(
        cmd_artist_review(
            SimpleNamespace(criar_playlist_vazia=True, id_artista="12x"),
            SimpleNamespace(client=NoNetworkClient()),
            lib=LibraryDB(tmp_path / "library.db"),
        )
    )

    assert result == 1
    assert json.loads(output[0]) == {
        "ok": False,
        "error": "o ID do artista precisa conter somente números",
    }


def test_parser_expone_criacao_de_playlist_para_atalhos():
    args = qobuz_dl_args().parse_args(
        [
            "--quiet",
            "artists",
            "--criar-playlist-vazia",
            "--id-artista",
            "123",
            "--abrir-perfil",
            "--json",
        ]
    )
    assert args.command == "artists"
    assert args.criar_playlist_vazia is True
    assert args.id_artista == "123"
    assert args.abrir_perfil is True
    assert args.json is True


# -- id da playlist, desfazer, marcações salvas e Atalhos unificado -------------


def _com_lancamento(tmp_path, *names):
    lib = _lib_com(tmp_path, *names)
    lib.record_artist_releases("qobuz", "1", [])
    lib.record_artist_releases(
        "qobuz",
        "1",
        [
            {
                "source_release_id": "9",
                "title": "Disco Novo",
                "release_date": "2099-01-01",
                "release_type": "album",
            }
        ],
    )
    return lib


def test_banco_guarda_e_devolve_o_id_da_playlist(tmp_path):
    lib = _lib_com(tmp_path, "Ana")
    assert lib.get_followed_artists("qobuz")[0]["review_playlist_id"] is None
    assert lib.set_artist_review_playlist("qobuz", "1", 777) is True
    assert lib.get_followed_artists("qobuz")[0]["review_playlist_id"] == "777"
    assert lib.set_artist_review_playlist("qobuz", "1", None) is True
    assert lib.get_followed_artists("qobuz")[0]["review_playlist_id"] is None
    assert lib.set_artist_review_playlist("qobuz", "404", "1") is False


def test_desfazer_restaura_os_lancamentos_pendentes(tmp_path):
    lib = _com_lancamento(tmp_path, "Ana")
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 1

    assert lib.mark_artist_reviewed("qobuz", "1") is True
    artista = lib.get_followed_artists("qobuz")[0]
    assert artista["pending_count"] == 0 and artista["last_reviewed_at"]

    assert lib.unmark_artist_reviewed("qobuz", "1") is True
    artista = lib.get_followed_artists("qobuz")[0]
    assert artista["pending_count"] == 1  # "novos" voltam
    assert artista["last_reviewed_at"] is None
    pendentes = lib.get_artist_releases("qobuz", "1", pending_only=True)
    assert [r["title"] for r in pendentes] == ["Disco Novo"]
    # desfazer de novo não faz nada: já não está conferido
    assert lib.unmark_artist_reviewed("qobuz", "1") is False


def test_desfazer_uma_segunda_conferencia_volta_a_data_anterior(tmp_path):
    lib = _com_lancamento(tmp_path, "Ana")
    lib.mark_artist_reviewed("qobuz", "1")
    primeira = lib.get_followed_artists("qobuz")[0]["last_reviewed_at"]
    lib.record_artist_releases(
        "qobuz",
        "1",
        [
            {
                "source_release_id": "10",
                "title": "Outro",
                "release_date": "2099-02-01",
                "release_type": "single",
            }
        ],
    )
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 1
    lib.mark_artist_reviewed("qobuz", "1")

    assert lib.unmark_artist_reviewed("qobuz", "1") is True
    artista = lib.get_followed_artists("qobuz")[0]
    assert artista["pending_count"] == 1
    assert artista["last_reviewed_at"] == primeira  # COM NOVOS, não "pendente" zerado


def test_desfazer_sem_estado_guardado_so_limpa_a_data(tmp_path):
    lib = _lib_com(tmp_path, "Ana")
    with sqlite3.connect(lib.path) as conn:
        conn.execute(
            "UPDATE followed_artists SET last_reviewed_at='2026-10-01T00:00:00+00:00'"
        )
    assert lib.unmark_artist_reviewed("qobuz", "1") is True
    assert lib.get_followed_artists("qobuz")[0]["last_reviewed_at"] is None
    assert lib.unmark_artist_reviewed("qobuz", "404") is False


def test_confirmar_guarda_o_id_da_playlist_no_banco(tmp_path):
    lib = _lib_com(tmp_path, "Ana")
    client = FakePlaylistClient()
    asyncio.run(confirm_artists(lib.get_followed_artists("qobuz"), lib, client))
    assert lib.get_followed_artists("qobuz")[0]["review_playlist_id"] == "101"


def test_confirmar_usa_o_id_guardado_se_a_playlist_ainda_existe(tmp_path):
    lib = _lib_com(tmp_path, "Ana")
    lib.set_artist_review_playlist("qobuz", "1", "900")
    client = FakePlaylistClient(existing=["Nome Renomeado"])  # id 900, outro nome
    result = asyncio.run(
        confirm_artists(lib.get_followed_artists("qobuz"), lib, client)
    )
    assert client.created == [] and result["reused"] == 1
    assert lib.get_followed_artists("qobuz")[0]["review_playlist_id"] == "900"


def test_confirmar_cria_outra_se_a_playlist_guardada_foi_apagada(tmp_path):
    lib = _lib_com(tmp_path, "Ana")
    lib.set_artist_review_playlist("qobuz", "1", "555")  # não está na conta
    client = FakePlaylistClient(existing=["Outra Coisa"])
    result = asyncio.run(
        confirm_artists(lib.get_followed_artists("qobuz"), lib, client)
    )
    assert client.created == ["ANA"] and result["created"] == 1
    assert lib.get_followed_artists("qobuz")[0]["review_playlist_id"] == "101"


def test_confirmar_confia_no_id_guardado_se_a_listagem_falhar(tmp_path):
    class SemListagem(FakePlaylistClient):
        async def get_user_playlists(self, limit=100):
            raise RuntimeError("falha de rede")

    lib = _lib_com(tmp_path, "Ana")
    lib.set_artist_review_playlist("qobuz", "1", "555")
    client = SemListagem()
    result = asyncio.run(
        confirm_artists(lib.get_followed_artists("qobuz"), lib, client)
    )
    assert client.created == [] and result["reused"] == 1  # sem duplicar


def test_undo_artists_reabre_e_informa_falhas(tmp_path):
    lib = _lib_com(tmp_path, "Ana", "Bia")
    artistas = lib.get_followed_artists("qobuz")
    asyncio.run(confirm_artists(artistas[:1], lib, FakePlaylistClient()))
    result = asyncio.run(undo_artists(artistas, lib))
    assert [a["name"] for a in result["undone"]] == ["Ana"]
    assert [i["artist"]["name"] for i in result["failed"]] == ["Bia"]
    assert lib.get_followed_artists("qobuz")[0]["last_reviewed_at"] is None


def test_armazenamento_das_marcacoes(tmp_path):
    caminho = tmp_path / "marcas.json"
    loja = _SelectionStore(str(caminho))
    assert loja.load() == []
    loja.save(["1", "3"])
    assert loja.load() == ["1", "3"]
    caminho.write_text("{quebrado", encoding="utf-8")
    assert loja.load() == []
    loja.save(["2"])
    loja.save([])
    assert not caminho.exists()
    assert _SelectionStore(None).load() == []
    _SelectionStore(None).save(["1"])  # sem caminho: não faz nada


def test_marcacoes_sobrevivem_se_o_app_for_fechado(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana", "Bia")
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", lambda *a, **k: None)
    args = SimpleNamespace(modo="alfabetica", sem_sincronizar=True)

    async def fechado(state, *, url_opener):
        state.toggle(0)
        raise KeyboardInterrupt  # como se o iOS tivesse encerrado o a-Shell

    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=FakePlaylistClient()),
            lib=lib,
            session=FakeSession(),
            checklist=fechado,
        )
    )
    assert (tmp_path / "artist_review_selection.json").exists()

    vistos = []

    async def reaberto(state, *, url_opener):
        vistos.append((state.selected_ids(), state.message))
        return "quit", []  # saída de propósito descarta o que não foi confirmado

    asyncio.run(
        cmd_artist_review(
            args,
            SimpleNamespace(client=FakePlaylistClient()),
            lib=lib,
            session=FakeSession(),
            checklist=reaberto,
        )
    )
    assert vistos[0][0] == ["1"] and "restaurada" in vistos[0][1]
    assert not (tmp_path / "artist_review_selection.json").exists()


def test_checklist_confirma_e_desfaz_na_mesma_rodada(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana", "Bia")
    client = FakePlaylistClient()
    asyncio.run(confirm_artists(lib.get_followed_artists("qobuz")[:1], lib, client))
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", lambda *a, **k: None)
    monkeypatch.setattr("qobuz_dl.artist_review.ui.ok", lambda *a, **k: None)
    monkeypatch.setattr("qobuz_dl.artist_review.ui.step_inline", lambda *a, **k: None)
    mensagens = []

    async def runner(state, *, url_opener):
        if not mensagens:
            mensagens.append("1ª")
            state.toggle(0)  # Ana conferida -> desfazer
            state.toggle(1)  # Bia pendente -> confirmar
            return "confirm", state.selected_ids()
        mensagens.append(state.message)
        return "quit", []

    asyncio.run(
        cmd_artist_review(
            SimpleNamespace(modo="alfabetica", sem_sincronizar=True),
            SimpleNamespace(client=client),
            lib=lib,
            session=FakeSession(),
            checklist=runner,
        )
    )
    por_nome = {a["name"]: a for a in lib.get_followed_artists("qobuz")}
    assert por_nome["Ana"]["last_reviewed_at"] is None
    assert por_nome["Bia"]["last_reviewed_at"] is not None
    assert client.created == ["ANA", "BIA"]  # a playlist da Ana não foi apagada
    assert "Conferidos: 1" in mensagens[1] and "Desfeitos: 1" in mensagens[1]


def test_checklist_entrega_lancamentos_pendentes_para_a_tela_de_detalhe(
    tmp_path, monkeypatch
):
    lib = _com_lancamento(tmp_path, "Ana", "Bia")
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit", lambda *a, **k: None)
    vistos = {}

    async def runner(state, *, url_opener):
        vistos.update(state.releases)
        return "quit", []

    asyncio.run(
        cmd_artist_review(
            SimpleNamespace(modo="alfabetica", sem_sincronizar=True),
            SimpleNamespace(client=FakePlaylistClient()),
            lib=lib,
            session=FakeSession(),
            checklist=runner,
        )
    )
    assert list(vistos) == ["1"]
    assert vistos["1"][0]["title"] == "Disco Novo"


def test_atalhos_reaproveita_playlist_sem_marcar_como_conferido(tmp_path, monkeypatch):
    class AtalhosClient(FakePlaylistClient):
        async def api_call(self, endpoint, *, id):
            return {"id": int(id), "name": "Ana"}

    lib = _lib_com(tmp_path, "Ana")
    client = AtalhosClient()
    saidas = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit_always", saidas.append)
    args = SimpleNamespace(
        criar_playlist_vazia=True, id_artista="1", abrir_perfil=False
    )

    for _ in range(2):  # rodar duas vezes não duplica a playlist
        asyncio.run(
            cmd_artist_review(
                args, SimpleNamespace(client=client), lib=lib, url_opener=lambda u: True
            )
        )

    primeira, segunda = (json.loads(x) for x in saidas)
    assert client.created == ["ANA"]
    assert primeira["playlist"]["reused"] is False and primeira["reviewed"] is False
    assert primeira["playlist"]["is_empty"] is True
    assert segunda["playlist"]["reused"] is True and segunda["reviewed"] is False
    assert segunda["playlist"]["is_empty"] is None  # não dá para afirmar
    assert segunda["playlist"]["id"] == primeira["playlist"]["id"]
    artista = lib.get_followed_artists("qobuz")[0]
    assert artista["review_playlist_id"] == primeira["playlist"]["id"]
    assert artista["last_reviewed_at"] is None


def test_parseia_checklist_apple_notas_e_rejeita_estados_conflitantes():
    from qobuz_dl.artist_review import parse_notes_checklist

    parsed = parse_notes_checklist(
        "- [x] Lady Gaga - [QOBUZ_ID:61585] - https://open.qobuz.com/artist/61585\n"
        "- [ ] 2ZDinizz - [QOBUZ_ID:12698784] - https://open.qobuz.com/artist/12698784"
    )
    assert [(row["source_artist_id"], row["verified"]) for row in parsed] == [
        ("61585", True),
        ("12698784", False),
    ]

    import pytest

    with pytest.raises(ValueError, match="aparece marcado e desmarcado"):
        parse_notes_checklist(
            "- [x] Lady Gaga - [QOBUZ_ID:61585]\n"
            "- [ ] Lady Gaga - [QOBUZ_ID:61585]"
        )


def test_sync_checklist_espelha_estado_sem_criar_playlist(tmp_path):  # --espelhar
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz",
        [
            {"source_artist_id": "61585", "name": "Lady Gaga"},
            {"source_artist_id": "12698784", "name": "2ZDinizz"},
        ],
    )
    # Artista que estava conferido, mas aparece desmarcado na Nota.
    assert lib.mark_artist_reviewed("qobuz", "61585") is True
    # Artista que estava pendente, mas aparece marcado na Nota.
    checklist = (
        "- [ ] Lady Gaga - [QOBUZ_ID:61585] - https://open.qobuz.com/artist/61585\n"
        "- [x] 2ZDinizz - [QOBUZ_ID:12698784] - https://open.qobuz.com/artist/12698784"
    )
    result = asyncio.run(sync_notes_checklist(checklist, lib, mirror=True))

    assert [row["source_artist_id"] for row in result["marked"]] == ["12698784"]
    assert [row["source_artist_id"] for row in result["unmarked"]] == ["61585"]
    assert [row["source_artist_id"] for row in result["verified"]] == ["12698784"]
    assert [row["source_artist_id"] for row in result["pending"]] == ["61585"]
    assert result["playlists_created"] == 0
    states = {row["source_artist_id"]: bool(row["last_reviewed_at"])
              for row in lib.get_followed_artists("qobuz")}
    assert states == {"61585": False, "12698784": True}


def test_sync_checklist_nao_altera_artistas_ausentes_da_lista(tmp_path):
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = LibraryDB(tmp_path / "library.db")
    lib.sync_followed_artists(
        "qobuz", [{"source_artist_id": "61585", "name": "Lady Gaga"}]
    )
    result = asyncio.run(
        sync_notes_checklist("- [x] Lady Gaga - [QOBUZ_ID:61585]", lib)
    )
    assert result["marked"][0]["source_artist_id"] == "61585"
    assert result["unknown"] == []


# -- sincronização da Nota: modo padrão, regra única de "conferido", Atalhos ----


def _nota(*linhas):
    return "\n".join(linhas)


def test_sync_padrao_nao_desfaz_o_que_foi_confirmado_no_a_shell(tmp_path):
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = _lib_com(tmp_path, "Ana", "Bia")
    asyncio.run(confirm_artists(lib.get_followed_artists("qobuz")[:1], lib, FakePlaylistClient()))
    nota = _nota("- [ ] Ana - [QOBUZ_ID:1]", "- [x] Bia - [QOBUZ_ID:2]")

    result = asyncio.run(sync_notes_checklist(nota, lib))

    por_nome = {a["name"]: bool(a["last_reviewed_at"]) for a in lib.get_followed_artists("qobuz")}
    assert por_nome == {"Ana": True, "Bia": True}  # Ana continua conferida
    assert result["unmarked"] == [] and result["mirror"] is False
    assert [i["name"] for i in result["kept_verified"]] == ["Ana"]
    assert [i["name"] for i in result["marked"]] == ["Bia"]
    assert [i["name"] for i in result["verified"]] == ["Ana", "Bia"]


def test_sync_espelhar_reabre_quem_esta_desmarcado_na_nota(tmp_path):
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = _lib_com(tmp_path, "Ana")
    asyncio.run(confirm_artists(lib.get_followed_artists("qobuz"), lib, FakePlaylistClient()))
    result = asyncio.run(
        sync_notes_checklist("- [ ] Ana - [QOBUZ_ID:1]", lib, mirror=True)
    )
    assert [i["name"] for i in result["unmarked"]] == ["Ana"]
    assert result["kept_verified"] == [] and result["mirror"] is True
    assert lib.get_followed_artists("qobuz")[0]["last_reviewed_at"] is None


def test_sync_marcado_na_nota_com_lancamento_novo_pede_revisao(tmp_path):
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = _com_lancamento(tmp_path, "Ana")
    lib.mark_artist_reviewed("qobuz", "1")
    lib.record_artist_releases(
        "qobuz",
        "1",
        [
            {
                "source_release_id": "10",
                "title": "Outro",
                "release_date": "2099-02-01",
                "release_type": "single",
            }
        ],
    )
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 1

    result = asyncio.run(sync_notes_checklist("- [x] Ana - [QOBUZ_ID:1]", lib))

    assert [i["name"] for i in result["needs_review"]] == ["Ana"]
    assert result["needs_review"][0]["pending_count"] == 1
    assert result["verified"] == []  # mesma regra da tela: COM NOVOS não é conferido
    assert [i["name"] for i in result["pending"]] == ["Ana"]
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 1  # nada apagado


def test_sync_desmarcado_com_lancamento_novo_nao_reabre_nem_no_espelho(tmp_path):
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = _com_lancamento(tmp_path, "Ana")
    lib.mark_artist_reviewed("qobuz", "1")
    lib.record_artist_releases(
        "qobuz",
        "1",
        [
            {
                "source_release_id": "10",
                "title": "Outro",
                "release_date": "2099-02-01",
                "release_type": "single",
            }
        ],
    )
    result = asyncio.run(
        sync_notes_checklist("- [ ] Ana - [QOBUZ_ID:1]", lib, mirror=True)
    )
    assert result["unmarked"] == [] and [i["name"] for i in result["pending"]] == ["Ana"]
    assert lib.get_followed_artists("qobuz")[0]["pending_count"] == 1


def test_sync_lista_os_seguidos_que_faltam_na_nota_prontos_para_colar(tmp_path):
    from qobuz_dl.artist_review import sync_notes_checklist

    lib = _lib_com(tmp_path, "Ana", "Caio", "Bia")
    bia = [a for a in lib.get_followed_artists("qobuz") if a["name"] == "Bia"]
    asyncio.run(confirm_artists(bia, lib, FakePlaylistClient()))
    result = asyncio.run(sync_notes_checklist("- [ ] Ana - [QOBUZ_ID:1]", lib))
    assert [m["line"] for m in result["missing"]] == [
        "- [x] Bia - [QOBUZ_ID:3] - https://open.qobuz.com/artist/3",
        "- [ ] Caio - [QOBUZ_ID:2] - https://open.qobuz.com/artist/2",
    ]
    # as linhas voltam a ser lidas pelo parser da própria Nota
    from qobuz_dl.artist_review import parse_notes_checklist

    lidas = parse_notes_checklist("\n".join(m["line"] for m in result["missing"]))
    assert [(i["source_artist_id"], i["verified"]) for i in lidas] == [("3", True), ("2", False)]


def _executar_sync(tmp_path, monkeypatch, nota, lib, qobuz, **opcoes):
    arquivo = tmp_path / "nota.txt"
    arquivo.write_text(nota, encoding="utf-8")
    saidas = []
    monkeypatch.setattr("qobuz_dl.artist_review.ui.emit_always", saidas.append)
    args = SimpleNamespace(sincronizar_checklist=str(arquivo), **opcoes)
    rc = asyncio.run(cmd_artist_review(args, qobuz, lib=lib))
    return rc, json.loads(saidas[-1])


def test_comando_sincroniza_arquivo_informado_sem_precisar_de_api(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana", "Bia")
    qobuz = SimpleNamespace()  # sem client: provaria qualquer chamada à API
    rc, saida = _executar_sync(
        tmp_path, monkeypatch, "- [x] Ana - [QOBUZ_ID:1]\n- [ ] Bia - [QOBUZ_ID:2]", lib, qobuz
    )
    assert rc == 0 and saida["ok"] is True
    assert [i["name"] for i in saida["marked"]] == ["Ana"]
    assert saida["mirror"] is False and saida["missing"] == []


def test_comando_atualiza_seguidos_antes_de_sincronizar(tmp_path, monkeypatch):
    class Cliente:
        async def get_all_favorites(self, kind, page_size=500, max_pages=400):
            return [{"id": 1, "name": "Ana"}, {"id": 7, "name": "Novo Seguido"}], 2

    lib = _lib_com(tmp_path, "Ana")
    rc, saida = _executar_sync(
        tmp_path,
        monkeypatch,
        "- [x] Novo Seguido - [QOBUZ_ID:7]",
        lib,
        SimpleNamespace(client=Cliente()),
        atualizar_seguidos=True,
        espelhar=False,
    )
    assert rc == 0 and [i["name"] for i in saida["marked"]] == ["Novo Seguido"]
    assert saida["unknown"] == []
    assert [m["name"] for m in saida["missing"]] == ["Ana"]


def test_comando_passa_o_modo_espelhar(tmp_path, monkeypatch):
    lib = _lib_com(tmp_path, "Ana")
    asyncio.run(confirm_artists(lib.get_followed_artists("qobuz"), lib, FakePlaylistClient()))
    rc, saida = _executar_sync(
        tmp_path, monkeypatch, "- [ ] Ana - [QOBUZ_ID:1]", lib, SimpleNamespace(), espelhar=True
    )
    assert saida["mirror"] is True and [i["name"] for i in saida["unmarked"]] == ["Ana"]


def test_parser_sincronizar_checklist_aceita_arquivo_e_opcoes():
    from qobuz_dl import cli

    parser = cli.qobuz_dl_args()
    simples = parser.parse_args(["artists", "--sincronizar-checklist"])
    assert simples.sincronizar_checklist is True
    assert simples.espelhar is False and simples.atualizar_seguidos is False
    completo = parser.parse_args(
        ["artists", "--sincronizar-checklist", "nota.txt", "--espelhar", "--atualizar-seguidos"]
    )
    assert completo.sincronizar_checklist == "nota.txt"
    assert completo.espelhar is True and completo.atualizar_seguidos is True
    igual = parser.parse_args(["artists", "--sincronizar-checklist=nota.txt"])
    assert igual.sincronizar_checklist == "nota.txt"


def test_parser_de_artists_nao_aceita_abreviacao_do_modo_json():
    from qobuz_dl import cli

    parser = cli.qobuz_dl_args()
    try:
        parser.parse_args(["artists", "--sincronizar-c"])
    except SystemExit as exc:
        assert exc.code != 0
    else:
        raise AssertionError("a abreviação deveria ser rejeitada")


def test_sincronizar_checklist_local_nao_precisa_de_login():
    from qobuz_dl import cli

    parser = cli.qobuz_dl_args()
    local = parser.parse_args(["artists", "--sincronizar-checklist"])
    assert cli._is_local_checklist_sync(local) is True
    com_api = parser.parse_args(["artists", "--sincronizar-checklist", "--atualizar-seguidos"])
    assert cli._is_local_checklist_sync(com_api) is False
    assert cli._is_local_checklist_sync(parser.parse_args(["artists"])) is False
    assert cli._is_local_checklist_sync(parser.parse_args(["artists", "--json"])) is False
