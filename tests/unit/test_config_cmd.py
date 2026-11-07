"""Testa qobuz_dl/config_cmd.py: show/path/get/set sem perder comentários."""

import json
import os
import stat
from types import SimpleNamespace

import pytest

from qobuz_dl import config_cmd

SENSIVEIS = frozenset({"email", "password", "user_auth_token"})

INI = """\
# comentário do topo
[qobuz]
email = eu@exemplo.com
password =
user_auth_token = segredo-abc
default_quality = 27
# qualidade acima; abaixo, o resto
no_lrc_files = false
directory = Qobuz Downloads

[outra]
x = 1
"""


@pytest.fixture
def ini(tmp_path):
    caminho = tmp_path / "config.ini"
    caminho.write_text(INI, encoding="utf-8")
    return str(caminho)


def _args(**kw):
    base = dict(action="show", key=None, value=None, nova=False, json=False)
    base.update(kw)
    return SimpleNamespace(**base)


def _rodar(ini, **kw):
    return config_cmd.cmd_config(
        _args(**kw),
        config_file=ini,
        db_path="/tmp/db.sqlite",
        library_db="/tmp/lib.sqlite",
        sensiveis=SENSIVEIS,
    )


# --- definir_chave ---------------------------------------------------------


def test_definir_chave_troca_so_a_linha_alvo():
    novo, antigo = config_cmd.definir_chave(INI, "qobuz", "default_quality", "6")
    assert antigo == "27"
    assert novo == INI.replace("default_quality = 27", "default_quality = 6")


def test_definir_chave_preserva_comentarios_e_ordem():
    novo, _ = config_cmd.definir_chave(INI, "qobuz", "directory", "Musicas")
    assert "# comentário do topo" in novo
    assert "# qualidade acima; abaixo, o resto" in novo
    assert novo.endswith("x = 1\n")


def test_definir_chave_ignora_mesma_chave_em_outra_secao():
    texto = "[a]\nx = 1\n[b]\nx = 2\n"
    novo, antigo = config_cmd.definir_chave(texto, "b", "x", "9")
    assert antigo == "2"
    assert novo == "[a]\nx = 1\n[b]\nx = 9\n"


def test_definir_chave_e_insensivel_a_maiusculas_na_chave_e_secao():
    novo, antigo = config_cmd.definir_chave(INI, "QOBUZ", "DEFAULT_QUALITY", "5")
    assert antigo == "27"
    assert "default_quality = 5" in novo


def test_definir_chave_inexistente_sem_nova_levanta():
    with pytest.raises(config_cmd.ConfigErro, match="--nova"):
        config_cmd.definir_chave(INI, "qobuz", "nao_existe", "1")


def test_definir_chave_nova_entra_no_fim_da_secao():
    novo, antigo = config_cmd.definir_chave(INI, "qobuz", "nova_opcao", "1", criar=True)
    assert antigo is None
    linhas = novo.splitlines()
    assert (
        linhas.index("nova_opcao = 1")
        == linhas.index("directory = Qobuz Downloads") + 1
    )


def test_definir_chave_secao_inexistente_levanta():
    with pytest.raises(config_cmd.ConfigErro, match="seção"):
        config_cmd.definir_chave(INI, "fantasma", "x", "1", criar=True)


def test_definir_chave_remove_continuacao_do_valor_antigo():
    texto = "[s]\nlista = a\n  b\n  c\nfim = 1\n"
    novo, antigo = config_cmd.definir_chave(texto, "s", "lista", "z")
    assert novo == "[s]\nlista = z\nfim = 1\n"
    assert antigo == "a"


def test_definir_chave_mantem_ausencia_de_quebra_final():
    novo, _ = config_cmd.definir_chave("[s]\nx = 1", "s", "x", "2")
    assert novo == "[s]\nx = 2"


# --- validar_valor ---------------------------------------------------------


@pytest.mark.parametrize("antigo,novo", [("false", "true"), ("True", "0"), ("1", "no")])
def test_validar_booleano_aceita_booleano(antigo, novo):
    config_cmd.validar_valor(antigo, novo)


def test_validar_booleano_rejeita_texto():
    with pytest.raises(config_cmd.ConfigErro, match="true ou false"):
        config_cmd.validar_valor("false", "talvez")


def test_validar_inteiro_rejeita_texto():
    with pytest.raises(config_cmd.ConfigErro, match="inteiro"):
        config_cmd.validar_valor("27", "alta")


@pytest.mark.parametrize("antigo", [None, "", "texto qualquer"])
def test_validar_sem_tipo_definido_aceita_qualquer_coisa(antigo):
    config_cmd.validar_valor(antigo, "qualquer coisa")


# --- cmd_config ------------------------------------------------------------


def test_path_imprime_so_o_caminho(ini, capsys):
    assert _rodar(ini, action="path") == 0
    assert capsys.readouterr().out.strip() == ini


def test_show_oculta_credenciais(ini, capsys):
    assert _rodar(ini) == 0
    saida = capsys.readouterr().out
    assert "segredo-abc" not in saida
    assert "eu@exemplo.com" not in saida
    assert "<redacted>" in saida
    assert "DEFAULT_QUALITY" in saida  # rótulos saem em maiúsculas no painel


def test_show_json_e_valido_e_sem_segredos(ini, capsys):
    assert _rodar(ini, json=True) == 0
    bruto = capsys.readouterr().out
    dados = json.loads(bruto)
    assert dados["arquivo"] == ini
    assert dados["opcoes"]["qobuz"]["default_quality"] == "27"
    assert dados["opcoes"]["qobuz"]["user_auth_token"] == "<redacted>"
    assert "segredo-abc" not in bruto


def test_get_devolve_o_valor(ini, capsys):
    assert _rodar(ini, action="get", key="default_quality") == 0
    assert capsys.readouterr().out.strip() == "27"


def test_get_credencial_sai_oculta(ini, capsys):
    assert _rodar(ini, action="get", key="user_auth_token") == 0
    assert capsys.readouterr().out.strip() == "<redacted>"


def test_get_chave_inexistente_falha(ini, capsys):
    assert _rodar(ini, action="get", key="nada") == 1


def test_get_sem_chave_e_erro_de_uso(ini):
    assert _rodar(ini, action="get") == 2


def test_set_altera_e_preserva_o_resto(ini):
    assert _rodar(ini, action="set", key="default_quality", value="6") == 0
    texto = open(ini, encoding="utf-8").read()
    assert texto == INI.replace("default_quality = 27", "default_quality = 6")


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX")
def test_set_deixa_o_arquivo_com_permissao_0600(ini):
    os.chmod(ini, 0o644)
    assert _rodar(ini, action="set", key="default_quality", value="6") == 0
    assert stat.S_IMODE(os.stat(ini).st_mode) == 0o600


def test_set_nao_deixa_temporarios_na_pasta(ini):
    _rodar(ini, action="set", key="default_quality", value="6")
    assert os.listdir(os.path.dirname(ini)) == ["config.ini"]


def test_set_rejeita_credencial(ini):
    antes = open(ini, encoding="utf-8").read()
    assert _rodar(ini, action="set", key="user_auth_token", value="novo") == 1
    assert open(ini, encoding="utf-8").read() == antes


def test_set_tipo_errado_nao_grava(ini):
    antes = open(ini, encoding="utf-8").read()
    assert _rodar(ini, action="set", key="no_lrc_files", value="talvez") == 1
    assert open(ini, encoding="utf-8").read() == antes


def test_set_chave_inexistente_pede_nova(ini):
    antes = open(ini, encoding="utf-8").read()
    assert _rodar(ini, action="set", key="inventada", value="1") == 1
    assert open(ini, encoding="utf-8").read() == antes


def test_set_com_nova_cria_a_opcao(ini):
    assert _rodar(ini, action="set", key="inventada", value="1", nova=True) == 0
    assert "inventada = 1" in open(ini, encoding="utf-8").read()


def test_set_sem_valor_e_erro_de_uso(ini):
    assert _rodar(ini, action="set", key="default_quality") == 2


def test_resultado_do_set_e_legivel_pelo_configparser(ini):
    _rodar(ini, action="set", key="directory", value="Minha Música")
    cfg = config_cmd.ler_config(ini)
    assert cfg.get("qobuz", "directory") == "Minha Música"
    assert cfg.get("outra", "x") == "1"


# --- arquivo ausente ou vazio ----------------------------------------------------


def test_show_arquivo_ausente_avisa_e_falha(tmp_path, capsys):
    caminho = str(tmp_path / "nao-existe.ini")
    assert _rodar(caminho) == 1
    saida = capsys.readouterr().out
    assert "não existe" in saida and "reset" in saida


def test_show_arquivo_vazio_avisa_e_falha(tmp_path, capsys):
    caminho = tmp_path / "config.ini"
    caminho.write_text("", encoding="utf-8")
    assert _rodar(str(caminho)) == 1
    assert "vazio" in capsys.readouterr().out


def test_show_json_informa_o_estado(tmp_path, capsys):
    ausente = str(tmp_path / "x.ini")
    assert _rodar(ausente, json=True) == 1
    assert json.loads(capsys.readouterr().out)["estado"] == "ausente"

    vazio = tmp_path / "v.ini"
    vazio.write_text("\n", encoding="utf-8")
    assert _rodar(str(vazio), json=True) == 1
    assert json.loads(capsys.readouterr().out)["estado"] == "vazio"


def test_show_json_arquivo_bom_tem_estado_ok_e_sai_0(ini, capsys):
    assert _rodar(ini, json=True) == 0
    assert json.loads(capsys.readouterr().out)["estado"] == "ok"


@pytest.mark.parametrize("acao", ["get", "set"])
def test_get_e_set_com_arquivo_ausente_nao_criam_nada(tmp_path, acao, capsys):
    caminho = tmp_path / "x.ini"
    assert _rodar(str(caminho), action=acao, key="k", value="v") == 1
    assert not caminho.exists()
    assert "não existe" in capsys.readouterr().out


def test_path_funciona_mesmo_sem_arquivo(tmp_path, capsys):
    caminho = str(tmp_path / "x.ini")
    assert _rodar(caminho, action="path") == 0
    assert capsys.readouterr().out.strip() == caminho
