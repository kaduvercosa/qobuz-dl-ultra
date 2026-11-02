"""Verifica que nenhuma linha da UI estoura a largura do terminal."""

import os
import re
import sys

import pytest

from qobuz_dl import utils
from qobuz_dl.cli import main

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


def _shutil_falso(monkeypatch, ui, get_terminal_size):
    """Troca o `shutil` SÓ dentro do `ui` (o módulo, não o objeto global).

    `ui.shutil` é o mesmo módulo que o pytest usa para medir o terminal ao
    escrever a barra de progresso; trocar `ui.shutil.get_terminal_size` faz o
    próprio pytest chamar a versão falsa e, se ela levantar erro, dá
    INTERNALERROR no modo `-v`. Trocar o atributo `shutil` do `ui` isola o teste.
    """
    import types

    monkeypatch.setattr(
        ui, "shutil", types.SimpleNamespace(get_terminal_size=get_terminal_size)
    )


def _sem_tty_nem_columns(monkeypatch):
    from qobuz_dl import ui

    def _sem_terminal(*args, **kwargs):
        raise OSError("sem tty")

    monkeypatch.delenv("COLUMNS", raising=False)
    monkeypatch.setattr(ui.os, "get_terminal_size", _sem_terminal)
    return ui


def test_raw_width_usa_o_fallback_pedido(monkeypatch):
    ui = _sem_tty_nem_columns(monkeypatch)
    _shutil_falso(monkeypatch, ui, lambda fallback: os.terminal_size(fallback))
    assert ui.raw_width() == 80
    assert ui.raw_width(fallback=(100, 24)) == 100


def test_raw_width_devolve_o_fallback_pedido_se_tudo_falhar(monkeypatch):
    ui = _sem_tty_nem_columns(monkeypatch)

    def _explode(*args, **kwargs):
        raise RuntimeError("sem terminal")

    _shutil_falso(monkeypatch, ui, _explode)
    assert ui.raw_width(fallback=(100, 24)) == 100


def _sem_tty_nem_linhas(monkeypatch):
    ui = _sem_tty_nem_columns(monkeypatch)
    monkeypatch.delenv("LINES", raising=False)
    return ui


def test_raw_height_usa_lines_quando_nao_ha_tty(monkeypatch):
    ui = _sem_tty_nem_linhas(monkeypatch)
    monkeypatch.setenv("LINES", "37")
    assert ui.raw_height() == 37


@pytest.mark.parametrize("valor", ["0", "-5", "abc"])
def test_raw_height_ignora_lines_invalido(valor, monkeypatch):
    ui = _sem_tty_nem_linhas(monkeypatch)
    monkeypatch.setenv("LINES", valor)
    _shutil_falso(monkeypatch, ui, lambda fallback: os.terminal_size(fallback))
    assert ui.raw_height(fallback=(100, 31)) == 31


def test_raw_height_devolve_o_fallback_se_tudo_falhar(monkeypatch):
    ui = _sem_tty_nem_linhas(monkeypatch)

    def _explode(*args, **kwargs):
        raise RuntimeError("sem terminal")

    _shutil_falso(monkeypatch, ui, _explode)
    assert ui.raw_height(fallback=(100, 31)) == 31


def test_so_o_ui_le_o_tamanho_do_terminal():
    """Teste-guarda: largura/altura vêm de `ui.raw_width()`/`ui.raw_height()`.

    Uma tela que chama `shutil.get_terminal_size` ou `os.get_terminal_size`
    por conta própria ignora o `COLUMNS`/`LINES` e o fallback do `ui`, e nos
    testes sai com a largura errada.
    """
    import ast
    from pathlib import Path

    pacote = Path(__file__).resolve().parents[2] / "qobuz_dl"
    achados = []
    for arquivo in sorted(pacote.glob("*.py")):
        if arquivo.name == "ui.py":
            continue
        for no in ast.walk(ast.parse(arquivo.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Attribute) and no.attr == "get_terminal_size":
                achados.append(f"{arquivo.name}:{no.lineno}")
    assert not achados, "use ui.raw_width()/ui.raw_height():\n  " + "\n  ".join(achados)
