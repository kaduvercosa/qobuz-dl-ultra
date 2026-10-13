"""Testa o script `tools/apple_cover_check.py` (checagem de UMA faixa)."""

import asyncio
import importlib.util
import os

import pytest

pytestmark = pytest.mark.unit

_CAMINHO = os.path.join(
    os.path.dirname(__file__), "..", "..", "tools", "apple_cover_check.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("apple_cover_check", _CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class _Resp:
    status_code = 200

    def __init__(self, dados):
        self._d = dados

    def json(self):
        return self._d


class _Sessao:
    def __init__(self, corpo):
        self.corpo = corpo

    async def get(self, url, **kw):
        return _Resp(self.corpo)

    async def head(self, url, **kw):
        return _Resp({})


def _faixa(album, titulo):
    return {
        "artistName": "Banda",
        "collectionName": album,
        "trackName": titulo,
        "artworkUrl100": "https://img.test/100x100bb.jpg",
    }


def test_faixa_encontrada():
    modulo = _carregar()
    sessao = _Sessao({"results": [_faixa("Disco", "Musica")]})
    url, fonte, motivo = asyncio.run(
        modulo.check("Banda", "Disco", "Musica", session=sessao)
    )
    assert url == "https://img.test/10000x10000bb.jpg"
    assert (fonte, motivo) == ("Apple/iTunes", None)


def test_faixa_nao_encontrada_devolve_o_motivo():
    modulo = _carregar()
    sessao = _Sessao({"results": [_faixa("Outro Disco Totalmente", "Outra")]})
    url, _, motivo = asyncio.run(
        modulo.check("Banda", "Disco", "Musica", session=sessao)
    )
    assert url is None and motivo


def test_format_trace_lista_consultas_resultados_e_vereditos():
    modulo = _carregar()
    trace = [
        {
            "type": "request",
            "tag": "busca principal song br",
            "url": "https://itunes.apple.com/search",
            "params": {"term": "Jão VSF", "entity": "song"},
            "results": [
                {
                    "tipo": "track",
                    "artista": "Jão",
                    "album": "Turnê ANTI-HERÓI (Ao Vivo)",
                    "faixa": "VSF (Ao Vivo)",
                    "id": 5,
                }
            ],
        },
        {"type": "reject", "reason": "versão incompatível", "label": "artist='Jão'"},
        {"type": "accept", "rank": 1.05, "perfect": True, "label": "artist='Jão'"},
    ]
    texto = "\n".join(modulo.format_trace(trace))
    assert "CONSULTAS À APPLE (1)" in texto
    assert "term=Jão VSF entity=song" in texto
    assert "1 resultado(s)" in texto
    assert "faixa='VSF (Ao Vivo)'" in texto
    assert "REJEITADO: versão incompatível" in texto
    assert "ACEITO (perfeito) rank=1.050" in texto


def test_main_com_all_imprime_a_trace(monkeypatch, capsys):
    modulo = _carregar()

    async def falso(artist, album, track, upc=None, isrc=None, trace=None, **_kw):
        trace.append(
            {"type": "request", "tag": "t", "url": "u", "params": {}, "results": []}
        )
        return None, None, "motivo-x"

    monkeypatch.setattr(modulo, "check", falso)
    assert modulo.main(["A", "B", "C", "--all"]) == 1
    saida = capsys.readouterr().out
    assert "CONSULTAS À APPLE (1)" in saida and "NÃO ENCONTRADA: motivo-x" in saida


def test_check_envia_tipo_paises_e_fallback(monkeypatch):
    modulo = _carregar()
    vistos = {}

    async def falsa(sessao, **kw):
        vistos.update(kw)
        return ("U", "Apple/iTunes", None)

    monkeypatch.setattr("qobuz_dl.utils.get_apple_hq_cover", falsa)
    asyncio.run(
        modulo.check("A", "B", "C", kind="single", countries=["jp"], fallback=True)
    )
    assert vistos["release_kind"] == "single"
    assert vistos["countries"] == ("jp",)
    assert vistos["fallback_countries"] is True

    asyncio.run(modulo.check("A", "B", "C", kind="album"))
    assert vistos["release_kind"] == "full"
    asyncio.run(modulo.check("A", "B", "C"))
    assert vistos["release_kind"] is None and vistos["countries"] is None


def test_decide_monta_o_album_da_qobuz(monkeypatch):
    modulo = _carregar()
    from qobuz_dl import retro_tags

    recebido = {}

    async def falsa(album, sessao, resultados, trace=None):
        recebido.update(album=album, trace=trace)
        return {
            "url": "U",
            "source": "Apple/iTunes",
            "method": "busca do álbum",
            "note": None,
        }

    class _Sessao:
        async def aclose(self):
            pass

    monkeypatch.setattr(retro_tags, "_decide_album_cover", falsa)
    decisao = asyncio.run(
        modulo.decide(
            "The Weeknd",
            "Take My Breath",
            ["Take My Breath (Single Version)"],
            upc="123",
            kind="single",
            session=_Sessao(),
            trace=[],
        )
    )
    album = recebido["album"]
    assert decisao["url"] == "U"
    assert album["title"] == "Take My Breath" and album["upc"] == "123"
    assert album["artist"] == {"name": "The Weeknd"}
    assert album["release_type"] == "single" and album["tracks_count"] == 1
    assert album["tracks"]["items"][0]["title"] == "Take My Breath (Single Version)"


def test_main_decide_usa_a_faixa_quando_nao_ha_album_track(monkeypatch):
    modulo = _carregar()
    chamado = {}

    async def falsa(
        artist, album, album_tracks, upc=None, kind=None, session=None, trace=None
    ):
        chamado.update(album_tracks=album_tracks, kind=kind)
        return {
            "url": "U",
            "source": "Apple/iTunes",
            "method": "busca do álbum",
            "note": None,
        }

    monkeypatch.setattr(modulo, "decide", falsa)
    assert modulo.main(["A", "B", "Faixa X", "--decide", "--kind", "single"]) == 0
    assert chamado == {"album_tracks": ["Faixa X"], "kind": "single"}

    chamado.clear()
    modulo.main(
        [
            "A",
            "B",
            "Faixa X",
            "--decide",
            "--album-track",
            "Um",
            "--album-track",
            "Dois",
        ]
    )
    assert chamado["album_tracks"] == ["Um", "Dois"]
