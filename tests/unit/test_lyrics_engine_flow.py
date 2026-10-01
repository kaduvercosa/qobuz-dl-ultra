"""Testes do fluxo de qobuz_dl/lyrics_engine.py: Musixmatch, orquestracao
de fetch_and_inject (Qobuz -> Musixmatch -> LRCLIB -> Genius), Genius no
__init__ e close(). Sem rede e sem tocar em arquivos de audio reais.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from qobuz_dl import lyrics_engine as le
from qobuz_dl.lyrics_engine import LyricsEngine

pytestmark = pytest.mark.unit

SYNC = "[00:01.00]linha um\n[00:05.00]linha dois"
PLAIN = "linha um\nlinha dois"


def _resp(dados=None, status=200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = dados
    return r


def _engine(settings=None, session=None):
    eng = LyricsEngine(session=session or MagicMock(), settings=settings)
    eng._inject_metadata = MagicMock(return_value=True)
    eng._save_lrc_file = MagicMock(return_value=True)
    eng._inject_instrumental_pauses = lambda t: t
    eng._fetch_musixmatch_lyrics = MagicMock(return_value=None)
    eng.extract_qobuz_lyrics = MagicMock(return_value=None)
    return eng


def _run(eng, **kw):
    return eng.fetch_and_inject("/m/a.flac", "Art", "Tit", "Alb", **kw)


def _mxm_ok(subtitle="[00:01.00]x"):
    return {
        "message": {
            "header": {"status_code": 200},
            "body": {
                "macro_calls": {
                    "track.subtitles.get": {
                        "message": {
                            "header": {"status_code": 200},
                            "body": {
                                "subtitle_list": [
                                    {"subtitle": {"subtitle_body": subtitle}}
                                ]
                            },
                        }
                    }
                }
            },
        }
    }


TOKEN_OK = {"message": {"header": {"status_code": 200}, "body": {"user_token": "tok"}}}


def _mxm_engine(*respostas):
    s = MagicMock()
    s.get.side_effect = list(respostas)
    return LyricsEngine(session=s)


def test_musixmatch_sucesso_e_token_em_cache():
    eng = _mxm_engine(
        _resp(TOKEN_OK), _resp(_mxm_ok("[00:01.00]a")), _resp(_mxm_ok("b"))
    )
    assert eng._fetch_musixmatch_lyrics("A", "T") == "[00:01.00]a"
    assert eng._mxm_token == "tok"
    assert eng._fetch_musixmatch_lyrics("A", "T") == "b"
    assert eng.session.get.call_count == 3


def test_musixmatch_token_http_ruim():
    eng = _mxm_engine(_resp(status=500))
    assert eng._fetch_musixmatch_lyrics("A", "T") is None
    assert eng._mxm_token is None


def test_musixmatch_token_com_status_interno_ruim():
    bad = {"message": {"header": {"status_code": 401}}}
    eng = _mxm_engine(_resp(bad))
    assert eng._fetch_musixmatch_lyrics("A", "T") is None


def _sub(**over):
    base = _mxm_ok()
    msg = base["message"]["body"]["macro_calls"]["track.subtitles.get"]["message"]
    msg.update(over)
    return base


@pytest.mark.parametrize(
    "letra",
    [
        _resp(status=404),
        _resp({"message": {"header": {"status_code": 404}}}),
        _resp({"message": {"header": {"status_code": 200}, "body": {}}}),
        _resp(_sub(header={"status_code": 404})),
        _resp(_sub(body={})),
        _resp(_sub(body={"subtitle_list": []})),
    ],
)
def test_musixmatch_respostas_sem_letra(letra):
    eng = _mxm_engine(_resp(TOKEN_OK), letra)
    assert eng._fetch_musixmatch_lyrics("A", "T") is None


def test_musixmatch_excecao_vira_none():
    s = MagicMock()
    s.get.side_effect = RuntimeError("rede")
    assert LyricsEngine(session=s)._fetch_musixmatch_lyrics("A", "T") is None


def test_init_com_genius(monkeypatch):
    fake = MagicMock()
    monkeypatch.setattr(le, "lyricsgenius", SimpleNamespace(Genius=fake))
    eng = LyricsEngine(genius_token="t", session=MagicMock())
    fake.assert_called_once_with("t", remove_section_headers=True)
    assert eng.genius is fake.return_value
    assert eng.genius.verbose is False


def test_init_sem_lyricsgenius_instalado(monkeypatch):
    monkeypatch.setattr(le, "lyricsgenius", None)
    assert LyricsEngine(genius_token="t", session=MagicMock()).genius is None


def test_close_so_fecha_sessao_propria():
    externa = MagicMock()
    LyricsEngine(session=externa).close()
    externa.close.assert_not_called()

    eng = LyricsEngine()
    eng.session = MagicMock()
    eng.close()
    eng.session.close.assert_called_once()

    eng.session.close.side_effect = RuntimeError("x")
    eng.close()


def test_nada_a_fazer_sem_embed_nem_save():
    eng = _engine()
    r = _run(eng, save_lrc=False, embed_lyrics=False)
    assert r["success"] is False
    eng.extract_qobuz_lyrics.assert_not_called()


def _qobuz(**over):
    d = {
        "synced": SYNC,
        "plain": PLAIN,
        "lang": "en",
        "translations": [
            {"language": "es", "synced": "[00:01.00]uno", "plain": "uno"},
            {"language": "pt", "synced": "[00:01.00]um", "plain": "um"},
        ],
    }
    d.update(over)
    return d


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_qobuz_sincronizada_bilingue_prefere_pt(embed, save):
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz()
    eng._build_bilingual_lrc = MagicMock(return_value="BILINGUE")
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    eng._build_bilingual_lrc.assert_called_once_with(SYNC, "[00:01.00]um")
    assert r["success"] and r["source"] == "Qobuz"
    assert r["synchronized"] and r["bilingual"]
    assert r["language"] == "en+pt"
    assert r["embedded"] is embed
    assert r["saved_external"] is save


def test_qobuz_traducao_sem_pt_usa_a_primeira():
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz(
        translations=[{"language": "es", "synced": "[00:01.00]uno", "plain": "uno"}]
    )
    r = _run(eng)
    assert r["language"] == "en+es"


def test_qobuz_sem_traducao_usa_idioma_original():
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz(translations=[])
    r = _run(eng)
    assert r["language"] == "en"
    assert r["bilingual"] is False


def test_qobuz_gravacao_falha_marca_sem_sucesso():
    eng = _engine()
    eng._inject_metadata.return_value = False
    eng._save_lrc_file.return_value = False
    eng.extract_qobuz_lyrics.return_value = _qobuz(translations=[])
    r = _run(eng)
    assert r["success"] is False and r["source"] is None


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_qobuz_so_texto_puro_com_traducao(embed, save):
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz(synced=None)
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    texto = (
        eng._inject_metadata.call_args.args[1]
        if embed
        else eng._save_lrc_file.call_args.args[1]
    )
    assert "--- TRADUCAO (PT) ---" in texto
    assert r["success"] and r["synchronized"] is False and r["bilingual"] is True


def test_only_synced_descarta_texto_puro_do_qobuz_e_segue_adiante():
    eng = _engine(settings=SimpleNamespace(only_synced_lyrics=True))
    eng.extract_qobuz_lyrics.return_value = _qobuz(synced=None)
    eng.session.get.return_value = _resp(status=404)
    r = _run(eng)
    assert r["success"] is False
    assert eng.session.get.call_count == 2


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
@pytest.mark.parametrize("letra,sincronizada", [(SYNC, True), (PLAIN, False)])
def test_musixmatch_fallback(embed, save, letra, sincronizada):
    eng = _engine()
    eng._fetch_musixmatch_lyrics.return_value = letra
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    assert r["success"] and r["source"] == "Musixmatch"
    assert r["synchronized"] is sincronizada


def test_musixmatch_sem_gravar_nao_marca_sucesso():
    eng = _engine()
    eng._inject_metadata.return_value = False
    eng._save_lrc_file.return_value = False
    eng._fetch_musixmatch_lyrics.return_value = SYNC
    assert _run(eng)["success"] is False


def test_musixmatch_texto_puro_ignorado_com_only_synced():
    eng = _engine(settings=SimpleNamespace(only_synced_lyrics=True))
    eng._fetch_musixmatch_lyrics.return_value = PLAIN
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC, "plainLyrics": PLAIN})
    r = _run(eng)
    assert r["source"] == "LRCLIB" and r["synchronized"] is True


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_lrclib_sincronizada(embed, save):
    eng = _engine()
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC})
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    assert r["success"] and r["source"] == "LRCLIB" and r["synchronized"]
    headers = eng.session.get.call_args.kwargs["headers"]
    assert headers["User-Agent"].startswith("qobuz-dl-ultra/")


def test_lrclib_repete_busca_sem_album_quando_primeira_falha():
    eng = _engine()
    eng.session.get.side_effect = [_resp(status=404), _resp({"plainLyrics": PLAIN})]
    r = _run(eng)
    primeira, segunda = (c.kwargs["params"] for c in eng.session.get.call_args_list)
    assert "album_name" in primeira and "album_name" not in segunda
    assert r["source"] == "LRCLIB" and r["synchronized"] is False


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_lrclib_texto_puro(embed, save):
    eng = _engine()
    eng.session.get.return_value = _resp({"plainLyrics": PLAIN})
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    assert r["success"] and r["synchronized"] is False


def test_lrclib_only_synced_ignora_texto_puro():
    eng = _engine(settings=SimpleNamespace(only_synced_lyrics=True))
    eng.session.get.return_value = _resp({"plainLyrics": PLAIN})
    assert _run(eng)["success"] is False


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_genius_ultimo_recurso(embed, save):
    eng = _engine()
    eng.session.get.return_value = _resp(status=404)
    eng.genius = MagicMock()
    eng.genius.search_song.return_value = SimpleNamespace(lyrics=PLAIN)
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    eng.genius.search_song.assert_called_once_with("Tit", "Art")
    assert r["success"] and r["source"] == "Genius"


def test_genius_sem_resultado_e_ignorado_com_only_synced():
    eng = _engine()
    eng.session.get.return_value = _resp(status=404)
    eng.genius = MagicMock()
    eng.genius.search_song.return_value = None
    assert _run(eng)["success"] is False

    eng2 = _engine(settings=SimpleNamespace(only_synced_lyrics=True))
    eng2.session.get.return_value = _resp(status=404)
    eng2.genius = MagicMock()
    _run(eng2)
    eng2.genius.search_song.assert_not_called()


def test_nenhuma_letra_encontrada():
    eng = _engine()
    eng.session.get.return_value = _resp(status=404)
    r = _run(eng)
    assert r["success"] is False and r["error"] is None


def test_excecao_e_registrada_no_resultado():
    eng = _engine()
    eng.session.get.side_effect = RuntimeError("boom")
    r = _run(eng)
    assert r["success"] is False and r["error"] == "boom"


def test_track_number_prefixa_saida(monkeypatch):
    saidas = []
    monkeypatch.setattr(le.tqdm, "write", lambda m: saidas.append(m))
    eng = _engine()
    eng.session.get.return_value = _resp(status=404)
    _run(eng, track_number=7)
    assert saidas and all("[7]" in s for s in saidas)
