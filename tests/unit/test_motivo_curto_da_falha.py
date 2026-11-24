"""Aviso do download direto que falhou: curto e sem afirmar que é Akamai.

O texto antigo ("Bloqueio Akamai detectado (ou falha de rede: ...)") repetia o
erro das 5 tentativas inteiro e culpava a CDN sem saber: "servidor encerrou a
conexão" também acontece por rede instável.
"""

import inspect

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


def test_mostra_so_o_ultimo_motivo():
    erro = RuntimeError(
        "Falha definitiva após 5 tentativas: Falha após\n        5 tentativas. "
        "Último erro: RemoteProtocolError: Server disconnected without sending a\n"
        "        response."
    )

    assert (
        downloader._motivo_curto_da_falha(erro)
        == "RemoteProtocolError: Server disconnected without sending a response."
    )


def test_sem_ultimo_erro_usa_o_texto_e_corta_o_que_passa_do_limite():
    assert downloader._motivo_curto_da_falha(RuntimeError("timeout")) == "timeout"
    curto = downloader._motivo_curto_da_falha(ValueError("x" * 200), limite=20)
    assert len(curto) == 20 and curto.endswith("…")


def test_erro_sem_texto_mostra_o_nome_do_tipo():
    assert downloader._motivo_curto_da_falha(RuntimeError("")) == "RuntimeError"


def test_pipeline_nao_afirma_mais_que_e_bloqueio_akamai():
    fonte = inspect.getsource(downloader.Download._download_and_tag_impl)
    assert "Akamai detectado" not in fonte
    assert "_motivo_curto_da_falha(e)" in fonte
