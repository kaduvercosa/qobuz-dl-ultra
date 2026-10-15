"""UX: doctor em painéis, -h geral, escolha do modo do `tags` e erros com próxima ação."""

import argparse
import asyncio
import io
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
    @pytest.mark.parametrize("flag", ["-h", "--help"])
    def test_ajuda_sem_comando_mostra_os_paineis(self, monkeypatch, flag, tmp_path):
        monkeypatch.setattr(sys, "argv", ["qobuz-dl", flag])
        monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
        (tmp_path / "config.ini").write_text("[DEFAULT]\n")
        chamadas = []
        monkeypatch.setattr(cli, "_print_welcome_screen", lambda: chamadas.append(1))
        with pytest.raises(SystemExit) as saida:
            asyncio.run(cli._initial_checks())
        assert saida.value.code == 0 and chamadas == [1]

    def test_ajuda_de_comando_continua_com_argparse(self, monkeypatch, tmp_path):
        monkeypatch.setattr(sys, "argv", ["qobuz-dl", "tags", "-h"])
        monkeypatch.setattr(cli, "CONFIG_PATH", str(tmp_path))
        monkeypatch.setattr(cli, "CONFIG_FILE", str(tmp_path / "config.ini"))
        (tmp_path / "config.ini").write_text("[DEFAULT]\n")
        chamadas = []
        monkeypatch.setattr(cli, "_print_welcome_screen", lambda: chamadas.append(1))
        asyncio.run(cli._initial_checks())  # não sai
        assert chamadas == []
