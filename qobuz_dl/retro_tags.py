# ============================================================================
# retro_tags.py -- injecao/correcao RETROATIVA de tags de metadados.
# ============================================================================
# Reescreve as tags de arquivos ja baixados a partir dos metadados atuais do
# Qobuz. Quando a Apple/iTunes encontrar uma capa validada, ela e baixada
# temporariamente, embutida no arquivo e registrada no comentario como
# "Capa: Apple/iTunes". Se a Apple nao retornar uma capa, a arte ja embutida
# e preservada (junto com as linhas de capa do comentario).
#
# Tres modos (`tags`, `tags --tags-only`, `tags --covers-only`):
#   - padrao / ambos : regrava as tags E troca a capa quando a Apple valida.
#   - so tags        : (tags_only=True) regrava as tags; NAO consulta a Apple e
#                      mantem a capa embutida como esta.
#   - so capas       : (covers_only=True) NAO regrava nenhuma tag; apenas troca
#                      a imagem embutida quando a Apple valida uma capa superior.
# Para cada arquivo, o log mostra o diff exato das tags alteradas.
# ============================================================================

import asyncio
import collections
import difflib
import logging
import os
import re
import shutil
import tempfile
import textwrap
from typing import Optional
import functools

import httpx
import mutagen.id3 as id3
from mutagen.flac import FLAC

from qobuz_dl import metadata, ui
from qobuz_dl.http_download import make_client
from qobuz_dl.color import GRAY, GREEN, OFF, RED, RESET
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import WARNING as YELLOW

# Significado fixo das cores no log do `tags`:
#   verde    = alteração aplicada
#   amarelo  = atenção (algo ficou pendente ou foi removido)
#   cinza    = nada a alterar
#   vermelho = erro
# (MUTED aqui era um apelido de WARNING, ou seja, AMARELO: "sem alteração" e
# "capa substituída" saíam da mesma cor. Agora o cinza é um cinza de verdade.)
MUTED = GRAY
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

    Cores: verde = tag nova/alterada; amarelo = tag removida (merece atenção).

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
            out.append(f"{YELLOW}- {key}:{OFF} {_short(' | '.join(old), max_value)}")
        elif len(old) == 1 and len(new) == 1:
            out.append(
                f"{GREEN}~ {key}:{OFF} {_short(old[0], max_value)!r}"
                f" → {_short(new[0], max_value)!r}"
            )
        else:
            out.append(f"{GREEN}~ {key}:{OFF}")
            matcher = difflib.SequenceMatcher(None, old, new, autojunk=False)
            for op, a0, a1, b0, b1 in matcher.get_opcodes():
                if op in ("replace", "delete"):
                    out.extend(
                        f"    {YELLOW}- {_short(line, max_value)}{OFF}"
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
    trace: Optional[list] = None,
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
        items_for_check = (album.get("tracks") or {}).get("items") or []
        result = await get_apple_hq_cover(
            session=http_session,
            upc=album.get("upc"),
            artist=artist,
            album=_album_full_title(album),
            release_kind=_release_kind(album),
            deep_album_search=True,
            # A versão certa do álbum é confirmada pela lista de faixas (single
            # x álbum, Deluxe x padrão, versão estendida x normal) e a busca
            # segue por outras lojas se as principais não tiverem o lançamento.
            album_tracks=[_qobuz_track_title(i) for i in items_for_check] or None,
            fallback_countries=True,
            **({"trace": trace} if trace is not None else {}),
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
                found = await _fetch_apple_cover_for_track(
                    item,
                    album,
                    http_session,
                    **({"trace": trace} if trace is not None else {}),
                )
            except Exception as exc:
                found = (None, None, f"erro ({type(exc).__name__}: {exc})")
            track_results[str(item.get("id"))] = found
            if found and isinstance(found[0], str) and found[0].strip():
                votes[found[0]] = votes.get(found[0], 0) + 1

        ranked = sorted(votes.items(), key=lambda pair: -pair[1])
        if (
            ranked
            and ranked[0][1] >= 2
            and (len(ranked) == 1 or ranked[0][1] > ranked[1][1])
        ):
            decision.update(
                url=ranked[0][0],
                source="Apple/iTunes",
                method=f"consenso de {ranked[0][1]}/{len(items)} faixas",
            )
            return decision

    decision["note"] = reason
    return decision


def _qobuz_track_title(item: dict) -> str:
    """Título da faixa na Qobuz juntando "title" e "version".

    A Qobuz separa os dois (ex.: "Fat Juicy &…" + "Radio Edit"), mas a Apple
    junta tudo no nome da faixa. Sem a versão o título pedido nunca bate.
    """
    title = (item.get("title") or "").strip()
    version = (item.get("version") or "").strip()
    if version and version.casefold() not in title.casefold():
        title = f"{title} ({version})"
    return title


async def _fetch_apple_cover_for_track(
    item: dict,
    album: dict,
    http_session: httpx.AsyncClient,
    trace: Optional[list] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Busca URL, fonte e diagnóstico da busca Apple."""
    from qobuz_dl.utils import get_apple_hq_cover

    album_data = item.get("album") or album or {}
    artist_data = (
        item.get("performer") or album_data.get("artist") or album.get("artist") or {}
    )

    track_title = _qobuz_track_title(item)

    result = await get_apple_hq_cover(
        session=http_session,
        upc=album_data.get("upc") or album.get("upc"),
        isrc=item.get("isrc"),
        artist=artist_data.get("name", ""),
        album=album_data.get("title") or album.get("title", ""),
        track_title=track_title,
        # Single so' casa com "X - Single"; album/EP nunca recebe capa de single.
        release_kind=_release_kind(album) or _release_kind(album_data),
        **({"trace": trace} if trace is not None else {}),
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


# ---------------------------------------------------------------------------
# Formatação da saída (cores com significado fixo, blocos espaçados)
# ---------------------------------------------------------------------------
# Desfechos da capa que pedem atenção do usuário (ficam em amarelo).
_COVER_ATTENTION = {
    "not_found",
    "download_failed",
    "embed_failed",
    "error",
    "no_compare",
}


def _compare_covers(file_path, cover_path):
    """(deve_trocar, motivo) -- o motivo vem da MESMA thread da comparação."""
    decision = metadata.should_replace_embedded_cover(file_path, cover_path)
    reason_getter = getattr(metadata, "last_cover_compare_reason", None)
    return decision, (reason_getter() if reason_getter else None)


def _term_width() -> int:
    try:
        columns = shutil.get_terminal_size((100, 24)).columns
    except Exception:
        columns = 100
    return max(60, min(columns, 120))


def _wrap_lines(text: str, indent: int, width: Optional[int] = None) -> list:
    """Quebra o texto em linhas que cabem no terminal, com recuo fixo."""
    width = width or _term_width()
    prefix = " " * indent
    return textwrap.wrap(
        text,
        width=width - 1,
        initial_indent=prefix,
        subsequent_indent=prefix,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [prefix]


def _labeled_lines(label: str, text: str, width: int) -> list:
    """Linha "   Rótulo  texto" com continuação alinhada sob o texto."""
    return textwrap.wrap(
        text,
        width=width - 1,
        initial_indent=f"   {label:<6} ",
        subsequent_indent=" " * 10,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [f"   {label}"]


def split_apple_diagnostic(text: str) -> list:
    """Separa o diagnóstico da Apple em [(rótulo, texto)].

    O motivo vem como uma frase só ("...; etapa do álbum: ...; exemplos: ...");
    em linhas separadas fica legível, em vez de uma parede de texto.
    """
    parts = re.split(
        r";\s+(?=(?:etapa do álbum|erros de rede|exemplos):\s)", text or ""
    )
    sections = [("Motivo", parts[0].strip())]
    for part in parts[1:]:
        label, _, rest = part.partition(": ")
        sections.append((label.strip().capitalize(), rest.strip()))
    return [(label, body) for label, body in sections if body]


def count_changed_tags(before_text, after_text) -> int:
    """Quantas tags (chaves) têm valor diferente entre os dois mapas."""
    before_text = before_text if isinstance(before_text, dict) else {}
    after_text = after_text if isinstance(after_text, dict) else {}
    return sum(
        1
        for key in set(before_text) | set(after_text)
        if _tag_lines(before_text.get(key, [])) != _tag_lines(after_text.get(key, []))
    )


def changed_tag_keys(before_text, after_text) -> list:
    """Chaves de tags cujo valor mudou (nova, removida ou alterada), em ordem."""
    before_text = before_text if isinstance(before_text, dict) else {}
    after_text = after_text if isinstance(after_text, dict) else {}
    return [
        key
        for key in sorted(set(before_text) | set(after_text), key=str)
        if _tag_lines(before_text.get(key, [])) != _tag_lines(after_text.get(key, []))
    ]


def _file_state(tags_changed: bool, cover_changed: bool, attention: bool):
    """(cor, texto) do resultado do arquivo: verde / amarelo / cinza."""
    if tags_changed and cover_changed:
        what = "tags e capa"
    elif tags_changed:
        what = "somente as tags"
    elif cover_changed:
        what = "somente a capa"
    else:
        what = None

    if what and attention:
        return YELLOW, f"Alterado ({what}), mas ficou uma pendência na capa"
    if what:
        return GREEN, f"Alterado -- {what}"
    return MUTED, "Sem alterações"


def _cover_line(outcome: str, cover_changed: bool, cover_source: Optional[str]):
    """(cor, texto) da linha de capa."""
    if outcome == "replaced":
        if cover_changed:
            return GREEN, f"substituída pela capa da {cover_source or 'Apple/iTunes'}"
        return MUTED, "regravada, mas a imagem final é igual à anterior"
    if outcome == "kept":
        return (
            MUTED,
            "mantida -- a capa atual já tem resolução igual ou maior que a da Apple",
        )
    if outcome == "kept_shape":
        return (
            MUTED,
            "mantida -- a capa da Apple não é quadrada (a atual é)",
        )
    if outcome == "no_compare":
        return (
            YELLOW,
            "mantida -- não deu para comparar as resoluções (imagem ilegível)",
        )
    if outcome == "not_found":
        return YELLOW, "não encontrada na Apple -- a capa atual foi mantida"
    if outcome == "download_failed":
        return (
            YELLOW,
            "encontrada na Apple, mas o download falhou -- a capa atual foi mantida",
        )
    if outcome == "embed_failed":
        return (
            YELLOW,
            "baixada da Apple, mas não deu para embutir -- a capa atual foi mantida",
        )
    if outcome == "error":
        return YELLOW, "erro na etapa da Apple -- a capa atual foi mantida"
    return MUTED, "não pesquisada"


def _cover_detail_lines(outcome: str, cover_source, detail: str, width: int) -> list:
    """Linhas de explicação (cinza) abaixo da linha de capa."""
    indent = 10
    lines = []
    if outcome == "replaced":
        text = f"Fonte: {cover_source or 'Apple/iTunes'} | {detail}"
        lines.extend(_wrap_lines(text, indent, width))
    elif outcome == "not_found":
        for label, body in split_apple_diagnostic(detail):
            if label == "Exemplos":
                lines.extend(_wrap_lines("Exemplos:", indent, width))
                for example in body.split(" | "):
                    wrapped = _wrap_lines(example, indent + 4, width)
                    wrapped[0] = " " * (indent + 2) + "• " + wrapped[0].lstrip()
                    lines.extend(wrapped)
            else:
                lines.extend(_wrap_lines(f"{label}: {body}", indent, width))
    elif (
        outcome in ("download_failed", "embed_failed", "error", "no_compare") and detail
    ):
        lines.extend(_wrap_lines(detail, indent, width))
    return [f"{MUTED}{line}{OFF}" for line in lines]


def _album_header_lines(album: dict, decision, width: int) -> list:
    """Cabeçalho do álbum (uma vez por álbum) e como a capa dele foi decidida."""
    title = (album.get("title") or "").strip()
    if not title:
        return []
    artist = ((album.get("artist") or {}).get("name") or "").strip()
    items = (album.get("tracks") or {}).get("items") or []
    count = album.get("tracks_count") or len(items)
    meta = " | ".join(
        part
        for part in (
            _RELEASE_LABELS.get(_classify_album(album)),
            f"{count} faixa{'s' if count != 1 else ''}" if count else None,
        )
        if part
    )
    head = f"== {title}" + (f" -- {artist}" if artist else "")
    if meta:
        head += f"  |  {meta}"
    head = _short(head, width - 6) + " "
    lines = [f"{CYAN}{head}{'=' * max(3, width - len(head) - 1)}{OFF}"]
    if decision and decision.get("url"):
        lines.append(
            f"{CYAN}   Capa Apple única para todas as faixas "
            f"({decision.get('method')}){OFF}"
        )
    elif decision is not None:
        lines.append(
            f"{MUTED}   Sem capa única para o álbum: cada faixa é avaliada "
            f"separadamente{OFF}"
        )
    return lines


def _legend_lines() -> list:
    return [
        f"{CYAN} Legenda das cores:{RESET}",
        f"   {GREEN}•{OFF} verde     alteração aplicada (tags e/ou capa)",
        f"   {YELLOW}•{OFF} amarelo   atenção: algo ficou pendente (ex.: capa não "
        f"achada) ou tag removida",
        f"   {MUTED}•{OFF} cinza     nada a alterar",
        f"   {RED}•{OFF} vermelho  erro",
    ]


_COVER_OUTCOME_LABELS = (
    ("replaced", "capa trocada", GREEN),
    ("kept", "mantida (a atual já era igual ou maior)", MUTED),
    ("kept_shape", "mantida (a da Apple não é quadrada)", MUTED),
    ("no_compare", "mantida (não deu para comparar resoluções)", YELLOW),
    ("not_found", "não encontrada na Apple", YELLOW),
    ("download_failed", "achada, mas o download falhou", YELLOW),
    ("embed_failed", "baixada, mas não deu para embutir", YELLOW),
    ("error", "erro na etapa da Apple", YELLOW),
)


def _summary_detail_lines(stats: dict, tags_only: bool, dry_run: bool, top: int = 10):
    """Linhas extras do resumo: campos mais alterados e destino das capas."""
    lines = []
    fields = stats.get("field_changes") or {}
    if fields:
        ranked = sorted(fields.items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append("")
        lines.append(
            f"{CYAN} Campos {'que seriam alterados' if dry_run else 'alterados'}"
            f" (faixas por campo):{RESET}"
        )
        for key, count in ranked[:top]:
            lines.append(f"{CYAN} • {str(key).ljust(26)}:{RESET} {GREEN}{count}{OFF}")
        if len(ranked) > top:
            lines.append(f"{MUTED}   … e mais {len(ranked) - top} campo(s){OFF}")
    outcomes = stats.get("cover_outcomes") or {}
    if outcomes and not tags_only:
        lines.append("")
        lines.append(f"{CYAN} Capas (o que houve em cada faixa):{RESET}")
        for outcome, text, color in _COVER_OUTCOME_LABELS:
            count = outcomes.get(outcome, 0)
            if count:
                lines.append(f"{CYAN} • {text.ljust(44)}:{RESET} {color}{count}{OFF}")
    return lines


def resolve_mode(covers_only: bool = False, tags_only: bool = False) -> str:
    """'both' (padrão), 'tags' (só tags) ou 'covers' (só capas)."""
    if covers_only and tags_only:
        raise ValueError("covers_only e tags_only são excludentes")
    return "covers" if covers_only else "tags" if tags_only else "both"


async def retag_directory(
    directory_path,
    client,
    settings,
    covers_only=False,
    tags_only=False,
    dry_run=False,
    journal=None,
    progress=None,
):
    """Reescreve tags e tenta substituir capas por versoes Apple/iTunes.

    A capa existente so e alterada quando uma imagem Apple valida e baixada.
    O terminal informa, para cada faixa, se a busca encontrou a capa, se o
    download da imagem falhou ou se nenhuma correspondencia foi aceita, e
    mostra o diff exato das tags que mudaram.

    covers_only=True: nao regrava tags; so troca a imagem embutida (e a origem
    da capa no comentario) quando a Apple tem uma capa validada e superior.
    tags_only=True: regrava as tags e NAO consulta a Apple; a capa embutida e
    as linhas de capa do comentario ficam como estao.
    Os dois juntos nao fazem sentido (ValueError); nenhum dos dois = ambos.

    dry_run=True: SIMULA. Faz tudo (consulta o Qobuz e a Apple) mas grava numa
    COPIA temporaria de cada arquivo e mostra o diff real; a biblioteca nao e
    tocada. journal: `tags_journal.Journal` que guarda os metadados originais
    antes de cada gravacao (para `tags --undo`); None = sem registro.

    progress: `tags_progress.Progress`. Com ``enable_skip()`` ligado, arquivos
    ja processados (e inalterados desde entao) sao pulados; cada arquivo
    concluido e marcado para `--resume`/`--incremental`. Na simulacao o
    progresso so e lido, nunca gravado.
    """
    mode = resolve_mode(covers_only, tags_only)
    covers_only, tags_only = mode == "covers", mode == "tags"

    if not os.path.isdir(directory_path):
        ui.emit(f"{RED}[!] A pasta não existe no dispositivo: '{directory_path}'{OFF}")
        return None

    ui.emit(f"\n{CYAN}[*] Iniciando correção retroativa de tags...{OFF}")
    ui.emit(f"{CYAN} • Pasta raiz  :{RESET} {directory_path}")
    if dry_run:
        ui.emit(
            f"{YELLOW} • SIMULAÇÃO   :{RESET} nada será gravado na sua pasta "
            "(as alterações abaixo são o que SERIA feito)"
        )
    if covers_only:
        ui.emit(
            f"{CYAN} • Modo        :{RESET} SOMENTE CAPAS (nenhuma tag será regravada)"
        )
    else:
        if tags_only:
            ui.emit(
                f"{CYAN} • Modo        :{RESET} SOMENTE TAGS "
                "(a capa não é pesquisada nem alterada)"
            )
        ui.emit(f"{CYAN} • Multi-tags  :{RESET} {multi_tags_label(settings)}")
    if tags_only:
        ui.emit(f"{CYAN} • Capas Apple :{RESET} desligadas (modo só tags)")
    else:
        ui.emit(
            f"{CYAN} • Capas Apple :{RESET} procurando e substituindo quando validada"
        )
    ui.emit("")
    for legend_line in _legend_lines():
        ui.emit(legend_line)

    all_files = find_audio_files(directory_path)
    files = [f for f in all_files if not (progress and progress.is_done(f))]
    stats = {
        "total": len(all_files),
        "skipped_done": len(all_files) - len(files),
        "updated": 0,
        "unchanged": 0,
        "no_id": 0,
        "not_found": 0,
        "errors": 0,
        "apple_embedded": 0,
        "apple_not_found": 0,
        "apple_failed": 0,
        # Para o resumo final: quais campos mais mudaram e o que houve com as capas.
        "field_changes": collections.Counter(),
        "cover_outcomes": collections.Counter(),
    }

    if not all_files:
        ui.emit(f"{YELLOW}[!] Nenhum arquivo .flac/.mp3 encontrado na pasta.{OFF}")
        return stats

    if stats["skipped_done"]:
        ui.emit(
            f"{CYAN}[i] {stats['skipped_done']} arquivo(s) já processados e sem "
            f"alteração serão pulados (rode sem --incremental/--resume para "
            f"refazer tudo).{OFF}"
        )
    if not files:
        ui.emit(
            f"{GREEN}[+] Nada a fazer: todos os arquivos já foram processados.{OFF}"
        )
        if progress and not dry_run:
            progress.start(keep_done=True)
            progress.finish()
        return stats

    loop = asyncio.get_running_loop()
    album_cache: dict = {}
    # Capa decidida UMA vez por álbum Qobuz (chave = ID do álbum): single,
    # álbum, edição Deluxe e coletânea têm IDs diferentes e nunca se misturam.
    album_decisions: dict = {}
    track_results: dict = {}  # faixa -> (url, fonte, motivo), da votação
    shared_holder = {"decision": None}  # só a capa do álbum atual fica em disco
    last_album_key = None  # para imprimir o cabeçalho do álbum uma vez por álbum
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

    dry_workdir = tempfile.mkdtemp(prefix="qdl_dry_") if dry_run else None
    track_progress = progress if (progress and not dry_run) else None
    if track_progress:
        # Execução completa zera a lista; retomar/incremental mantém o que já foi feito.
        track_progress.start(keep_done=track_progress._skip_enabled)
    completed = False

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
                ui.emit("")
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
                ui.emit("")
                ui.error(f"{label} {name} (erro ao consultar o Qobuz: {exc})")
                continue

            if not resolved:
                stats["not_found"] += 1
                ui.emit("")
                ui.skip(f"{label} {name} (faixa {track_id} não encontrada no Qobuz)")
                continue

            item, album, istrack = resolved
            name = _display_track_name(item, album, file_path)
            extension = os.path.splitext(file_path)[1].lower()
            tag_function = (
                metadata.tag_mp3 if extension == ".mp3" else metadata.tag_flac
            )
            root_dir = os.path.dirname(file_path)
            # Na simulação nem a capa temporária da Apple toca a pasta do usuário.
            dl_dir = dry_workdir if dry_run else root_dir

            cover_path: Optional[str] = None
            cover_source: Optional[str] = None
            # cover_outcome: replaced | kept | no_compare | not_found | download_failed |
            # embed_failed | error | not_searched (define cor e texto no log);
            # cover_status: detalhe em texto, mostrado abaixo da linha de capa.
            cover_outcome = "not_searched"
            cover_status = "não pesquisada"
            apple_diagnostic = None
            shared_cover = False  # True: o arquivo pertence ao álbum, não a esta faixa
            album_key = str(album.get("id") or "")

            apple_cover_url = apple_cover_source = apple_diagnostic = None
            try:
                album_decision = None
                if album_key and not tags_only:
                    album_decision = album_decisions.get(album_key)
                    if album_decision is None:
                        album_decision = await _decide_album_cover(
                            album, http_session, track_results
                        )
                        album_decisions[album_key] = album_decision

                if album_decision and album_decision.get("url"):
                    apple_cover_url = album_decision["url"]
                    apple_cover_source = album_decision.get("source")
                    apple_diagnostic = None
                elif not tags_only:
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

                if tags_only:
                    # Modo só tags: a Apple não é consultada e a capa fica como está.
                    cover_status = "não pesquisada (modo só tags)"

                elif (
                    not isinstance(apple_cover_url, str) or not apple_cover_url.strip()
                ):
                    cover_outcome = "not_found"
                    cover_status = (
                        apple_diagnostic or "nenhum candidato passou pelos filtros"
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
                            http_session, apple_cover_url, dl_dir
                        )
                        album_decision["path"] = path
                        shared_holder["decision"] = album_decision if path else None
                    if path:
                        cover_path = path
                        shared_cover = True
                        cover_source = apple_cover_source or "Apple/iTunes"
                        size_kib = max(1, os.path.getsize(path) // 1024)
                        cover_status = (
                            f"capa do álbum reaproveitada ({size_kib} KiB)"
                            if reused
                            else f"capa do álbum baixada ({size_kib} KiB)"
                        )
                    else:
                        cover_outcome = "download_failed"
                        cover_status = (
                            "a imagem encontrada na Apple não pôde ser baixada"
                        )
                        stats["apple_failed"] += 1

                else:
                    cover_path = await _download_apple_cover(
                        http_session,
                        apple_cover_url,
                        dl_dir,
                    )

                    if cover_path:
                        cover_source = apple_cover_source or "Apple/iTunes"
                        size_kib = max(1, os.path.getsize(cover_path) // 1024)
                        cover_status = f"capa baixada ({size_kib} KiB)"
                    else:
                        cover_outcome = "download_failed"
                        cover_status = (
                            "a imagem encontrada na Apple não pôde ser baixada"
                        )
                        stats["apple_failed"] += 1

            except Exception as exc:
                cover_outcome = "error"
                cover_status = f"{type(exc).__name__}: {exc}"
                stats["apple_failed"] += 1
                logger.warning(
                    "Falha ao obter capa Apple para '%s': %s",
                    file_path,
                    exc,
                    exc_info=True,
                )

            # Preserva a capa atual quando a candidata Apple não tem resolução
            # superior. A comparação é conservadora: em caso de dúvida, não troca.
            if cover_path:
                should_replace, compare_reason = await loop.run_in_executor(
                    None,
                    functools.partial(_compare_covers, file_path, cover_path),
                )
            else:
                should_replace, compare_reason = True, None
            if cover_path and not should_replace:
                if not shared_cover:
                    try:
                        os.remove(cover_path)
                    except OSError:
                        pass
                cover_path = None
                cover_source = None
                if compare_reason == "not_square":
                    cover_outcome = "kept_shape"
                    cover_status = (
                        "A capa da Apple não é quadrada e a atual é -- a atual "
                        "foi preservada"
                    )
                elif compare_reason == "unreadable":
                    cover_outcome = "no_compare"
                    cover_status = (
                        "Não foi possível ler as dimensões da capa (da Apple ou da "
                        "atual) -- por segurança a capa atual foi preservada"
                    )
                else:
                    cover_outcome = "kept"
                    cover_status = (
                        "Capa existente preservada (candidata não é superior)"
                    )

            before = await loop.run_in_executor(None, snapshot_tags, file_path)

            work_path = file_path  # arquivo que de fato recebe a gravação
            try:
                if dry_run:
                    # Simulação: grava numa cópia e compara com o original.
                    work_path = os.path.join(dry_workdir, os.path.basename(file_path))
                    await loop.run_in_executor(None, shutil.copy2, file_path, work_path)
                elif journal is not None:
                    try:
                        await loop.run_in_executor(None, journal.begin, file_path)
                    except Exception as exc:
                        raise RuntimeError(
                            "não foi possível guardar o backup para desfazer "
                            f"({exc}); o arquivo NÃO foi alterado"
                        ) from exc

                if covers_only:
                    # Modo "so capas": nenhuma tag e regravada; so a imagem
                    # (e a origem da capa no comentario) quando ha capa nova.
                    if cover_path:
                        embedded = await loop.run_in_executor(
                            None,
                            functools.partial(
                                metadata.replace_cover_only,
                                work_path,
                                cover_path,
                                cover_source=cover_source,
                                settings=settings,
                            ),
                        )
                        if not embedded:
                            cover_source = None
                            cover_outcome = "embed_failed"
                            cover_status = "a gravação da imagem no arquivo falhou"
                            stats["apple_failed"] += 1
                else:
                    await loop.run_in_executor(
                        None,
                        functools.partial(
                            tag_function,
                            work_path,
                            root_dir,
                            work_path,
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

                after = await loop.run_in_executor(None, snapshot_tags, work_path)

            except Exception as exc:
                stats["errors"] += 1
                ui.error(f"{label} {name} (erro ao gravar tags: {exc})")
                logger.debug("Falha ao gravar tags", exc_info=True)
                continue

            finally:
                if journal is not None and not dry_run:
                    try:
                        await loop.run_in_executor(None, journal.finish, file_path)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug(
                            "Falha ao fechar o registro de %s: %s", file_path, exc
                        )
                if dry_run and work_path != file_path:
                    try:
                        os.remove(work_path)
                    except OSError:
                        pass
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
                cover_outcome = "replaced"

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

            if tags_changed or cover_changed:
                stats["updated"] += 1
            else:
                stats["unchanged"] += 1
            if tags_changed:
                for changed_key in changed_tag_keys(before_text, after_text):
                    stats["field_changes"][changed_key] += 1
            if not tags_only:
                stats["cover_outcomes"][cover_outcome] += 1

            columns = _term_width()
            state_color, state_text = _file_state(
                tags_changed, cover_changed, cover_outcome in _COVER_ATTENTION
            )
            if dry_run:
                state_text += " [simulação]"

            # Cabeçalho do álbum: uma vez, quando o álbum muda.
            if album_key and album_key != last_album_key:
                header = _album_header_lines(
                    album, album_decisions.get(album_key), columns
                )
                if header:
                    ui.emit("")
                    for header_line in header:
                        ui.emit(header_line)
                last_album_key = album_key

            # Um bloco por faixa, separado por linha em branco. Cor: verde =
            # alterou, amarelo = atenção, cinza = nada a alterar.
            ui.emit("")
            ui.emit(f"{state_color}{label} {name}{OFF}")
            ui.emit(f"   {state_color}• {state_text}{OFF}")

            if covers_only:
                tags_color, tags_text = MUTED, "não tocadas (modo só capas)"
            elif tags_changed:
                changed = count_changed_tags(before_text, after_text)
                tags_color = GREEN
                tags_text = (
                    f"alteradas ({changed} campo{'s' if changed != 1 else ''})"
                    if changed
                    else "alteradas"
                )
            else:
                tags_color, tags_text = MUTED, "já estavam corretas"
            for line in _labeled_lines("Tags", tags_text, columns):
                ui.emit(f"{tags_color}{line}{OFF}")
            if tags_changed and not covers_only:
                for diff_line in format_tag_diff(before_text, after_text):
                    ui.emit(f"          {diff_line}")

            if tags_only:
                cover_color, cover_text = MUTED, "não tocada (modo só tags)"
            else:
                cover_color, cover_text = _cover_line(
                    cover_outcome, cover_changed, cover_source
                )
            for line in _labeled_lines("Capa", cover_text, columns):
                ui.emit(f"{cover_color}{line}{OFF}")
            for line in _cover_detail_lines(
                cover_outcome, cover_source, cover_status, columns
            ):
                ui.emit(line)

            if track_progress:
                track_progress.mark(file_path, None if tags_only else cover_outcome)

        completed = True
        if track_progress:
            track_progress.finish()

        ui.emit("")
        if dry_run:
            ui.emit(
                f"{GREEN}[+] Simulação concluída -- nenhum arquivo foi alterado.{OFF}"
            )
        else:
            ui.emit(f"{GREEN}[+] Correção de tags concluída.{OFF}")
        ui.emit("")
        ui.emit(f"{CYAN} Resumo{' da simulação' if dry_run else ''}:{RESET}")
        for text_label, key, color_on, explanation in (
            (
                "Arquivos analisados",
                "total",
                None,
                "faixas .flac/.mp3 encontradas na pasta",
            ),
            (
                "Seriam atualizados" if dry_run else "Atualizados",
                "updated",
                GREEN,
                "faixas com tags e/ou capa alteradas",
            ),
            ("Já corretos", "unchanged", None, "faixas que já estavam como deveriam"),
            (
                "Pulados (já feitos)",
                "skipped_done",
                None,
                "processados antes e sem alteração (--incremental/--resume)",
            ),
            (
                "Sem ID Qobuz",
                "no_id",
                YELLOW,
                "sem ID nas tags: não dá para corrigir sozinho",
            ),
            ("Não encontrados", "not_found", YELLOW, "ID não encontrado na Qobuz"),
            (
                "Capas que seriam trocadas" if dry_run else "Capas Apple embutidas",
                "apple_embedded",
                GREEN,
                "faixas que receberiam a capa da Apple"
                if dry_run
                else "faixas que receberam a capa da Apple",
            ),
            (
                "Capas Apple não encontradas",
                "apple_not_found",
                YELLOW,
                "sem capa válida na Apple (capa atual mantida)",
            ),
            (
                "Falhas na etapa Apple",
                "apple_failed",
                YELLOW,
                "capa achada, mas o download/gravação falhou",
            ),
            ("Erros", "errors", RED, "falhas ao consultar o Qobuz ou gravar tags"),
        ):
            if tags_only and key.startswith("apple_"):
                continue  # sem etapa Apple neste modo
            if key == "skipped_done" and not stats[key]:
                continue  # só aparece quando houve arquivos pulados
            value = stats[key]
            value_color = color_on if (color_on and value) else MUTED
            ui.emit(
                f"{CYAN} • {text_label.ljust(26)}:{RESET} "
                f"{value_color}{value}{OFF}  {MUTED}{explanation}{OFF}"
            )

        for summary_line in _summary_detail_lines(stats, tags_only, dry_run):
            ui.emit(summary_line)

        if stats["no_id"]:
            ui.emit(
                f"{YELLOW}[i] Arquivos sem ID Qobuz não podem ser corrigidos "
                f"automaticamente.{OFF}"
            )

        if journal is not None and not dry_run:
            if journal.recorded:
                ui.emit(
                    f"{CYAN}[i] Para desfazer esta execução:{RESET} "
                    f"qobuz-dl tags --undo {journal.run_id}   "
                    f"{MUTED}({journal.recorded} arquivo"
                    f"{'s' if journal.recorded != 1 else ''} com backup){OFF}"
                )
            if journal.unbacked:
                ui.emit(
                    f"{YELLOW}[!] {journal.unbacked} arquivo(s) de formato não "
                    f"reconhecido ficaram sem backup para desfazer.{OFF}"
                )
        elif dry_run and stats["updated"]:
            ui.emit(
                f"{CYAN}[i] Para aplicar de verdade, rode o mesmo comando sem "
                f"--dry-run.{RESET}"
            )

        return stats

    finally:
        if track_progress and not completed:
            track_progress.save()
        if shared_holder["decision"] is not None:
            _discard_shared_cover(shared_holder["decision"])
        if dry_workdir:
            shutil.rmtree(dry_workdir, ignore_errors=True)
        await http_session.aclose()
