"""Testes dos ramos finais de download e importação em core.py."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def test_download_from_id_status_404_sem_playlist(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=0,
        settings=SimpleNamespace(pl_failed=0, pl_skipped=0),
    )

    async def handle(*args, **kwargs):
        return False

    class Download:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            import httpx
            raise httpx.HTTPStatusError(
                "404",
                request=SimpleNamespace(),
                response=SimpleNamespace(status_code=404),
            )

    monkeypatch.setattr(core, "handle_download_id", handle)
    monkeypatch.setattr(core.downloader, "Download", Download)

    result = await core.QobuzDL.download_from_id(app, "x", is_playlist=False)
    assert result is False
    assert app.settings.pl_failed == 0


async def test_download_from_id_non_streamable_sem_playlist(monkeypatch):
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        delay=0,
        settings=SimpleNamespace(pl_failed=0),
    )

    async def handle(*args, **kwargs):
        return False

    class Download:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise core.NonStreamable("indisponível")

    monkeypatch.setattr(core, "handle_download_id", handle)
    monkeypatch.setattr(core.downloader, "Download", Download)

    result = await core.QobuzDL.download_from_id(app, "x", is_playlist=False)
    assert result is False
    assert app.settings.pl_failed == 0


async def test_mark_url_done_linha_completa(tmp_path):
    path = tmp_path / "urls.txt"
    url = "https://play.qobuz.com/track/1"
    path.write_text(f"{url}\nother\n", encoding="utf-8")

    core.QobuzDL.mark_url_done_in_file(SimpleNamespace(), str(path), url)

    assert path.read_text(encoding="utf-8").splitlines()[0] == f"{url} [DONE]"


async def test_download_from_txt_file_sem_urls_validas(monkeypatch, tmp_path):
    path = tmp_path / "urls.txt"
    path.write_text("# comentário\n\n", encoding="utf-8")
    called = []

    app = SimpleNamespace(download_list_of_urls=lambda *args, **kwargs: called.append(True))
    await core.QobuzDL.download_from_txt_file(app, str(path))

    assert called == []


async def test_lucky_mode_baixa_resultados(monkeypatch):
    called = []

    async def search(*args, **kwargs):
        return ["url"]

    async def download(urls):
        called.append(urls)

    app = SimpleNamespace(
        lucky_type="track",
        lucky_limit=2,
        search_by_type=search,
        download_list_of_urls=download,
    )

    result = await core.QobuzDL.lucky_mode(app, "query", download=True)

    assert result == ["url"]
    assert called == [["url"]]
