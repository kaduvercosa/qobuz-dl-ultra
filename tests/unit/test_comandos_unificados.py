"""Lote 2: `user` é apelido de `auth`; `lyrics --watch` observa a pasta."""

from types import SimpleNamespace

import pytest

from qobuz_dl import cli, commands
from qobuz_dl.commands import qobuz_dl_args

pytestmark = pytest.mark.unit

APELIDOS_DE_AUTH = ("login", "user", "account", "profile", "me", "info")


@pytest.fixture
def parser():
    return qobuz_dl_args(
        default_quality=6, default_limit=20, default_folder="MinhaMusica"
    )


@pytest.mark.parametrize("nome", ("auth",) + APELIDOS_DE_AUTH)
def test_todos_os_nomes_aceitam_login_e_json(parser, nome):
    padrao = parser.parse_args([nome])
    assert padrao.command == nome
    assert padrao.json is False and padrao.login is False
    assert parser.parse_args([nome, "--json"]).json is True
    assert parser.parse_args([nome, "-l"]).login is True


def test_user_nao_e_mais_um_comando_proprio(parser):
    comandos = {n: a for n, a, _ in cli._extract_subcommands(parser)}
    assert "user" not in comandos
    assert set(APELIDOS_DE_AUTH) <= set((comandos["auth"] or "").split(", "))
    assert not hasattr(commands, "user_args")


def test_toda_descricao_de_comando_existe(parser):
    for nome, _, _ in cli._extract_subcommands(parser):
        assert nome in cli._COMMAND_DESCRIPTIONS_PT
    assert "user" not in cli._COMMAND_DESCRIPTIONS_PT


def test_lyrics_watch_e_a_flag_global_convivem(parser):
    assert parser.parse_args(["lyrics"]).lyrics_watch is False
    assert parser.parse_args(["lyrics", "--watch"]).lyrics_watch is True
    com_pasta = parser.parse_args(["lyrics", "--watch", "/musicas"])
    assert com_pasta.lyrics_watch is True and com_pasta.DIR == "/musicas"
    assert parser.parse_args(["--watch"]).watch == "DEFAULT"
    assert parser.parse_args(["--watch", "/x"]).watch == "/x"


@pytest.mark.parametrize(
    "args, esperado",
    [
        (dict(command="lyrics", lyrics_watch=True, DIR=None, watch=None), "DEFAULT"),
        (dict(command="lyrics", lyrics_watch=True, DIR="/m", watch=None), "/m"),
        (dict(command="lyrics", lyrics_watch=False, DIR="/m", watch=None), None),
        (dict(command=None, watch="/x"), "/x"),
        (dict(command=None, watch="DEFAULT"), "DEFAULT"),
        (dict(command="dl", watch=None), None),
        (dict(command="tags", lyrics_watch=True, watch=None), None),
    ],
)
def test_pasta_observada(args, esperado):
    assert cli._watch_target(SimpleNamespace(**args)) == esperado
