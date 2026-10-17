"""UX: doctor em painéis, -h geral, escolha do modo do `tags` e erros com próxima ação."""

import argparse
import asyncio
import io
import re
import sys
from types import SimpleNamespace

import pytest

from qobuz_dl import cli, doctor
from qobuz_dl.doctor import FAIL, PASS, WARN, Check
from qobuz_dl.home_screen import render_checks

pytestmark = pytest.mark.unit


class TestRenderChecks:
    def _texto(self, largura=80, **kw):
        itens = [
            (PASS, "Python", "3.13"),
            (WARN, "ffmpeg", "não está no PATH " + "(motivo longo) " * 6),
            (FAIL, "config.ini", "não encontrado"),
        ]
        return render_checks(
            "Doctor",
            itens,
            kw.get("acoes", ()),
            kw.get("resumo", ""),
            largura=largura,
            cor=False,
        )

    @pytest.mark.parametrize("largura", [24, 40, 80, 140])
    def test_nenhuma_linha_passa_da_largura(self, largura):
        for linha in self._texto(largura).splitlines():
            assert len(linha) <= largura, linha

    def test_marcas_e_linha_unica_quando_cabe(self):
        texto = self._texto()
        assert "ok     Python  3.13" in texto
        assert "FALHA  config.ini  não encontrado" in texto
        assert "aviso  ffmpeg" in texto  # o detalhe longo quebra embaixo

    def test_paineis_de_passos_e_resumo(self):
        texto = self._texto(acoes=[("ffmpeg", "instale o ffmpeg")], resumo="1 falha")
        assert "Próximos passos" in texto and "instale o ffmpeg" in texto
        assert "Resumo" in texto and "1 falha" in texto

    def test_sem_acoes_nem_resumo_so_um_painel(self):
        texto = self._texto()
        assert "Próximos passos" not in texto and "Resumo" not in texto


class TestDoctorNextAction:
    @pytest.mark.parametrize(
        "check, trecho",
        [
            (Check(FAIL, "config.ini", "x"), "qobuz-dl -r"),
            (Check(FAIL, "config: password", "vazio"), "qobuz-dl -r"),
            (Check(FAIL, "Dependências", "faltando"), "pip install"),
            (Check(WARN, "ffmpeg", "x"), "ffmpeg"),
            (Check(WARN, "Keyring", "indisponível"), "disable_keyring"),
            (Check(FAIL, "Escrita na pasta", "x"), "permissão"),
        ],
    )
    def test_acao_para_cada_problema(self, check, trecho):
        assert trecho in doctor.next_action(check)

    def test_ok_e_opcionais_nao_tem_acao(self):
        assert doctor.next_action(Check(PASS, "Python", "3.13")) is None
        assert doctor.next_action(Check(WARN, "Opcional: PIL", "ausente")) is None

    def test_problema_desconhecido_nao_inventa_acao(self):
        assert doctor.next_action(Check(FAIL, "Algo novo", "x")) is None

    def test_render_mostra_proximos_passos_e_devolve_codigo(self, monkeypatch):
        from qobuz_dl import ui

        saida = []
        monkeypatch.setattr(
            ui, "emit_always", lambda t="", *a, **k: saida.append(str(t))
        )
        monkeypatch.setattr(ui, "blank", lambda *a, **k: None)
        codigo = doctor.render(
            [Check(PASS, "Python", "3.13"), Check(FAIL, "config.ini", "sumiu")]
        )
        texto = "\n".join(saida)
        assert codigo == 1
        assert "Próximos passos" in texto and "FALHARAM" in texto


class TestAuthFailureHint:
    @pytest.mark.parametrize(
        "exc, trecho",
        [
            (ConnectionError("nope"), "rede"),
            (TimeoutError(), "rede"),
            (OSError("Temporary failure in name resolution"), "rede"),
            (RuntimeError("401 Unauthorized"), "token"),
            (RuntimeError("keyring backend missing"), "keyring"),
            (ValueError("???"), "doctor"),
        ],
    )
    def test_mensagem_por_tipo_de_erro(self, exc, trecho):
        assert trecho in cli.auth_failure_hint(exc).lower()

    def test_excecoes_do_programa(self):
        from qobuz_dl import exceptions as ex

        assert "token" in cli.auth_failure_hint(ex.AuthenticationError("x")).lower()
        assert (
            "assinatura"
            in cli.auth_failure_hint(ex.NoActiveSubscriptionError()).lower()
        )
        assert "app_id" in cli.auth_failure_hint(ex.InvalidAppSecretError()).lower()


class TestEscolhaDoModo:
    def _args(self, **kw):
        base = dict(tags_only=False, covers_only=False, both=False)
        base.update(kw)
        return argparse.Namespace(**base)

    def _terminal(self, monkeypatch, resposta, interativo=True):
        stdin = SimpleNamespace(isatty=lambda: interativo)
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        monkeypatch.setattr(sys.stdout, "isatty", lambda: interativo, raising=False)
        pedidos = []

        def entrada(prompt=""):
            pedidos.append(prompt)
            if isinstance(resposta, BaseException):
                raise resposta
            return resposta

        monkeypatch.setattr("builtins.input", entrada)
        return pedidos

    @pytest.mark.parametrize(
        "resposta, tags_only, covers_only",
        [
            ("", False, False),
            ("1", False, False),
            ("2", True, False),
            ("3", False, True),
        ],
    )
    def test_respostas(self, monkeypatch, resposta, tags_only, covers_only):
        self._terminal(monkeypatch, resposta)
        args = self._args()
        cli._choose_tags_mode(args)
        assert (args.tags_only, args.covers_only) == (tags_only, covers_only)

    def test_resposta_invalida_usa_o_padrao(self, monkeypatch):
        self._terminal(monkeypatch, "banana")
        args = self._args()
        cli._choose_tags_mode(args)
        assert not args.tags_only and not args.covers_only

    @pytest.mark.parametrize("erro", [EOFError(), KeyboardInterrupt()])
    def test_fim_da_entrada_usa_o_padrao(self, monkeypatch, erro):
        self._terminal(monkeypatch, erro)
        args = self._args()
        cli._choose_tags_mode(args)
        assert not args.tags_only and not args.covers_only

    def test_nao_pergunta_sem_terminal(self, monkeypatch):
        pedidos = self._terminal(monkeypatch, "2", interativo=False)
        args = self._args()
        cli._choose_tags_mode(args)
        assert pedidos == [] and not args.tags_only

    @pytest.mark.parametrize("flag", ["tags_only", "covers_only", "both"])
    def test_nao_pergunta_quando_o_modo_foi_dado(self, monkeypatch, flag):
        pedidos = self._terminal(monkeypatch, "2")
        cli._choose_tags_mode(self._args(**{flag: True}))
        assert pedidos == []


class TestAjudaGeral:
    @pytest.fixture
    def amb(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
        (tmp_path / "config.ini").write_text("[DEFAULT]\n")
        chamadas = []
        monkeypatch.setattr(
            cli, "_print_welcome_screen", lambda: chamadas.append("geral")
        )
        monkeypatch.setattr(
            cli,
            "_print_command_help",
            lambda argv: chamadas.append(("cmd", argv)) or True,
        )
        return chamadas

    def _rodar(self, monkeypatch, *argv):
        monkeypatch.setattr(sys, "argv", ["qobuz-dl", *argv])
        with pytest.raises(SystemExit) as saida:
            asyncio.run(cli._initial_checks())
        assert saida.value.code == 0

    @pytest.mark.parametrize(
        "argv",
        [
            ["-h"],
            ["--help"],
            ["-v", "-h"],
            ["--no-color", "--help"],
            ["--log-level", "debug", "-h"],
            ["-h", "--quiet"],
        ],
    )
    def test_ajuda_sem_comando_mostra_a_tela_geral(self, amb, monkeypatch, argv):
        self._rodar(monkeypatch, *argv)
        assert amb == ["geral"]

    @pytest.mark.parametrize(
        "argv, comando",
        [
            (["tags", "-h"], "tags"),
            (["tags", "--help"], "tags"),
            (["-v", "tags", "-h"], "tags"),
            (["dl", "URL", "--help"], "dl"),
            (["--log-level", "debug", "doctor", "-h"], "doctor"),
        ],
    )
    def test_ajuda_com_comando_mostra_a_do_comando(
        self, amb, monkeypatch, argv, comando
    ):
        self._rodar(monkeypatch, *argv)
        assert amb == [("cmd", [comando])]

    def test_sem_pedido_de_ajuda_segue_o_fluxo(self, amb, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["qobuz-dl", "tags", "/musicas"])
        asyncio.run(cli._initial_checks())  # não sai
        assert amb == []


def _ajuda_do_comando(comando, largura, monkeypatch, capsys):
    """Texto de `qobuz-dl <comando> -h` com a largura pedida (parser real)."""
    from qobuz_dl import ui

    monkeypatch.setattr(ui, "raw_width", lambda: largura)
    monkeypatch.setenv("COLUMNS", str(largura))
    assert cli._print_command_help([comando]) is True
    return capsys.readouterr().out


def _primeiras_colunas(texto):
    """Rótulos da 1ª coluna dos painéis: '│ -q, --quality QUALIDADE   desc │'."""
    return [
        linha[2:].split("  ")[0].strip()
        for linha in texto.splitlines()
        if linha.startswith("│ ") and linha[2:3] == "-"
    ]


class TestAjudaDeUmComando:
    def test_render_cabe_na_largura_e_traz_uso_e_exemplo(self):
        from qobuz_dl.home_screen import render_ajuda_comando

        for largura in (24, 40, 80, 140):
            texto = render_ajuda_comando(
                "qobuz-dl tags [--dry-run] [PASTA]",
                "Varre uma pasta e corrige as tags " * 3,
                [
                    ("Argumentos", [("[PASTA]", "pasta da biblioteca")]),
                    (
                        "Opções",
                        [
                            ("-h, --help", "mostra esta ajuda"),
                            ("--dry-run", "só simula"),
                            ("--report [ARQUIVO]", "relatório"),
                        ],
                    ),
                    ("Chaves de formato", [("Pasta", "album_id, album_title")]),
                ],
                exemplo="tags --report ~/Music",
                largura=largura,
                cor=False,
            )
            assert "╭─ Uso" in texto and "~/Music" in texto
            assert "--dry-run" in texto and "Chaves de formato" in texto
            assert all(len(linha) <= max(largura, 24) for linha in texto.splitlines())

    def test_print_command_help_usa_o_parser_real(self, monkeypatch, capsys):
        texto = _ajuda_do_comando("dl", 100, monkeypatch, capsys)
        assert "╭─ Uso" in texto and "Opções" in texto and "--folder-format" in texto
        assert cli._print_command_help(["comando-que-nao-existe"]) is False

    def test_alias_tambem_mostra_a_ajuda(self, monkeypatch, capsys):
        assert "╭─ Uso" in _ajuda_do_comando("i", 100, monkeypatch, capsys)

    @pytest.mark.parametrize("largura", [40, 80, 140])
    def test_opcoes_separadas_por_linha_horizontal(self, largura):
        from qobuz_dl.home_screen import render_ajuda_comando

        opcoes = [
            ("--curta", "descrição que quebra em várias linhas " * 4),
            ("--outra", "texto"),
            ("--terceira N", "mais um texto"),
        ]
        texto = render_ajuda_comando(
            "qobuz-dl dl URL",
            "baixa",
            [("Opções", opcoes)],
            largura=largura,
            cor=False,
        )
        painel = texto.split("╭─ Opções", 1)[1].splitlines()
        # 3 opções => 2 separadores entre elas
        assert sum(1 for linha in painel if linha.lstrip().startswith("├")) == 2

    def test_flags_globais_da_tela_inicial_continuam_sem_separadores(self):
        from qobuz_dl.home_screen import render_home

        texto = render_home(
            [("dl", "", "baixa")],
            [("-v", "mais logs"), ("--quiet", "menos")],
            100,
            False,
        )
        painel = texto.split("Flags globais", 1)[1].split("╰", 1)[0]
        assert "├" not in painel


class TestOpcoesAgrupadas:
    ORDEM = [
        "Opções · Busca e seleção",
        "Opções · Qualidade e execução",
        "Opções · Pastas e nomes",
        "Opções · Capa",
        "Opções · Letras",
        "Opções · Tags e metadados",
        "Opções · Créditos e encarte",
        "Opções · Outras",
    ]

    def _acoes(self, comando):
        from qobuz_dl.commands import qobuz_dl_args

        parser = qobuz_dl_args()
        sub = next(
            a for a in parser._actions if isinstance(a, argparse._SubParsersAction)
        )
        return [
            a
            for a in sub.choices[comando]._actions
            if a.option_strings and a.help != argparse.SUPPRESS
        ]

    @pytest.mark.parametrize("comando", ["dl", "interactive", "lucky"])
    def test_grupos_na_ordem_de_uso(self, comando, monkeypatch, capsys):
        texto = _ajuda_do_comando(comando, 100, monkeypatch, capsys)
        titulos = [
            linha.strip("╭─ ").split(" ─")[0].strip()
            for linha in texto.splitlines()
            if linha.startswith("╭─ Opções ·")
        ]
        assert titulos and titulos[0] == self.ORDEM[0]
        posicoes = [self.ORDEM.index(t) for t in titulos]
        assert posicoes == sorted(posicoes), titulos

    @pytest.mark.parametrize("comando", ["dl", "interactive", "lucky"])
    @pytest.mark.parametrize("largura", [60, 100])
    def test_nenhuma_opcao_se_perde_nem_repete(
        self, comando, largura, monkeypatch, capsys
    ):
        texto = _ajuda_do_comando(comando, largura, monkeypatch, capsys)
        primeiras = [set(re.split(r"[ ,]+", p)) for p in _primeiras_colunas(texto)]
        for acao in self._acoes(comando):
            longa = acao.option_strings[-1]
            vezes = sum(1 for tokens in primeiras if longa in tokens)
            assert vezes == 1, (comando, longa, vezes)

    def test_opcoes_do_proprio_comando_vem_primeiro(self, monkeypatch, capsys):
        texto = _ajuda_do_comando("lucky", 100, monkeypatch, capsys)
        flat = [tok for p in _primeiras_colunas(texto) for tok in re.split(r"[ ,]+", p)]
        assert flat.index("--type") < flat.index("--number") < flat.index("--quality")
        assert flat.index("--quality") < flat.index("--directory")

    def test_ajuda_fica_no_fim_das_opcoes(self, monkeypatch, capsys):
        texto = _ajuda_do_comando("dl", 100, monkeypatch, capsys)
        ultimo = texto.split("╭─ Opções · Outras", 1)[1].split("╰", 1)[0]
        assert ultimo.rstrip().splitlines()[-1].lstrip("│ ").startswith("-h, --help")

    def test_tags_continua_com_um_painel_de_opcoes(self, monkeypatch, capsys):
        texto = _ajuda_do_comando("tags", 100, monkeypatch, capsys)
        assert texto.count("╭─ Opções") == 1 and "Opções ·" not in texto

    def test_opcao_desconhecida_vai_para_outras_e_nada_some(self):
        from qobuz_dl.home_screen import agrupar_opcoes

        opcoes = [
            ("--nova-opcao-futura", "x"),
            ("-q, --quality QUALIDADE", "q"),
            ("--no-zzz-tag", "z"),
            ("-h, --help", "ajuda"),
        ]
        grupos = dict(agrupar_opcoes(opcoes))
        assert [f for f, _ in grupos["Qualidade e execução"]] == [
            "-q, --quality QUALIDADE"
        ]
        assert [f for f, _ in grupos["Tags e metadados"]] == ["--no-zzz-tag"]
        assert [f for f, _ in grupos["Outras"]] == ["--nova-opcao-futura", "-h, --help"]
