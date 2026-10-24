import json

import pytest

from qobuz_dl import postprocess


@pytest.mark.asyncio
async def test_resumable_track_ids_uses_only_completed_tracks_with_audio(
    tmp_path, monkeypatch
):
    report = {
        "faixas": [
            {"id": 101, "download": {"situacao": "concluido"}},
            {"id": "202", "download": {"situacao": "ok"}},
            {"id": 303, "download": {"situacao": "pendente"}},
            {"id": 404, "download": {"situacao": "falha"}},
        ]
    }
    (tmp_path / postprocess.REPORT_FILENAME).write_text(
        json.dumps(report), encoding="utf-8"
    )

    monkeypatch.setattr(
        "qobuz_dl.playlist_links.scan_track_paths",
        lambda _directory: {"101": ["one.flac"], "303": ["three.flac"]},
    )

    assert await postprocess.resumable_track_ids(str(tmp_path)) == {"101"}


@pytest.mark.asyncio
async def test_resumable_track_ids_scans_nested_reports(tmp_path, monkeypatch):
    album_dir = tmp_path / "Artist" / "Album"
    album_dir.mkdir(parents=True)
    (album_dir / postprocess.REPORT_FILENAME).write_text(
        json.dumps({"faixas": [{"id": "55", "download": {"situacao": "ok"}}]}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "qobuz_dl.playlist_links.scan_track_paths",
        lambda _directory: {"55": [str(album_dir / "track.flac")]},
    )

    assert await postprocess.resumable_track_ids(str(tmp_path)) == {"55"}


@pytest.mark.asyncio
async def test_resumable_track_ids_returns_empty_for_missing_directory(tmp_path):
    assert await postprocess.resumable_track_ids(str(tmp_path / "missing")) == set()


def test_report_process_lock_is_exclusive_and_released(tmp_path):
    report_path = str(tmp_path / postprocess.REPORT_FILENAME)
    first = postprocess._try_acquire_process_lock(report_path)
    assert first is not None
    try:
        assert postprocess._try_acquire_process_lock(report_path) is None
    finally:
        postprocess._release_process_lock(first)

    third = postprocess._try_acquire_process_lock(report_path)
    assert third is not None
    postprocess._release_process_lock(third)
