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
    """Substitui as funções da camada `ui` usadas por render_stats por
    versões que só registram a chamada, na ordem em que aconteceram."""
    chamadas = []

    def _fake(nome):
        def _fn(*args, **kwargs):
            chamadas.append((nome, args, kwargs))

        return _fn

    for nome in ("banner", "section", "warn", "rule", "blank", "detail", "emit"):
        monkeypatch.setattr(ui, nome, _fake(nome))

    # kv registra label/value pra facilitar asserção direta, sem precisar
    # reconstruir a string formatada que ui.kv desenharia na tela.
    def _fake_kv(label, value, label_width=30, narrow_stack=False):
        chamadas.append(("kv", (label, value), {}))

    monkeypatch.setattr(ui, "kv", _fake_kv)

    # bar_gauge/truncate são chamadas de verdade (são puras e baratas) --
    # só is_narrow/width viram controláveis pelo teste.
    monkeypatch.setattr(ui, "is_narrow", lambda: False)
    monkeypatch.setattr(ui, "width", lambda *a, **k: 100)

    return chamadas


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

    def test_sem_dados_nao_tenta_mostrar_secoes_de_qualidade(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: {"total": 0})
        render_stats("qualquer.db")
        # Não deve nem tentar montar a seção de qualidade/formatos sem
        # nenhum dado -- confirma que a função sai cedo (early return).
        secoes = [c[1][0] for c in capturado if c[0] == "section"]
        assert secoes == []


class TestRenderStatsComDados:
    def test_retorna_0(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        assert render_stats("qualquer.db") == 0

    def test_secoes_esperadas_aparecem(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        secoes = [c[1][0] for c in capturado if c[0] == "section"]
        assert secoes == [
            "BIBLIOTECA",
            "QUALIDADE DE ÁUDIO",
            "FORMATOS",
            "PERÍODO",
            "TOP ARTISTAS",
        ]

    def test_percentuais_de_hires_e_qualidade_calculados_certo(
        self, monkeypatch, capturado
    ):
        # hires=4 de total=10 -> 40%; quality_met=8 de 10 -> 80%.
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        valores_kv = {c[1][0]: c[1][1] for c in capturado if c[0] == "kv"}
        assert "40%" in valores_kv["Hi-Res (\u226524bit)"]
        assert "80%" in valores_kv["Qualidade solicitada atingida"]

    def test_datas_sao_formatadas_para_br(self, monkeypatch, capturado):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        valores_kv = {c[1][0]: c[1][1] for c in capturado if c[0] == "kv"}
        assert valores_kv["Lançamento mais antigo"] == "01/01/2015"
        assert valores_kv["Lançamento mais recente"] == "15/06/2024"

    def test_sem_oldest_nem_newest_pula_secao_periodo(self, monkeypatch, capturado):
        stats = _stats_completo(oldest=None, newest=None)
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        render_stats("qualquer.db")
        secoes = [c[1][0] for c in capturado if c[0] == "section"]
        assert "PERÍODO" not in secoes

    def test_sem_top_artists_pula_secao(self, monkeypatch, capturado):
        stats = _stats_completo(top_artists=[])
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: stats)
        render_stats("qualquer.db")
        secoes = [c[1][0] for c in capturado if c[0] == "section"]
        assert "TOP ARTISTAS" not in secoes

    def test_dica_de_flag_aparece_so_quando_nao_pediu_todos_os_artistas(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db", show_all_artists=False)
        assert any(c[0] == "detail" and "--artistas" in str(c[1]) for c in capturado)

    def test_show_all_artists_adiciona_secao_com_a_lista_completa(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db", show_all_artists=True)
        secoes = [c[1][0] for c in capturado if c[0] == "section"]
        assert any(s.startswith("TODOS OS ARTISTAS") for s in secoes)
        # E a dica de usar --artistas não deveria aparecer de novo, já
        # que o usuário já pediu a lista completa.
        assert not any(
            c[0] == "detail" and "--artistas" in str(c[1]) for c in capturado
        )

    def test_top_artists_modo_largo_usa_uma_linha_por_artista(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(ui, "is_narrow", lambda: False)
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        linhas_emit = [c[1][0] for c in capturado if c[0] == "emit"]
        linhas_com_artista = [l for l in linhas_emit if "Artista A" in l]
        # Modo largo: nome e barra na MESMA linha (uma linha por artista).
        assert len(linhas_com_artista) == 1

    def test_top_artists_modo_estreito_usa_duas_linhas_por_artista(
        self, monkeypatch, capturado
    ):
        monkeypatch.setattr(ui, "is_narrow", lambda: True)
        monkeypatch.setattr(stats_view, "get_stats", lambda db_path: _stats_completo())
        render_stats("qualquer.db")
        linhas_emit = [c[1][0] for c in capturado if c[0] == "emit"]
        linhas_com_artista = [l for l in linhas_emit if "Artista A" in l]
        # Modo estreito: nome numa linha, barra recuada na linha seguinte.
        assert len(linhas_com_artista) == 1  # a linha com o NOME
        # E deve existir uma linha logo depois só com a barra/contagem
        # (não contém o nome do artista).
        idx = linhas_emit.index(linhas_com_artista[0])
        assert idx + 1 < len(linhas_emit)
        assert "Artista A" not in linhas_emit[idx + 1]
