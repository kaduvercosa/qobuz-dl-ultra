"""Testa os helpers de `qobuz_dl/cli.py` que não precisam de rede nem de login.

`test_cli.py` cobre assinatura/autenticação; aqui entram as peças que o
`.coverage` mostrava sem execução: credenciais (keyring + config.ini),
validação dos padrões de nome, saída formatada, logo e as listas de
subcomandos/flags usadas na tela de ajuda.
"""

import configparser
import sys
from datetime import datetime

import pytest

from qobuz_dl import cli
from qobuz_dl.commands import qobuz_dl_args

pytestmark = pytest.mark.unit


@pytest.fixture
def saida(monkeypatch):
    """Grava o que `ui` imprimiria numa lista (mesma técnica de test_cli.py:
    capsys + tqdm.write se comportam de forma diferente conforme o ambiente)."""
    linhas = []

    def _grava(*args, **_kwargs):
        linhas.append(str(args[0]) if args else "")

    for nome in ("emit", "emit_always", "ok", "warn", "error", "detail"):
        monkeypatch.setattr(cli.ui, nome, _grava)
    monkeypatch.setattr(cli.ui, "blank", lambda *a, **k: linhas.append(""))
    return linhas


# ---------------------------------------------------------------------------
# Keyring
# ---------------------------------------------------------------------------
class _KeyringFalso:
    def __init__(self, falha=False):
        self.dados = {}
        self.falha = falha

    def set_password(self, servico, chave, valor):
        if self.falha:
            raise RuntimeError("sem backend")
        self.dados[(servico, chave)] = valor

    def get_password(self, servico, chave):
        if self.falha:
            raise RuntimeError("sem backend")
        return self.dados.get((servico, chave))


@pytest.fixture
def keyring_falso(monkeypatch):
    k = _KeyringFalso()
    monkeypatch.setattr(cli, "keyring", k)
    return k


class TestKeyring:
    def test_salva_e_le(self, keyring_falso):
        assert cli._keyring_save("auth_token", "abc") is True
        assert keyring_falso.dados == {(cli.KEYRING_SERVICE, "auth_token"): "abc"}
        assert cli._keyring_load("auth_token") == "abc"

    def test_valor_vazio_nao_e_salvo(self, keyring_falso):
        assert cli._keyring_save("auth_token", "") is False
        assert cli._keyring_save("auth_token", None) is False
        assert keyring_falso.dados == {}

    def test_chave_inexistente(self, keyring_falso):
        assert cli._keyring_load("nada") is None

    def test_backend_quebrado_nao_propaga(self, monkeypatch):
        monkeypatch.setattr(cli, "keyring", _KeyringFalso(falha=True))
        assert cli._keyring_save("k", "v") is False
        assert cli._keyring_load("k") is None


# ---------------------------------------------------------------------------
# Token no config.ini
# ---------------------------------------------------------------------------
def _config(**qobuz):
    config = configparser.ConfigParser()
    config["qobuz"] = {k: str(v) for k, v in qobuz.items()}
    return config


class TestTokenNoConfig:
    def test_grava_nas_duas_chaves(self):
        config = _config()
        cli._save_token_in_both_config_keys(config, "  tok  ")
        assert config["qobuz"]["user_token"] == "tok"
        assert config["qobuz"]["user_auth_token"] == "tok"

    @pytest.mark.parametrize("token", ["", "   ", None])
    def test_token_vazio_nao_grava(self, token):
        config = _config()
        cli._save_token_in_both_config_keys(config, token)
        assert "user_token" not in config["qobuz"]

    def test_sem_secao_qobuz_usa_default(self):
        config = configparser.ConfigParser()
        cli._save_token_in_both_config_keys(config, "tok")
        assert config.defaults()["user_token"] == "tok"

    def test_ordem_de_prioridade_na_leitura(self, keyring_falso):
        config = _config(
            user_auth_token="B", user_token="C", auth_token="D", password="E"
        )
        # Keyring vence tudo...
        keyring_falso.dados[(cli.KEYRING_SERVICE, "auth_token")] = "A"
        assert cli._load_token_from_all_locations(config) == "A"
        # ...depois user_auth_token, user_token, auth_token e password.
        keyring_falso.dados.clear()
        assert cli._load_token_from_all_locations(config) == "B"
        config.remove_option("qobuz", "user_auth_token")
        assert cli._load_token_from_all_locations(config) == "C"
        config.remove_option("qobuz", "user_token")
        assert cli._load_token_from_all_locations(config) == "D"
        config.remove_option("qobuz", "auth_token")
        assert cli._load_token_from_all_locations(config) == "E"
        config.remove_option("qobuz", "password")
        assert cli._load_token_from_all_locations(config) == ""

    def test_disable_keyring_ignora_o_keyring(self, keyring_falso):
        keyring_falso.dados[(cli.KEYRING_SERVICE, "auth_token")] = "do-keyring"
        config = _config(disable_keyring="true", user_token="do-ini")
        assert cli._load_token_from_all_locations(config) == "do-ini"

    def test_espacos_sao_aparados_e_vazios_pulados(self, keyring_falso):
        config = _config(user_auth_token="   ", user_token="  tok  ")
        assert cli._load_token_from_all_locations(config) == "tok"


# ---------------------------------------------------------------------------
# validate_config_formats
# ---------------------------------------------------------------------------
class TestValidateConfigFormats:
    def test_padroes_validos_passam_em_silencio(self, saida):
        cli.validate_config_formats(
            {
                "folder_format": "{artist} - {album} ({year}) [{format}]",
                "track_format": "{track_number} - {tracktitle}",
            }
        )
        assert saida == []

    def test_aceita_especificadores_e_conversoes(self):
        cli.validate_config_formats({"t": "{track_number:02d} {tracktitle!s}"})

    def test_ignora_formatos_vazios_ou_none(self):
        cli.validate_config_formats({"a": "", "b": None})

    def test_variavel_desconhecida_aborta_com_sugestao(self, saida):
        with pytest.raises(SystemExit) as sai:
            cli.validate_config_formats({"folder_format": "{artst}"})
        assert sai.value.code == 1
        tudo = "\n".join(saida)
        assert "artst" in tudo
        assert "folder_format" in tudo
        assert "artist" in tudo  # "Você quis dizer '{artist}'?"

    def test_sintaxe_quebrada_aborta(self, saida):
        with pytest.raises(SystemExit) as sai:
            cli.validate_config_formats({"track_format": "{tracktitle"})
        assert sai.value.code == 1
        assert "track_format" in "\n".join(saida)

    def test_um_erro_entre_varios_ja_aborta(self):
        with pytest.raises(SystemExit):
            cli.validate_config_formats({"ok": "{artist}", "ruim": "{xyz123}"})

    @pytest.mark.parametrize(
        "variavel",
        [
            "artist",
            "album",
            "upc",
            "isrc",
            "bit_depth",
            "sampling_rate",
            "track_number",
            "disc_number",
            "release_type",
            "ExplicitFlag",
            "label",
        ],
    )
    def test_variaveis_documentadas_sao_aceitas(self, variavel):
        cli.validate_config_formats({"x": "{" + variavel + "}"})


# ---------------------------------------------------------------------------
# Formatação de valores / campos extras
# ---------------------------------------------------------------------------
class TestFormatTimestamp:
    @pytest.mark.parametrize("ts", [None, 0, ""])
    def test_vazio_vira_na(self, ts):
        assert cli._format_timestamp(ts) == "N/A"

    def test_formato_brasileiro(self):
        ts = int(datetime(2026, 10, 8, 14, 5, 9).timestamp())
        assert cli._format_timestamp(ts) == "08/10/2026 14:05:09"

    def test_valor_invalido_volta_como_texto(self):
        assert cli._format_timestamp(10**20) == str(10**20)


class TestFormatarValor:
    @pytest.mark.parametrize(
        "valor, esperado",
        [
            (None, "N/A"),
            ("", "N/A"),
            (True, "Sim"),
            (False, "Não"),
            (0, 0),
            ("texto", "texto"),
            ([1], [1]),
        ],
    )
    def test_valores(self, valor, esperado):
        assert cli._formatar_valor(valor) == esperado


class TestImprimirCamposExtras:
    def test_nada_extra_nao_imprime(self, saida):
        cli._imprimir_campos_extras({"a": 1}, "Extras", {"a"})
        assert saida == []

    def test_imprime_cada_tipo_de_valor(self, saida):
        dados = {
            "mostrado": "oculto",
            "simples": True,
            "vazio": None,
            "dict": {"x": 1, "y": None},
            "dict_vazio": {},
            "lista": ["a", False],
            "lista_vazia": [],
            "lista_de_dicts": [{"k": 1, "z": ""}],
        }
        cli._imprimir_campos_extras(dados, "Extras", {"mostrado"})
        out = "\n".join(saida)
        assert "[Extras]" in out
        assert "oculto" not in out
        assert "• simples: Sim" in out
        assert "• vazio: N/A" in out
        assert "• dict:" in out and "- x: 1" in out and "- y: N/A" in out
        assert "• dict_vazio: (vazio)" in out
        assert "- a" in out and "- Não" in out
        assert "• lista_vazia: (vazio)" in out
        assert "[0] k=1, z=N/A" in out


# ---------------------------------------------------------------------------
# Limpeza de temporários
# ---------------------------------------------------------------------------
class TestRemoveLeftovers:
    def test_so_envia_temporarios_para_a_lixeira(self, tmp_path, monkeypatch):
        enviados = []
        monkeypatch.setattr(cli.send2trash, "send2trash", enviados.append)
        (tmp_path / "Album").mkdir()
        mantidos = [tmp_path / "Album" / "01.flac", tmp_path / "capa.jpg"]
        lixo = [
            tmp_path / ".01.flac.tmp",
            tmp_path / "Album" / "~tmp_02.tmp",
            tmp_path / "Album" / ".oculto.tmp",
        ]
        for p in mantidos + lixo:
            p.write_bytes(b"x")
        cli._remove_leftovers(str(tmp_path))
        assert sorted(enviados) == sorted(str(p) for p in lixo)
        assert all(p.exists() for p in mantidos)

    def test_falha_da_lixeira_nao_interrompe(self, tmp_path, monkeypatch):
        chamadas = []

        def falha(caminho):
            chamadas.append(caminho)
            raise OSError("lixeira indisponível")

        monkeypatch.setattr(cli.send2trash, "send2trash", falha)
        (tmp_path / ".a.tmp").write_bytes(b"x")
        (tmp_path / ".b.tmp").write_bytes(b"x")
        cli._remove_leftovers(str(tmp_path))
        assert len(chamadas) == 2


# ---------------------------------------------------------------------------
# Logo
# ---------------------------------------------------------------------------
class TestLogo:
    def test_cinco_linhas_com_largura_uniforme(self):
        linhas = cli._render_logo_word("QOBUZ-DL")
        assert len(linhas) == 5
        assert len({len(l) for l in linhas}) == 1

    def test_largura_e_letras_vezes_cinco_mais_espacos(self):
        # 5 colunas por letra + 1 espaço entre letras.
        assert len(cli._render_logo_word("ULTRA")[0]) == 5 * 5 + 4

    def test_usa_o_bloco_cheio(self):
        assert cli._LOGO_BLOCK in "".join(cli._render_logo_word("Q"))

    def test_todas_as_letras_do_logo_existem_na_fonte(self):
        for palavra in ("QOBUZ-DL", "ULTRA", "QDL"):
            cli._render_logo_word(palavra)  # KeyError se faltar glifo

    def test_logo_largo_mostra_duas_palavras(self, saida):
        cli._print_logo(200)
        assert len(saida) == 10

    def test_logo_estreito_cai_para_qdl(self, saida):
        cli._print_logo(20)
        assert len(saida) == 5
        assert max(len(l.strip("\x1b[0123456789;m")) for l in saida) < 20

    def test_logo_e_centralizado(self, saida):
        cli._print_logo(200)
        primeira = saida[0]
        largura_arte = len(cli._render_logo_word("QOBUZ-DL")[0])
        margem = (200 - largura_arte) // 2
        assert margem > 0
        assert primeira.startswith(" " * margem)
        # margem + arte (sem códigos de cor: conftest define NO_COLOR=1)
        assert len(primeira) == margem + largura_arte


# ---------------------------------------------------------------------------
# Listas usadas na tela de ajuda
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def parser():
    return qobuz_dl_args()


class TestExtracaoDoParser:
    def test_subcomandos_trazem_nome_alias_e_ajuda(self, parser):
        por_nome = {
            nome: (alias, ajuda)
            for nome, alias, ajuda in cli._extract_subcommands(parser)
        }
        for esperado in ("dl", "lucky", "lyrics", "tags", "scan", "doctor", "stats"):
            assert esperado in por_nome
        alias, ajuda = por_nome["sync-playlist"]
        assert alias and "sp" in alias.split(", ")
        assert por_nome["tags"][1]

    def test_alias_nao_aparece_como_comando_separado(self, parser):
        nomes = [n for n, _, _ in cli._extract_subcommands(parser)]
        assert "sp" not in nomes
        assert len(nomes) == len(set(nomes))

    def test_parser_sem_subcomandos(self):
        import argparse

        assert cli._extract_subcommands(argparse.ArgumentParser()) == []

    def test_flags_globais_nao_incluem_help_nem_subcomandos(self, parser):
        flags = cli._extract_global_flags(parser)
        textos = [f for f, _, _ in flags]
        assert not any("--help" in t for t in textos)
        assert any("--purge" in t for t in textos)
        assert any("--show-config" in t for t in textos)
        assert all(isinstance(dest, str) for _, dest, _ in flags)

    def test_todo_subcomando_tem_descricao_em_portugues(self, parser):
        for nome, _, _ in cli._extract_subcommands(parser):
            assert nome in cli._COMMAND_DESCRIPTIONS_PT, f"falta descrição de '{nome}'"

    def test_descricao_de_tags_menciona_a_capa(self):
        assert "capa" in cli._COMMAND_DESCRIPTIONS_PT["tags"].lower()


# ---------------------------------------------------------------------------
# _bootstrap_ui
# ---------------------------------------------------------------------------
class TestBootstrapUi:
    @pytest.mark.parametrize(
        "argv, esperado",
        [
            (["--quiet"], {"quiet": True, "verbose": False, "color": None}),
            (["-v"], {"quiet": False, "verbose": True, "color": None}),
            (["--verbose"], {"quiet": False, "verbose": True, "color": None}),
            (["--no-color"], {"quiet": False, "verbose": False, "color": False}),
            ([], {"quiet": False, "verbose": False, "color": None}),
        ],
    )
    def test_flags_chegam_ao_ui(self, monkeypatch, argv, esperado):
        recebido = {}
        monkeypatch.setattr(sys, "argv", ["qobuz-dl", *argv])
        monkeypatch.setattr(cli.ui, "configure", lambda **kw: recebido.update(kw))
        monkeypatch.setattr(cli.ui, "install_logging", lambda: None)
        cli._bootstrap_ui()
        assert recebido == esperado
