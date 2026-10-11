"""Testa o que o `tags` grava (e preserva) sobre a capa embutida.

CONTEXTO
--------
Dois comportamentos do comando `tags` que apareciam no log como
"SOMENTE TAGS ALTERADAS" mesmo quando a capa era preservada:

1. Sem capa nova (`em_image=False`) o COMMENT era regravado SEM as linhas
   "Capa embutida: ..." / "Capa: ...". A origem da capa ia embora a cada
   retag. `preserve_cover_info=True` mantém essas linhas.
2. Modo `--covers-only`: `replace_cover_only` troca só a imagem (e o que
   descreve a capa no comentário/`COVER_SOURCE`), sem regravar nenhuma tag.

Tudo roda com fakes (nada abre áudio de verdade).
"""

from types import SimpleNamespace

import pytest

from qobuz_dl import metadata
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class FakeFLAC(dict):
    def __init__(self):
        super().__init__()
        self.tags = SimpleNamespace(vendor="x")
        self.pictures = []
        self.saved = 0

    def save(self, **kwargs):
        self.saved += 1

    def add_picture(self, picture):
        self.pictures.append(picture)

    def clear_pictures(self):
        self.pictures.clear()


class _SemCabecalho(Exception):
    pass


class APIC:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class COMM:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeID3(dict):
    def __init__(self, version=(2, 4, 0)):
        super().__init__()
        self.version = version
        self.saved = None
        self._n = 0

    def getall(self, nome):
        return [f for k, f in self.items() if k.startswith(nome)]

    def add(self, frame):
        self._n += 1
        self[f"{type(frame).__name__}:{self._n}"] = frame

    def save(self, filename=None, **kwargs):
        self.saved = (filename, kwargs)


def _album():
    return {
        "title": "Album Teste",
        "version": "",
        "artist": {"name": "Artista"},
        "genre": {"name": "Rock"},
        "genres_list": [],
        "release_date_original": "2024-05-01",
        "label": {"name": "Gravadora"},
        "upc": "1234567890123",
        "product_type": "album",
        "release_type": "album",
        "tracks_count": 10,
        "media_count": 1,
        "duration": 3000,
        "id": 999,
    }


def _item():
    return {
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


CAPA_FLAC = [
    "Fonte: Qobuz\nTipo: Album\n"
    "Capa embutida: 10 KiB | Redimensionada | Solicitada: 600\n"
    "Capa: Apple/iTunes"
]


# ---------------------------------------------------------------------------
# Extração das linhas/trechos de capa
# ---------------------------------------------------------------------------
class TestExtrairLinhasDeCapaFlac:
    def test_pega_so_as_linhas_de_capa(self):
        assert metadata._extract_flac_cover_comment_lines(CAPA_FLAC) == [
            "Capa embutida: 10 KiB | Redimensionada | Solicitada: 600",
            "Capa: Apple/iTunes",
        ]

    def test_aceita_texto_simples(self):
        assert metadata._extract_flac_cover_comment_lines("A\nCapa: Qobuz") == [
            "Capa: Qobuz"
        ]

    @pytest.mark.parametrize("valor", [None, [], "", ["sem nada de capa"]])
    def test_sem_linhas_de_capa(self, valor):
        assert metadata._extract_flac_cover_comment_lines(valor) == []

    def test_nao_duplica(self):
        assert metadata._extract_flac_cover_comment_lines(
            ["Capa: Qobuz", "Capa: Qobuz"]
        ) == ["Capa: Qobuz"]


class TestExtrairTrechosDeCapaMp3:
    def test_pega_so_os_trechos_de_capa(self):
        texto = (
            "Qobuz | 24b/96kHz | Estéreo | Trk ID: 1"
            " | Cover: 10 KiB (Req: org, Org: YES) | Capa: Apple/iTunes"
        )
        assert metadata._extract_mp3_cover_comment_segments(texto) == [
            "Cover: 10 KiB (Req: org, Org: YES)",
            "Capa: Apple/iTunes",
        ]

    @pytest.mark.parametrize("valor", [None, "", "Qobuz | 24b/96kHz"])
    def test_sem_trechos_de_capa(self, valor):
        assert metadata._extract_mp3_cover_comment_segments(valor) == []

    def test_aceita_lista_de_textos(self):
        assert metadata._extract_mp3_cover_comment_segments(["A", "Capa: Qobuz"]) == [
            "Capa: Qobuz"
        ]


class TestComentarioId3:
    def test_primeiro_comm_com_texto(self):
        audio = FakeID3()
        audio["COMM:vazio"] = COMM(text=[""])
        audio["COMM:cheio"] = COMM(text=["abc"])
        assert metadata._id3_comment_text(audio) == "abc"
        assert metadata._id3_comment_frame(audio) is audio["COMM:cheio"]

    def test_sem_comm(self):
        assert metadata._id3_comment_text(FakeID3()) == ""
        assert metadata._id3_comment_frame(FakeID3()) is None

    def test_objeto_sem_getall_nao_quebra(self):
        assert metadata._id3_comment_text(object()) == ""


# ---------------------------------------------------------------------------
# tag_flac(preserve_cover_info=...)
# ---------------------------------------------------------------------------
class TestTagFlacPreservaInfoDeCapa:
    def _gravar(self, monkeypatch, tmp_path, **kw):
        fake = FakeFLAC()
        fake["COMMENT"] = list(CAPA_FLAC)
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        origem = tmp_path / "a.flac"
        origem.write_bytes(b"x")
        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(origem),
            _item(),
            _album(),
            istrack=False,
            em_image=False,
            settings=QobuzDLSettings(),
            **kw,
        )
        return fake["COMMENT"]

    def test_mantem_as_linhas_de_capa_existentes(self, monkeypatch, tmp_path):
        comentario = self._gravar(monkeypatch, tmp_path, preserve_cover_info=True)
        assert "Fonte: Qobuz" in comentario  # o resto e' regerado
        assert "Capa: Apple/iTunes" in comentario.splitlines()
        assert (
            "Capa embutida: 10 KiB | Redimensionada | Solicitada: 600"
            in comentario.splitlines()
        )

    def test_sem_o_parametro_o_comportamento_antigo_e_mantido(
        self, monkeypatch, tmp_path
    ):
        # Downloads novos nao tem capa antiga para preservar.
        comentario = self._gravar(monkeypatch, tmp_path)
        assert "Capa: Apple/iTunes" not in comentario

    def test_sem_linhas_antigas_nao_inventa_nenhuma(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        origem = tmp_path / "a.flac"
        origem.write_bytes(b"x")
        metadata.tag_flac(
            str(origem),
            str(tmp_path),
            str(origem),
            _item(),
            _album(),
            istrack=False,
            settings=QobuzDLSettings(),
            preserve_cover_info=True,
        )
        assert "Capa:" not in fake["COMMENT"]


# ---------------------------------------------------------------------------
# replace_cover_only -- FLAC
# ---------------------------------------------------------------------------
class TestReplaceCoverOnlyFlac:
    def _preparar(self, monkeypatch, tmp_path):
        fake = FakeFLAC()
        fake["TITLE"] = "Titulo Original"
        fake["ARTIST"] = ["Fulano", "Ciclano"]
        fake["COMMENT"] = list(CAPA_FLAC)
        fake.pictures.append(SimpleNamespace(data=b"antiga"))
        monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
        audio = tmp_path / "a.flac"
        audio.write_bytes(b"x")
        capa = tmp_path / ".apple-cover.jpg"
        capa.write_bytes(b"jpg")
        return fake, str(audio), str(capa)

    def test_troca_so_a_capa_e_a_info_dela(self, monkeypatch, tmp_path):
        fake, audio, capa = self._preparar(monkeypatch, tmp_path)

        ok = metadata.replace_cover_only(
            audio,
            capa,
            cover_source="Apple/iTunes",
            settings=SimpleNamespace(embedded_art_size="org"),
        )

        assert ok is True and fake.saved == 1
        assert len(fake.pictures) == 1  # a antiga saiu
        # Nenhuma tag de metadados foi tocada:
        assert fake["TITLE"] == "Titulo Original"
        assert fake["ARTIST"] == ["Fulano", "Ciclano"]
        assert fake["COVER_SOURCE"] == "Apple/iTunes"
        linhas = fake["COMMENT"].splitlines()
        assert "Fonte: Qobuz" in linhas and "Tipo: Album" in linhas
        assert [ln for ln in linhas if ln.startswith("Capa:")] == ["Capa: Apple/iTunes"]
        embutida = [ln for ln in linhas if ln.startswith("Capa embutida:")]
        assert len(embutida) == 1
        assert "Original" in embutida[0] and "Solicitada: org" in embutida[0]
        assert "10 KiB | Redimensionada" not in fake["COMMENT"]

    def test_sem_arquivo_de_capa_nao_faz_nada(self, monkeypatch, tmp_path):
        fake, audio, _ = self._preparar(monkeypatch, tmp_path)
        assert metadata.replace_cover_only(audio, str(tmp_path / "nao.jpg")) is False
        assert metadata.replace_cover_only(audio, None) is False
        assert fake.saved == 0

    def test_falha_ao_embutir_restaura_a_capa_antiga_e_nao_salva(
        self, monkeypatch, tmp_path
    ):
        fake, audio, capa = self._preparar(monkeypatch, tmp_path)

        def embutir_falha(root_dir, flac, cover_override=None):
            flac.clear_pictures()  # o embed real limpa antes de embutir

        monkeypatch.setattr(metadata, "_embed_flac_img", embutir_falha)
        assert metadata.replace_cover_only(audio, capa, "Apple/iTunes") is False
        assert [p.data for p in fake.pictures] == [b"antiga"]
        assert fake.saved == 0
        assert "COVER_SOURCE" not in fake


# ---------------------------------------------------------------------------
# replace_cover_only -- MP3
# ---------------------------------------------------------------------------
class TestReplaceCoverOnlyMp3:
    def _preparar(self, monkeypatch, tmp_path, version=(2, 4, 0), comentario=True):
        audio_id3 = FakeID3(version)
        audio_id3.add(SimpleNamespace(text=["nao-e-capa"]))
        audio_id3.add(APIC(data=b"antiga", mime="image/jpeg", type=3, desc="cover"))
        if comentario:
            audio_id3.add(
                COMM(
                    text=[
                        "Qobuz | 24b/96kHz | Trk ID: 1"
                        " | Cover: 10 KiB (Req: 600, Org: NO) | Capa: Qobuz"
                    ]
                )
            )
        monkeypatch.setattr(metadata.id3, "ID3", lambda path: audio_id3)
        monkeypatch.setattr(metadata.id3, "APIC", APIC, raising=False)
        monkeypatch.setattr(metadata.id3, "COMM", COMM, raising=False)
        monkeypatch.setattr(metadata, "ID3NoHeaderError", _SemCabecalho)
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"x")
        capa = tmp_path / ".apple-cover.jpg"
        capa.write_bytes(b"jpg-novo")
        return audio_id3, str(audio), str(capa)

    def test_troca_a_imagem_e_so_os_trechos_de_capa_do_comentario(
        self, monkeypatch, tmp_path
    ):
        id3_falso, audio, capa = self._preparar(monkeypatch, tmp_path)

        ok = metadata.replace_cover_only(
            audio,
            capa,
            cover_source="Apple/iTunes",
            settings=SimpleNamespace(embedded_art_size="org"),
        )

        assert ok is True
        imagens = [f for k, f in id3_falso.items() if k.startswith("APIC")]
        assert [i.data for i in imagens] == [b"jpg-novo"]
        comentario = id3_falso.getall("COMM")[0].text[0]
        assert comentario.startswith("Qobuz | 24b/96kHz | Trk ID: 1")
        assert comentario.count("Capa:") == 1
        assert comentario.endswith("Capa: Apple/iTunes")
        assert "Capa: Qobuz" not in comentario
        assert "Org: YES" in comentario
        assert len(id3_falso.getall("COMM")) == 1

    def test_mantem_a_versao_do_id3_do_arquivo(self, monkeypatch, tmp_path):
        id3_falso, audio, capa = self._preparar(monkeypatch, tmp_path, (2, 4, 0))
        metadata.replace_cover_only(audio, capa)
        assert id3_falso.saved[1]["v2_version"] == 4

        id3_falso, audio, capa = self._preparar(monkeypatch, tmp_path, (2, 3, 0))
        metadata.replace_cover_only(audio, capa)
        assert id3_falso.saved[1]["v2_version"] == 3

    def test_sem_comentario_cria_um(self, monkeypatch, tmp_path):
        id3_falso, audio, capa = self._preparar(monkeypatch, tmp_path, comentario=False)
        metadata.replace_cover_only(audio, capa, cover_source="Apple/iTunes")
        comentario = id3_falso.getall("COMM")[0].text[0]
        assert comentario.startswith("Cover:")
        assert comentario.endswith("Capa: Apple/iTunes")

    def test_falha_ao_embutir_restaura_a_capa_e_nao_salva(self, monkeypatch, tmp_path):
        id3_falso, audio, capa = self._preparar(monkeypatch, tmp_path)

        def embutir_falha(root_dir, audio_id3, cover_override=None):
            for chave in [k for k in audio_id3.keys() if k.startswith("APIC")]:
                del audio_id3[chave]

        monkeypatch.setattr(metadata, "_embed_id3_img", embutir_falha)
        assert metadata.replace_cover_only(audio, capa, "Apple/iTunes") is False
        imagens = [f for k, f in id3_falso.items() if k.startswith("APIC")]
        assert [i.data for i in imagens] == [b"antiga"]
        assert id3_falso.saved is None


# ---------------------------------------------------------------------------
# tag_mp3(preserve_cover_info=...) -- usa os frames reais do mutagen (só
# contêineres de dado, sem I/O), como test_metadata_tagging.py
# ---------------------------------------------------------------------------
class TestTagMp3PreservaInfoDeCapa:
    ANTIGO = (
        "Qobuz | 24b/96kHz | Trk ID: 1"
        " | Cover: 10 KiB (Req: org, Org: YES) | Capa: Apple/iTunes"
    )

    def _gravar(self, monkeypatch, tmp_path, **kw):
        import mutagen.id3 as id3_real

        audio_id3 = FakeID3()
        audio_id3["COMM::eng"] = SimpleNamespace(text=[self.ANTIGO])
        monkeypatch.setattr(metadata.id3, "ID3", lambda path: audio_id3)
        origem = tmp_path / "a.mp3"
        origem.write_bytes(b"x")
        metadata.tag_mp3(
            str(origem),
            str(tmp_path),
            str(origem),
            _item(),
            _album(),
            istrack=False,
            em_image=False,
            settings=QobuzDLSettings(),
            **kw,
        )
        novos = [f for f in audio_id3.values() if isinstance(f, id3_real.COMM)]
        return novos[-1].text[0]

    def test_mantem_os_trechos_de_capa_existentes(self, monkeypatch, tmp_path):
        comentario = self._gravar(monkeypatch, tmp_path, preserve_cover_info=True)
        assert comentario.startswith("Qobuz |")
        assert " | Cover: 10 KiB (Req: org, Org: YES)" in comentario
        assert comentario.endswith(" | Capa: Apple/iTunes")

    def test_sem_o_parametro_o_comportamento_antigo_e_mantido(
        self, monkeypatch, tmp_path
    ):
        assert "Capa:" not in self._gravar(monkeypatch, tmp_path)
