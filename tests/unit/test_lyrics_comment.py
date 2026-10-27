"""Linha "Letra: <origem> (...)" do COMMENT (qobuz_dl.lyrics_comment)."""

import pytest

from qobuz_dl import lyrics_comment as lc

pytestmark = pytest.mark.unit


class TestBuildLine:
    def test_bilingue_com_idioma(self):
        linha = lc.build_line("Qobuz", bilingual=True, language="en+pt")
        assert linha == "Letra: Qobuz (sincronizada, bilíngue EN+PT)"

    def test_so_um_idioma(self):
        linha = lc.build_line("BiniLyrics", language="en")
        assert linha == "Letra: BiniLyrics (sincronizada, EN)"

    def test_idioma_desconhecido_e_omitido(self):
        assert lc.build_line("LRCLIB", language="unknown") == (
            "Letra: LRCLIB (sincronizada)"
        )

    def test_nao_usa_o_separador_do_mp3_nem_quebra_de_linha(self):
        linha = lc.build_line("Qobuz", bilingual=True, language="en+pt")
        assert " | " not in linha and "\n" not in linha


class TestSourceFromComment:
    @pytest.mark.parametrize(
        "texto, esperado",
        [
            ("Letra: Qobuz (sincronizada, EN)", "Qobuz"),
            (
                "Fonte: Qobuz\nLetra: Musixmatch (sincronizada)\nTipo: Album",
                "Musixmatch",
            ),
            ("Qobuz | Trk ID: 1 | Letra: BiniLyrics (sincronizada, EN)", "BiniLyrics"),
            ("Qobuz | Trk ID: 1", None),
            ("", None),
            (None, None),
        ],
    )
    def test_extrai_a_origem(self, texto, esperado):
        assert lc.source_from_comment(texto) == esperado

    def test_aceita_lista_de_valores(self):
        assert lc.source_from_comment(["x", "Letra: LRCLIB (sincronizada)"]) == "LRCLIB"


class TestFlac:
    def test_merge_troca_a_linha_antiga_e_mantem_as_outras(self):
        antigo = ["Fonte: Qobuz\nLetra: LRCLIB (sincronizada)\nCapa: Apple/iTunes"]
        novo = lc.merge_flac(antigo, "Letra: Qobuz (sincronizada, EN)")
        assert novo.splitlines() == [
            "Fonte: Qobuz",
            "Capa: Apple/iTunes",
            "Letra: Qobuz (sincronizada, EN)",
        ]

    def test_merge_sem_comentario_previo(self):
        assert lc.merge_flac(None, "Letra: Qobuz (sincronizada)") == (
            "Letra: Qobuz (sincronizada)"
        )

    def test_extract_so_devolve_linhas_de_letra(self):
        valores = [
            "Fonte: Qobuz\nLetra: Qobuz (sincronizada)",
            "Letra: Qobuz (sincronizada)",
        ]
        assert lc.extract_flac_lines(valores) == ["Letra: Qobuz (sincronizada)"]


class TestMp3:
    def test_merge_troca_o_trecho_antigo_e_mantem_os_outros(self):
        antigo = "Qobuz | Trk ID: 1 | Letra: LRCLIB (sincronizada) | Capa: Apple/iTunes"
        novo = lc.merge_mp3(antigo, "Letra: Qobuz (sincronizada, EN)")
        assert novo == (
            "Qobuz | Trk ID: 1 | Capa: Apple/iTunes | Letra: Qobuz (sincronizada, EN)"
        )

    def test_merge_sem_comentario_previo(self):
        assert lc.merge_mp3("", "Letra: Qobuz (sincronizada)") == (
            "Letra: Qobuz (sincronizada)"
        )

    def test_extract_so_devolve_trechos_de_letra(self):
        texto = "Qobuz | Trk ID: 1 | Letra: Qobuz (sincronizada, EN)"
        assert lc.extract_mp3_segments(texto) == ["Letra: Qobuz (sincronizada, EN)"]
