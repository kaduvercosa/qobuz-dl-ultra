# tests/unit/test_metadata_novas_tags.py
from qobuz_dl.metadata import _eh_various_artists, _tracktotal_do_disco
from qobuz_dl.settings import QobuzDLSettings


def test_various_artists_alias():
    s = QobuzDLSettings(various_artists_aliases="Various Artists, VA")
    album = {"artist": {"name": "Various Artists"}}
    assert _eh_various_artists(album, s)


def test_tracktotal_por_disco():
    album = {
        "tracks_count": 4,
        "media_count": 2,
        "tracks": {"items": [
            {"media_number": 1}, {"media_number": 1},
            {"media_number": 2}, {"media_number": 2},
        ]},
    }
    item = {"media_number": 2}
    assert _tracktotal_do_disco(album, item) == "2"


def test_tracktotal_album_um_disco():
    album = {"tracks_count": 12, "media_count": 1, "tracks": {"items": []}}
    assert _tracktotal_do_disco(album, {"media_number": 1}) == "12"
