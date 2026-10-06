from qobuz_dl.lyrics_engine import LyricsEngine


def engine():
    return object.__new__(LyricsEngine)


def test_qobuz_lrc_has_no_intro_marker_or_space_after_timestamp():
    e = engine()
    result = e._qobuz_lines_to_lrc(
        [
            {"start": 12500, "line": "Primeiro verso"},
            {"start": 16200, "line": "Segundo verso"},
        ],
        inject_intro=True,
    )

    assert result == "[00:12.500]Primeiro verso\n[00:16.200]Segundo verso"
    assert "~ ~ ~" not in result


def test_normalize_lrc_removes_artificial_space_and_normalizes_milliseconds():
    e = engine()
    result = e._normalize_lrc(
        "[00:12.5] Primeiro verso\n[00:16.20]  Segundo verso"
    )

    assert result == "[00:12.500]Primeiro verso\n[00:16.200]Segundo verso"


def test_bilingual_lrc_uses_same_timestamp_with_translation_marker():
    e = engine()
    result = e._build_bilingual_lrc(
        "[00:12.500]First line\n[00:16.200]Second line",
        "[00:12.500]Primeira linha\n[00:16.200]Segunda linha",
    )

    assert result == (
        "[00:12.500]First line\n"
        "[00:12.500]» Primeira linha\n"
        "[00:16.200]Second line\n"
        "[00:16.200]» Segunda linha"
    )
    assert "~" not in result


def test_instrumental_pause_is_half_second_after_previous_line():
    e = engine()
    result = e._inject_instrumental_pauses(
        "[00:05.000]Primeiro\n[00:20.000]Segundo"
    )

    assert result == (
        "[00:05.000]Primeiro\n"
        "[00:05.500]• • •\n"
        "[00:20.000]Segundo"
    )


def test_instrumental_pause_does_not_change_short_gaps():
    e = engine()
    result = e._inject_instrumental_pauses(
        "[00:05.000]Primeiro\n[00:15.000]Segundo"
    )

    assert result == "[00:05.000]Primeiro\n[00:15.000]Segundo"
