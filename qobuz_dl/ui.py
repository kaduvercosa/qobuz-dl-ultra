# Camada única de apresentação no terminal do qobuz-dl-ultra.
#
# Centraliza tudo que é escrito na tela em UM ponto de saída (emit()),
# protegido por UM lock (print_lock) e com UMA única fonte de verdade para
# largura/breakpoints do terminal. Isso evita que:
#   - print() cru, logger.info() e tqdm.write() disputem o stdout ao mesmo
#     tempo e cortem as barras de progresso do tqdm no meio;
#   - cada arquivo recalcule a largura do terminal com teto/fallback
#     diferentes, fazendo a mesma tela parecer "celular" num painel e "PC"
#     no outro.
#
# Uso básico:
#   from qobuz_dl import ui
#   ui.ok("Album baixado")            -> [+] verde
#   ui.warn("Faixa indisponivel")     -> [!] amarelo
#   ui.error("Falha de rede")         -> [!] vermelho
#   ui.step("Buscando letras...")     -> [*] cor de destaque
#   ui.detail("caminho/do/arquivo")   -> linha secundária recuada
#   ui.header("ALBUM", [("Artista", "Daft Punk"), ("Faixas", "14")])
#   ui.section("ESTATISTICAS")
#   ui.kv("Total de downloads", "1234")
#
# No boot da CLI, para que o `logging` de todos os módulos também respeite
# as barras de progresso do tqdm:
#   ui.configure(quiet=args.quiet, verbose=args.verbose, color=not args.no_color)
#   ui.install_logging()

import contextvars
import logging
import os
import re
import shutil
import sys
import threading
import unicodedata

from qobuz_dl.color import (
    ACCENT_DARK,
    BG,
    ERROR,
    GRAY,
    HIGHLIGHT,
    MUTED,
    RESET,
    SUCCESS,
    WARNING,
)

# --------------------------------------------------------------------------
# Lock único de escrita no terminal
# --------------------------------------------------------------------------
# downloader.py importa este mesmo objeto como `print_lock` -- existe um
# único lock para todo o processo, não um por módulo. Threads diferentes
# usando locks diferentes ainda embaralhariam a saída entre si.
print_lock = threading.Lock()

# --------------------------------------------------------------------------
# Larguras e breakpoints -- fonte única de verdade para todo o projeto
# --------------------------------------------------------------------------
MIN_WIDTH = 32  # piso: pipes/CI às vezes relatam 0 colunas
MAX_WIDTH = 100  # teto: linha longa demais cansa de ler em monitor grande
FALLBACK = (80, 24)

# Breakpoints responsivos unificados.
NARROW = 60  # celular / a-Shell no iPhone / Split View estreito
MEDIUM = 90  # iPad retrato, terminal de meia tela
COMPACT_WIDTH = 72  # abaixo/igual: atalhos e metadados compactos na TUI
# acima de MEDIUM -> "wide" (desktop, iPad paisagem)

# Override opcional para terminais móveis. ``None`` preserva a detecção automática
# pela largura; ``compact`` força cartões/atalhos e ``comfortable`` privilegia
# detalhes mesmo quando o terminal informa poucas colunas.
_ui_mode = None

LAYOUT_NARROW = "narrow"
LAYOUT_MEDIUM = "medium"
LAYOUT_WIDE = "wide"


def raw_width():
    """Return the current terminal width without clamping.

    Prefer the live TTY size so resizing a terminal is reflected immediately.
    For redirected output and tests, honor ``COLUMNS`` before using a fallback.
    """
    try:
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            return os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError, AttributeError):
        pass

    env = os.environ.get("COLUMNS")
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    try:
        return shutil.get_terminal_size(fallback=FALLBACK).columns
    except Exception:
        return FALLBACK[0]


def width(max_width=MAX_WIDTH, min_width=MIN_WIDTH):
    # Largura utilizável para desenhar blocos de texto, já com teto e piso
    # aplicados. Função única que substitui os cálculos redundantes que
    # existiam espalhados por vários arquivos do projeto.
    """Return terminal width clamped to the requested minimum and maximum."""
    return max(min(raw_width(), max_width), min_width)


def layout():
    # Classifica a largura atual do terminal em narrow/medium/wide, para
    # que outras funções decidam como se adaptar (empilhar valor, quebrar
    # linha, etc.).
    """Return the current layout mode ('narrow' or 'wide') based on terminal width."""
    if _ui_mode == "compact":
        return LAYOUT_NARROW
    if _ui_mode == "comfortable":
        return LAYOUT_WIDE
    cols = raw_width()
    if cols < NARROW:
        return LAYOUT_NARROW
    if cols < MEDIUM:
        return LAYOUT_MEDIUM
    return LAYOUT_WIDE


def is_narrow():
    # Atalho para checagens rápidas de "estou em tela estreita?".
    """Check whether the terminal is below the narrow-layout breakpoint."""
    return layout() == LAYOUT_NARROW


def compact_mode(columns=None):
    """Return whether dense/card rendering should be used."""
    if _ui_mode == "compact":
        return True
    if _ui_mode == "comfortable":
        return False
    return (raw_width() if columns is None else columns) <= COMPACT_WIDTH


def progress_ncols():
    # Largura usada pelas barras de progresso do tqdm.
    # Com teto em MAX_WIDTH: sem isso, numa janela muito larga a barra de
    # progresso ocuparia a tela inteira.
    """Calculate optimal width for progress bars based on terminal size."""
    return max(min(raw_width() - 1, MAX_WIDTH), 20)


# --------------------------------------------------------------------------
# Detecção de capacidade do terminal (cor e unicode)
# --------------------------------------------------------------------------
# Cache module-level: a detecção só roda uma vez por processo, não a cada
# chamada de emit().
_color_enabled = None
_unicode_enabled = None
_quiet = False
_verbose = False


def _detect_color():
    # Decide se é seguro emitir sequências ANSI de cor.
    # Delega para color._detect_color_capability() em vez de reimplementar
    # a regra aqui -- assim as duas partes do projeto nunca divergem sobre
    # quando a cor deve estar ligada ou desligada (ex.: saída redirecionada
    # para um arquivo com `> log.txt`).
    """Detect if color output should be enabled based on environment and TTY."""
    from qobuz_dl.color import _detect_color_capability

    return _detect_color_capability()


def _detect_unicode():
    # Assume unicode disponível se a codificação do stdout contiver "utf".
    """Detect if Unicode glyphs are supported by the terminal."""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def color_enabled():
    # Getter com cache preguiçoso (lazy) para o resultado de _detect_color().
    """Check if color output is currently enabled."""
    global _color_enabled
    if _color_enabled is None:
        _color_enabled = _detect_color()
    return _color_enabled


def unicode_enabled():
    # Getter com cache preguiçoso para o resultado de _detect_unicode().
    """Check if Unicode output is currently enabled."""
    global _unicode_enabled
    if _unicode_enabled is None:
        _unicode_enabled = _detect_unicode()
    return _unicode_enabled


def configure(quiet=None, verbose=None, color=None, unicode=None, ui_mode=None):
    # Ponto único de ajuste do comportamento global da UI. Chamado uma vez,
    # no boot da CLI, com os valores vindos dos argumentos de linha de
    # comando. Parâmetros None são ignorados (mantêm o valor atual).
    """Configure UI system (color, Unicode, quiet/verbose modes)."""
    global _quiet, _verbose, _color_enabled, _unicode_enabled, _ui_mode
    if quiet is not None:
        _quiet = bool(quiet)
    if verbose is not None:
        _verbose = bool(verbose)
    if color is not None:
        _color_enabled = bool(color)
    if unicode is not None:
        _unicode_enabled = bool(unicode)
    if ui_mode is not None:
        if ui_mode not in ("auto", "compact", "comfortable"):
            raise ValueError(f"modo de UI inválido: {ui_mode}")
        _ui_mode = None if ui_mode == "auto" else ui_mode


def c(code):
    # Devolve o código ANSI passado, ou string vazia quando a cor está
    # desligada. Usado em toda função de emit para envolver texto com cor
    # sem precisar de um `if color_enabled()` em cada chamada.
    """Apply color escape sequence if color is enabled, otherwise return empty string."""
    return code if color_enabled() else ""


def _glyph(fancy, plain):
    # Escolhe entre o glifo unicode "bonito" e o equivalente ASCII simples,
    # dependendo do suporte detectado do terminal.
    """Return a Unicode glyph if supported, otherwise return ASCII fallback."""
    return fancy if unicode_enabled() else plain


def heavy_bar_char():
    # Caractere usado nas barras/separadores grossos (títulos, banners).
    """Return the heavy bar character for progress bars (Unicode-aware)."""
    return _glyph("\u2501", "=")  # ━


def light_bar_char():
    # Caractere usado em separadores finos.
    """Return the light bar character for progress bars (Unicode-aware)."""
    return _glyph("\u2500", "-")  # ─


def block_char():
    # Caractere usado para preencher barras de progresso proporcionais
    # (ex.: bar_gauge no ranking de artistas).
    """Return the block character for bar gauges (Unicode-aware)."""
    return _glyph("\u2588", "#")  # █


# --------------------------------------------------------------------------
# Saída: um único caminho para o terminal
# --------------------------------------------------------------------------
# Linha de status "em andamento" (ex.: "[*] Logando...") deixada SEM quebra de
# linha. A proxima escrita pelo emit()/emit_always() apaga essa linha e escreve
# por cima, fazendo "Logando..." virar "Logado: OK" na mesma linha.
_pending_status = False


def _status_inline_supported():
    # So' sobrescreve em terminal interativo; em pipe/CI/arquivo cada mensagem
    # continua em sua propria linha (sem \r nem codigos ANSI no log).
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _clear_pending_locked():
    # Chamado dentro do print_lock: apaga a linha de status pendente.
    global _pending_status
    if _pending_status:
        _pending_status = False
        try:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
        except Exception:
            pass


def step_inline(message):
    """Print a progress step WITHOUT newline; the next message replaces it."""
    if _quiet:
        return
    if not _status_inline_supported():
        step(message)
        return
    global _pending_status
    with print_lock:
        _clear_pending_locked()
        linha = _tagged(HIGHLIGHT, "*", message)[0]
        _write_locked(linha, "")
        try:
            sys.stdout.flush()
        except Exception:
            pass
        _pending_status = True


def emit(text="", end="\n"):
    # ÚNICO ponto de escrita no terminal usado pelo restante do módulo (e,
    # por extensão, por todo o programa via as funções ok/warn/error/etc.).
    # Usa tqdm.write() quando disponível, para que barras de progresso
    # ativas sejam apagadas e redesenhadas ao redor da mensagem em vez de
    # serem cortadas ao meio. Respeita --quiet (não imprime nada).
    """Print a message unless quiet mode is active."""
    if _quiet:
        return
    with print_lock:
        _clear_pending_locked()
        _write_locked(text, end)
        _registrar_saida(text, end)


# True quando a última escrita por emit()/emit_always() terminou numa linha em
# branco. Serve só a `separator()`, para não empilhar duas linhas em branco.
_ultima_linha_vazia = False


def _registrar_saida(text, end):
    global _ultima_linha_vazia
    _ultima_linha_vazia = end == "\n" and (str(text) == "" or str(text).endswith("\n"))


def separator():
    # Linha em branco entre seções (ex.: preparação -> faixas). Não empilha:
    # se a última saída já terminou em linha em branco (como a caixa do
    # cabeçalho), não escreve outra. Sem lock aqui: emit() já o pega e o
    # print_lock não é reentrante.
    """Print a blank line between sections, never two in a row."""
    if not _ultima_linha_vazia:
        emit("")


def _write_locked(text, end):
    # Implementação real da escrita, já dentro do print_lock.
    # Tenta tqdm.write primeiro; se tqdm não estiver disponível, cai para
    # print() comum; se o terminal não suportar os caracteres unicode do
    # texto, reescreve substituindo o que não encaixa em vez de derrubar o
    # processo com UnicodeEncodeError.
    """Thread-safe write to stdout with optional flushing."""
    try:
        from tqdm import tqdm

        tqdm.write(str(text), end=end)
    except Exception:
        try:
            print(text, end=end, flush=True)
        except UnicodeEncodeError:
            enc = getattr(sys.stdout, "encoding", None) or "ascii"
            print(
                str(text).encode(enc, errors="replace").decode(enc),
                end=end,
                flush=True,
            )


def emit_always(text="", end="\n"):
    # Igual a emit(), mas ignora --quiet. Reservado para mensagens que o
    # usuário precisa ver mesmo em modo silencioso: erros fatais e prompts
    # de confirmação.
    """Print a message regardless of quiet mode (for critical output)."""
    with print_lock:
        _clear_pending_locked()
        _write_locked(text, end)
        _registrar_saida(text, end)


_track_index = contextvars.ContextVar("ui_track_index", default="")
_INDEX_AFTER_LABEL = re.compile(r"(: )\[\d+(?:[./]\d+)?\]\s+")


def format_index(track, total=None, disc=None, disc_total=1, playlist=False):
    """01/18 (disco único) | 02.05 (vários CDs: disco.faixa) | 05 (playlist)."""
    try:
        t = int(track)
    except (TypeError, ValueError):
        return ""
    try:
        multi = int(disc_total or 1) > 1 and disc is not None
    except (TypeError, ValueError):
        multi = False
    if multi:
        return f"{int(disc):02d}.{t:02d}"
    if playlist or not total:
        return f"{t:02d}"
    try:
        return f"{t:02d}/{int(total):02d}"
    except (TypeError, ValueError):
        return f"{t:02d}"


def index_from_meta(track_meta, album_meta=None, display_index=None):
    track_meta = track_meta or {}
    album_meta = album_meta or {}
    nested = track_meta.get("album") or {}
    total = album_meta.get("tracks_count") or nested.get("tracks_count")
    discs = album_meta.get("media_count") or nested.get("media_count") or 1
    if display_index:
        return format_index(display_index, playlist=True)
    return format_index(
        track_meta.get("track_number"), total, track_meta.get("media_number"), discs
    )


def set_track_index(value):
    return _track_index.set(value or "")


def reset_track_index(token):
    try:
        _track_index.reset(token)
    except ValueError:
        pass


def run_in_executor(loop, executor, fn, *args):
    """run_in_executor que PRESERVA o contexto (o do asyncio puro não preserva)."""
    ctx = contextvars.copy_context()
    return loop.run_in_executor(executor, ctx.run, fn, *args)


def faint(text):
    """Cinza apagado (métricas), mais escuro que o dim()."""
    return f"{c(GRAY)}{text}{c(RESET)}"


# --------------------------------------------------------------------------
# Mensagens semânticas (linhas com tag colorida: [+], [!], [*], [-])
# --------------------------------------------------------------------------
# Tamanho de "[x] " -- o recuo das linhas de continuação alinha o texto sob
# o texto da primeira linha, não sob a tag.
_LARGURA_TAG = 4


# Presentation state is task-local; the same dict is shared with its lyric worker.
_track_view = contextvars.ContextVar("ui_track_view", default=None)


def begin_track_view(parallel=False):
    return _track_view.set(
        {"parallel": bool(parallel), "audio": "", "lyrics": [], "existing": False}
    )


def end_track_view(token):
    _track_view.reset(token)


def finish_track_view(title, success):
    state = _track_view.get()
    if not state or not success or state["existing"]:
        return
    details = join_meta(state["audio"], *state["lyrics"]) if state["parallel"] else ""
    label = "Concluído:" if state["parallel"] else f"{tree_last()} Concluído:"
    _emit_kind("done", tail(f"{label} {title}", details))


def _tagged(color, tag, message):
    idx = _track_index.get()
    marca = f"[{idx}]" if idx else f"[{tag}]"
    largura_marca = _cell_width(marca) + 1
    limite = max(width() - largura_marca, 12)
    texto = str(message).strip()
    # RESULT_TREE_AND_GRAY_INDEX
    # Results are children of the operation that produced them.
    ramo = tree_last()
    label = texto.partition(_TAIL_MARK)[0]
    result_labels = (
        "Concluído:",
        "Concluída:",
        "Áudio transferido",
        "Letras prontas:",
        "Nenhuma letra encontrada:",
        "Falha ao gravar letras",
        "Erro na pesquisa de letras",
        "Letras:",
    )
    if label.startswith(result_labels):
        texto = f"{ramo} {texto}"
    # Keep both lyric stages and their notes in the same neutral gray.
    plain_label = label
    for branch in (tree_last(), tree_mid()):
        if plain_label.startswith(branch):
            plain_label = plain_label[len(branch) :].lstrip()
    if plain_label.startswith(
        (
            "Procurando letras:",
            "Letras prontas:",
            "Letras:",
            "Nenhuma letra encontrada:",
        )
    ):
        color = GRAY
    if idx:
        texto = _INDEX_AFTER_LABEL.sub(r"\1", texto, count=1)
    cabeca, sep, cauda = texto.partition(_TAIL_MARK)
    cabeca = cabeca.strip()
    if sep:
        cauda = cauda.strip()
        if _cell_width(cabeca) + len(_TAIL_GAP) + _cell_width(cauda) <= limite:
            partes = [f"{cabeca}{_TAIL_GAP}{faint(cauda)}"]
        else:
            partes = _wrap_lines(cabeca, limite)
            if _cell_width(partes[-1]) + len(_TAIL_GAP) + _cell_width(cauda) <= limite:
                partes[-1] = f"{partes[-1]}{_TAIL_GAP}{faint(cauda)}"
            else:
                partes.extend(faint(p) for p in _wrap_lines(cauda, limite))
    elif "\n" not in texto and _cell_width(texto) <= limite:
        partes = [texto]
    else:
        partes = _wrap_lines(texto, limite)
    pad = " " * largura_marca
    reset = c(RESET)
    cor = c(color)
    if reset and cor:
        partes = [p.replace(reset, reset + cor) for p in partes]
    if idx:
        primeira = f"{c(GRAY)}{marca}{reset} {cor}{partes[0]}{reset}"
    else:
        primeira = f"{cor}{marca} {partes[0]}{reset}"
    linhas = [primeira]
    linhas += [f"{pad}{cor}{parte}{reset}" for parte in partes[1:]]
    return linhas


_KINDS = {
    "step": (HIGHLIGHT, "*"),
    "ok": (SUCCESS, "+"),
    "skip": (MUTED, "-"),
    "warn": (WARNING, "!"),
    "error": (ERROR, "!"),
    "progress": (HIGHLIGHT, "*"),  # Em Progresso
    "search": (GRAY, "*"),  # Procurando letras
    "done": (SUCCESS, "+"),  # Concluído
    "lyrics": (GRAY, "+"),
    "lyrics_note": (GRAY, "-"),  # └─ Letras prontas
}


def tagged_lines(kind, message):
    # Devolve as linhas já formatadas (tag colorida, quebra, métricas em cinza)
    # SEM escrevê-las. Para código que roda dentro do `print_lock` (que não é
    # reentrante) e por isso não pode chamar ok()/step()/... sem travar.
    """Return formatted lines for kind in step/ok/skip/warn/error (no output)."""
    state = _track_view.get()
    if state and state["parallel"]:
        if kind == "search":
            return []
        if kind in {"lyrics", "lyrics_note"}:
            head, sep, detail = str(message).partition(_TAIL_MARK)
            if detail:
                prefix = "letras: " if kind == "lyrics" else ""
                state["lyrics"].append(prefix + detail.strip())
            return []
    if state and not state["parallel"] and kind in {"lyrics", "lyrics_note"}:
        message = str(message).replace(tree_last(), tree_mid(), 1)
    if state and not state["parallel"] and kind == "search":
        # "Procurando letras" é um filho da faixa, como "Áudio transferido".
        message = f"{tree_mid()} {message}"
    color, tag = _KINDS[kind]
    return _tagged(color, tag, message)


def _emit_tagged(color, tag, message, sempre=False):
    # Emite cada linha produzida por _tagged() separadamente, escolhendo
    # entre emit() e emit_always() conforme o parâmetro `sempre`.
    """Emit a tagged message with consistent formatting."""
    escrever = emit_always if sempre else emit
    for linha in _tagged(color, tag, message):
        escrever(linha)


def ok(message):
    # Mensagem de sucesso -- prefixo "[+]" verde.
    """Print a success message (green checkmark)."""
    _emit_tagged(SUCCESS, "+", message)


def _emit_kind(kind, message):
    color, tag = _KINDS[kind]
    _emit_tagged(color, tag, message)


def progress(message):
    if _track_view.get():
        message = str(message).replace("Em Progresso:", "Baixando:", 1)
    _emit_kind("progress", message)


def search(message):
    state = _track_view.get()
    if state and not state["parallel"]:
        message = f"{tree_mid()} {message}"
    _emit_kind("search", message)


def done(message):
    state = _track_view.get()
    if state is None:
        _emit_kind("done", message)
        return
    head, sep, detail = str(message).partition(_TAIL_MARK)
    state["audio"] = detail.strip() if sep else ""
    if not state["parallel"]:
        _emit_kind("lyrics", tail(f"{tree_mid()} Áudio transferido", state["audio"]))


def lyrics(message):
    _emit_kind("lyrics", message)


def step(message):
    # Mensagem de etapa em andamento -- prefixo "[*]" na cor de destaque.
    """Print a progress step message (arrow icon)."""
    _emit_tagged(HIGHLIGHT, "*", message)


def info(message):
    # Informação neutra, sem prefixo colorido nem quebra automática de tag.
    """Print an informational message (info icon)."""
    emit(message)


def warn(message):
    # Mensagem de aviso -- prefixo "[!]" amarelo. Ignora --quiet (usa
    # emit_always): a própria ajuda da flag promete "mostra apenas avisos
    # e erros", então um aviso suprimido por --quiet contradiz a promessa
    # da flag. BUGFIX: antes chamava emit() puro e um --quiet escondia
    # avisos que o usuário tinha sido informado que continuariam visíveis.
    """Print a warning message (yellow warning icon)."""
    _emit_tagged(WARNING, "!", message, sempre=True)


def error(message):
    # Mensagem de erro -- prefixo "[!]" vermelho. Ignora --quiet (usa
    # emit_always) porque erro é informação que o usuário sempre precisa ver.
    """Print an error message (red X icon)."""
    _emit_tagged(ERROR, "!", message, sempre=True)


def skip(message):
    # Item ignorado/pulado -- prefixo "[-]" na cor apagada.
    """Print a skip/omit message (yellow circle icon)."""
    state = _track_view.get()
    if state and (
        "Ja existe" in str(message)
        or "Já existe" in str(message)
        or "mesma faixa repetida" in str(message)
    ):
        state["existing"] = True
    _emit_tagged(GRAY, "-", message)


def detail(message, indent=4):
    # Linha secundária: comunica hierarquia pelo RECUO, não pela cor (texto
    # sai na cor padrão do terminal). Quebra automaticamente na largura
    # disponível, respeitando o recuo.
    """Print a detail message with indentation (dim arrow)."""
    message = str(message)
    pad = " " * indent
    if _cell_width(message) + indent <= width():
        emit(f"{c(RESET)}{pad}{message}")
        return
    for line in _wrap_lines(message, width() - indent):
        emit(f"{c(RESET)}{pad}{line}")


def debug(message):
    # Só aparece quando --verbose está ativo.
    """Print a debug message if verbose mode is active."""
    if _verbose:
        emit(f"{c(MUTED)}[debug] {message}{c(RESET)}")


def blank():
    # Atalho para uma linha em branco.
    """Print a blank line unless quiet mode is active."""
    emit("")


# --------------------------------------------------------------------------
# Estrutura visual (regras, banners, seções, tabelas chave/valor)
# --------------------------------------------------------------------------
def rule(char=None, cols=None, color=HIGHLIGHT):
    # Linha divisória de largura total (usa a largura do terminal por
    # padrão, ou `cols` se especificado).
    """Print a horizontal rule line."""
    ch = char or heavy_bar_char()
    n = cols if cols is not None else width()
    emit(f"{c(color)}{ch * n}{c(RESET)}")


def banner(title, cols=None):
    # Título centralizado entre duas linhas grossas -- usado para
    # destacar seções importantes (relatório de stats, menu de playlist).
    """Print a banner with text centered in a box."""
    n = cols if cols is not None else width()
    bar = heavy_bar_char() * n
    emit(f"\n{c(HIGHLIGHT)}{bar}{c(RESET)}")
    emit(f"{c(BG)}{c(HIGHLIGHT)}{center_cells(title, n)}{c(RESET)}")
    emit(f"{c(HIGHLIGHT)}{bar}{c(RESET)}\n")


def section(title):
    # Subtítulo de bloco dentro de um relatório maior.
    """Print a section header."""
    emit(f"  {c(BG)}{truncate(title, max(1, width() - 2))}{c(RESET)}")


def kv(label, value, label_width=30, narrow_stack=False):
    # Par rótulo/valor, sempre dentro da largura do terminal. Escolhe entre
    # três modos conforme o espaço disponível:
    #   1) coluna alinhada (rótulo + valor lado a lado) -- padrão em telas
    #      médias/largas;
    #   2) rótulo e valor empilhados, com o valor quebrado em várias linhas,
    #      quando o valor não cabe na coluna (ex.: listas longas);
    #   3) "rotulo: valor" corrido em telas estreitas.
    """Print a key-value pair with aligned formatting."""
    value = str(value)
    total = width()

    if is_narrow():
        if narrow_stack or len(value) > total - 4 - len(label):
            emit(f"  {c(HIGHLIGHT)}{label}:{c(RESET)}")
            wrapped(value, indent=4)
        else:
            emit(f"  {c(HIGHLIGHT)}{label}:{c(RESET)} {value}")
        return

    # Espaço que sobra para o valor depois de "  " + rótulo + "  ".
    room = total - 2 - label_width - 2
    if len(value) <= room:
        emit(f"  {c(HIGHLIGHT)}{label:<{label_width}}{c(RESET)}  {value}")
    else:
        emit(f"  {c(HIGHLIGHT)}{label}{c(RESET)}")
        wrapped(value, indent=4)


# --------------------------------------------------------------------------
# Caixas (cabeçalhos e resumos) -- UM só desenhista para o programa inteiro
# --------------------------------------------------------------------------
# Estilo escolhido por `QOBUZ_DL_BOX` (round, square, double, heavy, ascii).
# Sem Unicode no terminal, o estilo cai sozinho para ASCII ("+--+" / "|").
# Dica a-Shell: a fonte da janela (`config`) decide quais cantos aparecem
# bem; se algum glifo sair torto, ponha `export QOBUZ_DL_BOX=ascii` (ou
# `square`) no `.profile`, que o a-Shell executa a cada janela nova.
BOX_MAX = 48  # teto: em tela larga (iPad/PC) a caixa fica compacta
BOX_MIN = 31  # piso: abaixo disso nem o rótulo + valor cabem (MIN_WIDTH - 1)
# Largura da caixa = min(teto, largura do terminal - 1). A coluna a menos
# evita que uma linha cheia quebre para a linha de baixo em terminais que
# avançam o cursor ao escrever na última coluna (caso do celular, onde a
# caixa ocupa a linha toda). `QOBUZ_DL_BOX_WIDTH` troca o teto (ex.: 60).

# (canto sup. esq., sup. dir., inf. esq., inf. dir., horizontal, vertical,
#  junção esq., junção dir.)
_BOX_STYLES = {
    "round": (
        "\u256d",
        "\u256e",
        "\u2570",
        "\u256f",
        "\u2500",
        "\u2502",
        "\u251c",
        "\u2524",
    ),
    "square": (
        "\u250c",
        "\u2510",
        "\u2514",
        "\u2518",
        "\u2500",
        "\u2502",
        "\u251c",
        "\u2524",
    ),
    "double": (
        "\u2554",
        "\u2557",
        "\u255a",
        "\u255d",
        "\u2550",
        "\u2551",
        "\u2560",
        "\u2563",
    ),
    "heavy": (
        "\u250f",
        "\u2513",
        "\u2517",
        "\u251b",
        "\u2501",
        "\u2503",
        "\u2523",
        "\u252b",
    ),
    "ascii": ("+", "+", "+", "+", "-", "|", "+", "+"),
}

# Item de `linhas` que vira uma divisória horizontal dentro da caixa.
BOX_RULE = None


def box_style_name():
    """Nome do estilo de caixa em uso (respeita Unicode e QOBUZ_DL_BOX)."""
    if not unicode_enabled():
        return "ascii"
    name = os.environ.get("QOBUZ_DL_BOX", "").strip().lower()
    return name if name in _BOX_STYLES else "round"


def box_width(cols=None):
    """Largura total da caixa: teto compacto, nunca a linha inteira do terminal."""
    if cols is not None:
        n = int(cols)
    else:
        env = os.environ.get("QOBUZ_DL_BOX_WIDTH", "").strip()
        n = int(env) if env.isdigit() else BOX_MAX
    return max(BOX_MIN, min(n, width() - 1))


def box(title, lines, cols=None, color=HIGHLIGHT):
    """Monta uma caixa fechada e devolve a lista de linhas prontas.

    ``title`` vai embutido na borda de cima. ``lines`` aceita texto com ANSI
    (a medida ignora as sequências) e ``BOX_RULE`` para uma divisória. Cada
    linha é preenchida por CÉLULAS de terminal, então emoji e CJK não
    empurram a borda direita para fora.
    """
    tl, tr, bl, br, h, v, ml, mr = _BOX_STYLES[box_style_name()]
    total = box_width(cols)
    inner = total - 4  # "│ " + conteúdo + " │"
    edge = c(color)
    rst = c(RESET)

    if title:
        title_txt = f" {truncate(title, max(1, total - 6))} "
        fill = max(0, total - 3 - _cell_width(title_txt))
        top = (
            f"{edge}{tl}{h}{rst}{edge}{c(BG)}{title_txt}{rst}{edge}{h * fill}{tr}{rst}"
        )
    else:
        top = f"{edge}{tl}{h * (total - 2)}{tr}{rst}"

    out = [top]
    for line in lines:
        if line is BOX_RULE:
            out.append(f"{edge}{ml}{h * (total - 2)}{mr}{rst}")
            continue
        text = str(line)
        pad = " " * max(0, inner - _cell_width(text))
        out.append(f"{edge}{v}{rst} {text}{rst}{pad} {edge}{v}{rst}")
    out.append(f"{edge}{bl}{h * (total - 2)}{br}{rst}")
    return out


def emit_box(title, lines, cols=None, color=HIGHLIGHT, always=False):
    """Imprime a caixa numa única escrita (as linhas nunca se intercalam)."""
    escrever = emit_always if always else emit
    escrever("\n" + "\n".join(box(title, lines, cols=cols, color=color)) + "\n")


def header(kind, rows):
    # Cabeçalho de operação (álbum, faixa, playlist, lote de urls): caixa
    # fechada com o tipo embutido na borda de cima e os pares rótulo/valor
    # alinhados dentro. Valores longos quebram com recuo (nunca cortam) e a
    # medida é por células de terminal, então títulos em japonês/coreano ou
    # com emoji não desalinham a borda.
    """Print a closed header box with the kind in the top border."""
    rows = list(rows)
    inner = box_width() - 4
    label_width = max((_cell_width(str(label)) for label, _ in rows), default=0)
    # Rótulos enormes não podem roubar a coluna de valor inteira.
    label_width = min(label_width, max(4, inner // 2))
    value_room = max(12, inner - label_width - 2)

    lines = []
    for label, value in rows:
        parts = _wrap_lines(value, value_room)
        tag = pad_cells(truncate(str(label).upper(), label_width), label_width)
        lines.append(f"{c(HIGHLIGHT)}{tag}{c(RESET)}  {parts[0]}")
        lines.extend(" " * (label_width + 2) + part for part in parts[1:])
    emit_box(str(kind), lines)


# Tons dos resumos: (glifo bonito, glifo ASCII, cor). Mesmo significado de cor
# em todo o programa: verde = ok, amarelo = pulou, vermelho = falhou.
_TONES = {
    "ok": ("\u2714", "+", SUCCESS),
    "warn": ("\u21b7", "-", WARNING),
    "err": ("\u2716", "!", ERROR),
    "info": ("\u2022", "*", HIGHLIGHT),
}


def summary(title, rows, notes=(), cols=None, subtitle=""):
    # Painel de resumo (fim de álbum/faixa/playlist/lote). `rows` são tuplas
    # (rótulo, valor, tom) com tom em ok/warn/err/info; o valor sai alinhado
    # à direita. `notes` são observações em cinza abaixo de uma divisória.
    """Print a closed summary box with status glyphs and right-aligned values."""
    inner = box_width(cols) - 4
    rst = c(RESET)
    lines = []
    if subtitle:  # nome do álbum/playlist: quebra em vez de cortar o título
        lines.extend(f"{c(BG)}{part}{rst}" for part in _wrap_lines(subtitle, inner))
        lines.append(BOX_RULE)
    for label, value, tone in rows:
        fancy, plain, color = _TONES.get(tone, _TONES["info"])
        glyph = _glyph(fancy, plain)
        left = f"{c(color)}{glyph}{rst} {label}"
        right = f"{c(color)}{value}{rst}"
        gap = inner - _cell_width(left) - _cell_width(right)
        if gap >= 2:
            lines.append(left + " " * gap + right)
        else:  # não cabe lado a lado: valor desce, recuado
            lines.append(left)
            lines.append("  " + right)
    notes = [n for n in notes if n]
    if notes:
        lines.append(BOX_RULE)
        for note in notes:
            for part in _wrap_lines(note, inner):
                lines.append(f"{c(GRAY)}{part}{rst}")
    emit_box(title, lines, cols=cols)


def summary_downloads(kind, name, success, total, skipped=0, failed=0, notes=()):
    """Resumo padrão de downloads (álbum, playlist, lote, playlist importada).

    Linhas sem ocorrência (0 puladas, 0 falhas) não aparecem, como antes.
    """
    ok_tone = "ok" if success else ("err" if failed else "info")
    rows = [("Baixadas", f"{success}/{total}", ok_tone)]
    if skipped:
        rows.append(("Puladas / indisponíveis", str(skipped), "warn"))
    if failed:
        rows.append(("Falhas", str(failed), "err"))
    summary(f"RESUMO {kind}", rows, notes=notes, subtitle=name)


# --------------------------------------------------------------------------
# Árvore de progresso (glifos com fallback ASCII)
# --------------------------------------------------------------------------
def tree_last():
    """Ramo final da árvore (``└─``); no fallback ASCII, crase + hífen."""
    return _glyph("\u2514\u2500", "`-")


def dim(text):
    """Texto em cinza dentro de uma mensagem; sem cor, devolve o texto.

    Usa ``GRAY`` (cinza fixo) e não ``MUTED``: o esmaecido (SGR 2) só reduz a
    intensidade da cor atual, então sobre uma linha verde ou roxa sai um
    verde/roxo desbotado em vez de cinza (visto no a-Shell).
    """
    return f"{c(GRAY)}{text}{c(RESET)}"


_TAIL_GAP = "   "  # respiro entre o nome da faixa e as métricas
_TAIL_MARK = "\x1f"  # separa o nome das métricas até `_tagged` montar a linha


def tail(message, text):
    """Anexa ``text`` ao fim de ``message``; ``_tagged`` põe o respiro e o cinza.

    Só para mensagens de ``ok``/``step``/``warn``/``skip``/``error`` (passam por
    ``_tagged``, que monta a linha e quebra sem partir as métricas).
    """
    if not text:
        return message
    return f"{message}{_TAIL_MARK}{text}"


def join_meta(*parts):
    """Junta métricas com ``·`` (ou vírgula sem Unicode), ignorando as vazias."""
    return _glyph(" \u00b7 ", ", ").join(str(p) for p in parts if p)


def stats_text(size_bytes, seconds):
    """Métricas de uma conclusão (``78.3 MB · 6.2 s``); vazio sem dados.

    Só tamanho e tempo (sem velocidade): num download retomado a velocidade
    calculada com o tamanho total sairia irreal.
    """
    parts = []
    if size_bytes and size_bytes > 0:
        mb = size_bytes / (1024 * 1024)
        parts.append(f"{mb / 1024:.2f} GB" if mb >= 1024 else f"{mb:.1f} MB")
    if seconds is not None and seconds >= 0:
        parts.append(f"{seconds:.1f} s")
    return join_meta(*parts)


def tree_mid():
    """Ramo intermediário da árvore (``├─``); ASCII: ``|-``."""
    return _glyph("\u251c\u2500", "|-")


def bar_gauge(value, peak, max_blocks=None):
    # Barrinha horizontal proporcional (ex.: ranking de artistas por
    # quantidade de faixas). `max_blocks` é derivado da largura real do
    # terminal quando não informado, em vez de um número fixo -- isso evita
    # que a barra estoure a linha em terminais estreitos.
    """Print a horizontal bar gauge visualization."""
    if max_blocks is None:
        max_blocks = max(6, min(24, width() - 56))
    peak = peak or 1
    length = max(1, round(value * max_blocks / peak)) if value else 0
    return f"{c(HIGHLIGHT)}{block_char() * length}{c(RESET)}"


_ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")


def _cell_width(text):
    """Measure terminal cells, ignoring ANSI control sequences."""
    clean = _ANSI_ESCAPE.sub("", str(text))
    try:
        from prompt_toolkit.utils import get_cwidth

        return get_cwidth(clean)
    except ImportError:
        total = 0
        for char in clean:
            if unicodedata.combining(char) or char in "\u200d\ufe0f":
                continue
            total += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        return total


def _wrap_lines(text, limit):
    """Wrap text by terminal-cell width, including wide Unicode characters.

    Words are kept intact where possible; a single overlong token is split by
    display width so it cannot force a terminal line past the available width.
    """
    text = str(text)
    limit = max(int(limit), 12)
    if not text:
        return [""]

    lines = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        current = ""
        for word in words:
            if _cell_width(word) > limit:
                if current:
                    lines.append(current)
                    current = ""
                piece = ""
                for char in word:
                    if piece and _cell_width(piece + char) > limit:
                        lines.append(piece)
                        piece = char
                    else:
                        piece += char
                current = piece
                continue
            candidate = f"{current} {word}" if current else word
            if current and _cell_width(candidate) > limit:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines or [""]


def wrapped(text, indent=0, cols=None):
    # Texto corrido quebrado na largura do terminal, com recuo opcional.
    # Emite RESET no início de CADA linha de propósito: garante que texto
    # explicativo sempre saia na cor padrão do terminal, sem herdar cor de
    # quem escreveu antes.
    """Print wrapped text with optional indentation."""
    n = (cols if cols is not None else width()) - indent
    pad = " " * indent
    for line in _wrap_lines(text, n):
        emit(f"{c(RESET)}{pad}{line}")


def truncate(text, limit):
    """Truncate text by terminal-cell width, adding an ellipsis if needed."""
    text = str(text)
    limit = max(0, int(limit))
    if _cell_width(text) <= limit:
        return text
    if limit <= 3:
        result = ""
        for char in text:
            if _cell_width(result + char) > limit:
                break
            result += char
        return result

    target = limit - 3
    result = ""
    for char in text:
        if _cell_width(result + char) > target:
            break
        result += char
    return result + "..."


def pad_cells(text, cols):
    """Completa ``text`` com espaços à direita até ``cols`` células de terminal.

    ``str.ljust``/``f"{x:<n}"`` contam caracteres, não células: emoji e
    caracteres CJK ocupam 2 células e a linha passava da largura da tela.
    """
    text = str(text)
    return text + " " * max(0, int(cols) - _cell_width(text))


def center_cells(text, cols):
    """Centraliza ``text`` em ``cols`` células (igual a ``str.center`` em ASCII)."""
    text = truncate(text, cols)
    pad = max(0, int(cols) - _cell_width(text))
    left = pad // 2 + (pad & int(cols) & 1)  # mesma regra do str.center
    return " " * left + text + " " * (pad - left)


# --------------------------------------------------------------------------
# Ponte com o módulo logging
# --------------------------------------------------------------------------
class TqdmLoggingHandler(logging.Handler):
    # Handler de logging que redireciona toda mensagem (logger.info,
    # logger.warning, etc. de qualquer módulo do projeto) para emit()/
    # emit_always() deste arquivo -- ou seja, passa a usar o mesmo lock e o
    # mesmo tqdm.write que as barras de progresso do downloader, em vez de
    # escrever direto no stdout por cima delas.

    def __init__(self, bypass_quiet=False):
        """Initialize logging handler that respects quiet mode unless bypass_quiet is True."""
        super().__init__()
        # bypass_quiet=True quando --log-level foi passado explicitamente:
        # nesse caso o filtro de nivel do proprio `logging` (setLevel) ja'
        # decide o que chega aqui, entao aplicar o gate de --quiet DE NOVO
        # em cima seria a pessoa pedir --log-level DEBUG e continuar sem
        # ver debug nenhum. Sem --log-level explicito, mantem o gate: e'
        # o comportamento padrao (quiet/verbose) descrito em --help.
        self.bypass_quiet = bypass_quiet

    def emit(self, record):
        """Print a message unless quiet mode is active."""
        try:
            msg = self.format(record)
        except Exception:
            self.handleError(record)
            return
        # BUGFIX: antes so' logging.ERROR+ escapava de --quiet. Mas --quiet
        # promete no --help "mostra apenas avisos e erros" -- um
        # logger.warning(...) real tambem precisa sobreviver, do contrario
        # a promessa da flag vale pra ui.warn() mas nao pra logging.warning(),
        # que e' o mesmo tipo de mensagem vindo de outro caminho de codigo.
        if record.levelno >= logging.WARNING or self.bypass_quiet:
            emit_always(msg)
        else:
            emit(msg)


def install_logging(level=None):
    # Substitui os handlers do logger raiz pelo TqdmLoggingHandler. Deve
    # ser chamado uma vez, no início da CLI, depois de configure().
    """Configure Python logging to route through the UI system."""
    explicit_level = level is not None
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

    handler = TqdmLoggingHandler(bypass_quiet=explicit_level)
    handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(handler)
    root.setLevel(level)

    # Bibliotecas de rede são ruidosas em DEBUG; mantém elas um nível acima
    # para não poluir a saída em --verbose.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "PIL"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))

    return handler


# Define explicitamente o que é exportado por `from qobuz_dl.ui import *`.
__all__ = [
    "begin_track_view",
    "end_track_view",
    "finish_track_view",
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
    "step_inline",
    "truncate",
    "unicode_enabled",
    "warn",
    "width",
    "wrapped",
    "progress",
    "search",
    "done",
    "lyrics",
    "faint",
    "format_index",
    "index_from_meta",
    "set_track_index",
    "reset_track_index",
    "run_in_executor",
    "tagged_lines",
]
