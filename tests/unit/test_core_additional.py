"""Testes adicionais de caminhos de core.py ainda não cobertos."""

import os
from types import SimpleNamespace

import httpx
import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _noop_sleep(_seconds):
    return None


async def test_download_from_id_http_500_conta_falha(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=0,
        settings=SimpleNamespace(pl_failed=0, pl_skipped=0),
    )

    async def handle_download_id(*args, **kwargs):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise httpx.HTTPStatusError(
                "500",
                request=SimpleNamespace(),
                response=SimpleNamespace(status_code=500),
            )

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)

    result = await core.QobuzDL.download_from_id(
        app,
        "1",
        is_playlist=True,
    )

    assert result is False
    assert app.settings.pl_failed == 1


async def test_download_from_id_request_error_conta_falha(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=0,
        settings=SimpleNamespace(pl_failed=0, pl_skipped=0),
    )

    async def handle_download_id(*args, **kwargs):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise httpx.RequestError("offline")

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)

    result = await core.QobuzDL.download_from_id(
        app,
        "1",
        is_playlist=True,
    )

    assert result is False
    assert app.settings.pl_failed == 1


async def test_mark_url_done_ignora_arquivo_inexistente(tmp_path):
    app = SimpleNamespace()
    missing = tmp_path / "missing.txt"

    core.QobuzDL.mark_url_done_in_file(
        app,
        str(missing),
        "https://play.qobuz.com/track/1",
    )

    assert not missing.exists()


async def test_mark_url_done_trata_erro_de_escrita(monkeypatch, tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("https://play.qobuz.com/track/1\n", encoding="utf-8")

    real_open = open

    def failing_open(filename, *args, **kwargs):
        if "w" in args or kwargs.get("mode") == "w":
            raise OSError("read-only")
        return real_open(filename, *args, **kwargs)

    monkeypatch.setattr(core, "open", failing_open)

    core.QobuzDL.mark_url_done_in_file(
        SimpleNamespace(),
        str(path),
        "https://play.qobuz.com/track/1",
    )


async def test_download_list_vazio_retorna(monkeypatch):
    app = SimpleNamespace(settings=SimpleNamespace(max_workers=1), delay=0)
    await core.QobuzDL.download_list_of_urls(app, [])
    await core.QobuzDL.download_list_of_urls(app, None)


async def test_download_list_track_sequencial_delega(monkeypatch):
    calls = []
    app = SimpleNamespace(
        settings=SimpleNamespace(max_workers=1),
        delay=0,
    )

    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("track", url.rsplit("/", 1)[-1]),
    )

    async def handle_url(url):
        calls.append(url)
        return True

    app.handle_url = handle_url
    app.mark_url_done_in_file = lambda *args: None

    await core.QobuzDL.download_list_of_urls(
        app,
        ["https://play.qobuz.com/track/1"],
    )

    assert calls == ["https://play.qobuz.com/track/1"]


async def test_download_from_txt_file_trata_erro_de_leitura(monkeypatch):
    app = SimpleNamespace()
    monkeypatch.setattr(
        core,
        "open",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("erro")),
    )

    await core.QobuzDL.download_from_txt_file(app, "urls.txt")


async def test_lucky_mode_query_valida_sem_download():
    async def search_by_type(*args, **kwargs):
        return ["url"]

    app = SimpleNamespace(
        lucky_type="track",
        lucky_limit=1,
        search_by_type=search_by_type,
    )

    result = await core.QobuzDL.lucky_mode(app, "abc", download=False)
    assert result == ["url"]


async def test_initialize_client_fecha_cliente_anterior(monkeypatch):
    closed = []

    class OldClient:
        async def close(self):
            closed.append(True)

    new_client = object()

    class FakeClient:
        @classmethod
        async def create(cls, *args, **kwargs):
            return new_client

    app = SimpleNamespace(
        client=OldClient(),
        settings=SimpleNamespace(user_auth_token=None),
        force_english=False,
        quality=6,
    )

    monkeypatch.setattr(core.qopy, "Client", FakeClient)

    await core.QobuzDL.initialize_client(
        app,
        "email",
        "pwd",
        "app",
        ["secret"],
    )

    assert app.client is new_client
    assert closed == [True]


async def test_search_favoritos_playlists_usa_fallback(monkeypatch):
    class Response:
        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    responses = iter(
        [
            Response({}),
            Response({"playlist_ids": ["p1", "p2"]}),
            Response({"id": "p1", "name": "P1", "owner": {}}),
            Response({}),
        ],
    )

    async def request(*args, **kwargs):
        return next(responses)

    client = SimpleNamespace(
        session=SimpleNamespace(request=request),
        base="https://api/",
        sec="secret",
        user_id="u1",
        _modern_sig=lambda *args: "sig",
    )

    def metadata(self, item, item_type, mode_dict, fav_subtype=None):
        return {"name": item.get("name", "Unknown"), "id": item.get("id")}

    app = SimpleNamespace(
        client=client,
        _extract_rich_metadata=metadata,
    )

    result = await core.QobuzDL.search_by_type(
        app,
        "",
        "favorites",
        fav_subtype="playlists",
    )

    assert len(result) == 1
    assert result[0]["url"].endswith("/playlist/p1")
