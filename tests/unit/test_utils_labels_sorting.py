"""Testa os helpers de `utils` para filtro por gravadora, ordenação por data
de lançamento e o formatador tolerante a campos ausentes.

Eram os únicos helpers públicos de `utils.py` sem nenhuma chamada direta nos
testes -- só eram exercitados de forma indireta pelo fluxo de download.
"""

import pytest

from qobuz_dl import utils
from qobuz_dl.utils import PartialFormatter

pytestmark = pytest.mark.unit


class TestLabelMatches:
    def test_filtro_vazio_casa_com_tudo(self):
        assert utils.label_matches({"name": "X"}, "")
        assert utils.label_matches(None, None)
        assert utils.label_matches({}, "   ")

    def test_id_numerico_exato(self):
        assert utils.label_matches({"id": 123, "name": "Blue Note"}, "123")

    def test_id_numerico_nao_casa_por_prefixo(self):
        assert not utils.label_matches({"id": 1234, "name": "Blue Note"}, "123")

    def test_numero_tambem_pode_estar_no_nome(self):
        # "4AD" não é numérico; "1971" é, mas também aparece no nome.
        assert utils.label_matches({"id": 9, "name": "Label 1971 Records"}, "1971")

    def test_nome_contido_sem_diferenciar_caixa(self):
        assert utils.label_matches({"name": "Blue Note Records"}, "blue note")
        assert utils.label_matches({"name": "Blue Note Records"}, "RECORDS")

    def test_nome_diferente_nao_casa(self):
        assert not utils.label_matches({"name": "Blue Note"}, "Verve")

    def test_gravadora_ausente_nunca_casa_com_filtro(self):
        assert not utils.label_matches(None, "Verve")
        assert not utils.label_matches({}, "Verve")
        assert not utils.label_matches({"name": ""}, "Verve")

    def test_espacos_do_filtro_sao_ignorados(self):
        assert utils.label_matches({"name": "Verve"}, "  verve  ")


class TestNormalizeLabelFilter:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            (None, None),
            ("", None),
            ("   ", None),
            ("Blue Note", "Blue Note"),
            ("  123  ", "123"),
            ("https://www.qobuz.com/br-pt/label/blue-note/download-streaming/123", "123"),
            ("https://www.qobuz.com/us-en/label/blue-note/456/", "456"),
            ("https://play.qobuz.com/label/789?ref=abc", "789"),
        ],
    )
    def test_nome_id_ou_url(self, entrada, esperado):
        assert utils.normalize_label_filter(entrada) == esperado

    def test_url_sem_label_nao_e_interpretada(self):
        assert (
            utils.normalize_label_filter("https://exemplo.com/outra/coisa")
            == "https://exemplo.com/outra/coisa"
        )


class TestFilterChunksByLabel:
    @staticmethod
    def _conteudo():
        return [
            {
                "albums": {
                    "items": [
                        {"id": "a", "label": {"name": "Blue Note"}},
                        {"id": "b", "label": {"name": "Verve"}},
                        {"id": "c"},  # sem campo label: fica
                    ]
                }
            },
            {"albums": {"items": [{"id": "d", "label": {"id": 5, "name": "Verve"}}]}},
        ]

    def test_remove_outras_gravadoras_e_devolve_contagem(self):
        conteudo = self._conteudo()
        total, mantidos = utils.filter_chunks_by_label(conteudo, "albums", "verve")
        assert (total, mantidos) == (4, 3)
        ids = [i["id"] for ch in conteudo for i in ch["albums"]["items"]]
        assert ids == ["b", "c", "d"]

    def test_filtra_pelo_id(self):
        conteudo = self._conteudo()
        _, mantidos = utils.filter_chunks_by_label(conteudo, "albums", "5")
        # "d" casa pelo ID; "c" fica por não ter label; "a"/"b" saem.
        assert mantidos == 2

    def test_item_sem_label_nunca_e_removido(self):
        conteudo = [{"albums": {"items": [{"id": "x"}]}}]
        assert utils.filter_chunks_by_label(conteudo, "albums", "qualquer") == (1, 1)

    def test_chunk_sem_a_secao_nao_quebra(self):
        conteudo = [{"outra": {}}, {"albums": None}, {"albums": {}}]
        assert utils.filter_chunks_by_label(conteudo, "albums", "x") == (0, 0)

    def test_lista_vazia(self):
        assert utils.filter_chunks_by_label([], "albums", "x") == (0, 0)


class TestReleaseDate:
    def test_prefere_a_data_original(self):
        item = {"release_date_original": "1999-01-01", "release_date": "2020-05-05"}
        assert utils.release_date_key(item) == "1999-01-01"

    def test_cai_para_release_date(self):
        assert utils.release_date_key({"release_date": "2020-05-05"}) == "2020-05-05"

    def test_cai_para_o_album_da_faixa(self):
        item = {"album": {"release_date_original": "2001-02-03"}}
        assert utils.release_date_key(item) == "2001-02-03"
        item = {"album": {"release_date": "2002-03-04"}}
        assert utils.release_date_key(item) == "2002-03-04"

    @pytest.mark.parametrize("item", [{}, None, "texto", 5, {"album": "x"}, {"album": None}])
    def test_sem_data_ou_formato_inesperado(self, item):
        assert utils.release_date_key(item) == ""

    def test_sort_do_mais_novo_para_o_mais_antigo(self):
        itens = [
            {"id": 1, "release_date": "2001-01-01"},
            {"id": 2, "release_date": "2020-01-01"},
            {"id": 3, "release_date": "2010-06-01"},
        ]
        assert [i["id"] for i in utils.sort_by_release_date(itens)] == [2, 3, 1]

    def test_sem_data_vai_para_o_fim(self):
        itens = [{"id": "sem"}, {"id": "com", "release_date": "1990-01-01"}]
        assert [i["id"] for i in utils.sort_by_release_date(itens)] == ["com", "sem"]

    def test_nao_altera_a_lista_original(self):
        itens = [{"id": 1, "release_date": "2001"}, {"id": 2, "release_date": "2020"}]
        copia = list(itens)
        utils.sort_by_release_date(itens)
        assert itens == copia

    def test_lista_vazia(self):
        assert utils.sort_by_release_date([]) == []


class TestPartialFormatter:
    def test_campo_presente(self):
        assert PartialFormatter().format("{artist} - {title}", artist="A", title="B") == "A - B"

    def test_campo_ausente_vira_missing(self):
        assert PartialFormatter().format("{artist} - {title}", artist="A") == "A - n/a"

    def test_valor_vazio_ou_none_vira_missing(self):
        fmt = PartialFormatter(missing="?")
        assert fmt.format("{a}|{b}|{c}", a="", b=None, c=0) == "?|?|?"

    def test_missing_personalizado(self):
        assert PartialFormatter(missing="Desconhecido").format("{x}") == "Desconhecido"

    def test_spec_invalido_vira_bad_fmt(self):
        assert PartialFormatter(bad_fmt="!!").format("{x:d}", x="texto") == "!!"

    def test_spec_invalido_sem_bad_fmt_propaga(self):
        with pytest.raises(ValueError):
            PartialFormatter(bad_fmt="").format("{x:d}", x="texto")

    def test_spec_valido_e_aplicado(self):
        assert PartialFormatter().format("{n:02d}", n=7) == "07"

    def test_atributo_ausente_nao_levanta(self):
        class Obj:
            pass

        assert PartialFormatter().format("{o.nome}", o=Obj()) == "n/a"
