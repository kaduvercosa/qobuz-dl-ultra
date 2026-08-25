"""Camada unica de apresentacao no terminal do qobuz-dl-ultra.

PROBLEMA QUE ESTE MODULO RESOLVE
--------------------------------
Antes deste arquivo existir, a saida do programa vinha de tres canais
totalmente independentes e sem coordenacao nenhuma entre eles:

1. ``print()`` cru        -- cli.py, retro_tagger.py, radar.py, qopy.py, core.py
2. ``logging`` / logger   -- core.py, sync_playlist.py, sync.py, downloader.py
3. ``tqdm.write``         -- lyrics_engine.py, downloader.py (via ``safe_print``)

O ``safe_print()`` (tqdm.write protegido por lock) existia SO' dentro do
downloader.py. Resultado pratico: qualquer ``logger.info`` disparado de
core.py/sync*.py durante um download em andamento escrevia direto no stdout
por cima das barras de progresso do tqdm, picando a barra em varias linhas.

Alem disso, a largura do terminal era recalculada em 7 lugares diferentes,
cada um com teto e fallback proprios (100/90/nenhum, (80,24)/(70,24)/
(100,24)), e os breakpoints "responsivos" eram 60, 115 e ~52 colunas
dependendo do arquivo -- ou seja, a mesma tela ficava em "modo celular"
num painel e em "modo PC" no painel seguinte.

COMO USAR
---------
Um unico ponto de saida::

    from qobuz_dl import ui

    ui.ok("Album baixado")            # [+] verde
    ui.warn("Faixa indisponivel")     # [!] amarelo
    ui.error("Falha de rede")         # [!] vermelho
    ui.step("Buscando letras...")     # [*] cor de destaque
    ui.detail("caminho/do/arquivo")   #     recuado e apagado

    ui.header("ALBUM", [("Artista", "Daft Punk"), ("Faixas", "14")])
    ui.section("ESTATISTICAS")
    ui.kv("Total de downloads", "1234")

E, uma vez no boot da CLI, para que o ``logging`` de TODOS os modulos passe
a respeitar as barras de progresso::

    ui.configure(quiet=args.quiet, verbose=args.verbose, color=not args.no_color)
    ui.install_logging()
"""

import logging
import os
import shutil
import sys
import textwrap
import threading

from qobuz_dl.color import (
    ACCENT_DARK,
    BG,
    ERROR,
    HIGHLIGHT,
    MUTED,
    RESET,
    SUCCESS,
    WARNING,
)

# --------------------------------------------------------------------------
# Lock unico de escrita no terminal
# --------------------------------------------------------------------------
# O downloader.py importa este mesmo objeto como `print_lock`, para que
# exista UM lock para todo o processo em vez de um por modulo. Sem isso,
# duas threads com locks diferentes ainda embaralhariam a saida.
print_lock = threading.Lock()

# --------------------------------------------------------------------------
# Larguras e breakpoints -- fonte unica de verdade
# --------------------------------------------------------------------------
MIN_WIDTH = 32     # piso: pipes/CI as vezes relatam 0 colunas
MAX_WIDTH = 100    # teto: linha longa demais cansa de ler em monitor grande
FALLBACK = (80, 24)

# Breakpoints unificados (antes: 52, 60, 70, 90, 115 espalhados pelo codigo).
NARROW = 60        # celular / a-Shell no iPhone / Split View estreito
MEDIUM = 90        # iPad retrato, terminal de meia tela
# acima de MEDIUM -> "wide" (desktop, iPad paisagem)

LAYOUT_NARROW = "narrow"
LAYOUT_MEDIUM = "medium"
LAYOUT_WIDE = "wide"


def raw_width():
    """Largura real relatada pelo terminal, sem teto nem piso.

    Respeita ``COLUMNS`` quando definida (util em CI e para testes).
    """
    env = os.environ.get("COLUMNS")
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
        except ValueError:
            pass
    try:
        return shutil.get_terminal_size(fallback=FALLBACK).columns
    except Exception:
        return FALLBACK[0]


def width(max_width=MAX_WIDTH, min_width=MIN_WIDTH):
    """Largura utilizavel para desenhar blocos de texto.

    Substitui as 7 implementacoes divergentes que existiam em cli.py,
    core.py, commands.py, retro_tagger.py e color.py.
    """
    return max(min(raw_width(), max_width), min_width)


def layout():
    """Retorna ``narrow`` / ``medium`` / ``wide`` para logica responsiva."""
    cols = raw_width()
    if cols < NARROW:
        return LAYOUT_NARROW
    if cols < MEDIUM:
        return LAYOUT_MEDIUM
    return LAYOUT_WIDE


def is_narrow():
    return layout() == LAYOUT_NARROW


def progress_ncols():
    """Largura para as barras do tqdm.

    Antes ``_get_safe_ncols()`` no downloader.py fazia ``cols - 1`` SEM teto
    nenhum, entao numa janela de 300 colunas a barra ocupava 299 colunas.
    """
    return max(min(raw_width() - 1, MAX_WIDTH), 20)


# --------------------------------------------------------------------------
# Deteccao de capacidade do terminal (cor e unicode)
# --------------------------------------------------------------------------
_color_enabled = None
_unicode_enabled = None
_quiet = False
_verbose = False


def _detect_color():
    """Decide se e' seguro emitir sequencias ANSI.

    Antes o codigo emitia ``\\033[38;2;R;G;Bm`` (truecolor) sem nenhuma
    verificacao, entao ao redirecionar a saida (``qobuz-dl dl ... > log.txt``)
    o arquivo ficava cheio de lixo de escape.
    """
    # Delega para `color._detect_color_capability()` em vez de reimplementar a
    # regra. Antes as duas logicas viviam separadas e podiam divergir: cor
    # ligada aqui e desligada la' (ou o contrario) produzia saida inconsistente
    # dependendo de qual caminho tinha emitido a linha.
    from qobuz_dl.color import _detect_color_capability

    return _detect_color_capability()


def _detect_unicode():
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def color_enabled():
    global _color_enabled
    if _color_enabled is None:
        _color_enabled = _detect_color()
    return _color_enabled


def unicode_enabled():
    global _unicode_enabled
    if _unicode_enabled is None:
        _unicode_enabled = _detect_unicode()
    return _unicode_enabled


def configure(quiet=None, verbose=None, color=None, unicode=None):
    """Ajusta o comportamento global da UI (chamado uma vez, no boot)."""
    global _quiet, _verbose, _color_enabled, _unicode_enabled
    if quiet is not None:
        _quiet = bool(quiet)
    if verbose is not None:
        _verbose = bool(verbose)
    if color is not None:
        _color_enabled = bool(color)
    if unicode is not None:
        _unicode_enabled = bool(unicode)


def c(code):
    """Devolve o codigo ANSI, ou string vazia quando cor esta desligada."""
    return code if color_enabled() else ""


# Glifos com degradacao graciosa para terminais que nao sao UTF-8.
def _glyph(fancy, plain):
    return fancy if unicode_enabled() else plain


def heavy_bar_char():
    return _glyph("\u2501", "=")   # ━


def light_bar_char():
    return _glyph("\u2500", "-")   # ─


def block_char():
    return _glyph("\u2588", "#")   # █


# --------------------------------------------------------------------------
# Saida: um unico caminho para o terminal
# --------------------------------------------------------------------------
def emit(text="", end="\n"):
    """UNICO ponto de escrita no terminal de todo o programa.

    Usa ``tqdm.write`` quando o tqdm esta disponivel, para que as barras de
    progresso ativas sejam apagadas e redesenhadas em volta da mensagem em
    vez de serem picadas ao meio. Sempre protegido pelo lock global.
    """
    if _quiet:
        return
    with print_lock:
        _write_locked(text, end)


def _write_locked(text, end):
    try:
        from tqdm import tqdm

        tqdm.write(str(text), end=end)
    except Exception:
        try:
            print(text, end=end, flush=True)
        except UnicodeEncodeError:
            # Terminal legado (cp437/cp1252): remove o que nao encaixa em
            # vez de derrubar o download inteiro com UnicodeEncodeError.
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(
                str(text).encode(enc, errors="replace").decode(enc),
                end=end,
                flush=True,
            )


def emit_always(text="", end="\n"):
    """Como ``emit``, mas ignora ``--quiet`` (erros fatais, prompts)."""
    with print_lock:
        _write_locked(text, end)


# --------------------------------------------------------------------------
# Mensagens semanticas
# --------------------------------------------------------------------------
# Tamanho de "[x] " -- o recuo das linhas de continuacao alinha o texto sob o
# texto, nao sob a tag.
_LARGURA_TAG = 4


def _tagged(color, tag, message):
    """Monta uma mensagem com tag, quebrada na largura do terminal.

    ANTES devolvia UMA string com a mensagem inteira, sem quebra nenhuma:

        return f"{c(color)}[{tag}]{c(RESET)} {message}"

    Qualquer mensagem maior que o terminal estourava e o terminal quebrava
    onde dava, cortando palavra ao meio. Era inconsistente com `detail()` e
    `wrapped()`, que ja' quebravam -- e as funcoes de tag (`ok`, `warn`,
    `error`, `step`, `skip`) sao as mais usadas do programa.

    Caso real que isso deixava passar: o `stats` num banco vazio imprimia
    "[!] Nenhum dado encontrado. Comece a baixar para popular as
    estatisticas." -- 73 caracteres, estourando em qualquer terminal de 72
    colunas ou menos, o a-Shell no iPad incluso.

    DEPOIS devolve uma LISTA de linhas. Em 40 colunas:

        [!] Nenhum dado encontrado. Comece a
            baixar para popular as
            estatisticas.

    O RESET e' emitido em CADA linha de proposito: o `emit()` escreve uma
    linha por chamada, e sem fechar a cor em cada uma ela vaza para o texto
    seguinte.

    Returns:
        list[str]: as linhas prontas para `emit()`.
    """
    limite = max(width() - _LARGURA_TAG, 12)
    partes = _wrap_lines(message, limite)
    pad = " " * _LARGURA_TAG

    linhas = [f"{c(color)}[{tag}]{c(RESET)} {partes[0]}"]
    linhas += [f"{c(RESET)}{pad}{parte}" for parte in partes[1:]]
    return linhas


def _emit_tagged(color, tag, message, sempre=False):
    """Emite cada linha de `_tagged()` separadamente."""
    escrever = emit_always if sempre else emit
    for linha in _tagged(color, tag, message):
        escrever(linha)


def ok(message):
    """Sucesso -- prefixo ``[+]`` verde."""
    _emit_tagged(SUCCESS, "+", message)


def step(message):
    """Etapa em andamento -- prefixo ``[*]`` na cor de destaque."""
    _emit_tagged(HIGHLIGHT, "*", message)


def info(message):
    """Informacao neutra, sem prefixo colorido."""
    emit(message)


def warn(message):
    """Aviso -- prefixo ``[!]`` amarelo."""
    _emit_tagged(WARNING, "!", message)


def error(message):
    """Erro -- prefixo ``[!]`` vermelho. Ignora ``--quiet``."""
    _emit_tagged(ERROR, "!", message, sempre=True)


def skip(message):
    """Item pulado -- prefixo ``[-]`` apagado."""
    _emit_tagged(MUTED, "-", message)


def detail(message, indent=4):
    """Linha secundaria: hierarquia pelo RECUO, nao pela cor.

    Quebra na largura do terminal em vez de estourar a linha -- dicas de uso
    como "use qobuz-dl stats --artistas ..." passavam de 60 caracteres.

    MUDANCA: antes emitia `{OFF}texto{RESET}`, e como `OFF` era `Style.DIM`
    o texto saia esmaecido POR CIMA da cor vazada da linha anterior --
    resultado ilegivel. Linha secundaria agora e' texto limpo: o recuo ja'
    comunica que e' subordinada, sem custar contraste.
    """
    message = str(message)
    pad = " " * indent
    if len(message) + indent <= width():
        emit(f"{c(RESET)}{pad}{message}")
        return
    for line in _wrap_lines(message, width() - indent):
        emit(f"{c(RESET)}{pad}{line}")


def debug(message):
    """So' aparece com ``--verbose``."""
    if _verbose:
        emit(f"{c(MUTED)}[debug] {message}{c(RESET)}")


def blank():
    emit("")


# --------------------------------------------------------------------------
# Estrutura visual
# --------------------------------------------------------------------------
def rule(char=None, cols=None, color=HIGHLIGHT):
    """Linha divisoria de largura total."""
    ch = char or heavy_bar_char()
    n = cols if cols is not None else width()
    emit(f"{c(color)}{ch * n}{c(RESET)}")


def banner(title, cols=None):
    """Titulo centralizado entre duas linhas grossas.

    Padroniza os blocos que cli.py (stats), core.py (menu de playlist) e
    retro_tagger.py desenhavam cada um a sua maneira.
    """
    n = cols if cols is not None else width()
    bar = heavy_bar_char() * n
    emit(f"\n{c(HIGHLIGHT)}{bar}{c(RESET)}")
    emit(f"{c(BG)}{c(HIGHLIGHT)}{title.center(n)}{c(RESET)}")
    emit(f"{c(HIGHLIGHT)}{bar}{c(RESET)}\n")


def section(title):
    """Subtitulo de bloco dentro de um relatorio."""
    emit(f"  {c(BG)}{title}{c(RESET)}")


def kv(label, value, label_width=30, narrow_stack=False):
    """Par rotulo/valor, sempre dentro da largura do terminal.

    Tres modos, escolhidos pela largura real disponivel:
      * coluna alinhada (padrao em telas medias/largas);
      * rotulo e valor empilhados, com o valor quebrado em varias linhas,
        quando o valor nao cabe na coluna -- e' o caso de listas longas como
        "Sample rates";
      * ``rotulo: valor`` corrido em telas estreitas.

    Antes o valor era simplesmente concatenado, entao qualquer valor longo
    vazava para a linha seguinte sem recuo e desalinhava o bloco todo.
    """
    value = str(value)
    total = width()

    if is_narrow():
        if narrow_stack or len(value) > total - 4 - len(label):
            emit(f"  {c(HIGHLIGHT)}{label}:{c(RESET)}")
            wrapped(value, indent=4)
        else:
            emit(f"  {c(HIGHLIGHT)}{label}:{c(RESET)} {value}")
        return

    # Espaco que sobra para o valor depois de "  " + rotulo + "  ".
    room = total - 2 - label_width - 2
    if len(value) <= room:
        emit(f"  {c(HIGHLIGHT)}{label:<{label_width}}{c(RESET)}  {value}")
    else:
        emit(f"  {c(HIGHLIGHT)}{label}{c(RESET)}")
        wrapped(value, indent=4)


def header(kind, rows):
    """Cabecalho de operacao (album, faixa, playlist, lote de urls).

    Generalizacao de ``downloader.print_download_header`` -- que era o melhor
    padrao visual do projeto, mas vivia preso dentro do downloader e usava
    ``\\033[1m`` hardcoded em vez das constantes de color.py.

    A largura da barra acompanha o conteudo real (piso 20, teto = largura
    utilizavel), entao em telas estreitas ela nao vaza mais para a linha
    seguinte.
    """
    rows = list(rows)
    label_width = max((len(label) for label, _ in rows), default=8)

    header_line = f" [{kind}]"
    row_lines = [
        f" {label.upper():<{label_width}}  {value}" for label, value in rows
    ]
    content_width = max([len(header_line)] + [len(r) for r in row_lines], default=20)
    bar_width = max(20, min(content_width, width()))
    bar = heavy_bar_char() * bar_width

    lines = [
        f"\n{c(HIGHLIGHT)}{bar}{c(RESET)}",
        f"{c(BG)} [{kind}]{c(RESET)}",
        "",
    ]
    for label, value in rows:
        lines.append(
            f" {c(HIGHLIGHT)}{label.upper():<{label_width}}{c(RESET)}  {value}"
        )
    lines.append(f"{c(HIGHLIGHT)}{bar}{c(RESET)}\n")
    emit("\n".join(lines))


def bar_gauge(value, peak, max_blocks=None):
    """Barrinha horizontal proporcional (usada no ranking de artistas).

    ``max_blocks`` deriva da largura real do terminal quando nao e' informado,
    em vez de usar um numero fixo -- era o que fazia o ranking de artistas
    estourar a linha em terminais de ~72 colunas.
    """
    if max_blocks is None:
        max_blocks = max(6, min(24, width() - 56))
    peak = peak or 1
    length = max(1, round(value * max_blocks / peak)) if value else 0
    return f"{c(HIGHLIGHT)}{block_char() * length}{c(RESET)}"


def _wrap_lines(text, limit):
    """``textwrap.wrap`` com piso de largura e sempre >= 1 linha."""
    return textwrap.wrap(str(text), width=max(int(limit), 12)) or [""]


def wrapped(text, indent=0, cols=None):
    """Texto corrido quebrado na largura do terminal, com recuo.

    Emite RESET no inicio de CADA linha de proposito. E' o que garante que
    texto explicativo saia sempre na cor padrao do terminal, sem herdar
    estado de cor de quem imprimiu antes -- foi exatamente isso que deixou
    as descricoes da tela inicial coloridas e esmaecidas. Cor aqui e'
    ruido: quem precisa se destacar e' o comando, nao a explicacao dele.
    """
    n = (cols if cols is not None else width()) - indent
    pad = " " * indent
    for line in _wrap_lines(text, n):
        emit(f"{c(RESET)}{pad}{line}")


def truncate(text, limit):
    """Encurta preservando o inicio, com reticencias."""
    text = str(text)
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3] + "..."


# --------------------------------------------------------------------------
# Ponte com o modulo logging
# --------------------------------------------------------------------------
class TqdmLoggingHandler(logging.Handler):
    """Handler de logging que escreve pelo ``emit()`` deste modulo.

    E' isto que conserta o bug visual antigo: ``logger.info`` de core.py,
    sync.py e sync_playlist.py agora passa pelo mesmo lock e pelo mesmo
    ``tqdm.write`` que o downloader, em vez de escrever direto no stdout
    por cima das barras de progresso ativas.
    """

    def emit(self, record):
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return
        if record.levelno >= logging.ERROR:
            emit_always(msg)
        else:
            emit(msg)


def install_logging(level=None):
    """Troca os handlers da raiz pelo ``TqdmLoggingHandler``.

    Deve ser chamado uma vez, no inicio da CLI, depois de ``configure()``.
    """
    if level is None:
        if _quiet:
            level = logging.WARNING
        elif _verbose:
            level = logging.DEBUG
        else:
            level = logging.INFO

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = TqdmLoggingHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(level)

    # Bibliotecas de rede sao ruidosas em DEBUG; mantem elas um nivel acima.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    return handler


__all__ = [
    "ACCENT_DARK",
    "LAYOUT_MEDIUM",
    "LAYOUT_NARROW",
    "LAYOUT_WIDE",
    "MAX_WIDTH",
    "MEDIUM",
    "MIN_WIDTH",
    "NARROW",
    "TqdmLoggingHandler",
    "banner",
    "bar_gauge",
    "blank",
    "block_char",
    "c",
    "color_enabled",
    "configure",
    "debug",
    "detail",
    "emit",
    "emit_always",
    "error",
    "header",
    "heavy_bar_char",
    "info",
    "install_logging",
    "is_narrow",
    "kv",
    "layout",
    "light_bar_char",
    "ok",
    "print_lock",
    "progress_ncols",
    "raw_width",
    "rule",
    "section",
    "skip",
    "step",
    "truncate",
    "unicode_enabled",
    "warn",
    "width",
    "wrapped",
]
