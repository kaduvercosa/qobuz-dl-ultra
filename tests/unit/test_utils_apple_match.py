"""Testa a decisão "essa é a mesma música/álbum?" da busca de capa Apple.

CONTEXTO (bug real do comando `tags`)
-------------------------------------
No log de uma correção retroativa, 6 de 85 arquivos ficaram "sem capa" mesmo
com a faixa certa entre os candidatos da Apple. Causas, uma por grupo de teste:

1. COLABORAÇÃO: a Qobuz guarda só o artista principal ("Cardi B"), a Apple
   lista todos ("Cardi B & Bruno Mars"). Comparar as strings inteiras dava
   0,56 < 0,85 e rejeitava a música certa -> `score_artista`.
2. UPC/ISRC: identificador exato não deve passar pelo filtro fuzzy.
3. VERSÃO NO TÍTULO: a Qobuz separa `title` e `version`; a Apple junta.
4. DIAGNÓSTICO: a mensagem de falha mostrava o motivo MAIS FREQUENTE (de
   faixas de outros álbuns), não o do candidato mais parecido.

Por outro lado, as travas de segurança (artista errado, karaokê, edição
Live/Deluxe, coletânea) precisam continuar rejeitando: capa errada é pior que
nenhuma capa. Tudo aqui roda com uma sessão falsa -- nunca toca a rede.
"""

import pytest

from qobuz_dl import utils

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Sessão falsa
# ---------------------------------------------------------------------------
class _Resp:
    def __init__(self, dados=None, status=200):
        self._d = dados if dados is not None else {"results": []}
        self.status_code = status

    def json(self):
        return self._d


class _Sessao:
    """`lookup`/`search` devolvem sempre o mesmo corpo (nas lojas br e us)."""

    def __init__(self, lookup=None, search=None, head_status=200):
        self._lookup = lookup
        self._search = search
        self.head_status = head_status
        self.chamadas = []
        self.heads = []

    async def get(self, url, **kw):
        self.chamadas.append((url, kw.get("params")))
        if "/lookup" in url:
            return _Resp(self._lookup)
        return _Resp(self._search)

    async def head(self, url, **_kw):
        self.heads.append(url)
        return _Resp({}, status=self.head_status)


_CAPA = "https://img.test/100x100bb.jpg"


def colecao(artista, album):
    return {
        "wrapperType": "collection",
        "artistName": artista,
        "collectionName": album,
        "artworkUrl100": _CAPA,
    }


def faixa(artista, album, titulo):
    return {
        "wrapperType": "track",
        "artistName": artista,
        "collectionName": album,
        "trackName": titulo,
        "artworkUrl100": _CAPA,
    }


def corpo(*resultados):
    return {"resultCount": len(resultados), "results": list(resultados)}


async def buscar(sessao, **kw):
    return await utils.get_apple_hq_cover(sessao, **kw)


# ---------------------------------------------------------------------------
# Normalização usada na comparação
# ---------------------------------------------------------------------------
class TestExtrairEssencia:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            ("Así - Single", "asi"),
            ("Um Pouco - EP", "um pouco"),
            ("Please Me - Single", "please me"),
            ("Disco (Deluxe) [2020]", "disco"),
            ("Ação & Reação!", "acao reacao"),
        ],
    )
    def test_ignora_sufixo_single_ep_e_parenteses(self, entrada, esperado):
        assert utils.extrair_essencia(entrada) == esperado

    def test_single_no_meio_do_nome_e_mantido(self):
        assert utils.extrair_essencia("Single Ladies") == "single ladies"

    def test_hifen_sem_sufixo_conhecido_e_mantido(self):
        assert utils.extrair_essencia("Abc - Remix Pack") == "abc remix pack"


class TestExtrairTituloCompleto:
    def test_participacao_nao_muda_a_edicao(self):
        assert utils.extrair_titulo_completo(
            "Please Me (feat. Bruno Mars)"
        ) == utils.extrair_titulo_completo("Please Me")

    @pytest.mark.parametrize("marca", ["feat.", "ft.", "featuring", "with", "com"])
    def test_variantes_de_participacao(self, marca):
        assert utils.extrair_titulo_completo(f"Musica ({marca} Fulano)") == "musica"

    def test_colchetes_tambem(self):
        assert utils.extrair_titulo_completo("Musica [feat. Fulano]") == "musica"

    def test_versao_continua_no_titulo(self):
        # Radio Edit / Live / Remaster IDENTIFICAM a edição: não podem sumir.
        assert (
            utils.extrair_titulo_completo("Fat Juicy (Radio Edit)")
            == "fat juicy (radio edit)"
        )

    def test_parte_numerada_nao_e_participacao(self):
        assert utils.extrair_titulo_completo("Suite (Part 2)") == "suite (part 2)"

    def test_reticencias_unicode_e_ascii_equivalem(self):
        assert utils.extrair_titulo_completo(
            "Fat Juicy &… (Radio Edit)"
        ) == utils.extrair_titulo_completo("Fat Juicy &... (Radio Edit)")

    def test_sufixo_single_da_apple_e_ignorado(self):
        assert utils.extrair_titulo_completo("Asi - Single") == "asi"


# ---------------------------------------------------------------------------
# score_artista
# ---------------------------------------------------------------------------
class TestScoreArtista:
    @pytest.mark.parametrize(
        "pedido, apple",
        [
            ("Cardi B", "Cardi B & Bruno Mars"),
            ("J Balvin", "J Balvin, Dua Lipa, Bad Bunny & Tainy"),
            ("Baco Exu do Blues", "Baco Exu do Blues & Carol Biazin"),
            ("Abraham Mateo", "Abraham Mateo, Maria Becerra & Big One"),
            ("Ana Gabriela", "Ana Gabriela & Carol Biazin"),
            ("Bad Bunny", "Bruno Mars feat. Bad Bunny"),
            ("Anitta", "Ludmilla x Anitta"),
            ("Simone", "Simone e Simaria"),
        ],
    )
    def test_colaboracao_reconhece_o_artista_pedido(self, pedido, apple):
        assert utils.score_artista(pedido, apple) == pytest.approx(1.0)

    def test_comparacao_inversa_pedido_com_varios_artistas(self):
        assert utils.score_artista("Cardi B & Bruno Mars", "Cardi B") == pytest.approx(
            1.0
        )

    def test_ignora_acentos_e_caixa(self):
        assert utils.score_artista("ROSALÍA", "Rosalia") == pytest.approx(1.0)

    @pytest.mark.parametrize(
        "pedido, apple",
        [
            ("Cardi B", "Bruno Mars"),
            ("Cardi B", "Nicki Minaj & Megan Thee Stallion"),
            ("Anitta", "Lady Gaga"),
        ],
    )
    def test_artista_diferente_continua_baixo(self, pedido, apple):
        assert utils.score_artista(pedido, apple) < 0.85

    def test_pedido_vazio_nao_filtra(self):
        assert utils.score_artista("", "Qualquer Um") == 1.0
        assert utils.score_artista(None, "Qualquer Um") == 1.0

    def test_candidato_vazio_nao_casa(self):
        assert utils.score_artista("Cardi B", "") == 0.0

    def test_partes_artista_inclui_inteiro_e_individuais(self):
        partes = utils._partes_artista("Cardi B & Bruno Mars")
        assert partes == {"cardi b bruno mars", "cardi b", "bruno mars"}

    def test_partes_artista_descarta_vazios(self):
        assert utils._partes_artista("") == set()
        assert "" not in utils._partes_artista("A &  & B")


# ---------------------------------------------------------------------------
# UPC / ISRC como prova de identidade
# ---------------------------------------------------------------------------
class TestLookupExato:
    @pytest.mark.parametrize(
        "artista, album, apple",
        [
            ("Cardi B", "Please Me", "Cardi B & Bruno Mars"),
            ("J Balvin", "UN DIA (ONE DAY)", "J Balvin, Dua Lipa, Bad Bunny & Tainy"),
            ("Abraham Mateo", "Así", "Abraham Mateo, Maria Becerra & Big One"),
        ],
    )
    async def test_upc_aceita_colaboracao(self, artista, album, apple):
        s = _Sessao(lookup=corpo(colecao(apple, f"{album} - Single")))
        url, fonte, motivo = await buscar(
            s, upc="123", artist=artista, album=album, track_title=album
        )
        assert url == "https://img.test/10000x10000bb.jpg"
        assert (fonte, motivo) == ("Apple/iTunes", None)
        # Achou no lookup: nunca precisou da busca textual.
        assert all("/search" not in u for u, _ in s.chamadas)

    async def test_upc_confia_ate_em_titulo_diferente(self):
        # O UPC identifica a release inteira; o título da faixa não é conferido.
        s = _Sessao(lookup=corpo(colecao("Banda", "Disco")))
        url, _, _ = await buscar(
            s, upc="1", artist="Banda", album="Disco", track_title="Nada A Ver"
        )
        assert url is not None

    async def test_upc_com_artista_totalmente_diferente_tambem_e_aceito(self):
        # Decisão de projeto: identificador exato dispensa o filtro fuzzy.
        s = _Sessao(lookup=corpo(colecao("Outro Nome", "Disco")))
        url, _, _ = await buscar(s, upc="1", artist="Banda", album="Disco")
        assert url is not None

    async def test_isrc_aceita_quando_o_album_tem_relacao(self):
        s = _Sessao(
            lookup=corpo(
                faixa("Cardi B & Bruno Mars", "Please Me - Single", "Please Me")
            )
        )
        url, _, _ = await buscar(
            s, isrc="USX", artist="Cardi B", album="Please Me", track_title="Please Me"
        )
        assert url is not None
        assert "lookup?isrc=USX" in s.chamadas[0][0]

    async def test_isrc_em_colet_nea_e_rejeitado(self):
        s = _Sessao(lookup=corpo(faixa("Cardi B", "Hits 2019", "Please Me")))
        url, _, motivo = await buscar(
            s, isrc="USX", artist="Cardi B", album="Please Me", track_title="Please Me"
        )
        assert url is None
        assert "ISRC encontrado em outro álbum" in motivo

    async def test_isrc_sem_album_pedido_nao_exige_relacao(self):
        s = _Sessao(lookup=corpo(faixa("Banda", "Qualquer", "Musica")))
        url, _, _ = await buscar(s, isrc="USX", artist="Banda", track_title="Musica")
        assert url is not None

    async def test_upc_vence_a_busca_textual(self):
        s = _Sessao(
            lookup=corpo(colecao("Banda", "Disco")),
            search=corpo(colecao("Outro", "Outro")),
        )
        await buscar(s, upc="1", artist="Banda", album="Disco")
        assert len(s.chamadas) == 1

    async def test_lookup_sem_resultado_cai_na_busca_textual(self):
        s = _Sessao(
            lookup=corpo(),
            search=corpo(faixa("Banda", "Disco", "Musica")),
        )
        url, _, _ = await buscar(
            s, upc="1", isrc="2", artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is not None
        assert s.chamadas[-1][0].endswith("/search")
        assert s.chamadas[-1][1]["entity"] == "song"

    async def test_codigo_na_ou_none_e_ignorado(self):
        s = _Sessao(search=corpo(colecao("Banda", "Disco")))
        await buscar(s, upc="N/A", isrc="none", artist="Banda", album="Disco")
        assert all("/lookup" not in u for u, _ in s.chamadas)

    async def test_codigo_e_escapado_na_url(self):
        s = _Sessao(lookup=corpo())
        await buscar(s, upc="a b&c", artist="x", album="y")
        assert "a%20b%26c" in s.chamadas[0][0]


# ---------------------------------------------------------------------------
# Busca textual (fuzzy): aceita colaboração, mantém as travas
# ---------------------------------------------------------------------------
class TestBuscaTextual:
    @pytest.mark.parametrize(
        "artista, titulo, apple",
        [
            ("Cardi B", "Please Me", "Cardi B & Bruno Mars"),
            ("J Balvin", "UN DIA (ONE DAY)", "J Balvin, Dua Lipa, Bad Bunny & Tainy"),
            ("Ana Gabriela", "desnecessário", "Ana Gabriela & Carol Biazin"),
            ("Baco Exu do Blues", "Um Pouco", "Baco Exu do Blues & Carol Biazin"),
        ],
    )
    async def test_colaboracao_e_aceita(self, artista, titulo, apple):
        s = _Sessao(search=corpo(faixa(apple, f"{titulo} - Single", titulo)))
        url, _, motivo = await buscar(
            s, artist=artista, album=titulo, track_title=titulo
        )
        assert url is not None, motivo

    async def test_titulo_com_versao_junta_title_e_version(self):
        # O que o retro_tags passa agora: "Fat Juicy &…" + "(Radio Edit)".
        s = _Sessao(
            search=corpo(
                faixa(
                    "Sexyy Red & Bruno Mars",
                    "Fat Juicy &... (Radio Edit) - Single",
                    "Fat Juicy &... (Radio Edit)",
                )
            )
        )
        url, _, motivo = await buscar(
            s,
            artist="Sexyy Red",
            album="Fat Juicy &… (Radio Edit)",
            track_title="Fat Juicy &… (Radio Edit)",
        )
        assert url is not None, motivo

    async def test_titulo_sem_a_versao_ainda_e_rejeitado(self):
        # Documenta o motivo do bug: sem a versão o título completo só bate 0,58.
        s = _Sessao(
            search=corpo(
                faixa(
                    "Sexyy Red",
                    "Fat Juicy &... (Radio Edit)",
                    "Fat Juicy &... (Radio Edit)",
                )
            )
        )
        url, _, motivo = await buscar(
            s, artist="Sexyy Red", album="Fat Juicy &…", track_title="Fat Juicy &…"
        )
        assert url is None
        assert "título completo da faixa incompatível" in motivo

    async def test_participacao_so_de_um_lado_nao_atrapalha(self):
        s = _Sessao(
            search=corpo(
                faixa("Cardi B", "Please Me - Single", "Please Me (feat. Bruno Mars)")
            )
        )
        url, _, motivo = await buscar(
            s, artist="Cardi B", album="Please Me", track_title="Please Me"
        )
        assert url is not None, motivo

    async def test_busca_so_de_album_usa_entity_album(self):
        s = _Sessao(search=corpo(colecao("Banda", "Disco")))
        url, _, _ = await buscar(s, artist="Banda", album="Disco")
        assert url is not None
        assert s.chamadas[0][1]["entity"] == "album"
        assert s.chamadas[0][1]["limit"] == 25

    async def test_tenta_br_e_depois_us(self):
        s = _Sessao(search=corpo())
        await buscar(s, artist="Banda", album="Disco")
        paises = [p["country"] for _, p in s.chamadas]
        assert paises == ["br", "us"]

    async def test_escolhe_o_melhor_entre_varios(self):
        # Os dois passam nas travas, mas o álbum do segundo é mais parecido
        # com o pedido: ele ganha mesmo vindo depois na lista.
        pior = faixa("Banda", "Discos", "Musica")
        pior["artworkUrl100"] = "https://pior.test/100x100bb.jpg"
        melhor = faixa("Banda", "Disco", "Musica")
        melhor["artworkUrl100"] = "https://melhor.test/100x100bb.jpg"
        s = _Sessao(search=corpo(pior, melhor))
        url, _, _ = await buscar(s, artist="Banda", album="Disco", track_title="Musica")
        assert url.startswith("https://melhor.test/")

    async def test_empate_fica_com_o_primeiro_resultado_da_apple(self):
        # A Apple já ordena por relevância: em empate, vale a ordem dela.
        a = faixa("Banda", "Disco", "Musica")
        a["artworkUrl100"] = "https://primeiro.test/100x100bb.jpg"
        b = faixa("Banda & Convidado", "Disco - Single", "Musica")
        b["artworkUrl100"] = "https://segundo.test/100x100bb.jpg"
        s = _Sessao(search=corpo(a, b))
        url, _, _ = await buscar(s, artist="Banda", album="Disco", track_title="Musica")
        assert url.startswith("https://primeiro.test/")

    # --- travas de segurança: capa errada é pior que nenhuma capa ---------
    async def test_artista_diferente_e_rejeitado(self):
        s = _Sessao(
            search=corpo(faixa("Bruno Mars", "Please Me - Single", "Please Me"))
        )
        url, _, motivo = await buscar(
            s, artist="Cardi B", album="Please Me", track_title="Please Me"
        )
        assert url is None
        assert "artista incompatível" in motivo

    @pytest.mark.parametrize("termo", ["Karaoke", "Tribute", "Cover", "Instrumental"])
    async def test_termos_indesejados_sao_rejeitados(self, termo):
        s = _Sessao(search=corpo(faixa("Banda", f"Disco ({termo})", "Musica")))
        url, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is None
        assert "termo indesejado" in motivo

    async def test_termo_pedido_pelo_usuario_nao_e_lixo(self):
        # Se o álbum REAL se chama "... (Instrumental)", não pode ser barrado.
        s = _Sessao(search=corpo(colecao("Banda", "Disco (Instrumental)")))
        url, _, motivo = await buscar(s, artist="Banda", album="Disco (Instrumental)")
        assert url is not None, motivo

    async def test_edicao_live_e_rejeitada(self):
        s = _Sessao(search=corpo(faixa("Banda", "Disco - Single", "Musica (Live)")))
        url, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is None
        assert "faixa incompatível" in motivo

    async def test_album_deluxe_e_rejeitado_na_busca_de_album(self):
        s = _Sessao(search=corpo(colecao("Banda", "Disco (Deluxe Edition)")))
        url, _, motivo = await buscar(s, artist="Banda", album="Disco")
        assert url is None
        assert "álbum incompatível" in motivo or "edição" in motivo

    async def test_album_muito_diferente_para_faixa_e_rejeitado(self):
        s = _Sessao(
            search=corpo(faixa("Banda", "Totalmente Outra Coisa Aqui", "Musica"))
        )
        url, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is None
        assert "álbum muito diferente" in motivo

    async def test_resultado_sem_collection_name(self):
        s = _Sessao(search=corpo({"artistName": "Banda", "artworkUrl100": _CAPA}))
        url, _, motivo = await buscar(s, artist="Banda", album="Disco")
        assert url is None
        assert "sem collectionName" in motivo

    async def test_resultado_sem_artwork(self):
        s = _Sessao(search=corpo(colecao("Banda", "Disco") | {"artworkUrl100": ""}))
        url, _, motivo = await buscar(s, artist="Banda", album="Disco")
        assert url is None
        assert "sem artworkUrl100" in motivo

    async def test_head_falhando_em_todas_as_resolucoes(self):
        s = _Sessao(search=corpo(colecao("Banda", "Disco")), head_status=404)
        url, _, motivo = await buscar(s, artist="Banda", album="Disco")
        assert url is None
        assert "URL da imagem não validada" in motivo
        assert "HTTP 404" in motivo
        # Tentou as 3 resoluções antes de desistir.
        assert any("10000x10000bb" in h for h in s.heads)
        assert any("3000x3000bb" in h for h in s.heads)
        assert any("1400x1400bb" in h for h in s.heads)

    async def test_cai_para_resolucao_menor_quando_a_maior_nao_existe(self):
        class SoMedia(_Sessao):
            async def head(self, url, **_kw):
                return _Resp({}, status=200 if "3000x3000bb" in url else 404)

        s = SoMedia(search=corpo(colecao("Banda", "Disco")))
        url, _, _ = await buscar(s, artist="Banda", album="Disco")
        assert url == "https://img.test/3000x3000bb.jpg"


# ---------------------------------------------------------------------------
# Diagnóstico de falha
# ---------------------------------------------------------------------------
class TestDiagnostico:
    async def test_motivo_e_do_candidato_mais_proximo_nao_do_mais_frequente(self):
        # 3 lixos "iguais" + 1 candidato quase certo (só o artista difere).
        lixo = [faixa("Zzz", "Qqq", "Www") for _ in range(3)]
        quase = faixa("Bruno Mars", "Please Me - Single", "Please Me")
        s = _Sessao(search=corpo(*lixo, quase))
        _, _, motivo = await buscar(
            s, artist="Cardi B", album="Please Me", track_title="Please Me"
        )
        assert motivo.startswith("artista incompatível")
        assert "candidato mais próximo" in motivo
        assert "Bruno Mars" in motivo

    async def test_inclui_ate_tres_exemplos(self):
        itens = [faixa(f"Artista {i}", "Disco - Single", "Musica") for i in range(6)]
        s = _Sessao(search=corpo(*itens))
        _, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        exemplos = motivo.split("exemplos: ", 1)[1].split(" | ")
        assert len(exemplos) == 3

    async def test_sem_resultados_nas_lojas(self):
        s = _Sessao(lookup=corpo(), search=corpo())
        url, fonte, motivo = await buscar(s, upc="1", artist="Banda", album="Disco")
        assert (url, fonte) == (None, None)
        assert "não retornou resultados" in motivo

    async def test_erro_http_vira_motivo_de_rede(self):
        class Quebrada(_Sessao):
            async def get(self, url, **kw):
                return _Resp({}, status=503)

        url, _, motivo = await buscar(Quebrada(), upc="1", artist="a", album="b")
        assert url is None
        assert "HTTP 503" in motivo

    async def test_excecao_de_rede_nao_propaga(self):
        class Explode(_Sessao):
            async def get(self, url, **kw):
                raise ConnectionError("sem internet")

        url, _, motivo = await buscar(Explode(), artist="a", album="b")
        assert url is None
        assert "ConnectionError" in motivo
        assert "sem internet" in motivo

    async def test_nada_para_buscar(self):
        s = _Sessao()
        url, _, motivo = await buscar(s)
        assert url is None and s.chamadas == []
        assert isinstance(motivo, str) and motivo

    async def test_contrato_sempre_devolve_tupla_de_tres(self):
        s = _Sessao(search=corpo(colecao("Banda", "Disco")))
        ok = await buscar(s, artist="Banda", album="Disco")
        falha = await buscar(_Sessao(search=corpo()), artist="Banda", album="Disco")
        assert isinstance(ok, tuple) and len(ok) == 3
        assert isinstance(falha, tuple) and len(falha) == 3
