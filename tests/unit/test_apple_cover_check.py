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

    async def falso(
        artist, album, track, upc=None, isrc=None, session=None, trace=None
    ):
        trace.append(
            {"type": "request", "tag": "t", "url": "u", "params": {}, "results": []}
        )
        return None, None, "motivo-x"

    monkeypatch.setattr(modulo, "check", falso)
    assert modulo.main(["A", "B", "C", "--all"]) == 1
    saida = capsys.readouterr().out
    assert "CONSULTAS À APPLE (1)" in saida and "NÃO ENCONTRADA: motivo-x" in saida
