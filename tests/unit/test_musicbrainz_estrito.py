"""Correspondência estrita do MusicBrainz: título, versão, álbum e artista."""

import asyncio

import pytest

import qobuz_dl.musicbrainz as mb

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def limpa_estado_global():
    mb._MB_MATCH_CACHE.clear()
    mb._MB_SEM = None
    yield
    mb._MB_MATCH_CACHE.clear()
    mb._MB_SEM = None


def _gravacao(**sobrescritas):
    base = {
        "id": "rec-1",
        "title": "Song",
        "disambiguation": "",
        "artist-credit": [
            {"name": "Artist", "joinphrase": "", "artist": {"id": "art-1", "name": "Artist"}}
        ],
        "releases": [{"id": "rel-1", "title": "Album", "barcode": "123456789012"}],
    }
    base.update(sobrescritas)
    return base


def _args(**sobrescritas):
    base = {
        "title": "Song",
        "version": "",
        "album": "Album",
        "album_version": "",
        "upc": "123456789012",
        "artists": ["Artist"],
        "artist_text": "Artist",
    }
    base.update(sobrescritas)
    return base


class TestEscolherCorrespondencia:
    def test_tudo_confere_devolve_ids(self):
        r = mb.escolher_correspondencia([_gravacao()], **_args())

        assert r == {"track": "rec-1", "album": "rel-1", "artists": ["art-1"]}

    def test_ignora_acentos_maiusculas_e_pontuacao(self):
        grav = _gravacao(title="SONG!")
        r = mb.escolher_correspondencia([grav], **_args(title="sóng"))

        assert r is not None

    def test_titulo_diferente_nao_confere(self):
        assert mb.escolher_correspondencia([_gravacao()], **_args(title="Outra")) is None

    def test_versao_so_no_qobuz_nao_confere(self):
        r = mb.escolher_correspondencia([_gravacao()], **_args(version="Remastered 2011"))

        assert r is None

    def test_versao_so_no_musicbrainz_nao_confere(self):
        grav = _gravacao(title="Song (Remastered 2011)")

        assert mb.escolher_correspondencia([grav], **_args()) is None

    def test_versao_confere_pelo_titulo_ou_pela_desambiguacao(self):
        no_titulo = _gravacao(title="Song (Remastered 2011)")
        na_desamb = _gravacao(disambiguation="Remastered 2011")

        assert mb.escolher_correspondencia(
            [no_titulo], **_args(version="Remastered 2011")
        )
        assert mb.escolher_correspondencia(
            [na_desamb], **_args(version="Remastered 2011")
        )

    def test_album_diferente_nao_confere(self):
        grav = _gravacao(releases=[{"id": "rel-9", "title": "Greatest Hits"}])

        assert mb.escolher_correspondencia([grav], **_args()) is None

    def test_versao_do_album_precisa_conferir(self):
        grav = _gravacao(releases=[{"id": "rel-1", "title": "Album", "barcode": "123456789012"}])
        deluxe = _gravacao(releases=[{"id": "rel-2", "title": "Album (Deluxe)", "barcode": "123456789012"}])

        assert mb.escolher_correspondencia([grav], **_args(album_version="Deluxe")) is None
        r = mb.escolher_correspondencia([deluxe], **_args(album_version="Deluxe"))
        assert r["album"] == "rel-2"

    def test_escolhe_o_lancamento_certo_entre_varios(self):
        grav = _gravacao(
            releases=[
                {"id": "rel-comp", "title": "Hits 2020"},
                {"id": "rel-1", "title": "Album", "barcode": "123456789012"},
            ]
        )

        assert mb.escolher_correspondencia([grav], **_args())["album"] == "rel-1"

    def test_artista_diferente_nao_confere(self):
        r = mb.escolher_correspondencia(
            [_gravacao()], **_args(artists=["Outro"], artist_text="Outro")
        )

        assert r is None

    def test_artistas_com_participacao_conferem_mesmo_com_separador_diferente(self):
        grav = _gravacao(
            **{
                "artist-credit": [
                    {"name": "Drake", "joinphrase": " feat. ", "artist": {"id": "a1"}},
                    {"name": "Rihanna", "joinphrase": "", "artist": {"id": "a2"}},
                ]
            }
        )

        r = mb.escolher_correspondencia(
            [grav], **_args(artists=["Drake", "Rihanna"], artist_text="Drake")
        )

        assert r["artists"] == ["a1", "a2"]

    def test_artista_faltando_de_um_dos_lados_nao_confere(self):
        grav = _gravacao(
            **{
                "artist-credit": [
                    {"name": "Drake", "joinphrase": " feat. ", "artist": {"id": "a1"}},
                    {"name": "Rihanna", "joinphrase": "", "artist": {"id": "a2"}},
                ]
            }
        )

        assert mb.escolher_correspondencia(
            [grav], **_args(artists=["Drake"], artist_text="Drake")
        ) is None

    def test_pula_gravacao_ruim_e_usa_a_seguinte(self):
        r = mb.escolher_correspondencia(
            [_gravacao(id="ruim", title="Outra"), _gravacao(id="boa")], **_args()
        )

        assert r["track"] == "boa"

    def test_entradas_invalidas_nao_quebram(self):
        assert mb.escolher_correspondencia(None, **_args()) is None
        assert mb.escolher_correspondencia(["x", {}, {"id": "so-id"}], **_args()) is None


class _Resposta:
    def __init__(self, dados):
        self._dados = dados

    def raise_for_status(self):
        pass

    def json(self):
        return self._dados


class _Sessao:
    def __init__(self, dados=None, erro=None):
        self.dados = dados
        self.erro = erro
        self.chamadas = 0

    async def get(self, url, params=None):
        self.chamadas += 1
        if self.erro:
            raise self.erro
        return _Resposta(self.dados)


@pytest.fixture
def sem_espera(monkeypatch):
    async def _rapido(_):
        return None

    monkeypatch.setattr(mb.asyncio, "sleep", _rapido)


def _chamar(sessao, **sobrescritas):
    kwargs = {
        "isrc": "usabc1234567",
        "title": "Song",
        "version": "",
        "album": "Album",
        "album_version": "",
        "upc": "123456789012",
        "artists": ["Artist"],
        "artist_text": "Artist",
        "session": sessao,
    }
    kwargs.update(sobrescritas)
    return asyncio.run(mb.lookup_matching(**kwargs))


class TestLookupMatching:
    def test_devolve_ids_quando_tudo_confere(self, sem_espera):
        sessao = _Sessao({"recordings": [_gravacao()]})

        assert _chamar(sessao) == {"track": "rec-1", "album": "rel-1", "artists": ["art-1"]}

    def test_sem_correspondencia_devolve_none(self, sem_espera):
        sessao = _Sessao({"recordings": [_gravacao(title="Outra")]})

        assert _chamar(sessao) is None

    def test_dados_obrigatorios_ausentes_nao_fazem_requisicao(self, sem_espera):
        sessao = _Sessao({"recordings": [_gravacao()]})

        assert _chamar(sessao, isrc="") is None
        assert _chamar(sessao, album="") is None
        assert _chamar(sessao, artists=[], artist_text="") is None
        assert sessao.chamadas == 0

    def test_resultado_vai_para_o_cache(self, sem_espera):
        sessao = _Sessao({"recordings": [_gravacao()]})

        _chamar(sessao)
        _chamar(sessao)

        assert sessao.chamadas == 1

    def test_falha_de_rede_devolve_none_e_nao_e_cacheada(self, sem_espera):
        ruim = _Sessao(erro=RuntimeError("sem rede"))
        assert _chamar(ruim) is None

        boa = _Sessao({"recordings": [_gravacao()]})
        assert _chamar(boa) is not None


def test_edicao_sem_barcode_nao_inventada():
    r = mb.escolher_correspondencia([_gravacao()], **_args(upc=""))
    assert r["track"] == "rec-1"
    assert r["album"] is None


def test_edicoes_ambiguas_omitem_album():
    grav = _gravacao(releases=[
        {"id": "r1", "title": "Album", "barcode": "123456789012"},
        {"id": "r2", "title": "Album", "barcode": "123456789012"},
    ])
    assert mb.escolher_correspondencia([grav], **_args())["album"] is None


def test_gravacoes_ambiguas_rejeitadas():
    assert mb.escolher_correspondencia(
        [_gravacao(), _gravacao(id="rec-2")], **_args()) is None


def test_duracao_divergente_rejeitada():
    assert mb.escolher_correspondencia(
        [_gravacao(length=200000)], **_args(duration=148)) is None


def test_live_na_desambiguacao_rejeitado():
    assert mb.escolher_correspondencia(
        [_gravacao(disambiguation="live")], **_args()) is None


def test_nome_estruturado_nao_dividido():
    assert mb._conjunto_artistas(["Earth, Wind & Fire"]) != mb._conjunto_artistas(
        ["Earth", "Wind", "Fire"])


def test_lista_estruturada_prevalece_sobre_texto():
    creditos = [{"name": "Artist", "id": "a", "join": ""}]
    assert not mb._artista_confere(["Outro"], "Artist", creditos)
