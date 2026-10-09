"""Testes DIRETOS da lógica de matching de `qobuz_dl/library_scan.py`.

`test_library_scan.py` exercita o scan inteiro (`run_scan`); aqui cada regra
de segurança do `classify()` é travada isoladamente, sem disco nem banco, para
que uma regressão aponte a regra exata que quebrou:

* ID de tag > UPC > nome exato > fuzzy (fuzzy NUNCA auto-marca);
* nunca auto-marca com mais de um candidato, com bit-depth divergente, com
  contagem de faixas divergente ou com a pasta em `[INCOMPLETE]`/`[IN PROGRESS]`;
* artista ausente nunca vira auto-match.
"""

from pathlib import Path

import pytest

from qobuz_dl import library_scan as ls
from qobuz_dl.library_db import LibraryDB

pytestmark = pytest.mark.unit


def _album(id_, artist, title, *, source="qobuz", sid=None, upc=None,
           bit_depth=24, track_count=10):
    return {
        "id": id_,
        "source": source,
        "source_album_id": sid or str(id_),
        "artist": artist,
        "title": title,
        "upc": upc,
        "bit_depth": bit_depth,
        "track_count": track_count,
    }


def _meta(artist="Daft Punk", album="Discovery", *, bit_depth=24, tracks=10,
          state="ok", sid=None, upc=None, folder="/m/x"):
    return ls.FolderMeta(
        folder=Path(folder),
        artist=artist,
        album=album,
        bit_depth=bit_depth,
        sample_rate=96.0,
        track_count=tracks,
        source="tags",
        state=state,
        service_album_id=sid,
        upc=upc,
    )


def _index(*albums):
    return ls.build_library_index(list(albums))


# ---------------------------------------------------------------------------
# Normalização e nome de pasta
# ---------------------------------------------------------------------------
class TestNormalize:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            ("Discovery (Deluxe) [FLAC 24]", "discovery"),
            ("The Beatles", "beatles"),
            ("THE   WALL", "wall"),
            ("Beyoncé", "beyonce"),
            ("[IN PROGRESS] Álbum (2020)", "album"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_chave_estavel(self, entrada, esperado):
        assert ls.normalize(entrada) == esperado

    def test_so_remove_the_inicial(self):
        # "The" no meio do nome é parte do nome.
        assert ls.normalize("Over The Hills") == "over the hills"

    def test_remove_sufixos_repetidos(self):
        assert ls.normalize("X (A) (B) [C]") == "x"


class TestParseFolderName:
    def test_artista_e_album(self):
        assert ls._parse_folder_name("Daft Punk - Discovery (2001) [FLAC 24]") == (
            "Daft Punk",
            "Discovery (2001) [FLAC 24]",
        )

    def test_sem_separador_nao_inventa_artista(self):
        assert ls._parse_folder_name("Discovery") == (None, "Discovery")

    def test_so_o_primeiro_separador_divide(self):
        assert ls._parse_folder_name("A - B - C") == ("A", "B - C")

    def test_ignora_marcador_de_estado(self):
        assert ls._parse_folder_name("[INCOMPLETE] A - B") == ("A", "B")


class TestFirst:
    @pytest.mark.parametrize(
        "valor, esperado",
        [
            (None, None),
            ([], None),
            (["  x  "], "x"),
            (("a", "b"), "a"),
            ("  y ", "y"),
            ("", None),
            ("   ", None),
            (123, "123"),
        ],
    )
    def test_formas_de_tag(self, valor, esperado):
        assert ls._first(valor) == esperado

    def test_frame_id3_usa_o_atributo_text(self):
        class Frame:
            text = ["Valor ID3"]

        assert ls._first(Frame()) == "Valor ID3"


# ---------------------------------------------------------------------------
# Leitura de pasta
# ---------------------------------------------------------------------------
class TestAudioFiles:
    def test_ignora_nao_audio_e_ordena(self, tmp_path):
        for nome in ("02.flac", "01.mp3", "capa.jpg", "nota.txt"):
            (tmp_path / nome).write_bytes(b"x")
        assert [p.name for p in ls.audio_files(tmp_path)] == ["01.mp3", "02.flac"]

    def test_so_subpastas_de_disco_sao_juntadas(self, tmp_path):
        for d, n in (("CD 02", "b.flac"), ("CD 01", "a.flac"), ("Extras", "z.flac")):
            (tmp_path / d).mkdir()
            (tmp_path / d / n).write_bytes(b"x")
        # "Extras" não é pasta de disco: fica de fora.
        assert [p.name for p in ls.audio_files(tmp_path)] == ["a.flac", "b.flac"]

    def test_audio_direto_tem_prioridade_sobre_discos(self, tmp_path):
        (tmp_path / "solto.flac").write_bytes(b"x")
        (tmp_path / "CD 01").mkdir()
        (tmp_path / "CD 01" / "a.flac").write_bytes(b"x")
        assert [p.name for p in ls.audio_files(tmp_path)] == ["solto.flac"]

    def test_pasta_sem_audio(self, tmp_path):
        assert ls.audio_files(tmp_path) == []

    def test_symlink_de_audio_e_ignorado(self, tmp_path):
        alvo = tmp_path / "real.flac"
        alvo.write_bytes(b"x")
        pasta = tmp_path / "album"
        pasta.mkdir()
        try:
            (pasta / "link.flac").symlink_to(alvo)
        except OSError:
            pytest.skip("sistema sem suporte a symlink")
        assert ls.audio_files(pasta) == []


class TestReadFolderMetadata:
    def _pasta(self, tmp_path, nome="Daft Punk - Discovery (2001) [FLAC 24]", n=2):
        pasta = tmp_path / nome
        pasta.mkdir()
        for i in range(n):
            (pasta / f"{i + 1:02}.flac").write_bytes(b"x")
        return pasta

    def test_sem_audio_devolve_none(self, tmp_path):
        assert ls.read_folder_metadata(tmp_path) is None

    def test_usa_tags_quando_artista_e_album_existem(self, tmp_path, monkeypatch):
        pasta = self._pasta(tmp_path)
        monkeypatch.setattr(
            ls,
            "_read_tags",
            lambda p: {
                "artist": "Tag Artist",
                "album": "Tag Album",
                "bit_depth": 24,
                "sample_rate": 96.0,
                "service_album_id": "42",
                "upc": "0123",
            },
        )
        meta = ls.read_folder_metadata(pasta)
        assert (meta.artist, meta.album, meta.source) == (
            "Tag Artist",
            "Tag Album",
            "tags",
        )
        assert (meta.bit_depth, meta.sample_rate, meta.track_count) == (24, 96.0, 2)
        assert (meta.service_album_id, meta.upc) == ("42", "0123")

    def test_cai_no_nome_da_pasta_sem_tags(self, tmp_path, monkeypatch):
        pasta = self._pasta(tmp_path)
        monkeypatch.setattr(ls, "_read_tags", lambda p: {})
        meta = ls.read_folder_metadata(pasta)
        assert meta.source == "folder_name"
        assert meta.artist == "Daft Punk"
        assert meta.album == "Discovery (2001) [FLAC 24]"

    def test_artista_da_tag_ganha_do_nome_da_pasta(self, tmp_path, monkeypatch):
        pasta = self._pasta(tmp_path)
        monkeypatch.setattr(ls, "_read_tags", lambda p: {"artist": "Da Tag"})
        meta = ls.read_folder_metadata(pasta)
        # Sem álbum na tag o álbum vem da pasta, mas o artista da tag é mantido.
        assert meta.artist == "Da Tag"
        assert meta.source == "folder_name"

    def test_estado_vem_do_nome_da_pasta(self, tmp_path, monkeypatch):
        pasta = self._pasta(tmp_path, "[INCOMPLETE] A - B")
        monkeypatch.setattr(ls, "_read_tags", lambda p: {})
        assert ls.read_folder_metadata(pasta).state == "incomplete"


# ---------------------------------------------------------------------------
# Índice
# ---------------------------------------------------------------------------
class TestBuildLibraryIndex:
    def test_indexa_por_todas_as_chaves(self):
        a = _album(1, "The Artist", "Álbum (Deluxe)", sid="Q1", upc="00123")
        idx = _index(a)
        assert idx.by_full_key[("artist", "album")] == [a]
        assert idx.by_album_only["album"] == [a]
        assert idx.by_artist["artist"] == [a]
        assert idx.by_source_id[("qobuz", "Q1")] is a
        # Zeros à esquerda do UPC não contam.
        assert idx.by_upc["123"] == [a]

    def test_upc_vazio_nao_entra(self):
        idx = _index(_album(1, "A", "B", upc=None), _album(2, "A", "C", upc=" "))
        assert idx.by_upc == {}

    def test_mesmo_nome_gera_lista_com_dois(self):
        idx = _index(_album(1, "A", "B"), _album(2, "A", "B (Deluxe)"))
        assert len(idx.by_full_key[("a", "b")]) == 2

    def test_catalogo_vazio(self):
        idx = _index()
        assert idx.by_full_key == {} and idx.by_source_id == {}


class TestCompatibilidade:
    @pytest.mark.parametrize(
        "local, catalogo, ok",
        [(24, 24, True), (16, 24, False), (None, 24, True), (24, None, True)],
    )
    def test_bit_depth(self, local, catalogo, ok):
        assert ls._bit_depth_matches(local, catalogo) is ok

    @pytest.mark.parametrize(
        "local, catalogo, ok",
        [(10, 10, True), (9, 10, False), (0, 10, True), (10, 0, True),
         (None, None, True)],
    )
    def test_contagem_de_faixas(self, local, catalogo, ok):
        assert ls._track_count_matches(local, catalogo) is ok

    def test_compat_lista_cada_divergencia(self):
        meta = _meta(bit_depth=16, tracks=9)
        motivos = ls._compat(meta, _album(1, "A", "B", bit_depth=24, track_count=10))
        assert len(motivos) == 2
        assert motivos[0].startswith("bit_depth_mismatch")
        assert motivos[1].startswith("track_count_mismatch")

    def test_compat_vazio_quando_tudo_bate(self):
        assert ls._compat(_meta(), _album(1, "A", "B")) == []


# ---------------------------------------------------------------------------
# classify(): uma regra por teste
# ---------------------------------------------------------------------------
class TestClassifyPorIdDeTag:
    def test_id_de_tag_e_auto_match(self):
        r = ls.classify(_meta(sid="Q9"), _index(_album(7, "X", "Y", sid="Q9")))
        assert (r.kind, r.album_id, r.reason) == ("auto_match", 7, "tag_id")

    def test_id_de_tag_vence_nome_diferente(self):
        # Mesmo com artista/álbum totalmente diferentes, o ID é identidade certa.
        r = ls.classify(
            _meta(artist="Outro", album="Nome Errado", sid="Q9"),
            _index(_album(7, "X", "Y", sid="Q9")),
        )
        assert r.kind == "auto_match"

    def test_id_de_outro_servico_nao_casa(self):
        idx = _index(_album(7, "X", "Y", sid="Q9", source="tidal"))
        r = ls.classify(_meta(artist="", album="Nada", sid="Q9"), idx)
        assert r.kind == "unmatched"

    def test_bit_depth_divergente_vai_para_revisao(self):
        r = ls.classify(
            _meta(sid="Q9", bit_depth=16),
            _index(_album(7, "X", "Y", sid="Q9", bit_depth=24)),
        )
        assert r.kind == "review" and r.album_id is None
        assert "bit_depth_mismatch" in r.candidates[0].reason
        assert r.candidates[0].score == 0.99

    def test_pasta_incompleta_nunca_e_auto(self):
        r = ls.classify(
            _meta(sid="Q9", state="incomplete"),
            _index(_album(7, "X", "Y", sid="Q9")),
        )
        assert r.kind == "review"
        assert "pasta_incomplete" in r.candidates[0].reason

    def test_pasta_em_andamento_nunca_e_auto(self):
        r = ls.classify(
            _meta(sid="Q9", state="in_progress"),
            _index(_album(7, "X", "Y", sid="Q9")),
        )
        assert r.kind == "review"
        assert "pasta_in_progress" in r.candidates[0].reason

    def test_source_alternativo(self):
        idx = _index(_album(7, "X", "Y", sid="T1", source="tidal"))
        r = ls.classify(_meta(sid="T1"), idx, source="tidal")
        assert (r.kind, r.album_id) == ("auto_match", 7)


class TestClassifyPorUpc:
    def test_upc_unico_e_auto_match(self):
        r = ls.classify(
            _meta(artist="?", album="?", upc="0077"),
            _index(_album(3, "A", "B", upc="77")),
        )
        assert (r.kind, r.album_id, r.reason) == ("auto_match", 3, "upc")

    def test_upc_com_dois_albuns_nao_auto_marca_por_upc(self):
        idx = _index(_album(3, "A", "B", upc="77"), _album(4, "A", "C", upc="77"))
        r = ls.classify(_meta(artist="A", album="B", upc="77"), idx)
        # UPC ambíguo (2 hits) não decide; o nome "A - B" casa com UM álbum.
        assert (r.kind, r.album_id, r.reason) == ("auto_match", 3, "exact")

    def test_edicoes_com_o_mesmo_nome_normalizado_ficam_para_revisao(self):
        # "(Deluxe)" é removido na normalização: "B" e "B (Deluxe)" colidem de
        # propósito, então nunca auto-marca -- o usuário escolhe a edição.
        idx = _index(_album(3, "A", "B", upc="77"), _album(4, "A", "B (Deluxe)", upc="77"))
        r = ls.classify(_meta(artist="A", album="B", upc="77"), idx)
        assert r.kind == "review"
        assert {c.album_id for c in r.candidates} == {3, 4}

    def test_upc_com_contagem_divergente_vai_para_revisao(self):
        r = ls.classify(
            _meta(upc="77", tracks=3),
            _index(_album(3, "A", "B", upc="77", track_count=10)),
        )
        assert r.kind == "review"
        assert "track_count_mismatch" in r.candidates[0].reason
        assert r.candidates[0].score == 0.97

    def test_upc_prefere_a_mesma_fonte(self):
        idx = _index(
            _album(3, "A", "B", upc="77", source="tidal"),
            _album(4, "A", "B", upc="77", source="qobuz", sid="q4"),
        )
        r = ls.classify(_meta(upc="77"), idx)
        assert (r.kind, r.album_id) == ("auto_match", 4)


class TestClassifyPorNome:
    def test_nome_exato_unico_e_auto_match(self):
        r = ls.classify(_meta(), _index(_album(1, "Daft Punk", "Discovery")))
        assert (r.kind, r.album_id, r.reason) == ("auto_match", 1, "exact")

    def test_nome_exato_ignora_sufixos_e_acentos(self):
        r = ls.classify(
            _meta(artist="Daft Punk", album="Discovery (2001) [FLAC 24]"),
            _index(_album(1, "Daft Punk", "Discovery")),
        )
        assert r.kind == "auto_match"

    def test_dois_candidatos_nunca_auto(self):
        idx = _index(
            _album(1, "Daft Punk", "Discovery"),
            _album(2, "Daft Punk", "Discovery (Deluxe)"),
        )
        r = ls.classify(_meta(), idx)
        assert r.kind == "review"
        assert {c.album_id for c in r.candidates} == {1, 2}
        assert all("multiple_candidates" in c.reason for c in r.candidates)

    def test_contagem_divergente_vai_para_revisao(self):
        r = ls.classify(
            _meta(tracks=4), _index(_album(1, "Daft Punk", "Discovery"))
        )
        assert r.kind == "review"
        assert "track_count_mismatch" in r.candidates[0].reason
        assert r.candidates[0].score == 0.9

    def test_bit_depth_divergente_vai_para_revisao(self):
        r = ls.classify(
            _meta(bit_depth=16), _index(_album(1, "Daft Punk", "Discovery"))
        )
        assert r.kind == "review"
        assert "bit_depth_mismatch" in r.candidates[0].reason

    def test_sem_artista_nunca_e_auto(self):
        r = ls.classify(_meta(artist=""), _index(_album(1, "Daft Punk", "Discovery")))
        assert r.kind == "review"
        assert "missing_artist" in r.candidates[0].reason
        assert r.candidates[0].score == 0.6

    def test_pasta_incompleta_nunca_e_auto(self):
        r = ls.classify(
            _meta(state="incomplete"), _index(_album(1, "Daft Punk", "Discovery"))
        )
        assert r.kind == "review"
        assert "pasta_incomplete" in r.candidates[0].reason

    def test_dois_albuns_um_compativel_ainda_vai_para_revisao(self):
        idx = _index(
            _album(1, "Daft Punk", "Discovery", track_count=10),
            _album(2, "Daft Punk", "Discovery", track_count=14),
        )
        r = ls.classify(_meta(tracks=10), idx)
        assert r.kind == "review"


class TestClassifyFuzzy:
    def test_fuzzy_so_sugere_nunca_marca(self):
        r = ls.classify(
            _meta(artist="Daft Punk", album="Discoverry"),
            _index(_album(1, "Daft Punk", "Discovery")),
        )
        assert r.kind == "review"
        assert r.candidates[0].album_id == 1
        assert r.candidates[0].reason.startswith("fuzzy: titulo=")

    def test_fuzzy_por_artista_com_mesmo_album(self):
        r = ls.classify(
            _meta(artist="Daft Punkk", album="Discovery"),
            _index(_album(1, "Daft Punk", "Discovery")),
        )
        assert r.kind == "review"
        assert r.candidates[0].reason.startswith("fuzzy: artista=")

    def test_limiar_alto_rejeita(self):
        r = ls.classify(
            _meta(artist="Daft Punk", album="Discoverry"),
            _index(_album(1, "Daft Punk", "Discovery")),
            fuzzy_threshold=0.999,
        )
        assert r.kind == "unmatched"

    def test_no_maximo_cinco_candidatos_ordenados(self):
        albuns = [
            _album(i, "Artista", f"Titulo Longo Numero {'x' * i}") for i in range(1, 9)
        ]
        r = ls.classify(
            _meta(artist="Artista", album="Titulo Longo Numero xxxxz"),
            _index(*albuns),
            fuzzy_threshold=0.5,
        )
        # 8 álbuns passam do limiar; só os 5 melhores são devolvidos.
        assert r.kind == "review" and len(r.candidates) == 5
        scores = [c.score for c in r.candidates]
        assert scores == sorted(scores, reverse=True)

    def test_nada_parecido_e_unmatched(self):
        r = ls.classify(
            _meta(artist="Zzz", album="Qqq"), _index(_album(1, "Daft Punk", "Discovery"))
        )
        assert r.kind == "unmatched" and r.candidates == ()

    def test_catalogo_vazio_e_unmatched(self):
        assert ls.classify(_meta(), _index()).kind == "unmatched"


# ---------------------------------------------------------------------------
# Item de revisão e escolha manual
# ---------------------------------------------------------------------------
class TestRevisao:
    def test_review_item_serializa_pasta_e_candidatos(self):
        meta = _meta(folder="/m/Daft Punk - Discovery")
        cand = ls.Candidate(5, "qobuz", "Daft Punk", "Discovery", 0.9, "x")
        item = ls._review_item(meta, ls.MatchResult("review", candidates=(cand,)))
        assert item["folder"].replace("\\", "/") == "/m/Daft Punk - Discovery"
        assert item["local_artist"] == "Daft Punk"
        assert item["local_track_count"] == 10
        assert item["candidates"] == [
            {
                "album_id": 5,
                "source": "qobuz",
                "artist": "Daft Punk",
                "title": "Discovery",
                "score": 0.9,
                "reason": "x",
            }
        ]

    def test_apply_review_choice_marca_como_completo(self, tmp_path):
        lib = LibraryDB(tmp_path / "lib.db")
        aid = lib.upsert_album("qobuz", "111", "Discovery", "Daft Punk", track_count=2)
        pasta = tmp_path / "Daft Punk - Discovery"
        pasta.mkdir()
        row = ls.apply_review_choice(
            lib, {"folder": str(pasta)}, aid, sentinel_enabled=False
        )
        assert row["download_status"] == "complete"
        assert row["local_folder_path"] == str(pasta)
