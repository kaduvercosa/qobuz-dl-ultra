"""Regression tests for download_track() without network or audio output."""

from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


async def _get_format(*args, **kwargs):
    return ("flac", True, 16, 44100)


def make_obj(tmp_path, monkeypatch):
    settings = SimpleNamespace(
        embed_art=False,
        no_cover=True,
        saved_art_size=1200,
        embedded_art_size=1200,
        multiple_disc_track_format="{track_number}",
        fallback_folder_format="{album}",
        fallback_track_format="{track_number} - {title}",
        folder_format="{album}",
        track_format="{track_number} - {title}",
        legacy_charmap=False,
        pl_success=0,
        pl_skipped=0,
    )
    monkeypatch.setattr(downloader.httpx, "AsyncClient", lambda *a, **k: object())
    obj = downloader.Download(
        SimpleNamespace(), "track-1", str(tmp_path), 6, settings=settings
    )
    monkeypatch.setattr(obj, "_get_format", _get_format)
    return obj


async def test_download_track_amostra(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)

    async def get_track_url(*args, **kwargs):
        return {"sample": True}

    async def get_track_meta(*args, **kwargs):
        return {"title": "Demo"}

    obj.client.get_track_url = get_track_url
    obj.client.get_track_meta = get_track_meta

    result = await obj.download_track()

    assert result is False
    assert obj.settings.pl_skipped == 1


async def test_download_track_falha_no_download_and_tag(monkeypatch, tmp_path):
    obj = make_obj(tmp_path, monkeypatch)
    track_meta = {
        "id": "track-1",
        "title": "Track",
        "track_number": 1,
        "media_number": 1,
        "release_date_original": "2024-01-01",
        "album": {
            "title": "Album",
            "image": {"large": "https://example.test/cover.jpg"},
            "artist": {"name": "Artist"},
            "release_type": "album",
            "track_count": 1,
        },
        "performer": {"name": "Artist"},
    }

    async def get_track_url(*args, **kwargs):
        return {"sampling_rate": 44100, "format_id": 6}

    async def get_track_meta(*args, **kwargs):
        return track_meta

    obj.client.get_track_url = get_track_url
    obj.client.get_track_meta = get_track_meta

    async def fake_download_and_tag(*args, **kwargs):
        return False

    monkeypatch.setattr(obj, "_download_and_tag", fake_download_and_tag)
    monkeypatch.setattr(
        downloader,
        "process_folder_format_with_subdirs",
        lambda *args, **kwargs: str(tmp_path / "Album"),
    )

    async def fake_cover(*args, **kwargs):
        return None

    monkeypatch.setattr(downloader, "_get_cover_and_embed", fake_cover)

    result = await obj.download_track()
    assert result is False
