"""Regressão: `_try_apple_cover_bytes` com o contrato NOVO da busca Apple.

`get_apple_hq_cover` passou a devolver `(url, fonte, motivo)`. O downloader
ainda tratava o retorno como texto, então `re.sub(..., apple_url)` levantava
`TypeError: expected string or bytes-like object, got 'tuple'` -- tanto com
capa encontrada quanto sem -- e a exceção (fora do try) derrubava a etapa de
capa de todo download. Os testes antigos de `TestTryAppleCoverBytes` mockavam
a função devolvendo `str`, por isso nunca viram o problema.

Aqui o mock devolve exatamente o que a função real devolve.
"""

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


@pytest.fixture
def apple(monkeypatch):
    """Mock da busca (`retorno`) e do download (`baixados`)."""
    estado = {
        "retorno": ("https://apple/100x100bb.jpg", "Apple/iTunes", None),
        "pedidos": [],
        "baixados": [],
        "bytes": b"IMG",
    }

    async def fake_busca(**kwargs):
        estado["pedidos"].append(kwargs)
        return estado["retorno"]

    async def fake_download(url, session, max_bytes, headers=None):
        estado["baixados"].append(url)
        return estado["bytes"]

    monkeypatch.setattr(downloader, "get_apple_hq_cover", fake_busca)
    monkeypatch.setattr(downloader, "_download_bytes_with_limit", fake_download)
    return estado


async def _tentar(**kw):
    return await downloader._try_apple_cover_bytes(
        None, **{"artist": "Artista", "album": "Album", **kw}
    )


class TestRetornoEmTupla:
    async def test_sucesso_nao_levanta_e_devolve_os_bytes(self, apple):
        assert await _tentar() == b"IMG"

    async def test_url_da_tupla_e_a_que_e_baixada(self, apple):
        await _tentar()
        assert apple["baixados"][0].startswith("https://apple/")
        assert "100x100bb" not in apple["baixados"][0]  # trocou pela resolução maior

    async def test_falha_com_tupla_de_nones_devolve_none_sem_levantar(self, apple):
        apple["retorno"] = (None, None, "artista incompatível (0.56 < 0.85)")
        assert await _tentar() is None
        assert apple["baixados"] == []

    async def test_motivo_da_falha_vai_para_o_log_de_debug(self, apple, monkeypatch):
        # O logger do módulo está fixo em INFO, então o debug é interceptado
        # no próprio logger em vez de depender do nível do caplog.
        mensagens = []

        class _Log:
            def debug(self, msg, *a, **k):
                mensagens.append(str(msg))

        monkeypatch.setattr(downloader, "logger", _Log())
        apple["retorno"] = (None, None, "motivo-especifico-xyz")
        assert await _tentar() is None
        assert any("motivo-especifico-xyz" in m for m in mensagens)

    @pytest.mark.parametrize(
        "retorno",
        [
            (None, None, None),
            ("", "Apple/iTunes", None),
            ("   ", "Apple/iTunes", None),
            (None,),
            (),
        ],
    )
    async def test_urls_invalidas_na_tupla_sao_falha(self, apple, retorno):
        apple["retorno"] = retorno
        assert await _tentar() is None
        assert apple["baixados"] == []

    async def test_tupla_curta_so_com_a_url(self, apple):
        apple["retorno"] = ("https://apple/100x100bb.jpg",)
        assert await _tentar() == b"IMG"

    async def test_cascata_de_resolucoes_continua_funcionando(self, apple):
        apple["bytes"] = None  # nenhuma resolução cabe no limite
        assert await _tentar() is None
        assert len(apple["baixados"]) == len(downloader._APPLE_COVER_SIZES)

    async def test_repassa_todos_os_identificadores_para_a_busca(self, apple):
        await _tentar(upc="0123", isrc="USX1", track_title="Musica")
        pedido = apple["pedidos"][0]
        assert (pedido["upc"], pedido["isrc"], pedido["track_title"]) == (
            "0123",
            "USX1",
            "Musica",
        )
        assert (pedido["artist"], pedido["album"]) == ("Artista", "Album")


class TestCompatibilidadeComFormatoAntigo:
    async def test_url_em_texto_ainda_funciona(self, apple):
        apple["retorno"] = "https://apple/100x100bb.jpg"
        assert await _tentar() == b"IMG"

    @pytest.mark.parametrize("retorno", [None, "", "   "])
    async def test_texto_vazio_ou_none_e_falha(self, apple, retorno):
        apple["retorno"] = retorno
        assert await _tentar() is None
        assert apple["baixados"] == []

    async def test_tipo_inesperado_nao_derruba(self, apple):
        apple["retorno"] = 12345
        assert await _tentar() is None


async def test_sem_artista_ou_album_nem_busca(apple):
    assert await _tentar(artist=None) is None
    assert await _tentar(album="") is None
    assert apple["pedidos"] == []


async def test_excecao_na_busca_cai_para_a_qobuz(monkeypatch):
    async def explode(**kwargs):
        raise RuntimeError("timeout")

    monkeypatch.setattr(downloader, "get_apple_hq_cover", explode)
    assert await _tentar() is None
