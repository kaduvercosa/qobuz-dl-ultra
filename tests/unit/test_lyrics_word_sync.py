"""--lyrics-word-sync (config + CLI), LRC por palavra e dicas do retro-tagger."""

import argparse
import configparser
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qobuz_dl import binilyrics as bl
from qobuz_dl import commands, retro_tagger
from qobuz_dl.lyrics_engine import LyricsEngine
from qobuz_dl.settings import QobuzDLSettings

TTML = """<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal" xml:lang="en">
<body><div><p begin="1.000" end="4.000" itunes:key="L1"><span begin="1.000" end="2.000">an</span><span begin="2.000" end="3.000">gels</span> <span begin="3.000" end="4.000">sing</span></p></div></body></tt>"""

TRACK = {
    "isrc": "ISRC1",
    "album_name": "Alb",
    "duration": 200,
    "artist_name": "Emicida",
    "timing_type": "word",
    "lyricsUrl": "https://lrc.red/s/ISRC1.ttml",
    "track_name": "Song",
}


def _config(**valores):
    cfg = configparser.ConfigParser()
    cfg["qobuz"] = {k: str(v) for k, v in valores.items()}
    return cfg


# ------------------------------------------------------------- configuração
@pytest.mark.unit
def test_padrao_e_desligado():
    assert QobuzDLSettings().lyrics_word_sync is False
    s = QobuzDLSettings.from_arguments_configparser(argparse.Namespace(), _config())
    assert s.lyrics_word_sync is False


@pytest.mark.unit
def test_config_ini_liga():
    cfg = _config(lyrics_word_sync="true")
    s = QobuzDLSettings.from_arguments_configparser(argparse.Namespace(), cfg)
    assert s.lyrics_word_sync is True


@pytest.mark.unit
def test_flag_da_cli_liga():
    args = argparse.Namespace(lyrics_word_sync=True)
    s = QobuzDLSettings.from_arguments_configparser(args, _config())
    assert s.lyrics_word_sync is True


@pytest.mark.unit
def test_parser_aceita_a_flag():
    parser = commands.qobuz_dl_args()
    assert parser.parse_args(["dl", "x", "--lyrics-word-sync"]).lyrics_word_sync
    assert not hasattr(parser.parse_args(["dl", "x"]), "lyrics_word_sync")


# --------------------------------------------------------------- saída LRC
def _engine(word_sync):
    eng = LyricsEngine(session=MagicMock())
    eng.PROVIDERS = ("_provider_binilyrics",)
    eng.settings = SimpleNamespace(
        only_synced_lyrics=False,
        lyrics_translation_lang="pt",
        lyrics_word_sync=word_sync,
    )
    eng._inject_metadata = MagicMock(return_value=True)
    eng._save_lrc_file = MagicMock(return_value=True)
    eng._inject_instrumental_pauses = lambda t: t

    def get(url, **kwargs):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"results": [TRACK]}
        resp.text = TTML
        return resp

    eng.session.get = get
    return eng


def _buscar(eng):
    return eng.fetch_and_inject(
        file_path="/m/a.flac",
        artist="Emicida",
        track="Song",
        album="Alb",
        isrc="ISRC1",
        duration=200,
    )


@pytest.mark.unit
def test_com_word_sync_grava_lrc_por_palavra():
    eng = _engine(True)
    assert _buscar(eng)["success"]
    texto = eng._inject_metadata.call_args.args[1]
    assert texto == "[00:01.000]<00:01.000>an<00:02.000>gels <00:03.000>sing"


@pytest.mark.unit
def test_sem_word_sync_grava_lrc_por_linha():
    eng = _engine(False)
    assert _buscar(eng)["success"]
    assert eng._inject_metadata.call_args.args[1] == "[00:01.000]angels sing"


@pytest.mark.unit
def test_word_sync_nao_inventa_tempo_quando_o_ttml_e_por_linha():
    doc = bl.parse_ttml(
        '<tt xmlns="http://www.w3.org/ns/ttml"><body><div>'
        '<p begin="5.000" end="6.000">so linha</p></div></body></tt>'
    )
    assert bl.to_word_lrc(doc) == "[00:05.000]so linha"


# ------------------------------------------------------- dicas do retro-tagger
@pytest.mark.unit
def test_dicas_de_flac(monkeypatch):
    fake = {"ISRC": ["BRX6F1900014"]}

    class Audio(dict):
        info = SimpleNamespace(length=320.6)

    audio = Audio(fake)
    monkeypatch.setattr(retro_tagger, "FLAC", lambda path: audio)
    assert retro_tagger._lyrics_lookup_hints("/m/a.flac") == {
        "isrc": "BRX6F1900014",
        "duration": 321,
    }


@pytest.mark.unit
def test_dicas_de_mp3(monkeypatch):
    frame = SimpleNamespace(text=["USUM71607007"])
    audio = SimpleNamespace(tags={"TSRC": frame}, info=SimpleNamespace(length=229.0))
    monkeypatch.setattr(retro_tagger, "MP3", lambda path: audio)
    assert retro_tagger._lyrics_lookup_hints("/m/a.mp3") == {
        "isrc": "USUM71607007",
        "duration": 229,
    }


@pytest.mark.unit
def test_dicas_falha_de_leitura_devolve_vazio(monkeypatch):
    def quebra(path):
        raise OSError("arquivo corrompido")

    monkeypatch.setattr(retro_tagger, "FLAC", quebra)
    assert retro_tagger._lyrics_lookup_hints("/m/a.flac") == {}
