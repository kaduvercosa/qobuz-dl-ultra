"""Recuperação OAuth no assistente inicial e na autenticação do downloader."""

import configparser
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qobuz_dl import cli
from qobuz_dl.exceptions import AuthenticationError

pytestmark = pytest.mark.unit


async def test_initialize_renova_oauth_ao_receber_token_rejeitado(
    tmp_path, monkeypatch
):
    config_file = tmp_path / "config.ini"
    config = configparser.ConfigParser(interpolation=None)
    config["qobuz"] = {
        "auth_method": "oauth",
        "email": "conta@example.com",
        "password": "",
        "app_id": "304027809",
        "secrets": "oauth-secret",
        "user_auth_token": "token-novo",
        "native_lang": "false",
        "directory": "Minha Biblioteca",
        "fetch_lyrics": "true",
    }
    with config_file.open("w", encoding="utf-8") as handle:
        config.write(handle)

    qobuz = SimpleNamespace(
        settings=SimpleNamespace(user_auth_token="token-antigo"),
        force_english=True,
        initialize_client=AsyncMock(
            side_effect=[AuthenticationError("token expirado"), None]
        ),
    )
    renovar = AsyncMock(return_value=True)
    monkeypatch.setattr(cli, "_oauth_auth_command", renovar)
    monkeypatch.setattr(cli, "_keyring_load", lambda _key: None)

    await cli._initialize_client_with_auth_recovery(
        qobuz,
        str(config_file),
        "conta@example.com",
        "",
        "304027809",
        ["oauth-secret"],
        "token-antigo",
        True,
    )

    # A recuperação pede confirmação antes de abrir o navegador (confirm=True).
    renovar.assert_awaited_once_with(str(config_file), confirm=True)
    assert qobuz.settings.user_auth_token == "token-novo"
    assert qobuz.initialize_client.await_count == 2
    assert qobuz.initialize_client.await_args.args == (
        "conta@example.com",
        "",
        "304027809",
        ["oauth-secret"],
    )

    saved = configparser.ConfigParser(interpolation=None)
    saved.read(config_file, encoding="utf-8")
    assert saved.get("qobuz", "directory") == "Minha Biblioteca"
    assert saved.getboolean("qobuz", "fetch_lyrics") is True


async def test_initialize_nao_renova_em_falha_nao_autenticacao(tmp_path):
    config_file = tmp_path / "config.ini"
    config_file.write_text("[qobuz]\nauth_method = oauth\n", encoding="utf-8")
    qobuz = SimpleNamespace(
        settings=SimpleNamespace(user_auth_token="token"),
        force_english=True,
        initialize_client=AsyncMock(side_effect=TimeoutError("rede indisponível")),
    )
    with pytest.raises(TimeoutError):
        await cli._initialize_client_with_auth_recovery(
            qobuz, str(config_file), "e", "", "id", ["secret"], "token", True
        )
    assert qobuz.initialize_client.await_count == 1


async def test_reset_oauth_continua_com_as_opcoes_originais(tmp_path, monkeypatch):
    creds = {
        "email": "conta@example.com",
        "user_auth_token": "oauth-token",
        "app_id": "304027809",
        "app_secret": "oauth-secret",
    }
    profile = {"language_code": "pt"}
    monkeypatch.setattr(
        cli, "_obter_oauth_validado", AsyncMock(return_value=(creds, profile))
    )
    monkeypatch.setattr(cli, "_pick_accent_color", lambda: "")
    monkeypatch.setattr(cli.ui, "width", lambda: 80)
    monkeypatch.setattr(cli.ui, "c", lambda value: value)
    monkeypatch.setattr(cli, "_keyring_save", lambda *_args: False)
    monkeypatch.setattr(cli, "ensure_directory_ready", lambda *_args: None)
    monkeypatch.setattr(cli, "_print_welcome_screen", lambda: None)
    monkeypatch.setattr(cli.asyncio, "sleep", AsyncMock())
    respostas = iter(["yes", "n", "", "", ""])
    monkeypatch.setattr("builtins.input", lambda *_args: next(respostas))

    config_file = tmp_path / "config.ini"
    await cli._reset_config(str(config_file))

    saved = configparser.ConfigParser(interpolation=None)
    saved.read(config_file, encoding="utf-8")
    assert saved.get("qobuz", "auth_method") == "oauth"
    assert saved.get("qobuz", "app_id") == "304027809"
    assert saved.get("qobuz", "secrets") == "oauth-secret"
    assert saved.get("qobuz", "auth_token") == "oauth-token"
    assert saved.get("qobuz", "directory") == "Qobuz Downloads"
    assert saved.get("qobuz", "default_quality") == "27"
    assert saved.getboolean("qobuz", "fetch_lyrics") is False
    assert saved.getboolean("qobuz", "fetch_translation") is False
