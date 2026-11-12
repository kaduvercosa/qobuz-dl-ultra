"""Colagem da URL de retorno do OAuth: limpeza e mensagens que ajudam.

O QUE ESTE ARQUIVO TRAVA
------------------------
- A URL colada no terminal (principalmente no celular) pode vir com aspas ou
  <> em volta: isso não pode virar parte do código de autorização.
- Colar a URL da PÁGINA DE LOGIN do Qobuz (a que ainda não tem o código) é o erro
  mais comum no modo --oauth-no-browser; a mensagem tem de dizer o que fazer.
- A mensagem de erro de URL (própria, sem token nem código) aparece para o
  usuário; qualquer outro erro continua mostrando só o NOME da exceção.
"""

import pytest

from qobuz_dl import cli, oauth

pytestmark = pytest.mark.unit

URL = "http://localhost:11111/callback?code_autorisation=ABC123"
PAGINA_DE_LOGIN = (
    "https://www.qobuz.com/signin/oauth?ext_app_id=304027809"
    "&redirect_url=http%253A%252F%252Flocalhost%253A11111%252Fcallback"
)


def test_limpa_aspas_sinais_e_espacos_da_colagem():
    for colado in (f"'{URL}'", f'"{URL}"', f"<{URL}>", f"  {URL}\n"):
        assert oauth.extract_code_from_url(colado) == "ABC123"


def test_aceita_url_sem_esquema_como_o_safari_mostra():
    colado = "localhost:11111/callback?code_autorisation=ABC123"
    assert oauth.extract_code_from_url(colado) == "ABC123"


def test_pagina_de_login_tem_mensagem_propria():
    with pytest.raises(oauth.OAuthURLError) as erro:
        oauth.extract_code_from_url(PAGINA_DE_LOGIN)
    texto = str(erro.value)
    assert "login" in texto.lower() and "localhost" in texto
    assert "anônima" in texto


def test_erros_de_url_continuam_sendo_value_error():
    assert issubclass(oauth.OAuthURLError, ValueError)
    for ruim in ("", "   ", "http://localhost:11111/callback", "ABC123"):
        with pytest.raises(ValueError):
            oauth.extract_code_from_url(ruim)


async def test_cli_mostra_a_mensagem_da_url(tmp_path, monkeypatch):
    async def falha(**_kw):
        raise oauth.OAuthURLError("MENSAGEM-DA-URL")

    erros, avisos = [], []
    monkeypatch.setattr(oauth, "login", falha)
    monkeypatch.setattr(cli.ui, "error", erros.append)
    monkeypatch.setattr(cli.ui, "warn", avisos.append)
    ok = await cli._oauth_auth_command(str(tmp_path / "config.ini"), no_browser=True)
    assert ok is False
    assert erros == ["MENSAGEM-DA-URL"]
    assert any("Nenhuma configuração foi alterada" in a for a in avisos)
    assert not (tmp_path / "config.ini").exists()


async def test_cli_outros_erros_mostram_so_o_nome_da_excecao(tmp_path, monkeypatch):
    async def falha(**_kw):
        raise RuntimeError("segredo-token-123")

    erros = []
    monkeypatch.setattr(oauth, "login", falha)
    monkeypatch.setattr(cli.ui, "error", erros.append)
    monkeypatch.setattr(cli.ui, "detail", lambda *_a, **_k: None)
    monkeypatch.setattr(cli.ui, "warn", lambda _m: None)
    assert await cli._oauth_auth_command(str(tmp_path / "config.ini")) is False
    assert "RuntimeError" in erros[0]
    assert "segredo-token-123" not in erros[0]
