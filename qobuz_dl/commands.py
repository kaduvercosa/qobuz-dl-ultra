# ============================================================================
# commands.py
# Define TODOS os comandos (subcomandos) e opções de linha de comando (CLI)
# do qobuz-dl-ultra usando argparse.
# ============================================================================

import argparse
import os
import re
import shutil
from qobuz_dl.color import INFO as CYAN, RESET as OFF, BG


# ----------------------------------------------------------------------------
# Formatter customizado do argparse: ajusta a largura do texto de ajuda
# de acordo com o tamanho do terminal (evita quebras de linha feias).
# ----------------------------------------------------------------------------
class CustomHelpFormatter(argparse.RawTextHelpFormatter):
    def __init__(self, prog, indent_increment=2, max_help_position=50, width=None):
        try:
            term_width = shutil.get_terminal_size((100, 24)).columns
            width = term_width
        except Exception:
            width = 100
            term_width = 100

        max_pos = min(50, max(24, int(term_width * 0.4)))
        super().__init__(prog, indent_increment, max_pos, width)


# ----------------------------------------------------------------------------
# ArgumentParser customizado: traduz textos padrão do argparse para português
# e colore títulos e flags no terminal.
# ----------------------------------------------------------------------------
class ColoredArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs):
        kwargs["formatter_class"] = CustomHelpFormatter
        super().__init__(*args, **kwargs)

    def print_help(self, file=None):
        if file is None:
            import sys

            file = sys.stdout

        help_text = self.format_help()

        help_text = help_text.replace(
            "positional arguments:", "argumentos posicionais:"
        )
        help_text = help_text.replace("options:", "opções gerais:")
        help_text = help_text.replace("optional arguments:", "opções:")
        help_text = help_text.replace(
            "show this help message and exit", "mostra esta mensagem de ajuda e sai"
        )
        help_text = help_text.replace("usage:", "Uso:")

        help_text = re.sub(
            r"^([\w][\w\s]*:)$", f"\n{CYAN}{BG}\\1{OFF}", help_text, flags=re.MULTILINE
        )

        def colorize_responsive(match):
            spaces = match.group(1)
            flag_title = match.group(2)
            explanation = match.group(3)
            return f"{spaces}{CYAN}{flag_title}{OFF}{explanation}"

        help_text = re.sub(
            r"^(\s+)(-[^\n]{2,}?)( {2,}.*|)$",
            colorize_responsive,
            help_text,
            flags=re.MULTILINE,
        )

        help_text = help_text.lstrip("\n")
        file.write(help_text)


# ----------------------------------------------------------------------------
# Pasta padrão de download.
# ----------------------------------------------------------------------------
def _default_download_folder():
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    if ios_home:
        return os.path.join(ios_home, "QobuzDownloads")
    return "QobuzDownloads"


# ----------------------------------------------------------------------------
# Subcomando: "interactive" (aliases: "i", "fun")
# ----------------------------------------------------------------------------
def fun_args(subparsers, default_limit):
    interactive = subparsers.add_parser(
        "interactive",
        usage="qobuz-dl interactive [opções]",
        description="Pesquise interativamente por faixas e álbuns.",
        help="modo de pesquisa interativa",
        aliases=["i", "fun"],
    )
    interactive.add_argument(
        "-l",
        "--limit",
        metavar="int",
        default=default_limit,
        help=f"limite de resultados da pesquisa (padrão: {default_limit})",
    )
    return interactive


# ----------------------------------------------------------------------------
# Subcomando: "lucky"
# ----------------------------------------------------------------------------
def lucky_args(subparsers):
    lucky = subparsers.add_parser(
        "lucky",
        usage="qobuz-dl lucky [opções] <QUERY>",
        description="Baixa os primeiros <n> resultados retornados de uma pesquisa no Qobuz.",
        help="modo de download por busca automática",
    )
    lucky.add_argument(
        "-t",
        "--type",
        default="album",
        help="tipo de item a pesquisar (artist, album, track, playlist) (padrão: album)",
    )
    lucky.add_argument(
        "-n",
        "--number",
        metavar="int",
        type=int,
        default=1,
        help="número de resultados para baixar (padrão: 1)",
    )
    lucky.add_argument("QUERY", nargs="+", help="termo de busca")
    return lucky


# ----------------------------------------------------------------------------
# Subcomando: "dl"
# ----------------------------------------------------------------------------
def dl_args(subparsers):
    download = subparsers.add_parser(
        "dl",
        usage="qobuz-dl dl [opções] <SOURCE>",
        description="Baixa por URL de álbum, faixa, artista, gravadora ou playlist do Qobuz, ou um arquivo de texto com uma lista dessas URLs.",
        help="modo de download direto",
    )
    download.add_argument(
        "SOURCE",
        metavar="SOURCE",
        nargs="+",
        help=("uma ou mais URLs (separadas por espaço) ou um arquivo de texto"),
    )
    download.add_argument(
        "-b",
        "--blacklist",
        help="Caminho para um arquivo de texto contendo palavras-chave para ignorar (blacklist)",
        type=str,
        default=None,
    )
    download.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simula o que seria baixado sem fazer o download de fato.",
    )
    download.add_argument(
        "--since",
        metavar="DATE",
        type=str,
        default=None,
        help="Baixa apenas lançamentos a partir desta data (YYYY-MM-DD ou YYYY).",
    )
    download.add_argument(
        "--before",
        metavar="DATE",
        type=str,
        default=None,
        help="Baixa apenas lançamentos anteriores a esta data (YYYY-MM-DD ou YYYY).",
    )
    download.add_argument(
        "--tag-only",
        action="store_true",
        default=False,
        help="Reaplica as tags nos arquivos já baixados sem baixar novamente. Pula ausentes.",
    )
    download.add_argument(
        "--musicbrainz",
        action="store_true",
        default=False,
        help="Busca e embute IDs do MusicBrainz (MBID) via ISRC. Requer internet. (~1s/faixa)",
    )
    return download


# ----------------------------------------------------------------------------
# Subcomando: "auth" (alias: "login")
# ----------------------------------------------------------------------------
def auth_args(subparsers):
    auth = subparsers.add_parser(
        "auth",
        usage="qobuz-dl auth",
        description="Atualiza suas credenciais de acesso (email e token) sem precisar redefinir toda a configuração.",
        help="atualiza credenciais de login",
        aliases=["login"],
    )
    return auth


# ----------------------------------------------------------------------------
# Subcomando: "user" (aliases: "account", "profile", "me", "info")
# ----------------------------------------------------------------------------
def user_args(subparsers):
    user = subparsers.add_parser(
        "user",
        usage="qobuz-dl user [opções]",
        description="Mostra o perfil do usuário, status da assinatura, limites e dados da conta.",
        help="exibe informações do usuário e status da assinatura",
        aliases=["account", "profile", "me", "info"],
    )
    user.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Exibe o JSON bruto retornado pelo endpoint user/get",
    )
    return user


# ----------------------------------------------------------------------------
# Subcomando: "lyrics"
# ----------------------------------------------------------------------------
def lyrics_args(subparsers, default_folder=None):
    lyrics = subparsers.add_parser(
        "lyrics",
        usage="qobuz-dl lyrics [opções] [DIR]",
        description="Escaneia retroativamente um diretório e injeta letras ou traduções ausentes nos arquivos de áudio.",
        help="modo de injeção retroativa de letras",
    )
    lyrics.add_argument(
        "DIR",
        metavar="DIRECTORY",
        nargs="?",
        default=None,
        help=f'O diretório local contendo os arquivos a serem escaneados (padrão: "{default_folder}")',
    )
    return lyrics


# ----------------------------------------------------------------------------
# Subcomando: "sync-playlist" (alias: "sp")
# ----------------------------------------------------------------------------
def sync_playlist_args(subparsers):
    sync_pl = subparsers.add_parser(
        "sync-playlist",
        aliases=["sp"],
        usage="qobuz-dl sync-playlist [opções] <URL>",
        description="Sincroniza uma pasta local com uma playlist do Qobuz. "
        "Baixa faixas ausentes e remove faixas que não estão mais na playlist.",
        help="sincroniza uma pasta local com uma playlist do Qobuz",
    )
    sync_pl.add_argument(
        "URL",
        help="URL da playlist do Qobuz (ex. https://play.qobuz.com/playlist/12345)",
    )
    sync_pl.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Pula o aviso de confirmação antes de deletar/baixar",
    )
    return sync_pl


# ----------------------------------------------------------------------------
# Subcomando: "import-playlist" (alias: "ip")
# ----------------------------------------------------------------------------
def import_playlist_args(subparsers):
    ip = subparsers.add_parser(
        "import-playlist",
        aliases=["ip"],
        usage="qobuz-dl import-playlist [opções] <SOURCE>",
        description=(
            "Importa uma playlist de qualquer plataforma via URL ou arquivo exportado "
            "(TXT, CSV, JSON) e baixa/copia as faixas correspondentes do Qobuz.\n\n"
            "Fontes suportadas:\n"
            "  URL Spotify   : open.spotify.com/playlist/...\n"
            "  URL Deezer    : deezer.com/playlist/...\n"
            "  URL Apple Music: music.apple.com/.../playlist/...\n"
            "  TXT : um 'Artista - Título' por linha\n"
            "  CSV : colunas 'artist,title' (Exportify/Soundiiz/TuneMyMusic)\n"
            "  JSON: formato de exportação do Spotify\n"
        ),
        help="importa playlist por URL (Spotify/Deezer/Apple Music) ou arquivo",
    )
    ip.add_argument(
        "SOURCE",
        help=(
            "URL de playlist (Spotify, Deezer, Apple Music) "
            "ou caminho para arquivo exportado (.txt, .csv, .json)."
        ),
    )
    ip.add_argument(
        "--name",
        "-n",
        type=str,
        default=None,
        help="Nome da pasta local onde as faixas serão salvas (padrão: nome do arquivo).",
    )
    ip.add_argument(
        "--auto",
        action="store_true",
        default=False,
        help=(
            "Aceita automaticamente correspondências duvidosas "
            "(>=60%% de similaridade)."
        ),
    )
    return ip


# ----------------------------------------------------------------------------
# Opções de saída no terminal (-v/--verbose, --quiet, --no-color)
# ----------------------------------------------------------------------------
def add_output_args(parser, suppress=False):
    kwargs = {"default": argparse.SUPPRESS} if suppress else {}
    group = parser.add_argument_group("saída no terminal")
    group.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="mostra mensagens de diagnóstico (nível DEBUG)",
        **kwargs,
    )
    group.add_argument(
        "--quiet",
        action="store_true",
        help="mostra apenas avisos e erros (bom para cron/scripts)",
        **kwargs,
    )
    group.add_argument(
        "--no-color",
        action="store_true",
        help="desliga as cores ANSI (respeita também a variável NO_COLOR)",
        **kwargs,
    )
    group.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help=(
            "controle fino do logger (sobrepõe -v/--quiet). Útil pra "
            "isolar ruído de rede/retry sem ligar todo o DEBUG do programa"
        ),
        **kwargs,
    )
    return parser


# ----------------------------------------------------------------------------
# Opções comuns de download
# ----------------------------------------------------------------------------
def add_common_arg(custom_parser, default_folder, default_quality):
    custom_parser.add_argument(
        "-d",
        "--directory",
        metavar="PATH",
        default=default_folder,
        help=f'diretório para os downloads (padrão: "{default_folder}")',
    )
    custom_parser.add_argument(
        "--no-lrc-files",
        dest="lrc_files",
        action="store_false",
        default=argparse.SUPPRESS,
        help="não salva letras sincronizadas em arquivos .lrc externos",
    )
    custom_parser.add_argument(
        "-q",
        "--quality",
        metavar="int",
        type=int,
        default=default_quality,
        choices=[5, 6, 7, 27],
        help=(
            '"qualidade" do áudio (5, 6, 7, 27)\n'
            f"[320, LOSSLESS, 24B<=96KHZ, 24B>96KHZ] (padrão: {default_quality})"
        ),
    )
    custom_parser.add_argument(
        "--albums-only",
        action="store_true",
        help=("não baixa singles, EPs ou lançamentos de vários artistas (VA)"),
    )
    custom_parser.add_argument(
        "--no-m3u",
        action="store_true",
        help="não cria arquivos .m3u ao baixar playlists",
    )
    custom_parser.add_argument(
        "--no-fallback",
        action="store_true",
        help="desativa o downgrade automático de qualidade (pula itens indisponíveis na qualidade definida)",
    )
    custom_parser.add_argument(
        "--no-db", action="store_true", help="ignora e não consulta o banco de dados"
    )
    custom_parser.add_argument(
        "-ff",
        "--folder-format",
        metavar="PATTERN",
        help="""padrão para formatar nomes de pastas, ex:
        "{album_artist} - {album_title} ({year}) {{{barcode}}}". chaves disponíveis: 
        album_id, album_url, album_title, album_title_base, album_artist, album_genre, 
        album_composer, label, copyright, upc, barcode, release_date, year, media_type,
        format, bit_depth, sampling_rate, album_version, disc_count, track_count.
        Nota: Você pode usar '/' para criar subdiretórios.""",
    )
    custom_parser.add_argument(
        "-fbff",
        "--fallback-folder-format",
        metavar="PATTERN",
        help="""padrão alternativo (fallback) para pastas quando o formato principal falhar.""",
    )
    custom_parser.add_argument(
        "-tf",
        "--track-format",
        metavar="PATTERN",
        help="""padrão para formatar nomes das faixas. ex:
        "{track_number} - {track_title}" 
        chaves disponíveis:
        album_title, album_title_base, album_artist, track_id, track_artist, track_composer, 
        track_number, isrc, bit_depth, sampling_rate, track_title, track_title_base
        version, year, disc_number, release_date.
        Não pode conter caracteres bloqueados pelo sistema operativo.""",
    )
    custom_parser.add_argument(
        "-s",
        "--smart-discography",
        action="store_true",
        help="""Tenta filtrar álbuns de spam/tributos ao buscar a discografia de um artista.""",
    )
    custom_parser.add_argument(
        "--verify-download",
        dest="verify_after_download",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "decodifica cada faixa com ffmpeg logo após o download terminar "
            "para detectar corrupção real no áudio (e não apenas falhas nas tags)."
        ),
    )
    custom_parser.add_argument(
        "--progress-json",
        dest="progress_json",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "emite uma linha JSON para cada evento track-start/track-done no "
            "stdout para interfaces externas (GUI web, app)."
        ),
    )
    custom_parser.add_argument(
        "--delay",
        type=int,
        default=0,
        help="Aguarda uma quantidade específica de segundos entre o download das faixas.",
    )
    custom_parser.add_argument(
        "--playlist-as-albums",
        action="store_true",
        help="Baixa itens de playlist usando a estrutura de pastas do álbum de origem.",
    )
    custom_parser.add_argument(
        "--no-lyrics",
        action="store_true",
        help="desativa a busca e injeção automática de letras para esta sessão.",
    )
    custom_parser.add_argument(
        "--no-embed-lyrics",
        dest="no_embed_lyrics",
        action="store_true",
        default=argparse.SUPPRESS,
        help="não embute letras nas tags do arquivo de áudio (salva apenas como .lrc/.txt)",
    )
    custom_parser.add_argument(
        "--multi-tags",
        dest="multi_value_tags",
        action="store_true",
        default=argparse.SUPPRESS,
        help="divide metadados separados por vírgulas (como gêneros) em múltiplos campos na tag",
    )
    custom_parser.add_argument(
        "--no-multi-tags",
        action="store_true",
        help="desativa temporariamente a divisão de metadados em múltiplas tags (sobrescreve config.ini)",
    )
    custom_parser.add_argument(
        "--booklet-only",
        action="store_true",
        help="baixa apenas o Encarte Digital (Digital Booklet) e os PDFs extras (pula faixas)",
    )
    custom_parser.add_argument(
        "--native-lang",
        action="store_true",
        help="não força o idioma para Inglês; obtém metadados no idioma nativo da conta",
    )
    custom_parser.add_argument(
        "--no-credits",
        action="store_true",
        help="desativa a geração do arquivo Digital Booklet.txt (Créditos e Notas do Álbum)",
    )
    custom_parser.add_argument(
        "--with-credits",
        action="store_true",
        help="força a geração do arquivo Digital Booklet.txt (sobrescreve o config.ini)",
    )

    tag_group = custom_parser.add_argument_group("opções de tags")
    tag_group.add_argument(
        "--no-album-artist-tag",
        action="store_true",
        help="não adiciona a tag do artista do álbum",
    )
    tag_group.add_argument(
        "--no-album-title-tag",
        action="store_true",
        help="não adiciona a tag de título do álbum",
    )
    tag_group.add_argument(
        "--no-track-artist-tag",
        action="store_true",
        help="não adiciona a tag do artista da faixa",
    )
    tag_group.add_argument(
        "--no-track-title-tag",
        action="store_true",
        help="não adiciona a tag de título da faixa",
    )
    tag_group.add_argument(
        "--no-release-date-tag",
        action="store_true",
        help="não adiciona a tag da data de lançamento",
    )
    tag_group.add_argument(
        "--no-media-type-tag",
        action="store_true",
        help="não adiciona a tag do tipo de mídia (media type)",
    )
    tag_group.add_argument(
        "--no-genre-tag", action="store_true", help="não adiciona a tag do gênero"
    )
    tag_group.add_argument(
        "--no-replaygain-tag",
        action="store_true",
        help="Não adiciona tags de ReplayGain nos arquivos de áudio.",
    )
    tag_group.add_argument(
        "--no-album-url-tag",
        action="store_true",
        help="Não adiciona a tag QOBUZ ALBUM URL nos arquivos de áudio.",
    )
    tag_group.add_argument(
        "--no-track-number-tag",
        action="store_true",
        help="não adiciona o número da faixa",
    )
    tag_group.add_argument(
        "--no-track-total-tag",
        action="store_true",
        help="não adiciona o total de faixas",
    )
    tag_group.add_argument(
        "--no-disc-number-tag",
        action="store_true",
        help="não adiciona o número do disco",
    )
    tag_group.add_argument(
        "--no-disc-total-tag",
        action="store_true",
        help="não adiciona o total de discos",
    )
    tag_group.add_argument(
        "--no-composer-tag",
        action="store_true",
        help="não adiciona a tag do compositor",
    )
    tag_group.add_argument(
        "--no-explicit-tag",
        action="store_true",
        help="não adiciona a tag de conteúdo explícito",
    )
    tag_group.add_argument(
        "--no-copyright-tag",
        action="store_true",
        help="não adiciona a tag de copyright",
    )
    tag_group.add_argument(
        "--no-label-tag",
        action="store_true",
        help="não adiciona a tag da gravadora/selo",
    )
    tag_group.add_argument(
        "--no-upc-tag",
        action="store_true",
        help="não adiciona a tag do UPC/código de barras",
    )
    tag_group.add_argument(
        "--no-isrc-tag", action="store_true", help="não adiciona a tag do ISRC"
    )

    artwork_group = custom_parser.add_argument_group("opções de capa e arte")
    artwork_group.add_argument(
        "-e",
        "--embed-art",
        action="store_true",
        help="embute a capa no arquivo de áudio",
    )
    artwork_group.add_argument(
        "--og-cover", action="store_true", help="baixa a capa na qualidade original"
    )
    artwork_group.add_argument(
        "--no-cover", action="store_true", help="não baixa a arte da capa"
    )
    artwork_group.add_argument(
        "--embedded-art-size",
        choices=["50", "100", "150", "300", "600", "max", "org"],
        default=None,
        help="tamanho da arte embutida (padrão: org, ou a opção embedded_art_size do config.ini)",
    )
    artwork_group.add_argument(
        "--saved-art-size",
        choices=["50", "100", "150", "300", "600", "max", "org"],
        default="org",
        help="tamanho da capa salva no diretório (padrão: org)",
    )

    multiple_disc_group = custom_parser.add_argument_group(
        "opções para múltiplos discos"
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-prefix",
        default="CD",
        metavar="PREFIX",
        help="Define o prefixo para álbuns com múltiplos discos (padrão: CD)",
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-one-dir",
        action="store_true",
        help="armazena todos os lançamentos com discos múltiplos no mesmo diretório base",
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-track-format",
        metavar="FORMAT",
        help="formato da faixa para lançamentos com múltiplos discos",
    )

    parallel_group = custom_parser.add_argument_group("opções de download paralelo")
    parallel_group.add_argument(
        "--max-workers",
        type=int,
        metavar="N",
        help="número máximo de downloads paralelos (padrão: 1 -- sequencial)",
    )
    parallel_group.add_argument(
        "--segment-workers",
        type=int,
        metavar="N",
        help="threads usadas internamente no fallback de download segmentado",
    )


# ----------------------------------------------------------------------------
# Subcomando: "radar"
# ----------------------------------------------------------------------------
def radar_args(subparsers):
    radar = subparsers.add_parser(
        "radar",
        usage="qobuz-dl radar [opções]",
        description="Monitora e intercepta links copiados para download automático.",
        help="inicia o radar em segundo plano",
    )
    return radar


# ----------------------------------------------------------------------------
# Subcomando: "stats"
# ----------------------------------------------------------------------------
def stats_args(subparsers):
    stats = subparsers.add_parser(
        "stats",
        usage="qobuz-dl stats [opções]",
        description="Mostra estatísticas detalhadas sobre sua biblioteca.",
        help="exibe as estatísticas do banco de dados",
    )
    stats.add_argument(
        "--artistas",
        action="store_true",
        help="Mostra a lista completa de todos os artistas únicos baixados.",
    )
    return stats


# ----------------------------------------------------------------------------
# Montagem do parser principal
# ----------------------------------------------------------------------------
def qobuz_dl_args(default_quality=6, default_limit=20, default_folder=None):
    if default_folder is None:
        default_folder = _default_download_folder()

    parser = ColoredArgumentParser(
        prog="qobuz-dl",
        usage="qobuz-dl <comando> [opções]",
        description=(
            "O baixador definitivo de músicas do Qobuz.\nVeja exemplos de uso "
            "em https://github.com/kaduvercosa/qobuz-dl-ultra"
        ),
    )
    parser.add_argument(
        "-r",
        "--reset",
        action="store_true",
        help="cria/reseta o arquivo de configuração",
    )
    parser.add_argument(
        "-p",
        "--purge",
        action="store_true",
        help="apaga o banco de dados de IDs baixados",
    )
    parser.add_argument(
        "--sync-db",
        metavar="PATH",
        nargs="?",
        const="DEFAULT",
        help="escaneia um diretório local para restaurar IDs ausentes do Qobuz no banco de dados",
    )
    parser.add_argument(
        "--find-duplicates",
        metavar="PATH",
        nargs="?",
        const="DEFAULT",
        help=(
            "escaneia o diretório local em busca de faixas duplicadas por impressão digital de áudio "
            "(Chromaprint/AcoustID), não apenas tags"
        ),
    )
    parser.add_argument(
        "--watch",
        metavar="PATH",
        nargs="?",
        const="DEFAULT",
        help=(
            "observa o diretório local e executa a retro-marcação (injeção de letras) automaticamente "
            "sempre que novos arquivos de áudio aparecerem"
        ),
    )
    parser.add_argument(
        "-sc", "--show-config", action="store_true", help="mostra a configuração atual"
    )

    add_output_args(parser)

    subparsers = parser.add_subparsers(
        title="comandos",
        description="rode qobuz-dl <comando> --help para mais opções e detalhes\n(ex. qobuz-dl interactive --help)",
        dest="command",
        parser_class=ColoredArgumentParser,
    )

    interactive = fun_args(subparsers, default_limit)
    download = dl_args(subparsers)
    import_playlist = import_playlist_args(subparsers)
    lucky = lucky_args(subparsers)
    lyrics_cmd = lyrics_args(subparsers, default_folder=default_folder)
    sync_pl_cmd = sync_playlist_args(subparsers)
    radar = radar_args(subparsers)
    stats = stats_args(subparsers)
    auth_cmd = auth_args(subparsers)
    user_cmd = user_args(subparsers)

    for subparser in (interactive, download, lucky, sync_pl_cmd):
        add_common_arg(subparser, default_folder, default_quality)

    for subparser in (
        interactive,
        download,
        import_playlist,
        lucky,
        lyrics_cmd,
        sync_pl_cmd,
        radar,
        stats,
        auth_cmd,
        user_cmd,
    ):
        add_output_args(subparser, suppress=True)

    return parser
