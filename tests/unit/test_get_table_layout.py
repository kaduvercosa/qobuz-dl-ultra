"""Testa qobuz_dl/core.py::_get_table_layout -- decide se a tela cabe uma
tabela (modo largo) ou cai no modo cartão (estreito/celular), e calcula
larguras/cabeçalhos/bordas por categoria de item.
"""

import pytest

from qobuz_dl.core import _get_table_layout


class TestGetTableLayoutModoEstreito:
    def test_album_abaixo_do_minimo_usa_cartao(self):
        is_table, widths, headers, borders = _get_table_layout(
            89, is_multi=False, item_category="album"
        )
        assert is_table is False
        assert widths == []
        assert headers == []
        assert borders == {}

    def test_album_exatamente_no_minimo_ativa_tabela(self):
        is_table, widths, *_ = _get_table_layout(
            90, is_multi=False, item_category="album"
        )
        assert is_table is True
        # título e artista com largura útil (>= 18 e >= 15 caracteres)
        assert widths[0] >= 18 and widths[1] >= 15

    @pytest.mark.parametrize(
        "categoria,minimo",
        [("album", 90), ("track", 92), ("playlist", 60), ("artist", 45)],
    )
    def test_limite_de_tabela_por_categoria(self, categoria, minimo):
        assert _get_table_layout(minimo, False, categoria)[0] is True
        assert _get_table_layout(minimo - 1, False, categoria)[0] is False

    def test_multi_exige_duas_colunas_a_mais(self):
        assert _get_table_layout(91, True, "album")[0] is False
        assert _get_table_layout(92, True, "album")[0] is True

    def test_tabela_nunca_passa_da_largura_da_tela(self):
        for categoria in ("album", "track", "playlist", "artist"):
            for cols in (45, 60, 90, 92, 120, 160, 220):
                for multi in (False, True):
                    ok, widths, _, borders = _get_table_layout(cols, multi, categoria)
                    if ok:
                        prefixo = 5 if multi else 3
                        assert prefixo + len(borders["top"]) <= cols

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
        # Na menor tela em que a tabela aparece, nenhuma coluna pode virar
        # 0 ou negativa; abaixo disso a função devolve o modo cartão.
        for categoria, minimo in (
            ("album", 90),
            ("track", 92),
            ("playlist", 60),
            ("artist", 45),
        ):
            _, widths, _, _ = _get_table_layout(
                minimo, is_multi=False, item_category=categoria
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
            assert borda[0] in "┌├└"
            assert borda[-1] in "┐┤┘"

    def test_largura_total_das_colunas_cresce_com_a_tela(self):
        # Não trava um valor absoluto (a soma exata depende da conta
        # interna de overhead) -- só garante que uma tela mais larga
        # resulta em colunas mais largas, não menores/iguais.
        _, widths_estreita, _, _ = _get_table_layout(
            100, is_multi=False, item_category="track"
        )
        _, widths_larga, _, _ = _get_table_layout(
            200, is_multi=False, item_category="track"
        )
        assert sum(widths_larga) > sum(widths_estreita)
