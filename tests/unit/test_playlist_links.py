"""Playlist x álbum sem duplicatas (qobuz_dl/playlist_links.py)."""

import os
import sqlite3

from qobuz_dl import playlist_links as pl

SCHEMA = """CREATE TABLE downloads (
  "id" text NOT NULL, "media_type" text NOT NULL DEFAULT 'album',
  "quality" integer NOT NULL DEFAULT 27, "file_format" text NOT NULL DEFAULT 'FLAC',
  "quality_met" integer NOT NULL DEFAULT 0, "bit_depth" text, "sampling_rate" text,
  "saved_path" text NOT NULL DEFAULT '', "status" text NOT NULL DEFAULT 'downloaded',
  "url" text NOT NULL DEFAULT '', "release_date" text NOT NULL DEFAULT '',
  "artist" text NOT NULL DEFAULT '', "album" text NOT NULL DEFAULT '',
  PRIMARY KEY ("id", "quality"));"""


def _reader(path):
    """Arquivos falsos: o nome é '<id>.flac' (ou '<id>_hi.flac')."""
    return os.path.basename(path).split(".")[0].split("_")[0]


def _ranker(path):
    return (1, 24, 96000) if "_hi" in path else (1, 16, 44100)


def _touch(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return str(path)


def _db(tmp_path, rows=()):
    db = str(tmp_path / "db.sqlite")
    c = sqlite3.connect(db)
    c.executescript(SCHEMA)
    for tid, path in rows:
        c.execute(
            "INSERT INTO downloads (id, media_type, saved_path) VALUES (?, 'track', ?)",
            (tid, path),
        )
    c.commit()
    c.close()
    return db


def _saved(db, tid):
    c = sqlite3.connect(db)
    try:
        r = c.execute(
            "SELECT saved_path FROM downloads WHERE id=? AND media_type='track'", (tid,)
        ).fetchone()
        return r[0] if r else None
    finally:
        c.close()


def _absorb(root, db, dest, **kw):
    return pl.absorb_playlist_copies_sync(
        str(root), db, dest, reader=_reader, ranker=_ranker, **kw
    )


class TestCaminhos:
    def test_playlist_root(self, tmp_path):
        assert pl.playlist_root(tmp_path) == os.path.join(str(tmp_path), "Playlist")

    def test_playlist_dir_for(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        f = os.path.join(root, "Minha", "Album", "x.flac")
        assert pl.playlist_dir_for(f, root) == os.path.join(root, "Minha")
        assert pl.playlist_dir_for(str(tmp_path / "Album" / "x.flac"), root) is None
        assert pl.playlist_dir_for(root, root) is None


class TestLinks:
    def test_roundtrip_e_validos(self, tmp_path):
        pl_dir = str(tmp_path / "Playlist" / "P")
        alvo = _touch(tmp_path / "Album" / "A" / "1.flac")
        pl.add_link(pl_dir, 1, alvo)
        assert pl.load_links(pl_dir) == {
            "1": os.path.join("..", "..", "Album", "A", "1.flac")
        }
        assert pl.valid_links(pl_dir) == {"1": os.path.normpath(alvo)}
        os.remove(alvo)
        assert pl.valid_links(pl_dir) == {}

    def test_remove_link_apaga_arquivo_vazio(self, tmp_path):
        pl_dir = str(tmp_path / "P")
        alvo = _touch(tmp_path / "A" / "1.flac")
        pl.add_link(pl_dir, "1", alvo)
        assert pl.remove_link(pl_dir, "1") is True
        assert not os.path.exists(os.path.join(pl_dir, pl.LINKS_FILENAME))
        assert pl.remove_link(pl_dir, "1") is False

    def test_json_corrompido_vira_vazio(self, tmp_path):
        d = tmp_path / "P"
        d.mkdir()
        (d / pl.LINKS_FILENAME).write_text("{ nao e json", encoding="utf-8")
        assert pl.load_links(str(d)) == {}

    def test_link_existing_track_ignora_arquivo_dentro_da_playlist(self, tmp_path):
        pl_dir = str(tmp_path / "P")
        dentro = _touch(tmp_path / "P" / "1.flac")
        assert pl.link_existing_track(pl_dir, "1", dentro) is False
        fora = _touch(tmp_path / "A" / "1.flac")
        assert pl.link_existing_track(pl_dir, "1", fora) is True
        assert (
            pl.link_existing_track(pl_dir, "2", str(tmp_path / "nao_existe")) is False
        )


class TestAbsorb:
    def _cenario(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "Minha", "1.flac"))
        _touch(os.path.join(root, "Minha", "2.flac"))
        album = _touch(tmp_path / "Album" / "A" / "1.flac")
        m3u = os.path.join(root, "Minha", "Minha.m3u8")
        with open(m3u, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n#EXTINF:1, a - b\n1.flac\n#EXTINF:1, c - d\n2.flac")
        db = _db(tmp_path, [("1", copia), ("2", os.path.join(root, "Minha", "2.flac"))])
        return root, copia, album, m3u, db

    def test_remove_copia_aponta_link_atualiza_m3u_e_banco(self, tmp_path):
        root, copia, album, m3u, db = self._cenario(tmp_path)
        st = _absorb(tmp_path, db, {"1": album})
        assert [t for t, _o, _n in st["absorbed"]] == ["1"]
        assert not os.path.exists(copia)
        assert os.path.exists(album)
        assert os.path.exists(
            os.path.join(root, "Minha", "2.flac")
        )  # outra faixa intacta
        pl_dir = os.path.join(root, "Minha")
        assert pl.valid_links(pl_dir) == {"1": os.path.normpath(album)}
        linhas = open(m3u, encoding="utf-8").read().split("\n")
        assert linhas[2] == os.path.relpath(album, pl_dir)  # mesma posição/ordem
        assert linhas[4] == "2.flac"
        assert _saved(db, "1") == album

    def test_nao_apaga_se_album_tem_qualidade_menor(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "P", "1_hi.flac"))
        album = _touch(tmp_path / "Album" / "A" / "1.flac")  # rank menor
        db = _db(tmp_path, [("1", copia)])
        st = _absorb(tmp_path, db, {"1": album})
        assert st["absorbed"] == [] and st["kept"] == [os.path.abspath(copia)]
        assert os.path.exists(copia)

    def test_nao_apaga_se_nao_consegue_ler_qualidade(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "P", "1.flac"))
        album = _touch(tmp_path / "Album" / "A" / "1.flac")
        db = _db(tmp_path, [("1", copia)])
        st = pl.absorb_playlist_copies_sync(
            str(tmp_path), db, {"1": album}, reader=_reader, ranker=lambda p: None
        )
        assert st["absorbed"] == [] and os.path.exists(copia)

    def test_sem_banco_varre_a_pasta_playlist(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "P", "1.flac"))
        album = _touch(tmp_path / "Album" / "A" / "1.flac")
        st = _absorb(tmp_path, None, {"1": album})
        assert len(st["absorbed"]) == 1 and not os.path.exists(copia)

    def test_mesma_faixa_em_duas_playlists_retargeta_o_link(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "A", "1.flac"))
        album = _touch(tmp_path / "Album" / "X" / "1.flac")
        # playlist B já apontava para a cópia que está na playlist A
        pl.add_link(os.path.join(root, "B"), "1", copia)
        db = _db(tmp_path, [("1", copia)])
        _absorb(tmp_path, db, {"1": album})
        assert pl.valid_links(os.path.join(root, "A")) == {"1": os.path.normpath(album)}
        assert pl.valid_links(os.path.join(root, "B")) == {"1": os.path.normpath(album)}

    def test_lrc_acompanha_para_o_album(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "P", "1.flac"))
        _touch(os.path.join(root, "P", "1.lrc"), b"[00:01]oi")
        album = _touch(tmp_path / "Album" / "A" / "1.flac")
        db = _db(tmp_path, [("1", copia)])
        _absorb(tmp_path, db, {"1": album})
        assert os.path.isfile(str(tmp_path / "Album" / "A" / "1.lrc"))
        assert not os.path.exists(os.path.join(root, "P", "1.lrc"))

    def test_subpastas_vazias_da_playlist_somem(self, tmp_path):
        root = pl.playlist_root(tmp_path)
        copia = _touch(os.path.join(root, "P", "Album", "X", "1.flac"))
        album = _touch(tmp_path / "Album" / "A" / "1.flac")
        db = _db(tmp_path, [("1", copia)])
        _absorb(tmp_path, db, {"1": album})
        assert not os.path.exists(os.path.join(root, "P", "Album"))
        assert os.path.isdir(os.path.join(root, "P"))

    def test_sem_pasta_playlist_nao_faz_nada(self, tmp_path):
        album = _touch(tmp_path / "Album" / "A" / "1.flac")
        db = _db(tmp_path)
        st = _absorb(tmp_path, db, {"1": album})
        assert st["absorbed"] == []

    def test_registra_faixas_do_album_no_banco(self, tmp_path):
        a1 = _touch(tmp_path / "Album" / "A" / "1.flac")
        a2 = _touch(tmp_path / "Album" / "A" / "2.flac")
        db = _db(tmp_path)
        reg = {"quality": 27, "artist": "Art", "album": "Alb"}
        st = _absorb(tmp_path, db, {"1": a1, "2": a2}, reg=reg)
        assert st["registered"] == 2
        assert _saved(db, "1") == os.path.abspath(a1)
        # idempotente
        assert _absorb(tmp_path, db, {"1": a1, "2": a2}, reg=reg)["registered"] == 0

    def test_registro_nao_sobrescreve_linha_existente(self, tmp_path):
        a1 = _touch(tmp_path / "Album" / "A" / "1.flac")
        db = _db(tmp_path, [("1", "/outro/lugar.flac")])
        _absorb(tmp_path, db, {"1": a1}, reg={"quality": 27})
        assert _saved(db, "1") == "/outro/lugar.flac"


class TestM3uReescrita:
    def test_rewrite_ignora_comentarios_e_outros_caminhos(self, tmp_path):
        d = tmp_path / "P"
        d.mkdir()
        (d / "P.m3u8").write_text("#EXTM3U\n1.flac\n2.flac", encoding="utf-8")
        pl._rewrite_m3u(str(d), {str(d / "1.flac"): str(tmp_path / "A" / "1.flac")})
        assert (d / "P.m3u8").read_text(encoding="utf-8").split("\n") == [
            "#EXTM3U",
            os.path.join("..", "A", "1.flac"),
            "2.flac",
        ]
