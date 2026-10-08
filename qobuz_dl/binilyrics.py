# ============================================================================
# binilyrics.py -- fonte de letras BiniLyrics (TTML estilo Apple Music).
#
# Este modulo e' PURO (sem rede): escolhe a gravacao certa entre os resultados
# da API, le o TTML preservando o maximo de informacao e deriva as saidas
# (LRC por linha, LRC por palavra, texto puro e traducao). A parte de rede fica
# em LyricsEngine._provider_binilyrics().
#
# Regras seguidas (resumo):
#   * nunca usar results[0] as cegas: ISRC exato > titulo/versao + artista +
#     album + duracao (+-4 s) > tipo de sincronizacao (word > line > plain);
#   * <p> = evento/linha; <span> = fragmento temporizado (NAO e' 1 palavra);
#   * eventos sobrepostos, agentes (ttm:agent) e secoes sao preservados;
#   * nenhum timestamp e' inventado (line nunca vira word);
#   * traducao embutida: pt-BR tem prioridade; outra lingua nao substitui.
# ============================================================================
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

API_URL = "https://lyrics-api.binimum.org/getLyrics"
SOURCE_LABEL = "BiniLyrics"
DURATION_TOLERANCE_S = 4
MAX_TTML_BYTES = 2 * 1024 * 1024

NS_ITUNES = "http://music.apple.com/lyric-ttml-internal"
NS_TTM = "http://www.w3.org/ns/ttml#metadata"
NS_XML = "http://www.w3.org/XML/1998/namespace"

_TIMING_RANK = {"word": 3, "line": 2, "plain": 1}


# ---------------------------------------------------------------------------
# Modelo interno
# ---------------------------------------------------------------------------
@dataclass
class TTMLSpan:
    """Fragmento temporizado de uma linha (nem sempre uma palavra inteira)."""

    text: str
    begin: int | None = None  # ms
    end: int | None = None  # ms
    space_after: bool = False


@dataclass
class TTMLLine:
    """Um <p> do TTML (evento/linha). Pode se sobrepor a outros."""

    id: str
    text: str
    begin: int | None = None  # ms
    end: int | None = None  # ms
    agent: str | None = None
    section: str | None = None
    spans: list[TTMLSpan] = field(default_factory=list)


@dataclass
class TTMLDocument:
    language: str | None
    declared_timing: str | None
    lines: list[TTMLLine]
    agents: dict[str, str]  # xml:id -> type
    translations: dict[str, dict[str, str]]  # idioma -> {id da linha: texto}
    raw: str  # TTML original, intacto (fonte de verdade)

    @property
    def timing(self) -> str:
        """word / line / plain, deduzido do que realmente existe no TTML."""
        if any(s.begin is not None for ln in self.lines for s in ln.spans):
            return "word"
        if any(ln.begin is not None for ln in self.lines):
            return "line"
        return "plain"


# ---------------------------------------------------------------------------
# Tempo
# ---------------------------------------------------------------------------
_UNIT_MS = {"ms": 1, "s": 1000, "m": 60000, "h": 3600000}


def parse_time(value) -> int | None:
    """'10.200', '1:02.5', '00:01:02.500', '350ms' -> milissegundos."""
    if value is None:
        return None
    text = str(value).strip()
    unit = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m|h)", text)
    if unit:
        return round(float(unit.group(1)) * _UNIT_MS[unit.group(2)])
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        return None
    try:
        seconds = float(parts[-1])
        minutes = int(parts[-2]) if len(parts) >= 2 else 0
        hours = int(parts[-3]) if len(parts) == 3 else 0
    except ValueError:
        return None
    return round((hours * 3600 + minutes * 60 + seconds) * 1000)


def format_timestamp(ms: int) -> str:
    """ms -> '[mm:ss.mmm]' (mesmo formato canonico do LyricsEngine)."""
    ms = max(0, int(ms))
    minutes, rest = divmod(ms, 60000)
    return f"[{minutes:02d}:{rest / 1000:06.3f}]"


def _word_tag(ms: int) -> str:
    return "<" + format_timestamp(ms)[1:-1] + ">"


# ---------------------------------------------------------------------------
# Escolha da gravacao
# ---------------------------------------------------------------------------
def _normalize(text) -> str:
    """minusculas, sem acentos nem pontuacao, espacos colapsados."""
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", plain.lower()).strip()


_VERSION_PATTERNS = {
    "live": r"\blive\b|ao vivo|en vivo|en directo",
    "remix": r"\bremix\b",
    "acoustic": r"acou?stic",
    "instrumental": r"instrumental|karaoke",
    "radio": r"radio (?:edit|version|mix)",
    "demo": r"\bdemo\b",
}


def _title_parts(title) -> tuple[str, frozenset]:
    """(titulo-base, marcas de versao). Versoes diferentes NAO sao a mesma
    gravacao: 'Song' != 'Song (Live)' != 'Song (Remix)'."""
    raw = _normalize(title)
    flags = frozenset(k for k, pat in _VERSION_PATTERNS.items() if re.search(pat, raw))
    base = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", str(title or ""))
    base = re.split(r"\s+-\s+", base)[0]
    return _normalize(base), flags


def _artist_set(artist) -> set[str]:
    pattern = r",|&|;|\bfeat\.?\b|\bft\.?\b|\bwith\b|\bx\b"
    parts = re.split(pattern, str(artist or ""), flags=re.I)
    return {n for n in (_normalize(p) for p in parts) if n}


def _timing_rank(candidate: dict) -> int:
    return _TIMING_RANK.get(str(candidate.get("timing_type", "")).lower(), 0)


def select_candidate(
    results,
    *,
    isrc=None,
    title="",
    artist="",
    album="",
    duration=None,
):
    """Escolhe a gravacao certa entre os `results` da API (ou None).

    1. ISRC exato vence na hora (se houver mais de um, word > line > plain).
    2. Sem ISRC: so entram candidatos com o MESMO titulo-base e as MESMAS
       marcas de versao (live/remix/...), artista em comum e duracao dentro de
       +-4 s. Entre eles ganha quem combina mais artista/album/duracao e, no
       empate, o tipo de sincronizacao mais rico.
    Preferir nenhuma letra a uma letra de outra gravacao.
    """
    cands = [r for r in (results or []) if isinstance(r, dict) and r.get("lyricsUrl")]

    if isrc:
        wanted = str(isrc).strip().upper()
        exact = [r for r in cands if str(r.get("isrc", "")).strip().upper() == wanted]
        if exact:
            return max(exact, key=_timing_rank)

    want_base, want_flags = _title_parts(title)
    if not want_base:
        return None
    want_artists = _artist_set(artist)
    want_album = _normalize(album)

    ranked = []
    for r in cands:
        base, flags = _title_parts(r.get("track_name", ""))
        if base != want_base or flags != want_flags:
            continue

        cand_artists = _artist_set(r.get("artist_name", ""))
        common = want_artists & cand_artists
        if want_artists and cand_artists and not common:
            continue

        cand_dur = r.get("duration")
        diff = None
        if duration and isinstance(cand_dur, (int, float)):
            diff = abs(cand_dur - duration)
            if diff > DURATION_TOLERANCE_S:
                continue

        score = 10 * len(common)
        cand_album = _normalize(r.get("album_name", ""))
        if want_album and cand_album:
            if want_album == cand_album:
                score += 15
            elif want_album in cand_album or cand_album in want_album:
                score += 5
        if diff is not None:
            score += 10 - diff
        ranked.append((score, _timing_rank(r), r))

    if not ranked:
        return None
    return max(ranked, key=lambda item: (item[0], item[1]))[2]


# ---------------------------------------------------------------------------
# Parser TTML (loss-minimizing)
# ---------------------------------------------------------------------------
def _local(el) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _attr(el, *names) -> str | None:
    """Atributo por nome local (ignora o namespace)."""
    for key, value in el.attrib.items():
        if key.rsplit("}", 1)[-1] in names:
            return value
    return None


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _collect_spans(parent, inherited_space: bool = False) -> list[TTMLSpan]:
    """Spans-folha de um <p>, na ordem do documento. Spans que so agrupam
    outros (ex.: vocais de apoio) nao viram fragmento proprio."""
    spans: list[TTMLSpan] = []
    children = [c for c in parent if _local(c) == "span"]
    for index, child in enumerate(children):
        space = bool(child.tail) and child.tail[0].isspace()
        if not child.tail and index == len(children) - 1:
            space = inherited_space
        inner = [c for c in child if _local(c) == "span"]
        if inner:
            spans.extend(_collect_spans(child, space))
        else:
            spans.append(
                TTMLSpan(
                    text=_collapse("".join(child.itertext())),
                    begin=parse_time(child.get("begin")),
                    end=parse_time(child.get("end")),
                    space_after=space,
                )
            )
    return spans


def parse_ttml(data) -> TTMLDocument:
    """Le um TTML estilo Apple Music. Levanta ValueError se for invalido,
    grande demais ou conter DOCTYPE/ENTITY (nao confiamos no servidor)."""
    raw = data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data)
    if len(raw.encode("utf-8")) > MAX_TTML_BYTES:
        raise ValueError("TTML grande demais")
    if re.search(r"<!(?:DOCTYPE|ENTITY)", raw, re.IGNORECASE):
        raise ValueError("TTML com DOCTYPE/ENTITY nao e aceito")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError(f"TTML invalido: {exc}") from exc

    xml_lang = f"{{{NS_XML}}}lang"
    xml_id = f"{{{NS_XML}}}id"

    agents = {
        el.get(xml_id): el.get("type", "")
        for el in root.iter()
        if _local(el) == "agent" and el.get(xml_id)
    }

    translations: dict[str, dict[str, str]] = {}
    for el in root.iter():
        if _local(el) != "translation":
            continue
        lang = el.get(xml_lang)
        if not lang:
            continue
        bucket = translations.setdefault(lang, {})
        for text_el in el:
            key = text_el.get("for")
            if _local(text_el) == "text" and key:
                bucket[key] = _collapse("".join(text_el.itertext()))

    lines: list[TTMLLine] = []

    def walk(node, section):
        for child in node:
            kind = _local(child)
            if kind == "div":
                walk(child, _attr(child, "song-part", "songPart") or section)
            elif kind == "p":
                lines.append(
                    TTMLLine(
                        id=_attr(child, "key")
                        or child.get(xml_id)
                        or f"L{len(lines) + 1}",
                        text=_collapse("".join(child.itertext())),
                        begin=parse_time(child.get("begin")),
                        end=parse_time(child.get("end")),
                        agent=_attr(child, "agent"),
                        section=section,
                        spans=_collect_spans(child),
                    )
                )

    for body in (el for el in root if _local(el) == "body"):
        walk(body, None)

    return TTMLDocument(
        language=root.get(xml_lang),
        declared_timing=_attr(root, "timing"),
        lines=lines,
        agents=agents,
        translations=translations,
        raw=raw,
    )


# ---------------------------------------------------------------------------
# Saidas derivadas
# ---------------------------------------------------------------------------
def _timed_lines(doc: TTMLDocument) -> list[TTMLLine]:
    """Linhas com tempo e texto, em ordem cronologica (sobreposicoes ficam)."""
    timed = [ln for ln in doc.lines if ln.begin is not None and ln.text]
    return sorted(timed, key=lambda ln: ln.begin)


def to_plain(doc: TTMLDocument) -> str:
    return "\n".join(ln.text for ln in doc.lines if ln.text)


def to_line_lrc(doc: TTMLDocument) -> str:
    return "\n".join(
        f"{format_timestamp(ln.begin)}{ln.text}" for ln in _timed_lines(doc)
    )


def to_word_lrc(doc: TTMLDocument) -> str:
    """LRC estendido: [mm:ss.mmm]<mm:ss.mmm>frag<mm:ss.mmm>frag...
    So usa tempos que existem no TTML; sem spans temporizados cai no LRC por
    linha (nunca estima tempo de palavra)."""
    out = []
    for ln in _timed_lines(doc):
        timed = [s for s in ln.spans if s.begin is not None]
        if not timed:
            out.append(f"{format_timestamp(ln.begin)}{ln.text}")
            continue
        pieces = []
        for span in ln.spans:
            tag = _word_tag(span.begin) if span.begin is not None else ""
            pieces.append(f"{tag}{span.text}" + (" " if span.space_after else ""))
        out.append(f"{format_timestamp(ln.begin)}{''.join(pieces).rstrip()}")
    return "\n".join(out)


def pick_translation_lang(doc: TTMLDocument, target: str = "pt-br"):
    """Idioma da traducao embutida a usar (ou None).

    pt-BR tem prioridade; depois pt generico e outras variantes pt-*. Uma
    traducao em outro idioma (ex.: en-US) nunca substitui a pedida, e musica
    que ja esta no idioma-alvo nao precisa de traducao.
    """
    target = (target or "").strip().lower()
    if not target:
        return None
    base = target.split("-")[0]
    if (doc.language or "").lower().split("-")[0] == base:
        return None

    available = {lang.lower(): lang for lang in doc.translations}
    order = ["pt-br", "pt"] if base == "pt" else [target, base]
    for wanted in order:
        if wanted in available:
            return available[wanted]
    for low, original in sorted(available.items()):
        if low.startswith(base + "-"):
            return original
    return None


def translation_lrc(doc: TTMLDocument, lang: str) -> str:
    """Traducao no MESMO timestamp da linha original a que esta associada."""
    texts = doc.translations.get(lang, {})
    rows = [
        f"{format_timestamp(ln.begin)}{texts[ln.id]}"
        for ln in _timed_lines(doc)
        if texts.get(ln.id)
    ]
    return "\n".join(rows)


def translation_plain(doc: TTMLDocument, lang: str) -> str:
    texts = doc.translations.get(lang, {})
    return "\n".join(texts[ln.id] for ln in doc.lines if texts.get(ln.id))
