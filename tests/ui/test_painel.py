"""`ui.Painel`: caixa que mistura pares rótulo/valor e mensagens.

É o desenhista dos relatórios (`inspect`, `stats`): cada seção vira um painel,
no mesmo desenho do `ui.header`/`ui.summary`. Aqui se confere o que o painel
promete: alinhamento, quebra sem estourar a borda (inclusive CJK/emoji),
glifos dos tons, encadeamento sem linhas em branco dobradas e o painel vazio
que não imprime nada.
"""

import re

import pytest

from qobuz_dl import ui

pytestmark = pytest.mark.ui

LARGURAS = [40, 60, 100]
_ANSI = re.compile(r"\x1b\[[0-9;]*m")


@pytest.fixture
def saida(monkeypatch):
    blocos = []
    monkeypatch.setattr(ui, "emit", lambda texto="", end="\n": blocos.append(texto))
    return blocos


def _linhas(blocos):
    return [
        _ANSI.sub("", linha) for bloco in blocos for linha in str(bloco).split("\n")
    ]


def _corpo(blocos):
    """Linhas de dentro da caixa, sem as bordas laterais."""
    return [
        linha[2:].rstrip("│ ").rstrip() if linha.rstrip().endswith("│") else linha[2:]
        for linha in _linhas(blocos)
        if linha.startswith("│")
    ]


@pytest.fixture(autouse=True)
def terminal_fixo(monkeypatch):
    monkeypatch.setattr(ui, "raw_width", lambda *a, **k: 100)
    monkeypatch.delenv("QOBUZ_DL_BOX", raising=False)
    monkeypatch.delenv("QOBUZ_DL_BOX_WIDTH", raising=False)


def test_titulo_na_borda_de_cima(saida):
    ui.Painel("🎤 LETRAS").kv("Letra", "sim").emit()
    topo = next(l for l in _linhas(saida) if l.startswith("╭"))
    assert "🎤 LETRAS" in topo


def test_kv_alinha_os_valores_e_poe_o_rotulo_em_maiusculas(saida):
    ui.Painel("X").kv("Curto", "a").kv("Rótulo mais longo", "b").emit()
    corpo = _corpo(saida)
    assert corpo[0].startswith("CURTO") and corpo[1].startswith("RÓTULO MAIS LONGO")
    assert corpo[0].index("a") == corpo[1].index("b")


def test_valor_longo_quebra_com_recuo_sob_o_valor(saida):
    ui.Painel("X").kv("Rótulo", "palavra " * 30).emit()
    corpo = _corpo(saida)
    assert len(corpo) > 1
    recuo = len("RÓTULO") + 2
    assert all(l.startswith(" " * recuo) for l in corpo[1:])


@pytest.mark.parametrize(
    "tom, metodo", [("ok", "ok"), ("warn", "warn"), ("err", "error"), ("info", "info")]
)
def test_mensagem_comeca_com_o_glifo_do_tom(saida, tom, metodo):
    painel = ui.Painel("X")
    getattr(painel, metodo)("tudo certo")
    painel.emit()
    primeira = _corpo(saida)[0]
    fancy, plain, _ = ui._TONES[tom]
    assert primeira.split()[0] in (fancy, plain)
    assert primeira.endswith("tudo certo")


def test_mensagem_longa_quebra_alinhada_depois_do_glifo(saida):
    ui.Painel("X").warn("palavra " * 30).emit()
    corpo = _corpo(saida)
    assert len(corpo) > 1
    assert all(l.startswith("  palavra") for l in corpo[1:])  # sob o texto


def test_detail_e_text_respeitam_o_recuo(saida):
    ui.Painel("X").text("faixa", indent=0).detail("apoio", indent=2).emit()
    linhas = [l for l in _linhas(saida) if l.startswith("│")]
    assert linhas[0].startswith("│ faixa")
    assert linhas[1].startswith("│   apoio")


def test_rule_vira_divisoria(saida):
    ui.Painel("X").kv("a", "1").rule().kv("b", "2").emit()
    assert any(l.startswith("├") for l in _linhas(saida))


def test_painel_vazio_nao_imprime_nada(saida):
    painel = ui.Painel("X")
    assert not painel
    painel.emit()
    assert saida == []


def test_lead_controla_a_linha_em_branco_antes(saida):
    ui.Painel("A").kv("a", "1").emit(lead=True)
    ui.Painel("B").kv("b", "2").emit(lead=False)
    assert saida[0].startswith("\n") and saida[0].endswith("\n")
    assert not saida[1].startswith("\n") and saida[1].endswith("\n")


def test_encadeia_metodos(saida):
    painel = ui.Painel("X")
    assert painel.kv("a", "1").ok("b").detail("c").text("d").rule() is painel


@pytest.mark.parametrize("largura", LARGURAS)
def test_nenhuma_linha_passa_da_largura_do_terminal(largura, monkeypatch, saida):
    monkeypatch.setattr(ui, "raw_width", lambda *a, **k: largura)
    painel = ui.Painel(
        "🔬 AUTENTICIDADE (heurística espectral) com título longo demais"
    )
    painel.kv("Rótulo extremamente longo mesmo", "宇多田ヒカル " * 12)
    painel.warn("Daft Punk 🎧 " * 10).detail("x" * 200, indent=2)
    painel.text("caminho/" + "a" * 120)
    painel.emit()
    for linha in _linhas(saida):
        assert ui._cell_width(linha) <= ui.width(), repr(linha)


def test_painel_usa_a_largura_padrao_de_relatorio(saida):
    ui.Painel("X").kv("a", "1").emit()
    topo = next(l for l in _linhas(saida) if l.startswith("╭"))
    assert ui._cell_width(topo) == ui.box_width(ui.PAINEL_COLS)
