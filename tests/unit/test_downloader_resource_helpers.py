"""Testes de recursos e funções auxiliares do downloader."""

from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


@pytest.fixture
def no_http_session(monkeypatch):
    class FakeSession:
        async def aclose(self):
            return None

    monkeypatch.setattr(
        downloader.httpx,
        "AsyncClient",
        lambda *args, **kwargs: FakeSession(),
    )


def test_get_dir_lock_reutiliza_lock():
    downloader._dir_locks.pop("/tmp/qobuz-test", None)
    first = downloader._get_dir_lock("/tmp/qobuz-test")
    second = downloader._get_dir_lock("/tmp/qobuz-test")
    assert first is second


async def test_close_session_sem_lyrics_engine(tmp_path, no_http_session):
    obj = downloader.Download(SimpleNamespace(), "track", str(tmp_path), 6)
    session = obj.http_session
    await obj.close_session()
    assert session is not None


async def test_close_session_fecha_lyrics_engine(
    monkeypatch, tmp_path, no_http_session
):
    class Engine:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    obj = downloader.Download(SimpleNamespace(), "track", str(tmp_path), 6)
    engine = Engine()
    obj.lyrics_engine = engine

    await obj.close_session()

    assert engine.closed is True


async def test_close_session_ignora_falha_da_sessao(tmp_path, no_http_session):
    obj = downloader.Download(SimpleNamespace(), "track", str(tmp_path), 6)

    class BrokenSession:
        async def aclose(self):
            raise RuntimeError("close failed")

    obj.http_session = BrokenSession()
    await obj.close_session()
