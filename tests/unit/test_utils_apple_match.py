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


# ---------------------------------------------------------------------------
# Sufixos de edição que NÃO mudam a gravação x marcadores de versão que mudam
#
# Bug real do comando `tags` (Jão - "Fim Do Mundo"/"Ressaca"): a Apple devolve
# "Fim do Mundo (Bonus Track)" no álbum "LOBOS (Deluxe)", a Qobuz guarda só
# "Fim Do Mundo". O título completo batia 0,63 < 0,85 e a capa certa era
# rejeitada. "(Bonus Track)"/"(Deluxe)" só dizem em qual edição a faixa está;
# "(Ao Vivo)"/"(Live)"/"(Acoustic)" são OUTRA gravação e continuam barrados.
# ---------------------------------------------------------------------------
class TestTituloComparavel:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            ("Fim do Mundo (Bonus Track)", "fim do mundo"),
            ("Ressaca [Bonus Tracks]", "ressaca"),
            ("Musica (Deluxe Edition)", "musica"),
            ("Musica (Deluxe)", "musica"),
            ("Musica (Explicit)", "musica"),
            ("VSF (Ao Vivo)", "vsf"),
            ("Musica (Live)", "musica"),
            ("Musica (Acoustic Version)", "musica"),
        ],
    )
    def test_remove_sufixos_inofensivos_e_marcadores_de_versao(
        self, entrada, esperado
    ):
        assert utils.extrair_titulo_comparavel(entrada) == esperado

    @pytest.mark.parametrize(
        "entrada",
        [
            "Fat Juicy (Radio Edit)",
            "Musica (Remastered 2011)",
            "Suite (Part 2)",
            "Musica (Live at Wembley)",
        ],
    )
    def test_outras_versoes_continuam_no_titulo(self, entrada):
        assert utils.extrair_titulo_comparavel(entrada) == (
            utils.extrair_titulo_completo(entrada)
        )

    def test_musica_chamada_bonus_track_nao_vira_vazio(self):
        assert utils.extrair_titulo_comparavel("Bonus Track") == "bonus track"

    def test_vazio(self):
        assert utils.extrair_titulo_comparavel("") == ""
        assert utils.extrair_titulo_comparavel(None) == ""


class TestMarcadoresDeVersao:
    @pytest.mark.parametrize(
        "titulo, esperado",
        [
            ("VSF (Ao Vivo)", {"live"}),
            ("Song (Live at Wembley)", {"live"}),
            ("Cancion (En Vivo)", {"live"}),
            ("Musica (Acústico)", {"acoustic"}),
            ("Song (Unplugged)", {"acoustic"}),
            ("Musica (Demo)", {"demo"}),
            ("Musica (Remix)", {"remix"}),
            ("Musica (Instrumental)", {"instrumental"}),
            ("Musica (Live) (Remix)", {"live", "remix"}),
            ("VSF", set()),
            ("Alive", set()),  # \b: "live" dentro de outra palavra não conta
            ("", set()),
        ],
    )
    def test_detecta_marcadores(self, titulo, esperado):
        assert utils.marcadores_versao(titulo) == frozenset(esperado)


class TestEdicoesNaBuscaTextual:
    async def test_bonus_track_no_deluxe_e_aceito(self):
        # O caso do log: Qobuz "LOBOS"/"Fim Do Mundo", Apple "LOBOS (Deluxe)".
        s = _Sessao(
            search=corpo(
                faixa("Jão", "Álcool - Single", "Álcool"),
                faixa("Jão", "LOBOS (Deluxe)", "Fim do Mundo (Bonus Track)"),
            )
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="LOBOS", track_title="Fim Do Mundo"
        )
        assert url is not None, motivo

    async def test_bonus_track_pedido_e_apple_sem_sufixo_tambem(self):
        s = _Sessao(search=corpo(faixa("Jão", "LOBOS", "Ressaca")))
        url, _, motivo = await buscar(
            s, artist="Jão", album="LOBOS", track_title="Ressaca (Bonus Track)"
        )
        assert url is not None, motivo

    async def test_single_com_album_diferente_continua_rejeitado(self):
        # Mesmo título, mas a capa do single costuma ser outra: não arrisca.
        s = _Sessao(search=corpo(faixa("Jão", "Ressaca - Single", "Ressaca")))
        url, _, motivo = await buscar(
            s, artist="Jão", album="LOBOS", track_title="Ressaca"
        )
        assert url is None
        assert "álbum muito diferente" in motivo

    async def test_versao_ao_vivo_continua_rejeitada_para_faixa_de_estudio(self):
        s = _Sessao(
            search=corpo(faixa("Jão", "Turnê ANTI-HERÓI (Ao Vivo)", "VSF (Ao Vivo)"))
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="ANTI-HERÓI", track_title="VSF"
        )
        assert url is None
        assert "versão da faixa incompatível" in motivo

    async def test_album_ao_vivo_na_qobuz_aceita_faixa_ao_vivo_da_apple(self):
        # A Qobuz omite "(Ao Vivo)" no título da faixa, mas o álbum é ao vivo.
        s = _Sessao(
            search=corpo(faixa("Jão", "Turnê ANTI-HERÓI (Ao Vivo)", "VSF (Ao Vivo)"))
        )
        url, _, motivo = await buscar(
            s,
            artist="Jão",
            album="Turnê ANTI-HERÓI (Ao Vivo)",
            track_title="VSF",
        )
        assert url is not None, motivo

    async def test_titulo_longo_nao_deixa_o_live_passar_pela_similaridade(self):
        # Em títulos longos "(Live)" custa pouco na razão de similaridade
        # (>= 0,85); o conjunto de marcadores é que barra.
        titulo = "Uma Musica Com Um Titulo Realmente Bem Comprido Aqui"
        s = _Sessao(search=corpo(faixa("Banda", "Disco - Single", f"{titulo} (Live)")))
        url, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title=titulo
        )
        assert url is None
        assert "versão da faixa incompatível" in motivo

    @pytest.mark.parametrize("marcador", ["Acoustic", "Demo", "Unplugged"])
    async def test_outras_versoes_de_gravacao_sao_rejeitadas(self, marcador):
        s = _Sessao(
            search=corpo(faixa("Banda", "Disco - Single", f"Musica ({marcador})"))
        )
        url, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is None
        assert "faixa incompatível" in motivo

    async def test_remastered_continua_sendo_outra_edicao(self):
        s = _Sessao(
            search=corpo(faixa("Banda", "Disco - Single", "Musica (Remastered 2011)"))
        )
        url, _, motivo = await buscar(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is None
        assert "título completo da faixa incompatível" in motivo


# ---------------------------------------------------------------------------
# Achar a faixa DENTRO do álbum certo, avaliando todos os candidatos
#
# Bug real (Jão - "VSF"): a busca por faixa ("Jão VSF") só devolveu a versão
# ao vivo; o "Vsf" do álbum de estúdio "Anti-Herói" existe na Apple, mas nunca
# foi avaliado. Agora, quando a busca por faixa não resolve, o álbum pedido é
# localizado, as faixas dele são listadas e conferidas, e a escolha final
# vem de TODOS os candidatos -- nunca do primeiro resultado que apareceu.
# ---------------------------------------------------------------------------
def _arte(nome):
    return f"https://{nome}.test/100x100bb.jpg"


def album_apple(cid, artista, nome):
    return {
        "wrapperType": "collection",
        "collectionId": cid,
        "artistName": artista,
        "collectionName": nome,
        "artworkUrl100": _arte(f"album{cid}"),
    }


def faixa_arte(artista, album, titulo, nome_arte):
    item = faixa(artista, album, titulo)
    item["artworkUrl100"] = _arte(nome_arte)
    return item


class _SessaoRoteada:
    """Responde conforme o tipo de chamada: busca de faixa, de álbum ou
    lookup das faixas de um álbum (por collectionId)."""

    def __init__(self, song=None, album=None, tracks=None):
        self.song = song or corpo()
        self.album = album or corpo()
        self.tracks = tracks or {}
        self.chamadas = []

    async def get(self, url, **kw):
        params = kw.get("params") or {}
        self.chamadas.append((url, params))
        if "/lookup" in url:
            return _Resp(self.tracks.get(params.get("id"), corpo()))
        if params.get("entity") == "album":
            return _Resp(self.album)
        return _Resp(self.song)

    async def head(self, url, **_kw):
        return _Resp({}, status=200)

    def lookups(self):
        return [p["id"] for u, p in self.chamadas if "/lookup" in u]


class TestFaixaDentroDoAlbumCerto:
    async def test_estudio_e_achado_no_album_quando_a_busca_so_traz_ao_vivo(self):
        ao_vivo = "Turnê ANTI-HERÓI (Ao Vivo)"
        s = _SessaoRoteada(
            song=corpo(faixa("Jão", ao_vivo, "VSF (Ao Vivo)")),
            album=corpo(
                album_apple(20, "Jão", ao_vivo),
                album_apple(10, "Jão", "Anti-Herói"),
            ),
            tracks={
                10: corpo(
                    album_apple(10, "Jão", "Anti-Herói"),
                    faixa_arte("Jão", "Anti-Herói", "Fim de Festa", "outra"),
                    faixa_arte("Jão", "Anti-Herói", "Vsf", "estudio"),
                ),
                20: corpo(faixa_arte("Jão", ao_vivo, "VSF (Ao Vivo)", "aovivo")),
            },
        )
        url, fonte, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is not None, motivo
        assert url.startswith("https://estudio.test/")
        # O álbum ao vivo nem é consultado para uma faixa de estúdio.
        assert s.lookups() == [10]

    async def test_albuns_na_lista_do_log_edicao_deluxe_com_bonus_track(self):
        # Qobuz "LOBOS"/"Ressaca"; a Apple tem "Lobos" (sem Ressaca) e
        # "LOBOS (Deluxe)" (com "Ressaca (Bonus Track)"), mais o single.
        s = _SessaoRoteada(
            song=corpo(faixa("Jão", "Ressaca - Single", "Ressaca")),
            album=corpo(
                album_apple(2, "Jão", "Lobos"),
                album_apple(1, "Jão", "LOBOS (Deluxe)"),
            ),
            tracks={
                2: corpo(faixa_arte("Jão", "Lobos", "Outra Musica", "standard")),
                1: corpo(
                    faixa_arte("Jão", "LOBOS (Deluxe)", "Ressaca (Bonus Track)", "dlx")
                ),
            },
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="LOBOS", track_title="Ressaca"
        )
        assert url is not None, motivo
        assert url.startswith("https://dlx.test/")
        # Os dois álbuns (Lobos e LOBOS (Deluxe)) foram conferidos.
        assert set(s.lookups()) == {1, 2}

    async def test_prefere_a_edicao_identica_mesmo_que_apareca_depois(self):
        s = _SessaoRoteada(
            song=corpo(
                faixa_arte("Jão", "LOBOS (Deluxe)", "Musica", "deluxe"),
                faixa_arte("Jão", "LOBOS", "Musica", "padrao"),
            )
        )
        url, _, _ = await buscar(s, artist="Jão", album="LOBOS", track_title="Musica")
        assert url.startswith("https://padrao.test/")

    async def test_nao_para_no_primeiro_aceito_se_ele_nao_e_o_melhor(self):
        # A busca por faixa só acha a edição Deluxe (aceitável, mas não
        # idêntica); o álbum de título idêntico, achado na etapa seguinte,
        # vence.
        s = _SessaoRoteada(
            song=corpo(faixa_arte("Jão", "LOBOS (Deluxe)", "Musica", "deluxe")),
            album=corpo(album_apple(2, "Jão", "LOBOS")),
            tracks={2: corpo(faixa_arte("Jão", "LOBOS", "Musica", "padrao"))},
        )
        url, _, _ = await buscar(s, artist="Jão", album="LOBOS", track_title="Musica")
        assert url.startswith("https://padrao.test/")

    async def test_candidato_perfeito_da_busca_por_faixa_encerra_cedo(self):
        s = _SessaoRoteada(song=corpo(faixa_arte("Jão", "LOBOS", "Musica", "ok")))
        url, _, _ = await buscar(s, artist="Jão", album="LOBOS", track_title="Musica")
        assert url.startswith("https://ok.test/")
        assert all(
            "/lookup" not in u and p.get("entity") != "album" for u, p in s.chamadas
        )

    async def test_faixa_que_nao_existe_em_nenhum_album_e_rejeitada(self):
        s = _SessaoRoteada(
            song=corpo(faixa("Jão", "Anti-Herói", "Outra")),
            album=corpo(album_apple(10, "Jão", "Anti-Herói")),
            tracks={10: corpo(faixa("Jão", "Anti-Herói", "Fim de Festa"))},
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is None
        assert "candidato mais próximo" in motivo

    async def test_album_de_outro_artista_nao_e_consultado(self):
        s = _SessaoRoteada(
            album=corpo(album_apple(9, "Outro Artista", "Anti-Herói")),
            tracks={9: corpo(faixa("Outro Artista", "Anti-Herói", "VSF"))},
        )
        url, _, _ = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is None and s.lookups() == []

    async def test_lookup_do_album_e_reaproveitado_na_mesma_sessao(self):
        s = _SessaoRoteada(
            album=corpo(album_apple(10, "Jão", "Anti-Herói")),
            tracks={
                10: corpo(
                    faixa_arte("Jão", "Anti-Herói", "Vsf", "a"),
                    faixa_arte("Jão", "Anti-Herói", "Barcelona", "a"),
                )
            },
        )
        for titulo in ("VSF", "Barcelona"):
            url, _, motivo = await buscar(
                s, artist="Jão", album="Anti-Herói", track_title=titulo
            )
            assert url is not None, motivo
        # Uma consulta só para as duas faixas (a 2ª vem do cache da sessão).
        assert s.lookups().count(10) == 1

    async def test_busca_so_de_album_nao_faz_a_etapa_do_album(self):
        s = _SessaoRoteada(album=corpo(colecao("Banda", "Disco")))
        await buscar(s, artist="Banda", album="Disco")
        assert all("/lookup" not in u for u, _ in s.chamadas)


class TestDiagnosticoDaEtapaDoAlbum:
    async def test_motivo_diz_que_o_album_foi_achado_e_a_faixa_nao(self):
        s = _SessaoRoteada(
            song=corpo(faixa("Jão", "Turnê ANTI-HERÓI (Ao Vivo)", "VSF (Ao Vivo)")),
            album=corpo(album_apple(10, "Jão", "Anti-Herói")),
            tracks={10: corpo(faixa("Jão", "Anti-Herói", "Fim de Festa"))},
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is None
        assert "etapa do álbum" in motivo
        assert "'Anti-Herói' (1 faixas)" in motivo

    async def test_motivo_diz_quando_nenhum_album_e_compativel(self):
        s = _SessaoRoteada(
            album=corpo(album_apple(9, "Outro Artista", "Anti-Herói")),
        )
        _, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert "nenhum álbum compatível com 'Anti-Herói' (1 vistos)" in motivo

    async def test_motivo_diz_quando_a_busca_de_album_vem_vazia(self):
        s = _SessaoRoteada(song=corpo(faixa("Jão", "Outro Disco Qualquer", "Musica")))
        _, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert "a busca de álbum não retornou resultados" in motivo

    async def test_erro_de_rede_na_etapa_do_album_aparece_no_motivo(self):
        class Quebra(_SessaoRoteada):
            async def get(self, url, **kw):
                if "/lookup" in url:
                    return _Resp({}, status=503)
                return await super().get(url, **kw)

        s = Quebra(
            song=corpo(faixa("Jão", "Turnê ANTI-HERÓI (Ao Vivo)", "VSF (Ao Vivo)")),
            album=corpo(album_apple(10, "Jão", "Anti-Herói")),
        )
        _, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert "erros de rede" in motivo and "HTTP 503" in motivo


# ---------------------------------------------------------------------------
# A busca da Apple é irregular: "Jão Anti-Herói" pode devolver só o álbum ao
# vivo. O álbum de estúdio precisa ser achado por outros caminhos.
# ---------------------------------------------------------------------------
class _SessaoPorTermo:
    """Respostas por (entity, term) nas buscas e por id nos lookups."""

    def __init__(self, buscas=None, tracks=None, catalogo=None):
        self.buscas = buscas or {}
        self.tracks = tracks or {}
        self.catalogo = catalogo or {}
        self.chamadas = []

    async def get(self, url, **kw):
        params = kw.get("params") or {}
        self.chamadas.append((url, params))
        if "/lookup" in url:
            tabela = self.catalogo if params.get("entity") == "album" else self.tracks
            return _Resp(tabela.get(params.get("id"), corpo()))
        return _Resp(self.buscas.get((params.get("entity"), params.get("term")), corpo()))

    async def head(self, url, **_kw):
        return _Resp({}, status=200)


_AO_VIVO = "Turnê ANTI-HERÓI (Ao Vivo)"


def _faixas_antiheroi():
    return corpo(
        album_apple(1, "Jão", "Anti-Herói"),
        faixa_arte("Jão", "Anti-Herói", "Fim de Festa", "x"),
        faixa_arte("Jão", "Anti-Herói", "Vsf", "estudio"),
    )


class TestFontesAlternativasDeAlbum:
    # A busca por faixa e a busca "artista+álbum" só enxergam o ao vivo.
    _BASE = {
        ("song", "Jão VSF"): corpo(faixa("Jão", _AO_VIVO, "VSF (Ao Vivo)")),
        ("album", "Jão Anti-Herói"): corpo(album_apple(2, "Jão", _AO_VIVO)),
    }

    async def test_album_achado_na_busca_so_pelo_artista(self):
        buscas = dict(self._BASE)
        buscas[("album", "Jão")] = corpo(
            album_apple(2, "Jão", _AO_VIVO), album_apple(1, "Jão", "Anti-Herói")
        )
        s = _SessaoPorTermo(buscas=buscas, tracks={1: _faixas_antiheroi()})
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is not None, motivo
        assert url.startswith("https://estudio.test/")

    async def test_album_achado_no_catalogo_do_artista(self):
        buscas = dict(self._BASE)
        buscas[("musicArtist", "Jão")] = corpo(
            {"wrapperType": "artist", "artistId": 99, "artistName": "Jão"}
        )
        s = _SessaoPorTermo(
            buscas=buscas,
            tracks={1: _faixas_antiheroi()},
            catalogo={
                99: corpo(
                    {"wrapperType": "artist", "artistId": 99, "artistName": "Jão"},
                    album_apple(1, "Jão", "Anti-Herói"),
                )
            },
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is not None, motivo
        assert url.startswith("https://estudio.test/")

    async def test_faixa_achada_na_busca_de_musicas_pelo_nome_do_album(self):
        buscas = dict(self._BASE)
        buscas[("song", "Jão Anti-Herói")] = corpo(
            faixa_arte("Jão", "Anti-Herói", "Fim de Festa", "x"),
            faixa_arte("Jão", "Anti-Herói", "Vsf", "estudio"),
        )
        s = _SessaoPorTermo(buscas=buscas)
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is not None, motivo
        assert url.startswith("https://estudio.test/")
        # Não precisou nem listar álbuns.
        assert all("/lookup" not in u for u, _ in s.chamadas)

    async def test_catalogo_de_outro_artista_e_ignorado(self):
        buscas = dict(self._BASE)
        buscas[("musicArtist", "Jão")] = corpo(
            {"wrapperType": "artist", "artistId": 7, "artistName": "Outro Artista"}
        )
        s = _SessaoPorTermo(
            buscas=buscas,
            catalogo={7: corpo(album_apple(1, "Outro Artista", "Anti-Herói"))},
            tracks={1: _faixas_antiheroi()},
        )
        url, _, _ = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert url is None

    async def test_mesmo_candidato_por_duas_etapas_conta_uma_rejeicao(self):
        buscas = dict(self._BASE)
        buscas[("song", "Jão Anti-Herói")] = corpo(faixa("Jão", _AO_VIVO, "VSF (Ao Vivo)"))
        s = _SessaoPorTermo(buscas=buscas)
        _, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", track_title="VSF"
        )
        assert "1 rejeitado(s) no total" in motivo


class TestTrace:
    async def test_trace_lista_consultas_resultados_e_vereditos(self):
        s = _SessaoRoteada(
            song=corpo(faixa("Jão", "Anti-Herói", "Vsf")),
        )
        trace = []
        url, _, _ = await utils.get_apple_hq_cover(
            s, artist="Jão", album="Anti-Herói", track_title="VSF", trace=trace
        )
        assert url is not None
        pedidos = [e for e in trace if e["type"] == "request"]
        assert pedidos and pedidos[0]["results"][0]["faixa"] == "Vsf"
        assert any(e["type"] == "accept" and e["perfect"] for e in trace)

    async def test_trace_registra_rejeicoes_com_motivo(self):
        s = _SessaoRoteada(song=corpo(faixa("Jão", _AO_VIVO, "VSF (Ao Vivo)")))
        trace = []
        await utils.get_apple_hq_cover(
            s, artist="Jão", album="Anti-Herói", track_title="VSF", trace=trace
        )
        rejeicoes = [e for e in trace if e["type"] == "reject"]
        assert rejeicoes and "versão da faixa incompatível" in rejeicoes[0]["reason"]

    async def test_sem_trace_nada_muda(self):
        s = _SessaoRoteada(song=corpo(faixa("Banda", "Disco", "Musica")))
        url, _, _ = await utils.get_apple_hq_cover(
            s, artist="Banda", album="Disco", track_title="Musica"
        )
        assert url is not None


# ---------------------------------------------------------------------------
# Single x álbum: a Qobuz diz o tipo (release_kind) e a Apple marca singles
# com "- Single". Um single só recebe capa de single; álbum/EP nunca recebe a
# capa de um single (as artes costumam ser diferentes).
# ---------------------------------------------------------------------------
class TestTipoSingleXAlbum:
    @pytest.mark.parametrize(
        "nome, esperado",
        [
            ("Ressaca - Single", "single"),
            ("Ressaca – Single", "single"),
            ("Ressaca - single", "single"),
            ("Ressaca - Sencillo", "single"),
            ("Ressaca - EP", "full"),
            ("LOBOS (Deluxe)", "full"),
            ("Single Life", "full"),
            ("Anti-Herói", "full"),
            ("", "full"),
        ],
    )
    def test_apple_release_kind(self, nome, esperado):
        assert utils.apple_release_kind(nome) == esperado

    async def test_single_aceita_o_single_da_apple(self):
        s = _Sessao(search=corpo(faixa_arte("Jão", "Ressaca - Single", "Ressaca", "single")))
        url, _, motivo = await buscar(
            s, artist="Jão", album="Ressaca", track_title="Ressaca", release_kind="single"
        )
        assert url is not None, motivo

    async def test_single_nao_recebe_a_capa_do_album(self):
        # O mesmo nome de álbum e faixa, mas na Apple é o álbum completo.
        s = _Sessao(search=corpo(faixa_arte("Jão", "Ressaca", "Ressaca", "album")))
        url, _, motivo = await buscar(
            s, artist="Jão", album="Ressaca", track_title="Ressaca", release_kind="single"
        )
        assert url is None
        assert "tipo de lançamento incompatível (pedido=single, Apple=álbum/EP" in motivo

    async def test_album_nao_recebe_a_capa_do_single(self):
        s = _Sessao(search=corpo(faixa_arte("Jão", "Disco - Single", "Musica", "single")))
        url, _, motivo = await buscar(
            s, artist="Jão", album="Disco", track_title="Musica", release_kind="full"
        )
        assert url is None
        assert "tipo de lançamento incompatível (pedido=álbum/EP, Apple=single" in motivo

    async def test_album_aceita_ep_e_album_da_apple(self):
        for nome in ("Disco", "Disco - EP"):
            s = _Sessao(search=corpo(faixa_arte("Jão", nome, "Musica", "ok")))
            url, _, motivo = await buscar(
                s, artist="Jão", album="Disco", track_title="Musica", release_kind="full"
            )
            assert url is not None, (nome, motivo)

    async def test_sem_tipo_nada_muda(self):
        for nome in ("Disco", "Disco - Single"):
            s = _Sessao(search=corpo(faixa_arte("Jão", nome, "Musica", "ok")))
            url, _, _ = await buscar(
                s, artist="Jão", album="Disco", track_title="Musica"
            )
            assert url is not None

    async def test_escolhe_o_single_entre_single_e_album_da_mesma_faixa(self):
        s = _Sessao(
            search=corpo(
                faixa_arte("Jão", "Disco", "Musica", "album"),
                faixa_arte("Jão", "Disco - Single", "Musica", "single"),
            )
        )
        url, _, _ = await buscar(
            s, artist="Jão", album="Disco", track_title="Musica", release_kind="single"
        )
        assert url.startswith("https://single.test/")
        url, _, _ = await buscar(
            s, artist="Jão", album="Disco", track_title="Musica", release_kind="full"
        )
        assert url.startswith("https://album.test/")

    async def test_busca_de_album_tambem_respeita_o_tipo(self):
        s = _Sessao(search=corpo(colecao("Jão", "Disco - Single")))
        url, _, motivo = await buscar(
            s, artist="Jão", album="Disco", release_kind="full"
        )
        assert url is None and "tipo de lançamento incompatível" in motivo

    async def test_etapa_do_album_ignora_single_quando_o_pedido_e_album(self):
        s = _SessaoRoteada(
            album=corpo(
                album_apple(5, "Jão", "Disco - Single"),
                album_apple(6, "Jão", "Disco"),
            ),
            tracks={
                5: corpo(faixa_arte("Jão", "Disco - Single", "Musica", "single")),
                6: corpo(faixa_arte("Jão", "Disco", "Outra", "x")),
            },
        )
        url, _, _ = await buscar(
            s, artist="Jão", album="Disco", track_title="Musica", release_kind="full"
        )
        assert url is None
        assert set(s.lookups()) == {6}  # o single nem foi consultado


class TestBuscaProfundaDeAlbum:
    # Na busca por ÁLBUM (retag decide a capa por álbum) a busca principal só
    # enxerga o álbum ao vivo; o de estúdio vem da busca só pelo artista.
    _BASE = {("album", "Jão Anti-Herói"): corpo(album_apple(2, "Jão", _AO_VIVO))}

    async def test_sem_deep_o_comportamento_antigo_e_mantido(self):
        buscas = dict(self._BASE)
        buscas[("album", "Jão")] = corpo(album_apple(1, "Jão", "Anti-Herói"))
        s = _SessaoPorTermo(buscas=buscas)
        url, _, _ = await buscar(s, artist="Jão", album="Anti-Herói")
        assert url is None
        assert all(p.get("term") == "Jão Anti-Herói" for _, p in s.chamadas)

    async def test_deep_acha_o_album_pelo_artista(self):
        buscas = dict(self._BASE)
        buscas[("album", "Jão")] = corpo(
            album_apple(2, "Jão", _AO_VIVO), album_apple(1, "Jão", "Anti-Herói")
        )
        s = _SessaoPorTermo(buscas=buscas)
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", deep_album_search=True
        )
        assert url == "https://album1.test/10000x10000bb.jpg", motivo

    async def test_deep_acha_o_album_pelo_catalogo_do_artista(self):
        buscas = dict(self._BASE)
        buscas[("musicArtist", "Jão")] = corpo(
            {"wrapperType": "artist", "artistId": 99, "artistName": "Jão"}
        )
        s = _SessaoPorTermo(
            buscas=buscas,
            catalogo={
                99: corpo(
                    {"wrapperType": "artist", "artistId": 99, "artistName": "Jão"},
                    album_apple(1, "Jão", "Anti-Herói"),
                )
            },
        )
        url, _, motivo = await buscar(
            s, artist="Jão", album="Anti-Herói", deep_album_search=True
        )
        assert url == "https://album1.test/10000x10000bb.jpg", motivo

    async def test_deep_nao_aceita_o_album_ao_vivo_nem_o_single(self):
        buscas = dict(self._BASE)
        buscas[("album", "Jão")] = corpo(
            album_apple(2, "Jão", _AO_VIVO), album_apple(3, "Jão", "Anti-Herói - Single")
        )
        s = _SessaoPorTermo(buscas=buscas)
        url, _, _ = await buscar(
            s,
            artist="Jão",
            album="Anti-Herói",
            release_kind="full",
            deep_album_search=True,
        )
        assert url is None

    async def test_deep_prefere_a_edicao_identica(self):
        buscas = dict(self._BASE)
        buscas[("album", "Jão")] = corpo(
            album_apple(7, "Jão", "Anti-Herói (Deluxe)"), album_apple(1, "Jão", "Anti-Herói")
        )
        s = _SessaoPorTermo(buscas=buscas)
        url, _, _ = await buscar(
            s, artist="Jão", album="Anti-Herói", deep_album_search=True
        )
        assert url.startswith("https://album1.test/")


# ---------------------------------------------------------------------------
# Versões da mesma música em vários lançamentos (single, álbum, Deluxe,
# coletânea). Casos reais do `tags` com The Weeknd:
#  - "Earned It (...) (From The "Fifty Shades Of Grey" Soundtrack)": a
#    procedência entre parênteses não muda a gravação;
#  - "Take My Breath (Single Version)": a Apple chama de "Take My Breath", e a
#    "(Extended Version)" é OUTRA gravação (quase passava: 0,84 < 0,85);
#  - "Pray For Me" de "The Weeknd" no single "The Weeknd & Kendrick Lamar".
# ---------------------------------------------------------------------------
class TestTituloComJanelaDeProcedenciaEVersao:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            (
                'Earned It (Fifty Shades Of Grey) (From The "Fifty Shades Of Grey" Soundtrack)',
                "earned it (fifty shades of grey)",
            ),
            ("Musica (From Dust)", "musica"),
            ("Take My Breath (Single Version)", "take my breath"),
            ("Musica (Album Version)", "musica"),
            ("Musica (Original Version)", "musica"),
            ("Take My Breath (Extended Version)", "take my breath"),
            ("Musica (Extended Mix)", "musica"),
        ],
    )
    def test_comparavel(self, entrada, esperado):
        assert utils.extrair_titulo_comparavel(entrada) == esperado

    @pytest.mark.parametrize(
        "entrada",
        ["Musica (Radio Edit)", "Musica (Remastered 2011)", "Musica (Live From Paris)"],
    )
    def test_outras_versoes_continuam(self, entrada):
        assert utils.extrair_titulo_comparavel(entrada) == (
            utils.extrair_titulo_completo(entrada)
        )

    def test_extended_e_marcador_de_versao(self):
        assert utils.marcadores_versao("Take My Breath (Extended Version)") == (
            frozenset({"extended"})
        )
        assert utils.marcadores_versao("Take My Breath") == frozenset()


class TestTermoDeBusca:
    @pytest.mark.parametrize(
        "entrada, esperado",
        [
            (
                'Earned It (Fifty Shades Of Grey) (From The "Fifty Shades" Soundtrack)',
                "Earned It",
            ),
            ("Take My Breath (Single Version)", "Take My Breath"),
            ("Musica [Deluxe]", "Musica"),
            ('Titulo "com aspas"', "Titulo com aspas"),
            ("Simples", "Simples"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_termo(self, entrada, esperado):
        assert utils.termo_de_busca(entrada) == esperado


class TestFaixasEquivalentes:
    def test_mesma_gravacao_com_procedencia_e_versao_de_single(self):
        assert utils.faixas_equivalentes(
            'Earned It (Fifty Shades Of Grey) (From The "Fifty Shades Of Grey" Soundtrack)',
            "Earned It (Fifty Shades of Grey)",
        )
        assert utils.faixas_equivalentes(
            "Take My Breath (Single Version)", "Take My Breath"
        )

    def test_versao_estendida_nao_e_a_mesma_gravacao(self):
        assert not utils.faixas_equivalentes(
            "Take My Breath (Single Version)", "Take My Breath (Extended Version)"
        )
        assert not utils.faixas_equivalentes("Take My Breath", "Take My Breath (Instrumental)")

    def test_ao_vivo_so_vale_com_album_ao_vivo(self):
        assert not utils.faixas_equivalentes("VSF", "VSF (Ao Vivo)")
        assert utils.faixas_equivalentes(
            "VSF", "VSF (Ao Vivo)", "Turnê (Ao Vivo)", "Turnê (Ao Vivo)"
        )

    def test_radio_edit_continua_sendo_outra_edicao(self):
        assert not utils.faixas_equivalentes("Musica", "Musica (Radio Edit)")

    def test_titulos_so_de_pontuacao(self):
        assert utils.faixas_equivalentes(":(", ":(")
        assert not utils.faixas_equivalentes(":(", ":)")

    def test_conta_sem_repetir(self):
        pedidas = ["A", "A", "B", "C"]
        achadas = ["A", "B", "Z"]
        assert utils.contar_faixas_em_comum(pedidas, achadas) == 2  # A e B
        assert utils.contar_faixas_em_comum([], achadas) == 0
        assert utils.contar_faixas_em_comum(pedidas, []) == 0


# ---------------------------------------------------------------------------
# Loja Apple de mentira: álbuns (com faixas e lojas onde existem), busca de
# álbum/música/artista e lookup por id, tudo sensível ao país.
# ---------------------------------------------------------------------------
class _LojaApple:
    def __init__(self, albuns):
        # id -> dict(name, artist, tracks, art, stores(opcional), isrc(opcional))
        self.albuns = albuns
        self.chamadas = []

    def _visivel(self, cid, pais):
        lojas = self.albuns[cid].get("stores")
        return lojas is None or pais in lojas

    def _linha_album(self, cid):
        a = self.albuns[cid]
        return {
            "wrapperType": "collection",
            "collectionId": cid,
            "artistName": a["artist"],
            "collectionName": a["name"],
            "artworkUrl100": _arte(a["art"]),
        }

    def _linha_faixa(self, cid, titulo):
        a = self.albuns[cid]
        return {
            "wrapperType": "track",
            "kind": "song",
            "artistName": a["artist"],
            "collectionName": a["name"],
            "trackName": titulo,
            "artworkUrl100": _arte(a["art"]),
        }

    async def get(self, url, **kw):
        params = kw.get("params") or {}
        self.chamadas.append((url, params))
        pais = params.get("country", "br")
        if "/lookup" in url:
            if "isrc=" in url:
                linhas = [
                    self._linha_faixa(cid, t)
                    for cid, a in self.albuns.items()
                    if self._visivel(cid, pais)
                    for t in a["tracks"]
                    if t in a.get("isrc_titles", ())
                ]
                return _Resp({"results": linhas})
            if params.get("entity") == "song" and params.get("id") in self.albuns:
                cid = params["id"]
                if not self._visivel(cid, pais):
                    return _Resp({"results": []})
                return _Resp(
                    {
                        "results": [self._linha_album(cid)]
                        + [self._linha_faixa(cid, t) for t in self.albuns[cid]["tracks"]]
                    }
                )
            return _Resp({"results": []})
        entidade = params.get("entity")
        if entidade == "album":
            return _Resp(
                {
                    "results": [
                        self._linha_album(cid)
                        for cid in self.albuns
                        if self._visivel(cid, pais)
                    ]
                }
            )
        if entidade == "song":
            linhas = [
                self._linha_faixa(cid, t)
                for cid, a in self.albuns.items()
                if self._visivel(cid, pais)
                for t in a["tracks"]
            ]
            return _Resp({"results": linhas[:50]})
        return _Resp({"results": []})

    async def head(self, url, **_kw):
        return _Resp({}, status=200)

    def paises(self):
        return {str(p.get("country")) for _, p in self.chamadas if p.get("country")}


def _album_apple_loja(nome, artista, faixas, arte, **extra):
    return {"name": nome, "artist": artista, "tracks": faixas, "art": arte, **extra}


async def _album_certo(loja, qobuz_album, faixas, **kw):
    return await buscar(
        loja,
        artist="The Weeknd",
        album=qobuz_album,
        album_tracks=faixas,
        deep_album_search=True,
        **kw,
    )


class TestVerificacaoPorListaDeFaixas:
    async def test_earned_it_single_com_procedencia_no_titulo(self):
        loja = _LojaApple(
            {
                11: _album_apple_loja(
                    "Earned It (Fifty Shades of Grey) - Single",
                    "The Weeknd",
                    ["Earned It (Fifty Shades of Grey)"],
                    "single",
                ),
                12: _album_apple_loja(
                    "The Highlights",
                    "The Weeknd",
                    ["Earned It (Fifty Shades Of Grey)"] + [f"Outra {i}" for i in range(17)],
                    "highlights",
                ),
                14: _album_apple_loja(
                    "Earned It (Fifty Shades of Grey) [The Voice Performance] - Single",
                    "Brooke Simpson & Davon Fleming",
                    ["Earned It (Fifty Shades of Grey) [The Voice Performance]"],
                    "voice",
                ),
            }
        )
        url, _, motivo = await _album_certo(
            loja,
            "Earned It (Fifty Shades Of Grey)",
            [
                'Earned It (Fifty Shades Of Grey) (From The "Fifty Shades Of Grey" Soundtrack)'
            ],
            release_kind="single",
        )
        assert url is not None, motivo
        assert url.startswith("https://single.test/")

    async def test_take_my_breath_single_version_nao_pega_a_estendida(self):
        loja = _LojaApple(
            {
                32: _album_apple_loja(
                    "Take My Breath (Extended Version) - Single",
                    "The Weeknd",
                    ["Take My Breath (Extended Version)"],
                    "estendida",
                ),
                31: _album_apple_loja(
                    "Take My Breath - Single", "The Weeknd", ["Take My Breath"], "single"
                ),
                34: _album_apple_loja(
                    "Take My Breath (Instrumental) - Single",
                    "The Weeknd",
                    ["Take My Breath (Instrumental)"],
                    "instrumental",
                ),
            }
        )
        url, _, motivo = await _album_certo(
            loja,
            "Take My Breath",
            ["Take My Breath (Single Version)"],
            release_kind="single",
        )
        assert url is not None, motivo
        assert url.startswith("https://single.test/")

    async def test_pray_for_me_artista_com_participacao(self):
        loja = _LojaApple(
            {
                22: _album_apple_loja("Pray For Me - Single", "Lekan", ["Pray For Me"], "lekan"),
                21: _album_apple_loja(
                    "Pray for Me - Single",
                    "The Weeknd & Kendrick Lamar",
                    ["Pray For Me"],
                    "single",
                ),
                23: _album_apple_loja(
                    "Pray for Me", "The Bellamy Brothers", ["Pray for Me"], "bellamy"
                ),
            }
        )
        url, _, motivo = await _album_certo(
            loja, "Pray For Me", ["Pray For Me"], release_kind="single"
        )
        assert url is not None, motivo
        assert url.startswith("https://single.test/")

    async def test_album_padrao_nao_pega_deluxe_nem_single(self):
        faixas = [f"Musica {i}" for i in range(10)]
        loja = _LojaApple(
            {
                42: _album_apple_loja(
                    "After Hours (Deluxe)",
                    "The Weeknd",
                    faixas + [f"Bonus {i}" for i in range(4)],
                    "deluxe",
                ),
                43: _album_apple_loja(
                    "Musica 3 - Single", "The Weeknd", ["Musica 3"], "single3"
                ),
                41: _album_apple_loja("After Hours", "The Weeknd", faixas, "padrao"),
            }
        )
        url, _, _ = await _album_certo(loja, "After Hours", faixas, release_kind="full")
        assert url.startswith("https://padrao.test/")

    async def test_deluxe_pede_o_deluxe(self):
        faixas = [f"Musica {i}" for i in range(10)]
        extras = faixas + [f"Bonus {i}" for i in range(4)]
        loja = _LojaApple(
            {
                41: _album_apple_loja("After Hours", "The Weeknd", faixas, "padrao"),
                42: _album_apple_loja("After Hours (Deluxe)", "The Weeknd", extras, "deluxe"),
            }
        )
        url, _, _ = await _album_certo(
            loja, "After Hours (Deluxe)", extras, release_kind="full"
        )
        assert url.startswith("https://deluxe.test/")

    async def test_lista_que_nao_confere_e_rejeitada(self):
        loja = _LojaApple(
            {
                1: _album_apple_loja(
                    "Disco", "The Weeknd", ["Outra A", "Outra B", "Outra C"], "errado"
                )
            }
        )
        url, _, motivo = await _album_certo(
            loja, "Disco", ["Musica 1", "Musica 2", "Musica 3"], release_kind="full"
        )
        assert url is None
        assert "faixas do álbum não conferem (0/3" in motivo

    async def test_tolera_uma_faixa_com_titulo_diferente(self):
        # 3 de 4 conferem (Jaccard 3/5 = 0,6): mesma edição, uma faixa
        # escrita de outro jeito.
        loja = _LojaApple(
            {1: _album_apple_loja("Disco", "The Weeknd", ["A", "B", "C", "Zzz"], "ok")}
        )
        url, _, motivo = await _album_certo(
            loja, "Disco", ["A", "B", "C", "D"], release_kind="full"
        )
        assert url is not None, motivo

    async def test_metade_conferindo_nao_basta(self):
        # 2 de 4 (Jaccard 2/6): é outra edição/lançamento.
        loja = _LojaApple(
            {1: _album_apple_loja("Disco", "The Weeknd", ["A", "B", "X", "Y"], "ok")}
        )
        url, _, _ = await _album_certo(
            loja, "Disco", ["A", "B", "C", "D"], release_kind="full"
        )
        assert url is None


class TestLojasExtras:
    def _single_so_na_loja(self, pais):
        return _LojaApple(
            {
                1: _album_apple_loja(
                    "Earned It - Single",
                    "The Weeknd",
                    ["Earned It"],
                    "single",
                    stores={pais},
                ),
                2: _album_apple_loja(
                    "The Highlights",
                    "The Weeknd",
                    ["Earned It"] + [f"Outra {i}" for i in range(9)],
                    "highlights",
                ),
            }
        )

    async def test_sem_fallback_so_consulta_br_e_us(self):
        loja = self._single_so_na_loja("gb")
        url, _, _ = await _album_certo(
            loja, "Earned It", ["Earned It"], release_kind="single"
        )
        assert url is None
        assert loja.paises() == {"br", "us"}

    async def test_com_fallback_acha_o_single_em_outra_loja(self):
        loja = self._single_so_na_loja("gb")
        url, _, motivo = await _album_certo(
            loja,
            "Earned It",
            ["Earned It"],
            release_kind="single",
            fallback_countries=True,
        )
        assert url is not None, motivo
        assert url.startswith("https://single.test/")
        assert {"br", "us", "gb"} <= loja.paises()

    async def test_fallback_so_entra_se_as_principais_nao_resolverem(self):
        loja = self._single_so_na_loja("br")
        url, _, _ = await _album_certo(
            loja,
            "Earned It",
            ["Earned It"],
            release_kind="single",
            fallback_countries=True,
        )
        assert url is not None
        assert loja.paises() == {"br"}  # nem chegou a consultar us nem as extras

    async def test_paises_personalizados(self):
        loja = self._single_so_na_loja("jp")
        url, _, _ = await _album_certo(
            loja, "Earned It", ["Earned It"], release_kind="single", countries=("jp",)
        )
        assert url is not None and loja.paises() == {"jp"}

    async def test_fallback_tambem_na_busca_por_faixa(self):
        loja = self._single_so_na_loja("ca")
        url, _, _ = await buscar(
            loja,
            artist="The Weeknd",
            album="Earned It",
            track_title="Earned It",
            release_kind="single",
            fallback_countries=True,
        )
        assert url is not None and url.startswith("https://single.test/")


class TestIsrcRespeitaTipoEEdicao:
    # O mesmo ISRC aparece no single, no álbum e na coletânea.
    def _loja(self):
        titulos = ("Starboy",)
        return _LojaApple(
            {
                1: _album_apple_loja(
                    "Starboy - Single", "The Weeknd", ["Starboy"], "single",
                    isrc_titles=titulos,
                ),
                2: _album_apple_loja(
                    "Starboy (Deluxe)", "The Weeknd", ["Starboy"], "deluxe",
                    isrc_titles=titulos,
                ),
                3: _album_apple_loja(
                    "Starboy", "The Weeknd", ["Starboy"], "album", isrc_titles=titulos
                ),
            }
        )

    async def test_album_nao_recebe_a_capa_do_single_pelo_isrc(self):
        url, _, _ = await buscar(
            self._loja(),
            isrc="USUG11601",
            artist="The Weeknd",
            album="Starboy",
            track_title="Starboy",
            release_kind="full",
        )
        assert url.startswith("https://album.test/")  # edição idêntica ao pedido

    async def test_single_recebe_o_single_pelo_isrc(self):
        url, _, _ = await buscar(
            self._loja(),
            isrc="USUG11601",
            artist="The Weeknd",
            album="Starboy",
            track_title="Starboy",
            release_kind="single",
        )
        assert url.startswith("https://single.test/")

    async def test_isrc_prefere_a_edicao_de_titulo_identico(self):
        url, _, _ = await buscar(
            self._loja(),
            isrc="USUG11601",
            artist="The Weeknd",
            album="Starboy (Deluxe)",
            track_title="Starboy",
        )
        assert url.startswith("https://deluxe.test/")


class TestTermoLimpoNaBuscaDeMusica:
    async def test_busca_com_o_titulo_sem_parenteses_e_aspas(self):
        loja = _LojaApple(
            {1: _album_apple_loja("Earned It - Single", "The Weeknd", ["Earned It"], "s")}
        )
        await buscar(
            loja,
            artist="The Weeknd",
            album="Earned It",
            track_title='Earned It (Fifty Shades Of Grey) (From The "Fifty Shades" Soundtrack)',
            release_kind="single",
        )
        termos = [p.get("term") for _, p in loja.chamadas if p.get("entity") == "song"]
        assert "The Weeknd Earned It" in termos
        assert all('"' not in t for t in termos if t and "From" not in t)
