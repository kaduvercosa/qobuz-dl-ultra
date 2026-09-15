from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


async def _no_sleep(*args, **kwargs):
    return None


def make_obj(tmp_path, monkeypatch):
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
    monkeypatch.setattr(downloader.httpx, "AsyncClient", lambda *a, **k: object())
    obj = downloader.Download(
        client, "track-1", str(tmp_path), 6, settings=settings
    )
    obj.track_format = "{track_number} - {track_title}"
    obj.embed_art = False
    obj.http_session = object()
    obj.fetch_lyrics = True
    obj.no_lrc_files = False
    monkeypatch.setattr(downloader.asyncio, "sleep", _no_sleep)
    return obj


def metadata():
    return {
        "id": "track-1",
        "title": "Song",
        "track_number": 1,
        "media_number": 1,
        "performer": {"name": "Performer"},
        "album": {
            "title": "Album",
            "artist": {"name": "Album Artist"},
        },
    }


async def prepare_success(monkeypatch, obj):
    async def fresh_url(*args, **kwargs):
        return {"url": "direct"}

    async def download(url, filename, desc, **kwargs):
        with open(filename, "wb") as handle:
            handle.write(b"audio")

    def tag(src, root, final, *args, **kwargs):
        with open(final, "wb") as handle:
            handle.write(b"tagged")

    obj.client.get_track_url = fresh_url
    monkeypatch.setattr(downloader, "tqdm_download", download)
    monkeypatch.setattr(downloader.metadata, "tag_flac", tag)
    monkeypatch.setattr(downloader.abort_event, "is_set", lambda: False)
    monkeypatch.setattr(downloader, "emit_progress_json", lambda *a, **k: None)


async def test_download_and_tag_lyrics_updates_report(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    await prepare_success(monkeypatch, obj)
    calls = []

    async def fetch_lyrics(track_id, language=None):
        calls.append((track_id, language))
        if language is None:
            return {"original": {"lang": "en"}, "lyrics": "hello"}
        return {"translation": "ola"}

    class Lyrics:
        def fetch_and_inject(self, **kwargs):
            return {"status": "ok", "source": "qobuz"}

    obj._fetch_qobuz_lyrics_json = fetch_lyrics
    obj.lyrics_engine = Lyrics()
    report = {}

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False,
        letras_out=report,
    )

    assert result is True
    assert calls == [("track-1", None), ("track-1", "pt")]
    assert report


async def test_download_and_tag_lyrics_original_language_note(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    await prepare_success(monkeypatch, obj)
    notes = []

    async def fetch_lyrics(track_id, language=None):
        if language is None:
            return {"original": {"lang": "pt"}}
        return {}

    class Lyrics:
        def fetch_and_inject(self, **kwargs):
            return {"status": "ok"}

    obj._fetch_qobuz_lyrics_json = fetch_lyrics
    obj.lyrics_engine = Lyrics()
    monkeypatch.setattr(downloader.tqdm, "write", lambda message: notes.append(message))

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )

    assert result is True
    assert notes


async def test_download_and_tag_delay_from_argv(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    obj.fetch_lyrics = False
    await prepare_success(monkeypatch, obj)
    obj.settings.delay = 0
    monkeypatch.setattr(downloader.sys, "argv", ["pytest", "--delay", "2"])
    waits = []

    async def record_sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(downloader.asyncio, "sleep", record_sleep)

    result = await obj._download_and_tag(
        str(tmp_path), 1, {"url": "initial"}, metadata(), metadata(), True, False
    )

    assert result is True
    assert waits == [1, 2]


async def test_download_and_tag_verify_failure_isolated(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    obj.fetch_lyrics = False
    obj.settings.verify_after_download = True
    await prepare_success(monkeypatch, obj)
    events = []

    monkeypatch.setattr(downloader, "verify_audio_integrity", lambda path: (False, "invalid"))
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
    assert any(path.name.endswith(".corrupt") for path in tmp_path.iterdir())
