"""Linha "Letra: <origem> (...)" do COMMENT: de onde veio a letra gravada.

O motor de letras grava a letra num campo próprio (``LYRICS_SOURCE`` no FLAC, a
descrição do frame USLT no MP3, ``[by:...]`` no .lrc) e, além disso, deixa esta
linha no comentário do arquivo, para que quem abrir as tags veja a origem junto
com o Trk ID e a capa. Exemplos:

    Letra: Qobuz (sincronizada, bilíngue EN+PT)
    Letra: BiniLyrics (sincronizada, EN)

No FLAC o COMMENT tem uma linha por item. No MP3 também é uma linha por item
(arquivos novos); os MP3 gravados antes dessa mudança têm uma linha única
separada por " | ", e continuam sendo lidos e preservados nesse formato. Por
isso o texto da linha nunca contém " | " nem quebra de linha.

Este módulo não importa nada pesado (nem mutagen) de propósito: é usado pelo
motor de letras, pelo leitor de letras do `lyrics` e pelo gravador de tags
(`metadata`), que precisam preservar a linha quando reescrevem o COMMENT.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

PREFIX = "Letra:"
_SEPARATOR = " | "  # formato antigo do MP3: tudo numa linha só
_SPLIT_RE = re.compile(r"\r?\n| \| ")
_SOURCE_RE = re.compile(r"Letra:\s*([^(|\n]+?)\s*(?:\(|\||$)")


def build_line(
    source: object,
    *,
    bilingual: bool = False,
    language: object = None,
) -> str:
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


def source_from_comment(
    text: str | Iterable[object] | None,
) -> str | None:
    """Origem escrita numa linha/comentário 'Letra: X (...)'; None se não houver."""
    if not text:
        return None
    if not isinstance(text, str):
        text = "\n".join(str(part) for part in text)
    match = _SOURCE_RE.search(text)
    return match.group(1).strip() if match else None


def extract_flac_lines(
    values: str | Iterable[object] | None,
) -> list[str]:
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


def split_segments(text: str | Iterable[object] | None) -> list[str]:
    """Trechos de um COMM ID3, no formato novo (uma linha por item) ou no antigo
    (linha única separada por ' | '). Devolve os trechos sem espaços nas pontas e
    sem os vazios."""
    if not text:
        return []
    if not isinstance(text, str):
        text = "\n".join(str(part) for part in text)
    return [part.strip() for part in _SPLIT_RE.split(text) if part.strip()]


def separator_of(text: str | None) -> str:
    """Separador a usar ao reescrever um COMM existente.

    Mantém o formato que o arquivo já tem: quebra de linha no formato novo,
    ' | ' no antigo (e em comentários de outros programas, de uma linha só).
    """
    return "\n" if text and "\n" in text else _SEPARATOR


def extract_mp3_segments(
    text: str | Iterable[object] | None,
) -> list[str]:
    """Trechos de origem da letra de um COMM ID3 (formato novo ou antigo)."""
    segments: list[str] = []
    for segment in split_segments(text):
        if segment.startswith(PREFIX) and segment not in segments:
            segments.append(segment)
    return segments


def merge_flac(
    values: str | Iterable[object] | None,
    line: str,
) -> str:
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
    """COMM ID3 com o trecho de letra atualizado (os demais trechos ficam).

    Preserva o formato do comentário: uma linha por item (novo) ou ' | ' (antigo).
    """
    if not text:
        text = ""
    if not isinstance(text, str):
        text = "\n".join(str(part) for part in text)
    segments = [part for part in split_segments(text) if not part.startswith(PREFIX)]
    segments.append(line)
    return separator_of(text).join(segments)
