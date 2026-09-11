"""Testa as funções puras de formatação/normalização de qobuz_dl/metadata.py
-- nada aqui grava tag em arquivo de verdade nem toca imagem; é só a
lógica de string/nome que alimenta tag_flac()/tag_mp3().
"""

from qobuz_dl.metadata import (
    _format_copyright,
    _format_genres,
    _get_cover_path,
    _get_title,
    _get_title_with_version,
    _make_sort_name,
    _normalize_name,
)


# --------------------------------------------------------------------
# _make_sort_name
# --------------------------------------------------------------------
class TestMakeSortName:
    def test_artigo_the_vai_pro_final(self):
        assert _make_sort_name("The Beatles") == "Beatles, The"

    def test_sem_artigo_nao_muda(self):
        assert _make_sort_name("Metallica") == "Metallica"

    def test_apostrofo_l_e_tratado_sem_espaco_apos(self):
        assert _make_sort_name("L'Orchestra") == "Orchestra, L'"

    def test_lista_de_artistas_processa_cada_um(self):
        assert (
            _make_sort_name(["The Beatles", "Metallica"]) == "Beatles, The, Metallica"
        )

    def test_lista_ignora_itens_vazios(self):
        assert _make_sort_name(["The Beatles", "", None]) == "Beatles, The"

    def test_none_ou_vazio_vira_string_vazia(self):
        assert _make_sort_name(None) == ""
        assert _make_sort_name("") == ""

    def test_artigo_no_meio_do_nome_nao_conta(self):
        # Só artigo no INÍCIO do nome deve mover -- "Return of The King"
        # não é "The Return of King".
        assert _make_sort_name("Return of The King") == "Return of The King"

    def test_valor_nao_string_e_convertido(self):
        # name = str(name) no começo da função -- não pode explodir com
        # um tipo inesperado vindo da API.
        assert _make_sort_name(123) == "123"


# --------------------------------------------------------------------
# _get_title_with_version / _get_title
# --------------------------------------------------------------------
class TestGetTitleComVersao:
    def test_versao_e_anexada_quando_ausente_do_titulo(self):
        assert _get_title_with_version("Faixa", "Remix") == "Faixa (Remix)"

    def test_versao_ja_presente_no_titulo_nao_duplica(self):
        # Comparação é case-insensitive -- "remix" dentro de "Faixa
        # (Remix)" não pode virar "Faixa (Remix) (Remix)".
        assert _get_title_with_version("Faixa (Remix)", "remix") == "Faixa (Remix)"

    def test_sem_versao_titulo_fica_igual(self):
        assert _get_title_with_version("Faixa", "") == "Faixa"

    def test_titulo_vazio_com_versao(self):
        assert _get_title_with_version("", "Live") == " (Live)"


class TestGetTitle:
    def test_titulo_simples_sem_versao_nem_obra(self):
        assert _get_title({"title": "Faixa"}) == "Faixa"

    def test_titulo_com_versao(self):
        assert _get_title({"title": "Faixa", "version": "Remix"}) == "Faixa (Remix)"

    def test_titulo_com_obra_classica_e_versao(self):
        resultado = _get_title(
            {"title": "Allegro", "version": "Live", "work": "Symphony No. 5"}
        )
        assert resultado == "Symphony No. 5: Allegro (Live)"

    def test_titulo_sem_work_nem_version_no_dict(self):
        # .get() pra work/version -- não pode exigir as chaves presentes.
        assert _get_title({"title": "Faixa"}) == "Faixa"


# --------------------------------------------------------------------
# _format_copyright
# --------------------------------------------------------------------
class TestFormatCopyright:
    def test_marcador_p_vira_simbolo_fonografico(self):
        assert _format_copyright("(P) 2024 Gravadora") == "\u2117 2024 Gravadora"

    def test_marcador_c_vira_simbolo_de_copyright(self):
        assert _format_copyright("(C) 2024 Gravadora") == "\u00a9 2024 Gravadora"

    def test_ambos_marcadores_na_mesma_string(self):
        resultado = _format_copyright("(C) 2024 (P) 2024 Gravadora")
        assert resultado == "\u00a9 2024 \u2117 2024 Gravadora"

    def test_string_vazia_ou_none_passa_direto(self):
        assert _format_copyright("") == ""
        assert _format_copyright(None) is None

    def test_sem_marcador_nao_muda(self):
        assert _format_copyright("2024 Gravadora") == "2024 Gravadora"


# --------------------------------------------------------------------
# _format_genres
# --------------------------------------------------------------------
class TestFormatGenres:
    def test_seta_de_hierarquia_e_dividida(self):
        # "\u2192" é a seta usada pela API do Qobuz pra indicar hierarquia
        # de gênero (Rock -> Rock Alternativo).
        assert (
            _format_genres(["Rock\u2192Rock Alternativo"]) == "Rock, Rock Alternativo"
        )

    def test_barra_tambem_divide(self):
        assert _format_genres(["Pop/Dance"]) == "Pop, Dance"

    def test_generos_duplicados_sao_removidos_mantendo_ordem(self):
        assert _format_genres(["Rock", "Pop", "Rock"]) == "Rock, Pop"

    def test_lista_vazia_vira_string_vazia(self):
        assert _format_genres([]) == ""

    def test_multiplos_generos_com_hierarquia_cada_um(self):
        resultado = _format_genres(["Rock\u2192Indie", "Pop\u2192Synth-pop"])
        assert resultado == "Rock, Indie, Pop, Synth-pop"


# --------------------------------------------------------------------
# _normalize_name
# --------------------------------------------------------------------
class TestNormalizeName:
    def test_acentos_sao_removidos(self):
        assert _normalize_name("Café Tacvba") == "cafe tacvba"

    def test_maiusculas_viram_minusculas(self):
        assert _normalize_name("METALLICA") == "metallica"

    def test_espacos_nas_pontas_sao_removidos(self):
        assert _normalize_name("  Sigur Rós  ") == "sigur ros"

    def test_lista_e_unida_com_virgula(self):
        assert _normalize_name(["Café Tacvba", "Motörhead"]) == "cafe tacvba, motorhead"

    def test_lista_ignora_itens_vazios(self):
        assert _normalize_name(["Café Tacvba", "", None]) == "cafe tacvba"

    def test_mesmo_nome_com_acento_diferente_normaliza_igual(self):
        # É exatamente esse o propósito da função: detectar duplicata
        # apesar da diferença de acentuação/caixa.
        assert _normalize_name("Sigur Rós") == _normalize_name("SIGUR ROS")


# --------------------------------------------------------------------
# _get_cover_path
# --------------------------------------------------------------------
class TestGetCoverPath:
    def test_override_valido_tem_prioridade(self, tmp_path):
        capa_custom = tmp_path / "minha_capa.jpg"
        capa_custom.write_bytes(b"fake-jpeg-bytes")
        capa_embutida = tmp_path / "embed_cover.jpg"
        capa_embutida.write_bytes(b"outra-capa")

        resultado = _get_cover_path(str(tmp_path), override=str(capa_custom))
        assert resultado == str(capa_custom)

    def test_override_inexistente_e_ignorado_cai_pro_embed(self, tmp_path):
        capa_embutida = tmp_path / "embed_cover.jpg"
        capa_embutida.write_bytes(b"capa")

        resultado = _get_cover_path(
            str(tmp_path), override=str(tmp_path / "nao-existe.jpg")
        )
        assert resultado == str(capa_embutida)

    def test_capa_na_pasta_atual_e_encontrada(self, tmp_path):
        capa = tmp_path / "embed_cover.jpg"
        capa.write_bytes(b"capa")
        assert _get_cover_path(str(tmp_path)) == str(capa)

    def test_capa_na_pasta_pai_e_encontrada_multi_disco(self, tmp_path):
        # Cenário de álbum multi-disco: a capa fica na pasta pai
        # (compartilhada entre CD1/CD2), não em cada subpasta de disco.
        disco1 = tmp_path / "CD1"
        disco1.mkdir()
        capa_pai = tmp_path / "embed_cover.jpg"
        capa_pai.write_bytes(b"capa")

        resultado = _get_cover_path(str(disco1))
        assert resultado == str(capa_pai)

    def test_pasta_atual_tem_prioridade_sobre_a_pai(self, tmp_path):
        disco1 = tmp_path / "CD1"
        disco1.mkdir()
        capa_local = disco1 / "embed_cover.jpg"
        capa_local.write_bytes(b"capa local")
        capa_pai = tmp_path / "embed_cover.jpg"
        capa_pai.write_bytes(b"capa pai")

        resultado = _get_cover_path(str(disco1))
        assert resultado == str(capa_local)

    def test_nenhuma_capa_encontrada_devolve_none(self, tmp_path):
        assert _get_cover_path(str(tmp_path)) is None


# --------------------------------------------------------------------
# Novas validações de resiliência e tratamento de bordas
# --------------------------------------------------------------------
class TestMetadataBordasETratamento:
    def test_format_genres_com_strings_e_espacos(self):
        resultado = _format_genres(["  Pop  ", "Rock → Alternative  "])
        assert "Pop" in resultado
        assert "Rock" in resultado
        assert "Alternative" in resultado

    def test_get_title_with_version_formatacao_padrao(self):
        assert (
            _get_title_with_version("Track Title", "Deluxe") == "Track Title (Deluxe)"
        )
