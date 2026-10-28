"""Linha "Letra: <origem> (...)" do COMMENT: de onde veio a letra gravada.

O motor de letras grava a letra num campo próprio (``LYRICS_SOURCE`` no FLAC, a
descrição do frame USLT no MP3, ``[by:...]`` no .lrc) e, além disso, deixa esta
linha no comentário do arquivo, para que quem abrir as tags veja a origem junto
com o Trk ID e a capa. Exemplos:

    Letra: Qobuz (sincronizada, bilíngue EN+PT)
    Letra: BiniLyrics (sincronizada, EN)

No FLAC o COMMENT tem uma linha por item; no MP3 é uma linha única separada por
" | ". Por isso o texto da linha nunca contém " | " nem quebra de linha.

Este módulo não importa nada pesado (nem mutagen) de propósito: é usado pelo
motor de letras, pelo leitor de letras do `lyrics` e pelo gravador de tags
(`metadata`), que precisam preservar a linha quando reescrevem o COMMENT.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PREFIX = "Letra:"
_SEPARATOR = " | "
_SOURCE_RE = re.compile(r"Letra:\s*([^(|\n]+?)\s*(?:\(|\||$)")


def build_line(source: object, *, bilingual: bool = False, language: object = None,) -> str:
    """'Letra: Qobuz (sincronizada, bilíngue EN+PT)'."""
    idioma = str(language or "").strip()
    if idioma.lower() == "unknown":
        idioma = ""
    detalhes = ["sincronizada"]
    if bilingual:
        detalhes.append(f"bilíngue {idioma.upper()}" if idioma else "bilíngue")
    elif idioma:
        detalhes.append(idioma.upper())
    return f"{PREFIX} {str(source).strip()} ({', '.join(detalhes)})"


def source_from_comment(text: str | Iterable[object] | None,) -> str | None:
    """Origem escrita numa linha/comentário 'Letra: X (...)'; None se não houver."""
    if not text:
        return None
    if not isinstance(text, str):
        text = "\n".join(str(part) for part in text)
    match = _SOURCE_RE.search(text)
    return match.group(1).strip() if match else None


def extract_flac_lines(values: str | Iterable[object] | None,) -> list[str]:
    """Linhas de origem da letra de um COMMENT Vorbis (str ou lista de str)."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    lines: list[str] = []
    for value in values:
        for line in str(value).splitlines():
            line = line.strip()
            if line.startswith(PREFIX) and line not in lines:
                lines.append(line)
    return lines


def extract_mp3_segments(text: str | Iterable[object] | None,) -> list[str]:
    """Trechos de origem da letra de um COMM ID3 (linha única separada por ' | ')."""
    if not text:
        return []
    if not isinstance(text, str):
        text = _SEPARATOR.join(str(part) for part in text)
    segments: list[str] = []
    for segment in text.split(_SEPARATOR):
        segment = segment.strip()
        if segment.startswith(PREFIX) and segment not in segments:
            segments.append(segment)
    return segments


def merge_flac(values: str | Iterable[object] | None, line: str,) -> str:
    """COMMENT Vorbis com a linha de letra atualizada (as demais linhas ficam)."""
    if not values:
        values = []
    if isinstance(values, str):
        values = [values]
    lines = [
        existing
        for value in values
        for existing in str(value).splitlines()
        if existing.strip() and not existing.strip().startswith(PREFIX)
    ]
    lines.append(line)
    return "\n".join(lines)


def merge_mp3(text: str | Iterable[object] | None, line: str) -> str:
    """COMM ID3 com o trecho de letra atualizado (os demais trechos ficam)."""
    if not text:
        text = ""
    if not isinstance(text, str):
        text = _SEPARATOR.join(str(part) for part in text)
    segments = [
        part.strip()
        for part in text.split(_SEPARATOR)
        if part.strip() and not part.strip().startswith(PREFIX)
    ]
    segments.append(line)
    return _SEPARATOR.join(segments)
