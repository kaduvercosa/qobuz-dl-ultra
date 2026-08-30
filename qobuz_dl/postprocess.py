# ============================================================================
# postprocess.py -- geracao de artefatos de pos-processamento.
#
# Gera:
#   - Por album: download_report.json/csv, album.log, checksums.sha256,
#     album_state.json, credits.json/txt, quality_report.txt, index_entry,
#     e atualiza collection_report.md.
#   - Por faixa avulsa: track_report.json, track_state.json.
#   - Por playlist: playlist_report.json/csv, playlist_log.txt.
#
# Ponto de integracao:
#   - download_release()  -> album
#   - download_track()    -> faixa avulsa
#   - download_playlist() -> playlist (se existir no seu codigo)
# ============================================================================
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, List

logger = logging.getLogger(__name__)


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: str, data: Any) -> None:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = [existing]
        except Exception:
            existing = []
    else:
        existing = []

    existing.append(data)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def _write_text(path: str, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text + "\n\n")


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# ============================================================================
# ALBUM
# ============================================================================


def generate_download_report(
    dirn: str,
    album_title: str,
    artist_name: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    results: List[Any],
    album_meta: dict,
) -> None:
    """
    Gera download_report.json.
    """
    tracks = album_meta.get("tracks", {}).get("items", [])
    rows = []
    for idx, i in enumerate(tracks):
        t_num = str(i.get("track_number", idx + 1)).zfill(2)
        t_title = i.get("title", "Faixa")
        t_id = i.get("id")
        res = results[idx] if idx < len(results) else None
        status = "ok" if res is True else ("skipped" if res == "skipped" else "failed")
        reason = ""
        if status != "ok":
            if isinstance(res, Exception):
                reason = str(res)
            elif status == "skipped":
                reason = "indisponivel/demo/compra"
        rows.append(
            {
                "track_number": t_num,
                "track_id": t_id,
                "track_title": t_title,
                "status": status,
                "reason": reason,
                "format": file_format,
                "bit_depth": bit_depth,
                "sampling_rate": sampling_rate,
            }
        )

    report = {
        "album": album_title,
        "artista": artist_name,
        "gerado_em": _now_iso(),
        "faixas": rows,
    }
    _write_json(os.path.join(dirn, "download_report.json"), report)


def generate_album_log(
    dirn: str,
    album_title: str,
    artist_name: str,
    album_id: str,
    quality: int,
    mode_label: str,
    results: List[Any],
    album_meta: dict,
) -> None:
    """
    Gera album.log com cabecalho, eventos e resumo.
    """
    lines = []
    lines.append(f"Album: {album_title}")
    lines.append(f"Artista: {artist_name}")
    lines.append(f"ID: {album_id}")
    lines.append(f"Qualidade alvo: {quality}")
    lines.append(f"Modo: {mode_label}")
    lines.append(f"Gerado em: {_now_iso()}")
    lines.append("")
    lines.append("Eventos:")
    tracks = album_meta.get("tracks", {}).get("items", [])
    for idx, i in enumerate(tracks):
        t_num = str(i.get("track_number", idx + 1)).zfill(2)
        t_title = i.get("title", "Faixa")
        res = results[idx] if idx < len(results) else None
        if res is True:
            lines.append(f"  [OK] {t_num} - {t_title}")
        elif res == "skipped":
            lines.append(f"  [PULADA] {t_num} - {t_title}")
        else:
            reason = str(res) if isinstance(res, Exception) else "erro"
            lines.append(f"  [FALHA] {t_num} - {t_title} ({reason})")
    lines.append("")
    ok = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r == "skipped")
    failed = sum(1 for r in results if r not in (True, "skipped"))
    lines.append("Resumo:")
    lines.append(f"  Baixadas: {ok}")
    lines.append(f"  Puladas: {skipped}")
    lines.append(f"  Falhas: {failed}")
    _write_text(os.path.join(dirn, "album.log"), "\n".join(lines))


def generate_checksums(dirn: str) -> None:
    """
    Gera checksums.sha256 com hashes de audio, capa e booklet.
    """
    entries = []
    for root, _, files in os.walk(dirn):
        for fn in files:
            if fn.startswith("."):
                continue
            if fn in ("checksums.sha256", "album_state.json", "collection_report.md"):
                continue
            fpath = os.path.join(root, fn)
            try:
                h = _sha256_file(fpath)
                rel = os.path.relpath(fpath, dirn).replace("\\", "/")
                entries.append(f"{h}  {rel}")
            except Exception as e:
                logger.debug("Falha ao hashear %s: %s", fn, e)
    entries.sort(key=lambda x: x.split("  ", 1)[1])
    _write_text(os.path.join(dirn, "checksums.sha256"), "\n".join(entries) + "\n")


def generate_album_state(
    dirn: str,
    album_id: str,
    album_title: str,
    artist_name: str,
    complete: bool,
    failed_tracks: int,
    quality_met: bool,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    verified: bool = False,
) -> None:
    """
    Gera album_state.json com estado do album.
    """
    state = {
        "album_id": album_id,
        "album_titulo": album_title,
        "artista": artist_name,
        "completo": complete,
        "faixas_com_falha": failed_tracks,
        "qualidade_atingida": quality_met,
        "formato": file_format,
        "bit_depth": bit_depth,
        "sampling_rate": sampling_rate,
        "verificado": verified,
        "gerado_em": _now_iso(),
    }
    _write_json(os.path.join(dirn, "album_state.json"), state)


def generate_credits(
    dirn: str,
    album_meta: dict,
    album_title: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
) -> None:
    """
    Gera credits.json e credits.txt com creditos completos.
    """
    artist_name = album_meta.get("artist", {}).get("name", "Desconhecido")
    label = album_meta.get("label", {}).get("name", "")
    genre = album_meta.get("genre", {}).get("name", "")
    release_date = album_meta.get("release_date_original", "")
    upc = album_meta.get("upc", "")
    url = album_meta.get("url", "")
    tracks = album_meta.get("tracks", {}).get("items", [])

    credits = {
        "album": album_title,
        "artista": artist_name,
        "rotulo": label,
        "genero": genre,
        "data_lancamento": release_date,
        "upc": upc,
        "url": url,
        "qualidade": {
            "formato": file_format,
            "bit_depth": bit_depth,
            "sampling_rate": sampling_rate,
        },
        "faixas": [],
    }

    for idx, t in enumerate(tracks):
        t_num = str(t.get("track_number", idx + 1)).zfill(2)
        t_title = t.get("title", "Faixa")
        t_id = t.get("id")
        isrc = t.get("isrc", "")
        composer = t.get("composer", {}).get("name", "")
        performers_raw = t.get("performers", "")
        performers = []
        if performers_raw:
            for line in str(performers_raw).splitlines():
                if line.strip():
                    performers.append(line.strip())
        credits["faixas"].append(
            {
                "numero": t_num,
                "id": t_id,
                "titulo": t_title,
                "isrc": isrc,
                "compositor": composer,
                "interpretes": performers,
            }
        )

    # credits.txt
    lines = []
    lines.append("=" * 70)
    lines.append(f"Album: {album_title}")
    lines.append(f"Artista: {artist_name}")
    lines.append(f"Rotulo: {label}")
    lines.append(f"Genero: {genre}")
    lines.append(f"Lancamento: {release_date}")
    lines.append(f"UPC: {upc}")
    lines.append(f"URL: {url}")
    lines.append(f"Qualidade: {file_format} ({bit_depth}-bit / {sampling_rate} kHz)")
    lines.append("=" * 70)
    lines.append("")
    for tr in credits["faixas"]:
        lines.append(f"{tr['numero']}. {tr['titulo']} (ISRC: {tr['isrc']})")
        if tr["compositor"]:
            lines.append(f"   Compositor: {tr['compositor']}")
        if tr["interpretes"]:
            lines.append("   Creditos:")
            for p in tr["interpretes"]:
                lines.append(f"     - {p}")
        lines.append("")
    _write_text(os.path.join(dirn, "credits.txt"), "\n".join(lines))


def generate_quality_report(
    dirn: str,
    album_title: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    quality_met: bool,
    results: List[Any],
    album_meta: dict,
) -> None:
    """
    Gera quality_report.txt com qualidade real por faixa.
    """
    tracks = album_meta.get("tracks", {}).get("items", [])
    lines = []
    lines.append(f"Album: {album_title}")
    lines.append(
        f"Qualidade alvo: {file_format} ({bit_depth}-bit / {sampling_rate} kHz)"
    )
    lines.append(f"Qualidade atingida: {'sim' if quality_met else 'nao'}")
    lines.append("")
    lines.append("Faixas:")
    for idx, i in enumerate(tracks):
        t_num = str(i.get("track_number", idx + 1)).zfill(2)
        t_title = i.get("title", "Faixa")
        res = results[idx] if idx < len(results) else None
        status = "OK" if res is True else ("PULADA" if res == "skipped" else "FALHA")
        lines.append(f"  {t_num}. {t_title} -> {status}")
    _write_text(os.path.join(dirn, "quality_report.txt"), "\n".join(lines))


def generate_index_entry(
    db_path: str,
    album_id: str,
    album_title: str,
    artist_name: str,
    dirn: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    release_date: str,
    url: str,
) -> None:
    """
    Gera index_entry.json para indice local da colecao.
    """
    entry = {
        "album_id": album_id,
        "album_titulo": album_title,
        "artista": artist_name,
        "caminho": os.path.abspath(dirn).replace("\\", "/"),
        "formato": file_format,
        "bit_depth": bit_depth,
        "sampling_rate": sampling_rate,
        "data_lancamento": release_date,
        "url": url,
        "gerado_em": _now_iso(),
    }
    # Anexa a um indice global (opcional)
    index_path = os.path.join(os.path.dirname(db_path), "collection_index.jsonl")
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def update_collection_report(db_path: str, stats: dict) -> None:
    """
    Atualiza collection_report.md com estatisticas da colecao.
    """
    report_path = os.path.join(os.path.dirname(db_path), "collection_report.md")
    lines = []
    lines.append("# Relatorio da Colecao")
    lines.append("")
    lines.append(f"Gerado em: {_now_iso()}")
    lines.append("")
    lines.append("## Totais")
    lines.append(f"- Albuns: {stats.get('albums', 0)}")
    lines.append(f"- Faixas: {stats.get('tracks', 0)}")
    lines.append(f"- Hi-Res (>=24bit): {stats.get('hires', 0)}")
    lines.append(f"- FLAC: {stats.get('flac', 0)}")
    lines.append(f"- MP3: {stats.get('mp3', 0)}")
    lines.append(f"- Qualidade atingida: {stats.get('quality_met', 0)}")
    lines.append(f"- Qualidade nao atingida: {stats.get('quality_not_met', 0)}")
    lines.append("")
    lines.append("## Formatos")
    for fmt, cnt in stats.get("formats", {}).items():
        lines.append(f"- {fmt}: {cnt}")
    lines.append("")
    lines.append("## Bit Depths")
    for bd, cnt in stats.get("bit_depths", {}).items():
        lines.append(f"- {bd}: {cnt}")
    lines.append("")
    lines.append("## Sample Rates")
    for sr, cnt in stats.get("sample_rates", {}).items():
        lines.append(f"- {sr}: {cnt}")
    lines.append("")
    lines.append("## Top Artistas")
    for nome, cnt in stats.get("top_artists", [])[:10]:
        lines.append(f"- {nome}: {cnt}")
    lines.append("")
    if stats.get("oldest") or stats.get("newest"):
        lines.append("## Periodo")
        if stats.get("oldest"):
            lines.append(f"- Mais antigo: {stats['oldest']}")
        if stats.get("newest"):
            lines.append(f"- Mais recente: {stats['newest']}")
        lines.append("")
    _write_text(report_path, "\n".join(lines))


# ============================================================================
# FAIXA AVULSA
# ============================================================================


def generate_track_report(
    dirn: str,
    track_id: str,
    track_title: str,
    artist_name: str,
    album_name: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    success: bool,
    reason: str = "",
) -> None:
    """
    Gera track_report.json para uma faixa avulsa.
    """
    report = {
        "track_id": track_id,
        "track_titulo": track_title,
        "artista": artist_name,
        "album": album_name,
        "formato": file_format,
        "bit_depth": bit_depth,
        "sampling_rate": sampling_rate,
        "sucesso": success,
        "motivo": reason,
        "gerado_em": _now_iso(),
    }
    _write_json(os.path.join(dirn, "track_report.json"), report)


def generate_track_state(
    dirn: str,
    track_id: str,
    track_title: str,
    artist_name: str,
    album_name: str,
    file_format: str,
    bit_depth: Any,
    sampling_rate: Any,
    success: bool,
    verified: bool = False,
) -> None:
    """
    Gera track_state.json para uma faixa avulsa.
    """
    state = {
        "track_id": track_id,
        "track_titulo": track_title,
        "artista": artist_name,
        "album": album_name,
        "formato": file_format,
        "bit_depth": bit_depth,
        "sampling_rate": sampling_rate,
        "baixada_com_sucesso": success,
        "verificada": verified,
        "gerado_em": _now_iso(),
    }
    _write_json(os.path.join(dirn, "track_state.json"), state)


# ============================================================================
# PLAYLIST
# ============================================================================


def generate_playlist_report(
    dirn: str,
    playlist_id: str,
    playlist_title: str,
    results: List[Any],
    tracks_meta: List[dict],
) -> None:
    """
    Gera playlist_report.json para uma playlist.
    """
    rows = []
    for idx, (t_meta, res) in enumerate(zip(tracks_meta, results)):
        t_num = str(t_meta.get("track_number", idx + 1)).zfill(2)
        t_title = t_meta.get("title", "Faixa")
        t_id = t_meta.get("id")
        t_artist = t_meta.get("performer", {}).get("name", "") or t_meta.get(
            "artist", {}
        ).get("name", "")
        status = "ok" if res is True else ("skipped" if res == "skipped" else "failed")
        reason = ""
        if status != "ok":
            if isinstance(res, Exception):
                reason = str(res)
            elif status == "skipped":
                reason = "indisponivel/demo/compra"
        rows.append(
            {
                "track_number": t_num,
                "track_id": t_id,
                "track_title": t_title,
                "artista": t_artist,
                "status": status,
                "reason": reason,
            }
        )

    report = {
        "playlist_id": playlist_id,
        "playlist_titulo": playlist_title,
        "gerado_em": _now_iso(),
        "faixas": rows,
    }
    _write_json(os.path.join(dirn, "playlist_report.json"), report)


def generate_playlist_log(
    dirn: str,
    playlist_id: str,
    playlist_title: str,
    results: List[Any],
    tracks_meta: List[dict],
) -> None:
    """
    Gera playlist_log.txt com resumo da playlist.
    """
    lines = []
    lines.append(f"Playlist: {playlist_title}")
    lines.append(f"ID: {playlist_id}")
    lines.append(f"Gerado em: {_now_iso()}")
    lines.append("")
    lines.append("Faixas:")
    for idx, (t_meta, res) in enumerate(zip(tracks_meta, results)):
        t_num = str(t_meta.get("track_number", idx + 1)).zfill(2)
        t_title = t_meta.get("title", "Faixa")
        t_artist = t_meta.get("performer", {}).get("name", "") or t_meta.get(
            "artist", {}
        ).get("name", "")
        status = "OK" if res is True else ("PULADA" if res == "skipped" else "FALHA")
        lines.append(f"  {t_num}. {t_title} ({t_artist}) -> {status}")
    lines.append("")
    ok = sum(1 for r in results if r is True)
    skipped = sum(1 for r in results if r == "skipped")
    failed = sum(1 for r in results if r not in (True, "skipped"))
    lines.append("Resumo:")
    lines.append(f"  Baixadas: {ok}")
    lines.append(f"  Puladas: {skipped}")
    lines.append(f"  Falhas: {failed}")
    _write_text(os.path.join(dirn, "playlist_log.txt"), "\n".join(lines))
