"""Ida e volta REAL do `tags` (gravar -> desfazer) com áudio de verdade.

Os outros testes do registro de desfazer usam fakes. Aqui um FLAC e um MP3
minúsculos são gerados pelo ffmpeg, gravados pelo `metadata.tag_flac/tag_mp3`
com o mutagen real e restaurados pelo `tags_journal`. O critério é o mais
duro possível: o arquivo inteiro volta IGUAL, byte a byte.

Pula sozinho se o ffmpeg ou o mutagen real não estiverem disponíveis.
"""

import os
import shutil
import subprocess

import pytest

from qobuz_dl import metadata, tags_journal
from qobuz_dl.settings import QobuzDLSettings

FFMPEG = shutil.which("ffmpeg")

try:  # o mutagen pode estar simulado/quebrado no ambiente (a-Shell, offline)
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3

    FLAC.__init__  # noqa: B018 -- só confirma que não é um MagicMock
    _MUTAGEN_REAL = isinstance(FLAC, type)
except Exception:  # noqa: BLE001
    _MUTAGEN_REAL = False

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skipif(not FFMPEG, reason="ffmpeg indisponível"),
    pytest.mark.skipif(not _MUTAGEN_REAL, reason="mutagen real indisponível"),
]


def _ffmpeg(*args):
    subprocess.run(
        [FFMPEG, "-y", "-loglevel", "error", *args],
        check=True,
        capture_output=True,
    )


def _cover(path, cor):
    _ffmpeg("-f", "lavfi", "-i", f"color=c={cor}:s=64x64:d=1", "-frames:v", "1", path)


def _audio(path, formato):
    codec = ["-c:a", "flac"] if formato == "flac" else ["-c:a", "libmp3lame"]
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1", *codec, path)


def _item():
    return {
        "title": "Faixa",
        "version": "",
        "performer": {"name": "Tyler, The Creator"},
        "performers": "Tyler, The Creator, MainArtist - Kali Uchis, FeaturedArtist",
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


def _album():
    return {
        "title": "Album",
        "version": "",
        "artist": {"name": "Tyler, The Creator"},
        "genre": {"name": "Rap"},
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


def _gravar(formato, arquivo, capa):
    funcao = metadata.tag_flac if formato == "flac" else metadata.tag_mp3
    funcao(
        arquivo,
        os.path.dirname(arquivo),
        arquivo,
        _item(),
        _album(),
        False,
        True,
        settings=QobuzDLSettings(multi_value_tags=True),
        embed_cover_path=capa,
        preserve_cover_info=True,
    )


@pytest.fixture
def cenario(tmp_path):
    def montar(formato):
        pasta = tmp_path / "musicas"
        pasta.mkdir()
        arquivo = str(pasta / f"faixa.{formato}")
        capa_antiga = str(tmp_path / "antiga.jpg")
        capa_nova = str(tmp_path / "nova.jpg")
        _cover(capa_antiga, "red")
        _cover(capa_nova, "blue")
        _audio(arquivo, formato)
        # estado inicial: tags e capa "antigas", gravadas pelo próprio programa
        antigo = _item()
        antigo["performer"] = {"name": "Nome Antigo"}
        antigo["performers"] = "Nome Antigo, MainArtist"
        funcao = metadata.tag_flac if formato == "flac" else metadata.tag_mp3
        funcao(
            arquivo,
            str(pasta),
            arquivo,
            antigo,
            _album(),
            False,
            True,
            settings=QobuzDLSettings(),
            embed_cover_path=capa_antiga,
        )
        return arquivo, capa_nova, str(tmp_path / "config")

    return montar


@pytest.mark.parametrize("formato", ["flac", "mp3"])
def test_gravar_e_desfazer_devolve_o_arquivo_identico(cenario, formato):
    arquivo, capa_nova, config = cenario(formato)
    original = open(arquivo, "rb").read()

    journal = tags_journal.Journal.create(config, "both", os.path.dirname(arquivo))
    assert journal.begin(arquivo) is True
    _gravar(formato, arquivo, capa_nova)
    assert journal.finish(arquivo) is True

    gravado = open(arquivo, "rb").read()
    assert gravado != original  # o tags realmente mudou o arquivo

    resultado = tags_journal.undo_run(config)
    assert resultado["restored"] == 1
    assert resultado["errors"] == 0 and resultado["verify_failed"] == 0
    assert open(arquivo, "rb").read() == original


@pytest.mark.parametrize("formato", ["flac", "mp3"])
def test_arquivo_continua_legivel_depois_de_desfazer(cenario, formato):
    arquivo, capa_nova, config = cenario(formato)
    journal = tags_journal.Journal.create(config, "both", os.path.dirname(arquivo))
    journal.begin(arquivo)
    _gravar(formato, arquivo, capa_nova)
    journal.finish(arquivo)
    tags_journal.undo_run(config)

    if formato == "flac":
        audio = FLAC(arquivo)
        assert audio["ARTIST"] == ["Nome Antigo"]
        assert len(audio.pictures) == 1
    else:
        audio = ID3(arquivo)
        assert audio["TPE1"].text == ["Nome Antigo"]
        assert len(audio.getall("APIC")) == 1
    # e o áudio em si ainda decodifica
    _ffmpeg("-v", "error", "-i", arquivo, "-f", "null", "-")


@pytest.mark.parametrize("formato", ["flac", "mp3"])
def test_nao_desfaz_arquivo_alterado_depois(cenario, formato):
    arquivo, capa_nova, config = cenario(formato)
    journal = tags_journal.Journal.create(config, "both", os.path.dirname(arquivo))
    journal.begin(arquivo)
    _gravar(formato, arquivo, capa_nova)
    journal.finish(arquivo)

    # outra ferramenta mexe no arquivo depois do `tags`
    if formato == "flac":
        audio = FLAC(arquivo)
        audio["COMMENT"] = "mexi depois"
        audio.save()
    else:
        audio = ID3(arquivo)
        audio.add(
            __import__("mutagen.id3", fromlist=["TIT2"]).TIT2(encoding=3, text="x")
        )
        audio.save()
    mexido = open(arquivo, "rb").read()

    resultado = tags_journal.undo_run(config)
    assert resultado["changed_since"] == 1 and resultado["restored"] == 0
    assert open(arquivo, "rb").read() == mexido  # não pisou no trabalho alheio

    forcado = tags_journal.undo_run(config, force=True)
    assert forcado["restored"] == 1


@pytest.mark.parametrize("formato", ["flac", "mp3"])
def test_tyler_the_creator_fica_inteiro_no_arquivo_real(cenario, formato):
    """Com multi-tags, "Tyler, The Creator" é UM artista e "Kali Uchis" outro."""
    arquivo, capa_nova, _ = cenario(formato)
    _gravar(formato, arquivo, capa_nova)

    if formato == "flac":
        audio = FLAC(arquivo)
        assert audio["ARTIST"] == ["Tyler, The Creator", "Kali Uchis"]
        assert audio["ALBUMARTIST"] == ["Tyler, The Creator"]
    else:
        audio = ID3(arquivo)
        assert audio["TPE1"].text == ["Tyler, The Creator", "Kali Uchis"]
        assert audio["TPE2"].text == ["Tyler, The Creator"]


# --------------------------------------------------------------------------
# Fluxo completo: retag_directory (simulação, gravação, desfazer)
# --------------------------------------------------------------------------
class _ClienteFalso:
    """Qobuz de mentira: devolve o álbum com a faixa 555 já corrigida."""

    async def get_album_meta(self, album_id):
        album = _album()
        album["tracks"] = {"items": [_item()]}
        return album

    async def get_track_meta(self, track_id):  # não deve ser preciso
        raise AssertionError("a faixa deveria ser achada pelo álbum")


def _rodar(pasta, **kw):
    import asyncio

    from qobuz_dl import retro_tags

    return asyncio.run(
        retro_tags.retag_directory(
            pasta,
            _ClienteFalso(),
            QobuzDLSettings(multi_value_tags=True),
            tags_only=True,
            **kw,
        )
    )


@pytest.mark.parametrize("formato", ["flac", "mp3"])
def test_simulacao_nao_toca_na_biblioteca_e_mostra_o_diff(
    cenario, formato, monkeypatch
):
    from qobuz_dl import ui

    linhas = []
    monkeypatch.setattr(ui, "emit", lambda t="", *a, **k: linhas.append(str(t)))
    arquivo, _capa, _config = cenario(formato)
    pasta = os.path.dirname(arquivo)
    antes = open(arquivo, "rb").read()
    mtime = os.stat(arquivo).st_mtime_ns

    stats = _rodar(pasta, dry_run=True)

    assert open(arquivo, "rb").read() == antes  # nem um byte
    assert os.stat(arquivo).st_mtime_ns == mtime
    assert stats["updated"] == 1 and stats["errors"] == 0
    texto = "\n".join(linhas)
    assert "SIMULAÇÃO" in texto and "Tyler, The Creator" in texto
    # nenhum temporário da simulação sobra na pasta
    assert sorted(os.listdir(pasta)) == [os.path.basename(arquivo)]


@pytest.mark.parametrize("formato", ["flac", "mp3"])
def test_execucao_real_com_registro_e_desfazer_completo(cenario, formato, monkeypatch):
    from qobuz_dl import ui

    monkeypatch.setattr(ui, "emit", lambda *a, **k: None)
    arquivo, _capa, config = cenario(formato)
    pasta = os.path.dirname(arquivo)
    antes = open(arquivo, "rb").read()

    journal = tags_journal.Journal.create(config, "tags", pasta)
    stats = _rodar(pasta, journal=journal)
    assert stats["updated"] == 1 and journal.recorded == 1
    assert open(arquivo, "rb").read() != antes

    resultado = tags_journal.undo_run(config)
    assert resultado["restored"] == 1
    assert open(arquivo, "rb").read() == antes
