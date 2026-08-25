"""
playlist_import.py -- Parser de playlists exportadas de qualquer plataforma

Converte arquivos de playlist exportados (TXT, CSV, JSON) em uma lista
normalizada de {"artist": str, "title": str} que pode ser passada
diretamente para Client.match_lastfm_tracks() -- reutilizando todo o
pipeline de fuzzy matching + download que já existe para Last.fm.

Formatos suportados:
  TXT  -- uma entrada por linha: "Artista - Título" ou "Artista: Título"
         Linhas começando com # são comentários e ignoradas.
  CSV  -- qualquer export com colunas "artist"+"title" (Exportify, Soundiiz,
         TuneMyMusic, etc). Detecta o separador automaticamente (,  ;  \t).
  JSON -- Spotify export, exportify.net, ou qualquer JSON com os campos
         [{artist, title}] ou [{trackName, artistName}] ou
         [{track: {name, artists: [{name}]}}]

Uso:
    from qobuz_dl.playlist_import import parse_playlist_file
    tracks = parse_playlist_file("/path/to/playlist.csv")
    # → [{"artist": "Radiohead", "title": "Creep"}, ...]
"""

import csv
import json
import os
import re
from typing import List, Dict


def parse_playlist_file(path: str) -> List[Dict[str, str]]:
    """
    Detecta o formato do arquivo e retorna uma lista de dicts normalizados.

    Args:
        path: Caminho para o arquivo de playlist (.txt, .csv, .json).

    Returns:
        Lista de {"artist": str, "title": str}. Entradas sem artista ou
        título válido são descartadas silenciosamente.

    Raises:
        FileNotFoundError: Se o arquivo não existir.
        ValueError: Se o formato não for reconhecido ou o arquivo estiver vazio.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    ext = os.path.splitext(path)[1].lower()

    if ext == ".json":
        return _parse_json(path)
    elif ext == ".csv":
        return _parse_csv(path)
    elif ext in (".txt", ".text", ""):
        return _parse_txt(path)
    else:
        # Tenta detectar pelo conteúdo
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(512)
        if head.lstrip().startswith("{") or head.lstrip().startswith("["):
            return _parse_json(path)
        elif "," in head or "\t" in head or ";" in head:
            return _parse_csv(path)
        else:
            return _parse_txt(path)


# ─── Parsers por formato ──────────────────────────────────────────────────────


def _parse_txt(path: str) -> List[Dict[str, str]]:
    """
    TXT: uma entrada por linha.
    Formatos aceitos:
        Artista - Título          (separador mais comum)
        Artista: Título
        Artista | Título
        Título [sem artista]      (artista fica vazio → matching só por título)
    Linhas em branco e comentários (#) são ignoradas.
    """
    results = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue

            artist, title = _split_artist_title(line)
            if title:
                results.append({"artist": artist, "title": title})

    if not results:
        raise ValueError(f"Nenhuma entrada válida encontrada em {path}")
    return results


def _parse_csv(path: str) -> List[Dict[str, str]]:
    """
    CSV: detecta separador (, ; \\t) e mapeia colunas.
    Colunas reconhecidas automaticamente (case-insensitive):
        artist, artists, artist name, performer, track artist
        title, track, track name, song, song name, name
    Compatível com: Exportify, Soundiiz, TuneMyMusic, Last.fm export,
                    Apple Music / iTunes export, Spotify via exportify.net
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        sample = f.read(4096)

    # Detectar separador
    sep = ","
    for candidate in ["\t", ";", ","]:
        if sample.count(candidate) > sample.count(sep):
            sep = candidate

    results = []
    with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f, delimiter=sep)
        if not reader.fieldnames:
            raise ValueError(f"CSV sem cabeçalho: {path}")

        # Mapear nomes de colunas para chaves normalizadas
        headers_lower = {h.lower().strip(): h for h in reader.fieldnames if h}

        artist_col = _find_col(
            headers_lower,
            [
                "artist",
                "artists",
                "artist name",
                "artist_name",
                "performer",
                "track artist",
                "trackartist",
            ],
        )
        title_col = _find_col(
            headers_lower,
            [
                "title",
                "track",
                "track name",
                "track_name",
                "song",
                "song name",
                "song_name",
                "name",
            ],
        )

        if not title_col:
            raise ValueError(
                f"CSV sem coluna de título reconhecida. "
                f"Colunas encontradas: {list(headers_lower.keys())}"
            )

        for row in reader:
            title = (row.get(title_col) or "").strip()
            artist = (row.get(artist_col) or "").strip() if artist_col else ""

            # Exportify pode colocar múltiplos artistas separados por vírgula
            # Pega só o primeiro
            if "," in artist:
                artist = artist.split(",")[0].strip()

            if title:
                results.append({"artist": artist, "title": title})

    if not results:
        raise ValueError(f"Nenhuma entrada válida encontrada em {path}")
    return results


def _parse_json(path: str) -> List[Dict[str, str]]:
    """
    JSON: suporta múltiplos formatos de export.

    Formatos reconhecidos automaticamente:
      1. Exportify / exportify.net:
         [{"Track Name": "...", "Artist Name(s)": "..."}, ...]
      2. Spotify via API / backup:
         {"items": [{"track": {"name": "...", "artists": [{"name": "..."}]}}]}
      3. Formato genérico flat:
         [{"title": "...", "artist": "..."}, ...]
         [{"name": "...", "artist": "..."}, ...]
      4. Last.fm JSON export:
         {"track": [{"name": "...", "artist": {"name": "..."}}]}
    """
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        data = json.load(f)

    results = []

    # Normalizar para lista
    if isinstance(data, dict):
        # Spotify API format: {"items": [...]}
        if "items" in data:
            data = data["items"]
        # Last.fm format: {"track": [...]}
        elif "track" in data:
            data = data["track"]
        else:
            data = [data]

    if not isinstance(data, list):
        raise ValueError("JSON não contém uma lista de faixas reconhecível")

    for item in data:
        if not isinstance(item, dict):
            continue

        artist = ""
        title = ""

        # Spotify API: {"track": {"name": "...", "artists": [{"name": "..."}]}}
        if "track" in item and isinstance(item["track"], dict):
            t = item["track"]
            title = t.get("name", "")
            arts = t.get("artists", [])
            artist = arts[0].get("name", "") if arts else ""

        # Exportify: {"Track Name": "...", "Artist Name(s)": "..."}
        elif "Track Name" in item or "Artist Name(s)" in item:
            title = item.get("Track Name", "")
            artist = item.get("Artist Name(s)", "")
            if "," in artist:
                artist = artist.split(",")[0].strip()

        # Last.fm: {"name": "...", "artist": {"name": "..."}}
        elif "name" in item and "artist" in item:
            title = item.get("name", "")
            art = item.get("artist", {})
            artist = art.get("name", "") if isinstance(art, dict) else str(art)

        # Formato genérico: {"title": "...", "artist": "..."}
        else:
            title = (item.get("title") or item.get("name") or "").strip()
            artist = (item.get("artist") or item.get("artists") or "").strip()
            if isinstance(artist, list):
                artist = artist[0] if artist else ""

        title = str(title).strip()
        artist = str(artist).strip()
        if title:
            results.append({"artist": artist, "title": title})

    if not results:
        raise ValueError(f"Nenhuma entrada válida encontrada em {path}")
    return results


# ─── Helpers ─────────────────────────────────────────────────────────────────

_SEP_RE = re.compile(r"\s*(?:\s–\s|\s-\s|:\s|\s\|\s)\s*", re.UNICODE)


def _split_artist_title(line: str):
    """
    Divide "Artista - Título" em (artista, título).
    Retorna ("", line) se não conseguir detectar o separador.
    """
    parts = _SEP_RE.split(line, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", line.strip()


def _find_col(headers_lower: dict, candidates: list):
    """Retorna o nome original da primeira coluna que bate com candidates."""
    for c in candidates:
        if c in headers_lower:
            return headers_lower[c]
    return None
