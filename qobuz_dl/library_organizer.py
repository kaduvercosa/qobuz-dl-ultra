"""Reaplica os formatos de pasta/faixa à biblioteca local, com dry-run e undo."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

from mutagen.flac import FLAC
from mutagen.id3 import ID3, ID3NoHeaderError
from mutagen.mp3 import MP3
from pathvalidate import sanitize_filename

from qobuz_dl import ui
from qobuz_dl.color import GRAY, GREEN, INFO, RED, RESET, YELLOW
from qobuz_dl.download_utils import (
    format_release_type,
    process_folder_format_with_subdirs,
)
from qobuz_dl.utils import clean_filename

logger = logging.getLogger(__name__)
AUDIO_EXTENSIONS = (".flac", ".mp3")
JOURNAL_DIRNAME = "organize_undo"
SIDECAR_SUFFIXES = (".lrc", ".txt", ".missing.txt")
_KEEP_RUNS = 5


def _first(tags: dict, *keys: str, default: str = "") -> str:
    for key in keys:
        try:
            value = tags.get(key)
        except (AttributeError, TypeError):
            value = None
        if isinstance(value, (list, tuple)):
            value = value[0] if value else None
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def _split_total(value: str) -> tuple[str, str]:
    parts = str(value or "").split("/", 1)
    return parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""


def _number(value: str, fallback: int = 0) -> int:
    try:
        return int(str(value).strip().split("/", 1)[0])
    except (TypeError, ValueError):
        return fallback


def _id3_values(path: str) -> tuple[dict[str, str], Any]:
    audio = MP3(path)
    tags: ID3 | None = audio.tags
    try:
        if tags is None:
            tags = ID3(path)
    except (ID3NoHeaderError, OSError):
        tags = audio.tags
    values: dict[str, str] = {}
    frame_map = {
        "TIT2": "TITLE",
        "TALB": "ALBUM",
        "TPE1": "ARTIST",
        "TPE2": "ALBUMARTIST",
        "TCOM": "COMPOSER",
        "TCON": "GENRE",
        "TDRC": "DATE",
        "TYER": "DATE",
        "TRCK": "TRACKNUMBER",
        "TPOS": "DISCNUMBER",
        "TSRC": "ISRC",
        "TCOP": "COPYRIGHT",
        "TPUB": "LABEL",
    }
    if tags:
        for frame in tags.values():
            frame_id = getattr(frame, "FrameID", "")
            if frame_id in frame_map:
                text = getattr(frame, "text", None)
                if text:
                    values[frame_map[frame_id]] = str(text[0])
            elif frame_id == "TXXX":
                desc = str(getattr(frame, "desc", "")).strip().upper()
                text = getattr(frame, "text", None)
                if text:
                    values[desc] = str(text[0])
    return values, audio


def _read_tag_values(path: str) -> tuple[dict[str, str], Any]:
    if path.lower().endswith(".flac"):
        audio = FLAC(path)
        values: dict[str, str] = {}
        tag_data: Any = audio.tags
        for key, value in (tag_data or {}).items():
            if isinstance(value, (list, tuple)):
                value = value[0] if value else ""
            values[str(key).strip().upper()] = (
                str(value).strip() if value is not None else ""
            )
        return values, audio
    return _id3_values(path)


def metadata_for_file(path: str) -> dict[str, Any]:
    """Reconstroi, tanto quanto possível, os placeholders a partir das tags locais."""
    tags, audio = _read_tag_values(path)
    title = _first(tags, "TITLE")
    album = _first(tags, "ALBUM")
    track_artist = _first(tags, "ARTIST")
    album_artist = _first(tags, "ALBUMARTIST", "ALBUM ARTIST", default=track_artist)
    track_raw, track_total = _split_total(_first(tags, "TRACKNUMBER", "TRACK"))
    disc_raw, disc_total = _split_total(_first(tags, "DISCNUMBER", "DISC"))
    date = _first(tags, "DATE", "YEAR")
    year = date[:4] if date else ""
    extension = os.path.splitext(path)[1].lower()
    audio_info = getattr(audio, "info", None)
    bit_depth = getattr(audio_info, "bits_per_sample", None)
    sample_rate = getattr(audio_info, "sample_rate", None)
    sampling_rate: str
    if sample_rate:
        sampling_rate = str(round(float(sample_rate) / 1000.0, 3))
        sampling_rate = sampling_rate.rstrip("0").rstrip(".")
    else:
        sampling_rate = _first(tags, "SAMPLING_RATE", "SAMPLINGRATE")
    if bit_depth is None:
        bit_depth = _first(tags, "BIT_DEPTH", "BITDEPTH")
    file_format = "MP3" if extension == ".mp3" else "FLAC"
    bit_depth_text = str(bit_depth) if bit_depth not in (None, "") else ""
    media_type = _first(tags, "MEDIATYPE", "RELEASETYPE")
    release_type = format_release_type(
        media_type,
        track_count=track_total or _first(tags, "TRACKTOTAL", "TOTALTRACKS"),
        title=album,
        version=_first(tags, "ALBUM_VERSION", "VERSION"),
        duration_seconds=getattr(audio_info, "length", 0),
    )
    is_explicit = _first(tags, "EXPLICIT", "ITUNESADVISORY").lower() in {
        "1",
        "true",
        "yes",
        "explicit",
    }
    album_id = _first(tags, "QOBUZALBUMID", "QOBUZ ALBUM ID", "ALBUM_ID")
    track_id = _first(tags, "QOBUZTRACKID", "QOBUZ TRACK ID", "TRACK_ID")
    barcode = _first(tags, "BARCODE", "UPC", "QOBUZUPC")
    quality_tag = (
        "MP3"
        if file_format == "MP3"
        else (f"FLAC {bit_depth_text}" if bit_depth_text else "FLAC")
    )
    attrs: dict[str, Any] = {
        "artist": track_artist or album_artist,
        "album": album,
        "tracktitle": title,
        "albumartist": album_artist,
        "track_title": title,
        "track_title_base": title,
        "album_title": album,
        "album_title_base": album,
        "album_artist": album_artist,
        "track_artist": track_artist or album_artist,
        "track_composer": _first(tags, "COMPOSER"),
        "album_composer": _first(tags, "ALBUMCOMPOSER", "COMPOSER"),
        "track_number": f"{_number(track_raw):02d}" if track_raw else "",
        "track_count": track_total or _first(tags, "TRACKTOTAL", "TOTALTRACKS"),
        "disc_number": f"{_number(disc_raw):02d}" if disc_raw else "",
        "disc_count": disc_total or _first(tags, "DISCTOTAL", "TOTALDISCS"),
        "track_id": track_id,
        "isrc": _first(tags, "ISRC", "TSRC"),
        "year": year,
        "release_date": date,
        "version": _first(tags, "VERSION", "ALBUM_VERSION"),
        "album_version": _first(tags, "ALBUM_VERSION", "VERSION"),
        "version_tag": "",
        "album_genre": _first(tags, "GENRE"),
        "genre": _first(tags, "GENRE"),
        "label": _first(tags, "LABEL", "ORGANIZATION", "PUBLISHER"),
        "copyright": _first(tags, "COPYRIGHT"),
        "upc": barcode,
        "barcode": barcode,
        "album_id": album_id,
        "album_url": _first(tags, "QOBUZ ALBUM URL", "ALBUM_URL"),
        "format": file_format,
        "bit_depth": bit_depth_text,
        "sampling_rate": sampling_rate,
        "quality_tag": quality_tag,
        "media_type": release_type,
        "release_type": release_type,
        "explicit": "🅴" if is_explicit else "",
        "ExplicitFlag": "🅴" if is_explicit else "",
    }
    return attrs


def _safe_stem(value: str, legacy_charmap: bool = False) -> str:
    return sanitize_filename(
        clean_filename(value, legacy_charmap=legacy_charmap), replacement_text="_"
    )


def _limit_path(value: str, limit: int = 180) -> str:
    if len(value) <= limit:
        return value
    head = value[:110].rstrip(" .\"-_'")
    tail = value[-60:].lstrip(" .\"-_'")
    return f"{head}...{tail}"


def _audio_files(root: str, include_playlists: bool) -> list[str]:
    found = []
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".qobuz-organize"))
        relative = os.path.relpath(current, root)
        first = relative.split(os.sep, 1)[0] if relative != "." else ""
        if not include_playlists and first.casefold() == "playlist":
            dirs[:] = []
            continue
        for name in files:
            if name.lower().endswith(AUDIO_EXTENSIONS):
                found.append(os.path.abspath(os.path.join(current, name)))
    return sorted(found)


def _formatted_destinations(
    root: str,
    source: str,
    attrs: dict[str, Any],
    folder_format: str,
    track_format: str,
    legacy_charmap: bool,
    multiple_disc_track_format: str,
    multiple_disc_prefix: str,
    multiple_disc_one_dir: bool,
) -> str:
    # Garante erro claro antes de mover qualquer arquivo se o formato contém
    # um placeholder desconhecido.
    folder_format.format(**attrs)
    track_format.format(**attrs)
    if multiple_disc_one_dir and _number(str(attrs.get("disc_count", ""))) > 1:
        multiple_disc_track_format.format(**attrs)
    root_dir = process_folder_format_with_subdirs(
        folder_format, attrs, root, legacy_charmap=legacy_charmap
    )
    track_stem_source = (
        multiple_disc_track_format
        if multiple_disc_one_dir and _number(str(attrs.get("disc_count", ""))) > 1
        else track_format
    )
    track_stem = _safe_stem(track_stem_source.format(**attrs), legacy_charmap)
    if not track_stem:
        raise ValueError("o formato da faixa produziu um nome vazio")
    # O download pode guardar o marcador de explícito na tag TITLE mesmo
    # quando o nome físico original não o tinha. Não introduza esse sufixo
    # durante a migração; preserve-o somente se já estiver no nome do arquivo.
    if not Path(source).stem.rstrip().endswith("🅴") and track_stem.rstrip().endswith(
        "🅴"
    ):
        track_stem = track_stem[:-1].rstrip()
    disc_count = _number(str(attrs.get("disc_count", "")), 1)
    disc_number = _number(str(attrs.get("disc_number", "")), 1)
    if not multiple_disc_one_dir and disc_count > 1:
        disc_folder = _safe_stem(
            f"{multiple_disc_prefix} {disc_number:02d}", legacy_charmap
        )
        track_stem = os.path.join(disc_folder or f"CD {disc_number:02d}", track_stem)
    track_stem = _limit_path(track_stem)
    destination = os.path.abspath(
        os.path.join(root_dir, track_stem + Path(source).suffix)
    )
    root_real = os.path.realpath(root)
    destination_real = os.path.realpath(destination)
    try:
        common = os.path.normcase(os.path.commonpath((root_real, destination_real)))
        within_root = common == os.path.normcase(root_real)
    except ValueError:
        within_root = False
    if not within_root:
        raise ValueError("o formato produziria um caminho fora da pasta da biblioteca")
    return destination


def _plan_moves(
    root: str,
    folder_format: str,
    track_format: str,
    *,
    multiple_disc_track_format: str,
    multiple_disc_prefix: str,
    multiple_disc_one_dir: bool,
    legacy_charmap: bool,
    include_playlists: bool,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    audio = _audio_files(root, include_playlists)
    planned: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []
    audit: dict[str, dict[str, Any]] = {}
    companion_sources: set[str] = set()
    failed_folders: set[str] = set()
    folder_targets: dict[str, dict[str, Any]] = {}
    audio_source_dirs = {
        os.path.normcase(os.path.abspath(os.path.dirname(path))) for path in audio
    }

    for source in audio:
        source_dir = os.path.abspath(os.path.dirname(source))
        source_dir_key = os.path.normcase(source_dir)
        source_key = os.path.normcase(os.path.abspath(source))
        try:
            attrs = metadata_for_file(source)
            if not attrs.get("track_title") or not attrs.get("album_title"):
                raise ValueError(
                    "tags TITLE/ALBUM ausentes; não é seguro calcular o destino"
                )
            destination = _formatted_destinations(
                root,
                source,
                attrs,
                folder_format,
                track_format,
                legacy_charmap,
                multiple_disc_track_format,
                multiple_disc_prefix,
                multiple_disc_one_dir,
            )
            album_dir = process_folder_format_with_subdirs(
                folder_format, attrs, root, legacy_charmap=legacy_charmap
            )
            folder_target = folder_targets.setdefault(
                source_dir_key,
                {
                    "source_dir": source_dir,
                    "album_dirs": {},
                    "track_dirs": {},
                },
            )
            folder_target["album_dirs"][os.path.normcase(album_dir)] = album_dir
            destination_dir = os.path.dirname(destination)
            folder_target["track_dirs"][os.path.normcase(destination_dir)] = (
                destination_dir
            )
            changed = source_key != os.path.normcase(os.path.abspath(destination))
            audit[source_key] = {
                "source": source,
                "destination": destination,
                "source_dir": source_dir,
                "status": "changed" if changed else "unchanged",
            }
            if changed:
                source_stem = os.path.splitext(source)[0]
                planned.append(
                    {
                        "source": source,
                        "destination": destination,
                        "_track_source": os.path.normcase(os.path.abspath(source_stem)),
                        "_source_dir": source_dir_key,
                    }
                )
                dest_stem = os.path.splitext(destination)[0]
                for suffix in SIDECAR_SUFFIXES:
                    sidecar = source_stem + suffix
                    if os.path.isfile(sidecar) and sidecar not in companion_sources:
                        companion_sources.add(sidecar)
                        sidecar_dest = dest_stem + suffix
                        if os.path.normcase(
                            os.path.abspath(sidecar)
                        ) != os.path.normcase(os.path.abspath(sidecar_dest)):
                            planned.append(
                                {
                                    "source": sidecar,
                                    "destination": sidecar_dest,
                                    "_track_source": os.path.normcase(
                                        os.path.abspath(source_stem)
                                    ),
                                    "_source_dir": source_dir_key,
                                }
                            )
        except Exception as exc:
            logger.debug("Não foi possível planejar %s: %s", source, exc, exc_info=True)
            failed_folders.add(source_dir_key)
            audit[source_key] = {
                "source": source,
                "destination": None,
                "source_dir": source_dir,
                "status": "skipped",
                "reason": str(exc),
            }
            errors.append({"source": source, "reason": str(exc)})

    planned_sources = {
        os.path.normcase(os.path.abspath(item["source"])) for item in planned
    }
    for source_dir_key, target in folder_targets.items():
        source_dir = target["source_dir"]
        if source_dir_key in failed_folders:
            continue
        album_dirs = target["album_dirs"]
        track_dirs = target["track_dirs"]
        if len(album_dirs) != 1:
            errors.append(
                {
                    "source": source_dir,
                    "reason": "a pasta contém faixas de álbuns com destinos diferentes; arquivos acompanhantes não foram movidos",
                }
            )
            failed_folders.add(source_dir_key)
            continue
        if len(track_dirs) == 1:
            destination_base = next(iter(track_dirs.values()))
        else:
            destination_base = next(iter(album_dirs.values()))
        if source_dir_key == os.path.normcase(os.path.abspath(destination_base)):
            continue
        if source_dir_key == os.path.normcase(os.path.abspath(root)):
            has_root_assets = any(
                entry.is_file()
                and os.path.normcase(os.path.abspath(entry.path)) not in planned_sources
                and not entry.name.lower().endswith(AUDIO_EXTENSIONS)
                for entry in os.scandir(source_dir)
            )
            if has_root_assets:
                errors.append(
                    {
                        "source": source_dir,
                        "reason": "arquivos extras na raiz da biblioteca não foram movidos por segurança",
                    }
                )
                failed_folders.add(source_dir_key)
                continue

        # Inclui também dotfiles e arquivos dentro de subpastas. Subpastas que
        # contêm outras faixas são processadas pelo grupo próprio dessas faixas.
        for current_dir, directories, filenames in os.walk(source_dir):
            if current_dir != source_dir:
                current_key = os.path.normcase(os.path.abspath(current_dir))
                if current_key in audio_source_dirs:
                    directories[:] = []
                    continue
            directories[:] = [
                name
                for name in directories
                if os.path.normcase(os.path.abspath(os.path.join(current_dir, name)))
                not in audio_source_dirs
            ]
            for filename in filenames:
                asset_source = os.path.abspath(os.path.join(current_dir, filename))
                asset_key = os.path.normcase(asset_source)
                if asset_key in planned_sources or filename.lower().endswith(
                    AUDIO_EXTENSIONS
                ):
                    continue
                relative = os.path.relpath(asset_source, source_dir)
                asset_destination = os.path.abspath(
                    os.path.join(destination_base, relative)
                )
                if asset_key == os.path.normcase(asset_destination):
                    continue
                try:
                    within_root = os.path.normcase(
                        os.path.commonpath(
                            (
                                os.path.realpath(root),
                                os.path.realpath(asset_destination),
                            )
                        )
                    ) == os.path.normcase(os.path.realpath(root))
                except ValueError:
                    within_root = False
                if not within_root:
                    errors.append(
                        {
                            "source": asset_source,
                            "reason": "o destino do arquivo acompanhante ficaria fora da biblioteca",
                        }
                    )
                    failed_folders.add(source_dir_key)
                    continue
                planned.append(
                    {
                        "source": asset_source,
                        "destination": asset_destination,
                        "_track_source": asset_key,
                        "_source_dir": source_dir_key,
                    }
                )
                planned_sources.add(asset_key)

    source_keys = {
        os.path.normcase(os.path.abspath(item["source"])) for item in planned
    }
    by_destination: dict[str, list[dict[str, str]]] = {}
    for item in planned:
        key = os.path.normcase(os.path.abspath(item["destination"]))
        by_destination.setdefault(key, []).append(item)
    conflicted: set[int] = set()
    for key, items in by_destination.items():
        if len(items) > 1:
            conflicted.update(id(item) for item in items)
            for item in items:
                errors.append(
                    {
                        "source": item["source"],
                        "reason": f"mais de um arquivo teria o destino '{item['destination']}'",
                    }
                )
        destination = items[0]["destination"]
        if os.path.exists(destination) and key not in source_keys:
            conflicted.update(id(item) for item in items)
            for item in items:
                errors.append(
                    {
                        "source": item["source"],
                        "reason": f"destino já existe: '{destination}'",
                    }
                )
    blocked_tracks = {
        item["_track_source"] for item in planned if id(item) in conflicted
    }
    blocked_folders = failed_folders | {
        item["_source_dir"] for item in planned if id(item) in conflicted
    }
    for record in audit.values():
        source_dir_key = os.path.normcase(os.path.abspath(record["source_dir"]))
        if source_dir_key in blocked_folders:
            record["status"] = "skipped"
            record.setdefault(
                "reason",
                "a pasta do álbum foi bloqueada para evitar movimentação parcial",
            )

    known_error_sources = {
        os.path.normcase(os.path.abspath(error["source"]))
        for error in errors
        if error["source"].lower().endswith(AUDIO_EXTENSIONS)
    }
    for source_key, record in audit.items():
        if record["status"] == "skipped" and source_key not in known_error_sources:
            errors.append(
                {
                    "source": record["source"],
                    "reason": record.get(
                        "reason", "a faixa não pôde ser movida com segurança"
                    ),
                }
            )
    safe = []
    for item in planned:
        if (
            id(item) in conflicted
            or item["_track_source"] in blocked_tracks
            or item["_source_dir"] in blocked_folders
        ):
            continue
        safe.append(
            {key: value for key, value in item.items() if not key.startswith("_")}
        )
    return safe, errors, list(audit.values())


def _relative_label(root: str, path: str) -> str:
    relative = os.path.relpath(path, root)
    return "raiz da biblioteca" if relative == "." else relative


def _group_moves(
    root: str, moves: list[dict[str, str]]
) -> tuple[
    list[dict[str, str]],
    list[dict[str, str]],
    dict[tuple[str, str], dict[str, list[dict[str, str]]]],
]:
    tracks = [
        item for item in moves if item["source"].lower().endswith(AUDIO_EXTENSIONS)
    ]
    sidecars = [item for item in moves if item not in tracks]
    grouped: dict[tuple[str, str], dict[str, list[dict[str, str]]]] = {}
    group_by_stem: dict[str, tuple[str, str]] = {}

    for item in tracks:
        source_dir = os.path.dirname(item["source"])
        destination_dir = os.path.dirname(item["destination"])
        key = (
            _relative_label(root, source_dir),
            _relative_label(root, destination_dir),
        )
        group = grouped.setdefault(key, {"tracks": [], "sidecars": []})
        group["tracks"].append(item)
        stem = os.path.splitext(item["source"])[0]
        group_by_stem[os.path.normcase(os.path.abspath(stem))] = key

    for item in sidecars:
        source = item["source"]
        source_lower = source.lower()
        stem = next(
            (
                source[: -len(suffix)]
                for suffix in sorted(SIDECAR_SUFFIXES, key=len, reverse=True)
                if source_lower.endswith(suffix)
            ),
            os.path.splitext(source)[0],
        )
        group_key = group_by_stem.get(os.path.normcase(os.path.abspath(stem)))
        if group_key is None:
            group_key = (
                _relative_label(root, os.path.dirname(source)),
                _relative_label(root, os.path.dirname(item["destination"])),
            )
        group = grouped.setdefault(group_key, {"tracks": [], "sidecars": []})
        group["sidecars"].append(item)

    return tracks, sidecars, grouped


def _print_move_groups(
    grouped: dict[tuple[str, str], dict[str, list[dict[str, str]]]],
) -> None:
    for (source_dir, destination_dir), group in sorted(grouped.items()):
        if source_dir == destination_dir:
            ui.emit(f"  {GRAY}• Pasta mantida:{RESET} {source_dir}")
        else:
            ui.emit(f"  {GREEN}→{RESET} {source_dir} {INFO}→{RESET} {destination_dir}")
        ui.emit(
            f"    {GREEN}{len(group['tracks'])}{RESET} faixa(s); "
            f"{INFO}{len(group['sidecars'])}{RESET} arquivo(s) acompanhante(s)"
        )
        renamed = [
            item
            for item in group["tracks"]
            if os.path.basename(item["source"]) != os.path.basename(item["destination"])
        ]
        for item in renamed[:3]:
            ui.emit(
                f"    {YELLOW}Nome:{RESET} {os.path.basename(item['source'])}  "
                f"{GREEN}→{RESET} {os.path.basename(item['destination'])}"
            )
        if len(renamed) > 3:
            ui.emit(
                f"    {GRAY}... e mais {len(renamed) - 3} nome(s) alterado(s){RESET}"
            )


def _emit_dry_run(
    root: str, moves: list[dict[str, str]], show_files: bool
) -> tuple[int, int]:
    tracks, sidecars, grouped = _group_moves(root, moves)
    ui.step(
        f"{INFO}Prévia:{RESET} {GREEN}{len(tracks)}{RESET} faixa(s) e "
        f"{INFO}{len(sidecars)}{RESET} arquivo(s) acompanhante(s) envolvidos"
    )
    _print_move_groups(grouped)
    if show_files:
        ui.step("Detalhes por arquivo:")
        for item in moves:
            source_relative = os.path.relpath(item["source"], root)
            destination_relative = os.path.relpath(item["destination"], root)
            ui.emit(f"  {source_relative}  {GREEN}→{RESET} {destination_relative}")
    return len(tracks), len(sidecars)


def _atomic_json(path: str, data: dict[str, Any]) -> None:
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".~tmp_", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_path, path)
    except BaseException:
        try:
            os.remove(temp_path)
        except OSError:
            logger.debug(
                "Não foi possível remover o JSON temporário %s",
                temp_path,
                exc_info=True,
            )
        raise


def _new_run_dir(base_dir: str) -> tuple[str, str]:
    base = os.path.join(base_dir, JOURNAL_DIRNAME)
    os.makedirs(base, exist_ok=True)
    stem = time.strftime("%Y%m%d_%H%M%S")
    run_id = stem
    suffix = 1
    while os.path.exists(os.path.join(base, run_id)):
        suffix += 1
        run_id = f"{stem}_{suffix}"
    run_dir = os.path.join(base, run_id)
    os.makedirs(run_dir)
    return run_id, run_dir


def _journal_file(run_dir: str) -> str:
    return os.path.join(run_dir, "journal.json")


def _save_journal(run_dir: str | None, journal: dict[str, Any]) -> None:
    if run_dir is None:
        return
    _atomic_json(_journal_file(run_dir), journal)


def list_runs(base_dir: str) -> list[dict[str, Any]]:
    base = os.path.join(base_dir, JOURNAL_DIRNAME)
    if not os.path.isdir(base):
        return []
    result = []
    for run_id in sorted(os.listdir(base), reverse=True):
        run_dir = os.path.join(base, run_id)
        if not os.path.isdir(run_dir):
            continue
        try:
            with open(_journal_file(run_dir), encoding="utf-8") as handle:
                data = json.load(handle)
            entries = data.get("moves", [])
            result.append(
                {
                    "id": run_id,
                    "root": data.get("root", "?"),
                    "moves": len(entries),
                    "status": data.get("status", "?"),
                }
            )
        except (OSError, ValueError):
            logger.debug("Journal ilegível em %s", run_dir)
    return result


def _move_no_replace(source: str, destination: str) -> None:
    if os.path.exists(destination):
        raise FileExistsError(destination)
    os.makedirs(os.path.dirname(destination), exist_ok=True)
    os.rename(source, destination)


def _current_location(entry: dict[str, Any]) -> str | None:
    state = entry.get("state", "planned")
    candidates = [entry.get("undo_temp")]
    if entry.get("undo_state") == "undo_staged":
        candidates.append(entry.get("undo_temp"))
    if state == "done":
        candidates.extend(
            (entry.get("destination"), entry.get("temp"), entry.get("source"))
        )
    elif state == "staged":
        candidates.extend(
            (entry.get("temp"), entry.get("source"), entry.get("destination"))
        )
    else:
        candidates.extend(
            (entry.get("temp"), entry.get("source"), entry.get("destination"))
        )
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def _rollback_moves(
    run_dir: str | None, journal: dict[str, Any], *, dry_run: bool = False
) -> dict[str, int]:
    moves = journal.get("moves", [])
    counts: Counter[str] = Counter()
    active: list[tuple[dict[str, Any], str]] = []
    for entry in moves:
        if entry.get("undo_state") == "undone":
            continue
        current = _current_location(entry)
        if current is None:
            counts["missing"] += 1
            continue
        source = entry["source"]
        if os.path.abspath(current) == os.path.abspath(source):
            if not dry_run:
                entry["undo_state"] = "undone"
                _save_journal(run_dir, journal)
            counts["already_original"] += 1
        else:
            active.append((entry, current))

    current_paths = {
        os.path.normcase(os.path.abspath(current)) for _, current in active
    }
    safe: list[tuple[dict[str, Any], str]] = []
    for entry, current in active:
        source = entry["source"]
        source_key = os.path.normcase(os.path.abspath(source))
        if os.path.exists(source) and source_key not in current_paths:
            counts["conflict"] += 1
            continue
        safe.append((entry, current))
    if dry_run:
        counts["would_restore"] = len(safe)
        return dict(counts)

    # Primeira fase: liberar todos os caminhos originais (inclusive em ciclos).
    for entry, current in safe:
        if entry.get("undo_state") != "undo_staged":
            folder = os.path.dirname(entry["source"])
            os.makedirs(folder, exist_ok=True)
            undo_temp_path = os.path.join(folder, f".~qobuz-undo-{uuid.uuid4().hex}")
            _move_no_replace(current, undo_temp_path)
            entry["undo_temp"] = undo_temp_path
            entry["undo_state"] = "undo_staged"
            _save_journal(run_dir, journal)

    # Segunda fase: restaurar cada item à localização original.
    for entry, _current in safe:
        if entry.get("undo_state") != "undo_staged":
            continue
        undo_temp = entry.get("undo_temp")
        source = entry["source"]
        if not isinstance(undo_temp, str) or not os.path.exists(undo_temp):
            counts["missing"] += 1
            continue
        try:
            _move_no_replace(undo_temp, source)
            entry["undo_state"] = "undone"
            _save_journal(run_dir, journal)
            counts["restored"] += 1
        except OSError as exc:
            logger.warning("Não foi possível restaurar %s: %s", source, exc)
            counts["errors"] += 1
    journal["status"] = (
        "desfeita"
        if all(m.get("undo_state") == "undone" for m in moves)
        else "undo_parcial"
    )
    _save_journal(run_dir, journal)
    return dict(counts)


def undo_run(
    base_dir: str, run_id: str | None = None, *, dry_run: bool = False
) -> dict[str, Any] | None:
    runs = list_runs(base_dir)
    if not runs:
        return None
    selected = run_id or runs[0]["id"]
    if selected == "last":
        selected = runs[0]["id"]
    if os.path.basename(selected) != selected:
        return None
    run_dir = os.path.join(base_dir, JOURNAL_DIRNAME, selected)
    try:
        with open(_journal_file(run_dir), encoding="utf-8") as handle:
            journal = json.load(handle)
    except (OSError, ValueError):
        return None
    counts = _rollback_moves(run_dir, journal, dry_run=dry_run)
    return {"run": selected, **counts}


def organize_directory(
    root: str,
    *,
    folder_format: str,
    track_format: str,
    multiple_disc_track_format: str,
    journal_base: str,
    dry_run: bool = False,
    no_undo: bool = False,
    legacy_charmap: bool = False,
    multiple_disc_prefix: str = "CD",
    multiple_disc_one_dir: bool = False,
    include_playlists: bool = False,
    show_files: bool = False,
) -> dict[str, Any]:
    root = os.path.abspath(os.path.expanduser(root))
    if not os.path.isdir(root):
        raise NotADirectoryError(root)
    moves, errors, audit = _plan_moves(
        root,
        folder_format,
        track_format,
        multiple_disc_track_format=multiple_disc_track_format,
        multiple_disc_prefix=multiple_disc_prefix,
        multiple_disc_one_dir=multiple_disc_one_dir,
        legacy_charmap=legacy_charmap,
        include_playlists=include_playlists,
    )
    track_count = sum(
        item["source"].lower().endswith(AUDIO_EXTENSIONS) for item in moves
    )
    sidecar_count = len(moves) - track_count
    unchanged_tracks = [item for item in audit if item["status"] == "unchanged"]
    skipped_tracks = [item for item in audit if item["status"] == "skipped"]
    report = {
        "planned": len(moves),
        "tracks": track_count,
        "sidecars": sidecar_count,
        "total_tracks": len(audit),
        "unchanged_tracks": len(unchanged_tracks),
        "skipped_tracks": len(skipped_tracks),
        "unchanged": unchanged_tracks,
        "skipped": skipped_tracks,
        "changes": moves,
        "error_details": errors,
        "errors": len(errors),
    }
    if dry_run:
        _emit_dry_run(root, moves, show_files)
        return {
            **report,
            "moved": 0,
            "run_id": None,
        }
    if not moves:
        return {
            **report,
            "moved": 0,
            "run_id": None,
        }

    ui.step(
        f"Aplicando {track_count} faixa(s) e "
        f"{sidecar_count} arquivo(s) acompanhante(s)..."
    )
    run_id = None
    run_dir = None
    if not no_undo:
        run_id, run_dir = _new_run_dir(journal_base)
    entries: list[dict[str, Any]] = []
    for item in moves:
        entries.append(
            {
                **item,
                "temp": os.path.join(
                    os.path.dirname(item["source"]),
                    f".~qobuz-organize-{uuid.uuid4().hex}",
                ),
                "state": "planned",
                "undo_state": None,
                "undo_temp": None,
            }
        )
    journal = {
        "root": root,
        "started": time.time(),
        "status": "em_andamento",
        "moves": entries,
    }
    if run_dir:
        _save_journal(run_dir, journal)

    moved = 0
    try:
        # Encena todas as origens em temporários antes de ocupar os destinos;
        # isso torna seguros ciclos de renomeação sem sobrescrever nada.
        for entry in entries:
            _move_no_replace(entry["source"], entry["temp"])
            entry["state"] = "staged"
            if run_dir:
                _save_journal(run_dir, journal)
        for entry in entries:
            _move_no_replace(entry["temp"], entry["destination"])
            entry["state"] = "done"
            moved += 1
            if run_dir:
                _save_journal(run_dir, journal)
        journal["status"] = "concluida"
        if run_dir:
            _save_journal(run_dir, journal)
    except BaseException:
        if run_dir:
            _rollback_moves(run_dir, journal)
        else:
            # Sem journal, ainda tentamos reverter as movimentações concluídas.
            temp_journal = {"moves": entries, "status": "interrompida"}
            for entry in entries:
                entry["undo_state"] = None
            _rollback_moves(None, temp_journal)
        raise

    if run_dir:
        base = os.path.join(journal_base, JOURNAL_DIRNAME)
        old_runs = sorted(
            (
                name
                for name in os.listdir(base)
                if os.path.isdir(os.path.join(base, name))
            )
        )
        for old in old_runs[: max(0, len(old_runs) - _KEEP_RUNS)]:
            try:
                import shutil

                shutil.rmtree(os.path.join(base, old))
            except OSError:
                logger.debug("Não foi possível remover journal antigo %s", old)
    return {
        **report,
        "moved": moved,
        "run_id": run_id,
    }


def _emit_operation_summary(
    root: str, result: dict[str, Any], *, dry_run: bool
) -> None:
    if dry_run:
        ui.ok("Simulação concluída; nenhum arquivo foi alterado.")
        verb = "seriam movidas"
        companion_verb = "seriam movidos"
    else:
        ui.ok("Organização concluída.")
        verb = "movidas"
        companion_verb = "movidos"

    skipped_color = YELLOW if result["skipped_tracks"] else GRAY
    error_color = RED if result["errors"] else GRAY
    ui.emit("")
    ui.emit(f"{INFO}Resumo da organização{RESET}")
    ui.emit(f"  {INFO}Faixas analisadas:{RESET} {GREEN}{result['total_tracks']}{RESET}")
    ui.emit(f"  {GREEN}Faixas {verb}:{RESET} {GREEN}{result['tracks']}{RESET}")
    ui.emit(
        f"  {INFO}Arquivos acompanhantes {companion_verb}:{RESET} "
        f"{INFO}{result['sidecars']}{RESET}"
    )
    ui.emit(
        f"  {GRAY}Faixas sem alterações:{RESET} "
        f"{GREEN}{result['unchanged_tracks']}{RESET}"
    )
    ui.emit(
        f"  {skipped_color}Faixas puladas:{RESET} "
        f"{skipped_color}{result['skipped_tracks']}{RESET}"
    )
    ui.emit(
        f"  {error_color}Ocorrências de erro/conflito:{RESET} "
        f"{error_color}{result['errors']}{RESET}"
    )

    if not dry_run:
        ui.emit("")
        ui.emit(f"{INFO}Álbuns/pastas alterados{RESET}")
        if result["changes"]:
            _, _, grouped = _group_moves(root, result["changes"])
            _print_move_groups(grouped)
        else:
            ui.emit(f"  {GRAY}Nenhum.{RESET}")

    unchanged_by_folder: dict[str, int] = {}
    for record in result["unchanged"]:
        folder = _relative_label(root, os.path.dirname(record["source"]))
        unchanged_by_folder[folder] = unchanged_by_folder.get(folder, 0) + 1
    ui.emit("")
    ui.emit(f"{GRAY}Sem alterações{RESET}")
    if unchanged_by_folder:
        for folder, count in sorted(unchanged_by_folder.items()):
            ui.emit(
                f"  {GREEN}✓{RESET} {folder}: {GREEN}{count}{RESET} faixa(s) "
                f"já estava(m) no destino/nome correto"
            )
    else:
        ui.emit(f"  {GRAY}Nenhuma faixa.{RESET}")

    ui.emit("")
    ui.emit(f"{YELLOW}Pulados / conflitos{RESET}")
    if result["error_details"]:
        for error in result["error_details"][:8]:
            ui.emit(
                f"  {YELLOW}•{RESET} {os.path.basename(error['source'])}: "
                f"{RED}{error['reason']}{RESET}"
            )
        if len(result["error_details"]) > 8:
            ui.emit(
                f"  {GRAY}... e mais {len(result['error_details']) - 8} ocorrência(s){RESET}"
            )
    else:
        ui.emit(f"  {GREEN}✓ Nenhum.{RESET}")


def cmd_organize(
    arguments: Any,
    *,
    directory: str,
    journal_base: str,
    folder_format: str,
    track_format: str,
    multiple_disc_track_format: str,
    legacy_charmap: bool = False,
    multiple_disc_prefix: str = "CD",
    multiple_disc_one_dir: bool = False,
) -> int:
    if getattr(arguments, "undo_list", False):
        runs = list_runs(journal_base)
        if not runs:
            ui.warn("Nenhuma execução do organize guardada para desfazer.")
            return 0
        ui.step("Execuções do organize guardadas (da mais recente):")
        for run in runs:
            ui.emit(
                f"  {run['id']}  {run['moves']} movimentação(ões)  status={run['status']}"
            )
        return 0

    if getattr(arguments, "undo", None):
        dry_run = bool(getattr(arguments, "dry_run", False))
        result = undo_run(journal_base, arguments.undo, dry_run=dry_run)
        if result is None:
            ui.error(
                "Nenhuma execução do organize encontrada para desfazer. Use --undo-list para consultar."
            )
            return 1
        ui.emit(
            f"Execução {result['run']}: restaurados={result.get('restored', 0)}, seriam restaurados={result.get('would_restore', 0)}, conflito={result.get('conflict', 0)}, ausentes={result.get('missing', 0)}, falhas={result.get('errors', 0)}"
        )
        return 1 if result.get("conflict", 0) or result.get("errors", 0) else 0

    target = getattr(arguments, "DIR", None) or directory
    target_name = os.path.basename(os.path.normpath(os.path.expanduser(target)))
    ui.step(f"Reorganizando biblioteca local: {target_name or 'biblioteca'}")
    ui.emit(f"{INFO}Formato de pasta:{RESET} {folder_format}")
    ui.emit(f"{INFO}Formato de faixa:{RESET} {track_format}")
    ui.emit("")
    try:
        result = organize_directory(
            target,
            folder_format=folder_format,
            track_format=track_format,
            multiple_disc_track_format=multiple_disc_track_format,
            journal_base=journal_base,
            dry_run=bool(getattr(arguments, "dry_run", False)),
            no_undo=bool(getattr(arguments, "no_undo", False)),
            legacy_charmap=legacy_charmap,
            multiple_disc_prefix=multiple_disc_prefix,
            multiple_disc_one_dir=multiple_disc_one_dir,
            include_playlists=bool(getattr(arguments, "include_playlists", False)),
            show_files=bool(getattr(arguments, "show_files", False)),
        )
    except (OSError, ValueError) as exc:
        if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
            ui.error(
                "A pasta da biblioteca não existe ou não está acessível. "
                "Confira 'directory' no config.ini ou informe uma pasta no comando."
            )
        else:
            ui.error(f"Não foi possível organizar a biblioteca: {exc}")
        return 1
    dry_run = bool(getattr(arguments, "dry_run", False))
    summary_root = os.path.abspath(os.path.expanduser(target))
    _emit_operation_summary(summary_root, result, dry_run=dry_run)
    if not dry_run and result.get("run_id"):
        ui.emit("")
        ui.emit(f"Para desfazer: qobuz-dl organize --undo {result['run_id']}")
    return 1 if result["errors"] else 0
