"""Tela de busca em caixa: rótulos, largura, quando aparece e fallback."""

import asyncio
import logging

import pytest

from qobuz_dl import search_prompt as sp


@pytest.mark.unit
@pytest.mark.parametrize(
    "bruto,esperado",
    [
        ("Tracks", "Faixas"),
        ("Albums", "Álbuns"),
        ("🎵 Tracks", "Faixas"),
        ("Artists", "Artistas"),
        ("Favorites", "Favoritos"),
        ("Outro", "Outro"),
        (None, ""),
    ],
)
def test_rotulo_do_tipo_de_busca(bruto, esperado):
    assert sp.kind_label(bruto) == esperado


@pytest.mark.unit
@pytest.mark.parametrize("colunas,esperado", [(20, 30), (46, 42), (80, 76), (200, 196)])
def test_largura_da_caixa(colunas, esperado):
    assert sp.box_width(colunas) == esperado


@pytest.mark.unit
def test_caixa_ligada_por_padrao_mesmo_sem_tty():
    # o a-Shell pode não reportar TTY: o critério não depende disso
    assert sp.box_enabled({}) is True


@pytest.mark.unit
def test_variavel_de_ambiente_volta_ao_prompt_simples():
    assert sp.box_enabled({sp.SIMPLE_PROMPT_ENV: "1"}) is False
    assert sp.box_enabled({sp.SIMPLE_PROMPT_ENV: "off"}) is True


@pytest.mark.unit
def test_desligada_durante_os_testes():
    assert sp.box_enabled({"PYTEST_CURRENT_TEST": "x"}) is False


class _AppFalso:
    def __init__(self, resultado=None, erro=None):
        self.resultado, self.erro = resultado, erro

    async def run_async(self):
        if self.erro:
            raise self.erro
        return self.resultado


async def _fallback():
    return "via fallback"


@pytest.mark.unit
def test_devolve_o_texto_digitado_na_caixa():
    app = _AppFalso("baco exu")
    out = asyncio.run(sp.ask_query("Tracks", _fallback, build_app=lambda kind: app))
    assert out == "baco exu"


@pytest.mark.unit
def test_rotulo_chega_traduzido_ao_construir_a_caixa():
    vistos = []

    def build(kind):
        vistos.append(kind)
        return _AppFalso("x")

    asyncio.run(sp.ask_query("🎵 Tracks", _fallback, build_app=build))
    assert vistos == ["Faixas"]


@pytest.mark.unit
@pytest.mark.parametrize("erro", [KeyboardInterrupt, EOFError])
def test_cancelar_propaga_como_no_prompt_antigo(erro):
    with pytest.raises(erro):
        asyncio.run(
            sp.ask_query(
                "Tracks", _fallback, build_app=lambda k: _AppFalso(erro=erro())
            )
        )


@pytest.mark.unit
def test_erro_ao_desenhar_cai_no_prompt_simples():
    out = asyncio.run(
        sp.ask_query(
            "Tracks", _fallback, build_app=lambda k: _AppFalso(erro=RuntimeError("x"))
        )
    )
    assert out == "via fallback"


@pytest.mark.unit
def test_erro_ao_montar_cai_no_prompt_simples():
    def build(kind):
        raise ImportError("prompt_toolkit antigo")

    out = asyncio.run(sp.ask_query("Tracks", _fallback, build_app=build))
    assert out == "via fallback"


@pytest.mark.unit
def test_sem_terminal_interativo_usa_o_prompt_simples(monkeypatch):
    # em testes/pipes stdin não é TTY: nenhuma Application real é criada
    monkeypatch.setattr(sp, "box_enabled", lambda *a, **k: False)
    out = asyncio.run(sp.ask_query("Tracks", _fallback))
    assert out == "via fallback"


@pytest.mark.unit
def test_motivo_do_fallback_aparece_uma_unica_vez(monkeypatch):
    avisos = []

    class Captura(logging.Handler):
        def emit(self, record):
            avisos.append(record.getMessage())

    handler = Captura(level=logging.WARNING)
    sp.logger.addHandler(handler)
    monkeypatch.setattr(sp, "_warned", False)

    def quebra(kind):
        raise RuntimeError("falhou ao montar")

    try:
        for _ in range(2):
            out = asyncio.run(sp.ask_query("Tracks", _fallback, build_app=quebra))
            assert out == "via fallback"
    finally:
        sp.logger.removeHandler(handler)

    assert len(avisos) == 1
    assert "RuntimeError" in avisos[0] and "falhou ao montar" in avisos[0]
