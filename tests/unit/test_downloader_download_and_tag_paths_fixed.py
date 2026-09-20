from pathlib import Path
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


async def _get_format(*args, **kwargs):
    return ("flac", True, 16, 44100)


async def _no_sleep(*args, **kwargs):
    return None


def make_obj(tmp_path, monkeypatch, quality=6):
    settings = SimpleNamespace(
        legacy_charmap=False,
        multiple_disc_one_dir=False,
        multiple_disc_prefix="Disc",
        multiple_disc_track_format="{track_number}",
        verify_after_download=False,
        delay=0,
        segment_workers=None,
        embed_lyrics=True,
        lyrics_translation_lang="pt",
    )

    client = SimpleNamespace()

    # `Download.__init__` sempre cria um httpx.AsyncClient de verdade
    # (abre contexto SSL e sockets), mesmo que o teste nunca use rede.
    # Como a linha abaixo (`obj.http_session = object()`) sobrescreve essa
    # referencia sem nunca chamar `aclose()`, cada chamada a make_obj()
    # vazava um AsyncClient inteiro. Numa suite grande isso esgota o limite
    # de file descriptors do processo -- e' a causa do
    # "OSError: [Errno 24] Too many open files" visto no a-Shell.
    # Trocamos o AsyncClient por um objeto leve ANTES de instanciar Download,
    # entao nenhum socket/SSL real chega a ser aberto por este teste.
    monkeypatch.setattr(downloader.httpx, "AsyncClient", lambda *a, **k: object())

    obj = downloader.Download(
        client, "track-1", str(tmp_path), quality, settings=settings
    )

    obj.track_format = "{track_number} - {track_title}"
    obj.embed_art = False
    obj.http_session = object()
    monkeypatch.setattr(obj, "_get_format", _get_format)
    monkeypatch.setattr(downloader.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(downloader, "emit_progress_json", lambda *a, **k: None)
    return obj


def metadata():
    return {
        "id": "track-1",
        "title": "Song",
        "track_number": 1,
        "media_number": 1,
        "performer": {"name": "Artist"},
        "album": {"title": "Album", "artist": {"name": "Artist"}},
    }


async def test_download_and_tag_aborted(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: True)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "x"}, metadata(), metadata(), True, False
    )
    assert result is False


async def test_download_and_tag_missing_url(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {}, metadata(), metadata(), True, False
    )
    assert result is False


async def test_download_and_tag_direct_success(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)

    async def fresh_url(*args, **kwargs):
        return {"url": "https://example.test/audio"}

    async def fake_download(url, filename, desc, **kwargs):
        Path(filename).write_bytes(b"audio")

    def fake_tag(src, root, final, *args, **kwargs):
        Path(final).write_bytes(Path(src).read_bytes())

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", fake_download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", fake_tag)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "x"}, metadata(), metadata(), True, False
    )
    assert result is True
    assert obj.last_downloaded_file.endswith("01 - Song.flac")


async def test_download_and_tag_direct_fallback_to_segments(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)
    calls = []

    async def direct(*args, **kwargs):
        calls.append("direct")
        raise RuntimeError("blocked")

    async def segmented(*args, **kwargs):
        calls.append("segments")
        Path(args[1]).write_bytes(b"audio")

    def fake_tag(src, root, final, *args, **kwargs):
        Path(final).write_bytes(b"tagged")

    async def fresh_url(item_id, fmt_id=None, force_segments=False):
        return {"url_template": "template"} if force_segments else {"url": "x"}

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", direct)
    monkeypatch.setattr(downloader, "tqdm_download_segments", segmented)
    monkeypatch.setattr(downloader.metadata, "tag_flac", fake_tag)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )
    assert result is True
    assert calls == ["direct", "segments"]


async def test_download_and_tag_permanent_failure(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)

    async def fresh_url(*args, **kwargs):
        return {"url": "x"}

    async def permanent(*args, **kwargs):
        raise downloader._PermanentDownloadError("unavailable")

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", permanent)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )
    assert result is False
