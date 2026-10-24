"""Ramo paralelo de download_release(), sem downloads reais."""

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


def album_data(tracks):
    return {
        "streamable": True,
        "title": "Album",
        "url": "album-url",
        "release_date_original": "2024-01-01",
        "artist": {"name": "Artist"},
        "image": {"large": "https://example.test/cover.jpg"},
        "tracks_count": len(tracks),
        "tracks": {"items": tracks},
    }


async def test_download_release_paralelo(monkeypatch, tmp_path, no_http_session):
    tracks = [
        {"id": "t1", "title": "One", "track_number": 1, "media_number": 1},
        {"id": "t2", "title": "Two", "track_number": 2, "media_number": 1},
    ]
    obj = downloader.Download(SimpleNamespace(), "album-1", str(tmp_path), 6)
    obj.settings.max_workers = 2
    obj.client.get_album_meta = lambda item_id: album_data(tracks)

    async def get_album_meta(item_id):
        return album_data(tracks)

    obj.client.get_album_meta = get_album_meta

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    calls = []

    async def fake_process_track(*args, **kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr(obj, "_process_track", fake_process_track)

    result = await obj.download_release()

    assert result is True
    assert len(calls) == 2
    assert all(call["is_parallel"] is True for call in calls)
    assert all(call["position_pool"] is not None for call in calls)


async def test_download_release_retoma_apenas_faixas_pendentes(
    monkeypatch, tmp_path, no_http_session
):
    tracks = [
        {
            "id": "done",
            "title": "Already done",
            "track_number": 1,
            "media_number": 1,
        },
        {
            "id": "pending",
            "title": "Still pending",
            "track_number": 2,
            "media_number": 1,
        },
    ]
    obj = downloader.Download(SimpleNamespace(), "album-resume", str(tmp_path), 6)
    obj.settings.max_workers = 2
    obj.client.get_album_meta = lambda _item_id: album_data(tracks)

    async def get_album_meta(_item_id):
        return album_data(tracks)

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)
    monkeypatch.setattr(
        downloader.postprocess,
        "resumable_track_ids",
        lambda _directory: _return_completed_ids(),
    )
    skip_messages = []
    summaries = []

    def capture_skip(message):
        skip_messages.append((message, downloader.ui._track_index.get()))

    monkeypatch.setattr(downloader.ui, "skip", capture_skip)
    monkeypatch.setattr(
        downloader.ui,
        "summary_downloads",
        lambda *args, **kwargs: summaries.append((args, kwargs)),
    )
    obj.client.get_album_meta = get_album_meta
    processed = []

    async def fake_process_track(idx, track, **kwargs):
        processed.append(track["id"])
        return True

    monkeypatch.setattr(obj, "_process_track", fake_process_track)

    result = await obj.download_release()

    assert result is True
    assert processed == ["pending"]
    assert [entry for entry in skip_messages if "Já existe" in entry[0]] == [
        ("Pulando: Already done (Já existe no dispositivo)", "01/02")
    ]
    assert summaries[0][0] == ("DO ÁLBUM", "Album", 1, 2)
    assert summaries[0][1]["skipped"] == 0
    assert summaries[0][1]["failed"] == 0
    assert summaries[0][1]["existing"] == 1


async def _return_completed_ids():
    return {"done"}


async def test_cancelamento_aguarda_faixas_antes_de_fechar_sessao_http(
    monkeypatch, tmp_path
):
    """A sessão HTTP só fecha depois que as tarefas canceladas terminam."""
    import asyncio

    started = asyncio.Event()
    cleaned = asyncio.Event()
    closed = asyncio.Event()

    class FakeSession:
        async def aclose(self):
            # Se a sessão fechar antes do finally da faixa, há uma corrida.
            assert cleaned.is_set(), "sessão fechada antes da limpeza da faixa"
            closed.set()

    monkeypatch.setattr(downloader, "make_client", lambda **kwargs: FakeSession())
    tracks = [{"id": "t1", "title": "One", "track_number": 1, "media_number": 1}]
    obj = downloader.Download(SimpleNamespace(), "album-1", str(tmp_path), 6)
    obj.settings.max_workers = 1

    async def get_album_meta(item_id):
        return album_data(tracks)

    obj.client.get_album_meta = get_album_meta

    async def get_format(*args, **kwargs):
        return ("flac", True, 16, 44100)

    monkeypatch.setattr(obj, "_get_format", get_format)

    async def blocked_process_track(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return True

    monkeypatch.setattr(obj, "_process_track", blocked_process_track)
    download_task = asyncio.create_task(obj.download_release())
    try:
        await asyncio.wait_for(started.wait(), timeout=3)
        download_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await download_task

        assert cleaned.is_set()
        assert closed.is_set()
    finally:
        if not download_task.done():
            download_task.cancel()
            await asyncio.gather(download_task, return_exceptions=True)
        downloader.abort_event.clear()
