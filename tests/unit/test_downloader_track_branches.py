"""Branches de _process_track sem rede real."""

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


def _obj(tmp_path):
    settings = SimpleNamespace(multiple_disc_track_format="{track_number}")
    return downloader.Download(
        SimpleNamespace(), "album", str(tmp_path), 6, settings=settings
    )


async def test_process_track_abortado(monkeypatch, tmp_path, no_http_session):
    obj = _obj(tmp_path)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: True)
    report = []

    async def report_track(*args, **kwargs):
        report.append((args, kwargs))

    result = await obj._process_track(
        0,
        {"id": "t1", "track_number": 1},
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=downloader.asyncio.Semaphore(1),
        report_track=report_track,
    )

    assert result is False
    assert report == []


async def test_process_track_nao_streamable(monkeypatch, tmp_path, no_http_session):
    obj = _obj(tmp_path)
    monkeypatch.setattr(
        downloader,
        "is_track_streamable",
        lambda item: (False, "sem licença"),
    )
    placeholders = []
    reports = []
    monkeypatch.setattr(
        downloader,
        "create_missing_placeholder",
        lambda item, directory, reason: placeholders.append((item, directory, reason)),
    )

    async def report_track(*args, **kwargs):
        reports.append((args, kwargs))

    result = await obj._process_track(
        0,
        {"id": "t1", "track_number": 1, "title": "Track"},
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=downloader.asyncio.Semaphore(1),
        report_track=report_track,
    )

    assert result == "skipped"
    assert placeholders[0][2] == "sem licença"
    assert reports[0][0][2:4] == ("pulada", "sem licença")


async def test_process_track_erro_de_api(monkeypatch, tmp_path, no_http_session):
    obj = _obj(tmp_path)
    placeholders = []
    reports = []
    monkeypatch.setattr(
        downloader,
        "is_track_streamable",
        lambda item: (True, ""),
    )
    monkeypatch.setattr(
        downloader,
        "create_missing_placeholder",
        lambda item, directory, reason: placeholders.append(reason),
    )

    async def get_track_url(*args, **kwargs):
        raise RuntimeError("API indisponível")

    obj.client.get_track_url = get_track_url

    async def report_track(*args, **kwargs):
        reports.append((args, kwargs))

    result = await obj._process_track(
        0,
        {"id": "t1", "track_number": 1, "title": "Track"},
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=downloader.asyncio.Semaphore(1),
        report_track=report_track,
    )

    assert result is False
    assert "Erro de API" in placeholders[0]
    assert reports[0][0][2] == "falha"


async def test_process_track_apenas_sample(monkeypatch, tmp_path, no_http_session):
    obj = _obj(tmp_path)
    placeholders = []
    reports = []
    monkeypatch.setattr(
        downloader,
        "is_track_streamable",
        lambda item: (True, ""),
    )
    monkeypatch.setattr(
        downloader,
        "create_missing_placeholder",
        lambda item, directory, reason: placeholders.append(reason),
    )
    obj.client.get_track_url = lambda *args, **kwargs: None

    async def get_track_url(*args, **kwargs):
        return {"sample": True}

    obj.client.get_track_url = get_track_url

    async def report_track(*args, **kwargs):
        reports.append((args, kwargs))

    result = await obj._process_track(
        0,
        {"id": "t1", "track_number": 1, "title": "Demo"},
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=downloader.asyncio.Semaphore(1),
        report_track=report_track,
    )

    assert result == "skipped"
    assert "amostra" in placeholders[0]
    assert reports[0][0][2] == "pulada"
