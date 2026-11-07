"""`password` e `token` são conceitos separados no config.ini.

Versões antigas gravavam o token de sessão no campo `password`. Aqui se confere
que (1) uma senha de verdade continua sendo senha, (2) um token que está no campo
errado é reconhecido, migrado para o lugar certo e nunca vai para `cfg.password`.
"""

import configparser

import pytest

from qobuz_dl import cli

pytestmark = pytest.mark.unit

TOKEN = "T" * 80
SENHA = "minha-senha"


class _KeyringFalso:
    def __init__(self):
        self.dados = {}

    def set_password(self, servico, chave, valor):
        self.dados[(servico, chave)] = valor

    def get_password(self, servico, chave):
        return self.dados.get((servico, chave))


@pytest.fixture
def ambiente(monkeypatch, tmp_path):
    k = _KeyringFalso()
    monkeypatch.setattr(cli, "keyring", k)
    monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
    return k


def _config(**extra) -> configparser.ConfigParser:
    config = configparser.ConfigParser(interpolation=None)
    config.add_section("qobuz")
    base = {
        "email": "a@b.c",
        "default_limit": "20",
        "default_quality": "6",
        "app_id": "123",
        "secrets": "s1,s2",
    }
    base.update(extra)
    for chave, valor in base.items():
        config.set("qobuz", chave, valor)
    return config


class TestSeparar:
    def test_senha_curta_e_senha(self):
        assert cli._separar_senha_e_token(SENHA) == (SENHA, "")

    def test_valor_longo_e_token(self):
        assert cli._separar_senha_e_token(TOKEN) == ("", TOKEN)

    def test_limite_exato(self):
        assert cli._separar_senha_e_token("x" * 60) == ("x" * 60, "")
        assert cli._separar_senha_e_token("x" * 61) == ("", "x" * 61)

    def test_vazio_e_none(self):
        assert cli._separar_senha_e_token("") == ("", "")
        assert cli._separar_senha_e_token(None) == ("", "")  # type: ignore[arg-type]

    def test_espacos_sao_aparados(self):
        assert cli._separar_senha_e_token(f"  {SENHA}  ") == (SENHA, "")


class TestCarregar:
    def test_senha_de_verdade_nao_vira_token(self, ambiente):
        cfg = cli._load_cli_config(_config(password=SENHA, disable_keyring="true"), 0.0)
        assert cfg.password == SENHA
        assert cfg.token == ""

    def test_token_no_campo_password_e_migrado_para_auth_token(self, ambiente):
        config = _config(password=TOKEN, disable_keyring="true")
        cfg = cli._load_cli_config(config, 0.0)
        assert cfg.token == TOKEN
        assert cfg.password == ""
        assert config.get("qobuz", "password") == ""
        assert config.get("qobuz", "auth_token") == TOKEN

    def test_migracao_e_gravada_no_arquivo(self, ambiente, tmp_path):
        cli._load_cli_config(_config(password=TOKEN, disable_keyring="true"), 0.0)
        gravado = configparser.ConfigParser(interpolation=None)
        gravado.read(tmp_path / "config.ini", encoding="utf-8")
        assert gravado.get("qobuz", "password") == ""
        assert gravado.get("qobuz", "auth_token") == TOKEN

    def test_com_keyring_o_token_vai_para_o_keyring(self, ambiente):
        config = _config(password=TOKEN)
        cfg = cli._load_cli_config(config, 0.0)
        assert ambiente.dados[(cli.KEYRING_SERVICE, "auth_token")] == TOKEN
        assert cfg.token == TOKEN
        assert cfg.password == ""
        assert config.get("qobuz", "auth_token") == ""
        assert config.get("qobuz", "password") == ""

    def test_token_nunca_e_copiado_para_a_senha(self, ambiente):
        ambiente.dados[(cli.KEYRING_SERVICE, "auth_token")] = TOKEN
        cfg = cli._load_cli_config(_config(password=SENHA), 0.0)
        assert cfg.token == TOKEN
        assert cfg.password == SENHA

    def test_com_token_proprio_o_do_campo_password_nao_e_apagado(self, ambiente):
        config = _config(password="P" * 80, auth_token="Q" * 80, disable_keyring="true")
        cfg = cli._load_cli_config(config, 0.0)
        assert cfg.token == "Q" * 80
        assert cfg.password == ""
        # Nada foi migrado: o valor antigo continua lá para o usuário decidir.
        assert config.get("qobuz", "password") == "P" * 80

    def test_sem_nenhum_dos_dois(self, ambiente):
        cfg = cli._load_cli_config(_config(disable_keyring="true"), 0.0)
        assert (cfg.password, cfg.token) == ("", "")

    def test_arquivo_continua_privado_apos_migrar(self, ambiente, tmp_path):
        import os
        import stat

        if os.name != "posix":
            pytest.skip("permissões POSIX")
        cli._load_cli_config(_config(password=TOKEN, disable_keyring="true"), 0.0)
        modo = stat.S_IMODE(os.stat(tmp_path / "config.ini").st_mode)
        assert modo == 0o600


class TestLeituraDeTodosOsLugares:
    def test_senha_curta_nao_e_tratada_como_token(self, ambiente):
        config = _config(password=SENHA)
        assert cli._load_token_from_all_locations(config) == ""

    def test_token_no_campo_password_ainda_e_lido(self, ambiente):
        config = _config(password=TOKEN)
        assert cli._load_token_from_all_locations(config) == TOKEN
