"""Índice persistente da biblioteca (qobuz_dl/indice_biblioteca.py).

A tag só é lida na primeira vez e para arquivo novo/alterado; nas execuções
seguintes basta olhar tamanho e data de cada arquivo.
"""

import os

import pytest

from qobuz_dl import indice_biblioteca as ib

pytestmark = pytest.mark.unit


class Leitores:
    def __init__(self):
        self.ids = []

    def ler_id(self, caminho):
        self.ids.append(caminho)
        nome = os.path.basename(caminho).split(".")[0]
        return None if nome.startswith("semtag") else nome

    def ler_info(self, caminho):
        return ("flac", 24, 96.0)


def _arquivo(pasta, nome, conteudo=b"audio"):
    caminho = pasta / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(conteudo)
    return str(caminho)


def test_primeira_vez_le_tudo_e_a_segunda_nao_le_nada(tmp_path):
    lib = tmp_path / "lib"
    a = _arquivo(lib, "Album/100.flac")
    _arquivo(lib, "Album/101.flac")
    _arquivo(lib, "Album/capa.jpg")  # não é áudio
    indice = str(tmp_path / "track_index.db")
    leitores = Leitores()

    s1 = ib.atualizar(lib, indice, leitores.ler_id, leitores.ler_info)
    assert s1 == {"total": 2, "lidos": 2, "removidos": 0}

    leitores.ids.clear()
    s2 = ib.atualizar(lib, indice, leitores.ler_id, leitores.ler_info)
    assert s2 == {"total": 2, "lidos": 0, "removidos": 0}
    assert leitores.ids == []  # nenhuma tag lida de novo
    assert ib.consultar(indice, "100") == [ib.ArquivoIndexado(a, "flac", 24, 96.0)]


def test_arquivo_novo_alterado_e_removido(tmp_path):
    lib = tmp_path / "lib"
    a = _arquivo(lib, "100.flac")
    b = _arquivo(lib, "101.flac")
    indice = str(tmp_path / "idx.db")
    leitores = Leitores()
    ib.atualizar(lib, indice, leitores.ler_id, leitores.ler_info)

    novo = _arquivo(lib, "102.flac")
    _arquivo(lib, "100.flac", b"conteudo maior, o tamanho mudou")  # alterado
    os.remove(b)
    leitores.ids.clear()
    stats = ib.atualizar(lib, indice, leitores.ler_id, leitores.ler_info)

    assert stats == {"total": 2, "lidos": 2, "removidos": 1}
    assert sorted(leitores.ids) == sorted([a, novo])
    assert ib.consultar(indice, "101") == []
    assert [x.caminho for x in ib.consultar(indice, "102")] == [novo]


def test_arquivo_sem_tag_de_id_nao_entra_nas_consultas(tmp_path):
    lib = tmp_path / "lib"
    _arquivo(lib, "semtag.flac")
    indice = str(tmp_path / "idx.db")
    leitores = Leitores()

    stats = ib.atualizar(lib, indice, leitores.ler_id, leitores.ler_info)

    assert stats["total"] == 1
    assert ib.consultar(indice, "semtag") == []


def test_pasta_inexistente_e_indice_ausente_nao_levantam(tmp_path):
    leitores = Leitores()
    indice = str(tmp_path / "idx.db")

    assert ib.atualizar(
        tmp_path / "nao", indice, leitores.ler_id, leitores.ler_info
    ) == {
        "total": 0,
        "lidos": 0,
        "removidos": 0,
    }
    assert ib.consultar(str(tmp_path / "nao_existe.db"), "1") == []
    assert not os.path.exists(indice)


@pytest.mark.parametrize(
    ("formato", "bits", "taxa", "esperado"),
    [
        ("mp3", 0, 44.1, 5),
        ("flac", 16, 44.1, 6),
        ("flac", 24, 44.1, 7),
        ("flac", 24, 96.0, 7),
        ("flac", 24, 88.2, 7),
        ("flac", 24, 176.4, 27),
        ("flac", 24, 192.0, 27),
        ("", 0, 0.0, 0),
    ],
)
def test_nivel_de_qualidade(formato, bits, taxa, esperado):
    assert ib.nivel_de_qualidade(formato, bits, taxa) == esperado


def test_o_indice_fica_ao_lado_do_banco(tmp_path):
    assert ib.caminho_do_indice(str(tmp_path / "qobuz_dl.db")) == str(
        tmp_path / "track_index.db"
    )
