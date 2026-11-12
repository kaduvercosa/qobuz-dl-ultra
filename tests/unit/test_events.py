"""Eventos em JSON (`--events`): formato, contadores e o funil do postprocess.

O QUE ESTE ARQUIVO TRAVA
------------------------
- Uma linha JSON por evento, com `v`, `ts` e `evento`; destino ruim nunca derruba.
- `faixa.nova` só é verdadeira para o que foi baixado AGORA (o motivo "Arquivo já
  existente" do downloader é o que separa os dois casos; o teste confere que o
  texto continua lá).
- O downloader renomeia a pasta ("[IN PROGRESS] X" -> "X"); as faixas contadas
  antes do rename têm de aparecer no `lancamento_concluido` da pasta final.
- `postprocess` é o funil: é ele que emite os eventos de verdade.
"""

import io
import json
import sys
from pathlib import Path

import pytest

from qobuz_dl import events

pytestmark = pytest.mark.unit

RAIZ = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _limpo():
    events.reiniciar()
    yield
    events.reiniciar()


def _linhas(caminho):
    texto = Path(caminho).read_text(encoding="utf-8")
    return [json.loads(x) for x in texto.splitlines()]


def _relatorio(total=2, ok=2, falhas=0):
    return {
        "tipo": "album",
        "identificacao": {"titulo": "Album", "artista": "Art", "id": "1", "url": "u"},
        "progresso": {
            "resumo": {
                "total": total,
                "concluidas": ok,
                "puladas": 0,
                "falhas": falhas,
                "pendentes": 0,
            }
        },
        "qualidade": {"formato": "FLAC", "bit_depth": 24, "sampling_rate": 96},
    }


def test_uma_linha_json_por_evento(tmp_path):
    destino = tmp_path / "sub" / "ev.ndjson"
    assert events.configurar(str(destino)) is True
    events.emitir("teste", campo="açaí")
    events.emitir("teste", campo=2)
    events.fechar()
    achados = _linhas(destino)
    assert [e["campo"] for e in achados] == ["açaí", 2]
    assert all(
        e["v"] == events.VERSAO_ESQUEMA and e["evento"] == "teste" for e in achados
    )
    assert all("ts" in e for e in achados)
    assert "açaí" in destino.read_text(encoding="utf-8")  # sem \u00e7


def test_traco_grava_no_stderr(monkeypatch):
    falso = io.StringIO()
    monkeypatch.setattr(sys, "stderr", falso)
    assert events.configurar("-") is True
    events.emitir("teste")
    assert json.loads(falso.getvalue())["evento"] == "teste"


def test_destino_ruim_nao_levanta(tmp_path):
    arquivo = tmp_path / "arquivo"
    arquivo.write_text("x")
    assert events.configurar(str(arquivo / "dentro.ndjson")) is False
    events.emitir("teste")  # sem destino: nada acontece, nada quebra
    assert events.gravando() is False


def test_vazio_e_off_desligam(tmp_path):
    assert events.configurar("") is False
    assert events.configurar("off") is False


def test_assinante_com_defeito_nao_derruba():
    vistos = []

    def ruim(_ev):
        raise RuntimeError("defeito")

    events.assinar(ruim)
    events.assinar(vistos.append)
    events.emitir("teste")
    assert [e["evento"] for e in vistos] == ["teste"]


def test_lancamento_iniciado_so_uma_vez_ignorando_prefixo(tmp_path):
    vistos = []
    events.assinar(vistos.append)
    events.lancamento_iniciado(str(tmp_path / "[IN PROGRESS] Album"), titulo="A")
    events.lancamento_iniciado(str(tmp_path / "Album"), titulo="A")
    events.lancamento_iniciado(str(tmp_path / "[INCOMPLETE] Album"), titulo="A")
    assert [e["evento"] for e in vistos] == ["lancamento_iniciado"]


def test_faixa_nova_e_existente():
    vistos = []
    events.assinar(vistos.append)
    events.faixa("/m/A", "01", 1, "a", "Art", "ok")
    events.faixa("/m/A", "02", 2, "b", "Art", "ok", events.MOTIVO_EXISTENTE)
    events.faixa("/m/A", "03", 3, "c", "Art", "falha", "erro")
    assert [(e["situacao"], e["nova"]) for e in vistos] == [
        ("concluido", True),
        ("concluido", False),
        ("falha", False),
    ]
    resumo = events.resumo_sessao()
    assert resumo["faixas_novas"] == 1 and resumo["faixas_falhas"] == 1


def test_estado_final_da_faixa_vale(tmp_path):
    events.faixa("/m/A", "01", 1, "a", "Art", "falha", "rede")
    events.faixa("/m/A", "01", 1, "a", "Art", "ok")  # tentou de novo e deu certo
    resumo = events.resumo_sessao()
    assert resumo["faixas_novas"] == 1 and resumo["faixas_falhas"] == 0


def test_motivo_existente_ainda_esta_no_downloader():
    codigo = (RAIZ / "qobuz_dl" / "downloader.py").read_text(encoding="utf-8")
    assert events.MOTIVO_EXISTENTE in codigo, (
        "o downloader mudou o texto do motivo de arquivo existente; ajuste "
        "events.MOTIVO_EXISTENTE (senão toda faixa velha conta como nova)"
    )


def test_concluido_soma_faixas_contadas_antes_do_rename(tmp_path):
    vistos = []
    events.assinar(vistos.append)
    trabalho = str(tmp_path / "[IN PROGRESS] Album")
    events.faixa(trabalho, "01", 1, "a", "Art", "ok")
    events.faixa(trabalho, "02", 2, "b", "Art", "ok", events.MOTIVO_EXISTENTE)
    events.lancamento_concluido(str(tmp_path / "Album"), True, _relatorio())
    fim = [e for e in vistos if e["evento"] == "lancamento_concluido"][0]
    assert fim["faixas_novas"] == 1
    assert fim["completo"] is True
    assert fim["resumo"]["total"] == 2
    assert fim["qualidade"] == {"formato": "FLAC", "bit_depth": 24, "sampling_rate": 96}


def test_concluido_repetido_igual_nao_repete(tmp_path):
    vistos = []
    events.assinar(vistos.append)
    pasta = str(tmp_path / "Album")
    events.lancamento_concluido(pasta, True, _relatorio())
    events.lancamento_concluido(pasta, True, _relatorio())
    assert len(vistos) == 1
    events.lancamento_concluido(pasta, False, _relatorio(ok=1, falhas=1))
    assert len(vistos) == 2  # mudou o resultado: é outro evento


def test_sessao_concluida_leva_o_resumo(tmp_path):
    vistos = []
    events.assinar(vistos.append)
    events.faixa("/m/A", "01", 1, "a", "Art", "ok")
    events.lancamento_concluido("/m/A", True, _relatorio())
    resumo = events.sessao_concluida(True)
    fim = vistos[-1]
    assert fim["evento"] == "sessao_concluida" and fim["ok"] is True
    assert fim["lancamentos"] == resumo["lancamentos"] == 1
    assert fim["faixas_novas"] == 1


async def test_postprocess_emite_os_eventos(tmp_path):
    """O funil real: init_report, update_track_status e finalize_report."""
    from qobuz_dl import postprocess

    vistos = []
    events.assinar(vistos.append)
    pasta = tmp_path / "[IN PROGRESS] Album"
    pasta.mkdir()
    await postprocess.init_report(
        str(pasta),
        tipo="album",
        titulo="Album",
        artista="Art",
        item_id="1",
        faixas_previstas=[{"numero": "01", "id": 1, "titulo": "a"}],
    )
    await postprocess.init_report(
        str(pasta), tipo="album", titulo="Album", artista="Art"
    )
    await postprocess.update_track_status(
        str(pasta), numero="01", item_id=1, titulo="a", status="ok", artista="Art"
    )
    final = tmp_path / "Album"
    pasta.rename(final)
    await postprocess.finalize_report(str(final), completo=True)

    nomes = [e["evento"] for e in vistos]
    assert nomes == ["lancamento_iniciado", "faixa", "lancamento_concluido"]
    assert vistos[1]["situacao"] == "concluido" and vistos[1]["nova"] is True
    assert vistos[2]["completo"] is True and vistos[2]["faixas_novas"] == 1


def test_modulos_so_usam_a_biblioteca_padrao_no_import():
    """events e hooks entram no import do programa: nada pesado no topo."""
    import ast

    permitidos = {
        "__future__", "asyncio", "hashlib", "hmac", "json", "logging", "os",
        "secrets", "sys", "threading", "time", "dataclasses", "datetime",
        "typing", "urllib", "qobuz_dl",
    }  # fmt: skip
    for nome in ("events.py", "hooks.py"):
        arvore = ast.parse((RAIZ / "qobuz_dl" / nome).read_text(encoding="utf-8"))
        for no in arvore.body:
            if isinstance(no, ast.Import):
                modulos = {a.name.split(".")[0] for a in no.names}
            elif isinstance(no, ast.ImportFrom):
                modulos = {(no.module or "").split(".")[0]}
            else:
                continue
            assert modulos <= permitidos, (
                f"{nome}: import pesado no topo: {modulos - permitidos}"
            )
