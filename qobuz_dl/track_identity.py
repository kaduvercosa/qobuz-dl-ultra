"""
track_identity.py -- Identidade de faixas por ID do Qobuz (não pelo nome).

Problema que resolve
--------------------
Em playlists baixadas na pasta única (modo plano), o nome do arquivo é
``{artista} - {título}``. Duas faixas diferentes com o mesmo artista e título
(álbuns distintos, single vs. álbum, remaster etc.) geravam o MESMO caminho:

* modo sequencial: a segunda era "pulada (já existe)" e contada como sucesso;
* modo paralelo: as duas baixavam ao mesmo tempo e uma sobrescrevia a outra.

Como funciona agora
-------------------
1. Cada faixa tem uma lista ORDENADA e DETERMINÍSTICA de nomes candidatos
   (do mais simples ao mais específico). O último sempre contém o ID da faixa,
   portanto é único por faixa.
2. ``resolve_output_path`` escolhe, de forma atômica:
   a. um candidato que já pertence a ESTA faixa (mesmo ID gravado na tag do
      arquivo em disco, ou reservado em memória por outra tarefa em andamento);
   b. senão, o primeiro candidato livre (não existe em disco e não está
      reservado), que fica reservado para esta faixa até ``release_claims``.
3. A reserva em memória cobre o modo paralelo: o arquivo final só aparece em
   disco no fim do download, então sem ela duas tarefas poderiam escolher o
   mesmo nome ao mesmo tempo.

Re-execuções são idempotentes: a faixa A sempre reencontra o arquivo que tem o
seu ID, independentemente da ordem em que as faixas são processadas.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

# Estados devolvidos por resolve_output_path.
STATE_NEW = "new"  # candidato livre, agora reservado para esta faixa
STATE_EXISTING = "existing"  # já existe em disco e pertence a esta faixa
STATE_IN_PROGRESS = "in_progress"  # outra tarefa está baixando ESTA mesma faixa

_ID_TAG_KEYS = ("QDL_TRACK_ID", "QOBUZTRACKID", "QOBUZ TRACK ID")
_COMMENT_ID_RE = re.compile(r"Trk ID:\s*(\d+)")

# path normalizado -> ID da faixa que reservou o caminho (downloads em curso).
_claims: dict[str, str] = {}
_claims_lock = threading.Lock()


def _norm(path: str) -> str:
    """Chave de comparação de caminhos (insensível a maiúsculas/minúsculas)."""
    return os.path.normcase(os.path.abspath(path)).casefold()


def read_track_id(path: str) -> Optional[str]:
    """Lê o ID de faixa Qobuz gravado nas tags de um arquivo FLAC/MP3.

    Retorna ``None`` se o arquivo não existir, não puder ser lido ou não tiver
    ID. Para MP3 antigos (sem o frame TXXX do ID) usa como alternativa o trecho
    ``Trk ID: N`` que o programa sempre gravou no comentário.
    """
    try:
        lower = path.lower()
        if lower.endswith(".flac"):
            from mutagen.flac import FLAC

            audio = FLAC(path)
            for key in _ID_TAG_KEYS:
                values = audio.get(key)
                if values and str(values[0]).strip():
                    return str(values[0]).strip()
            for comment in audio.get("COMMENT", []) + audio.get("DESCRIPTION", []):
                match = _COMMENT_ID_RE.search(str(comment))
                if match:
                    return match.group(1)
            return None

        if lower.endswith(".mp3"):
            from mutagen.id3 import ID3

            id3_tags = ID3(path)
            wanted = {k.upper() for k in _ID_TAG_KEYS}
            for frame in id3_tags.getall("TXXX"):
                if str(frame.desc).upper() in wanted and frame.text:
                    text = str(frame.text[0]).strip()
                    if text:
                        return text
            for frame in id3_tags.getall("COMM"):
                for text in frame.text:
                    match = _COMMENT_ID_RE.search(str(text))
                    if match:
                        return match.group(1)
    except Exception as exc:  # arquivo parcial/corrompido/sem tags
        logger.debug("Não foi possível ler o ID da faixa em %s: %s", path, exc)
    return None


def unique_in_order(items: Iterable[str]) -> list[str]:
    """Remove repetidos preservando a ordem."""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def resolve_output_path(
    candidates: list[str],
    track_id: str,
    claimed: Optional[list[str]] = None,
) -> tuple[str, str]:
    """Escolhe o caminho de saída de uma faixa entre os candidatos.

    Args:
        candidates: caminhos completos, do mais simples ao mais específico.
        track_id: ID Qobuz da faixa que será gravada.
        claimed: lista onde o caminho reservado é anotado (para
            ``release_claims``).

    Returns:
        ``(caminho, estado)`` com estado em ``STATE_NEW``, ``STATE_EXISTING``
        ou ``STATE_IN_PROGRESS``.
    """
    track_id = str(track_id)
    if not candidates:
        raise ValueError("resolve_output_path precisa de ao menos um candidato")

    with _claims_lock:
        # Passo 1: algum candidato já é desta faixa?
        for path in candidates:
            owner = _claims.get(_norm(path))
            if owner is not None:
                if owner == track_id:
                    return path, STATE_IN_PROGRESS
                continue
            if os.path.exists(path) and read_track_id(path) == track_id:
                return path, STATE_EXISTING

        # Passo 2: primeiro candidato realmente livre.
        for path in candidates:
            key = _norm(path)
            if key not in _claims and not os.path.exists(path):
                _claims[key] = track_id
                if claimed is not None:
                    claimed.append(path)
                return path, STATE_NEW

        # Passo 3 (praticamente inalcançável): todos os candidatos estão
        # ocupados por outras faixas/arquivos ilegíveis. Usa o último (o que
        # leva o ID) e deixa o fluxo normal tratar o arquivo existente.
        return candidates[-1], STATE_EXISTING


def release_claims(claimed: Iterable[str]) -> None:
    """Libera as reservas feitas por uma tarefa (sucesso, falha ou cancelamento)."""
    with _claims_lock:
        for path in claimed:
            _claims.pop(_norm(path), None)


def fit_name(name: str, max_len: int = 180) -> str:
    """Encurta nomes longos mantendo começo e fim (o fim leva o sufixo/ID)."""
    if len(name) <= max_len:
        return name
    start_part = name[:110].rstrip(" .\"-_'")
    end_part = name[-60:].lstrip(" .\"-_'")
    return f"{start_part}...{end_part}"


def build_name_stems(
    base: str,
    album_title: Optional[str],
    track_id: str,
    *,
    sanitize,
    max_len: int = 180,
) -> list[str]:
    """Monta os nomes (sem extensão) candidatos, do mais simples ao mais específico.

    1. ``base``                          -> idêntico ao nome usado antes
    2. ``base (Álbum)``                  -> diferencia por álbum
    3. ``base (Álbum) [ID]``             -> único por faixa (mesmo álbum repetido)
    Sem álbum, o 3º vira ``base [ID]``.

    ``sanitize`` torna um trecho seguro como nome de arquivo. O truncamento é
    feito por candidato e sempre preserva o final (onde ficam álbum e ID).
    """
    album = sanitize(album_title.strip()) if album_title and album_title.strip() else ""
    tid = str(track_id).strip()
    base_safe = sanitize(base)

    suffixes = [""]
    if album:
        suffixes.append(f" ({album})")
        if tid:
            suffixes.append(f" ({album}) [{tid}]")
    elif tid:
        suffixes.append(f" [{tid}]")

    return unique_in_order(fit_name(base_safe + suf, max_len) for suf in suffixes)
