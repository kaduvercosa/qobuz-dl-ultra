"""Caminho de sucesso de _process_track() sem download real."""

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


async def test_process_track_sucesso(monkeypatch, tmp_path, no_http_session):
    settings = SimpleNamespace(multiple_disc_track_format="{track_number}")
    client = SimpleNamespace()
    obj = downloader.Download(client, "album", str(tmp_path), 6, settings=settings)

    monkeypatch.setattr(downloader, "is_track_streamable", lambda item: (True, ""))

    async def get_track_url(*args, **kwargs):
        return {"sampling_rate": 44100, "format_id": 6}

    client.get_track_url = get_track_url
    captured = {}

    async def fake_download_and_tag(*args, **kwargs):
        captured.update(kwargs)
        kwargs["letras_out"].update({"situacao": "sucesso"})
        return True

    monkeypatch.setattr(obj, "_download_and_tag", fake_download_and_tag)
    reports = []

    async def report_track(*args, **kwargs):
        reports.append((args, kwargs))

    result = await obj._process_track(
        0,
        {"id": "t1", "track_number": 1, "title": "Track", "media_number": 2},
        dirn=str(tmp_path),
        album_meta={"title": "Album"},
        is_multiple=True,
        is_parallel=True,
        position_pool="pool",
        semaphore=downloader.asyncio.Semaphore(1),
        report_track=report_track,
    )

    assert result is True
    assert captured["is_parallel"] is True
    assert captured["position_pool"] == "pool"
    assert reports[0][0][2] == "ok"
    assert reports[0][1]["letras"] == {"situacao": "sucesso"}
