"""A busca de letras do download NUNCA pode rodar dentro do `print_lock`.

Travamento visto no a-Shell com --max-workers 5: a busca de letra (rede, com
cooldown de 429 de até 60 s) rodava com o `print_lock` preso; as outras faixas
e o event loop, que imprimem pelo mesmo lock, ficavam parados, embora os
arquivos já estivessem baixados. Checagem estática no código-fonte do
downloader: simples, sem rede e sem montar o pipeline inteiro.
"""

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

DOWNLOADER = Path(__file__).resolve().parents[2] / "qobuz_dl" / "downloader.py"


def _chamadas_fetch_and_inject(tree):
    pais = {}
    for no in ast.walk(tree):
        for filho in ast.iter_child_nodes(no):
            pais[filho] = no
    for no in ast.walk(tree):
        if (
            isinstance(no, ast.Call)
            and isinstance(no.func, ast.Attribute)
            and no.func.attr == "fetch_and_inject"
        ):
            yield no, pais


def test_fetch_and_inject_nao_fica_dentro_de_with_print_lock():
    tree = ast.parse(DOWNLOADER.read_text(encoding="utf-8"))
    chamadas = list(_chamadas_fetch_and_inject(tree))
    assert chamadas, "chamada a fetch_and_inject não encontrada no downloader"

    for chamada, pais in chamadas:
        no = chamada
        while no in pais:
            no = pais[no]
            if isinstance(no, ast.With):
                for item in no.items:
                    expr = item.context_expr
                    nome = getattr(expr, "id", getattr(expr, "attr", ""))
                    assert nome != "print_lock", (
                        "fetch_and_inject está dentro de `with print_lock` "
                        f"(linha {no.lineno}): trava as outras faixas"
                    )


def test_download_pede_orcamento_de_espera_e_saida_em_bloco():
    tree = ast.parse(DOWNLOADER.read_text(encoding="utf-8"))
    for chamada, _ in _chamadas_fetch_and_inject(tree):
        nomes = {kw.arg for kw in chamada.keywords}
        assert "network_budget" in nomes
        assert "buffer_output" in nomes
