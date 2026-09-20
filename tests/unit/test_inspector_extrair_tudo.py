"""Testa `_formatar_tamanho()` e `_extrair_tudo()` de qobuz_dl/inspector.py.

`_extrair_tudo()` importa FLAC/MP3/File de dentro da própria função (não
no topo do módulo), então o fake entra no nível de origem
(`mutagen.flac.FLAC`, `mutagen.mp3.MP3`, `mutagen.File`) em vez de
`inspector.FLAC` -- como o `from mutagen.X import Y` roda de novo a cada
chamada da função, ele pega a versão fakeada automaticamente.

Frames de ID3 (APIC) continuam sendo os de verdade do mutagen -- são só
contêiner de dado.
"""

from types import SimpleNamespace

import mutagen
import mutagen.flac
import mutagen.mp3
import pytest
from mutagen.id3 import APIC

from qobuz_dl import inspector

pytestmark = pytest.mark.unit


class FakeInfo:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


# --------------------------------------------------------------------
# _formatar_tamanho
# --------------------------------------------------------------------
class TestFormatarTamanho:
    def test_bytes_sem_casas_decimais(self):
        assert inspector._formatar_tamanho(500) == "500 B"
        assert inspector._formatar_tamanho(0) == "0 B"

    def test_kilobytes(self):
        assert inspector._formatar_tamanho(2048) == "2.0 KB"

    def test_megabytes(self):
        assert inspector._formatar_tamanho(3 * 1024 * 1024) == "3.0 MB"

    def test_gigabytes(self):
        assert inspector._formatar_tamanho(2 * 1024**3) == "2.0 GB"

    def test_terabytes_via_fallback_do_loop(self):
        assert inspector._formatar_tamanho(int(1.5 * 1024**4)) == "1.5 TB"


# --------------------------------------------------------------------
# _extrair_tudo
# --------------------------------------------------------------------
class TestExtrairTudoFlac:
    def test_extrai_tecnico_tags_e_capas(self, monkeypatch, tmp_path):
        class FakeFLACAudio:
            def __init__(self, path):
                self.info = FakeInfo(
                    sample_rate=96000,
                    bits_per_sample=24,
                    channels=2,
                    length=245.3,
                    bitrate=4500000,
                )
                self.tags = [("ARTIST", "Artista Teste"), ("TITLE", "Faixa Teste")]
                self.pictures = [
                    SimpleNamespace(
                        mime="image/jpeg", width=1000, height=1000, data=b"x" * 500
                    )
                ]

        monkeypatch.setattr(mutagen.flac, "FLAC", FakeFLACAudio)

        arquivo = tmp_path / "faixa.flac"
        arquivo.write_bytes(b"x" * 2048)

        dados = inspector._extrair_tudo(str(arquivo))

        assert dados["tecnico"]["Arquivo"] == "faixa.flac"
        assert dados["tecnico"]["Extensão"] == "FLAC"
        assert dados["tecnico"]["Codec"] == "FLAC (lossless)"
        assert dados["tecnico"]["Sample rate"] == "96000 Hz"
        assert dados["tecnico"]["Profundidade de bits"] == "24 bits"
        assert dados["tags"]["ARTIST"] == "Artista Teste"
        assert dados["tags"]["TITLE"] == "Faixa Teste"
        assert len(dados["capas"]) == 1
        assert "image/jpeg" in dados["capas"][0]
        assert dados["_sample_rate"] == 96000
        assert dados["_duracao_s"] == 245.3

    def test_tags_repetidas_sao_concatenadas_com_virgula(self, monkeypatch, tmp_path):
        class FakeFLACAudio:
            def __init__(self, path):
                self.info = FakeInfo(
                    sample_rate=44100,
                    bits_per_sample=16,
                    channels=2,
                    length=180.0,
                    bitrate=1000000,
                )
                # FLAC/Vorbis permite a MESMA chave aparecer mais de uma
                # vez (ex.: múltiplos gêneros) -- _extrair_tudo concatena.
                self.tags = [("GENRE", "Rock"), ("GENRE", "Indie")]
                self.pictures = []

        monkeypatch.setattr(mutagen.flac, "FLAC", FakeFLACAudio)

        arquivo = tmp_path / "faixa2.flac"
        arquivo.write_bytes(b"x" * 10)

        dados = inspector._extrair_tudo(str(arquivo))

        assert dados["tags"]["GENRE"] == "Rock, Indie"


class TestExtrairTudoMp3:
    def test_extrai_tecnico_e_separa_apic_das_demais_tags(self, monkeypatch, tmp_path):
        class FakeMP3Audio:
            def __init__(self, path):
                self.info = FakeInfo(
                    sample_rate=44100,
                    channels=2,
                    length=200.5,
                    bitrate=320000,
                    mode="Stereo",
                )
                self.tags = {
                    "TIT2": "Faixa MP3 Teste",
                    "APIC:cover": APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="cover",
                        data=b"y" * 300,
                    ),
                }

        monkeypatch.setattr(mutagen.mp3, "MP3", FakeMP3Audio)

        arquivo = tmp_path / "faixa.mp3"
        arquivo.write_bytes(b"x" * 10)

        dados = inspector._extrair_tudo(str(arquivo))

        assert dados["tecnico"]["Codec"] == "MP3"
        assert dados["tecnico"]["Bitrate"] == "320 kbps"
        assert dados["tecnico"]["Modo"] == "Stereo"
        assert dados["tags"]["TIT2"] == "Faixa MP3 Teste"
        assert "TIT2" in dados["tags"]
        assert "APIC:cover" not in dados["tags"]  # foi pra "capas", não "tags"
        assert len(dados["capas"]) == 1
        assert "image/jpeg" in dados["capas"][0]


class TestExtrairTudoGenerico:
    def test_formato_generico_via_mutagen_file(self, monkeypatch, tmp_path):
        class FakeGenericAudio:
            def __init__(self):
                self.info = FakeInfo(
                    sample_rate=48000, channels=2, length=150.0, bitrate=256000
                )
                self.tags = {
                    "artist": ["Artista Um", "Artista Dois"],
                    "title": "Faixa Ogg",
                }

        monkeypatch.setattr(mutagen, "File", lambda path: FakeGenericAudio())

        arquivo = tmp_path / "faixa.ogg"
        arquivo.write_bytes(b"x" * 10)

        dados = inspector._extrair_tudo(str(arquivo))

        assert dados["tecnico"]["Codec"] == "FakeGenericAudio"
        assert dados["tags"]["artist"] == "Artista Um, Artista Dois"
        assert dados["tags"]["title"] == "Faixa Ogg"

    def test_formato_nao_reconhecido_lanca_value_error(self, monkeypatch, tmp_path):
        monkeypatch.setattr(mutagen, "File", lambda path: None)

        arquivo = tmp_path / "faixa.xyz"
        arquivo.write_bytes(b"x" * 10)

        with pytest.raises(ValueError):
            inspector._extrair_tudo(str(arquivo))
