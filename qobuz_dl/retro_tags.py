# ============================================================================
# retro_tags.py -- injecao/correcao RETROATIVA de tags de metadados.
# ============================================================================
# Reescreve as tags de arquivos ja baixados a partir dos metadados atuais do
# Qobuz. Quando a Apple/iTunes encontrar uma capa validada, ela e baixada
# temporariamente, embutida no arquivo e registrada no comentario como
# "Capa: Apple/iTunes". Se a Apple nao retornar uma capa, a arte ja embutida
# e preservada (junto com as linhas de capa do comentario).
#
# Modo "so capas" (covers_only=True / `tags --covers-only`): NAO regrava
# nenhuma tag; apenas troca a imagem embutida quando a Apple valida uma capa
# superior. Para cada arquivo, o log mostra o diff exato das tags alteradas.
# ============================================================================

import asyncio
import difflib
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


def _tag_lines(value) -> list:
    """Normaliza o valor de uma tag em linhas de texto (listas e quebras de linha)."""
    items = value if isinstance(value, (list, tuple)) else [value]
    lines: list = []
    for item in items:
        lines.extend(str(item).split("\n"))
    return lines


def _short(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def format_tag_diff(
    before_text,
    after_text,
    max_value: int = 100,
    max_lines: int = 60,
) -> list:
    """Linhas legíveis com o diff EXATO entre dois mapas de tags.

    "+ CHAVE: valor" (nova), "- CHAVE: valor" (removida) e, quando o valor
    mudou, "~ CHAVE: 'antes' → 'depois'". Valores com várias linhas (como o
    COMMENT) ou várias entradas mostram só as linhas removidas/adicionadas.
    """
    before_text = before_text if isinstance(before_text, dict) else {}
    after_text = after_text if isinstance(after_text, dict) else {}
    out: list = []

    for key in sorted(set(before_text) | set(after_text), key=str):
        in_before, in_after = key in before_text, key in after_text
        old = _tag_lines(before_text[key]) if in_before else []
        new = _tag_lines(after_text[key]) if in_after else []
        if old == new:
            continue

        if not in_before:
            out.append(f"{GREEN}+ {key}:{OFF} {_short(' | '.join(new), max_value)}")
        elif not in_after:
            out.append(f"{RED}- {key}:{OFF} {_short(' | '.join(old), max_value)}")
        elif len(old) == 1 and len(new) == 1:
            out.append(
                f"{YELLOW}~ {key}:{OFF} {_short(old[0], max_value)!r}"
                f" → {_short(new[0], max_value)!r}"
            )
        else:
            out.append(f"{YELLOW}~ {key}:{OFF}")
            matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
            for op, a0, a1, b0, b1 in matcher.get_opcodes():
                if op in ("replace", "delete"):
                    out.extend(
                        f"    {RED}- {_short(line, max_value)}{OFF}"
                        for line in old[a0:a1]
                    )
                if op in ("replace", "insert"):
                    out.extend(
                        f"    {GREEN}+ {_short(line, max_value)}{OFF}"
                        for line in new[b0:b1]
                    )

    if len(out) > max_lines:
        extra = len(out) - max_lines
        out = out[:max_lines] + [f"… (+{extra} linhas de diferença)"]
    return out


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


# Rótulos para o log (classify_release_type devolve estes valores).
_RELEASE_LABELS = {
    "single": "single",
    "ep": "EP",
    "album": "álbum",
    "live": "álbum ao vivo",
    "compilation": "coletânea",
}
# Quantas faixas de um álbum entram na votação por consenso.
_MAX_CONSENSUS_TRACKS = 40


def _classify_album(album: dict) -> str:
    """Tipo do lançamento pela regra do projeto (<=3 single, 4-7 EP, >7 álbum)."""
    from qobuz_dl.utils import classify_release_type

    tracks = (album.get("tracks") or {}).get("items") or []
    return classify_release_type(
        title=album.get("title"),
        version=album.get("version"),
        track_count=album.get("tracks_count") or len(tracks),
        duration_seconds=album.get("duration"),
        api_release_type=album.get("release_type") or album.get("product_type"),
    )


def _release_kind(album: dict) -> Optional[str]:
    """'single' ou 'full' (EP/álbum/ao vivo/coletânea); None se desconhecido.

    É o que separa a capa do single da capa do álbum onde a mesma faixa
    também aparece: são lançamentos diferentes na Qobuz (IDs diferentes) e,
    na Apple, "X - Single" é outro álbum.
    """
    if not isinstance(album, dict):
        return None
    kind = _classify_album(album)
    if kind == "single":
        return "single"
    if kind in _RELEASE_LABELS:
        return "full"
    return None


def _album_full_title(album: dict) -> str:
    title = (album.get("title") or "").strip()
    version = (album.get("version") or "").strip()
    if version and version.casefold() not in title.casefold():
        title = f"{title} ({version})"
    return title


async def _decide_album_cover(
    album: dict,
    http_session: httpx.AsyncClient,
    track_results: dict,
) -> dict:
    """Escolhe UMA capa Apple para o álbum Qobuz inteiro.

    1) Busca no nível do álbum (UPC e busca por álbum, com o tipo single/
       álbum respeitado).
    2) Sem resultado, consenso das faixas: cada faixa é pesquisada e a capa
       que a maioria das faixas encontrou vale para todas (a faixa que a Apple
       não localizou, ou localizou em outro lugar, herda a capa do álbum).
       Só vale se pelo menos 2 faixas concordam e nenhuma outra capa empata.
    3) Sem consenso (coletâneas, faixas espalhadas por vários álbuns): sem
       decisão por álbum, e cada faixa é avaliada sozinha.

    Devolve {"url", "source", "method", "note", "path", "used"}. Os resultados
    por faixa obtidos na votação ficam em `track_results` (não se pesquisa
    duas vezes).
    """
    from qobuz_dl.utils import get_apple_hq_cover

    decision = {
        "url": None,
        "source": None,
        "method": None,
        "note": None,
        "path": None,
        "used": False,
    }
    artist = (album.get("artist") or {}).get("name", "")
    reason = None

    try:
        result = await get_apple_hq_cover(
            session=http_session,
            upc=album.get("upc"),
            artist=artist,
            album=_album_full_title(album),
            release_kind=_release_kind(album),
            deep_album_search=True,
        )
        if isinstance(result, tuple) and result and isinstance(result[0], str):
            if result[0].strip():
                decision.update(
                    url=result[0],
                    source=(result[1] if len(result) > 1 else None) or "Apple/iTunes",
                    method="busca do álbum",
                )
                return decision
        if isinstance(result, tuple) and len(result) > 2:
            reason = result[2]
    except Exception as exc:
        reason = f"erro ({type(exc).__name__}: {exc})"
        logger.debug("Falha na busca de capa do álbum", exc_info=True)

    items = (album.get("tracks") or {}).get("items") or []
    if len(items) >= 2:
        votes: dict = {}
        for item in items[:_MAX_CONSENSUS_TRACKS]:
            try:
                found = await _fetch_apple_cover_for_track(item, album, http_session)
            except Exception as exc:
                found = (None, None, f"erro ({type(exc).__name__}: {exc})")
            track_results[str(item.get("id"))] = found
            if found and isinstance(found[0], str) and found[0].strip():
                votes[found[0]] = votes.get(found[0], 0) + 1

        ranked = sorted(votes.items(), key=lambda pair: -pair[1])
        if ranked and ranked[0][1] >= 2 and (
            len(ranked) == 1 or ranked[0][1] > ranked[1][1]
        ):
            decision.update(
                url=ranked[0][0],
                source="Apple/iTunes",
                method=f"consenso de {ranked[0][1]}/{len(items)} faixas",
            )
            return decision

    decision["note"] = reason
    return decision


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
        # Single so' casa com "X - Single"; album/EP nunca recebe capa de single.
        release_kind=_release_kind(album) or _release_kind(album_data),
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


def _discard_shared_cover(decision: dict) -> None:
    """Apaga o temporário da capa compartilhada de um álbum."""
    path = decision.get("path")
    decision["path"] = None
    if path:
        try:
            os.remove(path)
        except OSError as exc:
            logger.debug("Falha ao remover capa temporária '%s': %s", path, exc)


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


async def retag_directory(directory_path, client, settings, covers_only=False):
    """Reescreve tags e tenta substituir capas por versoes Apple/iTunes.

    A capa existente so e alterada quando uma imagem Apple valida e baixada.
    O terminal informa, para cada faixa, se a busca encontrou a capa, se o
    download da imagem falhou ou se nenhuma correspondencia foi aceita, e
    mostra o diff exato das tags que mudaram.

    covers_only=True: nao regrava tags; so troca a imagem embutida (e a origem
    da capa no comentario) quando a Apple tem uma capa validada e superior.
    """
    if not os.path.isdir(directory_path):
        ui.emit(f"{RED}[!] A pasta não existe no dispositivo: '{directory_path}'{OFF}")
        return None

    ui.emit(f"\n{CYAN}[*] Iniciando correção retroativa de tags...{OFF}")
    ui.emit(f"{CYAN} • Pasta raiz  :{RESET} {directory_path}")
    if covers_only:
        ui.emit(
            f"{CYAN} • Modo        :{RESET} SOMENTE CAPAS (nenhuma tag será regravada)"
        )
    else:
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
    # Capa decidida UMA vez por álbum Qobuz (chave = ID do álbum): single,
    # álbum, edição Deluxe e coletânea têm IDs diferentes e nunca se misturam.
    album_decisions: dict = {}
    track_results: dict = {}  # faixa -> (url, fonte, motivo), da votação
    shared_holder = {"decision": None}  # só a capa do álbum atual fica em disco
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
            shared_cover = False  # True: o arquivo pertence ao álbum, não a esta faixa

            try:
                album_key = str(album.get("id") or "")
                album_decision = None
                if album_key:
                    album_decision = album_decisions.get(album_key)
                    if album_decision is None:
                        album_decision = await _decide_album_cover(
                            album, http_session, track_results
                        )
                        album_decisions[album_key] = album_decision
                        if album_decision and album_decision.get("url"):
                            kind_label = _RELEASE_LABELS.get(
                                _classify_album(album), "álbum"
                            )
                            ui.emit(
                                f"{CYAN}[Álbum]{OFF} {album.get('title') or album_key}"
                                f" ({kind_label}): capa Apple única para todas as "
                                f"faixas -- {album_decision.get('method')}"
                            )

                if album_decision and album_decision.get("url"):
                    apple_cover_url = album_decision["url"]
                    apple_cover_source = album_decision.get("source")
                    apple_diagnostic = None
                else:
                    cached = track_results.get(str(item.get("id")))
                    if cached is None:
                        cached = await _fetch_apple_cover_for_track(
                            item,
                            album,
                            http_session,
                        )
                    (
                        apple_cover_url,
                        apple_cover_source,
                        apple_diagnostic,
                    ) = cached
                    album_decision = None

                if not isinstance(apple_cover_url, str) or not apple_cover_url.strip():
                    cover_status = (
                        f"Apple: {apple_diagnostic}"
                        if apple_diagnostic
                        else "Apple: nenhum candidato passou pelos filtros"
                    )
                    stats["apple_not_found"] += 1

                elif album_decision:
                    # Capa do álbum: baixada uma vez e reaproveitada pelas
                    # demais faixas (só a do álbum atual fica em disco).
                    path = album_decision.get("path")
                    reused = bool(path and os.path.isfile(path))
                    if not reused:
                        previous = shared_holder["decision"]
                        if previous is not None and previous is not album_decision:
                            _discard_shared_cover(previous)
                        path = await _download_apple_cover(
                            http_session, apple_cover_url, root_dir
                        )
                        album_decision["path"] = path
                        shared_holder["decision"] = album_decision if path else None
                    if path:
                        cover_path = path
                        shared_cover = True
                        cover_source = apple_cover_source or "Apple/iTunes"
                        size_kib = max(1, os.path.getsize(path) // 1024)
                        cover_status = (
                            f"Apple: capa do álbum reaproveitada ({size_kib} KiB)"
                            if reused
                            else f"Apple: capa do álbum baixada ({size_kib} KiB)"
                        )
                    else:
                        cover_status = "Apple: URL encontrada, download falhou"
                        stats["apple_failed"] += 1

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
                if not shared_cover:
                    try:
                        os.remove(cover_path)
                    except OSError:
                        pass
                cover_path = None
                cover_source = None
                cover_status = "Capa existente preservada (candidata não é superior)"

            before = await loop.run_in_executor(None, snapshot_tags, file_path)

            try:
                if covers_only:
                    # Modo "so capas": nenhuma tag e regravada; so a imagem
                    # (e a origem da capa no comentario) quando ha capa nova.
                    if cover_path:
                        embedded = await loop.run_in_executor(
                            None,
                            functools.partial(
                                metadata.replace_cover_only,
                                file_path,
                                cover_path,
                                cover_source=cover_source,
                                settings=settings,
                            ),
                        )
                        if not embedded:
                            cover_source = None
                            cover_status = "Apple: capa baixada, mas não foi possível embutir"
                            stats["apple_failed"] += 1
                else:
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
                            # Sem capa nova, mantem no comentario as linhas de
                            # capa que ja estavam la (senao o retag as apagava).
                            preserve_cover_info=True,
                        ),
                    )

                after = await loop.run_in_executor(None, snapshot_tags, file_path)

            except Exception as exc:
                stats["errors"] += 1
                ui.error(f"{label} {name} (erro ao gravar tags: {exc})")
                logger.debug("Falha ao gravar tags", exc_info=True)
                continue

            finally:
                if cover_path and not shared_cover:
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
            # No modo "so capas" as tags nao sao tocadas: a unica diferenca
            # possivel no COMMENT e a origem/tamanho da capa nova.
            tags_changed = False if covers_only else before_text != after_text
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
            if covers_only:
                ui.emit(f"    {CYAN}↳ Tags:{OFF} {MUTED}não tocadas (modo só capas){OFF}")
            else:
                ui.emit(
                    f"    {CYAN}↳ Tags:{OFF} "
                    f"{GREEN if tags_changed else MUTED}{'alteradas' if tags_changed else 'sem alteração'}{OFF}"
                )
                if tags_changed:
                    for diff_line in format_tag_diff(before_text, after_text):
                        ui.emit(f"        {diff_line}")
            ui.emit(
                f"    {CYAN}↳ Capa:{OFF} "
                f"{YELLOW if cover_changed else MUTED}{'substituída' if cover_changed else 'preservada/inalterada'}{OFF}"
                f" -- {result_message}"
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
        if shared_holder["decision"] is not None:
            _discard_shared_cover(shared_holder["decision"])
        await http_session.aclose()
