"""Rodapé de atalhos do modo interativo.

Os valores esperados foram tirados do código ORIGINAL (antes de a lógica sair do
`get_footer_text` para funções de módulo), então este arquivo trava o comportamento.
Formato: (colunas, is_multi, categoria, esperado).
"""

import pytest

from qobuz_dl import core

LINHA_UNICA = [
    (
        120,
        True,
        "album",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        True,
        "album",
        " [↑↓/jk] Mover [Espaço] Marcar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] OK",
    ),
    (80, True, "album", " ↑↓ Esp t a y n b m ↵"),
    (60, True, "album", " ↑↓ Esp t a y n b m ↵"),
    (45, True, "album", " ↑↓ Esp t a y n b m ↵"),
    (30, True, "album", " ↑↓ Esp t a y n b m ↵"),
    (
        120,
        False,
        "album",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        False,
        "album",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] Confirmar",
    ),
    (
        80,
        False,
        "album",
        " [↑↓/jk] Mover [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] OK",
    ),
    (60, False, "album", " ↑↓ a y n b m ↵"),
    (45, False, "album", " ↑↓ a y n b m ↵"),
    (30, False, "album", " ↑↓ a y n b m ↵"),
    (
        120,
        True,
        "track",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        True,
        "track",
        " [↑↓/jk] Mover [Espaço] Marcar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] OK",
    ),
    (80, True, "track", " ↑↓ Esp t a y n b m ↵"),
    (60, True, "track", " ↑↓ Esp t a y n b m ↵"),
    (45, True, "track", " ↑↓ Esp t a y n b m ↵"),
    (30, True, "track", " ↑↓ Esp t a y n b m ↵"),
    (
        120,
        False,
        "track",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        False,
        "track",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] Confirmar",
    ),
    (
        80,
        False,
        "track",
        " [↑↓/jk] Mover [a] A-Z [y/Y] Ano [n] Nova [b] Artista [m] Menu [Enter] OK",
    ),
    (60, False, "track", " ↑↓ a y n b m ↵"),
    (45, False, "track", " ↑↓ a y n b m ↵"),
    (30, False, "track", " ↑↓ a y n b m ↵"),
    (
        120,
        True,
        "playlist",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        True,
        "playlist",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Confirmar",
    ),
    (
        80,
        True,
        "playlist",
        " [↑↓]Mover [Esp]Marcar [t]Todos [a]A-Z [y]Ano [n] Nova [m] Menu [↵]OK",
    ),
    (60, True, "playlist", " ↑↓ Esp t a y n b m ↵"),
    (45, True, "playlist", " ↑↓ Esp t a y n b m ↵"),
    (30, True, "playlist", " ↑↓ Esp t a y n b m ↵"),
    (
        120,
        False,
        "playlist",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        False,
        "playlist",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Confirmar",
    ),
    (
        80,
        False,
        "playlist",
        " [↑↓/jk] Mover [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] OK",
    ),
    (60, False, "playlist", " [↑↓]Mover [a]A-Z [y]Ano [n] Nova [m] Menu [↵]OK"),
    (45, False, "playlist", " ↑↓ a y n b m ↵"),
    (30, False, "playlist", " ↑↓ a y n b m ↵"),
    (
        120,
        True,
        "artist",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Confirmar",
    ),
    (
        100,
        True,
        "artist",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Confirmar",
    ),
    (
        80,
        True,
        "artist",
        " [↑↓]Mover [Esp]Marcar [t]Todos [a]A-Z [y]Ano [n] Nova [m] Menu [↵]OK",
    ),
    (60, True, "artist", " ↑↓ Esp t a y n b m ↵"),
    (45, True, "artist", " ↑↓ Esp t a y n b m ↵"),
    (30, True, "artist", " ↑↓ Esp t a y n b m ↵"),
    (
        120,
        False,
        "artist",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Abrir artista",
    ),
    (
        100,
        False,
        "artist",
        " [↑↓/jk] Mover [1-9] Ir para [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Abrir artista",
    ),
    (
        80,
        False,
        "artist",
        " [↑↓/jk] Mover [a] A-Z [y/Y] Ano [n] Nova [m] Menu [Enter] Abrir",
    ),
    (60, False, "artist", " [↑↓]Mover [a]A-Z [y]Ano [n] Nova [m] Menu [↵]Abrir"),
    (45, False, "artist", " ↑↓ a y n m ↵"),
    (30, False, "artist", " ↑↓ a y n m ↵"),
    (
        120,
        True,
        "filter",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [1-9] Ir para [Enter] Confirmar",
    ),
    (
        100,
        True,
        "filter",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [1-9] Ir para [Enter] Confirmar",
    ),
    (
        80,
        True,
        "filter",
        " [↑↓/jk] Mover [Espaço] Selecionar [t] Todos [1-9] Ir para [Enter] Confirmar",
    ),
    (60, True, "filter", " [↑↓]Mover [Esp]Marcar [t]Todos [1-9]Ir [↵]OK"),
    (45, True, "filter", " ↑↓ Esp t ↵"),
    (30, True, "filter", " ↑↓ Esp t ↵"),
    (120, False, "filter", " [↑↓/jk] Mover [1-9] Ir para [Enter] Confirmar"),
    (100, False, "filter", " [↑↓/jk] Mover [1-9] Ir para [Enter] Confirmar"),
    (80, False, "filter", " [↑↓/jk] Mover [1-9] Ir para [Enter] Confirmar"),
    (60, False, "filter", " [↑↓/jk] Mover [1-9] Ir para [Enter] Confirmar"),
    (45, False, "filter", " [↑↓/jk] Mover [Enter] OK"),
    (30, False, "filter", " [↑↓/jk] Mover [Enter] OK"),
]

LEGENDA_COMPACTA = [
    (
        40,
        True,
        "album",
        [
            "↑↓ mover · Espaço marcar · t todos",
            "a A-Z · y ano · n nova busca",
            "b artista · m menu · ↵ confirmar",
        ],
    ),
    (
        60,
        True,
        "album",
        [
            "↑↓ mover · Espaço marcar · t todos · a A-Z · y ano",
            "n nova busca · b artista · m menu · ↵ confirmar",
        ],
    ),
    (
        40,
        False,
        "album",
        [
            "↑↓ mover · a A-Z · y ano",
            "n nova busca · b artista · m menu",
            "↵ confirmar",
        ],
    ),
    (
        60,
        False,
        "album",
        ["↑↓ mover · a A-Z · y ano · n nova busca · b artista", "m menu · ↵ confirmar"],
    ),
    (
        40,
        True,
        "track",
        [
            "↑↓ mover · Espaço marcar · t todos",
            "a A-Z · y ano · n nova busca",
            "b artista · m menu · ↵ confirmar",
        ],
    ),
    (
        60,
        True,
        "track",
        [
            "↑↓ mover · Espaço marcar · t todos · a A-Z · y ano",
            "n nova busca · b artista · m menu · ↵ confirmar",
        ],
    ),
    (
        40,
        False,
        "track",
        [
            "↑↓ mover · a A-Z · y ano",
            "n nova busca · b artista · m menu",
            "↵ confirmar",
        ],
    ),
    (
        60,
        False,
        "track",
        ["↑↓ mover · a A-Z · y ano · n nova busca · b artista", "m menu · ↵ confirmar"],
    ),
    (
        40,
        True,
        "playlist",
        [
            "↑↓ mover · Espaço marcar · t todos",
            "a A-Z · y ano · n nova busca · m menu",
            "↵ confirmar",
        ],
    ),
    (
        60,
        True,
        "playlist",
        [
            "↑↓ mover · Espaço marcar · t todos · a A-Z · y ano",
            "n nova busca · m menu · ↵ confirmar",
        ],
    ),
    (
        40,
        False,
        "playlist",
        ["↑↓ mover · a A-Z · y ano", "n nova busca · m menu · ↵ confirmar"],
    ),
    (
        60,
        False,
        "playlist",
        ["↑↓ mover · a A-Z · y ano · n nova busca · m menu", "↵ confirmar"],
    ),
    (
        40,
        True,
        "artist",
        [
            "↑↓ mover · Espaço marcar · t todos",
            "a A-Z · y ano · n nova busca · m menu",
            "↵ abrir",
        ],
    ),
    (
        60,
        True,
        "artist",
        [
            "↑↓ mover · Espaço marcar · t todos · a A-Z · y ano",
            "n nova busca · m menu · ↵ abrir",
        ],
    ),
    (
        40,
        False,
        "artist",
        ["↑↓ mover · a A-Z · y ano", "n nova busca · m menu · ↵ abrir"],
    ),
    (
        60,
        False,
        "artist",
        ["↑↓ mover · a A-Z · y ano · n nova busca · m menu · ↵ abrir"],
    ),
    (
        40,
        True,
        "filter",
        ["↑↓ mover · Espaço marcar · t todos", "1-9 ir · ↵ confirmar"],
    ),
    (60, True, "filter", ["↑↓ mover · Espaço marcar · t todos · 1-9 ir · ↵ confirmar"]),
    (40, False, "filter", ["↑↓ mover · ↵ confirmar"]),
    (60, False, "filter", ["↑↓ mover · ↵ confirmar"]),
]


def _ordenavel(categoria):
    # Mesma regra do `_tui_select`: só o menu simples ("filter") não ordena.
    return categoria != "filter"


@pytest.mark.parametrize(("colunas", "multi", "categoria", "esperado"), LINHA_UNICA)
def test_linha_unica_de_atalhos(colunas, multi, categoria, esperado):
    obtido = core._linha_unica_de_atalhos(
        colunas,
        is_multi=multi,
        sortable=_ordenavel(categoria),
        item_category=categoria,
    )
    assert obtido == esperado
    assert core.get_cwidth(obtido) <= colunas


@pytest.mark.parametrize(
    ("colunas", "multi", "categoria", "esperado"), LEGENDA_COMPACTA
)
def test_legenda_compacta_de_atalhos(colunas, multi, categoria, esperado):
    obtido = core._linhas_de_atalhos_compactos(
        colunas,
        is_multi=multi,
        sortable=_ordenavel(categoria),
        item_category=categoria,
    )
    assert obtido == esperado
    assert all(core.get_cwidth(linha) <= colunas - 2 for linha in obtido)


@pytest.mark.parametrize(
    ("categoria", "esperado"),
    [
        ("filter", ""),
        ("album", " [n] Nova [b] Artista [m] Menu"),
        ("track", " [n] Nova [b] Artista [m] Menu"),
        ("playlist", " [n] Nova [m] Menu"),
        ("artist", " [n] Nova [m] Menu"),
    ],
)
def test_navegacao_de_atalhos(categoria, esperado):
    assert core._navegacao_de_atalhos(categoria) == esperado
