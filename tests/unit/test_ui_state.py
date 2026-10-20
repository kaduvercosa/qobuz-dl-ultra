"""Testa o estado global e a lógica de saída de `qobuz_dl/ui.py`.

Complementa `test_ui_wrap.py`, `test_terminal_width.py` e
`test_ui_concurrency.py` (em tests/ui/), que cobrem quebra de linha, largura e
concorrência. Aqui entram: largura/layout por COLUMNS, detecção de cor e
unicode com cache, `configure`, modos quiet/verbose, mensagens com tag e a
ponte com o módulo `logging`.

`ui` guarda estado em variáveis de módulo; toda mudança é feita com
`monkeypatch` para NÃO vazar para os outros testes.
"""

import logging
import sys
import types

import pytest

from qobuz_dl import ui

pytestmark = pytest.mark.unit


@pytest.fixture
def saida(monkeypatch):
    """Intercepta a escrita final (`_write_locked`) e devolve as linhas."""
    linhas = []
    monkeypatch.setattr(ui, "_write_locked", lambda text, end: linhas.append(str(text)))
    return linhas


@pytest.fixture
def estado_limpo(monkeypatch):
    """Zera o estado global da UI para o teste e restaura ao final."""
    for nome, valor in (
        ("_color_enabled", None),
        ("_unicode_enabled", None),
        ("_quiet", False),
        ("_verbose", False),
    ):
        monkeypatch.setattr(ui, nome, valor)


def _sem_terminal(fd=None):
    raise OSError("sem terminal")


@pytest.fixture
def sem_tty(monkeypatch):
    """Garante que `raw_width()` não leia o terminal real de quem roda a suíte.

    `raw_width()` prefere o tamanho do TTY ao `COLUMNS`. Com a saída capturada
    (pytest padrão) isso não importa, mas com `pytest -s` num terminal os testes
    de largura passariam a depender da janela de quem os executa.
    """
    monkeypatch.setattr(ui.os, "get_terminal_size", _sem_terminal)


@pytest.fixture
def colunas(monkeypatch, sem_tty):
    def _define(n):
        monkeypatch.setenv("COLUMNS", str(n))

    return _define


# ---------------------------------------------------------------------------
# Largura e layout
# ---------------------------------------------------------------------------
class TestRawWidth:
    def test_usa_columns_quando_definida(self, colunas):
        colunas(123)
        assert ui.raw_width() == 123

    @pytest.mark.parametrize("valor", ["0", "-5", "abc", ""])
    def test_columns_invalida_cai_para_o_terminal(self, monkeypatch, sem_tty, valor):
        monkeypatch.setenv("COLUMNS", valor)
        monkeypatch.setattr(
            ui.shutil,
            "get_terminal_size",
            lambda fallback: types.SimpleNamespace(columns=77),
        )
        assert ui.raw_width() == 77

    def test_erro_ao_consultar_o_terminal_usa_o_fallback(self, monkeypatch, sem_tty):
        monkeypatch.delenv("COLUMNS", raising=False)

        def explode(fallback):
            raise OSError("sem tty")

        monkeypatch.setattr(ui.shutil, "get_terminal_size", explode)
        assert ui.raw_width() == ui.FALLBACK[0]


class _SaidaFalsa:
    """`sys.stdout` falso para simular saída em terminal ou redirecionada."""

    def __init__(self, tty=True, fileno=7):
        self._tty, self._fileno = tty, fileno

    def isatty(self):
        return self._tty

    def fileno(self):
        if isinstance(self._fileno, Exception):
            raise self._fileno
        return self._fileno


class TestRawWidthComTerminalVivo:
    """O tamanho real do TTY vence `COLUMNS` (redimensionar reflete na hora)."""

    def test_tty_vence_columns(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "50")
        monkeypatch.setattr(ui.sys, "stdout", _SaidaFalsa(tty=True))
        pedidos = []

        def tamanho(fd):
            pedidos.append(fd)
            return types.SimpleNamespace(columns=133)

        monkeypatch.setattr(ui.os, "get_terminal_size", tamanho)
        assert ui.raw_width() == 133
        assert pedidos == [7]

    def test_saida_redirecionada_respeita_columns(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "64")
        monkeypatch.setattr(ui.sys, "stdout", _SaidaFalsa(tty=False))
        monkeypatch.setattr(
            ui.os, "get_terminal_size", lambda fd: types.SimpleNamespace(columns=999)
        )
        assert ui.raw_width() == 64

    @pytest.mark.parametrize(
        "erro", [OSError("sem tty"), ValueError("fd fechado"), AttributeError("x")]
    )
    def test_falha_ao_ler_o_tty_cai_para_columns(self, monkeypatch, erro):
        monkeypatch.setenv("COLUMNS", "88")
        monkeypatch.setattr(ui.sys, "stdout", _SaidaFalsa(tty=True))

        def explode(fd):
            raise erro

        monkeypatch.setattr(ui.os, "get_terminal_size", explode)
        assert ui.raw_width() == 88

    def test_stdout_sem_fileno_utilizavel_cai_para_columns(self, monkeypatch):
        monkeypatch.setenv("COLUMNS", "70")
        monkeypatch.setattr(
            ui.sys, "stdout", _SaidaFalsa(tty=True, fileno=ValueError("sem fileno"))
        )
        assert ui.raw_width() == 70

    def test_stdout_sem_isatty_nao_quebra(self, monkeypatch, sem_tty):
        monkeypatch.setenv("COLUMNS", "55")
        monkeypatch.setattr(ui.sys, "stdout", object())
        assert ui.raw_width() == 55

    def test_largura_do_tty_e_limitada_por_width_como_qualquer_outra(self, monkeypatch):
        monkeypatch.setattr(ui.sys, "stdout", _SaidaFalsa(tty=True))
        monkeypatch.setattr(
            ui.os, "get_terminal_size", lambda fd: types.SimpleNamespace(columns=500)
        )
        assert ui.width() == ui.MAX_WIDTH


class TestWidth:
    @pytest.mark.parametrize(
        "colunas_reais, esperado",
        [(10, ui.MIN_WIDTH), (80, 80), (500, ui.MAX_WIDTH)],
    )
    def test_piso_e_teto(self, colunas, colunas_reais, esperado):
        colunas(colunas_reais)
        assert ui.width() == esperado

    def test_limites_personalizados(self, colunas):
        colunas(80)
        assert ui.width(max_width=60, min_width=40) == 60
        assert ui.width(max_width=200, min_width=100) == 100


class TestLayout:
    @pytest.mark.parametrize(
        "cols, esperado",
        [
            (30, ui.LAYOUT_NARROW),
            (ui.NARROW - 1, ui.LAYOUT_NARROW),
            (ui.NARROW, ui.LAYOUT_MEDIUM),
            (ui.MEDIUM - 1, ui.LAYOUT_MEDIUM),
            (ui.MEDIUM, ui.LAYOUT_WIDE),
            (300, ui.LAYOUT_WIDE),
        ],
    )
    def test_faixas(self, colunas, cols, esperado):
        colunas(cols)
        assert ui.layout() == esperado

    def test_is_narrow(self, colunas):
        colunas(40)
        assert ui.is_narrow() is True
        colunas(120)
        assert ui.is_narrow() is False


class TestProgressNcols:
    @pytest.mark.parametrize(
        "cols, esperado", [(10, 20), (50, 49), (80, 79), (500, ui.MAX_WIDTH)]
    )
    def test_ncols(self, colunas, cols, esperado):
        colunas(cols)
        assert ui.progress_ncols() == esperado


# ---------------------------------------------------------------------------
# Cor e unicode
# ---------------------------------------------------------------------------
class _Stdout:
    def __init__(self, encoding):
        self.encoding = encoding


class TestDetectUnicode:
    @pytest.mark.parametrize(
        "encoding, esperado",
        [
            ("utf-8", True),
            ("UTF-8", True),
            ("utf8", True),
            ("ascii", False),
            ("cp1252", False),
            ("", False),
            (None, False),
        ],
    )
    def test_pela_codificacao_do_stdout(self, monkeypatch, encoding, esperado):
        monkeypatch.setattr(sys, "stdout", _Stdout(encoding))
        assert ui._detect_unicode() is esperado

    def test_stdout_sem_atributo_encoding(self, monkeypatch):
        monkeypatch.setattr(sys, "stdout", object())
        assert ui._detect_unicode() is False


class TestCacheDeCapacidades:
    def test_cor_e_calculada_uma_vez(self, estado_limpo, monkeypatch):
        chamadas = []
        monkeypatch.setattr(ui, "_detect_color", lambda: chamadas.append(1) or True)
        assert ui.color_enabled() is True
        assert ui.color_enabled() is True
        assert len(chamadas) == 1

    def test_unicode_e_calculado_uma_vez(self, estado_limpo, monkeypatch):
        chamadas = []
        monkeypatch.setattr(ui, "_detect_unicode", lambda: chamadas.append(1) or False)
        assert ui.unicode_enabled() is False
        assert ui.unicode_enabled() is False
        assert len(chamadas) == 1

    def test_detect_color_respeita_no_color(self, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        assert not ui._detect_color()


class TestConfigure:
    def test_none_mantem_o_valor_atual(self, estado_limpo, monkeypatch):
        monkeypatch.setattr(ui, "_quiet", True)
        monkeypatch.setattr(ui, "_verbose", True)
        ui.configure()
        assert ui._quiet is True and ui._verbose is True

    def test_define_cada_flag(self, estado_limpo):
        ui.configure(quiet=True, verbose=True, color=False, unicode=False)
        assert (ui._quiet, ui._verbose) == (True, True)
        assert ui.color_enabled() is False
        assert ui.unicode_enabled() is False

    def test_valores_viram_bool(self, estado_limpo):
        ui.configure(quiet=1, verbose=0, color=1, unicode="x")
        assert ui._quiet is True and ui._verbose is False
        assert ui._color_enabled is True and ui._unicode_enabled is True

    def test_c_so_devolve_o_codigo_com_cor_ligada(self, estado_limpo):
        ui.configure(color=True)
        assert ui.c("\x1b[31m") == "\x1b[31m"
        ui.configure(color=False)
        assert ui.c("\x1b[31m") == ""


class TestGlifos:
    def test_unicode_ligado(self, estado_limpo):
        ui.configure(unicode=True)
        assert (ui.heavy_bar_char(), ui.light_bar_char(), ui.block_char()) == (
            "\u2501",
            "\u2500",
            "\u2588",
        )

    def test_unicode_desligado_cai_para_ascii(self, estado_limpo):
        ui.configure(unicode=False)
        assert (ui.heavy_bar_char(), ui.light_bar_char(), ui.block_char()) == (
            "=",
            "-",
            "#",
        )

    def test_glyph(self, estado_limpo):
        ui.configure(unicode=True)
        assert ui._glyph("A", "a") == "A"
        ui.configure(unicode=False)
        assert ui._glyph("A", "a") == "a"


# ---------------------------------------------------------------------------
# Saída: quiet / verbose / always
# ---------------------------------------------------------------------------
class TestEmit:
    def test_emit_escreve(self, estado_limpo, saida):
        ui.emit("oi")
        assert saida == ["oi"]

    def test_emit_respeita_quiet(self, estado_limpo, saida):
        ui.configure(quiet=True)
        ui.emit("oi")
        assert saida == []

    def test_emit_always_ignora_quiet(self, estado_limpo, saida):
        ui.configure(quiet=True)
        ui.emit_always("importante")
        assert saida == ["importante"]

    def test_debug_so_com_verbose(self, estado_limpo, saida):
        ui.debug("segredo")
        assert saida == []
        ui.configure(verbose=True, color=False)
        ui.debug("segredo")
        assert saida == ["[debug] segredo"]

    def test_blank_emite_linha_vazia(self, estado_limpo, saida):
        ui.blank()
        assert saida == [""]

    def test_info_nao_tem_tag(self, estado_limpo, saida):
        ui.info("neutro")
        assert saida == ["neutro"]


class TestMensagensComTag:
    @pytest.mark.parametrize(
        "funcao, tag",
        [
            ("ok", "[+]"),
            ("step", "[*]"),
            ("warn", "[!]"),
            ("error", "[!]"),
            ("skip", "[-]"),
        ],
    )
    def test_prefixo(self, estado_limpo, saida, funcao, tag):
        ui.configure(color=False)
        getattr(ui, funcao)("mensagem")
        assert saida == [f"{tag} mensagem"]

    def test_warn_e_error_ignoram_quiet(self, estado_limpo, saida):
        ui.configure(quiet=True, color=False)
        ui.warn("aviso")
        ui.error("erro")
        assert saida == ["[!] aviso", "[!] erro"]

    def test_ok_step_e_skip_respeitam_quiet(self, estado_limpo, saida):
        ui.configure(quiet=True, color=False)
        ui.ok("a")
        ui.step("b")
        ui.skip("c")
        assert saida == []

    def test_mensagem_longa_quebra_com_recuo_de_continuacao(
        self, estado_limpo, saida, colunas
    ):
        colunas(40)
        ui.configure(color=False)
        ui.warn("palavra " * 12)
        assert len(saida) > 1
        assert saida[0].startswith("[!] ")
        assert all(l.startswith("    ") for l in saida[1:])
        assert all(len(l) <= 40 for l in saida)

    def test_cor_envolve_cada_linha_e_fecha(self, estado_limpo, saida, monkeypatch):
        # Com NO_COLOR as constantes de cor são vazias; fixa valores reais
        # para provar que a linha inteira é colorida e fechada.
        monkeypatch.setattr(ui, "SUCCESS", "<verde>")
        monkeypatch.setattr(ui, "RESET", "<fim>")
        ui.configure(color=True)
        ui.ok("x")
        assert saida == ["<verde>[+] x<fim>"]

    def test_cor_desligada_nao_deixa_codigos(self, estado_limpo, saida, monkeypatch):
        monkeypatch.setattr(ui, "SUCCESS", "<verde>")
        monkeypatch.setattr(ui, "RESET", "<fim>")
        ui.configure(color=False)
        ui.ok("x")
        assert saida == ["[+] x"]


class TestDetail:
    def test_recuo_padrao(self, estado_limpo, saida):
        ui.configure(color=False)
        ui.detail("sub")
        assert saida == ["    sub"]

    def test_recuo_personalizado(self, estado_limpo, saida):
        ui.configure(color=False)
        ui.detail("sub", indent=2)
        assert saida == ["  sub"]

    def test_detail_longo_quebra_respeitando_o_recuo(
        self, estado_limpo, saida, colunas
    ):
        colunas(40)
        ui.configure(color=False)
        ui.detail("palavra " * 10)
        assert len(saida) > 1
        assert all(l.startswith("    ") and len(l) <= 40 for l in saida)


# ---------------------------------------------------------------------------
# Quebra, truncagem, gauge
# ---------------------------------------------------------------------------
class TestWrapLines:
    def test_texto_curto_uma_linha(self):
        assert ui._wrap_lines("oi", 40) == ["oi"]

    def test_texto_vazio_devolve_uma_linha_vazia(self):
        assert ui._wrap_lines("", 40) == [""]

    def test_piso_de_12_colunas(self):
        assert all(len(l) <= 12 for l in ui._wrap_lines("palavra " * 5, 3))

    def test_aceita_nao_string(self):
        assert ui._wrap_lines(12345, 40) == ["12345"]


class TestTruncate:
    @pytest.mark.parametrize(
        "texto, limite, esperado",
        [
            ("curto", 10, "curto"),
            ("exato", 5, "exato"),
            ("textolongo", 8, "texto..."),
            ("abcdef", 3, "abc"),
            ("abcdef", 2, "ab"),
            (123456, 5, "12..."),
        ],
    )
    def test_casos(self, texto, limite, esperado):
        assert ui.truncate(texto, limite) == esperado


class TestBarGauge:
    def test_valor_zero_nao_desenha_blocos(self, estado_limpo):
        ui.configure(color=False, unicode=False)
        assert ui.bar_gauge(0, 10, max_blocks=10) == ""

    def test_proporcional_ao_pico(self, estado_limpo):
        ui.configure(color=False, unicode=False)
        assert ui.bar_gauge(5, 10, max_blocks=10) == "#" * 5
        assert ui.bar_gauge(10, 10, max_blocks=10) == "#" * 10

    def test_valor_pequeno_tem_pelo_menos_um_bloco(self, estado_limpo):
        ui.configure(color=False, unicode=False)
        assert ui.bar_gauge(1, 1000, max_blocks=10) == "#"

    def test_pico_zero_nao_divide_por_zero(self, estado_limpo):
        ui.configure(color=False, unicode=False)
        assert ui.bar_gauge(3, 0, max_blocks=4)

    def test_tamanho_padrao_depende_da_largura(self, estado_limpo, colunas):
        ui.configure(color=False, unicode=False)
        colunas(40)
        assert len(ui.bar_gauge(10, 10)) == 6  # piso
        colunas(100)
        assert len(ui.bar_gauge(10, 10)) == 24  # teto


class TestKvEHeader:
    def test_kv_alinhado_em_tela_larga(self, estado_limpo, saida, colunas):
        colunas(100)
        ui.configure(color=False)
        ui.kv("Rotulo", "valor", label_width=10)
        assert saida == ["  Rotulo      valor"]

    def test_kv_estreito_fica_na_mesma_linha_quando_cabe(
        self, estado_limpo, saida, colunas
    ):
        colunas(40)
        ui.configure(color=False)
        ui.kv("Rotulo", "valor")
        assert saida == ["  Rotulo: valor"]

    def test_kv_estreito_empilha_valor_longo(self, estado_limpo, saida, colunas):
        colunas(40)
        ui.configure(color=False)
        ui.kv("Rotulo", "palavra " * 10)
        assert saida[0] == "  Rotulo:"
        assert len(saida) > 2 and all(l.startswith("    ") for l in saida[1:])

    def test_kv_largo_com_valor_longo_quebra_em_bloco(
        self, estado_limpo, saida, colunas
    ):
        colunas(100)
        ui.configure(color=False)
        ui.kv("Rotulo", "palavra " * 20, label_width=10)
        assert saida[0] == "  Rotulo"
        assert len(saida) > 2

    def test_header_traz_tipo_e_pares_em_maiusculas(self, estado_limpo, saida, colunas):
        colunas(100)
        ui.configure(color=False, unicode=False)
        ui.header("ALBUM", [("titulo", "Disco"), ("artista", "Banda")])
        texto = "\n".join(saida)
        linhas = [ln for ln in texto.splitlines() if ln.strip()]
        # Caixa fechada em ASCII: tipo embutido na borda de cima.
        assert linhas[0].startswith("+- ALBUM -")
        assert linhas[-1].startswith("+--") and linhas[-1].endswith("--+")
        assert "TITULO" in texto and "Disco" in texto
        assert "ARTISTA" in texto and "Banda" in texto
        assert all(ln.startswith(("+", "|")) for ln in linhas)

    def test_header_sem_linhas(self, estado_limpo, saida):
        ui.configure(color=False)
        ui.header("VAZIO", [])
        assert "VAZIO" in "\n".join(saida)


class TestRuleBannerSection:
    def test_rule_usa_a_largura(self, estado_limpo, saida, colunas):
        colunas(50)
        ui.configure(color=False, unicode=False)
        ui.rule()
        assert saida == ["=" * 50]

    def test_rule_personalizada(self, estado_limpo, saida):
        ui.configure(color=False)
        ui.rule(char="*", cols=7)
        assert saida == ["*" * 7]

    def test_banner_centraliza_o_titulo(self, estado_limpo, saida):
        ui.configure(color=False, unicode=False)
        ui.banner("TITULO", cols=20)
        texto = "\n".join(saida)
        assert "=" * 20 in texto
        assert "TITULO".center(20) in texto

    def test_section(self, estado_limpo, saida):
        ui.configure(color=False)
        ui.section("Bloco")
        assert saida == ["  Bloco"]


# ---------------------------------------------------------------------------
# Escrita real (_write_locked)
# ---------------------------------------------------------------------------
class TestWriteLocked:
    def test_cai_para_print_quando_tqdm_falha(self, monkeypatch, capsys):
        falso = types.ModuleType("tqdm")

        class T:
            @staticmethod
            def write(*a, **k):
                raise RuntimeError("tqdm quebrado")

        falso.tqdm = T
        monkeypatch.setitem(sys.modules, "tqdm", falso)
        ui._write_locked("olá", "\n")
        assert capsys.readouterr().out == "olá\n"

    def test_substitui_caracteres_que_o_terminal_nao_suporta(self, monkeypatch):
        falso = types.ModuleType("tqdm")

        class T:
            @staticmethod
            def write(*a, **k):
                raise RuntimeError("tqdm quebrado")

        falso.tqdm = T
        monkeypatch.setitem(sys.modules, "tqdm", falso)
        escritos = []

        class Terminal:
            encoding = "ascii"

            def write(self, s):
                if any(ord(ch) > 127 for ch in s):
                    raise UnicodeEncodeError("ascii", s, 0, 1, "fora do alcance")
                escritos.append(s)

            def flush(self):
                pass

        monkeypatch.setattr(sys, "stdout", Terminal())
        ui._write_locked("coração", "\n")  # não pode levantar
        assert "".join(escritos).startswith("cora")


# ---------------------------------------------------------------------------
# Ponte com o logging
# ---------------------------------------------------------------------------
@pytest.fixture
def logging_restaurado():
    raiz = logging.getLogger()
    handlers, nivel = list(raiz.handlers), raiz.level
    ruidosos = {
        n: logging.getLogger(n).level
        for n in ("httpx", "httpcore", "urllib3", "asyncio", "PIL")
    }
    yield raiz
    for h in list(raiz.handlers):
        raiz.removeHandler(h)
    for h in handlers:
        raiz.addHandler(h)
    raiz.setLevel(nivel)
    for n, v in ruidosos.items():
        logging.getLogger(n).setLevel(v)


def _registro(nivel, msg="mensagem"):
    return logging.LogRecord("t", nivel, __file__, 1, msg, None, None)


class TestTqdmLoggingHandler:
    def test_info_respeita_quiet(self, estado_limpo, saida):
        ui.configure(quiet=True)
        ui.TqdmLoggingHandler().emit(_registro(logging.INFO))
        assert saida == []

    def test_warning_sobrevive_ao_quiet(self, estado_limpo, saida):
        ui.configure(quiet=True)
        ui.TqdmLoggingHandler().emit(_registro(logging.WARNING, "aviso"))
        assert saida == ["aviso"]

    def test_bypass_quiet_deixa_tudo_passar(self, estado_limpo, saida):
        ui.configure(quiet=True)
        ui.TqdmLoggingHandler(bypass_quiet=True).emit(_registro(logging.DEBUG, "dbg"))
        assert saida == ["dbg"]

    def test_info_sem_quiet(self, estado_limpo, saida):
        ui.TqdmLoggingHandler().emit(_registro(logging.INFO, "info"))
        assert saida == ["info"]

    def test_erro_de_formatacao_nao_propaga(self, estado_limpo, saida, monkeypatch):
        h = ui.TqdmLoggingHandler()
        monkeypatch.setattr(
            h, "format", lambda r: (_ for _ in ()).throw(ValueError("x"))
        )
        monkeypatch.setattr(h, "handleError", lambda r: saida.append("tratado"))
        h.emit(_registro(logging.INFO))
        assert saida == ["tratado"]


class TestInstallLogging:
    def test_nivel_padrao_info(self, estado_limpo, logging_restaurado):
        h = ui.install_logging()
        assert logging_restaurado.level == logging.INFO
        assert logging_restaurado.handlers == [h]
        assert h.bypass_quiet is False

    def test_quiet_sobe_para_warning(self, estado_limpo, logging_restaurado):
        ui.configure(quiet=True)
        ui.install_logging()
        assert logging_restaurado.level == logging.WARNING

    def test_verbose_desce_para_debug(self, estado_limpo, logging_restaurado):
        ui.configure(verbose=True)
        ui.install_logging()
        assert logging_restaurado.level == logging.DEBUG

    def test_nivel_explicito_vence_e_libera_o_quiet(
        self, estado_limpo, logging_restaurado
    ):
        ui.configure(quiet=True)
        h = ui.install_logging(level=logging.DEBUG)
        assert logging_restaurado.level == logging.DEBUG
        assert h.bypass_quiet is True

    def test_substitui_handlers_existentes(self, estado_limpo, logging_restaurado):
        logging_restaurado.addHandler(logging.NullHandler())
        ui.install_logging()
        assert len(logging_restaurado.handlers) == 1
        assert isinstance(logging_restaurado.handlers[0], ui.TqdmLoggingHandler)

    def test_bibliotecas_ruidosas_ficam_em_warning_no_verbose(
        self, estado_limpo, logging_restaurado
    ):
        ui.configure(verbose=True)
        ui.install_logging()
        for nome in ("httpx", "httpcore", "urllib3", "asyncio", "PIL"):
            assert logging.getLogger(nome).level >= logging.WARNING


# ---------------------------------------------------------------------------
# Caixas (cabeçalho e resumos), árvore
# ---------------------------------------------------------------------------
def _linhas_da_caixa(saida):
    return [ln for ln in "\n".join(saida).splitlines() if ln.strip()]


class TestBox:
    @pytest.mark.parametrize("cols", [32, 40, 80, 120])
    @pytest.mark.parametrize("unicode_on", [True, False])
    def test_todas_as_linhas_tem_a_mesma_largura(
        self, estado_limpo, saida, colunas, cols, unicode_on
    ):
        colunas(cols)
        ui.configure(color=False, unicode=unicode_on)
        ui.header(
            "ÁLBUM",
            [
                ("Álbum", "Random Access Memories " * 4),
                ("Artista", "Daft Punk"),
                ("Qualidade", "FLAC (24bit/88.2kHz)"),
            ],
        )
        larguras = {ui._cell_width(ln) for ln in _linhas_da_caixa(saida)}
        assert len(larguras) == 1
        assert larguras.pop() <= max(cols, ui.BOX_MIN)

    def test_cjk_e_emoji_nao_desalinham_a_borda(self, estado_limpo, saida, colunas):
        colunas(60)
        ui.configure(color=False, unicode=True)
        ui.header("ÁLBUM", [("Álbum", "宇多田ヒカル -- 初恋 🌁"), ("Ano", "1999")])
        larguras = {ui._cell_width(ln) for ln in _linhas_da_caixa(saida)}
        assert len(larguras) == 1

    def test_ansi_nao_conta_na_largura(self, estado_limpo, saida, colunas):
        colunas(50)
        ui.configure(color=False, unicode=False)
        ui.emit_box("T", ["\x1b[32mverde\x1b[0m", "plano"])
        larguras = {ui._cell_width(ln) for ln in _linhas_da_caixa(saida)}
        assert len(larguras) == 1

    def test_valor_longo_quebra_sem_cortar(self, estado_limpo, saida, colunas):
        colunas(40)
        ui.configure(color=False, unicode=False)
        valor = "alpha bravo charlie delta echo foxtrot golf hotel india"
        ui.header("FAIXA", [("Faixa", valor)])
        corpo = " ".join(ln.strip("|").strip() for ln in _linhas_da_caixa(saida)[1:-1])
        assert corpo.replace("FAIXA", "", 1).split() == valor.split()
        assert "…" not in "\n".join(saida)

    def test_largura_respeita_teto_e_piso(self, estado_limpo, colunas):
        colunas(300)
        assert ui.box_width() == ui.BOX_MAX
        colunas(10)
        assert ui.box_width() >= ui.BOX_MIN

    def test_rule_vira_divisoria(self, estado_limpo, colunas):
        colunas(50)
        ui.configure(color=False, unicode=False)
        linhas = ui.box("T", ["a", ui.BOX_RULE, "b"])
        assert linhas[2].startswith("+-") and linhas[2].endswith("-+")
        assert linhas[1].startswith("|") and linhas[3].startswith("|")

    def test_estilo_por_variavel_de_ambiente(self, estado_limpo, monkeypatch, colunas):
        colunas(50)
        ui.configure(color=False, unicode=True)
        monkeypatch.setenv("QOBUZ_DL_BOX", "double")
        assert ui.box("T", ["x"])[0].startswith("\u2554")
        monkeypatch.setenv("QOBUZ_DL_BOX", "inexistente")
        assert ui.box("T", ["x"])[0].startswith("\u256d")
        monkeypatch.setenv("QOBUZ_DL_BOX", "ascii")
        assert ui.box("T", ["x"])[0].startswith("+")

    def test_sem_unicode_ignora_o_estilo_pedido(self, estado_limpo, monkeypatch):
        ui.configure(color=False, unicode=False)
        monkeypatch.setenv("QOBUZ_DL_BOX", "double")
        assert ui.box_style_name() == "ascii"


class TestSummary:
    def test_valor_alinhado_a_direita(self, estado_limpo, saida, colunas):
        colunas(60)
        ui.configure(color=False, unicode=False)
        ui.summary("RESUMO", [("Baixadas", "11/12", "ok")])
        linha = [ln for ln in _linhas_da_caixa(saida) if "Baixadas" in ln][0]
        assert linha.startswith("| + Baixadas")
        assert linha.endswith("11/12 |")

    def test_notas_ficam_abaixo_de_uma_divisoria(self, estado_limpo, saida, colunas):
        colunas(60)
        ui.configure(color=False, unicode=False)
        ui.summary("RESUMO", [("Baixadas", "1/1", "ok")], notes=["veja .missing.txt"])
        linhas = _linhas_da_caixa(saida)
        assert any(ln.startswith("+-") for ln in linhas[1:-1])
        assert "veja .missing.txt" in linhas[-2]

    def test_downloads_omite_linhas_zeradas(self, estado_limpo, saida, colunas):
        colunas(60)
        ui.configure(color=False, unicode=False)
        ui.summary_downloads("DO ÁLBUM", "Disco", 12, 12)
        texto = "\n".join(saida)
        assert "12/12" in texto and "Disco" in texto
        assert "Puladas" not in texto and "Falhas" not in texto

    def test_downloads_mostra_puladas_e_falhas(self, estado_limpo, saida, colunas):
        colunas(60)
        ui.configure(color=False, unicode=False)
        ui.summary_downloads("DA PLAYLIST", "Mix", 8, 12, skipped=3, failed=1)
        texto = "\n".join(saida)
        assert "8/12" in texto and "Puladas" in texto and "Falhas" in texto

    def test_nome_longo_quebra_dentro_da_caixa_sem_cortar(
        self, estado_limpo, saida, colunas
    ):
        colunas(44)
        ui.configure(color=False, unicode=True)
        nome = "Uma playlist com um nome realmente muito comprido de propósito"
        ui.summary_downloads("DA PLAYLIST", nome, 3, 4, skipped=1)
        linhas = _linhas_da_caixa(saida)
        assert len({ui._cell_width(ln) for ln in linhas}) == 1
        corpo = " ".join(ln.strip("│").strip() for ln in linhas[1:3])
        assert "…" not in corpo and "..." not in "\n".join(saida)
        assert nome.split()[0] in linhas[1] and nome.split()[-1] in "\n".join(
            linhas[1:4]
        )

    def test_cores_fixas_por_significado(self, estado_limpo, saida, colunas):
        colunas(60)
        ui.configure(color=True, unicode=True)
        ui.summary("R", [("a", "1", "ok"), ("b", "2", "warn"), ("c", "3", "err")])
        texto = "\n".join(saida)
        assert ui.SUCCESS in texto and ui.WARNING in texto and ui.ERROR in texto


class TestTree:
    def test_ramos_unicode(self, estado_limpo):
        ui.configure(unicode=True)
        assert ui.tree_last() == "\u2514\u2500"
        assert ui.tree_mid() == "\u251c\u2500"

    def test_ramos_ascii(self, estado_limpo):
        ui.configure(unicode=False)
        assert ui.tree_last() == "`-"
        assert ui.tree_mid() == "|-"


class TestStatsTextETail:
    def test_tamanho_e_tempo(self, estado_limpo):
        ui.configure(unicode=True)
        assert ui.stats_text(int(78.3 * 1024 * 1024), 6.24) == "78.3 MB \u00b7 6.2 s"

    def test_ascii_usa_virgula(self, estado_limpo):
        ui.configure(unicode=False)
        assert ui.stats_text(5 * 1024 * 1024, 1.0) == "5.0 MB, 1.0 s"

    def test_gigabytes(self, estado_limpo):
        ui.configure(unicode=True)
        assert ui.stats_text(2 * 1024**3, 90) == "2.00 GB \u00b7 90.0 s"

    def test_sem_dados_nao_gera_texto(self, estado_limpo):
        assert ui.stats_text(0, None) == ""

    def test_so_tempo_quando_tamanho_desconhecido(self, estado_limpo):
        ui.configure(unicode=True)
        assert ui.stats_text(0, 2.5) == "2.5 s"

    def test_tail_separa_com_respiro(self, estado_limpo, saida, colunas):
        colunas(100)
        ui.configure(color=False)
        ui.ok(ui.tail("Concluído: [01] Tempo", "20.8 MB"))
        assert saida == ["[+] Concluído: [01] Tempo   20.8 MB"]

    def test_tail_vazio_devolve_a_mensagem(self, estado_limpo):
        assert ui.tail("Concluído: [01] Tempo", "") == "Concluído: [01] Tempo"

    def test_dim_usa_cinza_fixo_e_nao_o_esmaecido(self, estado_limpo, monkeypatch):
        ui.configure(color=True)
        monkeypatch.setattr(ui, "GRAY", "<GRAY>")
        monkeypatch.setattr(ui, "MUTED", "<DIM>")
        monkeypatch.setattr(ui, "RESET", "<R>")
        assert ui.dim("20.8 MB") == "<GRAY>20.8 MB<R>"

    def test_dim_sem_cor_devolve_o_texto(self, estado_limpo):
        ui.configure(color=False)
        assert ui.dim("20.8 MB") == "20.8 MB"


class TestTaggedEspacamento:
    def test_preserva_espacos_quando_cabe(self, estado_limpo, saida, colunas):
        colunas(100)
        ui.configure(color=False, unicode=False)
        ui.ok(ui.tail(f" {ui.tree_last()} Concluído: [01] Tempo", "20.8 MB · 5.9 s"))
        assert saida == ["[+] `- Concluído: [01] Tempo   20.8 MB · 5.9 s"]

    def test_metricas_descem_inteiras_quando_nao_cabem(
        self, estado_limpo, saida, colunas
    ):
        colunas(44)
        ui.configure(color=False, unicode=False)
        ui.ok(ui.tail(" `- Concluído: [04] Como Yo :(", "27.1 MB, 7.4 s"))
        assert saida == [
            "[+] `- Concluído: [04] Como Yo :(",
            "    27.1 MB, 7.4 s",
        ]

    def test_metricas_ficam_na_ultima_linha_do_nome_quando_cabem(
        self, estado_limpo, saida, colunas
    ):
        colunas(40)
        ui.configure(color=False)
        ui.ok(ui.tail("Concluído: [09] Dónde Están Que No Los Veo", "43.1 MB"))
        assert saida[-1].endswith("Los Veo   43.1 MB")
        assert all(ui._cell_width(ln) <= 40 for ln in saida)
        assert "\x1f" not in "".join(saida)

    def test_ainda_quebra_quando_nao_cabe(self, estado_limpo, saida, colunas):
        colunas(40)
        ui.configure(color=False)
        ui.ok("palavra " * 12)
        assert len(saida) > 1
        assert all(ui._cell_width(ln) <= 40 for ln in saida)

    def test_cor_da_linha_volta_depois_do_trecho_apagado(
        self, estado_limpo, saida, colunas, monkeypatch
    ):
        colunas(100)
        ui.configure(color=True)
        monkeypatch.setattr(ui, "RESET", "<R>")
        monkeypatch.setattr(ui, "SUCCESS", "<G>")
        monkeypatch.setattr(ui, "GRAY", "<D>")
        ui.ok(ui.tail("Concluído: [01] Tempo", "20.8 MB"))
        assert saida == ["<G>[+] Concluído: [01] Tempo   <D>20.8 MB<R><G><R>"]


class TestLarguraDaCaixa:
    def test_tela_larga_usa_o_teto_compacto(self, estado_limpo, colunas):
        colunas(145)
        assert ui.box_width() == 48

    def test_celular_usa_a_linha_inteira_menos_um(self, estado_limpo, colunas):
        colunas(44)
        assert ui.box_width() == 43

    def test_teto_pode_ser_trocado_por_variavel(
        self, estado_limpo, monkeypatch, colunas
    ):
        colunas(145)
        monkeypatch.setenv("QOBUZ_DL_BOX_WIDTH", "60")
        assert ui.box_width() == 60

    def test_variavel_invalida_e_ignorada(self, estado_limpo, monkeypatch, colunas):
        colunas(145)
        monkeypatch.setenv("QOBUZ_DL_BOX_WIDTH", "abc")
        assert ui.box_width() == 48

    def test_nunca_passa_da_largura_do_terminal(
        self, estado_limpo, monkeypatch, colunas
    ):
        colunas(50)
        monkeypatch.setenv("QOBUZ_DL_BOX_WIDTH", "200")
        assert ui.box_width() == 49


class TestTaggedLinesEJoinMeta:
    def test_devolve_as_linhas_sem_escrever(self, estado_limpo, saida, colunas):
        colunas(100)
        ui.configure(color=False, unicode=True)
        linhas = ui.tagged_lines("ok", ui.tail("Capa salva", "cover.jpg"))
        assert linhas == ["[+] Capa salva   cover.jpg"]
        assert saida == []  # nada foi escrito

    @pytest.mark.parametrize(
        "kind,tag",
        [
            ("step", "[*]"),
            ("ok", "[+]"),
            ("skip", "[-]"),
            ("warn", "[!]"),
            ("error", "[!]"),
        ],
    )
    def test_cada_tipo_tem_a_sua_tag(self, estado_limpo, colunas, kind, tag):
        colunas(100)
        ui.configure(color=False)
        assert ui.tagged_lines(kind, "x")[0].startswith(tag + " ")

    def test_join_meta_ignora_vazios(self, estado_limpo):
        ui.configure(unicode=True)
        assert ui.join_meta("a", "", None, "b") == "a \u00b7 b"
        ui.configure(unicode=False)
        assert ui.join_meta("a", "b") == "a, b"

    def test_metricas_longas_quebram_e_cada_linha_fica_cinza(
        self, estado_limpo, colunas, monkeypatch
    ):
        colunas(44)
        ui.configure(color=True)
        monkeypatch.setattr(ui, "GRAY", "<D>")
        monkeypatch.setattr(ui, "RESET", "<R>")
        monkeypatch.setattr(ui, "SUCCESS", "<G>")
        linhas = ui.tagged_lines(
            "ok",
            ui.tail(
                "Letras prontas: [04] Une seule vie",
                "BILINGUAL · sincronizadas · só no metadata · via Qobuz",
            ),
        )
        assert len(linhas) >= 3
        for linha in linhas[1:]:
            assert "<D>" in linha  # nenhuma linha de métrica perde o cinza


class TestSeparator:
    def test_escreve_uma_linha_em_branco_depois_de_uma_mensagem(
        self, estado_limpo, saida, colunas
    ):
        colunas(100)
        ui.configure(color=False)
        ui.ok("Capa salva")
        ui.separator()
        assert saida == ["[+] Capa salva", ""]

    def test_nao_empilha_duas_linhas_em_branco(self, estado_limpo, saida, colunas):
        colunas(100)
        ui.configure(color=False)
        ui.ok("Capa salva")
        ui.separator()
        ui.separator()
        assert saida == ["[+] Capa salva", ""]

    def test_nao_escreve_depois_da_caixa_que_ja_termina_em_branco(
        self, estado_limpo, saida, colunas
    ):
        colunas(60)
        ui.configure(color=False, unicode=False)
        ui.header("ÁLBUM", [("Álbum", "Disco")])
        antes = len(saida)
        ui.separator()
        assert len(saida) == antes  # o cabeçalho já deixou a linha em branco

    def test_escreve_de_novo_depois_de_nova_mensagem(
        self, estado_limpo, saida, colunas
    ):
        colunas(100)
        ui.configure(color=False)
        ui.ok("a")
        ui.separator()
        ui.ok("b")
        ui.separator()
        assert saida == ["[+] a", "", "[+] b", ""]

    def test_quiet_nao_escreve(self, estado_limpo, saida):
        ui.configure(color=False, quiet=True)
        ui.separator()
        assert saida == []
