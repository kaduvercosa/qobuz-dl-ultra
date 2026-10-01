"""Testes de cobertura total para qobuz_dl/constants.py."""

from qobuz_dl import constants


def test_default_folder_contem_placeholders_essenciais():
    for ph in (
        "{release_type}",
        "{album_artist}",
        "{album_title}",
        "{year}",
        "{format}",
        "{bit_depth}",
    ):
        assert ph in constants.DEFAULT_FOLDER


def test_default_folder_e_string():
    assert isinstance(constants.DEFAULT_FOLDER, str)
    assert len(constants.DEFAULT_FOLDER) > 0


def test_default_track_contem_placeholders_essenciais():
    for ph in ("{track_number}", "{track_title_base}", "{explicit}"):
        assert ph in constants.DEFAULT_TRACK


def test_default_track_e_string():
    assert isinstance(constants.DEFAULT_TRACK, str)
    assert len(constants.DEFAULT_TRACK) > 0


def test_default_multiple_disc_track_contem_disc_e_track():
    assert "{disc_number}" in constants.DEFAULT_MULTIPLE_DISC_TRACK
    assert "{track_number}" in constants.DEFAULT_MULTIPLE_DISC_TRACK
    assert "{track_title_base}" in constants.DEFAULT_MULTIPLE_DISC_TRACK


def test_default_multiple_disc_track_e_string():
    assert isinstance(constants.DEFAULT_MULTIPLE_DISC_TRACK, str)
    assert len(constants.DEFAULT_MULTIPLE_DISC_TRACK) > 0


def test_ok_max_character_length_e_inteiro_positivo():
    assert isinstance(constants.OK_MAX_CHARACTER_LENGTH, int)
    assert constants.OK_MAX_CHARACTER_LENGTH > 0


def test_ok_max_character_length_valor_esperado():
    assert constants.OK_MAX_CHARACTER_LENGTH == 180


def test_defaults_sao_distintos():
    assert constants.DEFAULT_FOLDER != constants.DEFAULT_TRACK
    assert constants.DEFAULT_FOLDER != constants.DEFAULT_MULTIPLE_DISC_TRACK
    assert constants.DEFAULT_TRACK != constants.DEFAULT_MULTIPLE_DISC_TRACK


def test_defaults_sao_interpolaveis():
    dados = {
        "release_type": "Album",
        "album_artist": "Artista",
        "album_title": "Título",
        "year": "2024",
        "format": "FLAC",
        "bit_depth": "24",
    }
    resultado = constants.DEFAULT_FOLDER.format(**dados)
    assert "Artista" in resultado
    assert "Título" in resultado


def test_default_track_interpolavel():
    dados = {"track_number": "01", "track_title_base": "Nome", "explicit": ""}
    resultado = constants.DEFAULT_TRACK.format(**dados)
    assert "Nome" in resultado


def test_default_multiple_disc_interpolavel():
    dados = {"disc_number": "1", "track_number": "03", "track_title_base": "Faixa"}
    resultado = constants.DEFAULT_MULTIPLE_DISC_TRACK.format(**dados)
    assert "Faixa" in resultado
