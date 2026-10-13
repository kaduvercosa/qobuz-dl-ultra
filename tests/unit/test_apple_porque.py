"""Testa `tools/apple_porque.py`: o veredito "por que a capa não seria trocada".

Cada teste monta um catálogo Apple falso e confere QUAL das respostas o
diagnóstico dá: a música não existe (NAO TEM), existe só em outro álbum, só em
outra versão, existe no álbum certo mas um filtro recusou, ou a rede falhou.
Inclui a regressão dos títulos com parênteses aninhados "(Album Version
(Explicit))", que fazia a música certa ser recusada.
"""

import asyncio
import importlib.util
import os

import pytest

from qobuz_dl import utils

pytestmark = pytest.mark.unit

_CAMINHO = os.path.join(
    os.path.dirname(__file__), "..", "..", "tools", "apple_porque.py"
)


def _carregar():
    spec = importlib.util.spec_from_file_location("apple_porque", _CAMINHO)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


class _Resp:
    def __init__(self, dados=None, status=200):
        self._d = dados if dados is not None else {"results": []}
        self.status_code = status

    def json(self):
        return self._d


class _Apple:
    """Catálogo falso: {id: (artista, nome do álbum, [faixas])}."""

    def __init__(self, albuns, status=200, isrc_linhas=None):
        self.albuns = albuns
        self.status = status
        self.isrc_linhas = isrc_linhas or []

    @staticmethod
    def _arte():
        return "https://img.test/100x100bb.jpg"

    def _album(self, cid):
        artista, nome, _ = self.albuns[cid]
        return {
            "wrapperType": "collection",
            "collectionId": cid,
            "artistName": artista,
            "collectionName": nome,
            "artworkUrl100": self._arte(),
        }

    def _faixa(self, cid, titulo):
        artista, nome, _ = self.albuns[cid]
        return {
            "wrapperType": "track",
            "kind": "song",
            "artistName": artista,
            "collectionName": nome,
            "trackName": titulo,
            "artworkUrl100": self._arte(),
        }

    async def get(self, url, **kw):
        if self.status != 200:
            return _Resp({}, self.status)
        params = kw.get("params") or {}
        if "/lookup" in url:
            if "isrc=" in url:
                return _Resp({"results": self.isrc_linhas})
            if params.get("entity") == "song" and params.get("id") in self.albuns:
                cid = params["id"]
                faixas = self.albuns[cid][2]
                return _Resp(
                    {"results": [self._album(cid)] + [self._faixa(cid, t) for t in faixas]}
                )
            return _Resp({"results": []})
        entidade = params.get("entity")
        if entidade == "album":
            return _Resp({"results": [self._album(c) for c in self.albuns]})
        if entidade == "song":
            return _Resp(
                {
                    "results": [
                        self._faixa(c, t)
                        for c, (_a, _n, faixas) in self.albuns.items()
                        for t in faixas
                    ]
                }
            )
        return _Resp({"results": []})

    async def head(self, url, **_kw):
        return _Resp({}, status=200)


def _diag(catalogo, artista, album, faixa, **kw):
    modulo = _carregar()
    sessao = _Apple(catalogo, **kw.pop("loja", {}))
    return asyncio.run(modulo.diagnosticar(sessao, artista, album, faixa, **kw))


def _check(diag):
    return {rotulo: (ok, texto) for ok, rotulo, texto in diag["checklist"]}


# ---------------------------------------------------------------------------
def test_troca_quando_a_musica_exata_existe():
    cat = {1: ("The Weeknd", "Dawn FM", ["Gasoline", "Less Than Zero"])}
    d = _diag(cat, "The Weeknd", "Dawn FM (Explicit)", "Gasoline")
    assert d["veredito"] == "troca"
    assert d["url"]


def test_nao_tem_quando_so_ha_outra_musica_com_o_mesmo_titulo():
    # "Die For It" do Belly não está no catálogo; só a de outro artista.
    cat = {1: ("Ashaunté", "Die For It - Single", ["Die For It"])}
    d = _diag(cat, "Belly", "See You Next Wednesday", "Die For It")
    assert d["veredito"] == "nao_tem"
    ok = _check(d)
    assert ok["Artista"][0] is False
    assert ok["Música"][0] is False
    assert "Ashaunté" in " ".join(d["por_que"])


def test_outro_album_quando_a_musica_so_existe_em_outro_lancamento():
    cat = {1: ("The Weeknd", "Black Panther: The Album", ["Pray For Me"])}
    d = _diag(cat, "The Weeknd", "Pray For Me", "Pray For Me", tipo="single")
    assert d["veredito"] == "outro_album"
    ok = _check(d)
    assert ok["Artista"][0] is True
    assert ok["Música"][0] is True
    assert ok["Álbum"][0] is False
    assert "Black Panther" in " ".join(d["por_que"])


def test_outra_versao_quando_no_album_certo_so_ha_remix():
    cat = {1: ("The Weeknd", "Earned It", ["Earned It [Marian Hill Remix]"])}
    d = _diag(cat, "The Weeknd", "Earned It", "Earned It")
    assert d["veredito"] == "versao"
    assert _check(d)["Versão"][0] is False


def test_filtro_quando_o_tipo_single_x_album_recusa_e_mostra_o_motivo():
    # Qobuz diz "single"; a Apple só tem o álbum de mesmo nome (não "- Single").
    cat = {1: ("Banda", "Disco", ["Faixa"])}
    d = _diag(cat, "Banda", "Disco", "Faixa", tipo="single")
    assert d["veredito"] == "filtro"
    assert any("tipo de lançamento" in m for m in d["motivos"])
    assert _check(d)["Tipo"][0] is False


def test_titulo_parecido_no_mesmo_album_nao_e_dado_como_a_mesma_musica():
    cat = {1: ("Banda", "Disco", ["Die For You"])}
    d = _diag(cat, "Banda", "Disco", "Die For It")
    assert d["veredito"] == "titulo"
    assert "Die For You" in " ".join(d["por_que"])


def test_inconclusivo_quando_a_apple_nao_responde():
    d = _diag({}, "Banda", "Disco", "Faixa", loja={"status": 500})
    assert d["veredito"] == "rede"


def test_troca_por_isrc_nao_depende_do_texto():
    cat = {1: ("Outro Nome", "Outro Disco", ["Outra Faixa"])}
    linha = {
        "wrapperType": "track",
        "artistName": "Banda",
        "collectionName": "Disco",
        "trackName": "Faixa",
        "artworkUrl100": "https://img.test/100x100bb.jpg",
    }
    d = _diag(cat, "Banda", "Disco", "Faixa", isrc="US123", loja={"isrc_linhas": [linha]})
    assert d["veredito"] == "troca"
    assert "UPC/ISRC" in d["por_que"][0]


# ---------------------------------------------------------------------------
# Regressão: versão aninhada "(Album Version (Explicit))"
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "qobuz, apple",
    [
        ("Wicked Games (Album Version (Explicit))", "Wicked Games"),
        ("The Morning (Album Version (Explicit))", "The Morning"),
        ("Live For (Album Version (Explicit))", "Live For"),
    ],
)
def test_versao_aninhada_vira_o_mesmo_titulo(qobuz, apple):
    assert utils.extrair_titulo_comparavel(qobuz) == utils.extrair_titulo_comparavel(apple)


def test_termo_de_busca_com_parenteses_aninhados_nao_deixa_sobra():
    assert utils.termo_de_busca("Live For (Album Version (Explicit))") == "Live For"


def test_musica_com_versao_aninhada_agora_e_trocada():
    cat = {1: ("The Weeknd", "Trilogy", ["Wicked Games", "The Morning"])}
    d = _diag(
        cat, "The Weeknd", "Trilogy", "Wicked Games (Album Version (Explicit))", tipo="full"
    )
    assert d["veredito"] == "troca"


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "album, total, esperado",
    [("X", 1, "single"), ("X", 3, "single"), ("X", 4, "full"), ("X", 12, "full"),
     ("X", "", None), ("X", "abc", None)],
)
def test_tipo_do_lancamento(album, total, esperado):
    assert _carregar().tipo_do_lancamento(album, total) == esperado


def test_ler_tags_de_arquivo_ilegivel_devolve_none(tmp_path):
    arq = tmp_path / "x.flac"
    arq.write_text("não é um flac")
    assert _carregar().ler_tags(str(arq)) is None


def test_limpar_tag_tira_a_flag_de_explicito():
    assert _carregar().limpar_tag("Faixa \U0001f174") == "Faixa"


def test_cliente_com_pausa_repete_em_429_e_guarda_a_resposta():
    modulo = _carregar()

    class Real:
        def __init__(self):
            self.n = 0

        async def get(self, url, **kw):
            self.n += 1
            return _Resp({"results": [1]}, 429 if self.n == 1 else 200)

    async def rodar():
        real = Real()
        cliente = modulo.ClienteComPausa(real, intervalo=0)
        original = asyncio.sleep

        async def rapido(_s):
            return None

        modulo.asyncio.sleep = rapido
        try:
            a = await cliente.get("https://itunes.apple.com/search", params={"term": "x"})
            b = await cliente.get("https://itunes.apple.com/search", params={"term": "x"})
        finally:
            modulo.asyncio.sleep = original
        return real.n, a.status_code, b is a

    chamadas, status, mesma = asyncio.run(rodar())
    assert (chamadas, status, mesma) == (2, 200, True)


def test_uso_invalido_devolve_2():
    assert _carregar().main(["so", "dois"]) == 2
