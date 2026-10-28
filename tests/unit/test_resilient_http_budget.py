"""Orçamento de espera (`http_budget`): pular fonte em cooldown em vez de dormir.

Sem rede: o cliente é um dublê com `.get` que devolve respostas prontas.
"""

import threading

import pytest

from qobuz_dl import resilient_http as rh

pytestmark = pytest.mark.unit

URL = "https://api.example.test/data"


class _Resp:
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}

    def close(self):
        pass

    async def aclose(self):
        pass


class _Client:
    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return self.respostas.pop(0)


class _AsyncClient(_Client):
    async def get(self, url, **kwargs):
        return super().get(url, **kwargs)


def _sem_dormir(monkeypatch):
    dormiu = []
    monkeypatch.setattr(rh.time, "sleep", dormiu.append)
    return dormiu


def test_cooldown_longo_pula_sem_dormir(monkeypatch):
    rh._reset_state_for_tests()
    dormiu = _sem_dormir(monkeypatch)
    cliente = _Client(_Resp(429, {"Retry-After": "60"}), _Resp(200))

    with pytest.raises(rh.HttpBudgetExceeded) as erro:
        with rh.http_budget(15, max_cooldown_wait=5):
            rh.request_sync(cliente, "GET", URL)

    assert erro.value.reason == "cooldown"
    assert dormiu == []
    assert cliente.calls == 1  # não insistiu


def test_cooldown_curto_ainda_e_esperado(monkeypatch):
    rh._reset_state_for_tests()
    dormiu = _sem_dormir(monkeypatch)
    cliente = _Client(_Resp(429, {"Retry-After": "2"}), _Resp(200))

    with rh.http_budget(15, max_cooldown_wait=5):
        resposta = rh.request_sync(cliente, "GET", URL)

    assert resposta.status_code == 200
    assert len(dormiu) == 1 and dormiu[0] == pytest.approx(2.0, abs=0.01)


def test_sem_orcamento_continua_esperando_o_cooldown_inteiro(monkeypatch):
    rh._reset_state_for_tests()
    dormiu = _sem_dormir(monkeypatch)
    cliente = _Client(_Resp(429, {"Retry-After": "60"}), _Resp(200))

    resposta = rh.request_sync(cliente, "GET", URL)

    assert resposta.status_code == 200
    assert len(dormiu) == 1 and dormiu[0] > 5  # comportamento de sempre


def test_prazo_esgotado_levanta_antes_de_tentar(monkeypatch):
    rh._reset_state_for_tests()
    _sem_dormir(monkeypatch)
    cliente = _Client(_Resp(200))

    with pytest.raises(rh.HttpBudgetExceeded) as erro:
        with rh.http_budget(0):
            rh.request_sync(cliente, "GET", URL)

    assert erro.value.reason == "tempo"
    assert cliente.calls == 0


def test_cooldown_publicado_por_outra_faixa_e_pulado_na_hora(monkeypatch):
    rh._reset_state_for_tests()
    dormiu = _sem_dormir(monkeypatch)
    rh._publish_cooldown(rh._host(URL), 60.0)  # outra thread levou um 429
    cliente = _Client(_Resp(200))

    with pytest.raises(rh.HttpBudgetExceeded):
        with rh.http_budget(15, max_cooldown_wait=5):
            rh.request_sync(cliente, "GET", URL)

    assert cliente.calls == 0 and dormiu == []


def test_orcamento_nao_vaza_para_fora_do_with_nem_para_outras_threads():
    vistos = []
    with rh.http_budget(10):
        thread = threading.Thread(target=lambda: vistos.append(rh._budget.get()))
        thread.start()
        thread.join()
    assert vistos == [None]  # thread nova começa sem orçamento
    assert rh._budget.get() is None


def test_orcamentos_aninhados_valem_o_mais_restrito():
    with rh.http_budget(100, max_cooldown_wait=9):
        externo = rh._budget.get()
        with rh.http_budget(5, max_cooldown_wait=20):
            interno = rh._budget.get()
        assert rh._budget.get() is externo
    assert interno.deadline <= externo.deadline
    assert interno.max_cooldown_wait == 9


@pytest.mark.asyncio
async def test_async_cooldown_longo_pula_sem_dormir(monkeypatch):
    rh._reset_state_for_tests()
    dormiu = []

    async def falso_sleep(segundos):
        dormiu.append(segundos)

    monkeypatch.setattr(rh.asyncio, "sleep", falso_sleep)
    cliente = _AsyncClient(_Resp(429, {"Retry-After": "60"}), _Resp(200))

    with pytest.raises(rh.HttpBudgetExceeded):
        with rh.http_budget(15, max_cooldown_wait=5):
            await rh.request_async(cliente, "GET", URL)

    assert dormiu == []
    assert cliente.calls == 1
