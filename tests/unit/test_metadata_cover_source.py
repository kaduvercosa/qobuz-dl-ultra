"""Testa os helpers de `qobuz_dl/metadata.py` que registram DE ONDE veio a
capa embutida (tag `COVER_SOURCE` + linha "Capa: ..." no comentário) e a
divisão de valores multivalorados.

O comando `tags` grava "Capa: Apple/iTunes" quando troca a capa e o relatório
lê a origem de volta; estes helpers são a ponte entre os dois.
"""

import pytest

from qobuz_dl import metadata

pytestmark = pytest.mark.unit


class TestSplitMultiValue:
    @pytest.mark.parametrize(
        "texto, esperado",
        [
            ("A, B, C", ["A", "B", "C"]),
            ("Solo", ["Solo"]),
            ("", []),
            ("A, , B", ["A", "B"]),
            ("  A ,  B  ", ["A", "B"]),
        ],
    )
    def test_divide_por_virgula_e_espaco(self, texto, esperado):
        assert metadata._split_multi_value(texto) == esperado

    def test_virgula_sem_espaco_nao_divide(self):
        # Só ", " separa: "AC/DC, Queen" tem 2 artistas, "Earth,Wind" tem 1.
        assert metadata._split_multi_value("Earth,Wind") == ["Earth,Wind"]
        assert metadata._split_multi_value("AC/DC, Queen") == ["AC/DC", "Queen"]

    def test_remove_espacos_nas_pontas(self):
        assert metadata._split_multi_value("A,  B") == ["A", "B"]


class TestGetCoverSource:
    def test_sem_tags(self):
        assert metadata._get_cover_source(None) == metadata.COVER_SOURCE_UNKNOWN
        assert metadata._get_cover_source({}) == metadata.COVER_SOURCE_UNKNOWN

    def test_valor_em_texto(self):
        assert metadata._get_cover_source({"COVER_SOURCE": "Qobuz"}) == "Qobuz"

    def test_valor_em_lista_como_no_vorbis(self):
        # O mutagen devolve tags Vorbis como listas.
        assert metadata._get_cover_source({"COVER_SOURCE": ["Apple/iTunes"]}) == (
            "Apple/iTunes"
        )

    def test_lista_vazia_e_valor_vazio_viram_desconhecida(self):
        assert metadata._get_cover_source({"COVER_SOURCE": []}) == (
            metadata.COVER_SOURCE_UNKNOWN
        )
        assert metadata._get_cover_source({"COVER_SOURCE": ""}) == (
            metadata.COVER_SOURCE_UNKNOWN
        )

    def test_apara_espacos(self):
        assert metadata._get_cover_source({"COVER_SOURCE": "  Qobuz  "}) == "Qobuz"

    def test_tag_ausente_com_outras_tags(self):
        assert metadata._get_cover_source({"TITLE": "x"}) == metadata.COVER_SOURCE_UNKNOWN


class TestSetCoverSource:
    def test_grava_a_origem(self):
        tags = {}
        metadata._set_cover_source(tags, "Apple/iTunes")
        assert tags == {"COVER_SOURCE": "Apple/iTunes"}

    def test_apara_espacos(self):
        tags = {}
        metadata._set_cover_source(tags, "  Qobuz ")
        assert tags["COVER_SOURCE"] == "Qobuz"

    @pytest.mark.parametrize("origem", [None, ""])
    def test_origem_vazia_remove_a_tag(self, origem):
        tags = {"COVER_SOURCE": "Qobuz", "TITLE": "x"}
        metadata._set_cover_source(tags, origem)
        assert tags == {"TITLE": "x"}

    def test_remover_tag_inexistente_nao_levanta(self):
        tags = {}
        metadata._set_cover_source(tags, None)
        assert tags == {}

    def test_ida_e_volta(self):
        tags = {}
        metadata._set_cover_source(tags, "Apple/iTunes")
        assert metadata._get_cover_source(tags) == "Apple/iTunes"


class TestCoverCommentLine:
    def test_com_origem(self):
        assert metadata._cover_comment_line("Apple/iTunes") == "Capa: Apple/iTunes"

    @pytest.mark.parametrize("origem", [None, ""])
    def test_sem_origem(self, origem):
        assert metadata._cover_comment_line(origem) == (
            f"Capa: {metadata.COVER_SOURCE_UNKNOWN}"
        )


def test_mapa_de_generos_locais_nao_tem_entradas_vazias():
    assert metadata.LOCAL_GENRE_MAP
    for origem, destino in metadata.LOCAL_GENRE_MAP.items():
        assert origem.strip() and destino.strip()
        assert origem != destino


def test_limite_de_bloco_flac():
    # Um bloco de metadados FLAC tem no máximo 3 bytes de tamanho (0xFFFFFF).
    assert metadata.FLAC_MAX_BLOCKSIZE == 0xFFFFFF
