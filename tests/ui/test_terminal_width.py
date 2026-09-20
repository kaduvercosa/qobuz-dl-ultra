"""Verifica que nenhuma linha da UI estoura a largura do terminal."""

import os
import re
import sys
import pytest

from qobuz_dl.cli import main
from qobuz_dl import utils

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
LARGURAS = (120, 100, 80, 72, 60, 50, 40, 32)
COMANDOS = ([], ["stats"], ["stats", "--artistas"])


def _rodar(args, largura, dir_config, monkeypatch, capsys):
    monkeypatch.setenv("COLUMNS", str(largura))
    monkeypatch.setenv("CONFIG_DIR", str(dir_config))
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setattr(sys, "argv", ["qobuz-dl", *args])

    # Limpa estado para não vazar execuções prévias da CLI
    utils._BINARIOS_CHECADOS.clear()

    try:
        main()
    except SystemExit:
        pass

    return capsys.readouterr().out


@pytest.mark.slow
@pytest.mark.parametrize("largura", LARGURAS)
@pytest.mark.parametrize("args", COMANDOS, ids=lambda a: "-".join(a) or "tela-inicial")
def test_nenhuma_linha_estoura(largura, args, dir_config_temp, monkeypatch, capsys):
    saida = _rodar(args, largura, dir_config_temp, monkeypatch, capsys)

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
def test_config_dir_isolado_de_verdade(dir_config_temp, monkeypatch, capsys):
    monkeypatch.setenv("CONFIG_DIR", str(dir_config_temp))
    assert os.environ.get("CONFIG_DIR") == str(dir_config_temp)
    saida = _rodar(["stats"], 80, dir_config_temp, monkeypatch, capsys)
    assert saida.strip(), "o comando `stats` nao imprimiu nada -- teste inutil"
