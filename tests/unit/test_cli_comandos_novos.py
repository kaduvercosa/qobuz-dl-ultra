"""Parser e atalhos dos comandos novos: config, duplicates, cleanup, sync-db, watch."""

import pytest

from qobuz_dl import cli
from qobuz_dl.commands import qobuz_dl_args


def _parse(*argv):
    return qobuz_dl_args().parse_args(list(argv))


@pytest.mark.parametrize(
    "argv,esperado",
    [
        (["-r"], True),
        (["--reset"], True),
        (["-v", "--reset"], True),
        (["config", "reset"], True),
        (["cfg", "reset"], True),
        (["--log-level", "debug", "config", "reset", "-y"], True),
        (["config"], False),
        (["config", "show"], False),
        (["config", "set", "reset", "1"], False),
        (["dl", "reset"], False),
        ([], False),
    ],
)
def test_pede_reset(argv, esperado):
    assert cli._pede_reset(argv) is esperado


def test_config_padrao_e_show():
    a = _parse("config")
    assert (a.command, a.action) == ("config", "show")


def test_config_set_leva_chave_valor_e_nova():
    a = _parse("config", "set", "default_quality", "6", "--nova")
    assert (a.action, a.key, a.value, a.nova) == ("set", "default_quality", "6", True)


def test_config_rejeita_acao_desconhecida():
    with pytest.raises(SystemExit):
        _parse("config", "apagar")


def test_config_aceita_apelido():
    assert _parse("cfg", "path").action == "path"


def test_duplicates_mover_e_desfazer_sao_exclusivos():
    with pytest.raises(SystemExit):
        _parse("duplicates", "--mover", "--desfazer")


def test_duplicates_opcoes():
    a = _parse("duplicates", "Musicas", "--mover")
    assert (a.DIR, a.mover, a.desfazer) == ("Musicas", True, False)
    assert _parse("dups", "--desfazer").desfazer is True


def test_cleanup_opcoes():
    a = _parse("cleanup", "--apagar", "--idade", "2.5")
    assert (a.apagar, a.idade, a.DIR) == (True, 2.5, None)
    assert _parse("clean").apagar is False


def test_novos_comandos_aceitam_flags_de_saida_globais():
    for cmd in ("config", "duplicates", "cleanup", "sync-db", "watch"):
        a = _parse(cmd, "-v", "--quiet")
        assert a.verbose is True and a.quiet is True


# --- _aplicar_atalhos --------------------------------------------------------


def test_atalho_config_reset_liga_a_flag_reset():
    a = _parse("config", "reset")
    assert a.reset is False
    cli._aplicar_atalhos(a)
    assert a.reset is True


def test_atalho_config_show_nao_liga_reset():
    a = _parse("config", "show")
    cli._aplicar_atalhos(a)
    assert a.reset is False


def test_atalho_sync_db_vira_a_flag_legada():
    a = _parse("sync-db")
    cli._aplicar_atalhos(a)
    assert a.sync_db == "DEFAULT"
    b = _parse("sync-db", "Musicas")
    cli._aplicar_atalhos(b)
    assert b.sync_db == "Musicas"


def test_atalho_watch_vira_a_flag_legada_e_alimenta_watch_target():
    a = _parse("watch", "Musicas")
    cli._aplicar_atalhos(a)
    assert a.watch == "Musicas"
    assert cli._watch_target(a) == "Musicas"
    b = _parse("watch")
    cli._aplicar_atalhos(b)
    assert cli._watch_target(b) == "DEFAULT"


def test_flags_legadas_continuam_funcionando():
    assert _parse("--sync-db", "X").sync_db == "X"
    assert _parse("--watch").watch == "DEFAULT"
    assert _parse("--find-duplicates").find_duplicates == "DEFAULT"
    assert _parse("-r").reset is True


def test_pasta_da_biblioteca_usa_o_argumento_ou_o_padrao(monkeypatch):
    monkeypatch.setattr(cli, "IOS_HOME", None)
    assert cli._pasta_da_biblioteca("/a/b", "/x") == "/a/b"
    assert cli._pasta_da_biblioteca(None, "/x") == "/x"


def test_pasta_da_biblioteca_relativa_vai_para_a_home_do_ios(monkeypatch):
    monkeypatch.setattr(cli, "IOS_HOME", "/home/ios")
    assert cli._pasta_da_biblioteca(None, "Qobuz Downloads") == (
        "/home/ios/Qobuz Downloads"
    )
    assert cli._pasta_da_biblioteca("/abs", "x") == "/abs"


def test_confirmar_reset(monkeypatch):
    for resposta, esperado in [("s", True), ("SIM", True), ("n", False), ("", False)]:
        monkeypatch.setattr("builtins.input", lambda *_a, r=resposta: r)
        assert cli._confirmar_reset() is esperado


def test_confirmar_reset_sem_stdin_nega(monkeypatch):
    def _eof(*_a):
        raise EOFError

    monkeypatch.setattr("builtins.input", _eof)
    assert cli._confirmar_reset() is False


# --- assistente de primeira execução -------------------------------------------


@pytest.mark.parametrize(
    "argv,dispensa",
    [
        (["stats", "--json"], True),
        (["--json", "stats"], True),
        (["-v", "library", "missing"], True),
        (["doctor"], True),
        (["config", "get", "directory"], True),
        (["duplicates"], True),
        (["cleanup"], True),
        (["inspect", "faixa.flac"], True),
        (["auth", "--oauth"], True),
        (["--oauth", "auth"], True),
        (["auth", "--oauth-no-browser"], True),
        (["auth", "-l"], False),
        (["tags", "--help"], True),
        (["-h"], True),
        (["config", "reset"], False),
        (["-r"], False),
        (["dl", "https://play.qobuz.com/album/1"], False),
        (["interactive"], False),
        (["sync-db"], False),
        ([], False),
    ],
)
def test_dispensa_assistente(argv, dispensa):
    assert cli._dispensa_assistente(argv) is dispensa


async def test_initial_checks_nao_abre_assistente_em_comando_local(
    tmp_path, monkeypatch
):
    chamadas = []

    async def falso_assistente(_arquivo):
        chamadas.append(1)

    monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path / "cfg"))
    monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "cfg" / "config.ini"))
    monkeypatch.setattr(cli, "_reset_config", falso_assistente)
    monkeypatch.setattr("sys.argv", ["qobuz-dl", "stats", "--json"])
    await cli._initial_checks()
    assert chamadas == []

    monkeypatch.setattr("sys.argv", ["qobuz-dl", "dl", "url"])
    await cli._initial_checks()
    assert chamadas == [1]
