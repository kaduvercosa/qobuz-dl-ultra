"""Testa qobuz_dl/core.py::_get_table_layout -- decide se a tela cabe uma
tabela (modo largo) ou cai no modo cartão (estreito/celular), e calcula
larguras/cabeçalhos/bordas por categoria de item.
"""

import pytest

from qobuz_dl.core import _get_table_layout


class TestGetTableLayoutModoEstreito:
    def test_menos_de_78_colunas_desativa_tabela(self):
        is_table, widths, headers, borders = _get_table_layout(
            77, is_multi=False, item_category="album"
        )
        assert is_table is False
        assert widths == []
        assert headers == []
        assert borders == {}

    def test_exatamente_78_colunas_ja_ativa_tabela(self):
        is_table, *_ = _get_table_layout(78, is_multi=False, item_category="album")
        assert is_table is True

    def test_categoria_filter_sempre_desativa_tabela_mesmo_em_tela_larga(self):
        # Categoria "filter" é menu simples (sim/não) -- não faz sentido
        # de tabela mesmo com colunas de sobra.
        is_table, *_ = _get_table_layout(200, is_multi=False, item_category="filter")
        assert is_table is False

    def test_categoria_desconhecida_tambem_desativa_tabela(self):
        is_table, widths, headers, borders = _get_table_layout(
            200, is_multi=False, item_category="algo-que-nao-existe"
        )
        assert is_table is False
        assert widths == [] and headers == [] and borders == {}


class TestGetTableLayoutCategorias:
    @pytest.mark.parametrize(
        "categoria,qtd_colunas_esperada",
        [
            ("album", 6),
            ("track", 6),
            ("playlist", 4),
            ("artist", 2),
        ],
    )
    def test_numero_de_colunas_bate_com_os_headers(
        self, categoria, qtd_colunas_esperada
    ):
        is_table, widths, headers, borders = _get_table_layout(
            120, is_multi=False, item_category=categoria
        )
        assert is_table is True
        assert len(widths) == qtd_colunas_esperada
        assert len(headers) == qtd_colunas_esperada

    def test_headers_do_album_estao_corretos(self):
        _, _, headers, _ = _get_table_layout(120, is_multi=False, item_category="album")
        assert headers == ["ÁLBUM", "ARTISTA", "TIPO", "ANO", "FAIXAS", "QUALIDADE"]

    def test_headers_da_track_estao_corretos(self):
        _, _, headers, _ = _get_table_layout(120, is_multi=False, item_category="track")
        assert headers == ["FAIXA", "ARTISTA", "ÁLBUM", "TIPO", "DURAÇÃO", "QUALIDADE"]

    def test_headers_da_playlist_estao_corretos(self):
        _, _, headers, _ = _get_table_layout(
            120, is_multi=False, item_category="playlist"
        )
        assert headers == ["NOME DA PLAYLIST", "CRIADOR", "FAIXAS", "DURAÇÃO"]

    def test_headers_do_artist_estao_corretos(self):
        _, _, headers, _ = _get_table_layout(
            120, is_multi=False, item_category="artist"
        )
        assert headers == ["NOME DO ARTISTA", "LANÇAMENTOS"]

    def test_larguras_das_colunas_flex_nunca_ficam_negativas(self):
        # Cada categoria tem um max(N, ...) pro flex -- mesmo numa tela
        # bem apertada (mas ainda >=78 colunas), a coluna de texto livre
        # não pode virar 0 ou negativa.
        for categoria in ("album", "track", "playlist", "artist"):
            _, widths, _, _ = _get_table_layout(
                78, is_multi=False, item_category=categoria
            )
            assert all(w > 0 for w in widths), f"{categoria}: {widths}"

    def test_is_multi_reduz_o_espaco_disponivel_pro_flex(self):
        # is_multi=True usa um prefixo maior (checkbox de seleção múltipla
        # ocupa mais espaço que o marcador de seleção única) -- a coluna
        # de texto livre (título) tem que encolher, não pode ignorar isso.
        _, widths_single, _, _ = _get_table_layout(
            100, is_multi=False, item_category="album"
        )
        _, widths_multi, _, _ = _get_table_layout(
            100, is_multi=True, item_category="album"
        )
        assert widths_multi[0] <= widths_single[0]

    def test_bordas_tem_as_tres_chaves_e_comecam_e_terminam_certo(self):
        _, widths, _, borders = _get_table_layout(
            120, is_multi=False, item_category="album"
        )
        assert set(borders.keys()) == {"top", "mid", "bot"}
        for borda in borders.values():
            assert borda.startswith("+-")
            assert borda.endswith("-+")

    def test_largura_total_das_colunas_cresce_com_a_tela(self):
        # Não trava um valor absoluto (a soma exata depende da conta
        # interna de overhead) -- só garante que uma tela mais larga
        # resulta em colunas mais largas, não menores/iguais.
        _, widths_estreita, _, _ = _get_table_layout(
            80, is_multi=False, item_category="track"
        )
        _, widths_larga, _, _ = _get_table_layout(
            200, is_multi=False, item_category="track"
        )
        assert sum(widths_larga) > sum(widths_estreita)
