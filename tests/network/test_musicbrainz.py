"""Testa qobuz_dl/musicbrainz.py: lookup por ISRC contra a API pública do
MusicBrainz, via pytest-httpx (nunca toca rede real).

POR QUE ESTE ARQUIVO EXISTE
---------------------------
Lendo o módulo pra escrever estes testes, dois bugs reais apareceram e já
foram corrigidos em musicbrainz.py (ver comentários lá):

1. O throttle de 1 req/s (`await asyncio.sleep(1.0)`) ficava depois do
   bloco try/finally, um trecho que o `return` dentro do try tornava
   inalcançável em qualquer caminho normal -- o throttle nunca rodava de
   verdade. `test_throttle_e_respeitado_apos_requisicao` trava isso.
2. Uma falha de rede/HTTP (timeout, 500) era cacheada em `_MB_CACHE` do
   mesmo jeito que um "não encontrado" de verdade -- um erro transitório
   virava permanente pro resto da sessão. `test_erro_http_nao_e_cacheado`
   e `test_excecao_generica_nao_e_cacheada` travam isso.

ISOLAMENTO DO CACHE GLOBAL
---------------------------
`_MB_CACHE` e `_MB_SEM` são estado de módulo (intencional -- servem pra
sessão inteira de download). Isso significa que testes que rodam em
qualquer ordem podem vazar cache um pro outro, e o Semaphore pode ficar
preso a um event loop de um teste anterior. O fixture `mb` abaixo garante
os dois limpos antes E depois de cada teste.
"""

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from qobuz_dl import musicbrainz

# Capturado aqui, na importação do módulo -- ANTES de qualquer fixture
# rodar -- porque sem_throttle_de_verdade (autouse) substitui
# asyncio.sleep globalmente (é o mesmo objeto módulo em todo o processo,
# não uma cópia local de musicbrainz.py). TestCachePreenchidoEnquanto...
# usa isso pra ceder a vez de verdade pra outra task, sem cair no mock.
_sleep_de_verdade = asyncio.sleep

URL_RECORDING = f"{musicbrainz._MB_BASE}/recording/"


@pytest.fixture(autouse=True)
def mb():
    """Reseta o cache global e o semaphore de módulo antes/depois de cada
    teste -- sem isso, um teste que popula _MB_CACHE vaza pro próximo, e
    reusar o Semaphore entre event loops de testes diferentes explode."""
    musicbrainz._MB_CACHE.clear()
    musicbrainz._MB_SEM = None
    yield
    musicbrainz._MB_CACHE.clear()
    musicbrainz._MB_SEM = None


@pytest.fixture(autouse=True)
def sem_throttle_de_verdade(monkeypatch):
    """Substitui asyncio.sleep por um mock instantâneo -- sem isso, cada
    teste que faz uma requisição de verdade esperaria 1s real (ver
    test_throttle_e_respeitado_apos_requisicao, que precisa inspecionar
    a CHAMADA ao sleep, não sofrer o tempo dela)."""
    mock_sleep = AsyncMock()
    monkeypatch.setattr(musicbrainz.asyncio, "sleep", mock_sleep)
    return mock_sleep


def _resposta_com_recording(**overrides):
    base = {
        "recordings": [
            {
                "id": "track-mbid-123",
                "releases": [{"id": "album-mbid-456"}],
                "artist-credit": [
                    {"artist": {"id": "artist-mbid-789"}},
                ],
            }
        ]
    }
    base.update(overrides)
    return base


class TestSessaoExterna:
    async def test_sessao_passada_por_fora_nao_e_fechada_pela_funcao(self, httpx_mock):
        """own_session=False (sessão veio de fora) -- só quem CRIOU o
        client é responsável por fechá-lo. Até aqui só havia teste do
        caminho own_session=True (sem passar `session`)."""
        httpx_mock.add_response(json=_resposta_com_recording())

        async with httpx.AsyncClient() as client:
            resultado = await musicbrainz.lookup_by_isrc("GBAYE0000077", session=client)
            assert resultado == ("track-mbid-123", "album-mbid-456", "artist-mbid-789")
            assert client.is_closed is False


class TestEntradaInvalida:
    async def test_isrc_vazio_nao_faz_requisicao(self, httpx_mock):
        # Nenhum httpx_mock.add_response() registrado: se o código tentar
        # QUALQUER requisição, pytest-httpx falha o teste sozinho.
        resultado = await musicbrainz.lookup_by_isrc("")
        assert resultado == (None, None, None)

    async def test_isrc_none_nao_faz_requisicao(self, httpx_mock):
        resultado = await musicbrainz.lookup_by_isrc(None)
        assert resultado == (None, None, None)


class TestNormalizacaoDeIsrc:
    async def test_isrc_minusculo_e_com_espaco_e_normalizado(self, httpx_mock):
        # A normalização (`.strip().upper()`) tem que acontecer ANTES de
        # decidir a chave de cache/URL -- senão "gbaye0000001" e
        # " GBAYE0000001 " virariam duas entradas de cache diferentes
        # pro mesmo ISRC.
        httpx_mock.add_response(
            url=httpx.URL(URL_RECORDING).copy_merge_params(
                {"query": "isrc:GBAYE0000001", "fmt": "json", "limit": "1"}
            ),
            json=_resposta_com_recording(),
        )
        resultado = await musicbrainz.lookup_by_isrc("  gbaye0000001  ")
        assert resultado == ("track-mbid-123", "album-mbid-456", "artist-mbid-789")


class TestCache:
    async def test_segunda_chamada_usa_cache_sem_nova_requisicao(self, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(URL_RECORDING).copy_merge_params(
                {"query": "isrc:GBAYE0000001", "fmt": "json", "limit": "1"}
            ),
            json=_resposta_com_recording(),
        )

        primeira = await musicbrainz.lookup_by_isrc("GBAYE0000001")
        # Se a segunda chamada tentasse uma requisição nova, pytest-httpx
        # falharia por não ter uma segunda resposta registrada.
        segunda = await musicbrainz.lookup_by_isrc("GBAYE0000001")

        assert (
            primeira
            == segunda
            == (
                "track-mbid-123",
                "album-mbid-456",
                "artist-mbid-789",
            )
        )

    async def test_sem_recordings_tambem_e_cacheado(self, httpx_mock):
        httpx_mock.add_response(
            url=httpx.URL(URL_RECORDING).copy_merge_params(
                {"query": "isrc:GBAYE0000002", "fmt": "json", "limit": "1"}
            ),
            json={"recordings": []},
        )

        primeira = await musicbrainz.lookup_by_isrc("GBAYE0000002")
        segunda = await musicbrainz.lookup_by_isrc("GBAYE0000002")

        assert primeira == segunda == (None, None, None)


class TestExtracaoDosCampos:
    async def test_sem_releases_album_mbid_fica_none(self, httpx_mock):
        httpx_mock.add_response(
            json=_resposta_com_recording(
                recordings=[
                    {
                        "id": "track-mbid-x",
                        "releases": [],
                        "artist-credit": [{"artist": {"id": "artist-mbid-x"}}],
                    }
                ]
            )
        )
        resultado = await musicbrainz.lookup_by_isrc("GBAYE0000003")
        assert resultado == ("track-mbid-x", None, "artist-mbid-x")

    async def test_credito_com_item_nao_dict_e_ignorado(self, httpx_mock):
        # artist-credit da API real do MusicBrainz pode misturar objetos
        # de artista com fragmentos de texto solto (join phrases) -- o
        # código já se defende disso com `isinstance(credit, dict)`; este
        # teste garante que a defesa continua funcionando e pega o
        # primeiro item que É de fato um artista.
        httpx_mock.add_response(
            json=_resposta_com_recording(
                recordings=[
                    {
                        "id": "track-mbid-y",
                        "releases": [{"id": "album-mbid-y"}],
                        "artist-credit": [
                            "texto solto que nao e um artista",
                            {"artist": {"id": "artist-mbid-y"}},
                        ],
                    }
                ]
            )
        )
        resultado = await musicbrainz.lookup_by_isrc("GBAYE0000004")
        assert resultado == ("track-mbid-y", "album-mbid-y", "artist-mbid-y")

    async def test_sem_nenhum_artist_credit_valido_artist_mbid_fica_none(
        self, httpx_mock
    ):
        httpx_mock.add_response(
            json=_resposta_com_recording(
                recordings=[
                    {
                        "id": "track-mbid-z",
                        "releases": [{"id": "album-mbid-z"}],
                        "artist-credit": ["so texto solto, sem nenhum dict"],
                    }
                ]
            )
        )
        resultado = await musicbrainz.lookup_by_isrc("GBAYE0000005")
        assert resultado == ("track-mbid-z", "album-mbid-z", None)


class TestFalhasNaoSaoCacheadasPermanentemente:
    """Regressão do Bug 2: erro transitório não pode virar 'sem match'
    permanente -- a próxima chamada com o MESMO isrc precisa tentar de
    novo, não devolver direto do cache."""

    async def test_erro_http_nao_e_cacheado(self, httpx_mock):
        httpx_mock.add_response(status_code=503)
        httpx_mock.add_response(json=_resposta_com_recording())

        primeira = await musicbrainz.lookup_by_isrc("GBAYE0000006")
        assert primeira == (None, None, None)

        # Se o 503 tivesse sido cacheado, esta segunda chamada devolveria
        # (None, None, None) direto do cache, sem nem tentar a segunda
        # resposta registrada -- e pytest-httpx sobraria uma resposta não
        # usada no fim do teste (falha automática de "responses not used").
        segunda = await musicbrainz.lookup_by_isrc("GBAYE0000006")
        assert segunda == ("track-mbid-123", "album-mbid-456", "artist-mbid-789")

    async def test_excecao_generica_nao_e_cacheada(self, httpx_mock):
        httpx_mock.add_exception(httpx.ConnectTimeout("timeout simulado"))
        httpx_mock.add_response(json=_resposta_com_recording())

        primeira = await musicbrainz.lookup_by_isrc("GBAYE0000007")
        assert primeira == (None, None, None)

        segunda = await musicbrainz.lookup_by_isrc("GBAYE0000007")
        assert segunda == ("track-mbid-123", "album-mbid-456", "artist-mbid-789")


class TestThrottle:
    async def test_throttle_e_respeitado_apos_requisicao(
        self, httpx_mock, sem_throttle_de_verdade
    ):
        # Regressão do Bug 1: o sleep(1.0) tem que ser chamado de verdade
        # depois de UMA requisição bem-sucedida -- antes ele nunca era
        # alcançado.
        httpx_mock.add_response(json=_resposta_com_recording())

        await musicbrainz.lookup_by_isrc("GBAYE0000008")

        sem_throttle_de_verdade.assert_awaited_once_with(1.0)

    async def test_cache_hit_nao_dispara_throttle(
        self, httpx_mock, sem_throttle_de_verdade
    ):
        # Um hit de cache não faz requisição nenhuma -- não faz sentido
        # (nem deveria) throttlar algo que não chamou a rede.
        httpx_mock.add_response(json=_resposta_com_recording())

        await musicbrainz.lookup_by_isrc("GBAYE0000009")
        sem_throttle_de_verdade.reset_mock()

        await musicbrainz.lookup_by_isrc("GBAYE0000009")
        sem_throttle_de_verdade.assert_not_awaited()


class TestCachePreenchidoEnquantoEsperaSemaphore:
    async def test_segunda_checagem_apos_semaphore_evita_requisicao(self, httpx_mock):
        """Cobre a re-checagem de _MB_CACHE feita DEPOIS de conseguir o
        semaphore (double-checked locking). Segura o slot antes da
        lookup, deixa ela travar esperando, popula o cache "por fora"
        (simulando outra task que terminou enquanto esperávamos) e só
        então libera o semaphore. Sem essa segunda checagem, o código
        cairia direto pra fazer uma requisição de verdade -- e como
        nenhum httpx_mock.add_response() foi registrado aqui, isso faria
        o teste falhar sozinho.
        """
        sem = musicbrainz._get_sem()
        await sem.acquire()

        tarefa = asyncio.create_task(musicbrainz.lookup_by_isrc("GBAYE0000099"))
        await _sleep_de_verdade(0)

        musicbrainz._MB_CACHE["GBAYE0000099"] = ("t-race", "a-race", "ar-race")
        sem.release()

        resultado = await tarefa
        assert resultado == ("t-race", "a-race", "ar-race")
