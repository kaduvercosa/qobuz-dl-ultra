from types import SimpleNamespace

from qobuz_dl import downloader


def test_get_filename_attr_formats_numbers_and_fallbacks():
    track = {
        "id": "t1",
        "title": "Song",
        "track_number": 3,
        "media_number": 2,
        "release_date_original": "2024-05-06",
        "performer": {"name": "Singer"},
        "composer": {"name": "Writer"},
        "isrc": "ISRC1",
        "parental_warning": True,
    }
    album = {
        "title": "Record",
        "artist": {"name": "Album Artist"},
    }

    result = downloader.Download._get_filename_attr("Singer", track, album)

    assert result["artist"] == "Singer"
    assert result["albumartist"] == "Album Artist"
    assert result["track_number"] == "03"
    assert result["disc_number"] == "02"
    assert result["year"] == "2024"
    assert result["track_title"] == "Song"
    assert result["album_title"] == "Record"
    assert result["explicit"] == "🅴"


def test_get_filename_attr_uses_track_artist_when_album_artist_missing():
    track = {
        "title": "Song",
        "track_number": 1,
        "media_number": 1,
    }

    result = downloader.Download._get_filename_attr("Singer", track, {})

    assert result["albumartist"] == "Singer"
    assert result["album_artist"] == "Singer"


def test_get_track_attr_classifies_release_and_quality():
    meta = {
        "id": "a1",
        "title": "Song",
        "track_count": 5,
        "release_date_original": "2024-01-01",
        "genre": {"name": "Rock"},
        "label": {"name": "Label; Sub"},
        "album": {
            "title": "EP",
            "artist": {"name": "Artist"},
            "release_type": "single",
            "track_count": 5,
            "duration": 900,
        },
        "performer": {"name": "Artist"},
    }

    result = downloader.Download._get_track_attr(meta, "Song", 24, 96000, "FLAC")

    assert result["album"] == "EP"
    assert result["format"] == "FLAC"
    assert result["quality_tag"] == "FLAC 24"
    assert result["year"] == "2024"
    assert result["label"] == "Label / Sub"
    assert result["release_type"] == result["media_type"]


def test_get_album_attr_defaults_and_mp3_quality():
    album = {
        "id": "a1",
        "title": "Album",
        "artist": {"name": "Artist"},
        "release_date_original": "2020-01-01",
    }

    result = downloader.Download._get_album_attr(album, "Album", "MP3", None, None)

    assert result["album"] == "Album"
    assert result["quality_tag"] == "MP3"
    assert result["disc_count"] == 1
    assert result["track_count"] == 1
    assert result["year"] == "2020"


async def test_get_format_restriction_marks_quality_unmet(monkeypatch):
    client = SimpleNamespace()

    async def get_track_url(*args, **kwargs):
        return {
            "bit_depth": 24,
            "sampling_rate": 96000,
            "restrictions": [{"code": downloader.QL_DOWNGRADE}],
        }

    client.get_track_url = get_track_url
    obj = SimpleNamespace(client=client, quality=27)

    result = await downloader.Download._get_format(
        obj, {"id": "t1"}, is_track_id=True
    )

    assert result == ("FLAC", False, 24, 96000)
