"""Testa a quebra de linha das mensagens com tag ([+], [!], [*], [-]).

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
`ui._tagged()` montava a linha inteira num f-string, sem quebra. As funcoes
mais usadas do programa (`ok`, `warn`, `error`, `step`, `skip`) estouravam a
largura do terminal, enquanto `detail()` e `wrapped()` ja' quebravam -- uma
inconsistencia que passou despercebida por muito tempo.

Foi o teste de largura que expos o caso concreto: `stats` num banco vazio
imprimia "[!] Nenhum dado encontrado. Comece a baixar para popular as
estatisticas." com 73 caracteres, estourando qualquer terminal de 72 colunas
ou menos.

Detalhe importante de medicao: conta CARACTERES, nao bytes. Os acentos do
portugues ocupam 2 bytes em UTF-8 e os glifos da UI ocupam 3, entao medir bytes
da' falso positivo -- foi exatamente o erro que apareceu ao conferir esta
correcao pela primeira vez.
"""

import io
import re
import sys

import pytest

from qobuz_dl import ui

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# Mensagem real do programa que causou a descoberta do bug.
MENSAGEM_REAL = (
    "Nenhum dado encontrado. Comece a baixar para popular as estatísticas."
)

MENSAGENS = [
    MENSAGEM_REAL,
    "Álbum baixado com sucesso: Nujabes - Metaphorical Music (2003, FLAC 24/96)",
    "Falha ao autenticar no Qobuz: verifique email e senha no config.ini",
    "curta",
    "palavra-única-muito-longa-sem-nenhum-espaço-para-quebrar-em-lugar-algum",
]

FUNCOES = ["ok", "warn", "error", "step", "skip"]


def _capturar(funcao, mensagem, colunas, monkeypatch):
    """Roda uma funcao da UI e devolve as linhas sem codigo de cor."""
    monkeypatch.setenv("COLUMNS", str(colunas))
    ui.configure(color=False)

    buf = io.StringIO()
    real_out, real_err = sys.stdout, sys.stderr
    sys.stdout = sys.stderr = buf
    try:
        getattr(ui, funcao)(mensagem)
    finally:
        sys.stdout, sys.stderr = real_out, real_err

    return [ANSI.sub("", linha) for linha in buf.getvalue().splitlines()]


@pytest.mark.parametrize("colunas", [120, 80, 72, 60, 50, 40, 32])
@pytest.mark.parametrize("funcao", FUNCOES)
@pytest.mark.parametrize("mensagem", MENSAGENS, ids=lambda m: m[:18])
def test_nenhuma_linha_estoura(funcao, mensagem, colunas, monkeypatch):
    linhas = _capturar(funcao, mensagem, colunas, monkeypatch)

    estouros = [
        f"{len(linha)} de {colunas}: {linha!r}" for linha in linhas if len(linha) > colunas
    ]
    # A palavra unica gigante nao pode ser quebrada sem hifenizar; o textwrap
    # a deixa passar de proposito, e forcar corte no meio da palavra seria pior.
    if "palavra-única" in mensagem:
        pytest.skip("palavra unica maior que a largura nao tem onde quebrar")

    assert not estouros, "\n".join(estouros)


@pytest.mark.parametrize("funcao", FUNCOES)
def test_a_tag_aparece_uma_vez_so(funcao, monkeypatch):
    """A tag pertence a PRIMEIRA linha. Repeti-la em cada linha de continuacao
    faria a mensagem parecer varias mensagens diferentes."""
    linhas = _capturar(funcao, MENSAGEM_REAL, 40, monkeypatch)

    com_tag = [linha for linha in linhas if re.match(r"^\[[+!*-]\]", linha)]
    assert len(com_tag) == 1
    assert linhas[0] is com_tag[0] or linhas[0] == com_tag[0]


@pytest.mark.parametrize("funcao", FUNCOES)
def test_continuacao_alinha_sob_o_texto(funcao, monkeypatch):
    """As linhas seguintes recuam 4 espacos -- o tamanho de "[!] " -- para que
    o texto fique alinhado sob o texto, nao sob a tag."""
    linhas = _capturar(funcao, MENSAGEM_REAL, 40, monkeypatch)

    assert len(linhas) > 1, "a mensagem deveria ter sido quebrada em 40 colunas"
    for linha in linhas[1:]:
        assert linha.startswith("    "), repr(linha)
        assert not linha.startswith("     "), f"recuo maior que 4: {linha!r}"


@pytest.mark.parametrize("funcao", FUNCOES)
def test_nenhuma_palavra_e_cortada_ao_meio(funcao, monkeypatch):
    """A regressao visual que motivou tudo: texto partido no meio da palavra.

    Reconstroi a mensagem a partir das linhas emitidas e compara com a
    original. Se a quebra tivesse cortado uma palavra, as palavras nao
    baterinam.
    """
    linhas = _capturar(funcao, MENSAGEM_REAL, 32, monkeypatch)

    primeira = re.sub(r"^\[[+!*-]\]\s*", "", linhas[0])
    remontada = " ".join([primeira] + [linha.strip() for linha in linhas[1:]])

    assert remontada.split() == MENSAGEM_REAL.split()


def test_mensagem_curta_nao_ganha_linha_extra(monkeypatch):
    """Controle de sanidade: a quebra nao pode inventar linhas onde nao
    precisa."""
    linhas = _capturar("warn", "curta", 80, monkeypatch)
    assert len(linhas) == 1
