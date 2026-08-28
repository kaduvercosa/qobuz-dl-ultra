# ==============================================================================
# MÓDULO: constants.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Valores padrão (fallback) usados quando o usuário não define
#            folder_format/track_format no config.ini nem via linha de
#            comando (-ff/-tf em commands.py). Também usado como valor
#            padrão do parâmetro `folder_format`/`track_format` no
#            construtor de QobuzDL (core.py).
# ==============================================================================

# Formato PADRÃO de nome de pasta (quando --folder-format / config.ini não
# define nada). Usa os mesmos placeholders documentados em commands.py
# (-ff/--folder-format): release_type, album_artist, album_title, year,
# format, bit_depth, etc.
# Exemplo de resultado: "Album/Pink Floyd - The Wall (1979) [FLAC 24]"
DEFAULT_FOLDER = (
    "{release_type}/{album_artist} - {album_title} ({year}) [{format} {bit_depth}]"
)

# Formato padrao: so' numero + titulo. O artista nao e' repetido no
# nome do arquivo porque ja' aparece no nivel da pasta (ALBUMARTIST).
# Quem quiser o artista por faixa pode setar no config:
#   track_format = {track_number}. {track_title_base} - {track_artist}
DEFAULT_TRACK = "{track_number}. {track_title_base}"

# Formato padrão específico para álbuns com MÚLTIPLOS DISCOS (prefixa o
# número do disco antes do número da faixa, ex: "1.03 - Nome da Faixa").
# Usado quando multiple_disc_track_format não é definido no config.ini
# (ver --multiple-disc-track-format em commands.py).
DEFAULT_MULTIPLE_DISC_TRACK = "{disc_number}.{track_number} - {track_title_base}"

# character length for the longest allowed album track filename
# (folder_name + track_name + extension).
# Limite de segurança para não estourar limites de path do sistema
# operacional (principalmente Windows, que historicamente tem um teto de
# ~260 caracteres de caminho total). Se nomes de arquivo estiverem sendo
# cortados de forma agressiva demais/de menos, é este número que controla
# isso (procure por OK_MAX_CHARACTER_LENGTH em downloader.py/utils.py para
# ver onde o corte é aplicado de fato -- não é feito aqui).
OK_MAX_CHARACTER_LENGTH = 180
