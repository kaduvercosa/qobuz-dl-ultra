"""A suíte nunca abre o terminal de verdade (segfault no a-Shell).

O `conftest.py` troca `prompt_toolkit.PromptSession` por um guarda que recusa
`PromptSession()` sem `input=`/`output=`, e deixa `library_cmd._interactive()`
falso por padrão. Estes testes garantem que o guarda existe e que não atrapalha
quem passa entrada/saída próprias.
"""

import pytest

from qobuz_dl import library_cmd as lc

pytestmark = pytest.mark.unit


def test_prompt_session_sem_entrada_e_saida_e_recusado():
    with pytest.raises(AssertionError, match="terminal real"):
        lc.PromptSession()


def test_prompt_session_com_entrada_e_saida_passa_para_a_classe_real(monkeypatch):
    # O guarda repassa `input`/`output` à classe verdadeira.
    import tests.conftest as cf

    recebidos = {}

    def real(*args, **kwargs):
        recebidos.update(kwargs)
        return "sessao"

    monkeypatch.setattr(cf, "_PromptSessionReal", real)
    assert lc.PromptSession(input="pipe", output="dummy") == "sessao"
    assert recebidos == {"input": "pipe", "output": "dummy"}


async def test_ask_sem_terminal_devolve_erro_claro_em_vez_de_travar():
    with pytest.raises(AssertionError, match="terminal real"):
        await lc._ask("Escolha: ")


def test_library_cmd_nao_e_interativo_por_padrao():
    assert lc._interactive() is False
