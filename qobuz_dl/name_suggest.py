# ============================================================================
# name_suggest.py -- sugere artistas com vírgula no nome (`tags --suggest-names`).
# ============================================================================
# "Tyler, The Creator" gravado como texto "Tyler, The Creator, Kali Uchis" (ou
# como entradas "Tyler" + "The Creator" de uma tag multivalorada antiga) parece
# dois artistas. O programa protege uma lista embutida, os nomes estruturados da
# API e o `artists_with_comma` do config.ini; este módulo ajuda a descobrir os
# que FALTAM olhando só as tags da biblioteca (sem rede, nada é gravado).
#
# Sinal usado: dois pedaços que SÓ aparecem um ao lado do outro ("Tyler" nunca
# aparece sozinho nem com outro vizinho). É um indício, não uma prova: dois
# parceiros que sempre gravam juntos também se encaixam -- por isso o resultado
# é uma sugestão para revisar, e o config.ini só muda se a pessoa decidir.
# ============================================================================

import logging
import os
from collections import Counter
from typing import Any, Callable, Iterable, Optional

from qobuz_dl.retro_tags import AUDIO_EXTENSIONS, find_audio_files

logger = logging.getLogger(__name__)

# Campos de pessoas/grupos onde um nome com vírgula pode aparecer.
FLAC_FIELDS = ("ARTIST", "ALBUMARTIST", "COMPOSER")
ID3_FRAMES = ("TPE1", "TPE2", "TCOM")

MIN_FILES = 3  # faixas em que o par precisa aparecer junto
MIN_RATIO = 0.9  # fração das aparições de cada pedaço que são "com o vizinho"


def _entries(values: Iterable[str]) -> list:
    """Entradas de um campo: cada valor, e cada "A, B" de texto único repartido."""
    out: list = []
    for value in values:
        for part in str(value).split(", "):
            part = part.strip()
            if part:
                out.append(part)
    return out


def read_artist_lists(path: str) -> list:
    """Listas de entradas (ARTIST, ALBUMARTIST, COMPOSER) de um arquivo.

    Devolve uma lista de listas: uma por campo preenchido. Erros de leitura
    devolvem [] (arquivo estranho não derruba a varredura).
    """
    extension = os.path.splitext(path)[1].lower()
    lists: list = []
    try:
        if extension == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(path)
            for field in FLAC_FIELDS:
                values = audio.get(field)
                if values:
                    lists.append(_entries(values))
        elif extension == ".mp3":
            import mutagen.id3 as id3

            tags = id3.ID3(path)
            for frame_id in ID3_FRAMES:
                frame = tags.get(frame_id)
                if frame is not None and frame.text:
                    lists.append(_entries(frame.text))
    except Exception as exc:  # noqa: BLE001 -- um arquivo ruim não para a varredura
        logger.debug("Não foi possível ler os artistas de %s: %s", path, exc)
    return [entries for entries in lists if entries]


def suggest_from_lists(
    file_lists: Iterable[list],
    protected: Iterable[str] = (),
    min_files: int = MIN_FILES,
    min_ratio: float = MIN_RATIO,
) -> list:
    """Sugere nomes com vírgula a partir das listas de entradas de cada arquivo.

    ``file_lists``: para cada ARQUIVO, a lista de listas devolvida por
    ``read_artist_lists`` (um arquivo conta uma vez por pedaço/par).
    Devolve ``[{"name": "Tyler, The Creator", "files": 142}, ...]`` do mais
    frequente para o menos, já sem os nomes que estão em ``protected``.
    """
    present: Counter = Counter()  # arquivos em que o pedaço aparece
    together: Counter = Counter()  # arquivos em que (a, b) aparecem lado a lado
    for per_file in file_lists:
        seen_items: set = set()
        seen_pairs: set = set()
        for entries in per_file:
            seen_items.update(entries)
            seen_pairs.update(zip(entries, entries[1:]))
        present.update(seen_items)
        together.update(seen_pairs)

    nxt: dict = {}
    prv: set = set()
    for (a, b), count in together.items():
        if a == b or count < min_files:
            continue
        if count / present[a] >= min_ratio and count / present[b] >= min_ratio:
            nxt[a] = (b, count)
            prv.add(b)

    protected_keys = {name.casefold() for name in protected}
    suggestions: list[dict[str, Any]] = []
    for start in nxt:
        if start in prv:
            continue  # só começa nas pontas das correntes (Crosby, Stills, ...)
        chain, files, node = [start], None, start
        while node in nxt and nxt[node][0] not in chain:
            node, count = nxt[node]
            chain.append(node)
            files = count if files is None else min(files, count)
        name = ", ".join(chain)
        if name.casefold() in protected_keys:
            continue
        suggestions.append({"name": name, "files": files or 0})
    suggestions.sort(key=lambda item: (-item["files"], item["name"]))
    return suggestions


def scan_directory(
    directory: str,
    progress: Optional[Callable[[int, int], None]] = None,
) -> list:
    """Lê as tags de artista de todos os FLAC/MP3 da pasta (uma lista por arquivo)."""
    files = [
        p for p in find_audio_files(directory) if p.lower().endswith(AUDIO_EXTENSIONS)
    ]
    out = []
    for index, path in enumerate(files, 1):
        out.append(read_artist_lists(path))
        if progress and (index % 200 == 0 or index == len(files)):
            progress(index, len(files))
    return out


def config_line(existing: Iterable[str], suggestions: Iterable[dict]) -> str:
    """Linha pronta para o config.ini, juntando o que já havia com as sugestões."""
    names: list = []
    for name in list(existing) + [s["name"] for s in suggestions]:
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return "artists_with_comma = " + "; ".join(names)
