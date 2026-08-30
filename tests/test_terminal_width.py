"""Verifica que nenhuma linha da UI estoura a largura do terminal.

Roda os comandos que nao precisam de rede em 8 larguras diferentes e falha se
qualquer linha passar do numero de colunas.

Conta CARACTERES, nao bytes -- contar bytes da' falso positivo, porque os
glifos usados na UI (as barras U+2501, os blocos U+2588, as setas, os acentos)
ocupam 3 bytes cada em UTF-8.

CONVERSAO PARA PYTEST
---------------------
Antes este arquivo era um script: rodava tudo no nivel do modulo e terminava
com `sys.exit()`. Dois problemas concretos:

  * chamado por `pytest`, o `sys.exit()` no import DERRUBAVA a coleta inteira
    com INTERNALERROR -- ou seja, nenhum teste do projeto rodava;
  * ele avisava por `print` quando o CONFIG_DIR nao estava definido e seguia
    em frente. Sem CONFIG_DIR o programa abre o wizard de primeira execucao,
    imprime outra coisa e o teste passava sem ter checado o que dizia checar.
    Falso verde.

Agora e' teste de verdade, parametrizado por largura e comando, e o CONFIG_DIR
vem do conftest.py -- sempre presente, sempre isolado.
"""

import os
import re
import subprocess
import sys

import pytest

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

LARGURAS = (120, 100, 80, 72, 60, 50, 40, 32)

# Comandos que nao tocam a rede. O `[]` e' a tela inicial.
COMANDOS = ([], ["stats"], ["stats", "--artistas"])


def _rodar(args, largura, dir_config):
    env = dict(os.environ, COLUMNS=str(largura), CONFIG_DIR=str(dir_config))
    env["NO_COLOR"] = "1"
    r = subprocess.run(
        [sys.executable, "-m", "qobuz_dl", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )
    return r.stdout


@pytest.mark.slow
@pytest.mark.parametrize("largura", LARGURAS)
@pytest.mark.parametrize("args", COMANDOS, ids=lambda a: "-".join(a) or "tela-inicial")
def test_nenhuma_linha_estoura(largura, args, dir_config_temp):
    saida = _rodar(args, largura, dir_config_temp)

    estouros = []
    for n, linha in enumerate(saida.splitlines(), 1):
        limpa = ANSI.sub("", linha)
        if len(limpa) > largura:
            estouros.append(
                f"  linha {n}: {len(limpa)} de {largura} chars -> {limpa!r}"
            )

    assert not estouros, (
        f"{len(estouros)} linha(s) estouraram em {largura} colunas "
        f"(cmd={args or ['tela inicial']}):\n" + "\n".join(estouros)
    )


@pytest.mark.slow
def test_config_dir_isolado_de_verdade(dir_config_temp):
    """Controle de sanidade do teste acima.

    Se o CONFIG_DIR nao estiver valendo, o programa cai no wizard de primeira
    execucao e imprime algo diferente do que se pretende medir -- e o teste de
    largura passaria sem ter medido nada. Este teste garante que o ambiente
    isolado esta realmente em uso.
    """
    assert os.environ.get("CONFIG_DIR") == str(dir_config_temp)
    saida = _rodar(["stats"], 80, dir_config_temp)
    assert saida.strip(), "o comando `stats` nao imprimiu nada -- teste inutil"
