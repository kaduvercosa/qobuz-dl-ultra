"""Tradução das letras: só é procurada se o usuário escolheu isso no `qobuz-dl -r`.

A escolha fica em `fetch_translation` (config.ini). Quando é "não", o idioma de
tradução efetivo (`lyrics_translation_lang`) fica vazio e nenhuma parte do
programa (download, BiniLyrics, comando `lyrics`) pede ou monta tradução.

Colocar em tests/unit/.
"""

import configparser
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qobuz_dl import retro_tagger as rt
from qobuz_dl.lyrics_engine import LyricsEngine
from qobuz_dl.settings import QobuzDLSettings

pytestmark = pytest.mark.unit


def _settings(**valores):
    config = configparser.ConfigParser(interpolation=None)
    config["qobuz"] = valores
    return QobuzDLSettings.from_arguments_configparser(SimpleNamespace(), config)


# ------------------------------------------------------------- configuração
def test_padrao_procura_traducao():
    s = QobuzDLSettings()
    assert s.fetch_translation is True
    assert s.lyrics_translation_lang == "pt"


def test_recusar_traducao_no_reset_zera_o_idioma_efetivo():
    s = _settings(account_language="pt", fetch_translation="false")
    assert s.fetch_translation is False
    assert s.lyrics_translation_lang == ""


def test_aceitar_traducao_no_reset_mantem_o_idioma_da_conta():
    s = _settings(account_language="es", fetch_translation="true")
    assert s.lyrics_translation_lang == "es"


def test_config_antigo_sem_a_chave_continua_procurando():
    s = _settings(account_language="pt")
    assert s.lyrics_translation_lang == "pt"


def test_atribuicao_posterior_respeita_a_escolha():
    # É o que os comandos `lyrics` e `--watch` fazem no cli.py.
    s = QobuzDLSettings()
    s.fetch_translation = False
    s.lyrics_translation_lang = "pt"
    assert s.lyrics_translation_lang == ""
    s.fetch_translation = True
    assert s.lyrics_translation_lang == "pt"


# ----------------------------------------------------------------- download
def test_relatorio_de_letras_sem_traducao_pedida():
    from qobuz_dl.downloader import _build_letras_report

    r = _build_letras_report({"success": True, "language": "en"}, "", None)
    assert r["traducao_disponivel"] is None


# --------------------------------------------------------------- BiniLyrics
TTML = """<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal" xml:lang="en">
<head><metadata><itunes:iTunesMetadata><itunes:translations>
<itunes:translation xml:lang="pt-BR"><itunes:text for="L1">anjos cantam</itunes:text></itunes:translation>
</itunes:translations></itunes:iTunesMetadata></metadata></head>
<body><div><p begin="1.000" end="4.000" itunes:key="L1">angels sing</p></div></body></tt>"""

TRACK = {
    "isrc": "ISRC1",
    "album_name": "Alb",
    "duration": 200,
    "artist_name": "Emicida",
    "timing_type": "line",
    "lyricsUrl": "https://lrc.red/s/ISRC1.ttml",
    "track_name": "Song",
}


def _engine(settings):
    eng = LyricsEngine(session=MagicMock())
    eng.PROVIDERS = ("_provider_binilyrics",)
    eng.settings = settings
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


def test_binilyrics_com_traducao_escolhida_vira_bilingue():
    eng = _engine(QobuzDLSettings(fetch_translation=True))
    resultado = _buscar(eng)
    assert resultado["success"] and resultado["bilingual"]
    texto = eng._inject_metadata.call_args.args[1]
    assert texto == "[00:01.000]angels sing\n[00:01.000]anjos cantam"


def test_binilyrics_sem_traducao_escolhida_fica_so_no_original():
    eng = _engine(QobuzDLSettings(fetch_translation=False))
    resultado = _buscar(eng)
    assert resultado["success"] and not resultado["bilingual"]
    assert eng._inject_metadata.call_args.args[1] == "[00:01.000]angels sing"


# ------------------------------------------------------------ comando lyrics
class _Engine:
    def __init__(self):
        self.chamadas = []

    def fetch_and_inject(self, **kw):
        self.chamadas.append(kw)
        return {"success": True}

    def close(self):
        pass


@pytest.fixture
def ambiente(tmp_path, monkeypatch):
    (tmp_path / "f1.flac").write_bytes(b"x")
    monkeypatch.setattr(rt.ui, "emit", lambda texto="", end="\n": None)
    monkeypatch.setattr(rt, "extract_track_id", lambda p: "100")
    monkeypatch.setattr(
        rt,
        "inspect_existing_lyrics",
        lambda p: {
            "has_lyrics": False,
            "is_bilingual": False,
            "is_synced": False,
            "content": "",
            "lrc_exists": False,
            "language": None,
        },
    )
    monkeypatch.setattr(rt, "FLAC", lambda p: {"TITLE": ["f1"], "ARTIST": ["A"]})

    pedidos = []

    async def buscar(cli, tid, language=None):
        pedidos.append(language)
        corpo = {"original": {"lang": "en", "lines": [{"start": 1000, "line": "x"}]}}
        if language:
            corpo["translation"] = {
                "lang": language,
                "lines": [{"start": 1000, "line": "y"}],
            }
        return corpo

    monkeypatch.setattr(rt, "fetch_qobuz_lyrics_raw", buscar)
    engine = _Engine()
    monkeypatch.setattr(rt, "LyricsEngine", lambda *a, **k: engine)

    async def rodar(**kw):
        settings = QobuzDLSettings(lrc_files=True, embed_lyrics=True, **kw)
        await rt.process_retroactive_lyrics_async(
            str(tmp_path), SimpleNamespace(), settings=settings
        )

    return SimpleNamespace(rodar=rodar, pedidos=pedidos, engine=engine)


async def test_lyrics_sem_traducao_escolhida_nao_consulta_a_traducao(ambiente):
    await ambiente.rodar(fetch_translation=False)
    assert ambiente.pedidos == [None]
    assert len(ambiente.engine.chamadas) == 1
    assert ambiente.engine.chamadas[0]["qobuz_translation_response"] is None


async def test_lyrics_com_traducao_escolhida_consulta_a_traducao(ambiente):
    await ambiente.rodar(fetch_translation=True, lyrics_translation_lang="pt")
    assert ambiente.pedidos == [None, "pt"]
    assert ambiente.engine.chamadas[0]["qobuz_translation_response"] is not None
