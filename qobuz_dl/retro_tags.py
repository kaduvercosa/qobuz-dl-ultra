# ============================================================================
# retro_tags.py -- injecao/correcao RETROATIVA de tags de metadados.
# ============================================================================
# Reescreve as tags de arquivos ja baixados a partir dos metadados atuais do
# Qobuz. Quando a Apple/iTunes encontrar uma capa validada, ela e baixada
# temporariamente, embutida no arquivo e registrada no comentario como
# "Capa: Apple/iTunes". Se a Apple nao retornar uma capa, a arte ja embutida
# e preservada.
# ============================================================================

import asyncio
import logging
import os
import re
import tempfile
from typing import Optional
import functools

import httpx
import mutagen.id3 as id3
from mutagen.flac import FLAC

from qobuz_dl import metadata, ui
from qobuz_dl.http_download import make_client
from qobuz_dl.color import GREEN, OFF, RED, RESET
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import WARNING as YELLOW
from qobuz_dl.color import WARNING as MUTED
from qobuz_dl.retro_tagger import extract_track_id

logger = logging.getLogger(__name__)

AUDIO_EXTENSIONS = (".flac", ".mp3")


def resolve_library_dir(target_dir: str) -> str:
    """Normaliza a pasta alvo, sandbox do iOS/a-Shell e caminho Windows."""
    target_dir = os.path.expanduser(target_dir)
    home_dir = os.environ.get("HOME", "")

    if "Containers/Data/Application" in home_dir:
        docs_dir = os.path.join(home_dir, "Documents")
        if not target_dir.startswith(docs_dir):
            base_name = os.path.basename(target_dir.rstrip("/\\"))
            target_dir = os.path.join(
                docs_dir,
                base_name if base_name else "Qobuz Downloads",
            )

    if os.name == "nt":
        target_dir = os.path.abspath(target_dir)
        if not target_dir.startswith("\\\\?\\"):
            target_dir = "\\\\?\\" + target_dir

    return target_dir


def find_audio_files(directory_path: str) -> list[str]:
    """Lista arquivos FLAC/MP3 existentes, em ordem estavel."""
    found = []

    for root, _, files in os.walk(directory_path):
        for name in files:
            if name.startswith("~tmp_"):
                continue
            if name.lower().endswith(AUDIO_EXTENSIONS):
                found.append(os.path.join(root, name))

    found.sort()
    return found


def extract_album_id(file_path: str) -> Optional[str]:
    """Le o ID Qobuz do album nas tags, quando estiver disponivel."""
    extension = os.path.splitext(file_path)[1].lower()

    try:
        if extension == ".flac":
            audio = FLAC(file_path)

            for tag in ("QOBUZALBUMID", "QOBUZ ALBUM ID", "ALBUM_ID"):
                value = audio.get(tag)
                if value and str(value[0]).strip():
                    return str(value[0]).strip()

            for comment in audio.get("COMMENT", []):
                match = re.search(
                    r"qobuz\.com/album/([0-9a-zA-Z]+)",
                    str(comment),
                )
                if match:
                    return match.group(1)

        elif extension == ".mp3":
            audio_id3 = id3.ID3(file_path)

            for frame in audio_id3.getall("TXXX"):
                desc = frame.desc.upper().replace(" ", "").replace("_", "")
                if desc == "QOBUZALBUMID" and frame.text:
                    value = str(frame.text[0]).strip()
                    if value:
                        return value

    except Exception as exc:
        logger.debug(
            "Falha ao ler o ID do album em %s: %s",
            file_path,
            exc,
        )

    return None


def snapshot_tags(file_path: str) -> dict:
    """Captura tags e estado da capa para comparar antes/depois do retag."""
    extension = os.path.splitext(file_path)[1].lower()
    snapshot: dict = {"text": {}, "art": []}

    try:
        if extension == ".flac":
            audio = FLAC(file_path)

            for key, values in (audio.tags or {}).items():
                snapshot["text"][key.upper()] = [str(value) for value in values]

            snapshot["art"] = [
                (
                    picture.type,
                    picture.mime,
                    picture.desc,
                    len(picture.data),
                )
                for picture in audio.pictures
            ]

        elif extension == ".mp3":
            audio_id3 = id3.ID3(file_path)

            for key, frame in audio_id3.items():
                if key.startswith("APIC"):
                    continue

                text = getattr(frame, "text", None)
                snapshot["text"][key] = (
                    [str(value) for value in text]
                    if text is not None
                    else [repr(frame)]
                )

            snapshot["art"] = [
                (
                    frame.mime,
                    frame.type,
                    frame.desc,
                    len(frame.data),
                )
                for frame in audio_id3.getall("APIC")
            ]

    except Exception as exc:
        logger.debug(
            "Falha ao tirar foto das tags de %s: %s",
            file_path,
            exc,
        )

    return snapshot


def multi_tags_label(settings) -> str:
    """Texto curto com o modo multi-tags em uso."""
    return "ATIVADO" if getattr(settings, "multi_value_tags", False) else "DESATIVADO"


async def _fetch_album(client, album_id, cache: dict):
    """Busca o album uma unica vez, usando cache pelo ID."""
    if album_id in cache:
        return cache[album_id]

    try:
        cache[album_id] = await client.get_album_meta(album_id)
    except Exception as exc:
        logger.debug("Falha ao buscar o album %s: %s", album_id, exc)
        cache[album_id] = None

    return cache[album_id]


async def _resolve_metadata(client, track_id, album_id, album_cache):
    """Devolve item, album e istrack no mesmo formato usado pelo download."""
    track_meta = None

    if not album_id:
        track_meta = await client.get_track_meta(track_id)
        album_id = (track_meta.get("album") or {}).get("id")

    if album_id:
        album_meta = await _fetch_album(client, album_id, album_cache)
        if album_meta:
            for item in (album_meta.get("tracks") or {}).get("items", []):
                if str(item.get("id")) == str(track_id):
                    return item, album_meta, False

    if track_meta is None:
        track_meta = await client.get_track_meta(track_id)

    if track_meta and track_meta.get("album"):
        return track_meta, track_meta["album"], True

    return None


async def _fetch_apple_cover_for_track(
    item: dict,
    album: dict,
    http_session: httpx.AsyncClient,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Busca URL, fonte e diagnóstico da busca Apple."""
    from qobuz_dl.utils import get_apple_hq_cover

    album_data = item.get("album") or album or {}
    artist_data = (
        item.get("performer") or album_data.get("artist") or album.get("artist") or {}
    )

    # A Qobuz separa "title" e "version" (ex.: "Fat Juicy &…" + "Radio Edit"),
    # mas a Apple junta tudo no nome da faixa. Sem a versao, o titulo pedido
    # ("Fat Juicy &…") nunca bate com o da Apple ("Fat Juicy &... (Radio Edit)").
    track_title = (item.get("title") or "").strip()
    track_version = (item.get("version") or "").strip()
    if track_version and track_version.casefold() not in track_title.casefold():
        track_title = f"{track_title} ({track_version})"

    result = await get_apple_hq_cover(
        session=http_session,
        upc=album_data.get("upc") or album.get("upc"),
        isrc=item.get("isrc"),
        artist=artist_data.get("name", ""),
        album=album_data.get("title") or album.get("title", ""),
        track_title=track_title,
    )

    if isinstance(result, tuple):
        url = result[0] if len(result) > 0 else None
        source = result[1] if len(result) > 1 else None
        reason = result[2] if len(result) > 2 else None
        if not isinstance(url, str) or not url.strip():
            return None, None, reason or "Apple não encontrou candidato válido"
        return url, source or "Apple/iTunes", reason

    if isinstance(result, str) and result.strip():
        return result, "Apple/iTunes", None
    return None, None, "Busca Apple sem URL de capa"


async def _download_apple_cover(
    http_session: httpx.AsyncClient,
    cover_url: str,
    directory: str,
) -> Optional[str]:
    """Baixa uma capa Apple temporaria e devolve seu caminho local."""
    path: Optional[str] = None

    try:
        response = await http_session.get(
            cover_url,
            timeout=20,
            follow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if content_type and "image/" not in content_type:
            logger.warning(
                "Apple retornou conteudo inesperado para capa: %s (%s)",
                content_type,
                cover_url,
            )
            return None

        if not response.content:
            logger.warning("Apple retornou uma capa vazia: %s", cover_url)
            return None

        descriptor, path = tempfile.mkstemp(
            prefix=".apple-cover-",
            suffix=".jpg",
            dir=directory,
        )

        with os.fdopen(descriptor, "wb") as cover_file:
            cover_file.write(response.content)

        return path

    except Exception as exc:
        logger.warning("Falha ao baixar capa Apple '%s': %s", cover_url, exc)

        if path:
            try:
                os.remove(path)
            except OSError:
                pass

        return None


def _display_track_name(
    item: dict, album: dict, file_path: str, limit: int = 54
) -> str:
    """Retorna apenas artista e título/versão, sem pastas, faixa ou extensão."""
    item = item or {}
    album = album or {}
    album_data = item.get("album") or album

    performer = (
        item.get("performer") or album_data.get("artist") or album.get("artist") or {}
    )
    if isinstance(performer, dict):
        artist = performer.get("name") or performer.get("title") or ""
    elif isinstance(performer, (list, tuple)):
        artist = ", ".join(
            str(entry.get("name", "")).strip()
            if isinstance(entry, dict)
            else str(entry).strip()
            for entry in performer
            if entry
        )
    else:
        artist = str(performer or "").strip()

    title = str(item.get("title") or "").strip()
    version = str(item.get("version") or "").strip()
    if version and version.casefold() not in title.casefold():
        title = f"{title} ({version})" if title else version

    if not title:
        # Fallback para o nome do arquivo, sem diretório, extensão ou prefixo de faixa.
        title = os.path.splitext(os.path.basename(file_path))[0]
        title = re.sub(r"^\d{1,3}\s*[-_. ]\s*", "", title)
    if not artist:
        artist = str(
            album_data.get("artist", "")
            if isinstance(album_data.get("artist"), str)
            else ""
        ).strip()

    display = f"{artist} - {title}" if artist else title
    display = re.sub(r"\s+", " ", display).strip()
    if len(display) > limit:
        display = display[: limit - 1].rstrip() + "…"
    return display or os.path.splitext(os.path.basename(file_path))[0][:limit]


def _cover_result_message(
    cover_source: Optional[str],
    cover_status: str,
) -> str:
    """Monta o detalhe de capa impresso em linha separada."""
    if cover_source:
        return f"Capa: {cover_source}; {cover_status}"
    return cover_status


async def retag_directory(directory_path, client, settings):
    """Reescreve tags e tenta substituir capas por versoes Apple/iTunes.

    A capa existente so e alterada quando uma imagem Apple valida e baixada.
    O terminal informa, para cada faixa, se a busca encontrou a capa, se o
    download da imagem falhou ou se nenhuma correspondencia foi aceita.
    """
    if not os.path.isdir(directory_path):
        ui.emit(f"{RED}[!] A pasta não existe no dispositivo: '{directory_path}'{OFF}")
        return None

    ui.emit(f"\n{CYAN}[*] Iniciando correção retroativa de tags...{OFF}")
    ui.emit(f"{CYAN} • Pasta raiz  :{RESET} {directory_path}")
    ui.emit(f"{CYAN} • Multi-tags  :{RESET} {multi_tags_label(settings)}")
    ui.emit(
        f"{CYAN} • Capas Apple :{RESET} procurando e substituindo quando validada\n"
    )

    files = find_audio_files(directory_path)
    stats = {
        "total": len(files),
        "updated": 0,
        "unchanged": 0,
        "no_id": 0,
        "not_found": 0,
        "errors": 0,
        "apple_embedded": 0,
        "apple_not_found": 0,
        "apple_failed": 0,
    }

    if not files:
        ui.emit(f"{YELLOW}[!] Nenhum arquivo .flac/.mp3 encontrado na pasta.{OFF}")
        return stats

    loop = asyncio.get_running_loop()
    album_cache: dict = {}
    width = len(str(len(files)))

    http_session = make_client(
        follow_redirects=True,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0 Safari/537.36"
            )
        },
    )

    try:
        for index, file_path in enumerate(files, 1):
            label = f"[{index:0{width}d}/{len(files)}]"
            name = os.path.basename(file_path)

            track_id = await loop.run_in_executor(
                None,
                extract_track_id,
                file_path,
            )

            if not track_id:
                stats["no_id"] += 1
                ui.skip(f"{label} {name} (sem ID Qobuz nas tags)")
                continue

            album_id = await loop.run_in_executor(
                None,
                extract_album_id,
                file_path,
            )

            try:
                resolved = await _resolve_metadata(
                    client,
                    track_id,
                    album_id,
                    album_cache,
                )
            except Exception as exc:
                stats["errors"] += 1
                ui.error(f"{label} {name} (erro ao consultar o Qobuz: {exc})")
                continue

            if not resolved:
                stats["not_found"] += 1
                ui.skip(f"{label} {name} (faixa {track_id} não encontrada no Qobuz)")
                continue

            item, album, istrack = resolved
            name = _display_track_name(item, album, file_path)
            extension = os.path.splitext(file_path)[1].lower()
            tag_function = (
                metadata.tag_mp3 if extension == ".mp3" else metadata.tag_flac
            )
            root_dir = os.path.dirname(file_path)

            cover_path: Optional[str] = None
            cover_source: Optional[str] = None
            cover_status = "Apple: não pesquisada"
            apple_diagnostic = None

            try:
                (
                    apple_cover_url,
                    apple_cover_source,
                    apple_diagnostic,
                ) = await _fetch_apple_cover_for_track(
                    item,
                    album,
                    http_session,
                )

                if not isinstance(apple_cover_url, str) or not apple_cover_url.strip():
                    cover_status = (
                        f"Apple: {apple_diagnostic}"
                        if apple_diagnostic
                        else "Apple: nenhum candidato passou pelos filtros"
                    )
                    stats["apple_not_found"] += 1

                else:
                    cover_path = await _download_apple_cover(
                        http_session,
                        apple_cover_url,
                        root_dir,
                    )

                    if cover_path:
                        cover_source = apple_cover_source or "Apple/iTunes"
                        size_kib = max(1, os.path.getsize(cover_path) // 1024)
                        cover_status = f"Apple: capa baixada ({size_kib} KiB)"
                    else:
                        cover_status = "Apple: URL encontrada, download falhou"
                        stats["apple_failed"] += 1

            except Exception as exc:
                cover_status = f"Apple: erro ({type(exc).__name__}: {exc})"
                stats["apple_failed"] += 1
                logger.warning(
                    "Falha ao obter capa Apple para '%s': %s",
                    file_path,
                    exc,
                    exc_info=True,
                )

            # Preserva a capa atual quando a candidata Apple não tem resolução
            # superior. A comparação é conservadora: em caso de dúvida, não troca.
            if cover_path and not await loop.run_in_executor(
                None,
                functools.partial(
                    metadata.should_replace_embedded_cover,
                    file_path,
                    cover_path,
                ),
            ):
                try:
                    os.remove(cover_path)
                except OSError:
                    pass
                cover_path = None
                cover_source = None
                cover_status = "Capa existente preservada (candidata não é superior)"

            before = await loop.run_in_executor(None, snapshot_tags, file_path)

            try:
                await loop.run_in_executor(
                    None,
                    functools.partial(
                        tag_function,
                        file_path,
                        root_dir,
                        file_path,
                        item,
                        album,
                        istrack,
                        bool(cover_path),
                        settings=settings,
                        embed_cover_path=cover_path,
                        cover_source=cover_source,
                    ),
                )

                after = await loop.run_in_executor(None, snapshot_tags, file_path)

            except Exception as exc:
                stats["errors"] += 1
                ui.error(f"{label} {name} (erro ao gravar tags: {exc})")
                logger.debug("Falha ao gravar tags", exc_info=True)
                continue

            finally:
                if cover_path:
                    try:
                        os.remove(cover_path)
                    except OSError as exc:
                        logger.debug(
                            "Falha ao remover capa temporária '%s': %s",
                            cover_path,
                            exc,
                        )

            if cover_source:
                stats["apple_embedded"] += 1

            result_message = _cover_result_message(cover_source, cover_status)
            # Os snapshots reais usam as chaves "text" e "art". Alguns
            # chamadores/testes podem fornecer diretamente o mapa de tags;
            # nesse caso, comparar .get("text") compara None com None e perde
            # alterações reais.
            before_text = before.get("text", before)
            after_text = after.get("text", after)
            tags_changed = before_text != after_text
            cover_changed = before.get("art", []) != after.get("art", [])

            if tags_changed and cover_changed:
                action = "TAGS + CAPA ALTERADAS"
                action_color = GREEN
            elif tags_changed:
                action = "SOMENTE TAGS ALTERADAS"
                action_color = CYAN
            elif cover_changed:
                action = "SOMENTE CAPA ALTERADA"
                action_color = YELLOW
            else:
                action = "NENHUMA ALTERAÇÃO"
                action_color = MUTED

            if tags_changed or cover_changed:
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1

            # Linha principal neutra: somente o estado da operação recebe cor.
            ui.emit(f"{label} {name} [{action_color}{action}{OFF}]")
            ui.emit(
                f"    {CYAN}↳ Tags:{OFF} "
                f"{GREEN if tags_changed else MUTED}{'alteradas' if tags_changed else 'sem alteração'}{OFF}"
            )
            ui.emit(
                f"    {CYAN}↳ Capa:{OFF} "
                f"{YELLOW if cover_changed else MUTED}{'substituída' if cover_changed else 'preservada/inalterada'}{OFF}"
                f" — {result_message}"
            )

        ui.emit("")
        ui.emit(f"{GREEN}[+] Correção de tags concluída.{OFF}")
        ui.emit(f"{CYAN} • Arquivos analisados       :{RESET} {stats['total']}")
        ui.emit(f"{CYAN} • Atualizados               :{RESET} {stats['updated']}")
        ui.emit(f"{CYAN} • Já corretos               :{RESET} {stats['unchanged']}")
        ui.emit(f"{CYAN} • Sem ID Qobuz              :{RESET} {stats['no_id']}")
        ui.emit(f"{CYAN} • Não encontrados           :{RESET} {stats['not_found']}")
        ui.emit(
            f"{CYAN} • Capas Apple embutidas     :{RESET} {stats['apple_embedded']}"
        )
        ui.emit(
            f"{CYAN} • Capas Apple não encontradas:{RESET} {stats['apple_not_found']}"
        )
        ui.emit(f"{CYAN} • Falhas na etapa Apple     :{RESET} {stats['apple_failed']}")
        ui.emit(f"{CYAN} • Erros                     :{RESET} {stats['errors']}")

        if stats["no_id"]:
            ui.emit(
                f"{YELLOW}[i] Arquivos sem ID Qobuz não podem ser corrigidos "
                f"automaticamente.{OFF}"
            )

        return stats

    finally:
        await http_session.aclose()
