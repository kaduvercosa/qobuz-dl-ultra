"""Testa `tag_flac()`/`tag_mp3()` de qobuz_dl/metadata.py -- SEM abrir
arquivo de áudio de verdade. `FLAC(filename)`/`id3.ID3(filename)` são
trocados por fakes tipo-dict (mesmo padrão de test_lyrics_engine_unit.py
pro LyricsEngine): abrir um FLAC/MP3 de verdade exigiria um arquivo de
áudio válido só pra testar formatação de tag, que é overhead puro aqui.

Frames de ID3 (TIT2, TPE1, TXXX, COMM, etc.) continuam sendo os de
verdade do mutagen -- são só contêineres de dado, sem I/O, não precisam
de fake.

FORA DE ESCOPO de propósito: o caminho de recompactação de capa grande
demais (`_shrink_image_to_fit`, precisa do Pillow processando uma
imagem de verdade) e o embed de capa em si (`_embed_flac_img`/
`_embed_id3_img` como um todo) -- ver só um teste básico confirmando que
`add_picture`/`add(APIC)` é chamado quando `em_image=True`, sem entrar
no conteúdo da imagem.
"""

from types import SimpleNamespace

import mutagen.id3 as id3
import pytest
from mutagen.id3 import ID3NoHeaderError

import qobuz_dl.metadata as metadata
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------
class FakeFLAC(dict):
    """Fica no lugar de mutagen.flac.FLAC -- dict basta pra
    __contains__/__setitem__/__delitem__ que tag_flac() usa."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self.tags = SimpleNamespace(vendor="libFLAC algumaversao")
        self.saved_kwargs = None
        self.pictures = []

    def save(self, **kwargs):
        self.saved_kwargs = kwargs

    def add_picture(self, picture):
        self.pictures.append(picture)

    def clear_pictures(self):
        self.pictures.clear()


class FakeID3(dict):
    def __init__(self, filename=None):
        super().__init__()
        self.added = []
        self.saved_args = None
        self.popped = []

    def add(self, frame):
        self.added.append(frame)

    def pop(self, key, default=None):
        self.popped.append(key)
        return super().pop(key, default)

    def save(self, filename=None, **kwargs):
        self.saved_args = (filename, kwargs)


def _album(**overrides):
    base = {
        "title": "Album Teste",
        "version": "",
        "artist": {"name": "Artista"},
        "genre": {"name": "Rock"},
        "genres_list": [],
        "release_date_original": "2024-05-01",
        "copyright": "(C) 2024 Editora",
        "label": {"name": "Gravadora"},
        "upc": "1234567890123",
        "product_type": "album",
        "release_type": "album",
        "tracks_count": 10,
        "media_count": 1,
        "duration": 3000,
        "id": 999,
    }
    base.update(overrides)
    return base


def _item(**overrides):
    base = {
        "title": "Faixa Teste",
        "version": "",
        "performer": {"name": "Artista"},
        "performers": "Artista, MainArtist",
        "composer": {"name": "Compositor"},
        "isrc": "US1234567890",
        "parental_warning": False,
        "id": 555,
        "audio_info": {},
        "track_number": 3,
        "media_number": 1,
        "maximum_bit_depth": 24,
        "maximum_sampling_rate": 96,
        "maximum_channel_count": 2,
        "duration": 245,
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------
# tag_flac
# --------------------------------------------------------------------
class TestTagFlac:
    def test_grava_tags_vorbis_basicas_e_renomeia(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")
        destino = tmp_path / "final.flac"

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(destino),
            _item(),
            _album(),
            istrack=False,
            em_image=False,
            settings=QobuzDLSettings(),
        )

        assert fake["ALBUM"] == "Album Teste"
        assert fake["TITLE"] == "Faixa Teste"
        assert fake["TRACKNUMBER"] == "3"
        assert fake["TRACKTOTAL"] == "10"
        assert fake["DISCNUMBER"] == "1"
        assert fake["DISCTOTAL"] == "1"
        assert "COMMENT" in fake
        assert fake.saved_kwargs is not None
        assert not origem.exists()
        assert destino.exists()

    def test_remove_tags_de_encoder_residuais(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        fake["ENCODER"] = "algum encoder velho"
        fake["ENCODED-BY"] = "outro"
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert "ENCODER" not in fake
        assert "ENCODED-BY" not in fake

    def test_zera_vendor_string(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        assert fake.tags.vendor != ""
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert fake.tags.vendor == ""

    def test_multi_value_tags_grava_lista_de_artistas(self, monkeypatch, tmp_path):
        """Com multi_value_tags=True, ARTIST continua como texto único
        separado por vírgula; a implementação atual não cria ARTISTS."""
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")
        item = _item(
            performer={},  # sem isso, "Artista" (default) entra como
            # 3o artista via main_artist_raw antes da string performers
            performers=("Artista Um, MainArtist - Artista Dois, FeaturedArtist"),
        )

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            item,
            _album(artist={}),  # idem: sem isso, fallback pro artist do
            # álbum ainda entraria como artista extra
            istrack=False,
            settings=QobuzDLSettings(multi_value_tags=True),
        )

        # O comportamento atual grava ARTIST como texto único separado por
        # vírgula. Não há campo ARTISTS separado na implementação atual.
        assert fake["ARTIST"] == ["Artista Um", "Artista Dois"]
        assert "ARTISTS" not in fake

    def test_campos_multivalorados_mapeados_para_campos_plurais(self):
        """Verifica o mapeamento declarado atualmente em DISPLAY_JOINED_FIELDS."""
        assert metadata.DISPLAY_JOINED_FIELDS == {
            "ARTIST": "ARTISTS",
            "ALBUMARTIST": "ALBUMARTISTS",
            "COMPOSER": "COMPOSERS",
            "GENRE": "GENRES",
            "ARTISTSORT": "ARTISTSORTS",
            "ALBUMARTISTSORT": "ALBUMARTISTSORTS",
        }
        assert set(metadata.DISPLAY_JOINED_FIELDS) <= metadata.MULTI_VALUE_FIELDS

    def test_sem_multi_value_tags_mantem_virgula(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")
        item = _item(
            performer={},  # sem isso, "Artista" (default) entra como
            # 3o artista via main_artist_raw antes da string performers
            performers=("Artista Um, MainArtist - Artista Dois, FeaturedArtist"),
        )

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            item,
            _album(artist={}),  # idem: sem isso, fallback pro artist do
            # álbum ainda entraria como artista extra
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert fake["ARTIST"] == "Artista Um, Artista Dois"

    def test_embed_image_chama_add_picture_quando_capa_existe(
        self, monkeypatch, tmp_path
    ):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        (tmp_path / metadata.EMB_COVER_NAME).write_bytes(b"fake-jpeg-bytes")

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            _item(),
            _album(),
            istrack=False,
            em_image=True,
            settings=QobuzDLSettings(),
        )

        assert len(fake.pictures) == 1
        assert fake.pictures[0].data == b"fake-jpeg-bytes"

    def test_sem_capa_nao_chama_add_picture(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)

        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            _item(),
            _album(),
            istrack=False,
            em_image=True,
            settings=QobuzDLSettings(),
        )

        assert fake.pictures == []


# --------------------------------------------------------------------
# tag_mp3
# --------------------------------------------------------------------
class TestTagMp3:
    def test_grava_frames_basicos_e_renomeia(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")
        destino = tmp_path / "final.mp3"

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(destino),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert not origem.exists()
        assert destino.exists()

    def test_titulo_artista_e_album_vao_via_setitem_pelo_nome_do_frame(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
        audios_criados = []
        original_init = FakeID3.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3, "__init__", _init_e_registra)

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        audio = audios_criados[0]
        assert audio["TIT2"].text == ["Faixa Teste"]
        assert audio["TALB"].text == ["Album Teste"]
        assert audio["TPE1"].text == ["Artista"]
        assert audio["TRCK"].text == ["3/10"]
        assert audio["TPOS"].text == ["1/1"]

    def test_tags_sem_mapeamento_no_id3_legend_nao_aparecem(
        self, monkeypatch, tmp_path
    ):
        """Tags sem mapeamento ID3 são descartadas no MP3.

        COMPILATION e outras tags sem entrada em ID3_LEGEND não devem
        virar frames TXXX. Os identificadores Qobuz, por outro lado,
        têm mapeamento explícito porque são usados para identificar
        faixas e sincronizar playlists.
        """
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
        audios_criados = []
        original_init = FakeID3.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3, "__init__", _init_e_registra)

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")
        album = _album(title="Greatest Hits")  # força COMPILATION="1"

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item(),
            album,
            istrack=False,
            settings=QobuzDLSettings(),
        )

        audio = audios_criados[0]
        assert "COMPILATION" not in audio
        chaves_desc_em_txxx = {
            frame.desc for frame in audio.added if isinstance(frame, id3.TXXX)
        }
        assert "QOBUZTRACKID" in chaves_desc_em_txxx
        assert "COMPILATION" not in chaves_desc_em_txxx

    def test_txxx_e_comm_vao_via_add_nao_setitem(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
        audios_criados = []
        original_init = FakeID3.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3, "__init__", _init_e_registra)

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        audio = audios_criados[0]
        descricoes_txxx = {
            frame.desc for frame in audio.added if isinstance(frame, id3.TXXX)
        }
        assert "BARCODE" in descricoes_txxx
        assert any(isinstance(frame, id3.COMM) for frame in audio.added)

    def test_sem_header_id3_existente_cria_um_novo(self, monkeypatch, tmp_path):
        class FakeID3SemHeader(FakeID3):
            def __init__(self, filename=None):
                if filename is not None:
                    raise ID3NoHeaderError("sem header ID3 existente neste arquivo")
                super().__init__(filename)

        monkeypatch.setattr(metadata.id3, "ID3", FakeID3SemHeader)

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")
        destino = tmp_path / "final.mp3"

        # Não pode propagar ID3NoHeaderError -- tag_mp3 tem que cair no
        # except e criar um ID3() vazio pra seguir normalmente.
        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(destino),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert destino.exists()

    def test_remove_tenc_e_tsse_quando_presentes(self, monkeypatch, tmp_path):
        class FakeID3ComResiduo(FakeID3):
            def __init__(self, filename=None):
                super().__init__(filename)
                self["TENC"] = "residuo antigo"
                self["TSSE"] = "residuo antigo 2"

        monkeypatch.setattr(metadata.id3, "ID3", FakeID3ComResiduo)
        audios_criados = []
        original_init = FakeID3ComResiduo.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3ComResiduo, "__init__", _init_e_registra)

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        audio = audios_criados[0]
        assert "TENC" not in audio
        assert "TSSE" not in audio

    def test_embed_image_chama_add_apic_quando_capa_existe(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
        audios_criados = []
        original_init = FakeID3.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3, "__init__", _init_e_registra)
        (tmp_path / metadata.EMB_COVER_NAME).write_bytes(b"fake-jpeg-bytes")

        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item(),
            _album(),
            istrack=False,
            em_image=True,
            settings=QobuzDLSettings(),
        )

        audio = audios_criados[0]
        apics = [frame for frame in audio.added if isinstance(frame, id3.APIC)]
        assert len(apics) == 1
        assert apics[0].data == b"fake-jpeg-bytes"


# --------------------------------------------------------------------
# Nomes de artista que contêm vírgula ("Tyler, The Creator")
# --------------------------------------------------------------------
def _item_tyler(**overrides):
    base = dict(
        performer={"name": "Tyler, The Creator"},
        performers=(
            "Tyler, The Creator, MainArtist, Composer - "
            "Kali Uchis, FeaturedArtist"
        ),
        composer={"name": "Tyler, The Creator"},
    )
    base.update(overrides)
    return _item(**base)


def _album_tyler(**overrides):
    base = dict(artist={"name": "Tyler, The Creator"})
    base.update(overrides)
    return _album(**base)


class TestNomesComVirgula:
    def test_split_protegido_nao_quebra_nome_conhecido(self):
        protegidos = metadata._nomes_protegidos()
        partes = metadata._split_protegido(
            "Tyler, The Creator, Kali Uchis", ",", protegidos
        )
        assert partes == ["Tyler, The Creator", "Kali Uchis"]

    def test_split_protegido_ignora_maiusculas_e_preserva_grafia(self):
        protegidos = metadata._nomes_protegidos()
        partes = metadata._split_protegido("tyler, the creator, X", ",", protegidos)
        assert partes == ["tyler, the creator", "X"]

    def test_split_protegido_so_palavra_inteira(self):
        protegidos = ["Tyler, The Creator"]
        partes = metadata._split_protegido(
            "Not Tyler, The Creatorx, Y", ",", protegidos
        )
        assert partes == ["Not Tyler", "The Creatorx", "Y"]

    def test_split_protegido_sem_protegidos_equivale_ao_split(self):
        assert metadata._split_protegido("A, B ,C", ",", []) == ["A", "B", "C"]
        assert metadata._split_protegido("", ",", []) == []

    def test_nome_estruturado_da_api_e_protegido_automaticamente(self):
        album = _album(
            artists=[
                {"name": "Fulano, O Grande", "roles": ["main-artist"]},
                {"name": "Sem Virgula", "roles": ["main-artist"]},
            ]
        )
        protegidos = metadata._nomes_protegidos(album, _item())
        assert "Fulano, O Grande" in protegidos
        assert "Sem Virgula" not in protegidos

    def test_nome_do_config_e_protegido(self):
        settings = QobuzDLSettings(artists_with_comma="Banda, Do Fulano; Outro, Nome")
        assert settings.artists_with_comma == ("Banda, Do Fulano", "Outro, Nome")
        protegidos = metadata._nomes_protegidos(settings=settings)
        assert "Banda, Do Fulano" in protegidos

    def test_get_tags_mantem_texto_com_virgula(self):
        tags = metadata._get_tags_to_add(
            _album_tyler(), _item_tyler(), QobuzDLSettings()
        )
        assert tags["ARTIST"] == "Tyler, The Creator, Kali Uchis"
        assert tags["COMPOSER"] == "Tyler, The Creator"
        assert tags["ALBUMARTIST"] == "Tyler, The Creator"

    def test_get_tags_devolve_listas_exatas(self):
        saida = {}
        metadata._get_tags_to_add(
            _album_tyler(), _item_tyler(), QobuzDLSettings(), multi_out=saida
        )
        assert saida["ARTIST"] == ["Tyler, The Creator", "Kali Uchis"]
        assert saida["ALBUMARTIST"] == ["Tyler, The Creator"]
        assert saida["COMPOSER"] == ["Tyler, The Creator"]

    def test_artistsort_nao_quebra_artigo_nem_nome(self):
        """"The Weeknd" vira "Weeknd, The" (um valor só), não dois valores."""
        saida = {}
        item = _item(
            performer={"name": "The Weeknd"},
            performers="The Weeknd, MainArtist",
        )
        metadata._get_tags_to_add(
            _album(artist={"name": "The Weeknd"}),
            item,
            QobuzDLSettings(),
            multi_out=saida,
        )
        assert saida["ARTISTSORT"] == ["Weeknd, The"]

    def test_flac_multi_ligado_mantem_tyler_inteiro(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            _item_tyler(),
            _album_tyler(),
            istrack=False,
            settings=QobuzDLSettings(multi_value_tags=True),
        )

        assert fake["ARTIST"] == ["Tyler, The Creator", "Kali Uchis"]
        # Um artista só: grava texto simples, sem quebrar nada.
        assert fake["ALBUMARTIST"] == "Tyler, The Creator"
        assert fake["COMPOSER"] == "Tyler, The Creator"

    def test_flac_multi_desligado_grava_texto_unico(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            _item_tyler(),
            _album_tyler(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert fake["ARTIST"] == "Tyler, The Creator, Kali Uchis"
        assert fake["ALBUMARTIST"] == "Tyler, The Creator"

    def test_flac_solo_tyler_com_multi_ligado_nao_vira_dois(
        self, monkeypatch, tmp_path
    ):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        origem = tmp_path / "tmp.flac"
        origem.write_bytes(b"dummy")
        item = _item_tyler(performers="Tyler, The Creator, MainArtist")

        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.flac"),
            item,
            _album_tyler(),
            istrack=False,
            settings=QobuzDLSettings(multi_value_tags=True),
        )

        assert fake["ARTIST"] == "Tyler, The Creator"

    def test_mp3_multi_ligado_mantem_tyler_inteiro(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
        audios_criados = []
        original_init = FakeID3.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3, "__init__", _init_e_registra)
        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item_tyler(),
            _album_tyler(),
            istrack=False,
            settings=QobuzDLSettings(multi_value_tags=True),
        )

        audio = audios_criados[0]
        assert audio["TPE1"].text == ["Tyler, The Creator", "Kali Uchis"]

    def test_mp3_multi_desligado_grava_texto_unico(self, monkeypatch, tmp_path):
        monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
        audios_criados = []
        original_init = FakeID3.__init__

        def _init_e_registra(self, filename=None):
            original_init(self, filename)
            audios_criados.append(self)

        monkeypatch.setattr(FakeID3, "__init__", _init_e_registra)
        origem = tmp_path / "tmp.mp3"
        origem.write_bytes(b"dummy")

        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(tmp_path / "final.mp3"),
            _item_tyler(),
            _album_tyler(),
            istrack=False,
            settings=QobuzDLSettings(),
        )

        assert audios_criados[0]["TPE1"].text == ["Tyler, The Creator, Kali Uchis"]

    def test_valores_multi_cai_no_split_legado_sem_lista(self):
        assert metadata._valores_multi("ARTIST", "A, B", {}) == ["A", "B"]
        assert metadata._valores_multi("TITLE", "A, B", {}) is None
        assert metadata._valores_multi("ARTIST", "Solo", {}) is None
