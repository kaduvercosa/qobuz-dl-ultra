"""Testa qobuz_dl/color.py: detecção de capacidade de cor do terminal,
derivação de tons e leitura da cor de destaque (accent) do config.ini.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
O próprio color.py documenta dois bugs reais que já aconteceram aqui:

1. `colorama.init(autoreset=True)` substituía sys.stdout e ignorava
   NO_COLOR/FORCE_COLOR -- corrigido trocando por
   `just_fix_windows_console()`.
2. `OFF` usava `Style.DIM` em vez de `Style.RESET_ALL`, então todo
   `f"{GREEN}texto{OFF}"` no projeto (239 ocorrências) deixava o
   terminal esmaecido/colorido pra sempre depois da primeira cor.

Nenhum dos dois tinha um teste que travasse a regressão -- é exatamente
esse buraco que este arquivo fecha, junto com o resto da lógica pura do
módulo (que não dependia de nada disso pra ser coberta).

NOTA SOBRE COLOR_ON E AS CONSTANTES DERIVADAS (RED, GREEN, ACCENT, etc.)
-------------------------------------------------------------------------
`COLOR_ON` e tudo que depende dela (RED, GREEN, ACCENT, ACCENT_DARK...)
são decididos UMA vez, na importação do módulo -- de propósito, porque a
tela inicial é impressa antes do argparse rodar (ver comentário no
próprio color.py). Isso significa que não dá pra testar essas constantes
mudando `os.environ` depois que o módulo já foi importado: é preciso
`importlib.reload()` com o ambiente já ajustado. Os testes que precisam
disso usam o fixture `modulo_recarregado` abaixo; os que testam funções
puras (`_detect_color_capability`, `_darken`, `_load_accent_rgb`) não
precisam, porque essas leem o estado atual a cada chamada.
"""

import importlib
import os
import sys

import pytest

from qobuz_dl import color


# --------------------------------------------------------------------
# _detect_color_capability -- lida a cada chamada, não precisa de reload
# --------------------------------------------------------------------
class TestDetectColorCapability:
    def test_no_color_env_desliga_mesmo_com_tty(self, monkeypatch):
        # NO_COLOR (https://no-color.org/) tem que vencer qualquer outra
        # coisa, inclusive um terminal real -- é o primeiro check da função.
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert color._detect_color_capability() is False

    def test_flag_no_color_no_argv_desliga(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys, "argv", ["qobuz-dl", "dl", "--no-color", "URL"])
        assert color._detect_color_capability() is False

    def test_force_color_liga_mesmo_sem_tty(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])
        monkeypatch.setenv("FORCE_COLOR", "1")
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False, raising=False)
        assert color._detect_color_capability() is True

    def test_term_dumb_desliga_fora_do_windows(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])
        monkeypatch.setenv("TERM", "dumb")
        monkeypatch.setattr(color.os, "name", "posix")
        assert color._detect_color_capability() is False

    def test_sem_term_definido_tambem_desliga(self, monkeypatch):
        # TERM ausente cai no mesmo default ("") que TERM=dumb -- regressão
        # específica: `os.environ.get("TERM", "")` sem esse fallback
        # explodiria com KeyError em ambientes sem TERM (ex.: alguns
        # subprocessos do a-Shell).
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.delenv("TERM", raising=False)
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])
        monkeypatch.setattr(color.os, "name", "posix")
        assert color._detect_color_capability() is False

    def test_tty_de_verdade_liga_a_cor(self, monkeypatch):
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        assert color._detect_color_capability() is True

    def test_isatty_que_explode_nao_derruba_o_programa(self, monkeypatch):
        # Alguns wrappers de stdout (ex.: certos proxies de log) não
        # implementam isatty() corretamente e lançam. A função tem que
        # tratar isso como "sem TTY", não propagar a exceção.
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm")
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])

        def _explode():
            raise RuntimeError("stdout sem isatty")

        monkeypatch.setattr(sys.stdout, "isatty", _explode, raising=False)
        assert color._detect_color_capability() is False


# --------------------------------------------------------------------
# _darken -- função matemática pura
# --------------------------------------------------------------------
class TestDarken:
    def test_fator_padrao_reduz_proporcionalmente(self):
        assert color._darken((200, 100, 40)) == (110, 55, 22)

    def test_nunca_passa_de_255_nem_fica_negativo(self):
        # factor > 1 ou canais já baixos não podem estourar os limites de
        # um byte de cor -- o clamp é o que garante isso.
        assert color._darken((255, 255, 255), factor=2.0) == (255, 255, 255)
        assert color._darken((0, 0, 0), factor=0.55) == (0, 0, 0)

    def test_preto_continua_preto(self):
        assert color._darken((0, 0, 0)) == (0, 0, 0)


# --------------------------------------------------------------------
# _load_accent_rgb -- lê configparser, nunca deve derrubar o boot
# --------------------------------------------------------------------
class TestLoadAccentRgb:
    def _preparar_config(self, tmp_path, monkeypatch, conteudo: str):
        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        cfg_dir = tmp_path / "qobuz-dl"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "config.ini").write_text(conteudo, encoding="utf-8")

    def test_sem_config_cai_pro_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "nao-existe"))
        assert color._load_accent_rgb() == color._DEFAULT_ACCENT_RGB

    def test_accent_valido_e_lido(self, tmp_path, monkeypatch):
        self._preparar_config(
            tmp_path, monkeypatch, "[qobuz]\naccent_color = 10;20;30\n"
        )
        assert color._load_accent_rgb() == (10, 20, 30)

    def test_accent_ausente_na_secao_cai_pro_default(self, tmp_path, monkeypatch):
        self._preparar_config(tmp_path, monkeypatch, "[qobuz]\nemail = a@b.com\n")
        assert color._load_accent_rgb() == color._DEFAULT_ACCENT_RGB

    @pytest.mark.parametrize(
        "valor_invalido",
        [
            "lixo",  # não são números
            "10;20",  # faltando um canal
            "10;20;30;40",  # canal a mais
            "10;20;300",  # fora do range 0-255
            "-5;20;30",  # negativo
        ],
    )
    def test_accent_invalido_nao_derruba_e_cai_pro_default(
        self, tmp_path, monkeypatch, valor_invalido
    ):
        self._preparar_config(
            tmp_path, monkeypatch, f"[qobuz]\naccent_color = {valor_invalido}\n"
        )
        assert color._load_accent_rgb() == color._DEFAULT_ACCENT_RGB

    def test_config_ini_corrompido_nao_derruba_e_cai_pro_default(
        self, tmp_path, monkeypatch
    ):
        # Sem cabeçalho de seção nenhum -- configparser.MissingSectionHeaderError.
        self._preparar_config(tmp_path, monkeypatch, "isto nao e um ini valido")
        assert color._load_accent_rgb() == color._DEFAULT_ACCENT_RGB


# --------------------------------------------------------------------
# accent_preview -- responsividade por largura de terminal
# --------------------------------------------------------------------
class TestAccentPreview:
    def test_terminal_estreito_usa_modo_empilhado(self, monkeypatch):
        # NOTA: color.shutil é o MESMO objeto módulo que o shutil usado
        # pelo próprio pytest internamente (pra desenhar a barra de
        # progresso) -- não é uma cópia isolada. O pytest chama
        # `shutil.get_terminal_size(fallback=(80, 24))` com kwarg, então
        # o mock precisa aceitar *args/**kwargs, senão quebra o pytest
        # enquanto o mock estiver ativo (gera INTERNALERROR real, já visto
        # em execução).
        monkeypatch.setattr(color.shutil, "get_terminal_size", lambda *a, **k: (80, 24))
        resultado = color.accent_preview("\033[38;2;1;2;3m", "Teste")
        assert "Escuro:" in resultado and "Claro:" in resultado
        # Modo estreito quebra em duas linhas (uma pra cada amostra).
        assert resultado.count("\n") >= 2

    def test_terminal_largo_usa_modo_lado_a_lado(self, monkeypatch):
        monkeypatch.setattr(
            color.shutil, "get_terminal_size", lambda *a, **k: (140, 24)
        )
        resultado = color.accent_preview("\033[38;2;1;2;3m", "Teste")
        assert "Escuro:" in resultado and "Claro:" in resultado
        # Modo largo é uma linha só, lado a lado -- sem quebra dupla.
        assert "\n\n" not in resultado

    def test_recuo_nunca_fica_negativo_em_tela_minuscula(self, monkeypatch):
        # cols - 26 pode ficar bem negativo numa tela muito estreita; o
        # `max(2, ...)` tem que segurar isso. Sem o clamp, " " * negativo
        # não quebra (Python trata como zero), mas o recuo documentado
        # como "piso de 2" deixaria de existir silenciosamente.
        monkeypatch.setattr(color.shutil, "get_terminal_size", lambda *a, **k: (20, 24))
        resultado = color.accent_preview("\033[38;2;1;2;3m")
        indent_line = next(l for l in resultado.split("\n") if "Escuro:" in l)
        recuo = len(indent_line) - len(indent_line.lstrip(" "))
        assert recuo >= 2


# --------------------------------------------------------------------
# COLOR_ON e constantes derivadas -- precisam de reload com env ajustado
# --------------------------------------------------------------------
@pytest.fixture
def modulo_recarregado(monkeypatch):
    """Recarrega qobuz_dl.color do zero, com o ambiente já configurado
    pelo teste (NO_COLOR, FORCE_COLOR, argv). Necessário porque COLOR_ON
    e as constantes de cor são calculadas na importação, não a cada uso.
    """

    def _recarregar():
        if "qobuz_dl.color" in sys.modules:
            del sys.modules["qobuz_dl.color"]
        return importlib.import_module("qobuz_dl.color")

    yield _recarregar

    # Sempre deixa o módulo real (sem NO_COLOR forçado neste teste)
    # recarregado no estado normal pro resto da suíte não herdar o mock.
    if "qobuz_dl.color" in sys.modules:
        del sys.modules["qobuz_dl.color"]
    importlib.import_module("qobuz_dl.color")


class TestColorOnRegressao:
    def test_no_color_zera_as_constantes_de_cor(self, monkeypatch, modulo_recarregado):
        # Regressão do bug #1 do docstring: com NO_COLOR, RED/GREEN/OFF/etc.
        # têm que virar string vazia -- não só "funcionar", mas
        # literalmente não emitir nenhum código ANSI.
        monkeypatch.setenv("NO_COLOR", "1")
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])
        mod = modulo_recarregado()
        assert mod.COLOR_ON is False
        assert mod.RED == ""
        assert mod.GREEN == ""
        assert mod.OFF == ""

    def test_off_e_reset_all_no_e_dim(self, monkeypatch, modulo_recarregado):
        # Regressão do bug #2 do docstring: OFF precisa ser um TERMINADOR
        # de verdade (RESET_ALL), não Style.DIM -- senão o texto depois de
        # {OFF} continua esmaecido/colorido.
        monkeypatch.delenv("NO_COLOR", raising=False)
        monkeypatch.delenv("FORCE_COLOR", raising=False)
        monkeypatch.setenv("TERM", "xterm-256color")
        monkeypatch.setattr(sys, "argv", ["qobuz-dl"])
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
        mod = modulo_recarregado()
        assert mod.COLOR_ON is True
        from colorama import Style

        assert mod.OFF == Style.RESET_ALL
        assert mod.OFF != Style.DIM


# ---------------------------------------------------------------------
# _find_config_file
# ---------------------------------------------------------------------
class TestFindConfigFile:
    """CONFIG_DIR vem sempre preenchido pelo conftest.py pra suíte inteira
    -- então `if not config_dir:` nunca era exercitado por NENHUM outro
    teste do projeto. Aqui removemos CONFIG_DIR de propósito pra cobrir
    os 4 caminhos que só acontecem quando ele está ausente de verdade
    (o que acontece em uso real: CONFIG_DIR só existe pra viabilizar os
    testes, não é uma variável que o programa normalmente espera)."""

    def _limpar_env(self, monkeypatch):
        for chave in ("QOBUZ_DL_IOS_HOME", "CONFIG_DIR", "HOME", "APPDATA"):
            monkeypatch.delenv(chave, raising=False)

    def test_com_qobuz_dl_ios_home_usa_ele_direto(self, monkeypatch):
        self._limpar_env(monkeypatch)
        monkeypatch.setenv("QOBUZ_DL_IOS_HOME", "/caminho/ios")

        resultado = color._find_config_file()

        assert resultado == os.path.join(
            "/caminho/ios", "qobuz-dl", "config.ini"
        )

    def test_sem_ios_home_usa_home_barra_ponto_config(self, monkeypatch):
        self._limpar_env(monkeypatch)
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setenv("HOME", "/home/usuario")

        resultado = color._find_config_file()

        assert resultado == os.path.join(
            "/home/usuario", ".config", "qobuz-dl", "config.ini"
        )

    def test_home_dentro_de_containers_ios_usa_pasta_documents(self, monkeypatch):
        # Sandbox real de app iOS (quando QOBUZ_DL_IOS_HOME não foi
        # setado por algum motivo) -- HOME aponta pra dentro de
        # Containers/Data/Application, caso especial documentado no
        # próprio código.
        self._limpar_env(monkeypatch)
        monkeypatch.setattr(os, "name", "posix")
        monkeypatch.setenv(
            "HOME",
            "/private/var/mobile/Containers/Data/Application/ABC-123",
        )

        resultado = color._find_config_file()

        assert resultado == os.path.join(
            "/private/var/mobile/Containers/Data/Application/ABC-123",
            "Documents",
            "qobuz-dl",
            "config.ini",
        )

    def test_windows_usa_appdata(self, monkeypatch):
        self._limpar_env(monkeypatch)
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setenv("APPDATA", "C:\\Users\\Fulano\\AppData\\Roaming")

        resultado = color._find_config_file()

        assert resultado == os.path.join(
            "C:\\Users\\Fulano\\AppData\\Roaming", "qobuz-dl", "config.ini"
        )
