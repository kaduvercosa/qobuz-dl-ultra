"""Testa helpers de `qobuz_dl/qopy.py` (cliente da API) sem rede.

Cobre o que os outros `test_qopy_*.py` não tocavam: resolução do token em
várias fontes, busca de assinatura aninhada, paginação de `multi_meta`,
busca por ISRC/UPC, `match_external_tracks` e favoritos. `api_call` é sempre
substituído por um fake que registra os argumentos.
"""

import pytest

from qobuz_dl.qopy import Client, _resolve_user_auth_token

pytestmark = pytest.mark.unit


class _Fake:
    """`api_call` falso: devolve respostas em fila (ou levanta se for exceção)."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.chamadas = []

    async def __call__(self, epoint, **kw):
        self.chamadas.append((epoint, kw))
        r = self.respostas.pop(0) if self.respostas else {}
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def cliente(monkeypatch):
    c = Client()

    def instalar(*respostas):
        fake = _Fake(*respostas)
        monkeypatch.setattr(c, "api_call", fake)
        return fake

    c.instalar = instalar
    return c


# ---------------------------------------------------------------------------
# Token
# ---------------------------------------------------------------------------
class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class TestResolveUserAuthToken:
    def test_argumento_explicito_vence(self):
        c = _Obj(uat="do-uat", user_auth_token="do-user")
        assert _resolve_user_auth_token(c, "  explicito  ") == "explicito"

    def test_ordem_uat_depois_user_auth_token(self):
        assert _resolve_user_auth_token(_Obj(uat="a", user_auth_token="b")) == "a"
        assert _resolve_user_auth_token(_Obj(user_auth_token="b")) == "b"

    @pytest.mark.parametrize("c", [_Obj(), _Obj(uat="", user_auth_token=None)])
    def test_sem_nada_devolve_vazio(self, c):
        assert _resolve_user_auth_token(c) == ""

    def test_token_que_nao_e_texto_vira_texto(self):
        assert _resolve_user_auth_token(_Obj(uat=12345)) == "12345"

    def test_valor_so_com_espacos_e_aparado(self):
        # `"   "` é verdadeiro para `if value`, então é devolvido já aparado.
        assert _resolve_user_auth_token(_Obj(uat="   ")) == ""


class TestTokenCandidates:
    def test_mesma_prioridade_do_helper(self):
        c = Client()
        c.uat = "u"
        c.user_auth_token = "x"
        assert c._token_candidates("arg") == "arg"
        assert c._token_candidates() == "u"
        c.uat = ""
        assert c._token_candidates() == "x"

    def test_sem_token(self):
        c = Client()
        c.uat = c.user_auth_token = None
        assert c._token_candidates() == ""


# ---------------------------------------------------------------------------
# Assinatura
# ---------------------------------------------------------------------------
class TestFindSubscription:
    @pytest.fixture
    def c(self):
        return Client()

    def test_direto_na_raiz(self, c):
        sub = {"end_date": "2030-01-01"}
        assert c._find_subscription({"subscription": sub}) is sub

    @pytest.mark.parametrize("chave", ["user", "account", "profile", "credential"])
    def test_aninhado_em_chaves_conhecidas(self, c, chave):
        sub = {"offer": "studio"}
        assert c._find_subscription({chave: {"subscription": sub}}) is sub

    def test_aninhamento_profundo(self, c):
        sub = {"x": 1}
        dados = {"user": {"account": {"profile": {"subscription": sub}}}}
        assert c._find_subscription(dados) is sub

    def test_nao_inventa_assinatura(self, c):
        assert c._find_subscription({"user": {"id": 1}}) is None
        assert c._find_subscription({"outra": {"subscription": {"a": 1}}}) is None

    @pytest.mark.parametrize("valor", [None, "texto", 5, [], {"subscription": "x"}])
    def test_formatos_inesperados(self, c, valor):
        assert c._find_subscription(valor) is None


# ---------------------------------------------------------------------------
# multi_meta (paginação)
# ---------------------------------------------------------------------------
async def _coletar(gen):
    return [pagina async for pagina in gen]


class TestMultiMeta:
    async def test_pagina_ate_o_total(self, cliente):
        fake = cliente.instalar(
            {"albums": {"items": [{"id": 1}, {"id": 2}], "total": 3}},
            {"albums": {"items": [{"id": 3}], "total": 3}},
        )
        paginas = await _coletar(cliente.multi_meta("artist/get", "albums_count", "9", None))
        assert len(paginas) == 2
        # Segunda chamada avança o offset pelo que veio na primeira.
        assert fake.chamadas[0][1]["offset"] == 0
        assert fake.chamadas[1][1]["offset"] == 2
        assert fake.chamadas[0][1]["limit"] == 50

    async def test_para_na_pagina_vazia(self, cliente):
        cliente.instalar(
            {"albums": {"items": [{"id": 1}], "total": 99}},
            {"albums": {"items": [], "total": 99}},
        )
        paginas = await _coletar(cliente.multi_meta("artist/get", "k", "9", None))
        assert len(paginas) == 1

    async def test_playlist_usa_a_chave_tracks(self, cliente):
        cliente.instalar({"tracks": {"items": [{"id": 1}], "total": 1}})
        paginas = await _coletar(cliente.multi_meta("playlist/get", "k", "9", None))
        assert len(paginas) == 1

    async def test_type_aninha_a_resposta(self, cliente):
        cliente.instalar(
            {"label": {"albums": {"items": [{"id": 1}], "total": 1}}}
        )
        paginas = await _coletar(cliente.multi_meta("label/get", "k", "9", "label"))
        assert paginas == [{"albums": {"items": [{"id": 1}], "total": 1}}]

    async def test_total_ausente_usa_a_chave_de_contagem(self, cliente):
        cliente.instalar({"albums": {"items": [{"id": 1}]}, "albums_count": 1})
        paginas = await _coletar(cliente.multi_meta("artist/get", "albums_count", "9", None))
        assert len(paginas) == 1

    async def test_resposta_sem_itens_nao_gera_paginas(self, cliente):
        cliente.instalar({})
        assert await _coletar(cliente.multi_meta("artist/get", "k", "9", None)) == []


# ---------------------------------------------------------------------------
# Endpoints simples
# ---------------------------------------------------------------------------
async def test_get_track_lyrics_url(cliente):
    fake = cliente.instalar({"url": "u"})
    assert await cliente.get_track_lyrics_url(77) == {"url": "u"}
    assert fake.chamadas == [("track/lyricsUrl", {"track_id": 77})]


class TestBuscaPorIsrcEUpc:
    async def test_isrc_normaliza_e_devolve_o_id(self, cliente):
        fake = cliente.instalar({"tracks": {"items": [{"id": 55}, {"id": 56}]}})
        assert await cliente.search_by_isrc("  usabc1234567 ") == 55
        epoint, kw = fake.chamadas[0]
        assert epoint == "catalog/search"
        assert kw == {"query": "USABC1234567", "type": "tracks", "limit": 1}

    async def test_upc_apara_mas_nao_muda_a_caixa(self, cliente):
        fake = cliente.instalar({"albums": {"items": [{"id": "alb"}]}})
        assert await cliente.search_by_upc(" 0123456 ") == "alb"
        assert fake.chamadas[0][1] == {"query": "0123456", "type": "albums", "limit": 1}

    @pytest.mark.parametrize("metodo", ["search_by_isrc", "search_by_upc"])
    @pytest.mark.parametrize("codigo", ["", None])
    async def test_codigo_vazio_nem_chama_a_api(self, cliente, metodo, codigo):
        fake = cliente.instalar()
        assert await getattr(cliente, metodo)(codigo) is None
        assert fake.chamadas == []

    @pytest.mark.parametrize("metodo", ["search_by_isrc", "search_by_upc"])
    async def test_sem_resultado(self, cliente, metodo):
        cliente.instalar({"tracks": {"items": []}, "albums": {"items": []}})
        assert await getattr(cliente, metodo)("X") is None

    @pytest.mark.parametrize("metodo", ["search_by_isrc", "search_by_upc"])
    async def test_resposta_nula_ou_quebrada_vira_none(self, cliente, metodo):
        cliente.instalar(None)
        assert await getattr(cliente, metodo)("X") is None

    @pytest.mark.parametrize("metodo", ["search_by_isrc", "search_by_upc"])
    async def test_erro_da_api_nao_propaga(self, cliente, metodo):
        cliente.instalar(RuntimeError("503"))
        assert await getattr(cliente, metodo)("X") is None


class TestMatchExternalTracks:
    """Só o caminho com ISRC: faixas SEM ISRC hoje nem entram na fila fuzzy
    (o `append` está dentro do `if isrc`); como o método não é chamado em
    nenhum lugar do projeto, esse comportamento não é travado aqui."""

    async def test_isrc_exato_nao_usa_o_fuzzy(self, cliente, monkeypatch):
        ids = {"AAA": 1, "BBB": 2}

        async def por_isrc(isrc):
            return ids.get(isrc)

        async def nao_deve_rodar(lista):
            raise AssertionError("fuzzy chamado sem necessidade")

        monkeypatch.setattr(cliente, "search_by_isrc", por_isrc)
        monkeypatch.setattr(cliente, "get_track_ids_from_list", nao_deve_rodar)
        r = await cliente.match_external_tracks([{"isrc": "aaa"}, {"isrc": " bbb "}])
        assert r == [1, 2]

    async def test_isrc_sem_match_cai_no_fuzzy(self, cliente, monkeypatch):
        async def por_isrc(isrc):
            return 10 if isrc == "HIT" else None

        recebido = []

        async def fuzzy(lista):
            recebido.extend(lista)
            return [99]

        monkeypatch.setattr(cliente, "search_by_isrc", por_isrc)
        monkeypatch.setattr(cliente, "get_track_ids_from_list", fuzzy)
        faltou = {"isrc": "MISS", "title": "x"}
        r = await cliente.match_external_tracks([{"isrc": "hit"}, faltou])
        assert r == [10, 99]
        assert recebido == [faltou]

    async def test_lista_vazia(self, cliente):
        assert await cliente.match_external_tracks([]) == []


# ---------------------------------------------------------------------------
# Favoritos
# ---------------------------------------------------------------------------
class TestFavoritos:
    async def test_album(self, cliente):
        fake = cliente.instalar({"status": "success"})
        await cliente.add_favorite_album(123)
        assert fake.chamadas == [
            ("favorite/create", {"album_ids": "123", "artist_ids": "", "track_ids": ""})
        ]

    async def test_faixa(self, cliente):
        fake = cliente.instalar({})
        await cliente.add_favorite_track(9)
        assert fake.chamadas == [
            ("favorite/create", {"track_ids": "9", "album_ids": "", "artist_ids": ""})
        ]

    @pytest.mark.parametrize(
        "tipo, chave", [("track", "track_ids"), ("album", "album_ids")]
    )
    async def test_despacha_pelo_tipo(self, cliente, tipo, chave):
        fake = cliente.instalar({})
        await cliente.add_favorite("77", tipo)
        assert fake.chamadas[0][1][chave] == "77"

    async def test_tipo_desconhecido(self, cliente):
        cliente.instalar()
        with pytest.raises(ValueError, match="desconhecido"):
            await cliente.add_favorite("1", "artist")
