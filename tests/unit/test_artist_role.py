"""Funcao do artista pesquisado numa faixa (coluna FUNCAO da TUI)."""
import pytest

from qobuz_dl.artist_role import (
    SEM_FUNCAO,
    _parse_performers,
    funcao_do_artista,
    nome_casa_com_consulta,
)


class TestNomeCasa:
    @pytest.mark.parametrize(
        "nome,consulta",
        [
            ("The Weeknd", "the weeknd"),
            ("The Weeknd", "weeknd"),
            ("Beyoncé", "beyonce"),
            ("The Weeknd", "blinding lights the weeknd"),
            ("The Weeknd", "the weekend"),  # erro de digitacao
        ],
    )
    def test_casa(self, nome, consulta):
        assert nome_casa_com_consulta(nome, consulta)

    @pytest.mark.parametrize(
        "nome,consulta",
        [
            ("Phoebe Bridgers", "the weeknd"),
            ("Arctic Monkeys", "art"),
            ("Coldplay", "the weeknd"),
            ("", "the weeknd"),
        ],
    )
    def test_nao_casa(self, nome, consulta):
        assert not nome_casa_com_consulta(nome, consulta)


class TestParsePerformers:
    def test_basico(self):
        r = _parse_performers("A, MainArtist - B, Producer, Composer")
        assert r == [("A", ["MainArtist"]), ("B", ["Producer", "Composer"])]

    def test_nome_com_virgula(self):
        r = _parse_performers("Tyler, The Creator, MainArtist")
        assert r == [("Tyler, The Creator", ["MainArtist"])]

    def test_vazio(self):
        assert _parse_performers("") == []
        assert _parse_performers(None) == []


class TestFuncao:
    def test_principal_por_performer(self):
        item = {"title": "Blinding Lights", "performer": {"name": "The Weeknd"}}
        assert funcao_do_artista(item, "the weeknd") == ("Principal", "Principal")

    def test_feat_no_titulo(self):
        item = {
            "title": "Timeless (feat. The Weeknd)",
            "performer": {"name": "Playboi Carti"},
        }
        assert funcao_do_artista(item, "the weeknd") == ("Feat.", "Feat.")

    def test_creditos_completos(self):
        item = {
            "performer": {"name": "Outro"},
            "performers": "The Weeknd, FeaturedArtist, Composer - Metro, Producer",
        }
        # Feat. tem prioridade sobre Compositor: mostra so' uma funcao
        assert funcao_do_artista(item, "the weeknd") == ("Feat.", "Feat.")

    def test_principal_vence_todas(self):
        item = {
            "performer": {"name": "The Weeknd"},
            "performers": "The Weeknd, MainArtist, Composer, Producer",
        }
        assert funcao_do_artista(item, "the weeknd") == ("Principal", "Principal")

    def test_so_produtor(self):
        item = {
            "performer": {"name": "Outro"},
            "performers": "The Weeknd, Producer",
        }
        assert funcao_do_artista(item, "the weeknd")[0] == "Produtor"

    def test_sem_relacao(self):
        item = {"title": "Lost Weekend", "performer": {"name": "Phoebe Bridgers"}}
        assert funcao_do_artista(item, "the weeknd") == (SEM_FUNCAO, SEM_FUNCAO)

    def test_sem_consulta(self):
        item = {"performer": {"name": "The Weeknd"}}
        assert funcao_do_artista(item, None) == (SEM_FUNCAO, SEM_FUNCAO)
        assert funcao_do_artista(item, "") == (SEM_FUNCAO, SEM_FUNCAO)
