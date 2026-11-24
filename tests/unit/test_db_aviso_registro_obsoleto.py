"""Aviso de registro obsoleto removido do banco (db.handle_download_id).

O aviso mostrava o caminho completo do arquivo que sumiu e estourava a tela
do celular (a-Shell, ~40 colunas). Agora mostra só o nome do arquivo/pasta.
"""

import logging

import pytest

from qobuz_dl.db import (
    _nome_curto_do_caminho,
    create_db,
    handle_download_id,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "caminho, esperado",
    [
        ("/a/b/c/Faixa.flac", "Faixa.flac"),
        ("/a/b/c/Album (2020)/", "Album (2020)"),
        ("C:\\Musica\\Artista\\Faixa.mp3", "Faixa.mp3"),
        ("", "sem caminho"),
        (None, "sem caminho"),
    ],
)
def test_nome_curto_do_caminho(caminho, esperado):
    assert _nome_curto_do_caminho(caminho) == esperado


def test_nome_curto_corta_nome_muito_longo():
    nome = _nome_curto_do_caminho("/x/" + "a" * 100, limite=20)
    assert len(nome) == 20
    assert nome.endswith("…")


async def test_aviso_de_registro_obsoleto_nao_traz_o_caminho_todo(tmp_path, caplog):
    banco = str(tmp_path / "downloads.db")
    create_db(banco)
    sumiu = tmp_path / "pasta" / "muito" / "funda" / "Faixa.flac"
    await handle_download_id(
        banco, "123", add_id=True, quality=27, saved_path=str(sumiu)
    )

    with caplog.at_level(logging.WARNING, logger="qobuz_dl.db"):
        resultado = await handle_download_id(banco, "123", add_id=False, quality=27)

    assert resultado is None
    mensagens = [r.getMessage() for r in caplog.records if "obsoleto" in r.getMessage()]
    assert len(mensagens) == 1
    assert "Faixa.flac" in mensagens[0]
    assert str(tmp_path) not in mensagens[0]
    assert "funda" not in mensagens[0]
