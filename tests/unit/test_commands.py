"""Testa qobuz_dl/commands.py -- a montagem inteira do argparse (821 linhas,
zero teste até agora). Foco: cada subcomando registra os defaults/flags
certos, aliases funcionam, e a distribuição de `add_common_arg`/
`add_output_args` entre subparsers está correta (é fácil esquecer de
adicionar um subcomando novo numa dessas listas e ele silenciosamente não
ganhar as flags esperadas).
"""

import os

import pytest

from qobuz_dl.commands import (
    CustomHelpFormatter,
    _default_download_folder,
    qobuz_dl_args,
)


# --------------------------------------------------------------------
# _default_download_folder
# --------------------------------------------------------------------
class TestDefaultDownloadFolder:
    def test_sem_variavel_de_ambiente_usa_pasta_relativa(self, monkeypatch):
        monkeypatch.delenv("QOBUZ_DL_IOS_HOME", raising=False)
        assert _default_download_folder() == "QobuzDownloads"

    def test_com_variavel_ios_junta_o_caminho(self, monkeypatch):
        monkeypatch.setenv("QOBUZ_DL_IOS_HOME", "/private/var/mobile/App")
        resultado = _default_download_folder()
        assert resultado.endswith("QobuzDownloads")
        assert "/private/var/mobile/App" in resultado


# --------------------------------------------------------------------
# CustomHelpFormatter
# --------------------------------------------------------------------
class TestCustomHelpFormatter:
    def test_usa_a_largura_do_terminal_quando_disponivel(self, monkeypatch):
        import shutil

        # NOTA: shutil.get_terminal_size() de verdade devolve um
        # os.terminal_size (com atributo .columns), não uma tupla simples
        # -- um mock que devolva só (120, 40) faria .columns falhar e
        # cair silenciosamente no `except Exception` do próprio código
        # (que existe pra tratar ambiente sem terminal), mascarando o
        # teste.
        monkeypatch.setattr(
            shutil,
            "get_terminal_size",
            lambda fallback=(100, 24): os.terminal_size((120, 40)),
        )
        formatter = CustomHelpFormatter(prog="qobuz-dl")
        assert formatter._width == 120

    def test_cai_pro_fallback_100_se_get_terminal_size_falhar(self, monkeypatch):
        import shutil

        def _explode(fallback=(100, 24)):
            raise OSError("sem terminal")

        monkeypatch.setattr(shutil, "get_terminal_size", _explode)
        formatter = CustomHelpFormatter(prog="qobuz-dl")
        assert formatter._width == 100


# --------------------------------------------------------------------
# qobuz_dl_args -- montagem completa do parser
# --------------------------------------------------------------------
@pytest.fixture
def parser():
    return qobuz_dl_args(default_quality=6, default_limit=20, default_folder="MinhaMusica")


class TestParserPrincipal:
    def test_todos_os_subcomandos_esperados_existem(self, parser):
        args = parser.parse_args(["dl", "URL"])
        assert args.command == "dl"

        # Argumentos mínimos que cada subcomando aceita sem lançar
        # SystemExit por "invalid choice" ou argumento obrigatório
        # faltando -- confirma que o subcomando de fato foi registrado.
        minimos = {
            "interactive": [],
            "lucky": ["termo de busca"],
            "import-playlist": ["/caminho/playlist.m3u"],
            "lyrics": [],
            "sync-playlist": ["https://play.qobuz.com/playlist/12345"],
            "stats": [],
            "inspect": [],
            "auth": [],
            "user": [],
        }
        for comando, args_extra in minimos.items():
            resultado = parser.parse_args([comando, *args_extra])
            assert resultado.command == comando, f"subcomando '{comando}' não registrou"

    def test_flags_de_nivel_superior_existem(self, parser):
        args = parser.parse_args(["--reset", "dl", "URL"])
        assert args.reset is True
        assert args.purge is False

    def test_sync_db_aceita_valor_opcional_com_default_const(self, parser):
        # NOTA: --sync-db (nargs="?") consome avidamente o próximo token
        # como valor se ele não começar com "-" -- por isso o teste usa
        # a flag SOZINHA, sem um subcomando logo depois. Combinar os dois
        # (`--sync-db dl URL`) faz `--sync-db` engolir "dl" como se fosse
        # o path, sobrando "URL" pra tentar virar subcomando -> SystemExit.
        # É comportamento real do argparse pra nargs="?", não um bug.
        args = parser.parse_args(["--sync-db"])
        assert args.sync_db == "DEFAULT"

        args = parser.parse_args(["--sync-db", "/caminho/custom"])
        assert args.sync_db == "/caminho/custom"

    def test_sem_nenhum_comando_nao_lanca_e_command_fica_none(self, parser):
        args = parser.parse_args([])
        assert args.command is None


class TestAliasesDeSubcomando:
    @pytest.mark.parametrize("alias", ["interactive", "i", "fun"])
    def test_aliases_do_interactive(self, parser, alias):
        args = parser.parse_args([alias])
        assert args.command == alias

    @pytest.mark.parametrize("alias", ["auth", "login"])
    def test_aliases_do_auth(self, parser, alias):
        args = parser.parse_args([alias])
        assert args.command == alias

    @pytest.mark.parametrize("alias", ["user", "account", "profile", "me", "info"])
    def test_aliases_do_user(self, parser, alias):
        args = parser.parse_args([alias])
        assert args.command == alias


class TestSubcomandoDl:
    def test_source_e_obrigatorio(self, parser):
        with pytest.raises(SystemExit):
            parser.parse_args(["dl"])

    def test_aceita_multiplas_urls(self, parser):
        args = parser.parse_args(["dl", "URL1", "URL2", "URL3"])
        assert args.SOURCE == ["URL1", "URL2", "URL3"]

    def test_defaults_das_flags_booleanas(self, parser):
        args = parser.parse_args(["dl", "URL"])
        assert args.dry_run is False
        assert args.tag_only is False
        assert args.musicbrainz is False
        assert args.blacklist is None
        assert args.since is None
        assert args.before is None

    def test_flags_booleanas_ligam_com_store_true(self, parser):
        args = parser.parse_args(["dl", "URL", "--dry-run", "--musicbrainz"])
        assert args.dry_run is True
        assert args.musicbrainz is True
        assert args.tag_only is False  # as outras continuam desligadas


class TestSubcomandoStats:
    def test_default_artistas_e_false(self, parser):
        args = parser.parse_args(["stats"])
        assert args.artistas is False

    def test_flag_artistas_liga(self, parser):
        args = parser.parse_args(["stats", "--artistas"])
        assert args.artistas is True


class TestSubcomandoInspect:
    def test_caminho_default_e_none(self, parser):
        # Regressão documentada no próprio commands.py: caminho tinha que
        # ficar None quando não informado, pra detecção automática por
        # dispositivo em inspector.py rodar -- não pré-preenchido com a
        # pasta de DOWNLOADS.
        args = parser.parse_args(["inspect"])
        assert args.caminho is None

    def test_caminho_explicito_e_respeitado(self, parser):
        args = parser.parse_args(["inspect", "/minha/biblioteca"])
        assert args.caminho == "/minha/biblioteca"


class TestSubcomandoUser:
    def test_default_json_e_false(self, parser):
        args = parser.parse_args(["user"])
        assert args.json is False

    def test_flag_json_liga(self, parser):
        args = parser.parse_args(["user", "--json"])
        assert args.json is True


class TestDistribuicaoDeFlagsComuns:
    """`add_common_arg` (quality/folder/etc.) só é aplicada a
    interactive/dl/lucky/sync-playlist -- confirma que a lista não foi
    editada por engano removendo um desses, ou que um subcomando que NÃO
    devia ganhar essas flags não ganhou."""

    @pytest.mark.parametrize("comando,args_extra", [
        ("interactive", []),
        ("dl", ["URL"]),
        ("lucky", ["termo de busca"]),
        ("sync-playlist", ["https://play.qobuz.com/playlist/12345"]),
    ])
    def test_subcomandos_com_add_common_arg_tem_quality(self, parser, comando, args_extra):
        args = parser.parse_args([comando, *args_extra])
        assert hasattr(args, "quality")

    @pytest.mark.parametrize("comando,args_extra", [
        ("auth", []),
        ("user", []),
        ("stats", []),
        ("inspect", []),
    ])
    def test_subcomandos_sem_add_common_arg_nao_tem_quality(
        self, parser, comando, args_extra
    ):
        args = parser.parse_args([comando, *args_extra])
        assert not hasattr(args, "quality")

    def test_flag_antes_do_subcomando_sobrevive_ao_default_do_subcomando(self, parser):
        # O parser PRINCIPAL já registra -v/--verbose sem suprimir (por
        # isso hasattr(args, "verbose") é sempre True, não é isso que
        # `suppress=True` no subcomando garante). O que o suppress
        # garante de verdade: se "stats" registrasse --verbose SEM
        # suprimir, o parse do subcomando reescreveria verbose=False por
        # cima do que já tinha sido setado antes do subcomando -- com
        # suppress=True, o subcomando só define verbose se você passar a
        # flag DEPOIS dele; antes disso, o valor do nível principal
        # sobrevive intacto.
        args = parser.parse_args(["--verbose", "stats"])
        assert args.verbose is True

    def test_flag_depois_do_subcomando_tambem_funciona(self, parser):
        args = parser.parse_args(["stats", "--verbose"])
        assert args.verbose is True
