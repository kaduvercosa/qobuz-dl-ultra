# Default format strings
DEFAULT_FOLDER = (
    "{media_type}/{album_artist} - {album_title} ({year}) [{format} {bit_depth}]"
)
# Formato padrao: so' numero + titulo. O artista nao e' repetido no
# nome do arquivo porque ja' aparece no nivel da pasta (ALBUMARTIST).
# Quem quiser o artista por faixa pode setar no config:
#   track_format = {track_number}. {track_title_base} - {track_artist}
DEFAULT_TRACK = "{track_number}. {track_title_base}"
DEFAULT_MULTIPLE_DISC_TRACK = "{disc_number}.{track_number} - {track_title_base}"
# character length for the longest allowed album track filename
# (folder_name + track_name + extension).
OK_MAX_CHARACTER_LENGTH = 180
