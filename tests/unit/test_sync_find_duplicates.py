"""Testa qobuz_dl/sync.py::find_duplicate_tracks (agrupamento por assinatura).

_compute_fingerprint é trocada por um dublê que devolve a assinatura de cada
arquivo, então nenhum áudio real é lido (a função em si já tem testes em
test_sync_fingerprint.py).
"""

import pytest

from qobuz_dl import sync
from qobuz_dl.sync import find_duplicate_tracks

pytestmark = pytest.mark.unit


def _criar(tmp_path, *nomes):
    caminhos = []
    for nome in nomes:
        destino = tmp_path / nome
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_bytes(b"x")
        caminhos.append(str(destino))
    return caminhos


def _assinaturas(monkeypatch, por_nome):
    def fake(caminho):
        nome = caminho.replace("\\", "/").rsplit("/", 1)[-1]
        valor = por_nome[nome]
        return (None, None) if valor is None else (180, valor)

    monkeypatch.setattr(sync, "_compute_fingerprint", fake)


async def test_pasta_vazia_devolve_dict_vazio(tmp_path):
    assert await find_duplicate_tracks(str(tmp_path)) == {}


async def test_ignora_arquivos_que_nao_sao_flac_ou_mp3(tmp_path, monkeypatch):
    _criar(tmp_path, "capa.jpg", "faixa.wav", "notas.txt")
    _assinaturas(monkeypatch, {})
    assert await find_duplicate_tracks(str(tmp_path)) == {}


async def test_sem_duplicatas(tmp_path, monkeypatch):
    _criar(tmp_path, "a.flac", "b.flac")
    _assinaturas(monkeypatch, {"a.flac": "fp:1", "b.flac": "fp:2"})
    assert await find_duplicate_tracks(str(tmp_path)) == {}


async def test_agrupa_arquivos_com_a_mesma_assinatura(tmp_path, monkeypatch):
    a, b, c = _criar(tmp_path, "a.flac", "sub/b.flac", "c.mp3")
    _assinaturas(monkeypatch, {"a.flac": "fp:1", "b.flac": "fp:1", "c.mp3": "fp:2"})
    resultado = await find_duplicate_tracks(str(tmp_path))

    assert list(resultado) == ["fp:1"]
    assert sorted(resultado["fp:1"]) == sorted([a, b])


async def test_varios_grupos_independentes(tmp_path, monkeypatch):
    _criar(tmp_path, "a.flac", "b.flac", "c.mp3", "d.mp3", "e.flac")
    _assinaturas(
        monkeypatch,
        {
            "a.flac": "x",
            "b.flac": "x",
            "c.mp3": "y",
            "d.mp3": "y",
            "e.flac": "z",
        },
    )
    resultado = await find_duplicate_tracks(str(tmp_path))
    assert set(resultado) == {"x", "y"}
    assert all(len(v) == 2 for v in resultado.values())


async def test_extensao_em_maiusculas_e_considerada(tmp_path, monkeypatch):
    _criar(tmp_path, "A.FLAC", "B.MP3")
    _assinaturas(monkeypatch, {"A.FLAC": "k", "B.MP3": "k"})
    resultado = await find_duplicate_tracks(str(tmp_path))
    assert len(resultado["k"]) == 2


async def test_arquivos_ilegiveis_sao_pulados_e_contados(tmp_path, monkeypatch, caplog):
    _criar(tmp_path, "a.flac", "b.flac", "ruim.flac")
    _assinaturas(monkeypatch, {"a.flac": "k", "b.flac": "k", "ruim.flac": None})
    with caplog.at_level("INFO", logger="qobuz_dl.sync"):
        resultado = await find_duplicate_tracks(str(tmp_path))
    assert len(resultado["k"]) == 2
    assert "1 arquivo(s) ignorado(s)" in caplog.text


async def test_arquivo_ilegivel_nunca_forma_grupo(tmp_path, monkeypatch):
    _criar(tmp_path, "a.flac", "b.flac")
    _assinaturas(monkeypatch, {"a.flac": None, "b.flac": None})
    assert await find_duplicate_tracks(str(tmp_path)) == {}


async def test_log_informa_total_de_redundantes(tmp_path, monkeypatch, caplog):
    _criar(tmp_path, "a.flac", "b.flac", "c.flac")
    _assinaturas(monkeypatch, {"a.flac": "k", "b.flac": "k", "c.flac": "k"})
    with caplog.at_level("INFO", logger="qobuz_dl.sync"):
        await find_duplicate_tracks(str(tmp_path))
    assert "1 grupo(s)" in caplog.text and "2 arquivo(s) redundante(s)" in caplog.text


async def test_progresso_a_cada_25_arquivos(tmp_path, monkeypatch, caplog):
    nomes = [f"{i:03d}.flac" for i in range(26)]
    _criar(tmp_path, *nomes)
    _assinaturas(monkeypatch, {n: f"unico:{n}" for n in nomes})
    with caplog.at_level("INFO", logger="qobuz_dl.sync"):
        await find_duplicate_tracks(str(tmp_path))
    assert "[25/26]" in caplog.text and "[26/26]" in caplog.text
