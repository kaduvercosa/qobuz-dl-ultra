"""Testes sem rede para retry seletivo e cooldown HTTP compartilhado."""

import asyncio

import httpx
import pytest

from qobuz_dl import resilient_http as rh


def test_sync_retry_429_then_success(monkeypatch):
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "2"}, request=request)
        return httpx.Response(200, json={"ok": True}, request=request)

    monkeypatch.setattr(rh.time, "sleep", sleeps.append)
    rh._reset_state_for_tests()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = rh.request_sync(client, "GET", "https://api.example.test/data")
    assert response.status_code == 200
    assert len(calls) == 2
    assert len(sleeps) == 1 and sleeps[0] == pytest.approx(2.0, abs=0.01)


def test_sync_does_not_retry_permanent_4xx(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(404, request=request)

    rh._reset_state_for_tests()
    monkeypatch.setattr(rh.time, "sleep", lambda _: pytest.fail("não deve esperar"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = rh.request_sync(client, "GET", "https://api.example.test/missing")
    assert response.status_code == 404
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_async_retry_server_error(monkeypatch):
    calls = []
    sleeps = []

    def handler(request):
        calls.append(request)
        status = 503 if len(calls) == 1 else 200
        return httpx.Response(status, request=request)

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(rh.asyncio, "sleep", fake_sleep)
    rh._reset_state_for_tests()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await rh.request_async(
            client, "GET", "https://api.example.test/data"
        )
    assert response.status_code == 200
    assert len(calls) == 2
    assert len(sleeps) == 1 and sleeps[0] > 0


def test_retry_after_invalid_and_capped():
    assert rh._retry_after({"Retry-After": "n/a"}) is None
    assert rh._retry_after({"Retry-After": "999"}) == rh._MAX_RETRY_AFTER
    assert rh._retry_after({"Retry-After": "0"}) == 0


def test_post_is_not_retried(monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503, request=request)

    rh._reset_state_for_tests()
    monkeypatch.setattr(rh.time, "sleep", lambda _: pytest.fail("não deve esperar"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        response = rh.request_sync(client, "POST", "https://api.example.test/action")
    assert response.status_code == 503
    assert len(calls) == 1


def test_worker_count_uses_configured_parallelism_up_to_global_limit():
    assert rh.bounded_worker_count(1) == 1
    assert rh.bounded_worker_count(8) == 8
    assert rh.bounded_worker_count(100) == 16
    assert rh.bounded_worker_count("invalid") == 1
