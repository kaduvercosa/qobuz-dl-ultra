"""Testa qobuz_dl/core.py -- QobuzDL._extract_rich_metadata().

Método puro (não usa `self` em nenhum ponto do corpo -- só transforma o
JSON bruto da API num dict "meta" pra exibir na TUI de busca), por isso
chamado aqui como método "unbound" (`core.QobuzDL._extract_rich_metadata`)
com `None` no lugar de `self`, sem precisar construir uma instância real
de QobuzDL.

Contém a heurística de classificação Album/EP/Single/Live/Compilation
(via `classify_release_type`, chamada aqui como `_classify_release_type`)
-- ver o próprio docstring do método em core.py pra mais contexto de por
que essa lógica existe.
"""

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


def _extrai(i, item_type, mode_dict=None, fav_subtype=None):
    return core.QobuzDL._extract_rich_metadata(
        None, i, item_type, mode_dict or {}, fav_subtype
    )


# --------------------------------------------------------------------
# Ramo "rico" (album/track, ou requires_extra=True) -- campos básicos
# --------------------------------------------------------------------
class TestRamoRicoCamposBasicos:
    def test_album_completo_com_hires_version_e_parental(self):
        item = {
            "id": "abc123",
            "artist": {"name": "Benson Boone"},
            "title": "American Heart",
            "version": "Deluxe",
            "parental_warning": True,
            "release_date_original": "2025-06-20",
            "tracks_count": 10,
            "genre": {"name": "Pop"},
            "label": {"name": "Night Street Records"},
            "duration": 2415,
            "hires_streamable": True,
            "maximum_bit_depth": 24,
            "maximum_sampling_rate": 96.0,
        }
        meta = _extrai(item, "album")

        assert meta["artist"] == "Benson Boone"
        assert meta["title"] == "American Heart (Deluxe) [E]"
        assert meta["year"] == "2025"
        assert meta["tracks_count"] == 10
        assert meta["genre"] == "Pop"
        assert meta["label"] == "Night Street Records"
        assert meta["quality"] == "24b/96.0kHz"
        assert meta["duration"] == "00:40:15"
        assert meta["id"] == "abc123"
        # 10 faixas -> classify_release_type() classifica como "album"
        # mesmo sem nenhuma tag explícita da API.
        assert meta["type"] == "Album"

    def test_sem_hires_usa_qualidade_padrao(self):
        item = {"title": "Faixa", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["quality"] == "16b/44.1kHz"

    def test_duracao_ausente_formata_como_tracos(self):
        item = {"title": "Faixa", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["duration"] == "--:--"

    def test_duracao_zero_tambem_formata_como_tracos(self):
        item = {"title": "Faixa", "tracks_count": 1, "duration": 0}
        meta = _extrai(item, "track")
        assert meta["duration"] == "--:--"

    def test_sem_version_nem_parental_titulo_fica_limpo(self):
        item = {"title": "Só o Título", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["title"] == "Só o Título"


# --------------------------------------------------------------------
# Fallbacks de campo ausente
# --------------------------------------------------------------------
class TestFallbacksDeCampoAusente:
    def test_artista_ausente_cai_pro_performer(self):
        item = {"performer": {"name": "Fulano"}, "title": "X", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["artist"] == "Fulano"

    def test_artista_e_performer_ausentes_vira_unknown(self):
        item = {"title": "X", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["artist"] == "Unknown"

    def test_titulo_ausente_cai_pro_name(self):
        item = {"name": "Nome Alternativo", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["title"] == "Nome Alternativo"

    def test_titulo_e_name_ausentes_vira_unknown(self):
        item = {"tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["title"] == "Unknown"

    def test_ano_cai_pro_release_date_quando_original_ausente(self):
        item = {"title": "X", "release_date": "2020-01-01", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["year"] == "2020"

    def test_ano_ausente_fica_com_espacos(self):
        item = {"title": "X", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["year"] == "    "

    def test_album_aninhado_ausente_vira_unknown_album(self):
        item = {"title": "X", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["album"] == "Unknown Album"

    def test_album_aninhado_presente_extrai_titulo(self):
        item = {"title": "X", "tracks_count": 1, "album": {"title": "O Álbum"}}
        meta = _extrai(item, "track")
        assert meta["album"] == "O Álbum"


# --------------------------------------------------------------------
# genre/label podem vir como dict OU como string direta da API
# --------------------------------------------------------------------
class TestGeneroELabelFormatosMistos:
    def test_genero_e_label_como_string_direta(self):
        item = {"title": "X", "tracks_count": 1, "genre": "Rock", "label": "Indie Co"}
        meta = _extrai(item, "track")
        assert meta["genre"] == "Rock"
        assert meta["label"] == "Indie Co"

    def test_genero_e_label_ausentes_ficam_vazios(self):
        item = {"title": "X", "tracks_count": 1}
        meta = _extrai(item, "track")
        assert meta["genre"] == ""
        assert meta["label"] == ""


# --------------------------------------------------------------------
# Classificação de tipo de release (delega pra classify_release_type,
# mas confere que os dados certos chegam até ela e que o rótulo final
# formatado sai correto -- "EP" maiúsculo, resto Title Case).
# --------------------------------------------------------------------
class TestClassificacaoDeTipo:
    def test_ep_no_titulo_prioriza_sobre_contagem_alta(self):
        # 10 faixas sozinho classificaria como "album", mas "EP" explícito
        # no título tem prioridade sobre contagem (ver classify_release_type).
        item = {"title": "Minha Coletânea EP", "tracks_count": 10}
        meta = _extrai(item, "album")
        assert meta["type"] == "EP"

    def test_live_na_versao_classifica_como_live(self):
        item = {"title": "Show Ao Vivo", "version": "Live", "tracks_count": 12}
        meta = _extrai(item, "album")
        assert meta["type"] == "Live"

    def test_contagem_baixa_classifica_como_single(self):
        item = {"title": "Faixa Solo", "tracks_count": 2}
        meta = _extrai(item, "album")
        assert meta["type"] == "Single"

    def test_release_type_unknown_busca_dentro_do_album_aninhado(self):
        # Sem release_type/product_type no item de topo, mas o "album"
        # aninhado tem release_type="ep" -- e sem contagem/duração pra
        # sobrepor, classify_release_type cai pra essa tag resgatada.
        item = {
            "title": "Faixa Sem Palavra-Chave",
            "album": {"title": "Some Album", "release_type": "ep"},
        }
        meta = _extrai(item, "track")
        assert meta["type"] == "EP"

    def test_release_type_explicito_no_item_e_usado(self):
        item = {"title": "Faixa X", "release_type": "single", "tracks_count": 0}
        meta = _extrai(item, "track")
        assert meta["type"] == "Single"


# --------------------------------------------------------------------
# Ramo "requires_extra" -- item_type nem sempre é literalmente
# "album"/"track", mas mode_dict pode forçar o ramo rico mesmo assim.
# --------------------------------------------------------------------
class TestRequiresExtraForcaRamoRico:
    def test_requires_extra_true_forca_ramo_rico(self):
        item = {"title": "Faixa Favorita", "tracks_count": 1}
        meta = _extrai(item, "favorite_track", mode_dict={"requires_extra": True})
        # Ramo rico -> tem "title"/"type"/"quality", não "name"/"count"
        assert meta["title"] == "Faixa Favorita"
        assert "type" in meta

    def test_requires_extra_false_e_item_type_generico_usa_ramo_simples(self):
        item = {"name": "Artista Favorito", "tracks_count": 5}
        meta = _extrai(item, "favorite_artist", mode_dict={"requires_extra": False})
        assert meta == {"name": "Artista Favorito", "count": 5, "id": None}


# --------------------------------------------------------------------
# Ramo "simples" -- playlists (com owner) vs genérico (artista/gênero)
# --------------------------------------------------------------------
class TestRamoSimples:
    def test_playlist_extrai_dono_e_contagem_de_faixas(self):
        item = {
            "id": 42,
            "name": "Minha Playlist",
            "owner": {"name": "Fulano"},
            "tracks_count": 30,
            "duration": 5400,
        }
        meta = _extrai(item, "playlist")
        assert meta == {
            "name": "Minha Playlist",
            "owner": "Fulano",
            "count": 30,
            "duration": "01:30:00",
            "id": 42,
        }

    def test_fav_subtype_playlists_forca_ramo_de_playlist_mesmo_com_outro_item_type(
        self,
    ):
        item = {"name": "X", "owner": {"name": "Ciclano"}, "tracks_count": 3}
        meta = _extrai(item, "favorites", fav_subtype="playlists")
        assert meta["owner"] == "Ciclano"

    def test_playlist_sem_owner_vira_unknown(self):
        item = {"name": "X", "tracks_count": 3}
        meta = _extrai(item, "playlist")
        assert meta["owner"] == "Unknown"

    def test_generico_prioriza_albums_count_sobre_tracks_count(self):
        item = {"name": "Um Artista", "albums_count": 7, "tracks_count": 200}
        meta = _extrai(item, "artist")
        assert meta == {"name": "Um Artista", "count": 7, "id": None}

    def test_generico_sem_albums_count_usa_tracks_count(self):
        item = {"name": "Um Gênero", "tracks_count": 15}
        meta = _extrai(item, "genre")
        assert meta["count"] == 15

    def test_generico_sem_nenhuma_contagem_fica_zero(self):
        item = {"name": "X"}
        meta = _extrai(item, "genre")
        assert meta["count"] == 0

    def test_generico_sem_name_vira_unknown(self):
        item = {}
        meta = _extrai(item, "genre")
        assert meta["name"] == "Unknown"
