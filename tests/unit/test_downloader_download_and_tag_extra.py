from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


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
    obj = downloader.Download(
        SimpleNamespace(), "track-1", str(tmp_path), quality, settings=settings
    )
    obj.track_format = "{track_number} - {track_title}"
    obj.embed_art = False
    obj.http_session = object()
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


async def test_sample_url_creates_missing_placeholder(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)

    async def fresh_url(*args, **kwargs):
        return {"sample": True}

    obj.client.get_track_url = fresh_url
    missing = []
    monkeypatch.setattr(
        downloader,
        "create_missing_placeholder",
        lambda *args: missing.append(args),
    )

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )

    assert result is False
    assert len(missing) == 1


async def test_failed_direct_download_without_segment_template(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)
    calls = []

    async def fresh_url(item_id, fmt_id=None, force_segments=False):
        return {"url": "direct"} if not force_segments else {"other": True}

    async def direct(*args, **kwargs):
        calls.append("direct")
        raise RuntimeError("blocked")

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", direct)
    monkeypatch.setattr(
        downloader,
        "ui",
        SimpleNamespace(
            warn=lambda *a: None,
            error=lambda *a: None,
            skip=lambda *a: None,
        ),
    )

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )

    assert result is False
    assert calls == ["direct", "direct"]


async def test_tag_failure_returns_false(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)

    async def fresh_url(*args, **kwargs):
        return {"url": "direct"}

    async def download(url, filename, desc, **kwargs):
        open(filename, "wb").write(b"audio")

    def failing_tag(*args, **kwargs):
        raise RuntimeError("bad tags")

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", failing_tag)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )

    assert result is False


async def test_integrity_failure_emits_failed_event(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    obj.settings.verify_after_download = True
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)
    events = []

    async def fresh_url(*args, **kwargs):
        return {"url": "direct"}

    async def download(url, filename, desc, **kwargs):
        open(filename, "wb").write(b"audio")

    def tag(src, root, final, *args, **kwargs):
        open(final, "wb").write(b"tagged")

    def invalid(*args, **kwargs):
        return False, "invalid audio"

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", tag)
    monkeypatch.setattr(downloader, "verify_audio_integrity", invalid)
    monkeypatch.setattr(
        downloader,
        "emit_progress_json",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )

    assert result is False
    assert any(args[1] == "track_failed" for args, kwargs in events)
