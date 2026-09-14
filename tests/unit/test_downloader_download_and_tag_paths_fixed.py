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
        open(filename, "wb").write(b"audio")

    def fake_tag(src, root, final, *args, **kwargs):
        open(final, "wb").write(open(src, "rb").read())

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
        open(args[1], "wb").write(b"audio")

    def fake_tag(src, root, final, *args, **kwargs):
        open(final, "wb").write(b"tagged")

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
