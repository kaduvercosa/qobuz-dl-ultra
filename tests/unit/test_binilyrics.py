"""Fonte BiniLyrics: escolha da gravação, parser TTML, saídas e provider.

Os TTMLs abaixo são sintéticos (textos inventados): o que importa é a
estrutura -- spans fragmentados, agentes, seções, sobreposição e traduções.
"""

from unittest.mock import MagicMock

import pytest

from qobuz_dl import binilyrics as bl
from qobuz_dl import lyrics_engine as le
from qobuz_dl.lyrics_engine import LyricsEngine

TTML_WORD = """<?xml version="1.0" encoding="UTF-8"?>
<tt xmlns="http://www.w3.org/ns/ttml"
    xmlns:itunes="http://music.apple.com/lyric-ttml-internal"
    xmlns:ttm="http://www.w3.org/ns/ttml#metadata"
    itunes:timing="Word" xml:lang="es">
<head><metadata>
<ttm:agent type="person" xml:id="v1"/><ttm:agent type="group" xml:id="v2"/>
<iTunesMetadata xmlns="http://music.apple.com/lyric-ttml-internal"><translations>
<translation type="subtitle" xml:lang="en-US">
<text for="L1">english one</text><text for="L2">english two</text></translation>
<translation type="subtitle" xml:lang="pt-BR">
<text for="L1">português um</text><text for="L2">português dois</text></translation>
</translations></iTunesMetadata></metadata></head>
<body dur="00:00:20.000">
<div begin="0.500" end="9.000" itunes:song-part="Verse">
<p begin="1.000" end="4.000" ttm:agent="v1" itunes:key="L1"><span begin="1.000" end="2.000">an</span><span begin="2.000" end="3.000">gels</span> <span begin="3.000" end="4.000">sing</span></p>
</div>
<div begin="10.000" end="20.000" itunes:song-part="Chorus">
<p begin="10.200" end="13.400" ttm:agent="v2" itunes:key="L2"><span begin="10.200" end="11.000">hello</span> <span begin="11.000" end="13.400">world</span></p>
<p begin="12.500" end="14.000" ttm:agent="v1" itunes:key="L3"><span begin="12.500" end="14.000">(oh)</span></p>
</div></body></tt>"""

TTML_LINE = """<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en">
<body><div>
<p begin="10.000" end="13.000">Texto da linha</p>
<p begin="1:02.500" end="1:05.000">Outra linha</p>
</div></body></tt>"""

TTML_PLAIN = """<tt xmlns="http://www.w3.org/ns/ttml" xml:lang="en">
<body><div><p>primeira</p><p>segunda</p></div></body></tt>"""


def cand(isrc, name, album, dur, timing, artist="Emicida"):
    return {
        "isrc": isrc,
        "id": isrc,
        "album_name": album,
        "duration": dur,
        "artist_name": artist,
        "timing_type": timing,
        "lyricsUrl": f"https://lrc.red/s/{isrc}.ttml",
        "track_name": name,
    }


STUDIO = cand(
    "BRX6F1900014", "Amarelo (feat. Pabllo Vittar & Majur)", "AmarElo", 321, "word"
)
LIVE = cand(
    "BRX6F2100060",
    "AmarElo (Sample: Sujeito de Sorte - Belchior) [Ao Vivo]",
    "AmarElo (Ao Vivo)",
    338,
    "line",
    "Emicida, Majur, Pabllo Vittar",
)


# ---------------------------------------------------------------- tempo
@pytest.mark.unit
@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("10.200", 10200),
        ("1:02.5", 62500),
        ("00:01:02.500", 62500),
        ("350ms", 350),
        ("12s", 12000),
        ("abc", None),
        (None, None),
    ],
)
def test_parse_time(valor, esperado):
    assert bl.parse_time(valor) == esperado


@pytest.mark.unit
def test_format_timestamp_e_canonico():
    assert bl.format_timestamp(10200) == "[00:10.200]"
    assert bl.format_timestamp(62500) == "[01:02.500]"


# ------------------------------------------------------------- seleção
@pytest.mark.unit
def test_isrc_exato_vence_mesmo_com_titulo_diferente():
    r = bl.select_candidate(
        [LIVE, STUDIO], isrc="brx6f1900014", title="Outro nome", artist="x", duration=1
    )
    assert r is STUDIO


@pytest.mark.unit
def test_nao_usa_o_primeiro_resultado_estudio_vs_ao_vivo():
    # o ao vivo vem primeiro, mas a faixa é a de estúdio
    r = bl.select_candidate(
        [LIVE, STUDIO],
        title="AmarElo (feat. Pabllo Vittar & Majur)",
        artist="Emicida",
        album="AmarElo",
        duration=321,
    )
    assert r is STUDIO


@pytest.mark.unit
def test_faixa_ao_vivo_nao_casa_com_a_de_estudio():
    r = bl.select_candidate(
        [STUDIO], title="AmarElo [Ao Vivo]", artist="Emicida", duration=338
    )
    assert r is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "dur_candidato,aceita", [(323, True), (325, True), (338, False)]
)
def test_tolerancia_de_duracao(dur_candidato, aceita):
    c = cand("ISRC1", "Song", "Alb", dur_candidato, "line")
    r = bl.select_candidate([c], title="Song", artist="Emicida", duration=321)
    assert (r is c) is aceita


@pytest.mark.unit
def test_mesma_gravacao_prefere_word_depois_line_depois_plain():
    a = cand("A", "Song", "Alb", 200, "plain")
    b = cand("B", "Song", "Alb", 200, "line")
    c = cand("C", "Song", "Alb", 200, "word")
    r = bl.select_candidate(
        [a, b, c], title="Song", artist="Emicida", album="Alb", duration=200
    )
    assert r is c


@pytest.mark.unit
def test_artista_diferente_e_rejeitado():
    c = cand("A", "Song", "Alb", 200, "word", artist="Outra Banda")
    assert (
        bl.select_candidate([c], title="Song", artist="Emicida", duration=200) is None
    )


@pytest.mark.unit
def test_sem_resultados_ou_sem_url():
    assert bl.select_candidate([], title="Song") is None
    assert bl.select_candidate([{"track_name": "Song"}], title="Song") is None
    assert bl.select_candidate(None, title="Song") is None


# -------------------------------------------------------------- parser
@pytest.mark.unit
def test_parse_word_preserva_estrutura():
    doc = bl.parse_ttml(TTML_WORD)
    assert doc.timing == "word" and doc.language == "es"
    assert doc.declared_timing == "Word"
    assert doc.agents == {"v1": "person", "v2": "group"}
    assert [ln.id for ln in doc.lines] == ["L1", "L2", "L3"]
    assert [ln.section for ln in doc.lines] == ["Verse", "Chorus", "Chorus"]
    assert [ln.agent for ln in doc.lines] == ["v1", "v2", "v1"]
    assert doc.raw == TTML_WORD  # fonte de verdade intacta


@pytest.mark.unit
def test_span_nao_e_palavra_fragmentos_preservados():
    primeira = bl.parse_ttml(TTML_WORD).lines[0]
    assert [s.text for s in primeira.spans] == ["an", "gels", "sing"]
    assert primeira.text == "angels sing"
    assert [s.space_after for s in primeira.spans] == [False, True, False]


@pytest.mark.unit
def test_sobreposicao_nao_e_corrigida_nem_removida():
    doc = bl.parse_ttml(TTML_WORD)
    l2, l3 = doc.lines[1], doc.lines[2]
    assert (l2.begin, l2.end) == (10200, 13400)
    assert (l3.begin, l3.end) == (12500, 14000)  # começa antes de l2 acabar
    assert len(bl.to_line_lrc(doc).splitlines()) == 3


@pytest.mark.unit
def test_tipos_line_e_plain():
    assert bl.parse_ttml(TTML_LINE).timing == "line"
    assert bl.parse_ttml(TTML_PLAIN).timing == "plain"


# -------------------------------------------------------------- saídas
@pytest.mark.unit
def test_line_lrc_e_cronologico_e_canonico():
    lrc = bl.to_line_lrc(bl.parse_ttml(TTML_LINE))
    assert lrc == "[00:10.000]Texto da linha\n[01:02.500]Outra linha"


@pytest.mark.unit
def test_word_lrc_usa_so_tempos_do_ttml():
    lrc = bl.to_word_lrc(bl.parse_ttml(TTML_WORD)).splitlines()
    assert lrc[0] == "[00:01.000]<00:01.000>an<00:02.000>gels <00:03.000>sing"


@pytest.mark.unit
def test_line_nunca_vira_word():
    doc = bl.parse_ttml(TTML_LINE)
    assert bl.to_word_lrc(doc) == bl.to_line_lrc(doc)
    assert "<" not in bl.to_word_lrc(doc)


@pytest.mark.unit
def test_plain_sem_timestamps():
    assert bl.to_plain(bl.parse_ttml(TTML_PLAIN)) == "primeira\nsegunda"


# ------------------------------------------------------------- tradução
@pytest.mark.unit
def test_pt_br_tem_prioridade_sobre_en_us():
    doc = bl.parse_ttml(TTML_WORD)
    assert bl.pick_translation_lang(doc, "pt") == "pt-BR"
    assert bl.pick_translation_lang(doc, "pt-br") == "pt-BR"


@pytest.mark.unit
def test_en_us_nao_substitui_pt_br():
    doc = bl.parse_ttml(TTML_WORD)
    doc.translations.pop("pt-BR")
    assert bl.pick_translation_lang(doc, "pt") is None


@pytest.mark.unit
def test_musica_ja_em_portugues_nao_ganha_traducao():
    doc = bl.parse_ttml(TTML_WORD)
    doc.language = "pt-BR"
    assert bl.pick_translation_lang(doc, "pt") is None


@pytest.mark.unit
def test_traducao_fica_no_timestamp_da_linha_original():
    doc = bl.parse_ttml(TTML_WORD)
    assert bl.translation_lrc(doc, "pt-BR").splitlines() == [
        "[00:01.000]português um",
        "[00:10.200]português dois",
    ]


# ----------------------------------------------------------- segurança
@pytest.mark.unit
@pytest.mark.parametrize(
    "dado",
    [
        '<!DOCTYPE x [<!ENTITY a "b">]><tt xmlns="http://www.w3.org/ns/ttml"/>',
        "isto nao e xml",
        "<tt>" + "a" * (bl.MAX_TTML_BYTES + 1) + "</tt>",
    ],
)
def test_ttml_perigoso_ou_invalido_e_rejeitado(dado):
    with pytest.raises(ValueError):
        bl.parse_ttml(dado)


# ------------------------------------------------------------ provider
def _resp(status=200, json_data=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data
    r.text = text
    return r


def _engine(rotas):
    eng = LyricsEngine(session=MagicMock())
    eng.PROVIDERS = ("_provider_binilyrics",)
    eng._inject_metadata = MagicMock(return_value=True)
    eng._save_lrc_file = MagicMock(return_value=True)
    eng._inject_instrumental_pauses = lambda t: t
    chamadas = []

    def get(url, **kwargs):
        chamadas.append((url, kwargs))
        return rotas(url, kwargs)

    eng.session.get = get
    eng.chamadas = chamadas
    return eng


def _rotas_estudio(url, kwargs):
    if url == bl.API_URL:
        return _resp(json_data={"results": [LIVE, STUDIO], "total": 2})
    return _resp(text=TTML_WORD)


def _buscar(eng, **extra):
    base = dict(
        file_path="/m/a.flac",
        artist="Emicida",
        track="Amarelo (feat. Pabllo Vittar & Majur)",
        album="AmarElo",
        isrc="BRX6F1900014",
        duration=321,
    )
    base.update(extra)
    return eng.fetch_and_inject(**base)


@pytest.mark.unit
def test_provider_baixa_ttml_da_gravacao_certa_e_traduz_para_pt_br():
    eng = _engine(_rotas_estudio)
    r = _buscar(eng)
    assert r["success"] and r["source"] == "BiniLyrics"
    assert r["synchronized"] and r["bilingual"] and r["language"] == "es+pt-br"
    assert eng.chamadas[0][1]["params"]["duration"] == 321  # segundos, numérico
    assert eng.chamadas[1][0] == STUDIO["lyricsUrl"]
    texto = eng._inject_metadata.call_args.args[1]
    # original e tradução com o MESMO timestamp, sem símbolo
    assert "[00:01.000]angels sing\n[00:01.000]português um" in texto
    assert "english" not in texto


@pytest.mark.unit
def test_provider_sem_duracao_nao_chama_a_rede():
    eng = _engine(_rotas_estudio)
    r = _buscar(eng, duration=None)
    assert r["success"] is False and eng.chamadas == []


@pytest.mark.unit
def test_provider_sem_candidato_correto_nao_baixa_ttml():
    eng = _engine(lambda url, kw: _resp(json_data={"results": [LIVE]}))
    r = _buscar(eng, isrc=None)
    assert r["success"] is False and len(eng.chamadas) == 1


@pytest.mark.unit
def test_provider_texto_puro_respeita_only_synced():
    def rotas(url, kw):
        if url == bl.API_URL:
            return _resp(json_data={"results": [dict(STUDIO, timing_type="plain")]})
        return _resp(text=TTML_PLAIN)

    eng = _engine(rotas)
    eng.settings = MagicMock(only_synced_lyrics=True, lyrics_translation_lang="pt")
    assert _buscar(eng)["success"] is False

    eng2 = _engine(rotas)
    r = _buscar(eng2)
    assert r["success"] and r["synchronized"] is False


@pytest.mark.unit
def test_provider_ttml_invalido_nao_derruba_as_outras_fontes():
    def rotas(url, kw):
        if url == bl.API_URL:
            return _resp(json_data={"results": [STUDIO]})
        return _resp(text="<!DOCTYPE x><tt/>")

    eng = _engine(rotas)
    eng.PROVIDERS = ("_provider_binilyrics", "_provider_lrclib")
    eng._provider_lrclib = lambda q: le.LyricsCandidate("[00:01.000]ok", "LRCLIB", True)
    r = _buscar(eng)
    assert r["success"] and r["source"] == "LRCLIB"


@pytest.mark.unit
def test_binilyrics_esta_no_registro_depois_do_qobuz():
    assert (
        LyricsEngine.PROVIDERS.index("_provider_qobuz")
        < LyricsEngine.PROVIDERS.index("_provider_binilyrics")
        < LyricsEngine.PROVIDERS.index("_provider_musixmatch")
    )
