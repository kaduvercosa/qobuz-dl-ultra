"""Lista de artistas em TABELA com checkbox (``qobuz-dl artists``).

MÓDULO: mesmo visual do modo interativo (título ``=== ... ===``, tabela com
bordas, linha atual com fundo de destaque, ``➤`` e ``◇``/``*`` como checkbox e
cartões nas telas estreitas). A lógica (``ChecklistState``) e o desenho
(``header_fragments``/``list_fragments``/``footer_fragments``) não dependem do
terminal, então são testados sem prompt_toolkit; ``run_checklist`` só liga tudo
a teclado e toque. Também tem as versões em texto (``roster_lines`` e
``release_lines``) usadas fora da tela cheia, e a tela de detalhe do artista
(lançamentos pendentes, abrir perfil, marcar).
ONDE PROCURAR: a confirmação (criar a playlist e gravar no banco) fica em
``artist_review.py``; aqui só se escolhe quem confirmar.
"""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Any, Callable, Optional

from qobuz_dl import ui
from qobuz_dl.color import GRAY, HIGHLIGHT, RESET, SUCCESS, WARNING

PROFILE_URL = "https://open.qobuz.com/artist/{artist_id}"

ACTION_CONFIRM = "confirm"
ACTION_QUIT = "quit"

# Colunas da tabela (largura fixa em células); ARTISTA ocupa o que sobrar.
_COL_NEW = 6
_COL_STATUS = 13
_COL_DATE = 12
_COL_OPEN = 7
_MIN_FLEX = 20
_PREFIX_COLS = 5  # " ➤ ◇ "
OPEN_LABEL = "[abrir]"
HEADERS = ("ARTISTA", "NOVOS", "STATUS", "CONFERIDO EM", "PERFIL")


def profile_url(artist_id: Any) -> str:
    return PROFILE_URL.format(artist_id=artist_id)


def _fold(text: Any) -> str:
    """Minúsculas e sem acentos, para a busca achar 'Beyonce' em 'Beyoncé'."""
    folded = unicodedata.normalize("NFKD", str(text or "").casefold())
    return "".join(ch for ch in folded if not unicodedata.combining(ch))


def _fit(text: Any, cols: int, collapse: bool = True) -> str:
    """Corta ``text`` para caber em ``cols`` células (com reticências) e completa."""
    text = " ".join(str(text).split()) if collapse else str(text)
    cols = max(int(cols), 1)
    if ui._cell_width(text) <= cols:
        return ui.pad_cells(text, cols)
    out = ""
    for ch in text:
        if ui._cell_width(out + ch) > cols - 1:
            break
        out += ch
    return ui.pad_cells(out + "…", cols)


def _center(text: Any, cols: int) -> str:
    text = " ".join(str(text).split())
    width = ui._cell_width(text)
    if width >= cols:
        return _fit(text, cols)
    left = (cols - width) // 2
    return " " * left + text + " " * (cols - width - left)


@dataclass
class Row:
    artist_id: str
    name: str
    verified: bool  # conferido e sem lançamento novo desde então
    pending: int  # lançamentos novos desde a última conferência
    reviewed_date: str  # AAAA-MM-DD da última conferência ("" se nunca)


def row_from_artist(artist: dict) -> Row:
    pending = int(artist.get("pending_count") or 0)
    reviewed = str(artist.get("last_reviewed_at") or "")
    return Row(
        artist_id=str(artist["source_artist_id"]),
        name=str(artist.get("name") or "Unknown"),
        verified=bool(reviewed) and pending == 0,
        pending=pending,
        reviewed_date=reviewed[:10],
    )


# ---------------------------------------------------------------------------
# Tabela (mesma geometria do modo interativo)
# ---------------------------------------------------------------------------


def table_layout(columns: int, prefix_len: int = _PREFIX_COLS, with_open: bool = True):
    """Devolve ``(é_tabela, larguras, cabeçalhos, bordas)``.

    Cai no modo cartão (``False``) quando sobra menos de ``_MIN_FLEX`` células
    para o nome do artista. ``with_open=False`` tira a coluna PERFIL (só útil
    com toque/clique).
    """
    fixed = [_COL_NEW, _COL_STATUS, _COL_DATE] + ([_COL_OPEN] if with_open else [])
    seps = len(fixed)  # separadores " │ " entre as colunas
    flex = int(columns) - prefix_len - 4 - sum(fixed) - 3 * seps
    if flex < _MIN_FLEX:
        return False, [], [], {}
    widths = [flex] + fixed
    headers = list(HEADERS[: len(widths)])
    borders = {
        "top": "┌─" + "─┬─".join("─" * w for w in widths) + "─┐",
        "mid": "├─" + "─┼─".join("─" * w for w in widths) + "─┤",
        "bot": "└─" + "─┴─".join("─" * w for w in widths) + "─┘",
    }
    return True, widths, headers, borders


class ChecklistState:
    """Estado da lista: quem está conferido, quem foi marcado agora e a busca."""

    def __init__(self, artists: list[dict], releases: Optional[dict] = None) -> None:
        self.rows: list[Row] = []
        self.selected: set[str] = set()  # a confirmar (cria playlist e grava)
        self.undo: set[str] = set()  # conferidos que serão reabertos
        self.releases: dict[str, list[dict]] = dict(releases or {})
        self.cursor = 0
        self.query = ""
        self.search_mode = False
        self.only_pending = False
        self.sort_news_first = False
        self.message = ""
        self.quit_armed = False
        self.undo_armed = False
        self.view = "list"  # "list" ou "detail" (tela do artista)
        self.detail_id = ""
        self.detail_scroll = 0
        self.on_change: Optional[Callable[["ChecklistState"], None]] = None
        self.load(artists)

    def _notify(self) -> None:
        if self.on_change is not None:
            self.on_change(self)

    # -- dados ---------------------------------------------------------------

    def load(
        self,
        artists: list[dict],
        *,
        keep_selected: Optional[set] = None,
        keep_undo: Optional[set] = None,
        releases: Optional[dict] = None,
    ) -> None:
        """Recarrega as linhas mantendo o cursor no mesmo artista.

        ``keep_selected``/``keep_undo``: ids que devem continuar marcados (os que
        falharam). Sem isso, a seleção só mantém quem ainda faz sentido: marcados
        a confirmar não conferidos e marcados a desfazer ainda conferidos.
        """
        current = self.current()
        current_id = current.artist_id if current else None
        self.rows = [row_from_artist(a) for a in artists]
        by_id = {row.artist_id: row for row in self.rows}
        previous = self.selected if keep_selected is None else set(keep_selected)
        self.selected = {i for i in previous if i in by_id and not by_id[i].verified}
        previous_undo = self.undo if keep_undo is None else set(keep_undo)
        self.undo = {i for i in previous_undo if i in by_id and by_id[i].verified}
        if releases is not None:
            self.releases = dict(releases)
        self.quit_armed = False
        self.undo_armed = False
        if self.view == "detail" and self.detail_id not in by_id:
            self.view = "list"
        for index, row in enumerate(self.visible()):
            if row.artist_id == current_id:
                self.cursor = index
                break
        self._clamp()

    def restore_selected(self, ids: Any) -> int:
        """Remarca ids guardados de uma sessão anterior; devolve quantos valeram."""
        valid = {
            row.artist_id
            for row in self.rows
            if row.artist_id in {str(i) for i in ids} and not row.verified
        }
        self.selected |= valid
        return len(valid)

    def visible(self) -> list[Row]:
        needle = _fold(self.query).strip()
        rows = self.rows
        if self.only_pending:
            rows = [r for r in rows if not r.verified]
        if needle:
            rows = [r for r in rows if needle in _fold(r.name)]
        if self.sort_news_first:
            # sort estável: dentro de cada grupo continua a ordem alfabética
            rows = sorted(rows, key=lambda r: 0 if r.pending else 1)
        return rows

    def current(self) -> Optional[Row]:
        visible = self.visible()
        if 0 <= self.cursor < len(visible):
            return visible[self.cursor]
        return None

    def selected_ids(self) -> list[str]:
        """Ids marcados, na ordem em que aparecem na lista (alfabética)."""
        return [row.artist_id for row in self.rows if row.artist_id in self.selected]

    def undo_ids(self) -> list[str]:
        return [row.artist_id for row in self.rows if row.artist_id in self.undo]

    def counts(self) -> tuple[int, int, int]:
        verified = sum(1 for row in self.rows if row.verified)
        return verified, len(self.rows), len(self.selected)

    # -- navegação -----------------------------------------------------------

    def _clamp(self) -> None:
        last = len(self.visible()) - 1
        self.cursor = max(0, min(self.cursor, last)) if last >= 0 else 0

    def move(self, delta: int) -> None:
        self.cursor += delta
        self._clamp()
        self.quit_armed = False
        self.undo_armed = False

    def first(self) -> None:
        self.cursor = 0

    def last(self) -> None:
        self.cursor = max(0, len(self.visible()) - 1)

    # -- ações ---------------------------------------------------------------

    def toggle(self, index: Optional[int] = None) -> bool:
        """Marca/desmarca o artista (índice na lista visível; padrão: o cursor).

        Pendente: marca para confirmar. Já conferido: marca para DESFAZER a
        conferência (só vale depois do Enter; a playlist não é apagada).
        """
        visible = self.visible()
        if index is not None:
            if not 0 <= index < len(visible):
                return False
            self.cursor = index
        row = self.current()
        self.quit_armed = False
        self.undo_armed = False
        if row is None:
            return False
        if row.verified:
            if row.artist_id in self.undo:
                self.undo.discard(row.artist_id)
                self.message = ""
            else:
                self.undo.add(row.artist_id)
                self.message = (
                    f"{row.name}: conferência será desfeita no Enter "
                    "(a playlist não é apagada)."
                )
        elif row.artist_id in self.selected:
            self.selected.discard(row.artist_id)
            self.message = ""
        else:
            self.selected.add(row.artist_id)
            self.message = ""
        self._notify()
        return True

    def select_all_visible(self) -> int:
        """Marca todos os pendentes visíveis; se já estavam todos, desmarca.

        Respeita busca e filtro. Devolve quantos ficaram marcados/desmarcados.
        """
        pending = [r for r in self.visible() if not r.verified]
        self.quit_armed = False
        self.undo_armed = False
        if not pending:
            self.message = "Nenhum pendente visível para marcar."
            return 0
        ids = {r.artist_id for r in pending}
        if ids <= self.selected:
            self.selected -= ids
            self.message = f"{len(ids)} desmarcado(s)."
        else:
            self.selected |= ids
            self.message = f"{len(ids)} marcado(s). Enter confirma."
        self._notify()
        return len(ids)

    def toggle_sort(self) -> None:
        """Alterna entre ordem alfabética e 'novos primeiro' (cursor acompanha)."""
        current = self.current()
        self.sort_news_first = not self.sort_news_first
        if current is not None:
            for index, row in enumerate(self.visible()):
                if row.artist_id == current.artist_id:
                    self.cursor = index
                    break
        self._clamp()

    def start_search(self) -> None:
        self.search_mode = True

    def type_char(self, char: str) -> None:
        self.query += char
        self.cursor = 0

    def backspace(self) -> None:
        self.query = self.query[:-1]
        self.cursor = 0

    def end_search(self) -> None:
        self.search_mode = False
        self._clamp()

    def clear_filters(self) -> bool:
        """Limpa busca e filtro. Devolve True se havia algo para limpar."""
        had = bool(self.query) or self.only_pending or self.search_mode
        self.query = ""
        self.only_pending = False
        self.search_mode = False
        self.cursor = 0
        return had

    def toggle_only_pending(self) -> None:
        self.only_pending = not self.only_pending
        self.cursor = 0

    def request_quit(self) -> bool:
        """Devolve True se pode sair; com marcados sem confirmar, pede um 2º aviso."""
        pending = len(self.selected) + len(self.undo)
        if pending and not self.quit_armed:
            self.quit_armed = True
            self.message = (
                f"{pending} marcado(s) sem confirmar: Enter confirma; "
                "q de novo descarta."
            )
            return False
        return True

    def request_confirm(self) -> bool:
        """Devolve True quando o Enter deve confirmar agora.

        Desfazer conferências pede um segundo Enter, porque o engano seria
        silencioso (as playlists continuam no Qobuz).
        """
        if not self.selected and not self.undo:
            self.message = "Nada marcado. Use Espaço para marcar quem você conferiu."
            return False
        if self.undo and not self.undo_armed:
            self.undo_armed = True
            self.message = (
                f"Desfazer {len(self.undo)} conferência(s)? "
                "Enter de novo confirma; Espaço cancela."
            )
            return False
        return True

    # -- tela do artista -----------------------------------------------------

    def open_detail(self) -> bool:
        row = self.current()
        if row is None:
            return False
        self.view = "detail"
        self.detail_id = row.artist_id
        self.detail_scroll = 0
        return True

    def close_detail(self) -> None:
        self.view = "list"

    def detail_row(self) -> Optional[Row]:
        for row in self.rows:
            if row.artist_id == self.detail_id:
                return row
        return None

    def detail_releases(self) -> list[dict]:
        return list(self.releases.get(self.detail_id, []))

    def scroll_detail(self, delta: int) -> None:
        self.detail_scroll = max(0, self.detail_scroll + delta)

    def toggle_detail(self) -> bool:
        """Marca/desmarca o artista da tela de detalhe."""
        for index, row in enumerate(self.visible()):
            if row.artist_id == self.detail_id:
                return self.toggle(index)
        return False

    # -- conteúdo das células ---------------------------------------------------

    def status_of(self, row: Row) -> tuple[str, str]:
        """(texto, tipo) do estado; o tipo escolhe a cor: done/picked/new/plain."""
        if row.artist_id in self.undo:
            return "A DESFAZER", "undo"
        if row.verified:
            return "CONFERIDO", "done"
        if row.artist_id in self.selected:
            return "A CONFIRMAR", "picked"
        if row.pending:
            return "COM NOVOS", "new"
        return "PENDENTE", "plain"

    def check_glyph(self, row: Row) -> str:
        if row.artist_id in self.undo:
            return "↺"
        if row.verified:
            return "✔"
        return "*" if row.artist_id in self.selected else "◇"

    def cells(self, row: Row) -> list[tuple[str, str]]:
        """(texto, tipo) das 5 colunas: ARTISTA, NOVOS, STATUS, DATA, PERFIL."""
        status, kind = self.status_of(row)
        return [
            (row.name, "done" if row.verified else "name"),
            (str(row.pending) if row.pending else "-", "new" if row.pending else "dim"),
            (status, kind),
            (row.reviewed_date or "-", "dim"),
            (OPEN_LABEL, "open"),
        ]

    # -- textos de título e rodapé ----------------------------------------------

    def title(self) -> str:
        if self.view == "detail":
            row = self.detail_row()
            return f"=== {row.name if row else 'ARTISTA'} ==="
        return "=== ARTISTAS SEGUIDOS ==="

    def footer_lines(self, columns: int) -> list[tuple[str, str]]:
        """Linhas do rodapé como (classe de estilo, texto); sempre a mesma altura."""
        width = max(10, int(columns) - 1)
        verified, total, picked = self.counts()
        counter = f"Conferidos: {verified}/{total} · Marcados: {picked}"
        if self.undo:
            counter += f" · A desfazer: {len(self.undo)}"
        if self.view == "list":
            if self.search_mode:
                counter += f" · Busca: {self.query}▏"
            elif self.query:
                counter += f" · Busca: {self.query}"
            if self.only_pending:
                counter += " · só pendentes"
            if self.sort_news_first:
                counter += " · novos primeiro"
        if self.view == "detail":
            keys = "↑↓ rolar · O abre o perfil · Espaço marca · Esc volta"
        elif self.search_mode:
            keys = "Digite para buscar · Enter/Esc fecha a busca"
        else:
            keys = (
                "↑↓ mover · Espaço marca · A todos · D detalhes · O perfil · "
                "S ordem · / busca · F pendentes · Enter confirma · Q sai"
            )
        lines = [("class:message", _fit(self.message, width).rstrip())]
        lines.append(("class:highlight", _fit(counter, width).rstrip()))
        lines.extend(("class:footer", part) for part in ui._wrap_lines(keys, width))
        return lines


# ---------------------------------------------------------------------------
# Desenho (fragmentos de texto estilizado; sem prompt_toolkit)
# ---------------------------------------------------------------------------

Handlers = Optional[Callable[[str, int], Any]]


def _table_cols(columns: int) -> int:
    # Mesma folga do modo interativo: 2 colunas livres no fim da linha.
    return max(32, int(columns) - 2)


def header_height(columns: int, state: Optional[ChecklistState] = None) -> int:
    if state is not None and state.view == "detail":
        return 1
    return 4 if table_layout(_table_cols(columns))[0] else 1


def header_fragments(state: ChecklistState, columns: int) -> list[tuple]:
    is_table, widths, headers, borders = table_layout(_table_cols(columns))
    title = _fit(state.title(), max(4, columns - 2)).rstrip()
    res: list[tuple] = [("class:title", f" {title}")]
    if is_table and state.view == "list":
        pad = " " * _PREFIX_COLS
        res.append(("class:meta", "\n" + pad + borders["top"] + "\n"))
        res.append(("class:meta", pad + "│ "))
        for idx, (text, width) in enumerate(zip(headers, widths)):
            shaped = _fit(text, width) if idx == 0 else _center(text, width)
            res.append(("class:table_header", shaped))
            res.append(("class:meta", " │ " if idx < len(headers) - 1 else " │\n"))
        res.append(("class:meta", pad + borders["mid"]))
    return res


def footer_height(state: ChecklistState, columns: int) -> int:
    return len(state.footer_lines(columns))


def footer_fragments(state: ChecklistState, columns: int) -> list[tuple]:
    lines = state.footer_lines(columns)
    res: list[tuple] = []
    for index, (style, text) in enumerate(lines):
        res.append((style, text + ("\n" if index < len(lines) - 1 else "")))
    return res


_CELL_STYLES = {
    "name": "class:item_title",
    "done": "class:done",
    "picked": "class:highlight",
    "new": "class:new",
    "undo": "class:undo",
    "dim": "class:dim",
    "open": "class:open",
    "plain": "",
}


def _cell_style(kind: str, hovered: bool) -> str:
    return "class:hovered" if hovered else _CELL_STYLES[kind]


def _frag(style: str, text: str, handler: Any = None) -> tuple:
    return (style, text, handler) if handler is not None else (style, text)


def _fill(pieces: list[tuple], columns: int, hovered: bool) -> list[tuple]:
    """Completa a linha até a borda; linha atual leva o fundo de destaque."""
    used = sum(ui._cell_width(p[1]) for p in pieces)
    out = list(pieces)
    pad = max(0, columns - used)
    if pad:
        out.append(("class:hovered" if hovered else "", " " * pad))
    out.append(("", "\n"))
    return out


def list_fragments(
    state: ChecklistState,
    columns: int,
    avail: int,
    view: dict,
    handlers: Handlers = None,
) -> list[tuple]:
    """Desenha só as linhas que cabem em ``avail`` (fatia centrada no cursor).

    ``view`` guarda ``top`` (primeiro item visível) e ``per_page`` entre
    desenhos. ``handlers(kind, índice)`` devolve o tratador de mouse para
    ``"toggle"`` (marcar), ``"detail"`` (abrir a tela do artista) ou ``"open"``
    (abrir perfil); sem ele, só texto.
    """
    visible = state.visible()
    if not visible:
        text = (
            "Nenhum artista encontrado para essa busca."
            if (state.query or state.only_pending)
            else "Nenhum artista seguido."
        )
        view["top"], view["per_page"] = 0, 1
        return [("class:meta", " " + text)]

    is_table, widths, _headers, borders = table_layout(_table_cols(columns))
    per_item = 2 if is_table else 4
    k = max(1, int(avail) // per_item)
    margin = 1 if k >= 3 else 0
    top = int(view.get("top", 0))
    cursor = state.cursor
    if cursor < top + margin:
        top = cursor - margin
    elif cursor > top + k - 1 - margin:
        top = cursor - k + 1 + margin
    top = max(0, min(top, max(0, len(visible) - k)))
    view["top"], view["per_page"] = top, k
    shown = range(top, min(len(visible), top + k))

    res: list[tuple] = []

    def h(kind: str, index: int):
        return handlers(kind, index) if handlers else None

    for index in shown:
        row = visible[index]
        hovered = index == state.cursor
        selected = row.artist_id in state.selected
        ptr = "➤" if hovered else " "
        glyph = state.check_glyph(row)
        cells = state.cells(row)

        def style_for(kind: str, hovered: bool = hovered) -> str:
            return _cell_style(kind, hovered)

        glyph_style = (
            "class:hovered"
            if hovered
            else "class:done"
            if row.verified
            else "class:highlight"
            if selected
            else "class:checkbox"
        )
        border = "class:hovered" if hovered else "class:meta"

        if is_table:
            pieces = [
                _frag(
                    "class:hovered" if hovered else "", f" {ptr} ", h("toggle", index)
                ),
                _frag(glyph_style, f"{glyph} ", h("toggle", index)),
                _frag(border, "│ ", h("toggle", index)),
            ]
            for col, ((text, kind), width) in enumerate(zip(cells, widths)):
                shaped = _fit(text, width) if col == 0 else _center(text, width)
                click = ("detail", "toggle", "toggle", "toggle", "open")[col]
                handler = h(click, index)
                pieces.append(_frag(style_for(kind), shaped, handler))
                if col < len(widths) - 1:
                    pieces.append(_frag(border, " │ ", h("toggle", index)))
            pieces.append(_frag(border, " │", h("toggle", index)))
            res.extend(_fill(pieces, columns, hovered))
            edge = borders["bot"] if index == shown[-1] else borders["mid"]
            res.extend(
                _fill([("class:meta", " " * _PREFIX_COLS + edge)], columns, False)
            )
        else:
            inner = max(10, columns - 6)
            thick = ("┏", "┓", "┗", "┛", "━")
            thin = ("╭", "╮", "╰", "╯", "─")
            tl, tr, bl, br, hz = thick if selected else thin
            card_border = (
                "class:hovered"
                if hovered
                else "class:highlight"
                if selected
                else "class:meta"
            )
            name, name_kind = cells[0]
            status, status_kind = cells[2]
            date = cells[3][0]
            lead = f"{ptr} {glyph} "
            first = _fit(f"👤 {name}", inner - len(lead), collapse=False)
            bits = [status]
            if date != "-":
                bits.append(date)
            if row.pending:
                bits.append(f"{row.pending} novos")
            detail = _fit(
                "     " + " · ".join(bits), inner - len(OPEN_LABEL) - 1, collapse=False
            )
            res.append((card_border, f" {tl}{hz * (inner + 2)}{tr}\n"))
            res.extend(
                _fill(
                    [
                        _frag(card_border, " │ ", h("toggle", index)),
                        _frag(glyph_style, lead, h("toggle", index)),
                        _frag(style_for(name_kind), first, h("detail", index)),
                        _frag(card_border, " │", h("toggle", index)),
                    ],
                    columns,
                    False,
                )
            )
            res.extend(
                _fill(
                    [
                        _frag(card_border, " │ ", h("toggle", index)),
                        _frag(style_for(status_kind), detail, h("toggle", index)),
                        _frag(style_for("open"), " " + OPEN_LABEL, h("open", index)),
                        _frag(card_border, " │", h("toggle", index)),
                    ],
                    columns,
                    False,
                )
            )
            res.append((card_border, f" {bl}{hz * (inner + 2)}{br}\n"))

    if res and res[-1][1].endswith("\n"):
        res[-1] = (res[-1][0], res[-1][1][:-1])
    return res


def _toggle_label(state: ChecklistState, row: Row) -> str:
    if row.verified:
        return "[cancelar]" if row.artist_id in state.undo else "[desfazer]"
    return "[desmarcar]" if row.artist_id in state.selected else "[marcar]"


def detail_fragments(
    state: ChecklistState,
    columns: int,
    avail: int,
    handlers: Handlers = None,
) -> list[tuple]:
    """Tela do artista: estado, perfil, botões e a tabela de lançamentos pendentes."""
    row = state.detail_row()
    if row is None:
        return [("class:meta", " Artista não encontrado.")]

    def h(kind: str):
        return handlers(kind, 0) if handlers else None

    status, kind = state.status_of(row)
    glyph = state.check_glyph(row)
    info = [f"{glyph} {status}"]
    if row.reviewed_date:
        info.append(f"conferido em {row.reviewed_date}")
    if row.pending:
        info.append(f"{row.pending} novo(s)")
    lines: list[list[tuple]] = [
        [("class:item_title", " " + _fit(row.name, columns - 2).rstrip())],
        [(_cell_style(kind, False) or "class:meta", " " + " · ".join(info))],
        [
            ("class:meta", f" Perfil: open.qobuz.com/artist/{row.artist_id} "),
            _frag("class:open", OPEN_LABEL, h("detail_open")),
        ],
        [
            _frag("class:open", " " + _toggle_label(state, row), h("detail_toggle")),
            ("", "  "),
            _frag("class:open", "[voltar]", h("detail_back")),
        ],
        [("", "")],
    ]
    releases = state.detail_releases()
    if not releases:
        lines.append([("class:dim", " Nenhum lançamento pendente registrado.")])
    else:
        flex = (columns - 2) - 33
        compact = flex < _MIN_FLEX
        overhead = 0 if compact else 4
        room = max(1, int(avail) - len(lines) - overhead - 1)
        top = max(0, min(state.detail_scroll, max(0, len(releases) - room)))
        state.detail_scroll = top
        shown = releases[top : top + room]
        data = [
            (
                str(r.get("title") or "Unknown"),
                str(r.get("release_type") or "-"),
                str(r.get("release_date") or "-"),
            )
            for r in shown
        ]
        if compact:
            for title, kind_, date in data:
                tail = " ".join(x for x in (kind_, date) if x != "-")
                space = max(8, columns - 4 - len(tail) - 1)
                lines.append(
                    [
                        ("class:highlight", " • "),
                        ("class:item_title", _fit(title, space).rstrip()),
                        ("class:dim", " " + tail),
                    ]
                )
        else:
            widths = [flex, _COL_TYPE, _COL_RELEASE_DATE]
            top_b = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
            mid_b = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
            bot_b = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"
            sep = ("class:meta", " │ ")
            lines.append([("class:meta", " " + top_b)])
            lines.append(
                [
                    ("class:meta", " │ "),
                    ("class:table_header", _fit("LANÇAMENTO", flex)),
                    sep,
                    ("class:table_header", _center("TIPO", _COL_TYPE)),
                    sep,
                    ("class:table_header", _center("DATA", _COL_RELEASE_DATE)),
                    ("class:meta", " │"),
                ]
            )
            lines.append([("class:meta", " " + mid_b)])
            for title, kind_, date in data:
                lines.append(
                    [
                        ("class:meta", " │ "),
                        ("class:item_title", _fit(title, flex)),
                        sep,
                        ("class:dim", _center(kind_, _COL_TYPE)),
                        sep,
                        ("class:dim", _center(date, _COL_RELEASE_DATE)),
                        ("class:meta", " │"),
                    ]
                )
            lines.append([("class:meta", " " + bot_b)])
        if len(releases) > room:
            lines.append(
                [
                    (
                        "class:dim",
                        f" mostrando {top + 1}-{top + len(shown)} de {len(releases)}"
                        " · ↑↓ rola",
                    )
                ]
            )
    res: list[tuple] = []
    for index, line in enumerate(lines):
        res.extend(line)
        if index < len(lines) - 1:
            res.append(("", "\n"))
    return res


# ---------------------------------------------------------------------------
# Versões em texto (fora da tela cheia): prompt de texto e sorteio
# ---------------------------------------------------------------------------


def _paint(color: str, text: str) -> str:
    return f"{ui.c(color)}{text}{ui.c(RESET)}" if color else text


def roster_lines(rows: list[Row], columns: int) -> list[str]:
    """Tabela numerada e colorida para o prompt de texto (``o 3``, ``m 1,3``).

    Em tela estreita vira uma lista compacta de uma linha por artista.
    """
    num_w = max(2, len(str(len(rows))))
    prefix_len = num_w + 1
    is_table, widths, headers, borders = table_layout(
        max(32, int(columns) - 1), prefix_len=prefix_len, with_open=False
    )
    state = ChecklistState([])
    state.rows = rows
    lines: list[str] = []
    if is_table:
        pad = " " * prefix_len
        head = [
            _fit(t, w) if i == 0 else _center(t, w)
            for i, (t, w) in enumerate(zip(headers, widths))
        ]
        lines.append(_paint(GRAY, pad + borders["top"]))
        lines.append(
            pad
            + _paint(GRAY, "│ ")
            + _paint(GRAY, " │ ").join(_paint(HIGHLIGHT, h) for h in head)
            + _paint(GRAY, " │")
        )
        lines.append(_paint(GRAY, pad + borders["mid"]))
    colors = {
        "name": HIGHLIGHT,
        "done": SUCCESS,
        "picked": HIGHLIGHT,
        "new": WARNING,
        "undo": WARNING,
        "dim": GRAY,
        "plain": "",
    }
    for number, row in enumerate(rows, start=1):
        cells = state.cells(row)[:4]
        index = _paint(HIGHLIGHT, f"{number:>{num_w}}") + " "
        if is_table:
            parts = []
            for col, ((text, kind), wd) in enumerate(zip(cells, widths)):
                shaped = _fit(text, wd) if col == 0 else _center(text, wd)
                parts.append(_paint(colors[kind], shaped))
            bar = _paint(GRAY, " │ ")
            lines.append(
                index + _paint(GRAY, "│ ") + bar.join(parts) + _paint(GRAY, " │")
            )
        else:
            status, kind = state.status_of(row)
            mark = "[x]" if row.verified else "[ ]"
            name_room = max(8, int(columns) - num_w - 1 - 4 - 1 - _COL_STATUS)
            name_color = colors["done" if row.verified else "name"]
            lines.append(
                index
                + _paint(SUCCESS if row.verified else "", mark)
                + " "
                + _paint(name_color, _fit(row.name, name_room))
                + " "
                + _paint(colors[kind], status)
            )
    if is_table:
        lines.append(_paint(GRAY, " " * prefix_len + borders["bot"]))
    return lines


_COL_TYPE = 10
_COL_RELEASE_DATE = 12


def release_lines(releases: list[dict], columns: int) -> list[str]:
    """Tabela dos lançamentos pendentes de um artista (TÍTULO │ TIPO │ DATA)."""
    if not releases:
        return [_paint(GRAY, "Nenhum lançamento pendente registrado.")]
    fixed = [_COL_TYPE, _COL_RELEASE_DATE]
    flex = max(32, int(columns) - 1) - 4 - sum(fixed) - 3 * len(fixed)
    rows = [
        (
            str(r.get("title") or "Unknown"),
            str(r.get("release_type") or "-"),
            str(r.get("release_date") or "-"),
        )
        for r in releases
    ]
    if flex < _MIN_FLEX:
        lines = []
        for title, kind, date in rows:
            tail = " ".join(x for x in (kind, date) if x != "-")
            room = max(8, int(columns) - 1 - 2 - (len(tail) + 1 if tail else 0))
            lines.append(
                f"{_paint(HIGHLIGHT, '•')} {_fit(title, room).rstrip()} "
                f"{_paint(GRAY, tail)}".rstrip()
            )
        return lines
    widths = [flex] + fixed
    heads = ["LANÇAMENTO", "TIPO", "DATA"]
    top = "┌─" + "─┬─".join("─" * w for w in widths) + "─┐"
    mid = "├─" + "─┼─".join("─" * w for w in widths) + "─┤"
    bot = "└─" + "─┴─".join("─" * w for w in widths) + "─┘"
    sep = _paint(GRAY, " │ ")
    lines = [
        _paint(GRAY, top),
        _paint(GRAY, "│ ")
        + sep.join(
            _paint(HIGHLIGHT, _fit(t, w) if i == 0 else _center(t, w))
            for i, (t, w) in enumerate(zip(heads, widths))
        )
        + _paint(GRAY, " │"),
        _paint(GRAY, mid),
    ]
    for title, kind, date in rows:
        cells = [
            _paint(HIGHLIGHT, _fit(title, widths[0])),
            _paint(GRAY, _center(kind, widths[1])),
            _paint(GRAY, _center(date, widths[2])),
        ]
        lines.append(_paint(GRAY, "│ ") + sep.join(cells) + _paint(GRAY, " │"))
    lines.append(_paint(GRAY, bot))
    return lines


# ---------------------------------------------------------------------------
# Tela (prompt_toolkit)
# ---------------------------------------------------------------------------


async def run_checklist(
    state: ChecklistState,
    *,
    url_opener: Callable[[str], bool],
) -> tuple[str, list[str]]:
    """Mostra a tabela e devolve ``("confirm", ids)`` ou ``("quit", [])``.

    Teclado (PC/iPad): setas, PgUp/PgDn, Home/End, Espaço, A (todos), D (tela do
    artista), O (perfil), S (ordem), /, F, Enter, Q.
    Toque/clique: tocar no nome abre a tela do artista; tocar na caixa, no STATUS
    ou nas bordas marca; tocar em [abrir] abre o perfil.
    Ctrl+C levanta KeyboardInterrupt, como os outros prompts do projeto.
    """
    import shutil

    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.keys import Keys
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.mouse_events import MouseEventType
    from prompt_toolkit.styles import Style, merge_styles

    from qobuz_dl.interactive_ui import _hex_accent, pt_style

    view: dict = {"top": 0, "per_page": 5, "rechecks": 0, "avail": 0}

    def size() -> tuple[int, int]:
        try:
            out = get_app().output.get_size()
            return int(out.columns), int(out.rows)
        except Exception:
            cols, rows = shutil.get_terminal_size((80, 24))
            return cols, rows

    def open_profile(row) -> None:
        url = profile_url(row.artist_id)
        try:
            ok = bool(url_opener(url))
        except Exception:
            ok = False
        state.message = (
            f"Perfil aberto: {row.name}." if ok else f"Não consegui abrir: {url}"
        )

    def handlers(kind: str, index: int):
        def on_click(event):
            if event.event_type != MouseEventType.MOUSE_UP:
                return NotImplemented
            visible = state.visible()
            if kind == "open":
                if 0 <= index < len(visible):
                    state.cursor = index
                    open_profile(visible[index])
            elif kind == "detail":
                if 0 <= index < len(visible):
                    state.cursor = index
                    state.open_detail()
            elif kind == "detail_open":
                row = state.detail_row()
                if row is not None:
                    open_profile(row)
            elif kind == "detail_toggle":
                state.toggle_detail()
            elif kind == "detail_back":
                state.close_detail()
            else:
                state.toggle(index)
            get_app().invalidate()
            return None

        return on_click

    def get_header():
        return header_fragments(state, size()[0])

    def get_list():
        columns, term_rows = size()
        info = listing.render_info
        if info is not None and info.window_height > 0:
            avail = info.window_height
        else:
            # -1: a linha de segurança embaixo também sai da altura útil.
            avail = (
                term_rows
                - header_height(columns, state)
                - footer_height(state, columns)
                - 1
            )
        view["avail"] = avail
        _schedule_recheck()
        # O Window guarda a rolagem anterior; a fatia já cabe, então força 0.
        listing.vertical_scroll = 0
        if state.view == "detail":
            return detail_fragments(state, columns, avail, handlers)
        return list_fragments(state, columns, avail, view, handlers)

    def _schedule_recheck():
        # Mesma correção do modo interativo (última linha cortada no a-Shell até
        # o primeiro toque): se a altura medida difere da usada, pede um
        # redesenho, no máximo 3 seguidos.
        import asyncio

        def _recheck():
            try:
                info = listing.render_info
                real = info.window_height if info is not None else 0
                if real > 0 and real != view["avail"]:
                    if view["rechecks"] < 3:
                        view["rechecks"] += 1
                        get_app().invalidate()
                else:
                    view["rechecks"] = 0
            except Exception:
                pass

        try:
            asyncio.get_running_loop().call_soon(_recheck)
        except RuntimeError:
            pass

    def get_footer():
        return footer_fragments(state, size()[0])

    header = Window(
        FormattedTextControl(get_header),
        height=lambda: Dimension.exact(header_height(size()[0], state)),
        dont_extend_height=True,
    )
    listing = Window(
        FormattedTextControl(get_list, focusable=True, show_cursor=False),
        wrap_lines=False,
        always_hide_cursor=True,
    )
    footer = Window(
        FormattedTextControl(get_footer),
        height=lambda: Dimension.exact(footer_height(state, size()[0])),
        dont_extend_height=True,
    )
    # Alguns terminais móveis (a-Shell com a barra de atalhos) anunciam mais
    # linhas do que as visíveis: uma linha de segurança mantém o rodapé à vista.
    safe_area = Window(
        FormattedTextControl(" "),
        height=Dimension.exact(1),
        dont_extend_height=True,
    )

    in_list = Condition(lambda: state.view == "list")
    in_detail = Condition(lambda: state.view == "detail")
    searching = Condition(lambda: state.search_mode)
    normal = in_list & ~searching
    kb = KeyBindings()

    def page() -> int:
        return max(1, int(view.get("per_page", 5)) - 1)

    @kb.add("up", filter=in_list)
    def _up(event):
        state.move(-1)

    @kb.add("down", filter=in_list)
    def _down(event):
        state.move(1)

    @kb.add("pageup", filter=in_list)
    def _pgup(event):
        state.move(-page())

    @kb.add("pagedown", filter=in_list)
    def _pgdn(event):
        state.move(page())

    @kb.add("home", filter=normal)
    def _home(event):
        state.first()

    @kb.add("end", filter=normal)
    def _end(event):
        state.last()

    @kb.add("space", filter=normal)
    @kb.add("x", filter=normal)
    def _toggle(event):
        state.toggle()

    @kb.add("a", filter=normal)
    def _all(event):
        state.select_all_visible()

    @kb.add("s", filter=normal)
    def _sort(event):
        state.toggle_sort()

    @kb.add("d", filter=normal)
    @kb.add("right", filter=normal)
    def _detail(event):
        state.open_detail()

    @kb.add("o", filter=normal)
    def _open(event):
        row = state.current()
        if row is not None:
            open_profile(row)

    @kb.add("/", filter=normal)
    def _search(event):
        state.start_search()

    @kb.add("f", filter=normal)
    def _filter(event):
        state.toggle_only_pending()

    @kb.add("enter", filter=normal)
    def _confirm(event):
        if state.request_confirm():
            event.app.exit(result=(ACTION_CONFIRM, state.selected_ids()))

    @kb.add("q", filter=normal)
    def _quit(event):
        if state.request_quit():
            event.app.exit(result=(ACTION_QUIT, []))

    @kb.add("escape", filter=normal, eager=True)
    def _esc(event):
        state.clear_filters()

    @kb.add("enter", filter=searching)
    @kb.add("escape", filter=searching, eager=True)
    def _end_search(event):
        state.end_search()

    @kb.add("backspace", filter=searching)
    def _backspace(event):
        state.backspace()

    @kb.add(Keys.Any, filter=searching)
    def _typed(event):
        if event.data and event.data.isprintable():
            state.type_char(event.data)

    # -- tela do artista --
    @kb.add("up", filter=in_detail)
    def _d_up(event):
        state.scroll_detail(-1)

    @kb.add("down", filter=in_detail)
    def _d_down(event):
        state.scroll_detail(1)

    @kb.add("pageup", filter=in_detail)
    def _d_pgup(event):
        state.scroll_detail(-page())

    @kb.add("pagedown", filter=in_detail)
    def _d_pgdn(event):
        state.scroll_detail(page())

    @kb.add("o", filter=in_detail)
    def _d_open(event):
        row = state.detail_row()
        if row is not None:
            open_profile(row)

    @kb.add("space", filter=in_detail)
    @kb.add("x", filter=in_detail)
    def _d_toggle(event):
        state.toggle_detail()

    @kb.add("escape", filter=in_detail, eager=True)
    @kb.add("left", filter=in_detail)
    @kb.add("backspace", filter=in_detail)
    @kb.add("q", filter=in_detail)
    @kb.add("enter", filter=in_detail)
    def _d_back(event):
        state.close_detail()

    @kb.add("c-c")
    def _interrupt(event):
        event.app.exit(exception=KeyboardInterrupt)

    extra = Style.from_dict(
        {
            "done": "fg:#5fb760",
            "dim": "fg:#808080",
            "new": "fg:#e0a030",
            "undo": "fg:#d9534f",
            "open": f"fg:{_hex_accent} underline",
            "message": f"fg:{_hex_accent}",
        }
    )
    app: Application = Application(
        layout=Layout(
            HSplit([header, listing, footer, safe_area]), focused_element=listing
        ),
        key_bindings=kb,
        style=merge_styles([pt_style, extra]),
        full_screen=True,
        mouse_support=True,
        erase_when_done=True,
    )
    return await app.run_async()
