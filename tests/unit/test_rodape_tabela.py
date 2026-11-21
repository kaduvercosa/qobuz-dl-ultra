"""Rodapé da TABELA: atalhos agrupados em Lista / Ordem / Navegar."""

import pytest

from qobuz_dl import core


def _g(columns=100, **kw):
    base = {"is_multi": True, "sortable": True, "item_category": "track"}
    base.update(kw)
    return core._grupos_de_atalhos(columns, **base)


def test_multi_ordenavel_tem_tres_grupos_e_mantem_os_atalhos():
    grupos = _g()
    assert [r.strip() for r, _t in grupos] == ["Lista", "Ordem", "Navegar"]
    texto = " ".join(t for _r, t in grupos)
    for atalho in (
        "[↑↓/jk] Mover",
        "[Espaço] Selecionar",
        "[t] Todos",
        "[a] A-Z",
        "[y/Y] Ano",
        "[n] Nova",
        "[b] Artista",
        "[m] Menu",
        "[Enter] Confirmar",
    ):
        assert atalho in texto
    assert "[1-9]" not in texto  # multi + ordenável nunca teve "ir para"


def test_artista_sem_multi_abre_o_artista():
    texto = " ".join(t for _r, t in _g(is_multi=False, item_category="artist"))
    assert "[Enter] Abrir artista" in texto
    assert "[t] Todos" not in texto and "[b] Artista" not in texto


def test_playlist_nao_tem_atalho_de_artista():
    texto = " ".join(t for _r, t in _g(item_category="playlist"))
    assert "[b] Artista" not in texto and "[n] Nova" in texto


def test_menu_simples_so_tem_a_lista():
    grupos = _g(is_multi=False, sortable=False, item_category="filter")
    assert [r.strip() for r, _t in grupos] == ["Lista"]


@pytest.mark.parametrize("columns", [73, 80, 100, 140])
def test_nenhuma_linha_passa_da_largura(columns):
    grupos = _g(columns)
    if grupos is None:
        return  # não coube: o rodapé volta à linha única de antes
    for prefixo, texto in grupos:
        assert core.get_cwidth(prefixo + texto) <= columns - 1


def test_sem_espaco_devolve_none():
    assert _g(40) is None
