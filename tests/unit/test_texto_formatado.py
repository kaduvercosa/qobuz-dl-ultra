"""Helper de tipagem do texto formatado do prompt_toolkit.

O QUE ESTE ARQUIVO TRAVA
------------------------
`core.como_texto_formatado` existe só para o mypy (o prompt_toolkit novo tipa o
texto como list[OneStyleAndTextTuple], invariante). Em tempo de execução ele tem de
devolver a MESMA função: se algum dia passar a embrulhar ou converter, a TUI muda
de comportamento sem ninguém perceber.
"""

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


def test_devolve_a_mesma_funcao():
    def texto():
        return [("class:x", "olá")]

    assert core.como_texto_formatado(texto) is texto
    assert core.como_texto_formatado(texto)() == [("class:x", "olá")]
