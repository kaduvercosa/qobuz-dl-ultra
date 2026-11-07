"""Testa qobuz_dl/duplicates_cmd.py: escolha da cópia, mover e desfazer."""

import json
import os
from types import SimpleNamespace

from qobuz_dl import duplicates_cmd as dup


def _arq(raiz, rel, tamanho=10):
    caminho = os.path.join(str(raiz), *rel.split("/"))
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "wb") as f:
        f.write(b"x" * tamanho)
    return caminho


def _args(**kw):
    base = dict(DIR=None, mover=False, desfazer=False, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _buscar(grupos):
    async def buscar(_pasta):
        return grupos

    return buscar


def test_escolhe_flac_mesmo_que_o_mp3_seja_maior(tmp_path):
    mp3 = _arq(tmp_path, "a.mp3", 500)
    flac = _arq(tmp_path, "b.flac", 100)
    fica, sobras = dup.escolher_copia([mp3, flac])
    assert fica == flac and sobras == [mp3]


def test_entre_iguais_escolhe_o_maior_arquivo(tmp_path):
    pequeno = _arq(tmp_path, "a.flac", 10)
    grande = _arq(tmp_path, "b.flac", 99)
    assert dup.escolher_copia([pequeno, grande])[0] == grande


def test_empate_total_usa_caminho_mais_curto_e_e_deterministico(tmp_path):
    longo = _arq(tmp_path, "sub/pasta/a.flac", 10)
    curto = _arq(tmp_path, "a.flac", 10)
    assert dup.escolher_copia([longo, curto])[0] == curto
    assert dup.escolher_copia([curto, longo]) == dup.escolher_copia([longo, curto])


def test_mover_tira_as_sobras_e_mantem_a_melhor(tmp_path):
    fica = _arq(tmp_path, "A/a.flac", 50)
    sobra = _arq(tmp_path, "B/a.flac", 10)
    movidos, erros = dup.mover_sobras(str(tmp_path), [(fica, [sobra])])
    assert (movidos, erros) == (1, [])
    assert os.path.exists(fica) and not os.path.exists(sobra)
    assert os.path.exists(os.path.join(tmp_path, "_duplicatas", "a.flac"))


def test_mover_nao_sobrescreve_nomes_iguais_em_duplicatas(tmp_path):
    fica = _arq(tmp_path, "keep.flac", 50)
    s1 = _arq(tmp_path, "A/x.flac", 10)
    s2 = _arq(tmp_path, "B/x.flac", 11)
    dup.mover_sobras(str(tmp_path), [(fica, [s1, s2])])
    nomes = sorted(os.listdir(tmp_path / "_duplicatas"))
    assert nomes == [".movidos.json", "x (2).flac", "x.flac"]


def test_desfazer_devolve_tudo_ao_lugar_e_limpa_a_pasta(tmp_path):
    fica = _arq(tmp_path, "keep.flac", 50)
    sobra = _arq(tmp_path, "B/x.flac", 10)
    dup.mover_sobras(str(tmp_path), [(fica, [sobra])])
    devolvidos, avisos = dup.desfazer_movimentos(str(tmp_path))
    assert (devolvidos, avisos) == (1, [])
    assert os.path.exists(sobra)
    assert not (tmp_path / "_duplicatas").exists()


def test_desfazer_avisa_quando_o_lugar_original_foi_ocupado(tmp_path):
    fica = _arq(tmp_path, "keep.flac", 50)
    sobra = _arq(tmp_path, "B/x.flac", 10)
    dup.mover_sobras(str(tmp_path), [(fica, [sobra])])
    _arq(tmp_path, "B/x.flac", 3)  # alguém criou outro arquivo ali
    devolvidos, avisos = dup.desfazer_movimentos(str(tmp_path))
    assert devolvidos == 0 and len(avisos) == 1
    # o arquivo continua guardado e registrado para uma nova tentativa
    assert os.path.exists(tmp_path / "_duplicatas" / "x.flac")
    assert len(json.load(open(dup._caminho_diario(str(tmp_path))))) == 1


def test_desfazer_sem_diario_nao_faz_nada(tmp_path):
    assert dup.desfazer_movimentos(str(tmp_path)) == (0, [])


async def test_cmd_sem_mover_so_lista(tmp_path, capsys):
    fica = _arq(tmp_path, "a.flac", 50)
    sobra = _arq(tmp_path, "b.mp3", 10)
    codigo = await dup.cmd_duplicates(
        _args(), directory=str(tmp_path), buscar=_buscar({"fp": [fica, sobra]})
    )
    assert codigo == 0
    assert os.path.exists(sobra)
    assert not (tmp_path / "_duplicatas").exists()
    saida = capsys.readouterr().out
    assert "fica: a.flac" in saida and "sobra: b.mp3" in saida


async def test_cmd_mover_e_depois_desfazer(tmp_path):
    fica = _arq(tmp_path, "a.flac", 50)
    sobra = _arq(tmp_path, "b.mp3", 10)
    grupos = {"fp": [fica, sobra]}
    assert (
        await dup.cmd_duplicates(
            _args(mover=True), directory=str(tmp_path), buscar=_buscar(grupos)
        )
        == 0
    )
    assert not os.path.exists(sobra)
    assert await dup.cmd_duplicates(_args(desfazer=True), directory=str(tmp_path)) == 0
    assert os.path.exists(sobra)


async def test_cmd_sem_duplicatas(tmp_path, capsys):
    codigo = await dup.cmd_duplicates(
        _args(), directory=str(tmp_path), buscar=_buscar({})
    )
    assert codigo == 0
    assert "Nenhuma duplicata" in capsys.readouterr().out


async def test_cmd_json_lista_fica_e_sobras(tmp_path, capsys):
    fica = _arq(tmp_path, "a.flac", 50)
    sobra = _arq(tmp_path, "b.mp3", 10)
    await dup.cmd_duplicates(
        _args(json=True), directory=str(tmp_path), buscar=_buscar({"f": [sobra, fica]})
    )
    dados = json.loads(capsys.readouterr().out)
    assert dados == [{"fica": fica, "sobras": [sobra]}]


async def test_cmd_pasta_inexistente(tmp_path):
    assert await dup.cmd_duplicates(_args(), directory=str(tmp_path / "nada")) == 1
