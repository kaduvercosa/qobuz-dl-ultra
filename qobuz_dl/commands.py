# ============================================================================
# commands.py
# Define TODOS os comandos (subcomandos) e opções de linha de comando (CLI)
# do qobuz-dl-ultra usando argparse.
# ============================================================================

import argparse
import os
import shutil

from qobuz_dl.color import ANSI_BOLD
from qobuz_dl.color import RESET as OFF


# ----------------------------------------------------------------------------
# Formatter customizado do argparse: ajusta a largura do texto de ajuda
# de acordo com o tamanho do terminal (evita quebras de linha feias).
# ----------------------------------------------------------------------------
class CustomHelpFormatter(argparse.RawTextHelpFormatter):
    def __init__(self, prog, indent_increment=2, max_help_position=50, width=None):
        """Initialize formatter with terminal-aware width calculation."""
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
        """Imprime a ajuda em painéis (mesmo estilo da tela inicial)."""
        if file is None:
            import sys

            file = sys.stdout
        try:
            texto = self._ajuda_em_paineis()
        except Exception:
            # Rede de segurança: nunca deixar `-h` quebrar por causa do visual.
            texto = self._ajuda_argparse()
        file.write(texto.lstrip("\n") + "\n")

    # Ajuda em painéis ------------------------------------------------------
    def _ajuda_em_paineis(self):
        from qobuz_dl import ui
        from qobuz_dl.color import HIGHLIGHT, MUTED
        from qobuz_dl.home_screen import EXEMPLOS, render_ajuda_comando

        formatter = self._get_formatter()
        traducao = {
            "positional arguments": "Argumentos",
            "options": "Opções",
            "optional arguments": "Opções",
        }
        grupos = []
        for grupo in self._action_groups:
            itens = []
            for acao in grupo._group_actions:
                if acao.help is argparse.SUPPRESS:
                    continue
                if isinstance(acao, argparse._SubParsersAction):
                    for sub in acao._choices_actions:
                        nomes = [
                            n
                            for n, sp in acao.choices.items()
                            if sp is acao.choices[sub.dest] and n != sub.dest
                        ]
                        rotulo = sub.dest + (f" ({', '.join(nomes)})" if nomes else "")
                        itens.append((rotulo, sub.help or ""))
                    continue
                if acao.option_strings:
                    rotulo = ", ".join(acao.option_strings)
                    if acao.nargs != 0:
                        meta = formatter._format_args(
                            acao, formatter._get_default_metavar_for_optional(acao)
                        )
                        rotulo += f" {meta}"
                else:
                    rotulo = formatter._format_args(
                        acao, formatter._get_default_metavar_for_positional(acao)
                    )
                try:
                    texto = formatter._expand_help(acao) if acao.help else ""
                except Exception:
                    texto = acao.help or ""
                texto = texto.replace(
                    "show this help message and exit",
                    "mostra esta mensagem de ajuda e sai",
                )
                itens.append((rotulo, texto))
            titulo = traducao.get(grupo.title or "", grupo.title or "Opções")
            grupos.append((titulo[:1].upper() + titulo[1:], itens))
        grupos += list(getattr(self, "paineis_extras", ()))

        uso = " ".join(self.format_usage().split())
        for prefixo in ("usage:", "Uso:"):
            if uso.startswith(prefixo):
                uso = uso[len(prefixo) :].strip()
        nome = self.prog.split()[-1]
        exemplo = (self.epilog or "").strip() or EXEMPLOS.get(nome, ("", ""))[1]
        negrito = ui.c(ANSI_BOLD)
        return render_ajuda_comando(
            uso,
            self.description or "",
            grupos,
            exemplo=exemplo,
            largura=ui.width(max_width=10_000, min_width=24),
            cor=bool(negrito),
            destaque=HIGHLIGHT,
            mudo=MUTED,
            reset=OFF,
            negrito=negrito,
        )

    def _ajuda_argparse(self):
        """Ajuda antiga (texto corrido do argparse, traduzida e colorida)."""
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
        for titulo, itens in getattr(self, "paineis_extras", ()):
            help_text += f"\n{titulo}:\n"
            for rotulo, texto in itens:
                help_text += f"  {rotulo}: {texto}\n"
        return help_text


# ----------------------------------------------------------------------------
# Chaves dos padrões de nome (-ff, -fbff, -tf): aparecem num painel próprio no
# fim da ajuda, em vez de lotar a descrição de cada opção.
# ----------------------------------------------------------------------------
CHAVES_PASTA = (
    "album_id, album_url, album_title, album_title_base, album_artist, "
    "album_genre, album_composer, label, copyright, upc, barcode, "
    "release_date, year, release_type, media_type, format, bit_depth, "
    "sampling_rate, album_version, disc_count, track_count"
)
CHAVES_FAIXA = (
    "album_title, album_title_base, album_artist, track_id, track_artist, "
    "track_composer, track_number, isrc, bit_depth, sampling_rate, "
    "track_title, track_title_base, version, year, disc_number, release_date"
)
NOTA_RELEASE_TYPE = (
    "release_type (Album/EP/Single/Compilation/Live) é calculado com correção "
    "de heurística e é o que o padrão usa; media_type vem cru da API da Qobuz "
    "e pode divergir em alguns lançamentos -- prefira release_type."
)
PAINEL_CHAVES = (
    "Chaves de formato",
    [
        ("Pasta (-ff, -fbff)", CHAVES_PASTA),
        ("Faixa (-tf)", CHAVES_FAIXA),
        ("Nota", NOTA_RELEASE_TYPE),
    ],
)


# ----------------------------------------------------------------------------
# Pasta padrão de download.
# ----------------------------------------------------------------------------
def _default_download_folder():
    """Return the default download folder based on environment (iOS-aware)."""
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    if ios_home:
        return os.path.join(ios_home, "QobuzDownloads")
    return "QobuzDownloads"


# ----------------------------------------------------------------------------
# Subcomando: "interactive" (aliases: "i", "fun")
# ----------------------------------------------------------------------------
def fun_args(subparsers, default_limit):
    """Define the 'interactive' subcommand and its arguments."""
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
        metavar="N",
        default=default_limit,
        help=f"limite de resultados da pesquisa (padrão: {default_limit})",
    )
    interactive.add_argument(
        "-sd",
        "--sort-date",
        dest="sort_date",
        action="store_true",
        default=False,
        help="ordena os resultados da busca por data de lançamento, do mais novo ao mais antigo",
    )
    interactive.add_argument(
        "--filter-label",
        metavar="GRAVADORA",
        type=str,
        default=None,
        help="Baixa só lançamentos desta gravadora (nome, ID numérico ou URL da gravadora no Qobuz)",
    )
    return interactive


# ----------------------------------------------------------------------------
# Subcomando: "lucky"
# ----------------------------------------------------------------------------
def lucky_args(subparsers):
    """Define the 'lucky' subcommand and its arguments."""
    lucky = subparsers.add_parser(
        "lucky",
        usage="qobuz-dl lucky [opções] <BUSCA>",
        description="Baixa os primeiros <n> resultados retornados de uma pesquisa no Qobuz.",
        help="modo de download por busca automática",
    )
    lucky.add_argument(
        "-t",
        "--type",
        metavar="TIPO",
        default="album",
        help="tipo de item a pesquisar (artist, album, track, playlist) (padrão: album)",
    )
    lucky.add_argument(
        "-n",
        "--number",
        metavar="N",
        type=int,
        default=1,
        help="número de resultados para baixar (padrão: 1)",
    )
    lucky.add_argument("QUERY", metavar="BUSCA", nargs="+", help="termo de busca")
    return lucky


# ----------------------------------------------------------------------------
# Subcomando: "dl"
# ----------------------------------------------------------------------------
def dl_args(subparsers):
    """Define the 'dl' subcommand and its arguments."""
    download = subparsers.add_parser(
        "dl",
        usage="qobuz-dl dl [opções] <URL_OU_ARQUIVO>",
        description="Baixa por URL de álbum, faixa, artista, gravadora ou playlist do Qobuz, ou um arquivo de texto com uma lista dessas URLs.",
        help="modo de download direto",
    )
    download.add_argument(
        "SOURCE",
        metavar="URL_OU_ARQUIVO",
        nargs="+",
        help=("uma ou mais URLs (separadas por espaço) ou um arquivo de texto"),
    )
    download.add_argument(
        "-b",
        "--blacklist",
        metavar="ARQUIVO",
        help="Caminho para um arquivo de texto contendo palavras-chave para ignorar (blacklist)",
        type=str,
        default=None,
    )
    download.add_argument(
        "--filter-label",
        metavar="GRAVADORA",
        type=str,
        default=None,
        help="Baixa só lançamentos desta gravadora (nome, ID numérico ou URL da gravadora no Qobuz)",
    )
    download.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simula o que seria baixado sem fazer o download de fato.",
    )
    download.add_argument(
        "--since",
        metavar="DATA",
        type=str,
        default=None,
        help="Baixa apenas lançamentos a partir desta data (YYYY-MM-DD ou YYYY).",
    )
    download.add_argument(
        "--before",
        metavar="DATA",
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
        "--no-sentinel",
        action="store_true",
        default=False,
        help="não grava o arquivo .streamrip.json (sentinela de dedup) nas pastas dos álbuns",
    )
    return download


# ----------------------------------------------------------------------------
# Subcomando: "auth" (alias: "login")
# ----------------------------------------------------------------------------
def auth_args(subparsers):
    """Define the 'auth' subcommand and its arguments."""
    auth = subparsers.add_parser(
        "auth",
        usage="qobuz-dl auth [opções]",
        description=(
            "Mostra o perfil, a assinatura, os limites e os dados da conta; com "
            "-l/--login atualiza e-mail e token sem redefinir toda a configuração. "
            "Também responde por user, account, profile, me e info."
        ),
        help="exibe a conta e a assinatura, ou atualiza as credenciais de login",
        aliases=["login", "user", "account", "profile", "me", "info"],
    )
    auth.add_argument(
        "-l",
        "--login",
        action="store_true",
        help="Atualizar e-mail e token de autenticação.",
    )
    auth.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Exibe o JSON bruto retornado pelo endpoint user/get",
    )
    return auth


# ----------------------------------------------------------------------------
# Apelidos de "auth": "user", "account", "profile", "me", "info"
# ----------------------------------------------------------------------------
# O antigo subcomando separado "user" foi unificado ao "auth" (veja auth_args).


# ----------------------------------------------------------------------------
# Subcomando: "lyrics"
# ----------------------------------------------------------------------------
def lyrics_args(subparsers, default_folder=None):
    """Define the 'lyrics' subcommand and its arguments."""
    lyrics = subparsers.add_parser(
        "lyrics",
        usage="qobuz-dl lyrics [opções] [PASTA]",
        description="Escaneia retroativamente um diretório e injeta letras ou traduções ausentes nos arquivos de áudio.",
        help="modo de injeção retroativa de letras",
    )
    lyrics.add_argument(
        "DIR",
        metavar="PASTA",
        nargs="?",
        default=None,
        help=f'O diretório local contendo os arquivos a serem escaneados (padrão: "{default_folder}")',
    )
    lyrics.add_argument(
        "--max-workers",
        type=int,
        metavar="N",
        help=(
            "arquivos processados ao mesmo tempo (padrão: 1 -- sequencial; "
            "também lê max_workers do config.ini). O máximo efetivo é 3, "
            "porque os provedores de letra limitam requisições"
        ),
    )
    lyrics.add_argument(
        "--watch",
        dest="lyrics_watch",
        action="store_true",
        default=False,
        help=(
            "fica observando a PASTA e injeta letras nos arquivos de áudio novos "
            "que aparecerem (precisa do extra: pip install 'qobuz-dl-ultra[watch]'); "
            "é o mesmo que a flag global --watch"
        ),
    )
    # Salvaguardas iguais às do tags: simular, continuar e desfazer.
    lyrics.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help=(
            "SIMULA: consulta as fontes e mostra o que seria feito em cada faixa, "
            "sem gravar letra, .lrc nem progresso (também vale para --undo)"
        ),
    )
    skip = lyrics.add_mutually_exclusive_group()
    skip.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=False,
        help=(
            "continua uma execução interrompida (CTRL+C, queda de rede) e refaz "
            "as faixas adiadas ou com falha: pula as que já foram feitas"
        ),
    )
    skip.add_argument(
        "--incremental",
        dest="incremental",
        action="store_true",
        default=False,
        help=(
            "só processa arquivos novos ou alterados desde a última execução do "
            "lyrics nesta pasta (mais as adiadas ou com falha)"
        ),
    )
    lyrics.add_argument(
        "--no-undo",
        dest="no_undo",
        action="store_true",
        default=False,
        help=(
            "não guarda o estado anterior das letras; sem isso cada execução cria "
            "um registro para desfazer (são mantidas as 5 últimas)"
        ),
    )
    undo = lyrics.add_mutually_exclusive_group()
    undo.add_argument(
        "--undo",
        dest="undo",
        nargs="?",
        const="last",
        default=None,
        metavar="ID",
        help=(
            "desfaz uma execução do lyrics (letras embutidas e .lrc; a mais "
            "recente, ou a do ID dado; veja --undo-list) e não consulta o Qobuz"
        ),
    )
    undo.add_argument(
        "--undo-list",
        dest="undo_list",
        action="store_true",
        default=False,
        help="lista as execuções do lyrics que podem ser desfeitas",
    )
    lyrics.add_argument(
        "--undo-force",
        dest="undo_force",
        action="store_true",
        default=False,
        help="com --undo: restaura mesmo arquivos alterados depois do lyrics",
    )
    return lyrics


# ----------------------------------------------------------------------------
# Subcomando: "tags"
# ----------------------------------------------------------------------------
def tags_args(subparsers, default_folder=None):
    """Define the 'tags' subcommand (retroactive metadata tag correction)."""
    tags = subparsers.add_parser(
        "tags",
        usage="qobuz-dl tags [opções] [PASTA]",
        description=(
            "Escaneia retroativamente um diretório e regrava as tags de metadados "
            "(artista, álbum, compositor, gênero, data, faixa/disco, IDs...) dos "
            "arquivos de áudio que EXISTEM no dispositivo, a partir dos metadados "
            "atuais do Qobuz. Também tenta trocar a capa embutida por uma capa "
            "em alta resolução da Apple/iTunes, mas só quando a correspondência "
            "é validada (sem certeza, a capa atual é mantida). Não mexe em letras. "
            "Escolha o que alterar: --tags-only (só tags), --covers-only (só capas) "
            "ou nenhuma das duas / --both (tags e capas, o padrão). Use --dry-run "
            "para simular sem gravar e --undo para desfazer a última execução."
        ),
        help="modo de correção retroativa de tags de metadados",
    )
    tags.add_argument(
        "DIR",
        metavar="PASTA",
        nargs="?",
        default=None,
        help=f'O diretório local contendo os arquivos a serem corrigidos (padrão: a pasta do config.ini, atualmente "{default_folder}")',
    )
    multi = tags.add_mutually_exclusive_group()
    multi.add_argument(
        "--multi-tags",
        dest="multi_value_tags",
        action="store_true",
        default=argparse.SUPPRESS,
        help="grava artistas, compositores e gêneros como tags múltiplas de verdade (sobrescreve config.ini)",
    )
    multi.add_argument(
        "--no-multi-tags",
        action="store_true",
        help='grava artistas, compositores e gêneros como texto único "A, B" (sobrescreve config.ini)',
    )
    tags.add_argument(
        "--continuous-track-numbers",
        dest="continuous_track_numbers",
        action="store_true",
        default=argparse.SUPPRESS,
        help="grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini)",
    )
    # O que alterar: só tags, só capas ou os dois (padrão).
    what = tags.add_mutually_exclusive_group()
    what.add_argument(
        "--tags-only",
        dest="tags_only",
        action="store_true",
        default=False,
        help=(
            "só regrava as tags de metadados; NÃO consulta a Apple/iTunes e a "
            "capa embutida fica como está"
        ),
    )
    what.add_argument(
        "--covers-only",
        dest="covers_only",
        action="store_true",
        default=False,
        help=(
            "só troca a capa embutida (quando a Apple/iTunes tem uma validada e "
            "de resolução maior); NÃO regrava nenhuma tag de metadados"
        ),
    )
    tags.add_argument(
        "--force-covers",
        dest="force_covers",
        action="store_true",
        default=False,
        help=(
            "troca a capa embutida SEMPRE que a Apple/iTunes devolver uma capa "
            "validada, mesmo que a atual tenha resolução igual ou maior (ou seja "
            "quadrada e a nova não). POR SUA CONTA E RISCO: se a Apple "
            "devolver uma capa de outra edição, ela será gravada. Não vale com "
            "--tags-only"
        ),
    )
    what.add_argument(
        "--both",
        dest="both",
        action="store_true",
        default=False,
        help="regrava as tags E troca a capa quando validada (é o padrão)",
    )
    tags.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=False,
        help=(
            "SIMULA: mostra o diff real de cada arquivo sem gravar nada na sua "
            "pasta (também vale para --undo)"
        ),
    )
    skip = tags.add_mutually_exclusive_group()
    skip.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        default=False,
        help=(
            "continua uma execução interrompida (CTRL+C, queda de rede): pula os "
            "arquivos que ela já tinha terminado"
        ),
    )
    skip.add_argument(
        "--incremental",
        dest="incremental",
        action="store_true",
        default=False,
        help=(
            "só processa arquivos novos ou alterados desde a última execução "
            "do tags nesta pasta (e neste modo)"
        ),
    )
    tags.add_argument(
        "--no-undo",
        dest="no_undo",
        action="store_true",
        default=False,
        help=(
            "não guarda os metadados originais; sem isso cada execução cria um "
            "registro para desfazer (são mantidas as 5 últimas)"
        ),
    )
    undo = tags.add_mutually_exclusive_group()
    undo.add_argument(
        "--undo",
        dest="undo",
        nargs="?",
        const="last",
        default=None,
        metavar="ID",
        help=(
            "desfaz uma execução do tags (a mais recente, ou a do ID dado; veja "
            "--undo-list) e não consulta o Qobuz"
        ),
    )
    undo.add_argument(
        "--undo-list",
        dest="undo_list",
        action="store_true",
        default=False,
        help="lista as execuções do tags que podem ser desfeitas",
    )
    tags.add_argument(
        "--suggest-names",
        dest="suggest_names",
        action="store_true",
        default=False,
        help=(
            "só lê as tags da pasta e sugere artistas com vírgula no nome (como "
            '"Tyler, The Creator") para o artists_with_comma do config.ini; '
            "não consulta o Qobuz nem grava nada"
        ),
    )
    tags.add_argument(
        "--report",
        dest="report",
        nargs="?",
        const="relatorio_biblioteca.html",
        default=None,
        metavar="ARQUIVO",
        help=(
            "só lê a pasta e gera um relatório do que está faltando (sem capa, "
            "sem ISRC, ID Qobuz inválido...): HTML, ou CSV se o nome terminar "
            "em .csv (padrão: relatorio_biblioteca.html); não consulta o Qobuz "
            "nem grava nos arquivos de música"
        ),
    )
    tags.add_argument(
        "--undo-force",
        dest="undo_force",
        action="store_true",
        default=False,
        help="com --undo: restaura mesmo arquivos alterados depois do tags",
    )
    return tags


# ----------------------------------------------------------------------------
# Subcomando: "sync-playlist" (alias: "sp")
# ----------------------------------------------------------------------------
def sync_playlist_args(subparsers):
    """Define the 'sync-playlist' subcommand and its arguments."""
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
    """Define the 'import-playlist' subcommand and its arguments."""
    ip = subparsers.add_parser(
        "import-playlist",
        aliases=["ip"],
        usage="qobuz-dl import-playlist [opções] <URL_OU_ARQUIVO>",
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
        metavar="URL_OU_ARQUIVO",
        help=(
            "URL de playlist (Spotify, Deezer, Apple Music) "
            "ou caminho para arquivo exportado (.txt, .csv, .json)."
        ),
    )
    ip.add_argument(
        "--name",
        "-n",
        metavar="NOME",
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
    """Add output control arguments (--verbose, --quiet, --no-color, --log-level) to the parser."""
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
        "--ui",
        choices=["auto", "compact", "comfortable"],
        default=argparse.SUPPRESS if suppress else "auto",
        help=(
            "modo visual: auto detecta a largura; compact usa cartões para "
            "celular/iPad; comfortable privilegia detalhes"
        ),
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
    """Add common download arguments (quality, directory, formats, tags, etc.) to a subcommand parser."""
    custom_parser.paineis_extras = [PAINEL_CHAVES]
    custom_parser.add_argument(
        "-d",
        "--directory",
        metavar="PASTA",
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
        metavar="QUALIDADE",
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
        metavar="PADRÃO",
        help=(
            'padrão para formatar nomes de pastas, ex: "{album_artist} - '
            '{album_title} ({year}) {{{barcode}}}". Use "/" para criar '
            "subpastas. As chaves disponíveis estão no fim desta ajuda."
        ),
    )
    custom_parser.add_argument(
        "-fbff",
        "--fallback-folder-format",
        metavar="PADRÃO",
        help="""padrão alternativo (fallback) para pastas quando o formato principal falhar.""",
    )
    custom_parser.add_argument(
        "-tf",
        "--track-format",
        metavar="PADRÃO",
        help=(
            'padrão para formatar nomes das faixas, ex: "{track_number} - '
            '{track_title}". Não pode conter caracteres bloqueados pelo '
            "sistema operacional. As chaves disponíveis estão no fim desta ajuda."
        ),
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
            "stdout para interfaces externas (scripts, apps)."
        ),
    )
    custom_parser.add_argument(
        "--check-storage",
        dest="check_storage",
        action="store_true",
        default=argparse.SUPPRESS,
        help=("verifica o espaço livre antes de iniciar cada álbum (padrão)"),
    )
    custom_parser.add_argument(
        "--no-check-storage",
        dest="check_storage",
        action="store_false",
        default=argparse.SUPPRESS,
        help=("desativa a verificação automática de espaço livre antes do álbum"),
    )
    custom_parser.add_argument(
        "--delay",
        metavar="SEGUNDOS",
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
        "--lyrics-word-sync",
        dest="lyrics_word_sync",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "grava letras sincronizadas por palavra (LRC estendido) quando a "
            "fonte oferece (BiniLyrics); a maioria dos players só lê por linha"
        ),
    )
    custom_parser.add_argument(
        "--multi-tags",
        dest="multi_value_tags",
        action="store_true",
        default=argparse.SUPPRESS,
        help="grava artistas, compositores e gêneros como tags múltiplas de verdade: o campo principal continua com vírgula (é o que o player mostra) e os valores separados vão em ARTISTS, ALBUMARTISTS, COMPOSERS e GENRES",
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
        "--musicbrainz",
        action="store_true",
        help=(
            "grava os IDs do MusicBrainz (faixa, álbum, artista) quando título, "
            "versão, álbum e artista conferem; adiciona cerca de 1 s por faixa"
        ),
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
        default=None,
        help="tamanho da capa salva no diretório (padrão: org, ou o valor do config.ini)",
    )

    multiple_disc_group = custom_parser.add_argument_group(
        "opções para múltiplos discos"
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-prefix",
        default=None,
        metavar="PREFIXO",
        help="Define o prefixo para álbuns com múltiplos discos (padrão: CD, ou o valor do config.ini)",
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-one-dir",
        action="store_true",
        help="armazena todos os lançamentos com discos múltiplos no mesmo diretório base",
    )
    multiple_disc_group.add_argument(
        "--continuous-track-numbers",
        dest="continuous_track_numbers",
        action="store_true",
        default=argparse.SUPPRESS,
        help="grava o número da faixa seguido em álbuns com vários discos (1 a 11, em vez de reiniciar em cada disco); DISCNUMBER/DISCTOTAL continuam gravados e o nome do arquivo continua por disco (ou continuous_track_numbers = true no config.ini)",
    )
    multiple_disc_group.add_argument(
        "--multiple-disc-track-format",
        metavar="FORMATO",
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
# Subcomando: "stats"
# ----------------------------------------------------------------------------
def stats_args(subparsers):
    """Define the 'stats' subcommand and its arguments."""
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


def inspect_args(subparsers):
    """Define the 'inspect' subcommand and its arguments."""
    inspect = subparsers.add_parser(
        "inspect",
        usage="qobuz-dl inspect [opções] [ARQUIVO_OU_PASTA]",
        description=(
            "Mostra TUDO sobre uma faixa de áudio local: dados técnicos, tags "
            "organizadas por assunto, capas, letras, um diagnóstico do que falta "
            "corrigir e uma checagem heurística de autenticidade (é genuinamente "
            "lossless/hi-res ou um upsample de fonte lossy?). Sem argumento abre "
            "um navegador de arquivos; com um ARQUIVO inspeciona direto; com uma "
            "PASTA e --resumo, resume todas as faixas dela."
        ),
        help="inspeciona tags, capa, letras e autenticidade de um arquivo de áudio local",
        epilog=None,
    )
    inspect.add_argument(
        "caminho",
        nargs="?",
        default=None,
        metavar="ARQUIVO_OU_PASTA",
        # Fica None quando a pessoa não digita nada: o cli.py então usa a
        # pasta `directory` do config.ini (se configurada e existente) e, na
        # falta dela, run_inspector() detecta a pasta de música pelo
        # dispositivo (_detectar_pasta_padrao() em inspector.py).
        help=(
            "arquivo de áudio (inspeciona direto) ou pasta (onde começar a "
            "navegação; padrão: a pasta do config.ini; sem ela, detecta pelo "
            "dispositivo)"
        ),
    )
    inspect.add_argument(
        "--resumo",
        action="store_true",
        help="resume todas as faixas da pasta (formato, qualidade, capa, problemas) sem abrir o navegador",
    )
    inspect.add_argument(
        "--json",
        action="store_true",
        help="imprime só o JSON do relatório (para scripts); com PASTA, o resumo dela",
    )
    inspect.add_argument(
        "--completo",
        action="store_true",
        help="também decodifica o áudio inteiro (integridade) e mede volume e pico com o ffmpeg",
    )
    inspect.add_argument(
        "--sem-espectro",
        dest="sem_espectro",
        action="store_true",
        help="pula a análise espectral de autenticidade (bem mais rápido, ideal no celular)",
    )
    inspect.add_argument(
        "--comparar",
        action="store_true",
        help="compara o ARQUIVO com o Qobuz e mostra o que o `tags` mudaria (simulação; precisa de login)",
    )
    return inspect


# ----------------------------------------------------------------------------
# Subcomando: "sync-favorites" (alias: "sf")  -- portado da ideia do libsync
# ----------------------------------------------------------------------------
def sync_favorites_args(subparsers):
    """Define the 'sync-favorites' subcommand and its arguments."""
    sf = subparsers.add_parser(
        "sync-favorites",
        aliases=["sf"],
        usage="qobuz-dl sync-favorites [opções]",
        description=(
            "Sincroniza seus álbuns favoritos do Qobuz com o catálogo local "
            "(library.db): mostra o que é novo/removido e, opcionalmente, baixa "
            "o que falta. Dica: rode `qobuz-dl scan` antes na primeira vez para "
            "marcar o que você já tem no disco."
        ),
        help="sincroniza favoritos da conta com o catálogo local",
    )
    sf.add_argument(
        "--download-new",
        action="store_true",
        default=False,
        help="baixa os álbuns favoritados desde a última sincronização",
    )
    sf.add_argument(
        "--download-missing",
        action="store_true",
        default=False,
        help="baixa TODOS os favoritos que ainda não estão completos no disco",
    )
    sf.add_argument(
        "--limit",
        metavar="N",
        type=int,
        default=None,
        help="máximo de álbuns baixados por execução",
    )
    sf.add_argument(
        "--every",
        metavar="MIN",
        type=int,
        default=None,
        help="repete a sincronização a cada MIN minutos (modo contínuo/NAS)",
    )
    sf.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="só mostra o diff (novos/removidos); não grava nem baixa",
    )
    sf.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="não pede confirmação antes de baixar",
    )
    sf.add_argument(
        "--no-sentinel",
        action="store_true",
        default=False,
        help="não grava o arquivo .streamrip.json nas pastas dos álbuns",
    )
    return sf


# ----------------------------------------------------------------------------
# Subcomando: "artists" -- revisão manual de artistas seguidos
# ----------------------------------------------------------------------------
def artist_review_args(subparsers):
    """Define a listagem alfabética e o sorteio de artistas para revisão."""
    artists = subparsers.add_parser(
        "artists",
        aliases=["artist-review"],
        allow_abbrev=False,
        usage=(
            "qobuz-dl artists [--modo alfabetica|sorteio] [--sem-sincronizar] "
            "[--json] [--criar-playlist-vazia --id-artista ID [--abrir-perfil]] "
            "[--sincronizar-checklist [ARQUIVO] [--espelhar] [--atualizar-seguidos]]"
        ),
        description=(
            "Sincroniza artistas seguidos e lançamentos e mostra uma tabela com "
            "checkbox. A criação da playlist pelo Atalhos é separada da conferência. "
            "Também pode espelhar a checklist exportada do Apple Notas no banco local."
        ),
        help="revisa artistas seguidos em ordem alfabética ou por sorteio",
    )
    artists.add_argument(
        "--modo",
        choices=("alfabetica", "sorteio"),
        default="alfabetica",
        help="lista todos em ordem alfabética ou sorteia um artista (padrão: alfabetica)",
    )
    artists.add_argument(
        "--sem-sincronizar",
        action="store_true",
        default=False,
        help="usa o catálogo local sem consultar favoritos e discografias no Qobuz",
    )
    artists.add_argument(
        "--json",
        action="store_true",
        help=(
            "emite os artistas como JSON (id, nome e URL), sem abrir a interface "
            "interativa nem consultar as discografias"
        ),
    )
    artists.add_argument(
        "--sincronizar-checklist",
        nargs="?",
        const=True,
        default=False,
        metavar="ARQUIVO",
        help=(
            "traz para o banco o que está marcado na checklist do Apple Notas; sem "
            "ARQUIVO usa tracklist-artists.txt junto ao config.ini. Só marca por "
            "padrão e não exige login"
        ),
    )
    artists.add_argument(
        "--espelhar",
        action="store_true",
        default=False,
        help=(
            "com --sincronizar-checklist: a Nota manda; reabre quem está conferido "
            "no banco mas desmarcado na Nota"
        ),
    )
    artists.add_argument(
        "--atualizar-seguidos",
        action="store_true",
        default=False,
        help=(
            "com --sincronizar-checklist: antes, atualiza a lista de seguidos pela "
            "API (exige login)"
        ),
    )
    artists.add_argument(
        "--criar-playlist-vazia",
        action="store_true",
        help=(
            "cria uma playlist privada e vazia com o nome do artista em maiúsculas; "
            "requer --id-artista"
        ),
    )
    artists.add_argument(
        "--id-artista",
        metavar="ID",
        help="ID Qobuz do artista para --criar-playlist-vazia",
    )
    artists.add_argument(
        "--abrir-perfil",
        action="store_true",
        help="abre o perfil do artista depois de criar a playlist vazia",
    )
    return artists


# ----------------------------------------------------------------------------
# Subcomando: "scan"  -- casa pastas do disco com o catálogo (ideia do libsync)
# ----------------------------------------------------------------------------
def scan_args(subparsers):
    """Define the 'scan' subcommand and its arguments."""
    scan = subparsers.add_parser(
        "scan",
        aliases=["library-scan"],
        usage="qobuz-dl scan [opções] [PASTA]",
        description=(
            "Varre uma pasta de música e casa cada álbum com o catálogo local "
            "(por tag QOBUZALBUMID, UPC, nome e fuzzy), marcando como baixado o "
            "que for certo e perguntando sobre os duvidosos. Funciona offline."
        ),
        help="reconcilia sua pasta de música com o catálogo de favoritos",
    )
    scan.add_argument(
        "DIR",
        metavar="PASTA",
        nargs="?",
        default=None,
        help="pasta a varrer (padrão: diretório de downloads)",
    )
    scan.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="classifica e reporta sem gravar nada",
    )
    scan.add_argument(
        "--no-sentinel",
        action="store_true",
        default=False,
        help="não grava .streamrip.json (use em montagens somente-leitura)",
    )
    scan.add_argument(
        "--no-adopt",
        action="store_true",
        default=False,
        help="não adiciona ao catálogo pastas com tag de ID que não estão nos favoritos",
    )
    scan.add_argument(
        "--no-review",
        action="store_true",
        default=False,
        help="não pergunta sobre os casos duvidosos",
    )
    scan.add_argument(
        "--rescan",
        action="store_true",
        default=False,
        help="reexamina também pastas que já têm sentinela",
    )
    scan.add_argument(
        "--max-depth",
        type=int,
        default=4,
        metavar="N",
        help="profundidade máxima de pastas (padrão: 4)",
    )
    scan.add_argument(
        "--fuzzy-threshold",
        type=float,
        default=0.85,
        metavar="0-1",
        help="similaridade mínima para sugerir um candidato (padrão: 0.85)",
    )
    scan.add_argument(
        "--json",
        metavar="ARQUIVO",
        default=None,
        help="salva o relatório completo em JSON",
    )
    return scan


# ----------------------------------------------------------------------------
# Subcomando: "library" (alias: "lib")
# ----------------------------------------------------------------------------
LIBRARY_ACTIONS = [
    "status",
    "missing",
    "list",
    "history",
    "reconcile",
    "reset-stuck",
    "unmark",
]


def library_args(subparsers):
    """Define the 'library' subcommand and its arguments."""
    lib = subparsers.add_parser(
        "library",
        aliases=["lib"],
        usage="qobuz-dl library [ação] [opções]",
        description=(
            "Consulta e mantém o catálogo local. Ações: status (padrão), missing "
            "(favoritos não baixados), list, history (sincronizações), reconcile "
            "(sentinelas do disco ⇄ catálogo), reset-stuck, unmark <ID>."
        ),
        help="status e manutenção do catálogo local (library.db)",
    )
    lib.add_argument(
        "action",
        nargs="?",
        default="status",
        choices=LIBRARY_ACTIONS,
        metavar="ação",
        help="uma de: " + ", ".join(LIBRARY_ACTIONS),
    )
    lib.add_argument(
        "TARGET",
        metavar="ALVO",
        nargs="?",
        default=None,
        help="pasta (reconcile) ou ID do álbum (unmark)",
    )
    lib.add_argument(
        "--limit", type=int, default=None, metavar="N", help="limita a listagem"
    )
    lib.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="reconcile: devolve a not_downloaded o que sumiu do disco",
    )
    lib.add_argument(
        "--dry-run", action="store_true", default=False, help="reconcile: só reporta"
    )
    return lib


# ----------------------------------------------------------------------------
# Subcomando: "organize" (alias: "rename")
# ----------------------------------------------------------------------------
def organize_args(subparsers):
    """Define a reorganização local de arquivos já baixados."""
    organize = subparsers.add_parser(
        "organize",
        aliases=["rename"],
        usage="qobuz-dl organize [opções] [PASTA]",
        description=(
            "Reaplica os formatos de pasta e faixa aos arquivos locais, sem "
            "consultar o Qobuz nem alterar áudio ou tags. Use --dry-run para "
            "uma prévia somente leitura e --show-files para ver cada caminho. "
            "Destinos já existentes não são sobrescritos. A pasta Playlist é "
            "preservada por padrão; --include-playlists a inclui. Ao mudar a "
            "pasta de um álbum, os demais arquivos dela, inclusive ocultos, "
            "acompanham as faixas. O journal permite --undo, mas não é backup "
            "dos arquivos: faça uma cópia independente antes da execução real."
        ),
        help="reaplica os formatos na biblioteca local (com prévia e undo)",
    )
    organize.add_argument(
        "DIR",
        metavar="PASTA",
        nargs="?",
        default=None,
        help="biblioteca a reorganizar (padrão: directory do config.ini)",
    )
    organize.add_argument(
        "-ff",
        "--folder-format",
        dest="folder_format",
        default=None,
        metavar="FORMATO",
        help="formato de pasta a aplicar (padrão: folder_format do config.ini)",
    )
    organize.add_argument(
        "-tf",
        "--track-format",
        dest="track_format",
        default=None,
        metavar="FORMATO",
        help="formato de faixa a aplicar (padrão: track_format do config.ini)",
    )
    organize.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="prévia somente leitura; não move arquivos nem cria journal",
    )
    organize.add_argument(
        "--show-files",
        action="store_true",
        default=False,
        help="com --dry-run, lista cada arquivo e os caminhos relativos",
    )
    organize.add_argument(
        "--include-playlists",
        action="store_true",
        default=False,
        help="inclui a pasta Playlist (por padrão, ela é preservada)",
    )
    organize.add_argument(
        "--no-undo",
        action="store_true",
        default=False,
        help="não registra movimentos para --undo (não recomendado)",
    )
    undo = organize.add_mutually_exclusive_group()
    undo.add_argument(
        "--undo",
        nargs="?",
        const="last",
        metavar="ID",
        default=None,
        help="restaura a última execução ou a execução informada no journal",
    )
    undo.add_argument(
        "--undo-list",
        dest="undo_list",
        action="store_true",
        default=False,
        help="lista as execuções registradas disponíveis para desfazer",
    )
    return organize


# ----------------------------------------------------------------------------
# Subcomando: "doctor" (alias: "check")  -- diagnóstico (ideia do libsync)
# ----------------------------------------------------------------------------
def doctor_args(subparsers):
    """Define the 'doctor' subcommand and its arguments."""
    doc = subparsers.add_parser(
        "doctor",
        aliases=["check"],
        usage="qobuz-dl doctor [--json]",
        description=(
            "Diagnostica ambiente, config, keyring, bancos e sentinelas. Somente "
            "leitura, sem rede, e nunca mostra segredos."
        ),
        help="verifica a saúde da instalação e da biblioteca",
    )
    doc.add_argument("--json", action="store_true", default=False, help="saída em JSON")
    return doc


# ----------------------------------------------------------------------------
# Montagem do parser principal
# ----------------------------------------------------------------------------
def qobuz_dl_args(default_quality=6, default_limit=20, default_folder=None):
    """Build and return the main ArgumentParser with all subcommands and global flags."""
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
        metavar="PASTA",
        nargs="?",
        const="DEFAULT",
        help="escaneia um diretório local para restaurar IDs ausentes do Qobuz no banco de dados",
    )
    parser.add_argument(
        "--find-duplicates",
        metavar="PASTA",
        nargs="?",
        const="DEFAULT",
        help=(
            "escaneia o diretório local em busca de faixas duplicadas por impressão digital de áudio "
            "(Chromaprint/AcoustID), não apenas tags"
        ),
    )
    parser.add_argument(
        "--watch",
        metavar="PASTA",
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
    tags_cmd = tags_args(subparsers, default_folder=default_folder)
    sync_pl_cmd = sync_playlist_args(subparsers)
    stats = stats_args(subparsers)
    inspect_cmd = inspect_args(subparsers)
    auth_cmd = auth_args(subparsers)
    sync_fav_cmd = sync_favorites_args(subparsers)
    artists_cmd = artist_review_args(subparsers)
    scan_cmd = scan_args(subparsers)
    library_cmd = library_args(subparsers)
    organize_cmd = organize_args(subparsers)
    doctor_cmd = doctor_args(subparsers)

    for subparser in (interactive, download, lucky, sync_pl_cmd, sync_fav_cmd):
        add_common_arg(subparser, default_folder, default_quality)

    for subparser in (
        interactive,
        download,
        import_playlist,
        lucky,
        lyrics_cmd,
        tags_cmd,
        sync_pl_cmd,
        stats,
        inspect_cmd,
        auth_cmd,
        sync_fav_cmd,
        artists_cmd,
        scan_cmd,
        library_cmd,
        organize_cmd,
        doctor_cmd,
    ):
        add_output_args(subparser, suppress=True)

    return parser
