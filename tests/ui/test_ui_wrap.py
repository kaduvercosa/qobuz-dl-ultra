"""Testes de quebra de linha das mensagens da UI.

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
`ui._tagged()` montava a linha inteira num f-string, sem quebra. As funções
mais usadas do programa (`ok`, `warn`, `error`, `step`, `skip`) estouravam a
largura do terminal, enquanto `detail()` e `wrapped()` já quebravam -- uma
inconsistência que passou despercebida por muito tempo.

Foi o teste de largura que expôs o caso concreto: `stats` num banco vazio
imprimia "[!] Nenhum dado encontrado. Comece a baixar para popular as
estatisticas." com 73 caracteres, estourando qualquer terminal de 72 colunas
ou menos.

Detalhe importante de medição: conta CARACTERES, não bytes. Os acentos do
português ocupam 2 bytes em UTF-8 e os glifos da UI ocupam 3, então medir bytes
dá falso positivo -- foi exatamente o erro que apareceu ao conferir esta
correção pela primeira vez.
"""

import re

import pytest

from qobuz_dl import ui

ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~])")

MENSAGEM_REAL = "Nenhum dado encontrado. Comece a baixar para popular as estatisticas."

MENSAGENS = [
    MENSAGEM_REAL,
    ("Album baixado com sucesso: Nujabes - Metaphorical Music (2003, FLAC 24/96)"),
    "Falha ao autenticar no Qobuz: verifique email e senha no config.ini",
    "curta",
    "palavra-unica-muito-longa-sem-nenhum-espaço-para-quebrar-em-lugar-algum",
]

FUNCOES = ["ok", "warn", "error", "step", "skip"]


def _capturar(funcao, mensagem, colunas, monkeypatch, capsys):
    """Executa uma função da UI e retorna linhas sem códigos ANSI."""
    monkeypatch.setenv("COLUMNS", str(colunas))
    ui.configure(color=False)

    getattr(ui, funcao)(mensagem)

    captured = capsys.readouterr()
    texto = captured.out + captured.err

    return [ANSI.sub("", linha) for linha in texto.splitlines()]


@pytest.mark.parametrize("colunas", [120, 80, 72, 60, 50, 40, 32])
@pytest.mark.parametrize("funcao", FUNCOES)
@pytest.mark.parametrize("mensagem", MENSAGENS, ids=lambda valor: valor[:18])
def test_nenhuma_linha_estoura(
    funcao,
    mensagem,
    colunas,
    monkeypatch,
    capsys,
):
    """Nenhuma linha deve ultrapassar a largura configurada."""

    linhas = _capturar(
        funcao,
        mensagem,
        colunas,
        monkeypatch,
        capsys,
    )

    estouros = [
        f"{len(linha)} de {colunas}: {linha!r}"
        for linha in linhas
        if len(linha) > colunas
    ]

    assert not estouros, "\n".join(estouros)


@pytest.mark.parametrize("funcao", FUNCOES)
def test_a_tag_aparece_uma_vez_so(funcao, monkeypatch, capsys):
    """A tag deve aparecer somente na primeira linha."""
    linhas = _capturar(
        funcao,
        MENSAGEM_REAL,
        40,
        monkeypatch,
        capsys,
    )

    com_tag = [linha for linha in linhas if re.match(r"^\[[+!*~-]\]", linha)]

    assert len(com_tag) == 1, f"tag apareceu {len(com_tag)} vezes"
    assert linhas[0] == com_tag[0], "a tag não está na primeira linha"


@pytest.mark.parametrize("funcao", FUNCOES)
def test_continuacao_alinha_sob_o_texto(funcao, monkeypatch, capsys):
    """As continuações devem usar exatamente quatro espaços."""
    linhas = _capturar(
        funcao,
        MENSAGEM_REAL,
        40,
        monkeypatch,
        capsys,
    )

    assert len(linhas) > 1, "a mensagem deveria ter sido quebrada em 40 colunas"

    for linha in linhas[1:]:
        assert linha.startswith("    "), f"continuação sem recuo: {linha!r}"
        assert not linha.startswith("     "), f"recuo maior que 4: {linha!r}"


@pytest.mark.parametrize("funcao", FUNCOES)
def test_nenhuma_palavra_e_cortada_ao_meio(funcao, monkeypatch, capsys):
    """A quebra deve preservar as palavras da mensagem original."""
    linhas = _capturar(
        funcao,
        MENSAGEM_REAL,
        32,
        monkeypatch,
        capsys,
    )

    primeira = re.sub(r"^\[[+!*~-]\]\s*", "", linhas[0])
    remontada = " ".join([primeira] + [linha.strip() for linha in linhas[1:]])

    assert remontada.split() == MENSAGEM_REAL.split(), (
        f"palavras não batem:\n"
        f"original: {MENSAGEM_REAL.split()!r}\n"
        f"remontada: {remontada.split()!r}"
    )


def test_mensagem_curta_nao_ganha_linha_extra(monkeypatch, capsys):
    """Uma mensagem curta deve continuar ocupando uma linha."""
    linhas = _capturar(
        "warn",
        "curta",
        80,
        monkeypatch,
        capsys,
    )

    assert len(linhas) == 1, f"esperava 1 linha, obteve {len(linhas)}"


@pytest.mark.parametrize("funcao", FUNCOES)
def test_primeira_linha_tem_tag(funcao, monkeypatch, capsys):
    """A primeira linha deve sempre começar com uma tag."""
    linhas = _capturar(
        funcao,
        MENSAGEM_REAL,
        80,
        monkeypatch,
        capsys,
    )

    assert linhas, "nenhuma linha foi emitida"
    assert re.match(r"^\[[+!*~-]\] ", linhas[0]), (
        f"primeira linha não começa com tag: {linhas[0]!r}"
    )


def test_mensagem_vazia_nao_estoura(monkeypatch, capsys):
    """Mensagem vazia não deve quebrar a UI."""
    linhas = _capturar(
        "warn",
        "",
        32,
        monkeypatch,
        capsys,
    )

    assert len(linhas) >= 1, "nenhuma linha foi emitida para mensagem vazia"
    assert all(len(linha) <= 32 for linha in linhas)


@pytest.mark.parametrize("colunas", [80, 40, 32])
def test_terminal_muito_estreito(colunas, monkeypatch, capsys):
    """Mesmo em terminais estreitos, a UI não deve estourar."""
    linhas = _capturar(
        "warn",
        MENSAGEM_REAL,
        colunas,
        monkeypatch,
        capsys,
    )

    assert linhas, "nenhuma linha emitida em terminal estreito"
    assert all(len(linha) <= colunas for linha in linhas)
