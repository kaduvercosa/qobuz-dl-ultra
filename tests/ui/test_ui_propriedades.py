"""Propriedades das funções de largura do `ui` (quebra, preenchimento, corte).

Em vez de exemplos escolhidos à mão, cada propriedade vale para QUALQUER texto:
mistura de ASCII, japonês, coreano, emoji, acentos combinados e quebras de
linha. Foi assim que apareceu o bug de `wrap_indented`/`labeled_lines`, que
mediam em caracteres e deixavam a linha passar da tela com emoji/CJK.

Cada propriedade roda de dois jeitos:
- com `hypothesis` (extra `dev`), que gera e reduz os casos que falham;
- com um gerador fixo (`random.Random(semente)`), que roda sempre, até onde o
  `hypothesis` não está instalado (a-Shell), e é reproduzível.
"""

import random

import pytest

from qobuz_dl import ui

pytestmark = pytest.mark.ui

ALFABETO = (
    list("abcij .-_/")
    + list("宇多田ヒカル한국어")
    + ["🎧", "🎵", "é", "́", "‍", "\n", "  "]
)
# Largura mínima realista: o prefixo de `labeled_lines` já ocupa 10 células.
COLS_MIN = 20
CASOS = 400


def _texto(rnd, maximo=150):
    return "".join(rnd.choice(ALFABETO) for _ in range(rnd.randint(0, maximo)))


# -- propriedades (recebem os dados já gerados) ------------------------------
def prop_wrap_lines_cabe_na_largura(texto, limite):
    limite = max(limite, 12)  # `_wrap_lines` tem piso de 12
    for linha in ui._wrap_lines(texto, limite):
        assert ui._cell_width(linha) <= limite, (texto, limite, linha)


def prop_wrap_nao_perde_conteudo(texto, cols, recuo):
    linhas = ui.wrap_indented(texto, recuo, cols)
    assert "".join("".join(linhas).split()) == "".join(texto.split())
    linhas = ui.labeled_lines("Letra", texto, cols)
    assert "".join("".join(linhas).split()).endswith("".join(texto.split()))


def prop_wrap_indented_cabe_na_largura(texto, cols, recuo):
    for linha in ui.wrap_indented(texto, recuo, cols):
        assert ui._cell_width(linha) <= cols - 1, (texto, cols, recuo, linha)


def prop_labeled_lines_cabe_na_largura(texto, cols):
    for linha in ui.labeled_lines("Letra", texto, cols):
        assert ui._cell_width(linha) <= cols - 1, (texto, cols, linha)


def prop_pad_cells_completa_ate_a_largura(texto, n):
    assert ui._cell_width(ui.pad_cells(texto, n)) == max(n, ui._cell_width(texto))


def prop_truncate_nunca_passa_do_limite(texto, n):
    assert ui._cell_width(ui.truncate(texto, n)) <= n, (texto, n)


def prop_center_cells_nunca_passa_do_limite(texto, n):
    assert ui._cell_width(ui.center_cells(texto, n)) <= n, (texto, n)


# -- execução com gerador fixo (sempre roda) ---------------------------------
def test_propriedades_com_gerador_fixo():
    rnd = random.Random(20261102)
    for _ in range(CASOS):
        texto = _texto(rnd)
        cols = rnd.randint(COLS_MIN, 120)
        recuo = rnd.randint(0, 6)
        n = rnd.randint(1, 80)
        prop_wrap_lines_cabe_na_largura(texto, rnd.randint(12, 80))
        prop_wrap_nao_perde_conteudo(texto, cols, recuo)
        prop_wrap_indented_cabe_na_largura(texto, cols, recuo)
        prop_labeled_lines_cabe_na_largura(texto, cols)
        prop_pad_cells_completa_ate_a_largura(texto, n)
        prop_truncate_nunca_passa_do_limite(texto, n)
        prop_center_cells_nunca_passa_do_limite(texto, n)


def test_texto_ascii_quebra_como_o_textwrap():
    """Em ASCII o resultado é idêntico ao do `textwrap` que a função substituiu."""
    import textwrap

    rnd = random.Random(7)
    for _ in range(CASOS):
        palavras = [
            "".join(rnd.choice("abcdefgh") for _ in range(rnd.randint(1, 9)))
            for _ in range(rnd.randint(0, 30))
        ]
        texto, cols, recuo = " ".join(palavras), rnd.randint(20, 100), rnd.randint(0, 4)
        esperado = textwrap.wrap(
            texto,
            width=cols - 1,
            initial_indent=" " * recuo,
            subsequent_indent=" " * recuo,
            break_long_words=True,
            break_on_hyphens=False,
        ) or [" " * recuo]
        assert ui.wrap_indented(texto, recuo, cols) == esperado


def test_wrap_indented_japones_e_emoji_cabem():
    texto = "宇多田ヒカル " * 20 + "🎧" * 30
    for linha in ui.wrap_indented(texto, 2, 60):
        assert ui._cell_width(linha) <= 59


# -- execução com hypothesis (quando instalado) ------------------------------
try:
    import hypothesis
except ImportError:  # a-Shell e instalações sem o extra `dev`
    hypothesis = None


@pytest.mark.skipif(hypothesis is None, reason="hypothesis não instalado")
def test_hypothesis_roda_as_mesmas_propriedades():
    """Mesmas propriedades, mas com casos gerados (e reduzidos) pelo hypothesis."""
    from hypothesis import given, settings
    from hypothesis import strategies as st

    texto = st.lists(
        st.sampled_from(ALFABETO),
        min_size=0,
        max_size=150,
    ).map("".join)
    cols = st.integers(min_value=COLS_MIN, max_value=120)
    largura = st.integers(min_value=1, max_value=80)
    recuo = st.integers(min_value=0, max_value=6)
    cfg = settings(max_examples=200, deadline=None)

    @cfg
    @given(texto, st.integers(min_value=12, max_value=80))
    def wrap_lines(t, limite):
        prop_wrap_lines_cabe_na_largura(t, limite)

    @cfg
    @given(texto, cols, recuo)
    def wrap_indented(t, c, r):
        prop_wrap_indented_cabe_na_largura(t, c, r)
        prop_wrap_nao_perde_conteudo(t, c, r)

    @cfg
    @given(texto, cols)
    def labeled(t, c):
        prop_labeled_lines_cabe_na_largura(t, c)

    @cfg
    @given(texto, largura)
    def medidas(t, n):
        prop_pad_cells_completa_ate_a_largura(t, n)
        prop_truncate_nunca_passa_do_limite(t, n)
        prop_center_cells_nunca_passa_do_limite(t, n)

    for propriedade in (wrap_lines, wrap_indented, labeled, medidas):
        propriedade()
