"""Testa qobuz_dl/library_cleanup.py: temporários velhos e pastas vazias."""

import os
import time
from types import SimpleNamespace

from qobuz_dl import library_cleanup as lc


def _arq(raiz, rel, idade_h=0.0):
    caminho = os.path.join(str(raiz), *rel.split("/"))
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "wb") as f:
        f.write(b"x")
    if idade_h:
        t = time.time() - idade_h * 3600
        os.utime(caminho, (t, t))
    return caminho


def _args(**kw):
    base = dict(DIR=None, apagar=False, idade=None)
    base.update(kw)
    return SimpleNamespace(**base)


def test_pastas_vazias_acha_a_pasta_sem_arquivos(tmp_path):
    (tmp_path / "vazia").mkdir()
    _arq(tmp_path, "cheia/a.flac")
    assert lc.pastas_vazias(str(tmp_path)) == [str(tmp_path / "vazia")]


def test_pastas_vazias_nunca_inclui_a_raiz(tmp_path):
    assert lc.pastas_vazias(str(tmp_path)) == []


def test_pastas_vazias_encadeia_pais_que_so_tem_subpastas_vazias(tmp_path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    achadas = lc.pastas_vazias(str(tmp_path))
    assert achadas == [str(tmp_path / "a" / "b"), str(tmp_path / "a")]


def test_pasta_so_com_arquivo_oculto_nao_e_vazia(tmp_path):
    _arq(tmp_path, "x/.DS_Store")
    assert lc.pastas_vazias(str(tmp_path)) == []


def test_ignorar_conta_temporarios_como_ja_removidos(tmp_path):
    tmp = _arq(tmp_path, "x/a.flac.part")
    assert lc.pastas_vazias(str(tmp_path), ignorar=frozenset({tmp})) == [
        str(tmp_path / "x")
    ]


async def test_simulacao_nao_apaga_nada(tmp_path, capsys):
    velho = _arq(tmp_path, "x/a.flac.part", idade_h=48)
    assert await lc.cmd_cleanup(_args(), directory=str(tmp_path)) == 0
    assert os.path.exists(velho)
    saida = capsys.readouterr().out
    assert "simulação" in saida and "x/a.flac.part".replace("/", os.sep) in saida


async def test_apagar_remove_temporario_velho_e_a_pasta_que_sobrou_vazia(tmp_path):
    velho = _arq(tmp_path, "x/a.flac.part", idade_h=48)
    assert await lc.cmd_cleanup(_args(apagar=True), directory=str(tmp_path)) == 0
    assert not os.path.exists(velho)
    assert not (tmp_path / "x").exists()
    assert tmp_path.exists()


async def test_apagar_preserva_temporario_recente(tmp_path):
    recente = _arq(tmp_path, "x/a.flac.part", idade_h=1)
    await lc.cmd_cleanup(_args(apagar=True), directory=str(tmp_path))
    assert os.path.exists(recente)


async def test_idade_menor_alcanca_temporarios_mais_novos(tmp_path):
    recente = _arq(tmp_path, "x/a.flac.part", idade_h=2)
    await lc.cmd_cleanup(_args(apagar=True, idade=1), directory=str(tmp_path))
    assert not os.path.exists(recente)


async def test_apagar_nunca_toca_em_audio_capa_ou_letra(tmp_path):
    itens = [
        _arq(tmp_path, "Al/01.flac", idade_h=999),
        _arq(tmp_path, "Al/cover.jpg", idade_h=999),
        _arq(tmp_path, "Al/01.lrc", idade_h=999),
    ]
    await lc.cmd_cleanup(_args(apagar=True), directory=str(tmp_path))
    assert all(os.path.exists(i) for i in itens)


async def test_nada_para_limpar(tmp_path, capsys):
    _arq(tmp_path, "Al/01.flac")
    assert await lc.cmd_cleanup(_args(), directory=str(tmp_path)) == 0
    assert "Nada para limpar" in capsys.readouterr().out


async def test_pasta_inexistente_devolve_1(tmp_path):
    assert await lc.cmd_cleanup(_args(), directory=str(tmp_path / "nada")) == 1
