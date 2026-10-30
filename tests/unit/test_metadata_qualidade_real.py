"""O comentário do arquivo mostra a qualidade REAL baixada, não o máximo.

`maximum_bit_depth`/`maximum_sampling_rate` são o máximo que a faixa tem no
catálogo do Qobuz. Baixando em qualidade 5 (MP3) ou 6 (FLAC 16/44.1), o
comentário não pode dizer 24/96. Sem fakes de áudio de verdade: mesmo padrão
de test_metadata_tagging.py.
"""

from types import SimpleNamespace

import mutagen.id3 as id3
import pytest

import qobuz_dl.metadata as metadata
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


class FakeFLAC(dict):
    def __init__(self, bits=None, rate=None):
        super().__init__()
        self.tags = SimpleNamespace(vendor="libFLAC algumaversao")
        self.pictures = []
        if bits is not None:
            self.info = SimpleNamespace(bits_per_sample=bits, sample_rate=rate)

    def save(self, **kwargs):
        pass

    def add_picture(self, picture):
        self.pictures.append(picture)

    def clear_pictures(self):
        self.pictures.clear()


class FakeID3(dict):
    def __init__(self, filename=None):
        super().__init__()
        self.added = []

    def add(self, frame):
        self.added.append(frame)

    def pop(self, key, default=None):
        return super().pop(key, default)

    def save(self, filename=None, **kwargs):
        pass


def _album():
    return {
        "title": "Album Teste",
        "version": "",
        "artist": {"name": "Artista"},
        "genre": {"name": "Rock"},
        "genres_list": [],
        "release_date_original": "2024-05-01",
        "tracks_count": 10,
        "media_count": 1,
        "duration": 3000,
        "id": 999,
    }


def _item(**overrides):
    base = {
        "title": "Faixa Teste",
        "version": "",
        "performer": {"name": "Artista"},
        "performers": "Artista, MainArtist",
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


def _comentario_flac(monkeypatch, tmp_path, fake):
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
        em_image=False,
        settings=QobuzDLSettings(),
    )
    return str(fake["COMMENT"])


def _comentario_mp3(monkeypatch, tmp_path, kbps):
    monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
    monkeypatch.setattr(metadata, "_mp3_real_bitrate_kbps", lambda filename: kbps)
    fake_frames = []
    original_init = FakeID3.__init__

    def _init_e_registra(self, filename=None):
        original_init(self, filename)
        fake_frames.append(self)

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
    frames = [f for fake in fake_frames for f in fake.added]
    comm = [f for f in frames if isinstance(f, id3.COMM)]
    return "\n".join("\n".join(str(parte) for parte in f.text) for f in comm)


class TestFlacQualidadeReal:
    def test_baixado_abaixo_do_maximo_mostra_real_e_maximo(self, monkeypatch, tmp_path):
        comentario = _comentario_flac(
            monkeypatch, tmp_path, FakeFLAC(bits=16, rate=44100)
        )
        assert "Qualidade: FLAC 16-bit / 44.1 kHz / Estéreo" in comentario
        assert "Máx. no Qobuz: 24-bit / 96 kHz" in comentario
        assert "Qualidade: FLAC 24-bit" not in comentario

    def test_baixado_no_maximo_nao_repete_a_linha_de_maximo(
        self, monkeypatch, tmp_path
    ):
        comentario = _comentario_flac(
            monkeypatch, tmp_path, FakeFLAC(bits=24, rate=96000)
        )
        assert "Qualidade: FLAC 24-bit / 96 kHz / Estéreo" in comentario
        assert "Máx. no Qobuz" not in comentario

    def test_sem_leitura_do_arquivo_cai_no_valor_do_catalogo(
        self, monkeypatch, tmp_path
    ):
        comentario = _comentario_flac(monkeypatch, tmp_path, FakeFLAC())
        assert "Qualidade: FLAC 24-bit / 96 kHz / Estéreo" in comentario
        assert "Máx. no Qobuz" not in comentario


class TestMp3QualidadeReal:
    def test_mp3_mostra_bitrate_real_e_maximo_do_qobuz(self, monkeypatch, tmp_path):
        comentario = _comentario_mp3(monkeypatch, tmp_path, 320)
        esperado = "Qualidade: MP3 320 kbps / Estéreo\nMáx. no Qobuz: 24-bit / 96 kHz"
        assert esperado in comentario
        assert "24b/96kHz" not in comentario

    def test_mp3_sem_leitura_usa_o_valor_do_catalogo(self, monkeypatch, tmp_path):
        comentario = _comentario_mp3(monkeypatch, tmp_path, None)
        assert "Qualidade: 24b/96kHz / Estéreo\nHiRes:" in comentario

    def test_comentario_do_mp3_tem_uma_linha_por_item(self, monkeypatch, tmp_path):
        comentario = _comentario_mp3(monkeypatch, tmp_path, 320)
        linhas = comentario.splitlines()
        assert linhas[0] == "Fonte: Qobuz"
        assert " | " not in comentario
        for rotulo in ("Duração:", "Tipo:", "Lançamento:", "Trk ID: 555"):
            assert sum(1 for linha in linhas if linha.startswith(rotulo)) == 1
        assert "Tipo: Album" in linhas


class TestHelpers:
    def test_khz_label(self):
        assert metadata._khz_label(96.0) == "96"
        assert metadata._khz_label(44.1) == "44.1"
        assert metadata._khz_label(None) == "None"

    def test_flac_real_quality_sem_info_devolve_none(self):
        assert metadata._flac_real_quality(SimpleNamespace()) == (None, None)

    def test_same_quality_sem_dado_confiavel_nao_inventa_maximo(self):
        assert metadata._same_quality(16, 44.1, None, None) is True
        assert metadata._same_quality(16, 44.1, 24, 96) is False
        assert metadata._same_quality(24, 96.0, 24, 96) is True

    def test_mp3_real_bitrate_arquivo_inexistente(self, tmp_path):
        arquivo = tmp_path / "nao_existe.mp3"
        assert metadata._mp3_real_bitrate_kbps(str(arquivo)) is None
