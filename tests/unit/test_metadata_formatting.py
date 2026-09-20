"""Testa as funções puras de formatação/normalização de qobuz_dl/metadata.py
-- nada aqui grava tag em arquivo de verdade nem toca imagem; é só a
lógica de string/nome que alimenta tag_flac()/tag_mp3().
"""

from qobuz_dl.metadata import (
    _format_copyright,
    _format_genres,
    _get_cover_path,
    _get_tags_to_add,
    _get_title,
    _get_title_with_version,
    _make_sort_name,
    _normalize_name,
)
from qobuz_dl.settings import QobuzDLSettings


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


# --------------------------------------------------------------------
# _get_tags_to_add
# --------------------------------------------------------------------
def _album(**overrides):
    base = {
        "title": "Álbum Teste",
        "version": "",
        "artist": {"name": "Artista Principal"},
        "genre": {"name": "Électronique"},
        "genres_list": [],
        "release_date_original": "2024-05-01",
        "copyright": "(P) 2024 Gravadora (C) 2024 Editora",
        "label": {"name": "Gravadora   Teste"},
        "upc": "1234567890123",
        "product_type": "album",
        "release_type": "album",
        "tracks_count": 10,
        "duration": 3000,
        "id": 999,
    }
    base.update(overrides)
    return base


def _item(**overrides):
    base = {
        "title": "Faixa Teste",
        "version": "",
        "performer": {"name": "Artista Principal"},
        "performers": (
            "Artista Principal, MainArtist - Compositor Tal, Composer, ComposerLyricist"
        ),
        "composer": {"name": "Compositor Fallback"},
        "isrc": "US1234567890",
        "parental_warning": False,
        "id": 555,
        "audio_info": {},
    }
    base.update(overrides)
    return base


class TestGetTagsToAdd:
    def test_album_ou_item_vazio_devolve_dict_vazio(self):
        settings = QobuzDLSettings()
        assert _get_tags_to_add(None, _item(), settings) == {}
        assert _get_tags_to_add(_album(), None, settings) == {}
        assert _get_tags_to_add({}, {}, settings) == {}

    def test_tags_basicas_de_album_e_faixa(self):
        settings = QobuzDLSettings()
        tags = _get_tags_to_add(_album(), _item(), settings)

        assert tags["ALBUM"] == "Álbum Teste"
        assert tags["TITLE"] == "Faixa Teste"
        assert tags["DATE"] == "2024-05-01"
        assert tags["ISRC"] == "US1234567890"
        assert tags["BARCODE"] == "1234567890123"
        assert tags["MEDIATYPE"] == "ALBUM"

    def test_titulo_nao_duplica_versao_ja_presente(self):
        settings = QobuzDLSettings()
        item = _item(title="Faixa Teste (Ao Vivo)", version="Ao Vivo")

        tags = _get_tags_to_add(_album(), item, settings)

        assert tags["TITLE"] == "Faixa Teste (Ao Vivo)"

    def test_faixa_explicita_marca_emoji_e_tags_de_aviso(self):
        settings = QobuzDLSettings()
        tags_explicita = _get_tags_to_add(
            _album(), _item(parental_warning=True), settings
        )
        tags_normal = _get_tags_to_add(
            _album(), _item(parental_warning=False), settings
        )

        assert tags_explicita["TITLE"] == "Faixa Teste 🅴"
        assert tags_explicita["ITUNESADVISORY"] == "1"
        assert tags_explicita["EXPLICIT"] == "1"
        assert tags_explicita["RATING"] == "Explicit"

        assert tags_normal["TITLE"] == "Faixa Teste"
        assert tags_normal["ITUNESADVISORY"] == ""
        assert tags_normal["EXPLICIT"] == ""
        assert tags_normal["RATING"] == ""

    def test_album_artist_e_sort_name_vem_como_lista(self):
        # get_album_artist() devolve LISTA (multi-artist tagging nativo em
        # FLAC/Vorbis) -- ALBUMARTIST não é string aqui.
        settings = QobuzDLSettings()
        album = _album(artist={"name": "The Beatles"})

        tags = _get_tags_to_add(album, _item(), settings)

        assert tags["ALBUMARTIST"] == ["The Beatles"]
        assert tags["ALBUMARTISTSORT"] == "Beatles, The"

    def test_artistas_da_faixa_deduplicados_por_performers(self):
        settings = QobuzDLSettings()
        item = _item(
            performer={"name": "Artista X"},
            performers=(
                "Artista X, MainArtist - "
                "artista x, FeaturedArtist - "  # mesmo nome, acento/caixa diferentes
                "Produtor Y, Producer - "  # role não elegível, não deve entrar
                "Artista Z, PrimaryArtist"
            ),
        )

        tags = _get_tags_to_add(_album(), item, settings)

        assert tags["ARTIST"] == "Artista X, Artista Z"

    def test_artist_vazio_quando_sem_performer_nem_artist_do_album(self):
        settings = QobuzDLSettings()
        album = _album(artist={})
        item = _item(performer={}, performers="")

        tags = _get_tags_to_add(album, item, settings)

        assert tags["ARTIST"] == ""
        assert "ARTISTSORT" not in tags

    def test_compositor_via_performers_tem_prioridade_sobre_fallback(self):
        settings = QobuzDLSettings()
        tags = _get_tags_to_add(_album(), _item(), settings)

        assert tags["COMPOSER"] == "Compositor Tal"

    def test_compositor_cai_pro_fallback_sem_performers_elegiveis(self):
        settings = QobuzDLSettings()
        item = _item(performers="", composer={"name": "Compositor Fallback"})

        tags = _get_tags_to_add(_album(), item, settings)

        assert tags["COMPOSER"] == "Compositor Fallback"

    def test_genero_traduzido_pelo_mapa_local_sem_duplicar(self):
        settings = QobuzDLSettings()
        album = _album(
            genre={"name": "Électronique"},
            genres_list=["Électronique", "Ambiance"],
        )

        tags = _get_tags_to_add(album, _item(), settings)

        # genre principal (traduzido) substitui o primeiro item da lista
        assert tags["GENRE"] == "Electronic, Ambient"

    def test_copyright_formatado_e_label_com_espacos_colapsados(self):
        settings = QobuzDLSettings()
        tags = _get_tags_to_add(_album(), _item(), settings)

        assert tags["COPYRIGHT"] == "\u2117 2024 Gravadora \u00a9 2024 Editora"
        assert tags["LABEL"] == "Gravadora Teste"

    def test_copyright_tag_e_controlada_por_no_label_tag_nao_no_copyright_tag(self):
        """[BUG CONHECIDO, não corrigido aqui] `_get_tags_to_add` esconde
        COPYRIGHT atrás de `settings.no_label_tag`, nunca lê
        `settings.no_copyright_tag` -- apesar dela existir em
        QobuzDLSettings e ter uma flag própria no CLI (`--no-copyright-tag`,
        commands.py). Resultado: passar só `--no-copyright-tag` não
        suprime a tag COPYRIGHT (só `--no-label-tag` suprime, e junto
        derruba LABEL também). Este teste documenta o comportamento
        ATUAL -- ver aviso na resposta sobre corrigir isso de verdade."""
        settings_no_copyright = QobuzDLSettings(no_copyright_tag=True)
        settings_no_label = QobuzDLSettings(no_label_tag=True)

        tags_no_copyright = _get_tags_to_add(_album(), _item(), settings_no_copyright)
        tags_no_label = _get_tags_to_add(_album(), _item(), settings_no_label)

        assert "COPYRIGHT" in tags_no_copyright  # comportamento atual (bug)
        assert "COPYRIGHT" not in tags_no_label
        assert "LABEL" not in tags_no_label

    def test_compilation_tag_via_classify_release_type(self):
        settings = QobuzDLSettings()
        album_compilation = _album(title="Greatest Hits", tracks_count=0, duration=0)
        album_normal = _album(title="Álbum Normal", tracks_count=10)

        tags_compilation = _get_tags_to_add(album_compilation, _item(), settings)
        tags_normal = _get_tags_to_add(album_normal, _item(), settings)

        assert tags_compilation["COMPILATION"] == "1"
        assert "COMPILATION" not in tags_normal

    def test_replaygain_somente_quando_presente_no_audio_info(self):
        settings = QobuzDLSettings()
        item_com_rg = _item(
            audio_info={
                "replaygain_track_gain": -6.5,
                "replaygain_track_peak": 0.98,
                "replaygain_album_gain": -7.0,
                "replaygain_album_peak": 0.99,
            }
        )
        item_sem_rg = _item(audio_info={})

        tags_com_rg = _get_tags_to_add(_album(), item_com_rg, settings)
        tags_sem_rg = _get_tags_to_add(_album(), item_sem_rg, settings)

        assert tags_com_rg["REPLAYGAIN_TRACK_GAIN"] == "-6.5 dB"
        assert tags_com_rg["REPLAYGAIN_TRACK_PEAK"] == "0.98"
        assert tags_com_rg["REPLAYGAIN_ALBUM_GAIN"] == "-7.0 dB"
        assert tags_com_rg["REPLAYGAIN_ALBUM_PEAK"] == "0.99"
        assert "REPLAYGAIN_TRACK_GAIN" not in tags_sem_rg

    def test_work_tag_apenas_quando_presente_e_habilitada(self):
        settings = QobuzDLSettings()
        tags_com_work = _get_tags_to_add(_album(), _item(work="Sinfonia N.5"), settings)
        tags_sem_work = _get_tags_to_add(_album(), _item(), settings)
        tags_desabilitada = _get_tags_to_add(
            _album(),
            _item(work="Sinfonia N.5"),
            QobuzDLSettings(no_work_tag=True),
        )

        assert tags_com_work["WORK"] == "Sinfonia N.5"
        assert "WORK" not in tags_sem_work
        assert "WORK" not in tags_desabilitada

    def test_regente_e_conjunto_via_performers(self):
        settings = QobuzDLSettings()
        item = _item(
            performers=(
                "Maestro Um, Conductor - Orquestra Tal, Orchestra - Coro Tal, Choir"
            )
        )

        tags = _get_tags_to_add(_album(), item, settings)

        assert tags["CONDUCTOR"] == "Maestro Um"  # 1 só -> string, não lista
        assert set(tags["ENSEMBLE"]) == {"Orquestra Tal", "Coro Tal"}

    def test_ids_qobuz_e_url_do_album_sao_montados(self):
        settings = QobuzDLSettings()
        album = _album(title="Test Album Two!", id=999)
        item = _item(id=555)

        tags = _get_tags_to_add(album, item, settings)

        assert tags["QOBUZTRACKID"] == "555"
        assert tags["QOBUZALBUMID"] == "999"
        assert (
            tags["QOBUZ ALBUM URL"] == "https://www.qobuz.com/album/test-album-two/999"
        )

    def test_flags_no_x_tag_suprimem_a_tag_correspondente(self):
        casos = [
            ("no_album_title_tag", ["ALBUM"]),
            ("no_track_title_tag", ["TITLE"]),
            ("no_album_artist_tag", ["ALBUMARTIST", "ALBUMARTISTSORT"]),
            ("no_composer_tag", ["COMPOSER"]),
            ("no_release_date_tag", ["DATE"]),
            ("no_genre_tag", ["GENRE"]),
            ("no_isrc_tag", ["ISRC"]),
            ("no_upc_tag", ["BARCODE"]),
            ("no_media_type_tag", ["MEDIATYPE"]),
            ("no_album_url_tag", ["QOBUZ ALBUM URL"]),
        ]
        for flag, chaves_esperadas_ausentes in casos:
            settings = QobuzDLSettings(**{flag: True})
            tags = _get_tags_to_add(_album(), _item(), settings)
            for chave in chaves_esperadas_ausentes:
                assert chave not in tags, f"{flag}=True deveria remover {chave}"

    def test_no_explicit_tag_suprime_as_tres_tags_de_aviso(self):
        settings = QobuzDLSettings(no_explicit_tag=True)
        tags = _get_tags_to_add(_album(), _item(parental_warning=True), settings)

        assert "ITUNESADVISORY" not in tags
        assert "EXPLICIT" not in tags
        assert "RATING" not in tags
