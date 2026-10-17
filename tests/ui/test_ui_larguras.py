"""Larguras em células de terminal: emoji e CJK ocupam 2 células.

Antes, `banner` centralizava com `str.center` e `stats` preenchia o nome do
artista com `f"{x:<n}"`; os dois contam caracteres, então títulos com emoji
(🔍 INSPETOR DE ÁUDIO) e artistas em japonês passavam da largura da tela.
"""

import pytest

from qobuz_dl import stats_view, ui

LARGURAS = [40, 60, 100]


def _coletar(monkeypatch, largura):
    saida = []
    monkeypatch.setattr(ui, "raw_width", lambda: largura)
    monkeypatch.setattr(ui, "emit", lambda text="", end="\n": saida.append(text))
    return saida


def _linhas(saida):
    return [l for bloco in saida for l in str(bloco).split("\n")]


def _celulas(linha):
    return ui._cell_width(linha)


@pytest.mark.parametrize("n", range(1, 40))
def test_center_cells_igual_ao_str_center_em_ascii(n):
    for texto in ("", "a", "ESTATÍSTICAS", "QOBUZ-DL  ·  X"):
        if len(texto) <= n:
            assert ui.center_cells(texto, n) == texto.center(n)


@pytest.mark.parametrize(
    "texto", ["🔍 INSPETOR DE ÁUDIO", "宇多田ヒカル", "Daft Punk 🎧"]
)
@pytest.mark.parametrize("n", [12, 20, 31, 40, 41])
def test_center_cells_ocupa_exatamente_n_celulas(texto, n):
    assert _celulas(ui.center_cells(texto, n)) == n


def test_pad_cells_conta_celulas_e_nao_caracteres():
    assert _celulas(ui.pad_cells("宇多田", 10)) == 10
    assert _celulas(ui.pad_cells("abc", 10)) == 10
    assert ui.pad_cells("já passou do limite", 3) == "já passou do limite"


@pytest.mark.parametrize("largura", LARGURAS)
def test_banner_com_emoji_cabe_na_tela(largura, monkeypatch):
    saida = _coletar(monkeypatch, largura)
    ui.banner("🔍 INSPETOR DE ÁUDIO")
    for linha in _linhas(saida):
        assert _celulas(linha) <= ui.width(), repr(linha)


@pytest.mark.parametrize("largura", [32, 40, 60])
def test_secao_com_titulo_longo_e_cortada(largura, monkeypatch):
    saida = _coletar(monkeypatch, largura)
    ui.section("🔬 AUTENTICIDADE (heurística espectral) e mais um monte de texto")
    for linha in _linhas(saida):
        assert _celulas(linha) <= ui.width(), repr(linha)


@pytest.mark.parametrize("largura", LARGURAS)
def test_stats_top_artistas_cjk_e_emoji_cabem(largura, monkeypatch):
    stats = {
        "total": 10, "albums": 5, "tracks": 5, "unique_artists": 3,
        "unique_albums": 5, "hires": 5, "quality_met": 9, "quality_not_met": 1,
        "bit_depths": {}, "sample_rates": {}, "formats": {"FLAC": 10},
        "oldest": None, "newest": None,
        "top_artists": [
            ("宇多田ヒカル 宇多田ヒカル 宇多田ヒカル 宇多田ヒカル", 5),
            ("Daft Punk 🎧", 3),
            ("Artista Com Um Nome Extremamente Longo Que Não Cabe Na Tela", 2),
        ],
        "artist_list": [],
    }  # fmt: skip
    monkeypatch.setattr(stats_view, "get_stats", lambda _db: stats)
    saida = _coletar(monkeypatch, largura)
    stats_view.render_stats("x")
    for linha in _linhas(saida):
        assert _celulas(linha) <= ui.width(), repr(linha)
