"""Testes do fluxo de qobuz_dl/lyrics_engine.py: Musixmatch, orquestracao
de fetch_and_inject (Qobuz -> BiniLyrics -> Musixmatch -> LRCLIB), Genius no
__init__ e close(). Regra central: SO letra sincronizada e' aceita; letra
plana (sem timestamps) e' sempre descartada. Sem rede e sem tocar em arquivos
de audio reais.
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
    eng._build_bilingual_lrc = MagicMock(return_value="[00:01.00]BILINGUE")
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
def test_qobuz_so_texto_puro_e_descartado(embed, save):
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz(synced=None)
    eng.session.get.return_value = _resp(status=404)
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    assert r["success"] is False
    eng._inject_metadata.assert_not_called()
    eng._save_lrc_file.assert_not_called()


def test_qobuz_traducao_so_texto_puro_nao_vira_bilingue():
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz(
        translations=[{"language": "pt", "synced": None, "plain": "um"}]
    )
    r = _run(eng)
    assert r["success"] and r["synchronized"] is True
    assert r["bilingual"] is False and r["language"] == "en"


def test_texto_puro_do_qobuz_segue_adiante_para_proxima_fonte():
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz(synced=None)
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC})
    r = _run(eng)
    assert r["source"] == "LRCLIB" and r["synchronized"] is True


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_musixmatch_fallback(embed, save):
    eng = _engine()
    eng._fetch_musixmatch_lyrics.return_value = SYNC
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    assert r["success"] and r["source"] == "Musixmatch"
    assert r["synchronized"] is True


def test_musixmatch_sem_gravar_nao_marca_sucesso():
    eng = _engine()
    eng._inject_metadata.return_value = False
    eng._save_lrc_file.return_value = False
    eng._fetch_musixmatch_lyrics.return_value = SYNC
    assert _run(eng)["success"] is False


def test_musixmatch_texto_puro_e_ignorado():
    eng = _engine()
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
    eng.session.get.side_effect = [_resp(status=404), _resp({"syncedLyrics": SYNC})]
    r = _run(eng)
    primeira, segunda = (c.kwargs["params"] for c in eng.session.get.call_args_list)
    assert "album_name" in primeira and "album_name" not in segunda
    assert r["source"] == "LRCLIB" and r["synchronized"] is True


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_lrclib_texto_puro_e_descartado(embed, save):
    eng = _engine()
    eng.session.get.return_value = _resp({"plainLyrics": PLAIN})
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    assert r["success"] is False
    eng._inject_metadata.assert_not_called()
    eng._save_lrc_file.assert_not_called()


def test_lrclib_usa_synced_mesmo_com_plain_presente():
    eng = _engine()
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC, "plainLyrics": PLAIN})
    r = _run(eng)
    assert r["success"] and r["synchronized"] is True
    assert eng._inject_metadata.call_args.args[1] == SYNC


@pytest.mark.parametrize("embed,save", [(True, True), (True, False), (False, True)])
def test_genius_nunca_e_usado(embed, save):
    """Genius so tem texto puro: nem e' consultado, nem grava nada."""
    eng = _engine()
    eng.session.get.return_value = _resp(status=404)
    eng.genius = MagicMock()
    eng.genius.search_song.return_value = SimpleNamespace(lyrics=PLAIN)
    r = _run(eng, embed_lyrics=embed, save_lrc=save)
    eng.genius.search_song.assert_not_called()
    assert "_provider_genius" not in LyricsEngine.PROVIDERS
    assert r["success"] is False


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


def test_quiet_omite_progresso_e_sucesso(monkeypatch):
    saidas = []
    monkeypatch.setattr(le.tqdm, "write", lambda m: saidas.append(m))
    eng = _engine()
    eng.extract_qobuz_lyrics.return_value = _qobuz()
    r = _run(eng, quiet=True)
    assert r["success"] is True
    assert saidas == []


def test_quiet_nao_esconde_erros(monkeypatch):
    saidas = []
    monkeypatch.setattr(le.tqdm, "write", lambda m: saidas.append(m))
    eng = _engine()
    eng.session.get.side_effect = RuntimeError("boom")
    r = _run(eng, quiet=True)
    assert r["error"] == "boom"
    assert any("boom" in s for s in saidas)


def _fontes_mockadas(eng):
    nomes = [
        "_provider_qobuz",
        "_provider_binilyrics",
        "_provider_musixmatch",
        "_provider_lrclib",
    ]
    for nome in nomes:
        setattr(eng, nome, MagicMock(return_value=None))
    return {nome: getattr(eng, nome) for nome in nomes}


def test_sem_better_than_consulta_todas_as_fontes():
    eng = _engine()
    fontes = _fontes_mockadas(eng)
    _run(eng)
    assert all(f.called for f in fontes.values())


def test_better_than_so_consulta_fontes_de_prioridade_melhor():
    eng = _engine()
    fontes = _fontes_mockadas(eng)
    _run(eng, better_than=4)  # melhor que o Musixmatch: Qobuz e BiniLyrics
    assert fontes["_provider_qobuz"].called
    assert fontes["_provider_binilyrics"].called
    assert not fontes["_provider_musixmatch"].called
    assert not fontes["_provider_lrclib"].called


def test_better_than_3_so_deixa_o_qobuz():
    eng = _engine()
    fontes = _fontes_mockadas(eng)
    _run(eng, better_than=3)
    assert [n for n, f in fontes.items() if f.called] == ["_provider_qobuz"]


def test_better_than_sem_resultado_nao_grava_nada():
    eng = _engine()
    _fontes_mockadas(eng)
    r = _run(eng, better_than=5)
    assert r["success"] is False
    eng._inject_metadata.assert_not_called()
    eng._save_lrc_file.assert_not_called()


# ---------------------------------------------------------------------------
# Registro de fontes (LyricsEngine.PROVIDERS)
# ---------------------------------------------------------------------------


def test_fonte_nova_entra_pela_tupla_providers():
    eng = _engine()
    eng.PROVIDERS = ("_provider_nova",) + LyricsEngine.PROVIDERS
    eng._provider_nova = lambda query: le.LyricsCandidate(SYNC, "NovaFonte", True)
    r = _run(eng)
    assert r["success"] and r["source"] == "NovaFonte" and r["synchronized"]
    # a fonte nova veio antes: as seguintes nem são consultadas
    eng.extract_qobuz_lyrics.assert_not_called()


def test_fonte_com_erro_nao_bloqueia_as_seguintes():
    eng = _engine()
    eng.PROVIDERS = ("_provider_quebrada", "_provider_lrclib")

    def quebrada(query):
        raise RuntimeError("fora do ar")

    eng._provider_quebrada = quebrada
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC})
    r = _run(eng)
    assert r["success"] and r["source"] == "LRCLIB" and r["error"] is None


def test_falha_ao_gravar_imprime_mensagem_de_erro(monkeypatch):
    saidas = []
    monkeypatch.setattr(le.tqdm, "write", lambda m: saidas.append(m))
    eng = _engine()
    eng._inject_metadata.return_value = False
    eng._save_lrc_file.return_value = False
    eng._fetch_musixmatch_lyrics.return_value = SYNC
    r = _run(eng)
    assert r["success"] is False and r["source"] is None
    assert any("Falha ao gravar" in s for s in saidas)
    assert not any("injetadas" in s for s in saidas)


# ------------------------------------------------ barreira so-sincronizada
class _FonteFalsa:
    """Fonte futura mal-comportada: devolve candidatos que nao deveriam passar."""

    def __init__(self, candidato):
        self.candidato = candidato

    def __call__(self, query):
        return self.candidato


@pytest.mark.parametrize(
    "candidato",
    [
        le.LyricsCandidate(PLAIN, "X", False),
        le.LyricsCandidate(PLAIN, "X", True),  # marcada como sync, sem timestamps
    ],
)
def test_barreira_final_descarta_candidato_sem_timestamps(candidato):
    eng = _engine()
    eng.PROVIDERS = ("_fonte",)
    eng._fonte = _FonteFalsa(candidato)
    r = _run(eng)
    assert r["success"] is False
    eng._inject_metadata.assert_not_called()
    eng._save_lrc_file.assert_not_called()


def test_has_lrc_timestamps():
    assert le.has_lrc_timestamps("[00:01.00]a")
    assert le.has_lrc_timestamps("x\n[01:02.345]b")
    assert not le.has_lrc_timestamps(PLAIN)
    assert not le.has_lrc_timestamps("")
    assert not le.has_lrc_timestamps(None)


# ------------------------------------------------- variantes de busca
def test_artist_variants():
    assert le.artist_variants("Drake, Rihanna") == ["Drake, Rihanna", "Drake"]
    assert le.artist_variants("A feat. B") == ["A feat. B", "A"]
    assert le.artist_variants("Beyoncé") == ["Beyoncé"]
    assert le.artist_variants("") == []


def test_title_variants():
    assert le.title_variants("One Dance (feat. Kyla)") == [
        "One Dance (feat. Kyla)",
        "One Dance",
    ]
    assert le.title_variants("Bohemian Rhapsody - 2011 Remaster") == [
        "Bohemian Rhapsody - 2011 Remaster",
        "Bohemian Rhapsody",
    ]
    assert le.title_variants("Halo") == ["Halo"]


def test_query_variants_exato_primeiro_e_limitado():
    pares = le.query_variants("Drake, Rihanna", "One Dance (feat. Kyla) - Remastered")
    assert pares[0] == ("Drake, Rihanna", "One Dance (feat. Kyla) - Remastered")
    assert len(pares) <= le.MAX_QUERY_VARIANTS
    assert le.query_variants("Beyoncé", "Halo") == [("Beyoncé", "Halo")]


def test_lrc_fits_duration():
    assert le.lrc_fits_duration("[03:00.00]x", 180)
    assert not le.lrc_fits_duration("[04:00.00]x", 180)
    assert le.lrc_fits_duration("[04:00.00]x", None)
    assert le.lrc_fits_duration(PLAIN, 180)


# ----------------------------------------------- LRCLIB: duracao e variantes
def test_lrclib_envia_duracao():
    eng = _engine()
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC, "duration": 174})
    r = _run(eng, duration=174)
    assert r["source"] == "LRCLIB"
    assert eng.session.get.call_args.kwargs["params"]["duration"] == 174


def test_lrclib_sem_duracao_nao_envia_parametro():
    eng = _engine()
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC})
    _run(eng)
    assert "duration" not in eng.session.get.call_args.kwargs["params"]


def test_lrclib_rejeita_resultado_com_duracao_diferente():
    eng = _engine()
    eng.session.get.return_value = _resp({"syncedLyrics": SYNC, "duration": 260})
    assert _run(eng, duration=200)["success"] is False


def test_lrclib_tenta_variante_do_artista_e_titulo_quando_exata_falha():
    eng = _engine()
    eng.session.get.side_effect = lambda url, **kw: (
        _resp({"syncedLyrics": SYNC})
        if kw["params"]["artist_name"] == "Drake"
        and kw["params"]["track_name"] == "One Dance (feat. Kyla)"
        else _resp(status=404)
    )
    r = eng.fetch_and_inject(
        "/m/a.flac", "Drake, Rihanna", "One Dance (feat. Kyla)", "Views"
    )
    assert r["source"] == "LRCLIB"
    primeira = eng.session.get.call_args_list[0].kwargs["params"]
    assert primeira["artist_name"] == "Drake, Rihanna"  # exata vem primeiro


# ----------------------------------------- Musixmatch: dicas e variantes
def test_musixmatch_envia_dicas_de_album_duracao_e_isrc():
    eng = LyricsEngine(session=MagicMock())
    eng._mxm_token = "tok"
    eng.session.get.return_value = _resp(_mxm_ok(SYNC))
    assert (
        eng._fetch_musixmatch_lyrics("A", "T", album="Al", duration=174.4, isrc="I")
        == SYNC
    )
    params = eng.session.get.call_args.kwargs["params"]
    assert params["q_album"] == "Al"
    assert params["q_duration"] == 174
    assert params["track_isrc"] == "I"


def test_musixmatch_sem_dicas_nao_envia_parametros_extras():
    eng = LyricsEngine(session=MagicMock())
    eng._mxm_token = "tok"
    eng.session.get.return_value = _resp(_mxm_ok(SYNC))
    eng._fetch_musixmatch_lyrics("A", "T")
    params = eng.session.get.call_args.kwargs["params"]
    assert not {"q_album", "q_duration", "track_isrc"} & set(params)


def test_musixmatch_repete_sem_dicas_e_depois_com_variantes():
    eng = _engine()
    chamadas = []

    def fake(artista, titulo, **dicas):
        chamadas.append((artista, titulo, bool(dicas)))
        return (
            SYNC
            if (artista, titulo, bool(dicas)) == ("Drake", "One Dance", False)
            else None
        )

    eng._fetch_musixmatch_lyrics = fake
    eng.session.get.return_value = _resp(status=404)
    r = eng.fetch_and_inject(
        "/m/a.flac",
        "Drake, Rihanna",
        "One Dance (feat. Kyla)",
        "Views",
        isrc="X",
        duration=174,
    )
    assert r["source"] == "Musixmatch"
    assert chamadas[0] == ("Drake, Rihanna", "One Dance (feat. Kyla)", True)
    assert chamadas[1] == ("Drake, Rihanna", "One Dance (feat. Kyla)", False)


def test_musixmatch_rejeita_letra_que_passa_do_fim_da_faixa():
    eng = _engine()
    eng._fetch_musixmatch_lyrics.return_value = "[09:00.00]x"
    eng.session.get.return_value = _resp(status=404)
    assert _run(eng, duration=100)["success"] is False


# ------------------------------------------------ mensagens no estilo unificado
class _Cand:
    def __init__(self, bilingual=False, source="Qobuz"):
        self.bilingual = bilingual
        self.source = source


def _largura(monkeypatch, colunas="100"):
    """Fixa a largura (o `ui` prefere o TTY real ao COLUMNS; sem isto,
    `pytest -s` num terminal dependeria da janela de quem roda)."""
    from qobuz_dl import ui

    def _sem_terminal(fd=None):
        raise OSError("sem terminal")

    monkeypatch.setattr(ui.os, "get_terminal_size", _sem_terminal)
    monkeypatch.setenv("COLUMNS", colunas)


def _linhas(kind_msg):
    from qobuz_dl import ui

    kind, msg = kind_msg
    return kind, ui.tagged_lines(kind, msg)


def test_mensagem_de_sucesso_padrao_nao_repete_o_destino(monkeypatch):
    _largura(monkeypatch)
    kind, linhas = _linhas(
        le.LyricsEngine._delivery_message(_Cand(), True, True, True, "[01] Tempo")
    )
    assert kind == "ok"
    assert "Letras: [01] Tempo" in linhas[0]
    assert "sincronizadas" in linhas[0] and "via Qobuz" in linhas[0]
    assert "metadata" not in linhas[0] and ".lrc" not in linhas[0]


def test_mensagem_de_sucesso_parcial_avisa_o_destino(monkeypatch):
    _largura(monkeypatch)
    _, so_meta = _linhas(
        le.LyricsEngine._delivery_message(_Cand(True), True, False, True, "[01] T")
    )
    _, so_lrc = _linhas(
        le.LyricsEngine._delivery_message(_Cand(), False, True, True, "[01] T")
    )
    assert "BILINGUAL" in so_meta[0] and "só no metadata" in so_meta[0]
    assert "só em .lrc" in so_lrc[0]


def test_mensagem_de_falha_e_erro_e_identifica_a_faixa(monkeypatch):
    _largura(monkeypatch)
    kind, linhas = _linhas(
        le.LyricsEngine._delivery_message(_Cand(), True, True, False, "[07] Faixa")
    )
    assert kind == "error"
    assert "Falha ao gravar letras: [07] Faixa" in linhas[0]
