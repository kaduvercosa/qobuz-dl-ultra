"""Guarda de estilo: como o código trata exceções amplas.

Resultado da revisão dos `except` amplos do pacote. Duas regras que o código já
cumpre e que não devem voltar atrás:

1. Nada de `except:` puro.
2. `except BaseException` só para limpar e repropagar (tem de conter `raise`);
   engolir Ctrl+C ou cancelamento de tarefa trava o programa.

(O `except Exception` que só ignora e precisa de comentário é guardado por
`test_excecoes_silenciosas.py`.)
"""

import ast
import pathlib

import pytest

pytestmark = pytest.mark.unit

PACOTE = pathlib.Path(__file__).resolve().parents[2] / "qobuz_dl"


def _handlers():
    for arquivo in sorted(PACOTE.glob("*.py")):
        texto = arquivo.read_text(encoding="utf-8")
        linhas = texto.splitlines()
        for no in ast.walk(ast.parse(texto)):
            if isinstance(no, ast.ExceptHandler):
                trecho = "\n".join(linhas[no.lineno - 1 : no.end_lineno])
                yield arquivo.name, no, trecho


def _nomes(no):
    if no.type is None:
        return {"<puro>"}
    elementos = no.type.elts if isinstance(no.type, ast.Tuple) else [no.type]
    return {getattr(e, "id", getattr(e, "attr", "?")) for e in elementos}


def test_nao_ha_except_puro():
    achados = [f"{a}:{n.lineno}" for a, n, _ in _handlers() if "<puro>" in _nomes(n)]
    assert not achados, f"use um tipo de exceção: {achados}"


def test_base_exception_sempre_repropaga():
    achados = [
        f"{a}:{n.lineno}"
        for a, n, _ in _handlers()
        if "BaseException" in _nomes(n)
        and not any(isinstance(x, ast.Raise) for x in ast.walk(n))
    ]
    assert not achados, f"`except BaseException` sem `raise`: {achados}"
