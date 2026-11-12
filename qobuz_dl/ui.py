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
#   ui.Painel("ESTATISTICAS").kv("Total de downloads", "1234").emit()
#
# No boot da CLI, para que o `logging` de todos os módulos também respeite
# as barras de progresso do tqdm:
#   ui.configure(quiet=args.quiet, verbose=args.verbose, color=not args.no_color)
#   ui.install_logging()

import contextlib
import contextvars
import dataclasses
import json
import logging
import os
import re
import shutil
import sys
import threading
import unicodedata
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypedDict

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

# Valores de `interactive_layout` (config.ini): como a lista de resultados do modo
# interativo é desenhada. "auto" escolhe pela largura da tela.
LAYOUTS_INTERATIVOS = ("auto", "cartao", "compacto", "tabela")


@dataclasses.dataclass
class EstadoUI:
    """Todo o estado mutável do terminal num só lugar (antes eram 7 globais).

    Campos:
      quiet / verbose        flags de saída (`--quiet`, `--verbose`)
      color_enabled          None = ainda não detectado (cache preguiçoso)
      unicode_enabled        idem, para glifos unicode
      ui_mode                override para terminais móveis: ``None`` preserva a
                             detecção automática pela largura; ``compact`` força
                             cartões/atalhos e ``comfortable`` privilegia detalhes
      layout_interativo      como a lista de resultados do modo interativo é
                             desenhada: ``auto``/``cartao``/``compacto``/``tabela``
      pending_status         há uma linha de status "em andamento" sem quebra
      ultima_linha_vazia     a última escrita terminou em linha em branco

    Existe UMA instância, `ui.estado`. Para mexer: `ui.configure(...)` no boot
    do programa; em teste, `monkeypatch.setattr(ui.estado, "quiet", True)` ou
    `with ui.estado_temporario(quiet=True): ...`. Nunca troque a instância
    (`ui.estado = ...`): as funções do módulo leem o objeto que já existe.
    """

    quiet: bool = False
    verbose: bool = False
    color_enabled: bool | None = None
    unicode_enabled: bool | None = None
    ui_mode: str | None = None
    layout_interativo: str = "auto"
    pending_status: bool = False
    ultima_linha_vazia: bool = False


estado = EstadoUI()


def redefinir_estado() -> None:
    """Volta todos os campos de `estado` ao padrão (cor e unicode serão redetectados)."""
    for campo in dataclasses.fields(EstadoUI):
        setattr(estado, campo.name, campo.default)


@contextlib.contextmanager
def estado_temporario(**campos: Any) -> Iterator[EstadoUI]:
    """Muda campos de `estado` dentro do bloco e restaura tudo ao sair.

    ``with ui.estado_temporario(quiet=True, color_enabled=False): ...``
    Campo inexistente levanta ``AttributeError`` (erro de digitação não passa).
    """
    antes = dataclasses.asdict(estado)
    for nome in campos:
        if nome not in antes:
            raise AttributeError(f"EstadoUI não tem o campo '{nome}'")
    try:
        for nome, valor in campos.items():
            setattr(estado, nome, valor)
        yield estado
    finally:
        for nome, valor in antes.items():
            setattr(estado, nome, valor)


LAYOUT_NARROW = "narrow"
LAYOUT_MEDIUM = "medium"
LAYOUT_WIDE = "wide"


def raw_width(fallback: tuple[int, int] = FALLBACK) -> int:
    """Return the current terminal width without clamping.

    Prefer the live TTY size so resizing a terminal is reflected immediately.
    For redirected output and tests, honor ``COLUMNS`` before using a fallback.
    ``fallback`` é o par (colunas, linhas) usado quando nada informa a largura.
    """
    try:
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            return os.get_terminal_size(sys.stdout.fileno()).columns
    except (OSError, ValueError, AttributeError):
        # Sem tty ou tamanho indisponível: cai para COLUMNS/fallback.
        pass

    env = os.environ.get("COLUMNS")
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
        except (TypeError, ValueError):
            # COLUMNS inválido: segue para o fallback.
            pass
    try:
        return shutil.get_terminal_size(fallback=fallback).columns
    except Exception:
        return fallback[0]


def raw_height(fallback: tuple[int, int] = FALLBACK) -> int:
    """Altura atual do terminal, em linhas (par do ``raw_width``).

    Mesma ordem de busca: tamanho do TTY ao vivo, depois ``LINES`` e por fim o
    par ``fallback`` (colunas, linhas). Telas que precisam saber quantas linhas
    cabem (a lista do modo interativo, por exemplo) usam esta função em vez de
    chamar ``shutil.get_terminal_size`` direto.
    """
    try:
        if hasattr(sys.stdout, "isatty") and sys.stdout.isatty():
            return os.get_terminal_size(sys.stdout.fileno()).lines
    except (OSError, ValueError, AttributeError):
        # Sem tty ou tamanho indisponível: cai para LINES/fallback.
        pass

    env = os.environ.get("LINES")
    if env:
        try:
            value = int(env)
            if value > 0:
                return value
        except (TypeError, ValueError):
            # LINES inválido: segue para o fallback.
            pass
    try:
        return shutil.get_terminal_size(fallback=fallback).lines
    except Exception:
        return fallback[1]


def width(max_width: int = MAX_WIDTH, min_width: int = MIN_WIDTH) -> int:
    # Largura utilizável para desenhar blocos de texto, já com teto e piso
    # aplicados. Função única que substitui os cálculos redundantes que
    # existiam espalhados por vários arquivos do projeto.
    """Return terminal width clamped to the requested minimum and maximum."""
    return max(min(raw_width(), max_width), min_width)


# Largura dos relatórios de varredura (letras/tags retroativas): mais larga que
# a dos painéis, para caber caminhos e motivos sem quebrar a todo momento.
REPORT_MAX_WIDTH = 120
# Piso igual ao dos painéis: em tela de 40 colunas (a-Shell no iPhone) o relatório
# se ajusta à tela em vez de ficar sempre com 60 colunas e quebrar no meio.
REPORT_MIN_WIDTH = MIN_WIDTH
# Teto da caixa "ÁLBUM" dos relatórios retroativos (`lyrics` e `tags`).
ALBUM_BOX_COLS = 90
# Teto da largura dos painéis de relatório (`stats`, `inspect`): ``Painel``.
PAINEL_COLS = 72


def report_width() -> int:
    """Largura dos relatórios retroativos (entre REPORT_MIN e REPORT_MAX)."""
    return width(max_width=REPORT_MAX_WIDTH, min_width=REPORT_MIN_WIDTH)


def _wrap_prefixed(
    text: Any, width: int, first_prefix: str, next_prefix: str
) -> list[str]:
    """Quebra ``text`` em linhas de no máximo ``width`` CÉLULAS, com prefixos.

    ``first_prefix`` abre a primeira linha e ``next_prefix`` as seguintes. Mede
    em células (emoji e CJK valem 2), ao contrário do ``textwrap``, que conta
    caracteres e deixava a linha passar da tela. Uma palavra maior que a linha é
    cortada por células. Devolve ``[]`` se não houver palavra.
    """
    lines = []
    prefix = first_prefix
    current = ""

    def room() -> int:
        return max(1, int(width) - _cell_width(prefix))

    for word in str(text).split():
        while _cell_width(word) > room():
            if current:  # fecha a linha atual antes de partir a palavra
                lines.append(prefix + current)
                prefix, current = next_prefix, ""
                continue
            piece = ""
            for char in word:
                if piece and _cell_width(piece + char) > room():
                    break
                piece += char
            lines.append(prefix + piece)
            prefix = next_prefix
            word = word[len(piece) :]
        candidate = f"{current} {word}" if current else word
        if _cell_width(candidate) <= room():
            current = candidate
        else:
            lines.append(prefix + current)
            prefix, current = next_prefix, word
    if current:
        lines.append(prefix + current)
    return lines


def _recuo_de_continuacao(visivel: str) -> int:
    """Recuo das linhas seguintes de uma linha quebrada: o da própria linha,
    mais o que o marcador (``•`` ou ``[x]``) ocupa, para o texto alinhar."""
    recuo = len(visivel) - len(visivel.lstrip(" "))
    resto = visivel[recuo:]
    if resto[:1] in ("•", "-") and resto[1:2] == " ":
        return recuo + 2
    if resto[:1] == "[" and resto[2:3] == "]" and resto[3:4] == " ":
        return recuo + 4
    return recuo


def wrap_ansi_line(linha: Any, cols: int | None = None) -> list[str]:
    """Quebra uma linha COM cores ANSI para caber em ``cols`` colunas.

    Mede em células, ignorando as sequências de cor. Quebra entre palavras, mantém
    o recuo (e o do marcador ``•``/``[x]``) nas linhas seguintes. Uma palavra maior
    que a linha (um caminho, por exemplo) NÃO é partida: vai inteira para uma linha
    só dela, para continuar copiável. Linha que já cabe volta intacta. Uma cor aberta numa linha continua valendo na seguinte, que
    é como o terminal trata o código de cor.
    """
    linha = str(linha)
    limite = max(10, int(cols or report_width()) - 1)
    if _cell_width(linha) <= limite:
        return [linha]
    recuo = min(_recuo_de_continuacao(_ANSI_ESCAPE.sub("", linha)), limite // 2)
    margem = " " * recuo
    linhas, atual, primeira = [], "", True
    for palavra in linha.split(" "):
        if not primeira and not palavra:
            atual += " "  # espaço duplo no meio da linha
            continue
        candidata = palavra if primeira else f"{atual} {palavra}"
        if _cell_width(candidata) <= limite:
            atual, primeira = candidata, False
            continue
        if not primeira:
            linhas.append(atual.rstrip())
        # Palavra maior que a linha (um caminho) fica inteira, em linha própria:
        # o terminal a quebra sozinho e, ao copiar, o caminho continua íntegro.
        atual, primeira = margem + palavra, False
    if atual.strip():
        linhas.append(atual)
    return linhas or [linha]


def emit_wrapped(linha: Any, cols: int | None = None) -> None:
    """``emit`` de uma linha com cores que não passa da largura do relatório.

    Aceita ``\n`` dentro do texto: cada trecho é quebrado separadamente.
    """
    for trecho in str(linha).split("\n"):
        for parte in wrap_ansi_line(trecho, cols):
            emit(parte)


def resumo_linhas(
    rotulo: str,
    valor: Any,
    explicacao: str = "",
    *,
    cor_rotulo: str = "",
    cor_valor: str = "",
    cor_explicacao: str = "",
    reset: str = "",
    larg_rotulo: int = 26,
    cols: int | None = None,
) -> list[str]:
    """Linhas de uma linha de resumo "• Rótulo : valor  explicação".

    Em tela larga, rótulos alinhados na mesma coluna (``larg_rotulo`` é o do maior
    rótulo da tabela, para nenhum estourar a coluna). Em tela estreita
    (< ``NARROW``) a explicação desce para a linha de baixo, recuada.
    """
    cols = int(cols or report_width())
    valor = str(valor)
    if cols >= NARROW:
        rot = pad_cells(rotulo, larg_rotulo)
        linha = f"{cor_rotulo} • {rot}:{reset} {cor_valor}{valor}{reset}"
        if explicacao:
            linha += f"  {cor_explicacao}{explicacao}{reset}"
        return wrap_ansi_line(linha, cols)
    saida = wrap_ansi_line(
        f"{cor_rotulo} • {rotulo}:{reset} {cor_valor}{valor}{reset}", cols
    )
    if explicacao:
        saida += wrap_ansi_line(f"   {cor_explicacao}{explicacao}{reset}", cols)
    return saida


def wrap_indented(text: Any, indent: int, cols: int | None = None) -> list[str]:
    """Quebra ``text`` em linhas que cabem em ``cols``, com recuo fixo."""
    cols = cols or report_width()
    prefix = " " * indent
    return _wrap_prefixed(text, cols - 1, prefix, prefix) or [prefix]


def labeled_lines(label: str, text: Any, cols: int) -> list[str]:
    """Linha "   Rótulo  texto" com continuação alinhada sob o texto."""
    return _wrap_prefixed(text, cols - 1, f"   {pad_cells(label, 6)} ", " " * 10) or [
        f"   {label}"
    ]


def layout() -> str:
    # Classifica a largura atual do terminal em narrow/medium/wide, para
    # que outras funções decidam como se adaptar (empilhar valor, quebrar
    # linha, etc.).
    """Return the current layout mode ('narrow' or 'wide') based on terminal width."""
    if estado.ui_mode == "compact":
        return LAYOUT_NARROW
    if estado.ui_mode == "comfortable":
        return LAYOUT_WIDE
    cols = raw_width()
    if cols < NARROW:
        return LAYOUT_NARROW
    if cols < MEDIUM:
        return LAYOUT_MEDIUM
    return LAYOUT_WIDE


def is_narrow() -> bool:
    # Atalho para checagens rápidas de "estou em tela estreita?".
    """Check whether the terminal is below the narrow-layout breakpoint."""
    return layout() == LAYOUT_NARROW


def compact_mode(columns: int | None = None) -> bool:
    """Return whether dense/card rendering should be used."""
    if estado.ui_mode == "compact":
        return True
    if estado.ui_mode == "comfortable":
        return False
    return (raw_width() if columns is None else columns) <= COMPACT_WIDTH


def progress_ncols() -> int:
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


def _detect_color() -> bool:
    # Decide se é seguro emitir sequências ANSI de cor.
    # Delega para color._detect_color_capability() em vez de reimplementar
    # a regra aqui -- assim as duas partes do projeto nunca divergem sobre
    # quando a cor deve estar ligada ou desligada (ex.: saída redirecionada
    # para um arquivo com `> log.txt`).
    """Detect if color output should be enabled based on environment and TTY."""
    from qobuz_dl.color import _detect_color_capability

    return _detect_color_capability()


def _detect_unicode() -> bool:
    # Assume unicode disponível se a codificação do stdout contiver "utf".
    """Detect if Unicode glyphs are supported by the terminal."""
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    return "utf" in enc


def color_enabled() -> bool:
    # Getter com cache preguiçoso (lazy) para o resultado de _detect_color().
    """Check if color output is currently enabled."""
    if estado.color_enabled is None:
        estado.color_enabled = _detect_color()
    return estado.color_enabled


def unicode_enabled() -> bool:
    # Getter com cache preguiçoso para o resultado de _detect_unicode().
    """Check if Unicode output is currently enabled."""
    if estado.unicode_enabled is None:
        estado.unicode_enabled = _detect_unicode()
    return estado.unicode_enabled


def configure(
    quiet: bool | None = None,
    verbose: bool | None = None,
    color: bool | None = None,
    unicode: bool | None = None,
    ui_mode: str | None = None,
    layout_interativo: str | None = None,
) -> None:
    # Ponto único de ajuste do comportamento global da UI. Chamado uma vez,
    # no boot da CLI, com os valores vindos dos argumentos de linha de
    # comando. Parâmetros None são ignorados (mantêm o valor atual).
    """Configure UI system (color, Unicode, quiet/verbose modes)."""
    if quiet is not None:
        estado.quiet = bool(quiet)
    if verbose is not None:
        estado.verbose = bool(verbose)
    if color is not None:
        estado.color_enabled = bool(color)
    if unicode is not None:
        estado.unicode_enabled = bool(unicode)
    if ui_mode is not None:
        if ui_mode not in ("auto", "compact", "comfortable"):
            raise ValueError(f"modo de UI inválido: {ui_mode}")
        estado.ui_mode = None if ui_mode == "auto" else ui_mode
    if layout_interativo is not None:
        if layout_interativo not in LAYOUTS_INTERATIVOS:
            raise ValueError(f"layout interativo inválido: {layout_interativo}")
        estado.layout_interativo = layout_interativo


def c(code: str) -> str:
    # Devolve o código ANSI passado, ou string vazia quando a cor está
    # desligada. Usado em toda função de emit para envolver texto com cor
    # sem precisar de um `if color_enabled()` em cada chamada.
    """Apply color escape sequence if color is enabled, otherwise return empty string."""
    return code if color_enabled() else ""


def _glyph(fancy: str, plain: str) -> str:
    # Escolhe entre o glifo unicode "bonito" e o equivalente ASCII simples,
    # dependendo do suporte detectado do terminal.
    """Return a Unicode glyph if supported, otherwise return ASCII fallback."""
    return fancy if unicode_enabled() else plain


def heavy_bar_char() -> str:
    # Caractere usado nas barras/separadores grossos (títulos, banners).
    """Return the heavy bar character for progress bars (Unicode-aware)."""
    return _glyph("\u2501", "=")  # ━


def light_bar_char() -> str:
    # Caractere usado em separadores finos.
    """Return the light bar character for progress bars (Unicode-aware)."""
    return _glyph("\u2500", "-")  # ─


def block_char() -> str:
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


def _status_inline_supported() -> bool:
    # So' sobrescreve em terminal interativo; em pipe/CI/arquivo cada mensagem
    # continua em sua propria linha (sem \r nem codigos ANSI no log).
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _clear_pending_locked() -> None:
    # Chamado dentro do print_lock: apaga a linha de status pendente.
    if estado.pending_status:
        estado.pending_status = False
        try:
            sys.stdout.write("\r\033[2K")
            sys.stdout.flush()
        except Exception:
            # Terminal sem suporte: efeito só cosmético.
            pass


def step_inline(message: Any) -> None:
    """Print a progress step WITHOUT newline; the next message replaces it."""
    if estado.quiet:
        return
    if not _status_inline_supported():
        step(message)
        return
    with print_lock:
        _clear_pending_locked()
        linha = _tagged(HIGHLIGHT, "*", message)[0]
        _write_locked(linha, "")
        try:
            sys.stdout.flush()
        except Exception:
            # Terminal sem suporte: efeito só cosmético.
            pass
        estado.pending_status = True


def emit(text: Any = "", end: str = "\n") -> None:
    # ÚNICO ponto de escrita no terminal usado pelo restante do módulo (e,
    # por extensão, por todo o programa via as funções ok/warn/error/etc.).
    # Usa tqdm.write() quando disponível, para que barras de progresso
    # ativas sejam apagadas e redesenhadas ao redor da mensagem em vez de
    # serem cortadas ao meio. Respeita --quiet (não imprime nada).
    """Print a message unless quiet mode is active."""
    if estado.quiet:
        return
    with print_lock:
        _clear_pending_locked()
        _write_locked(text, end)
        _registrar_saida(text, end)


# True quando a última escrita por emit()/emit_always() terminou numa linha em
# branco. Serve só a `separator()`, para não empilhar duas linhas em branco.


def _registrar_saida(text: Any, end: str) -> None:
    estado.ultima_linha_vazia = end == "\n" and (
        str(text) == "" or str(text).endswith("\n")
    )


def separator() -> None:
    # Linha em branco entre seções (ex.: preparação -> faixas). Não empilha:
    # se a última saída já terminou em linha em branco (como a caixa do
    # cabeçalho), não escreve outra. Sem lock aqui: emit() já o pega e o
    # print_lock não é reentrante.
    """Print a blank line between sections, never two in a row."""
    if not estado.ultima_linha_vazia:
        emit("")


def _write_locked(text: Any, end: str) -> None:
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


def emit_always(text: Any = "", end: str = "\n") -> None:
    # Igual a emit(), mas ignora --quiet. Reservado para mensagens que o
    # usuário precisa ver mesmo em modo silencioso: erros fatais e prompts
    # de confirmação.
    """Print a message regardless of quiet mode (for critical output)."""
    with print_lock:
        _clear_pending_locked()
        _write_locked(text, end)
        _registrar_saida(text, end)


def emit_json(data: Any) -> None:
    """Imprime ``data`` como JSON (UTF-8, recuo 2) na saída padrão, mesmo com --quiet.

    É o único jeito de um comando falar JSON: `--json` em `library`, `stats`,
    `config`, `duplicates`, `doctor`. Nada mais pode sair no stdout nesse modo.
    Valores que o JSON não conhece (datas, Path) viram texto.
    """
    emit_always(json.dumps(data, ensure_ascii=False, indent=2, default=str))


_track_index = contextvars.ContextVar("ui_track_index", default="")
_INDEX_AFTER_LABEL = re.compile(r"(: )\[\d+(?:[./]\d+)?\]\s+")


def format_index(
    track: Any,
    total: Any = None,
    disc: Any = None,
    disc_total: Any = 1,
    playlist: bool = False,
) -> str:
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


def index_from_meta(
    track_meta: dict[str, Any] | None,
    album_meta: dict[str, Any] | None = None,
    display_index: Any = None,
) -> str:
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


def set_track_index(value: str | None) -> contextvars.Token[str]:
    return _track_index.set(value or "")


def reset_track_index(token: contextvars.Token[str]) -> None:
    try:
        _track_index.reset(token)
    except ValueError:
        # Token de outro contexto: nada a restaurar.
        pass


def run_in_executor(
    loop: Any, executor: Any, fn: Callable[..., Any], *args: Any
) -> Any:
    """run_in_executor que PRESERVA o contexto (o do asyncio puro não preserva)."""
    ctx = contextvars.copy_context()
    return loop.run_in_executor(executor, ctx.run, fn, *args)


def faint(text: Any) -> str:
    """Cinza apagado (métricas), mais escuro que o dim()."""
    return f"{c(GRAY)}{text}{c(RESET)}"


# --------------------------------------------------------------------------
# Mensagens semânticas (linhas com tag colorida: [+], [!], [*], [-])
# --------------------------------------------------------------------------
# Tamanho de "[x] " -- o recuo das linhas de continuação alinha o texto sob
# o texto da primeira linha, não sob a tag.
_LARGURA_TAG = 4


# Presentation state is task-local; the same dict is shared with its lyric worker.
class _TrackView(TypedDict):
    parallel: bool
    audio: str
    lyrics: list[str]
    existing: bool


_track_view: contextvars.ContextVar[_TrackView | None] = contextvars.ContextVar(
    "ui_track_view", default=None
)


def begin_track_view(
    parallel: bool = False,
) -> contextvars.Token[_TrackView | None]:
    return _track_view.set(
        {"parallel": bool(parallel), "audio": "", "lyrics": [], "existing": False}
    )


def end_track_view(token: contextvars.Token[_TrackView | None]) -> None:
    _track_view.reset(token)


def finish_track_view(title: Any, success: bool) -> None:
    state = _track_view.get()
    if not state or not success or state["existing"]:
        return
    details = join_meta(state["audio"], *state["lyrics"]) if state["parallel"] else ""
    label = "Concluído:" if state["parallel"] else f"{tree_last()} Concluído:"
    _emit_kind("done", tail(f"{label} {title}", details))


def _tagged(color: str, tag: str, message: Any) -> list[str]:
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
            "Letras:",
            "Nenhuma letra encontrada:",
        )
    ):
        color = GRAY
    completed = plain_label.startswith(("Concluído:", "Concluída:"))
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
        index_color = GRAY if completed else color
        primeira = f"{c(index_color)}{marca}{reset} {cor}{partes[0]}{reset}"
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


def tagged_lines(kind: str, message: Any) -> list[str]:
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


def _emit_tagged(color: str, tag: str, message: Any, sempre: bool = False) -> None:
    # Emite cada linha produzida por _tagged() separadamente, escolhendo
    # entre emit() e emit_always() conforme o parâmetro `sempre`.
    """Emit a tagged message with consistent formatting."""
    escrever = emit_always if sempre else emit
    for linha in _tagged(color, tag, message):
        escrever(linha)


def ok(message: Any) -> None:
    # Mensagem de sucesso -- prefixo "[+]" verde.
    """Print a success message (green checkmark)."""
    _emit_tagged(SUCCESS, "+", message)


def _emit_kind(kind: str, message: Any) -> None:
    color, tag = _KINDS[kind]
    _emit_tagged(color, tag, message)


def progress(message: Any) -> None:
    if _track_view.get():
        message = str(message).replace("Em Progresso:", "Baixando:", 1)
    _emit_kind("progress", message)


def search(message: Any) -> None:
    state = _track_view.get()
    if state and not state["parallel"]:
        message = f"{tree_mid()} {message}"
    _emit_kind("search", message)


def done(message: Any) -> None:
    state = _track_view.get()
    if state is None:
        _emit_kind("done", message)
        return
    head, sep, detail = str(message).partition(_TAIL_MARK)
    state["audio"] = detail.strip() if sep else ""
    if not state["parallel"]:
        _emit_kind("lyrics", tail(f"{tree_mid()} Áudio transferido", state["audio"]))


def lyrics(message: Any) -> None:
    _emit_kind("lyrics", message)


def step(message: Any) -> None:
    # Mensagem de etapa em andamento -- prefixo "[*]" na cor de destaque.
    """Print a progress step message (arrow icon)."""
    _emit_tagged(HIGHLIGHT, "*", message)


def info(message: Any) -> None:
    # Informação neutra, sem prefixo colorido nem quebra automática de tag.
    """Print an informational message (info icon)."""
    emit(message)


def warn(message: Any) -> None:
    # Mensagem de aviso -- prefixo "[!]" amarelo. Ignora --quiet (usa
    # emit_always): a própria ajuda da flag promete "mostra apenas avisos
    # e erros", então um aviso suprimido por --quiet contradiz a promessa
    # da flag. BUGFIX: antes chamava emit() puro e um --quiet escondia
    # avisos que o usuário tinha sido informado que continuariam visíveis.
    """Print a warning message (yellow warning icon)."""
    _emit_tagged(WARNING, "!", message, sempre=True)


def error(message: Any) -> None:
    # Mensagem de erro -- prefixo "[!]" vermelho. Ignora --quiet (usa
    # emit_always) porque erro é informação que o usuário sempre precisa ver.
    """Print an error message (red X icon)."""
    _emit_tagged(ERROR, "!", message, sempre=True)


def skip(message: Any) -> None:
    # Item ignorado/pulado -- cinza; faixa já existente é aviso amarelo.
    """Print a skip/omit message (yellow circle icon)."""
    state = _track_view.get()
    texto = str(message)
    normalizado = texto.casefold()
    arquivo_existente = (
        "ja existe" in normalizado
        or "já existe" in normalizado
        or "consta no banco local" in normalizado
        or ("já baixada" in normalizado and "id " in normalizado)
        or ("ja baixada" in normalizado and "id " in normalizado)
    )
    faixa_repetida = "mesma faixa repetida" in normalizado
    if state and (arquivo_existente or faixa_repetida):
        state["existing"] = True
    if arquivo_existente:
        _emit_tagged(WARNING, "!", message, sempre=True)
    else:
        _emit_tagged(GRAY, "-", message)


def detail(message: Any, indent: int = 4) -> None:
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


def debug(message: Any) -> None:
    # Só aparece quando --verbose está ativo.
    """Print a debug message if verbose mode is active."""
    if estado.verbose:
        emit(f"{c(MUTED)}[debug] {message}{c(RESET)}")


def blank() -> None:
    # Atalho para uma linha em branco.
    """Print a blank line unless quiet mode is active."""
    emit("")


# --------------------------------------------------------------------------
# Estrutura visual (regras divisórias)
# --------------------------------------------------------------------------
def rule(
    char: str | None = None, cols: int | None = None, color: str = HIGHLIGHT
) -> None:
    # Linha divisória de largura total (usa a largura do terminal por
    # padrão, ou `cols` se especificado).
    """Print a horizontal rule line."""
    ch = char or heavy_bar_char()
    n = cols if cols is not None else width()
    emit(f"{c(color)}{ch * n}{c(RESET)}")


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


def box_style_name() -> str:
    """Nome do estilo de caixa em uso (respeita Unicode e QOBUZ_DL_BOX)."""
    if not unicode_enabled():
        return "ascii"
    name = os.environ.get("QOBUZ_DL_BOX", "").strip().lower()
    return name if name in _BOX_STYLES else "round"


def box_width(cols: int | None = None) -> int:
    """Largura total da caixa: teto compacto, nunca a linha inteira do terminal."""
    if cols is not None:
        n = int(cols)
    else:
        env = os.environ.get("QOBUZ_DL_BOX_WIDTH", "").strip()
        n = int(env) if env.isdigit() else BOX_MAX
    return max(BOX_MIN, min(n, width() - 1))


def box(
    title: Any, lines: Iterable[Any], cols: int | None = None, color: str = HIGHLIGHT
) -> list[str]:
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


def emit_box(
    title: Any,
    lines: Iterable[Any],
    cols: int | None = None,
    color: str = HIGHLIGHT,
    always: bool = False,
    lead: bool = True,
) -> None:
    """Imprime a caixa numa única escrita (as linhas nunca se intercalam).

    A caixa sai com uma linha em branco depois e, com ``lead=True``, outra
    antes. Em caixas seguidas, ``lead=False`` evita duas linhas em branco
    entre elas.
    """
    escrever = emit_always if always else emit
    inicio = "\n" if lead else ""
    escrever(inicio + "\n".join(box(title, lines, cols=cols, color=color)) + "\n")


def header(
    kind: Any,
    rows: Iterable[tuple[Any, Any]],
    cols: int | None = None,
    fit: bool = False,
    lead: bool = True,
) -> None:
    # Cabeçalho de operação (álbum, faixa, playlist, lote de urls): caixa
    # fechada com o tipo embutido na borda de cima e os pares rótulo/valor
    # alinhados dentro. Valores longos quebram com recuo (nunca cortam) e a
    # medida é por células de terminal, então títulos em japonês/coreano ou
    # com emoji não desalinham a borda.
    """Print a closed header box with the kind in the top border.

    ``cols`` troca o teto de largura da caixa (BOX_MAX); o piso e o limite da
    tela continuam valendo. Com ``fit=True`` a caixa se ajusta ao conteúdo:
    fica só do tamanho da linha mais longa (até o teto), em vez de ocupar o
    teto inteiro. ``lead=False`` tira a linha em branco de antes (ver
    ``emit_box``), para encadear vários painéis.
    """
    rows = list(rows)
    if fit:
        teto = cols if cols is not None else BOX_MAX
        rotulo_max = max((_cell_width(str(label)) for label, _ in rows), default=0)
        valor_max = max((_cell_width(str(value)) for _, value in rows), default=0)
        natural = max(rotulo_max + 2 + valor_max + 4, _cell_width(str(kind)) + 6)
        cols = min(natural, teto)
    inner = box_width(cols) - 4
    emit_box(str(kind), _kv_lines(rows, inner), cols=cols, lead=lead)


def _kv_lines(
    rows: Iterable[tuple[Any, Any]], inner: int, label_width: int | None = None
) -> list[str]:
    """Linhas "RÓTULO  valor" alinhadas para dentro de uma caixa de ``inner`` células.

    Valores longos quebram com recuo (nunca cortam). ``label_width`` fixa a
    coluna dos rótulos; sem ele vale o maior rótulo, limitado à metade da caixa.
    """
    rows = [(str(label), value) for label, value in rows]
    if label_width is None:
        label_width = max((_cell_width(label) for label, _ in rows), default=0)
    # Rótulos enormes não podem roubar a coluna de valor inteira.
    label_width = min(label_width, max(4, inner // 2))
    value_room = max(12, inner - label_width - 2)

    lines = []
    for label, value in rows:
        parts = _wrap_lines(value, value_room)
        tag = pad_cells(truncate(label.upper(), label_width), label_width)
        lines.append(f"{c(HIGHLIGHT)}{tag}{c(RESET)}  {parts[0]}")
        lines.extend(" " * (label_width + 2) + part for part in parts[1:])
    return lines


class Painel:
    """Caixa que mistura pares rótulo/valor e mensagens (ok, aviso, erro...).

    Para telas de relatório (``inspect`` e afins): cada seção vira um painel,
    no mesmo desenho do ``header``/``summary``. Monte com ``kv``, ``ok``,
    ``warn``, ``error``, ``info``, ``text``, ``detail`` e ``rule`` e imprima
    de uma vez com ``emit``; o painel todo sai numa única escrita.
    """

    def __init__(self, title: Any, cols: int | None = None) -> None:
        self.title = str(title)
        self.cols = PAINEL_COLS if cols is None else cols
        self._items: list[tuple[Any, ...]] = []

    # -- montagem (todas devolvem o próprio painel, para encadear) ----------
    def kv(self, label: Any, value: Any) -> "Painel":
        self._items.append(("kv", str(label), str(value)))
        return self

    def _msg(self, tone: str, text: Any) -> "Painel":
        self._items.append(("msg", tone, str(text)))
        return self

    def ok(self, text: Any) -> "Painel":
        return self._msg("ok", text)

    def warn(self, text: Any) -> "Painel":
        return self._msg("warn", text)

    def error(self, text: Any) -> "Painel":
        return self._msg("err", text)

    def info(self, text: Any) -> "Painel":
        return self._msg("info", text)

    def text(self, text: Any, indent: int = 0) -> "Painel":
        """Linha de texto normal (cor padrão), com recuo."""
        self._items.append(("text", int(indent), str(text)))
        return self

    def detail(self, text: Any, indent: int = 0) -> "Painel":
        """Linha de apoio em cinza, com recuo."""
        self._items.append(("detail", int(indent), str(text)))
        return self

    def rule(self) -> "Painel":
        self._items.append(("rule",))
        return self

    def __bool__(self) -> bool:
        return bool(self._items)

    # -- desenho -------------------------------------------------------------
    def lines(self) -> list[str | None]:
        """Linhas internas da caixa (sem a moldura)."""
        inner = box_width(self.cols) - 4
        rst = c(RESET)
        label_width = max(
            (_cell_width(item[1]) for item in self._items if item[0] == "kv"),
            default=0,
        )
        out: list[str | None] = []
        for item in self._items:
            kind = item[0]
            if kind == "kv":
                out.extend(_kv_lines([(item[1], item[2])], inner, label_width))
            elif kind == "msg":
                fancy, plain, color = _TONES.get(item[1], _TONES["info"])
                marca = f"{c(color)}{_glyph(fancy, plain)}{rst}"
                parts = _wrap_lines(item[2], inner - 2)
                out.append(f"{marca} {parts[0]}")
                out.extend("  " + part for part in parts[1:])
            elif kind == "text":
                recuo = " " * item[1]
                out.extend(recuo + p for p in _wrap_lines(item[2], inner - item[1]))
            elif kind == "detail":
                out.extend(
                    f"{' ' * item[1]}{c(GRAY)}{p}{rst}"
                    for p in _wrap_lines(item[2], inner - item[1])
                )
            else:
                out.append(BOX_RULE)
        return out

    def emit(self, lead: bool = True) -> None:
        """Imprime o painel (nada, se estiver vazio)."""
        if self._items:
            emit_box(self.title, self.lines(), cols=self.cols, lead=lead)


# Tons dos resumos: (glifo bonito, glifo ASCII, cor). Mesmo significado de cor
# em todo o programa: verde = ok, amarelo = pulou, vermelho = falhou.
_TONES = {
    "ok": ("\u2714", "+", SUCCESS),
    "warn": ("\u21b7", "-", WARNING),
    "err": ("\u2716", "!", ERROR),
    "info": ("\u2022", "*", HIGHLIGHT),
}


def summary(
    title: Any,
    rows: Iterable[tuple[Any, Any, str]],
    notes: Iterable[Any] = (),
    cols: int | None = None,
    subtitle: str = "",
    elapsed: float | None = None,
    lead: bool = True,
) -> None:
    # Painel de resumo (fim de álbum/faixa/playlist/lote). `rows` são tuplas
    # (rótulo, valor, tom) com tom em ok/warn/err/info; o valor sai alinhado
    # à direita. `notes` aceita dois formatos: tupla (rótulo, valor, tom), que
    # vira uma linha igual às demais (mesmo glifo e mesma cor do tom), ou
    # texto simples, que sai em cinza abaixo de uma divisória (formato antigo).
    # `elapsed` (segundos) acrescenta a linha "Tempo total" por último.
    # `lead=False` tira a linha em branco de antes (para encadear painéis).
    """Print a closed summary box with status glyphs and right-aligned values."""
    inner = box_width(cols) - 4
    rst = c(RESET)
    lines: list[str | None] = []
    if subtitle:  # nome do álbum/playlist: quebra em vez de cortar o título
        lines.extend(f"{c(BG)}{part}{rst}" for part in _wrap_lines(subtitle, inner))
        lines.append(BOX_RULE)

    row_notes = [n for n in notes if isinstance(n, (tuple, list)) and len(n) == 3]
    text_notes = [n for n in notes if n and isinstance(n, str)]
    all_rows = list(rows) + [tuple(n) for n in row_notes]
    if elapsed is not None:
        all_rows.append(elapsed_row(elapsed))

    for label, value, tone in all_rows:
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
    if text_notes:
        lines.append(BOX_RULE)
        for note in text_notes:
            for part in _wrap_lines(note, inner):
                lines.append(f"{c(GRAY)}{part}{rst}")
    emit_box(title, lines, cols=cols, lead=lead)


def summary_downloads(
    kind: Any,
    name: Any,
    success: int,
    total: int,
    skipped: int = 0,
    failed: int = 0,
    notes: Iterable[Any] = (),
    existing: int = 0,
    elapsed: float | None = None,
    total_bytes: float | None = None,
) -> None:
    """Resumo consolidado dos resultados de download.

    `elapsed` (segundos) acrescenta "Tempo total"; `total_bytes` acrescenta
    "Tamanho baixado" e, havendo `elapsed`, a "Velocidade média".
    """
    ok_tone = "ok" if success else ("err" if failed else "info")
    rows = [("Baixadas", f"{success}/{total}", ok_tone)]
    if existing:
        rows.append(("Já existentes", str(existing), "warn"))
    if skipped:
        rows.append(("Puladas / indisponíveis", str(skipped), "warn"))
    if failed:
        rows.append(("Falhas", str(failed), "err"))
    if total_bytes:
        rows.append(("Tamanho baixado", format_bytes(total_bytes), "info"))
        if elapsed:
            rows.append(
                ("Velocidade média", format_speed(total_bytes, elapsed), "info")
            )
    summary(
        f"RESUMO {kind}",
        rows,
        notes=notes,
        subtitle=name,
        elapsed=elapsed,
    )


# --------------------------------------------------------------------------
# Árvore de progresso (glifos com fallback ASCII)
# --------------------------------------------------------------------------
def tree_last() -> str:
    """Ramo final da árvore (``└─``); no fallback ASCII, crase + hífen."""
    return _glyph("\u2514\u2500", "`-")


def dim(text: Any) -> str:
    """Texto em cinza dentro de uma mensagem; sem cor, devolve o texto.

    Usa ``GRAY`` (cinza fixo) e não ``MUTED``: o esmaecido (SGR 2) só reduz a
    intensidade da cor atual, então sobre uma linha verde ou roxa sai um
    verde/roxo desbotado em vez de cinza (visto no a-Shell).
    """
    return f"{c(GRAY)}{text}{c(RESET)}"


_TAIL_GAP = "   "  # respiro entre o nome da faixa e as métricas
_TAIL_MARK = "\x1f"  # separa o nome das métricas até `_tagged` montar a linha


def tail(message: str, text: Any) -> str:
    """Anexa ``text`` ao fim de ``message``; ``_tagged`` põe o respiro e o cinza.

    Só para mensagens de ``ok``/``step``/``warn``/``skip``/``error`` (passam por
    ``_tagged``, que monta a linha e quebra sem partir as métricas).
    """
    if not text:
        return message
    return f"{message}{_TAIL_MARK}{text}"


def join_meta(*parts: Any) -> str:
    """Junta métricas com ``·`` (ou vírgula sem Unicode), ignorando as vazias."""
    return _glyph(" \u00b7 ", ", ").join(str(p) for p in parts if p)


def stats_text(size_bytes: float | None, seconds: float | None) -> str:
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


def format_elapsed(seconds: Any) -> str:
    """Duração para o resumo: ``8.4 s``, ``1 min 42 s``, ``1 h 02 min``."""
    secs = max(0.0, float(seconds))
    if secs < 59.95:
        return f"{secs:.1f} s"
    total = int(round(secs))
    hours, rest = divmod(total, 3600)
    minutes, sec = divmod(rest, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min {sec:02d} s"


def format_bytes(size: Any) -> str:
    """Tamanho legível (base 1000): ``312 MB``, ``3.1 MB``, ``850 KB``."""
    value = max(0.0, float(size))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.0f} {unit}" if value >= 100 else f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.0f} TB"  # inalcançável; satisfaz o type checker


def format_speed(size: Any, seconds: Any) -> str:
    """Velocidade média: ``3.1 MB/s``; vazio se não houver tempo medido."""
    try:
        if float(seconds) <= 0:
            return ""
        return f"{format_bytes(float(size) / float(seconds))}/s"
    except (TypeError, ValueError, ZeroDivisionError):
        return ""


def format_clock(seconds: Any) -> str:
    """Duração de uma faixa/álbum para o cabeçalho: ``06:09`` ou ``01:14:24``."""
    try:
        total = int(round(float(seconds)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, rest = divmod(total, 3600)
    minutes, sec = divmod(rest, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def elapsed_row(seconds: Any) -> tuple[str, str, str]:
    """Linha ``Tempo total`` dos resumos, no mesmo formato das demais."""
    return ("Tempo total", format_elapsed(seconds), "info")


def tree_mid() -> str:
    """Ramo intermediário da árvore (``├─``); ASCII: ``|-``."""
    return _glyph("\u251c\u2500", "|-")


def bar_gauge(value: float, peak: float | None, max_blocks: int | None = None) -> str:
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


def _cell_width(text: Any) -> int:
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


def _wrap_lines(text: Any, limit: Any) -> list[str]:
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


def wrapped(text: Any, indent: int = 0, cols: int | None = None) -> None:
    # Texto corrido quebrado na largura do terminal, com recuo opcional.
    # Emite RESET no início de CADA linha de propósito: garante que texto
    # explicativo sempre saia na cor padrão do terminal, sem herdar cor de
    # quem escreveu antes.
    """Print wrapped text with optional indentation."""
    n = (cols if cols is not None else width()) - indent
    pad = " " * indent
    for line in _wrap_lines(text, n):
        emit(f"{c(RESET)}{pad}{line}")


def truncate(text: Any, limit: Any) -> str:
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


def pad_cells(text: Any, cols: Any) -> str:
    """Completa ``text`` com espaços à direita até ``cols`` células de terminal.

    ``str.ljust``/``f"{x:<n}"`` contam caracteres, não células: emoji e
    caracteres CJK ocupam 2 células e a linha passava da largura da tela.
    """
    text = str(text)
    return text + " " * max(0, int(cols) - _cell_width(text))


def center_cells(text: Any, cols: Any) -> str:
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

    def __init__(self, bypass_quiet: bool = False) -> None:
        """Initialize logging handler that respects quiet mode unless bypass_quiet is True."""
        super().__init__()
        # bypass_quiet=True quando --log-level foi passado explicitamente:
        # nesse caso o filtro de nivel do proprio `logging` (setLevel) ja'
        # decide o que chega aqui, entao aplicar o gate de --quiet DE NOVO
        # em cima seria a pessoa pedir --log-level DEBUG e continuar sem
        # ver debug nenhum. Sem --log-level explicito, mantem o gate: e'
        # o comportamento padrao (quiet/verbose) descrito em --help.
        self.bypass_quiet = bypass_quiet

    def emit(self, record: logging.LogRecord) -> None:
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


def install_logging(level: int | None = None) -> TqdmLoggingHandler:
    # Substitui os handlers do logger raiz pelo TqdmLoggingHandler. Deve
    # ser chamado uma vez, no início da CLI, depois de configure().
    """Configure Python logging to route through the UI system."""
    explicit_level = level is not None
    if level is None:
        if estado.quiet:
            level = logging.WARNING
        elif estado.verbose:
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
    "layout",
    "light_bar_char",
    "ok",
    "print_lock",
    "progress_ncols",
    "raw_height",
    "raw_width",
    "rule",
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
