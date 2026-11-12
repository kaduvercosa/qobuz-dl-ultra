"""Layout COMPACTO (uma linha por resultado) da lista interativa.

Estes testes NÃO abrem o prompt_toolkit: trocam `Window`, `Layout`, `HSplit`,
`Dimension` e `Application` de `core` por falsos que só guardam as funções de
desenho, e depois chamam essas funções (`get_header_text`, `get_list_text`,
`get_footer_text`) com a largura que o teste quiser. Assim o desenho de verdade
roda em qualquer ambiente (inclusive onde o prompt_toolkit não está instalado).
A rolagem e as teclas ficam com `test_core_tui_40_colunas.py` (tela real).
"""

import asyncio

import pytest

from qobuz_dl import core, ui
from qobuz_dl.interactive_ui import LINHAS_DETALHE

pytestmark = pytest.mark.unit


class _Tela:
    def __init__(self, colunas, linhas):
        self.columns = colunas
        self.rows = linhas

    def get_size(self):
        return self


class _App:
    ultimo = None

    def __init__(self, layout, key_bindings, **kwargs):
        self.output = _Tela(40, 24)
        self.layout = layout
        _App.ultimo = self

    async def run_async(self):
        return None


class _Janela:
    def __init__(self, content=None, height=None, **kwargs):
        self.content = content
        self.height = height
        self.render_info = None
        self.vertical_scroll = 0


class _Controle:
    def __init__(self, text="", **kwargs):
        self.text = text


class _Dim:
    def __init__(self, **kwargs):
        self.kw = kwargs

    @classmethod
    def exact(cls, n):
        return cls(exact=n)


class _HSplit:
    def __init__(self, children):
        self.children = children


class _Layout:
    def __init__(self, container):
        self.container = container


def _album(n, **extra):
    meta = {
        "title": f"Álbum número {n} com título bem comprido para cortar",
        "artist": f"Artista {n}",
        "type": "Album",
        "year": str(2000 + n),
        "tracks_count": 10 + n,
        "quality": "24b/96kHz",
        "duration": "00:45:00",
    }
    meta.update(extra)
    return {"meta": meta, "url": f"u{n}"}


@pytest.fixture
def tela(monkeypatch):
    monkeypatch.setattr(core, "Application", _App)
    monkeypatch.setattr(core, "Window", _Janela)
    monkeypatch.setattr(core, "FormattedTextControl", _Controle)
    monkeypatch.setattr(core, "Dimension", _Dim)
    monkeypatch.setattr(core, "HSplit", _HSplit)
    monkeypatch.setattr(core, "Layout", _Layout)
    monkeypatch.setattr(core, "get_app", lambda: _App.ultimo)

    def abrir(opcoes, colunas, linhas=24, layout="auto", categoria="album", multi=True):
        _App.ultimo = None

        async def _ir():
            ui.estado.layout_interativo = layout
            # `get_app()` só passa a valer quando a tela é montada.
            _App.ultimo = _App(None, None)
            _App.ultimo.output = _Tela(colunas, linhas)
            await core._tui_select(
                "teste", opcoes, is_multi=multi, item_category=categoria
            )

        monkeypatch.setattr(
            core,
            "Application",
            lambda layout, key_bindings, **kw: _guardar(layout, colunas, linhas),
        )
        asyncio.run(_ir())
        janelas = _App.ultimo.layout.container.children
        return janelas

    def _guardar(layout, colunas, linhas):
        _App.ultimo.layout = layout
        return _App.ultimo

    return abrir


def _texto(fragmentos):
    return "".join(t for _s, t in fragmentos)


def _linhas(janela):
    return _texto(janela.content.text()).split("\n")


def _largura(linha):
    return core.get_cwidth(linha)


class TestCompacto:
    def test_uma_linha_por_item_e_cabe_na_tela(self, tela):
        opcoes = [_album(n) for n in range(12)]
        cabecalho, lista, rodape, _seguro = tela(opcoes, 40, 40, layout="compacto")
        linhas = _linhas(lista)
        assert len(linhas) == 12
        assert all(_largura(linha) <= 40 for linha in linhas)
        # Nenhuma borda de cartão ou de tabela.
        assert not any(c in _texto(lista.content.text()) for c in "╭╰┌└├")

    def test_lista_longa_mostra_so_o_que_cabe_na_altura(self, tela):
        opcoes = [_album(n) for n in range(40)]
        cab, lista, rodape, _s = tela(opcoes, 40, 24, layout="compacto")
        linhas = _linhas(lista)
        rodape_linhas = _texto(rodape.content.text()).count("\n")
        # tela = título (2) + lista + rodapé + 1 linha de segurança
        assert len(linhas) == 24 - 2 - rodape_linhas - 1
        assert len(linhas) > 10  # o cartão mostraria uns 4 itens nessa tela

    def test_linha_mostra_titulo_artista_e_qualidade_curta(self, tela):
        opcoes = [_album(1)]
        _c, lista, _r, _s = tela(opcoes, 40, 30, layout="compacto")
        linha = _linhas(lista)[0]
        assert "Álbum número" in linha
        assert "24/96" in linha
        assert linha.rstrip().endswith("24/96")

    def test_rodape_traz_o_painel_do_item_focado_com_altura_fixa(self, tela):
        opcoes = [_album(1), "Menu inicial"]
        _c, _l, rodape, _s = tela(opcoes, 40, 30, layout="compacto")
        texto = _texto(rodape.content.text())
        assert "Artista 1" in texto
        assert texto.count("\n") >= LINHAS_DETALHE + 1

    def test_painel_mantem_a_altura_em_item_sem_detalhes(self, tela):
        com = tela([_album(1), "Menu inicial"], 40, 30, layout="compacto")
        altura_com = _texto(com[2].content.text()).count("\n")
        # Foca o item de texto: o painel vira linhas vazias, mesma altura.
        sem = tela(["Menu inicial", _album(1)], 40, 30, layout="compacto")
        assert _texto(sem[2].content.text()).count("\n") == altura_com

    def test_altura_do_rodape_acompanha_o_texto(self, tela):
        _c, _l, rodape, _s = tela([_album(1)], 40, 30, layout="compacto")
        rows = _texto(rodape.content.text()).count("\n")
        assert rodape.height().kw == {"exact": rows}

    def test_cabecalho_so_com_o_titulo(self, tela):
        cab, *_ = tela([_album(1)], 40, 30, layout="compacto")
        assert "┌" not in _texto(cab.content.text())

    def test_largo_forcado_continua_compacto_e_mostra_o_ano(self, tela):
        _c, lista, _r, _s = tela([_album(3)], 100, 30, layout="compacto")
        linha = _linhas(lista)[0]
        assert "2003" in linha and "24/96" in linha
        assert _largura(linha) <= 100

    def test_selecionado_e_item_de_texto_aparecem(self, tela):
        _c, lista, _r, _s = tela(
            [_album(1), "Nova pesquisa"], 40, 30, layout="compacto"
        )
        assert "Nova pesquisa" in _texto(lista.content.text())

    @pytest.mark.parametrize("categoria", ["track", "playlist", "artist"])
    def test_outras_categorias(self, tela, categoria):
        metas = {
            "track": {
                "title": "Faixa longa demais para a tela pequena",
                "artist": "Alguém",
                "album": "Disco",
                "type": "Track",
                "year": "2020",
                "quality": "16b/44.1kHz",
                "duration": "00:03:20",
            },
            "playlist": {
                "name": "Minha playlist enorme demais",
                "owner": "Ana",
                "count": 20,
                "duration": "01:00:00",
            },
            "artist": {"name": "Artista de nome bem comprido mesmo", "count": 12},
        }
        opcoes = [{"meta": metas[categoria], "url": "u"}] * 3
        _c, lista, rodape, _s = tela(
            opcoes, 40, 30, layout="compacto", categoria=categoria
        )
        assert all(_largura(linha) <= 40 for linha in _linhas(lista))
        assert all(_largura(linha) <= 40 for linha in _linhas(rodape))


class TestEscolhaDoLayout:
    def test_auto_em_tela_estreita_usa_compacto(self, tela):
        _c, lista, _r, _s = tela([_album(1)], 40, 30, layout="auto")
        assert "╭" not in _texto(lista.content.text())

    def test_cartao_forcado_mantem_as_bordas(self, tela):
        _c, lista, _r, _s = tela([_album(1)], 40, 30, layout="cartao")
        assert "╭" in _texto(lista.content.text())

    def test_auto_em_tela_media_sem_tabela_fica_em_cartao(self, tela):
        _c, lista, _r, _s = tela([_album(1)], 80, 30, layout="auto")
        assert "╭" in _texto(lista.content.text())

    def test_tabela_quando_cabe(self, tela):
        cab, lista, _r, _s = tela([_album(1)], 140, 30, layout="auto")
        assert "┌" in _texto(cab.content.text())

    def test_menu_simples_nunca_vira_compacto(self, tela):
        _c, lista, _r, _s = tela(
            ["Sim", "Não"], 40, 30, layout="compacto", categoria="filter", multi=False
        )
        assert "╭" in _texto(lista.content.text())
