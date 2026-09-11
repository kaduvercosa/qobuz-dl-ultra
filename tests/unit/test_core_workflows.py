from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def test_playlist_precarregada_baixa_sem_variavel_indefinida(
    tmp_path, monkeypatch
):
    app = SimpleNamespace(
        directory=str(tmp_path),
        folder_format="original",
        settings=SimpleNamespace(
            multiple_disc_one_dir=False,
            max_workers=1,
            pl_success=0,
            pl_skipped=0,
            pl_failed=0,
        ),
        playlist_as_albums=False,
        delay=0,
    )
    downloaded = []

    async def download_from_id(track_id, **kwargs):
        downloaded.append((track_id, kwargs["playlist_index"]))
        app.settings.pl_success += 1
        return True

    async def finalize_report(*args, **kwargs):
        return None

    app.download_from_id = download_from_id
    monkeypatch.setattr(core.downloader, "print_download_header", lambda *a, **k: None)
    monkeypatch.setattr(core.downloader, "safe_print", lambda *a, **k: None)
    monkeypatch.setattr(core.postprocess, "finalize_report", finalize_report)

    result = await core.QobuzDL.download_from_playlist_file(
        app, name="Favoritas", _preloaded_track_ids=["10", "20"]
    )

    assert result is True
    assert downloaded == [("10", 1), ("20", 2)]
    assert app.folder_format == "original"
    assert app.settings.multiple_disc_one_dir is False


async def test_lista_so_marca_url_concluida(tmp_path, monkeypatch):
    source = tmp_path / "links.txt"
    success_url = "https://play.qobuz.com/track/10"
    failed_url = "https://play.qobuz.com/track/20"
    source.write_text(f"{success_url}\n{failed_url}\n", encoding="utf-8")

    app = SimpleNamespace(
        settings=SimpleNamespace(max_workers=1),
        delay=0,
    )

    async def handle_url(url):
        return url == success_url

    app.handle_url = handle_url
    app.download_from_txt_file = None
    app.mark_url_done_in_file = core.QobuzDL.mark_url_done_in_file.__get__(app)

    await core.QobuzDL.download_list_of_urls(
        app, [success_url, failed_url], txt_file=str(source)
    )

    assert source.read_text(encoding="utf-8").splitlines() == [
        f"{success_url} [DONE]",
        failed_url,
    ]
