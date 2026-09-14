"""Testa qobuz_dl/interactive_ui.py: derivação do tema visual (pt_style,
prompt_style, _hex_accent, _darker_accent) a partir da cor de destaque.

CONTEXTO -- por que este arquivo não existia
---------------------------------------------
`_shade`, `_align_text` e `_get_table_layout` (as três funções puras deste
módulo) já têm cobertura completa, testadas via o reexport em core.py --
ver tests/unit/test_core_formatting.py e test_get_table_layout.py.

O que faltava: o bloco de nível de módulo (linhas 55-61 de
interactive_ui.py) que tenta casar o escape ANSI de `color._ACCENT` com um
regex TrueColor pra derivar `_hex_accent`/`_darker_accent` da cor de
destaque real, em vez do hex fixo hardcoded. Toda a suíte roda com
NO_COLOR=1 (ver conftest.py), então `_ACCENT` sai sempre vazio e o regex
NUNCA casa -- o branch `if _match:` (o "caminho feliz", na verdade) nunca
executa sob o resto dos testes, e nenhum arquivo de teste existente
menciona `interactive_ui`.

Como interactive_ui.py importa `_ACCENT` de color.py POR VALOR (`from
qobuz_dl.color import _ACCENT`), forçar cor ligada requer recarregar os
dois módulos NESSA ordem (color primeiro, senão interactive_ui continua
com o `_ACCENT` antigo em memória).
"""

import importlib
import os
import sys

import pytest


@pytest.fixture
def recarrega_com_cor_forcada(tmp_path, monkeypatch):
    """Recarrega qobuz_dl.color (FORCE_COLOR=1 + accent_color customizado
    no config) e, em seguida, qobuz_dl.interactive_ui -- devolve os dois
    módulos recarregados.

    A limpeza escreve o ambiente "cor desligada" direto em os.environ (em
    vez de confiar na ordem de finalização entre esta fixture e o
    monkeypatch) e recarrega de novo, garantindo que o resto da suíte
    nunca herde os módulos com cor ligada, não importa a ordem de
    teardown dos fixtures.
    """
    config_dir = tmp_path / "config_cor_customizada"
    config_file = config_dir / "qobuz-dl" / "config.ini"
    config_file.parent.mkdir(parents=True)
    # accent_color deliberadamente diferente do default (95;168;211) --
    # só assim um teste que passar por acidente (ex.: caindo no `else` e
    # pegando o hex fixo de sempre) fica visível como falha.
    config_file.write_text("[qobuz]\naccent_color = 200;50;100\n", encoding="utf-8")

    monkeypatch.setenv("CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)

    for nome in ("qobuz_dl.color", "qobuz_dl.interactive_ui"):
        sys.modules.pop(nome, None)
    color = importlib.import_module("qobuz_dl.color")
    interactive_ui = importlib.import_module("qobuz_dl.interactive_ui")

    yield color, interactive_ui

    os.environ["NO_COLOR"] = "1"
    os.environ.pop("FORCE_COLOR", None)
    for nome in ("qobuz_dl.color", "qobuz_dl.interactive_ui"):
        sys.modules.pop(nome, None)
    importlib.import_module("qobuz_dl.color")
    importlib.import_module("qobuz_dl.interactive_ui")


class TestTemaComCorLigada:
    def test_hex_accent_deriva_da_cor_customizada_quando_regex_casa(
        self, recarrega_com_cor_forcada
    ):
        color, interactive_ui = recarrega_com_cor_forcada

        # Confere a premissa antes de tudo: se _ACCENT não for um escape
        # TrueColor de verdade aqui, o teste não está exercitando o
        # branch `if _match:` e qualquer assert abaixo não prova nada.
        assert color.COLOR_ON is True
        assert color._ACCENT == "\033[38;2;200;50;100m"

        # 200;50;100 -> #c83264. Comparar também com o hex fixo original
        # prova que o valor veio do regex casando com a cor customizada,
        # e não do fallback do `else` (que sempre dá "#5fa8d3").
        assert interactive_ui._hex_accent == "#c83264"
        assert interactive_ui._hex_accent != "#5fa8d3"

        # _darker_accent = 80% de cada canal (200,50,100 -> 160,40,80).
        assert interactive_ui._darker_accent == "#a02850"

    def test_cor_desligada_continua_caindo_no_hex_fixo_default(self):
        # Guarda de regressão pro comportamento atual (branch `else`):
        # sem reload nenhum, a suíte inteira roda com NO_COLOR=1, então
        # isto tem que continuar valendo o tempo todo.
        from qobuz_dl import interactive_ui

        assert interactive_ui._hex_accent == "#5fa8d3"
        assert interactive_ui._darker_accent == "#4c86a8"
