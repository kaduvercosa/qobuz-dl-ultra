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


@pytest.fixture
def colunas(monkeypatch):
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
    def test_columns_invalida_cai_para_o_terminal(self, monkeypatch, valor):
        monkeypatch.setenv("COLUMNS", valor)
        monkeypatch.setattr(
            ui.shutil,
            "get_terminal_size",
            lambda fallback: types.SimpleNamespace(columns=77),
        )
        assert ui.raw_width() == 77

    def test_erro_ao_consultar_o_terminal_usa_o_fallback(self, monkeypatch):
        monkeypatch.delenv("COLUMNS", raising=False)

        def explode(fallback):
            raise OSError("sem tty")

        monkeypatch.setattr(ui.shutil, "get_terminal_size", explode)
        assert ui.raw_width() == ui.FALLBACK[0]


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
        assert "[ALBUM]" in texto
        assert "TITULO" in texto and "Disco" in texto
        assert "ARTISTA" in texto and "Banda" in texto
        assert "=" * 20 in texto

    def test_header_sem_linhas(self, estado_limpo, saida):
        ui.configure(color=False)
        ui.header("VAZIO", [])
        assert "[VAZIO]" in "\n".join(saida)


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
