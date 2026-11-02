"""Testa qobuz_dl/stats_view.py: formatação de data e o render_stats
completo, capturando as chamadas na camada `ui` em vez de precisar de um
terminal de verdade.

`stats_view.py` só depende de `qobuz_dl.ui` (que por sua vez só depende de
`qobuz_dl.color` + stdlib) -- diferente da maioria dos outros módulos
grandes do projeto, importa direto sem precisar de uma cadeia de stubs
pra mutagen/httpx/tenacity/etc.
"""

import pytest

from qobuz_dl import stats_view, ui
from qobuz_dl.stats_view import _fmt_date, render_stats


# --------------------------------------------------------------------
# _fmt_date
# --------------------------------------------------------------------
class TestFmtDate:
    def test_data_iso_vira_formato_br(self):
        assert _fmt_date("2019-05-31T12:00:00Z") == "31/05/2019"

    def test_apenas_a_data_sem_horario(self):
        assert _fmt_date("2019-05-31") == "31/05/2019"

    def test_valor_ausente_vira_interrogacao(self):
        assert _fmt_date(None) == "?"
        assert _fmt_date("") == "?"

    def test_valor_malformado_e_devolvido_como_veio(self):
        # Sem os dois "-" esperados (split não dá 3 partes) -- ValueError
        # capturado, devolve o valor original em vez de quebrar a tela
        # inteira de estatísticas por causa de uma data suja no banco.
        assert _fmt_date("data-invalida-sem-formato") == "data-invalida-sem-formato"
        assert _fmt_date("20190531") == "20190531"


# --------------------------------------------------------------------
# render_stats -- captura as chamadas de ui.* em vez de imprimir de verdade
# --------------------------------------------------------------------
@pytest.fixture
def capturado(monkeypatch):
    """Substitui as funcoes da camada `ui` usadas por render_stats por
    versoes que so registram a chamada, na ordem em que aconteceram.

    `header` guarda (titulo, linhas rotulo/valor); `emit_box` guarda
    (titulo, linhas de texto). `box_width` e `bar_gauge`/`truncate` rodam de
    verdade (sao puras e baratas)."""
    chamadas = []

    def _fake(nome):
        def _fn(*args, **kwargs):
            chamadas.append((nome, args, kwargs))

        return _fn

    for nome in ("warn", "blank", "detail", "emit", "header", "emit_box"):
        monkeypatch.setattr(ui, nome, _fake(nome))

    monkeypatch.setattr(ui, "width", lambda *a, **k: 100)
    monkeypatch.setattr(ui, "box_width", lambda cols=None: 76)  # interno: 72

    return chamadas


def _paineis(chamadas):
    """{titulo: {rotulo: valor}} dos paineis de pares rotulo/valor."""
    return {c[1][0]: dict(c[1][1]) for c in chamadas if c[0] == "header"}


def _titulos(chamadas):
    """Titulos de todos os paineis, na ordem em que foram impressos."""
    return [c[1][0] for c in chamadas if c[0] in ("header", "emit_box")]


def _linhas_da_caixa(chamadas, titulo):
    return next(c[1][1] for c in chamadas if c[0] == "emit_box" and c[1][0] == titulo)


def _stats_completo(**overrides):
    base = {
        "total": 10,
        "albums": 6,
        "tracks": 4,
        "unique_artists": 3,
        "unique_albums": 6,
        "hires": 4,
        "quality_met": 8,
        "quality_not_met": 2,
        "bit_depths": {24: 4, 16: 6},
        "sample_rates": {96: 3, 44: 7},
        "formats": {"FLAC": 8, "MP3": 2},
        "oldest": "2015-01-01",
        "newest": "2024-06-15",
        "top_artists": [("Artista A", 5), ("Artista B", 3), ("Artista C", 2)],
        "artist_list": ["Artista A", "Artista B", "Artista C"],
    }
    base.update(overrides)
    return base


class TestRenderStatsSemDados:
    def test_total_zero_mostra_aviso_e_retorna_0(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: {"total": 0})
        codigo = render_stats("qualquer.db")
        assert codigo == 0
        nomes = [c[0] for c in capturado]
        assert "warn" in nomes

    def test_stats_none_tambem_mostra_aviso(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: None)
        codigo = render_stats("qualquer.db")
        assert codigo == 0
        assert any(c[0] == "warn" for c in capturado)

    def test_sem_dados_nao_tenta_mostrar_paineis(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: {"total": 0})
        render_stats("qualquer.db")
        # Sem nenhum dado a funcao sai cedo (early return): nenhum painel.
        assert _titulos(capturado) == []


class TestRenderStatsComDados:
    def test_retorna_0(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        assert render_stats("qualquer.db") == 0

    def test_paineis_esperados_aparecem_na_ordem(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        assert _titulos(capturado) == [
            "BIBLIOTECA",
            "QUALIDADE DE ÁUDIO",
            "FORMATOS",
            "PERÍODO",
            "TOP ARTISTAS",
        ]

    def test_so_o_primeiro_painel_tem_linha_em_branco_antes(
        self, monkeypatch, capturado
    ):
        # Cada caixa ja termina com uma linha em branco: sem "lead" nas demais,
        # nao ficam duas linhas em branco entre os paineis.
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        leads = [c[2]["lead"] for c in capturado if c[0] in ("header", "emit_box")]
        assert leads == [True, False, False, False, False]

    def test_totais_da_biblioteca(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        biblioteca = _paineis(capturado)["BIBLIOTECA"]
        assert biblioteca["Downloads"] == "10"
        assert biblioteca["Álbuns"] == "6"
        assert biblioteca["Avulsas"] == "4"
        assert biblioteca["Artistas"] == "3"

    def test_percentuais_de_hires_e_qualidade_calculados_certo(
        self, monkeypatch, capturado
    ):
        # hires=4 de total=10 -> 40%; quality_met=8 de 10 -> 80%.
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        qualidade = _paineis(capturado)["QUALIDADE DE ÁUDIO"]
        assert "40%" in qualidade["Hi-Res ≥24bit"]
        assert "80%" in qualidade["Meta atingida"]
        assert qualidade["Reduzida"] == "2"

    def test_formatos_com_percentual(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        formatos = _paineis(capturado)["FORMATOS"]
        assert formatos == {"FLAC": "8  (80%)", "MP3": "2  (20%)"}

    def test_datas_sao_formatadas_para_br(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        periodo = _paineis(capturado)["PERÍODO"]
        assert periodo["Mais antigo"] == "01/01/2015"
        assert periodo["Mais recente"] == "15/06/2024"

    def test_sem_oldest_nem_newest_pula_painel_periodo(self, monkeypatch, capturado):
        stats = _stats_completo(oldest=None, newest=None)
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        render_stats("qualquer.db")
        assert "PERÍODO" not in _titulos(capturado)

    def test_sem_top_artists_pula_painel(self, monkeypatch, capturado):
        stats = _stats_completo(top_artists=[])
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        render_stats("qualquer.db")
        assert "TOP ARTISTAS" not in _titulos(capturado)

    def test_sem_bit_depths_nem_sample_rates_pula_essas_linhas(
        self, monkeypatch, capturado
    ):
        # bit_depths/sample_rates vazios -> os dois `if` de exibicao sao
        # falsos (nenhum teste existente deixava essas chaves vazias).
        stats = _stats_completo(bit_depths={}, sample_rates={})
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        render_stats("qualquer.db")
        qualidade = _paineis(capturado)["QUALIDADE DE ÁUDIO"]
        assert "Bit depths" not in qualidade
        assert "Sample rates" not in qualidade

    def test_bit_depths_e_sample_rates_nao_quebram_no_meio_de_um_item(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        qualidade = _paineis(capturado)["QUALIDADE DE ÁUDIO"]
        # Sem espacos dentro de cada item ("24bit→4"): a quebra de linha so
        # pode cair entre os itens, nunca entre o valor e a quantidade.
        assert qualidade["Bit depths"].startswith("24bit\u21924  /  16bit\u21926")
        assert "96kHz\u21923" in qualidade["Sample rates"]

    def test_dica_de_flag_aparece_so_quando_nao_pediu_todos_os_artistas(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db", show_all_artists=False)
        assert any(c[0] == "detail" and "--artistas" in str(c[1]) for c in capturado)

    def test_show_all_artists_adiciona_painel_com_a_lista_completa(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db", show_all_artists=True)
        titulos = _titulos(capturado)
        assert titulos[-1] == "TODOS OS ARTISTAS (3)"
        linhas = _linhas_da_caixa(capturado, "TODOS OS ARTISTAS (3)")
        assert [l.split(" ", 1)[1] for l in linhas] == [
            "Artista A",
            "Artista B",
            "Artista C",
        ]
        # E a dica de usar --artistas nao deveria aparecer de novo, ja
        # que o usuario ja pediu a lista completa.
        assert not any(
            c[0] == "detail" and "--artistas" in str(c[1]) for c in capturado
        )

    def test_top_artists_modo_largo_usa_uma_linha_por_artista(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        linhas = _linhas_da_caixa(capturado, "TOP ARTISTAS")
        # Modo largo: nome e barra na MESMA linha (uma linha por artista).
        assert len(linhas) == 3
        assert "Artista A" in linhas[0] and "5" in linhas[0]

    def test_top_artists_modo_estreito_usa_duas_linhas_por_artista(
        self, monkeypatch, capturado
    ):
        # Caixa de 31 colunas (o piso) e contagens grandes: o nome ficaria
        # menor que o minimo ao lado da barra, entao o ranking empilha.
        monkeypatch.setattr(ui, "box_width", lambda cols=None: 31)
        stats = _stats_completo(
            top_artists=[("Artista A", 123456789), ("Artista B", 5)]
        )
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        render_stats("qualquer.db")
        linhas = _linhas_da_caixa(capturado, "TOP ARTISTAS")
        # Modo estreito: nome numa linha, barra recuada na linha seguinte.
        assert len(linhas) == 4
        assert "Artista A" in linhas[0] and "Artista A" not in linhas[1]
        assert linhas[1].startswith("    ") and linhas[1].rstrip().endswith("123456789")

    def test_no_piso_de_largura_com_contagem_curta_ainda_cabe_numa_linha(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(ui, "box_width", lambda cols=None: 31)
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        assert len(_linhas_da_caixa(capturado, "TOP ARTISTAS")) == 3

    def test_nenhuma_linha_do_ranking_passa_da_largura_interna(
        self, monkeypatch, capturado
    ):
        longo = "Artista Com Um Nome Extremamente Longo Que Nao Cabe Na Tela"
        stats = _stats_completo(top_artists=[(longo, 1234), ("Curto", 7)])
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        for caixa in (76, 48, 31):
            capturado.clear()
            monkeypatch.setattr(ui, "box_width", lambda cols=None, n=caixa: n)
            render_stats("qualquer.db")
            for linha in _linhas_da_caixa(capturado, "TOP ARTISTAS"):
                assert ui._cell_width(linha) <= caixa - 4, (caixa, linha)
