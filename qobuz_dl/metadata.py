# # ============================================================================
# # metadata.py -- normalização, tagging, arte e informações de áudio.
# # Fluxo principal: _get_tags_to_add() cria tags; tag_flac()/tag_mp3() grava-as.
# # Também cuida de capas embutidas, gêneros, IDs Qobuz, ReplayGain e tags clássicas.
# # ============================================================================
import io
import logging
import os
import re
import struct
import threading
import unicodedata
from typing import Any, Optional, cast

import humanize
import mutagen.id3 as id3
from mutagen.flac import FLAC, Picture, VCFLACDict
from mutagen.id3 import ID3NoHeaderError

from qobuz_dl import lyrics_comment
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.utils import (
    classify_release_type,
    encontrar_binario,
    get_album_artist,
)

logger = logging.getLogger(__name__)

# # Campos que podem conter vários créditos (ex.: "A, B") e que, com
# # multi_value_tags, viram tags DE VERDADE multivaloradas (uma entrada por
# # valor), em vez de um texto único com separador trocado.
MULTI_VALUE_FIELDS = {
    "ARTIST",
    "ARTISTSORT",
    "ALBUMARTIST",
    "ALBUMARTISTSORT",
    "COMPOSER",
    "GENRE",
    "LYRICIST",
    "PRODUCER",
    "MIXER",
    "ENGINEER",
    "ARRANGER",
}


# # Campos multivalorados: com multi_value_tags, cada campo de
# # MULTI_VALUE_FIELDS (ARTIST, ALBUMARTIST, COMPOSER, GENRE e os *SORT) é
# # gravado como VÁRIOS valores no próprio campo (ARTIST=["A", "B"]); não há
# # campos plurais separados (ARTISTS, ALBUMARTISTS...).


def _split_multi_value(value: str) -> list:
    """Quebra um texto unido por ", " em uma lista de valores individuais.

    Usado para gravar tags multivaloradas de verdade (FLAC: múltiplas
    entradas VORBIS_COMMENT com a mesma chave; ID3v2.4: múltiplos valores
    de texto no mesmo frame), em vez de apenas trocar o separador dentro de
    uma única string -- que era o bug original (tudo virava "A ; B" num só
    campo, sem nenhuma separação real).
    """
    return [part.strip() for part in value.split(", ") if part.strip()]


# # Nomes que TÊM vírgula no próprio nome. Sem proteção, "Tyler, The Creator"
# # virava dois artistas ("Tyler" e "The Creator"), porque a API do Qobuz
# # entrega créditos como texto separado por vírgula ("Nome, Papel, Papel").
# # Nomes extras podem ser informados em config.ini (artists_with_comma, separados
# # por ";"); nomes estruturados vindos da própria API (lista "artists") também
# # são protegidos automaticamente.
NOMES_COM_VIRGULA = frozenset(
    {
        "Tyler, The Creator",
        "Earth, Wind & Fire",
        "Crosby, Stills, Nash & Young",
        "Crosby, Stills & Nash",
        "Emerson, Lake & Palmer",
        "Blood, Sweat & Tears",
        "Peter, Paul and Mary",
        "Peter, Paul & Mary",
    }
)

_PH_ABRE, _PH_FECHA = "", ""


def _nomes_protegidos(qobuz_album=None, qobuz_item=None, settings=None) -> list:
    """Nomes com vírgula que não devem ser quebrados, do mais longo ao mais curto.

    União de: lista embutida, config (settings.artists_with_comma) e nomes de
    artistas ESTRUTURADOS da API (lista "artists": cada entrada é UMA pessoa/grupo
    com seus papéis, então uma vírgula ali faz parte do nome). Os campos de texto
    "artist"/"performer" não entram: neles o Qobuz às vezes junta "A, B" de verdade.
    """
    nomes = set(NOMES_COM_VIRGULA)
    for extra in getattr(settings, "artists_with_comma", None) or ():
        if isinstance(extra, str) and "," in extra:
            nomes.add(extra.strip())
    for fonte in (qobuz_album, qobuz_item):
        if not isinstance(fonte, dict):
            continue
        lista = fonte.get("artists")
        if not isinstance(lista, list):
            continue
        for artista in lista:
            nome = artista.get("name") if isinstance(artista, dict) else None
            if isinstance(nome, str) and "," in nome:
                nomes.add(nome.strip())
    return sorted(nomes, key=len, reverse=True)


def _split_protegido(texto, sep: str, protegidos) -> list:
    """Como ``texto.split(sep)``, mas sem quebrar nomes protegidos.

    Cada nome protegido (sem diferenciar maiúsculas, só como palavra inteira) é
    trocado por um marcador antes de quebrar e restaurado depois. Devolve as
    partes sem espaços nas pontas e sem vazios.
    """
    if not texto or not isinstance(texto, str):
        return []
    restaurar: dict[str, str] = {}
    trabalho = texto
    for nome in protegidos or ():
        padrao = re.compile(r"(?<!\w)" + re.escape(nome) + r"(?!\w)", re.IGNORECASE)

        def _troca(m):
            marcador = f"{_PH_ABRE}{len(restaurar)}{_PH_FECHA}"
            restaurar[marcador] = m.group(0)
            return marcador

        trabalho = padrao.sub(_troca, trabalho)
    partes = []
    for parte in trabalho.split(sep):
        parte = parte.strip()
        for marcador, original in restaurar.items():
            parte = parte.replace(marcador, original)
        if parte:
            partes.append(parte)
    return partes


def _valores_multi(chave: str, valor, multi_out=None):
    """Lista a gravar numa tag multivalorada (ou None se não é multivalorada).

    Prefere a lista EXATA montada em ``_get_tags_to_add`` (que sabe que
    "Tyler, The Creator" é um artista só); cai no split por ", " só se não houver
    lista ou se o texto da tag já não corresponde a ela.
    """
    if chave not in MULTI_VALUE_FIELDS or not isinstance(valor, str):
        return None
    lista = (multi_out or {}).get(chave)
    if lista and ", ".join(lista) == valor:
        return list(lista)
    if ", " in valor:
        return _split_multi_value(valor)
    return None


COPYRIGHT, PHON_COPYRIGHT = "\u00a9", "\u2117"
# ANTES: os dois valores estavam trocados ("\u2117", "\u00a9") -- COPYRIGHT
# (usado pro marcador "(C)") recebia o símbolo de copyright FONOGRÁFICO
# (℗), e PHON_COPYRIGHT (usado pro marcador "(P)") recebia o símbolo de
# copyright comum (©). O oposto do que os nomes das variáveis dizem, e do
# padrão real: (C) = © (obra), (P) = ℗ (gravação sonora). Achado
# escrevendo teste pra _format_copyright().
# # Limite máximo de um bloco de metadados FLAC: 0xFFFFFF bytes.
FLAC_MAX_BLOCKSIZE = 16777215

# # Limite máximo de um bloco de metadados FLAC: 0xFFFFFF bytes.
ID3_LEGEND = {
    "albumartist": id3.TPE2,
    "album": id3.TALB,
    "artist": id3.TPE1,
    "title": id3.TIT2,
    "date": id3.TDRC,
    "mediatype": id3.TMED,
    "genre": id3.TCON,
    "composer": id3.TCOM,
    "itunesadvisory": id3.TXXX,
    "copyright": id3.TCOP,
    "label": id3.TPUB,
    "barcode": id3.TXXX,
    "isrc": id3.TSRC,
    "comment": id3.COMM,
    "year": id3.TYER,
    "performer": id3.TOPE,
    # IDs usados por sync-playlist, .m3u e pela identificação de faixas na
    # pasta única de playlist. Sem estas chaves o MP3 não gravava o ID.
    "QOBUZTRACKID": id3.TXXX,
    "QOBUZALBUMID": id3.TXXX,
    "QOBUZ TRACK ID": id3.TXXX,
    "QOBUZ ALBUM ID": id3.TXXX,
    "QOBUZ ALBUM URL": id3.TXXX,
    "replaygain_track_gain": id3.TXXX,
    "replaygain_track_peak": id3.TXXX,
    "conductor": id3.TPE3,
    "lyricist": id3.TEXT,
    "producer": id3.TXXX,
    "mixer": id3.TXXX,
    "engineer": id3.TXXX,
    "arranger": id3.TXXX,
    "ensemble": id3.TXXX,
    "work": id3.TIT1,
}

EMB_COVER_NAME = "embed_cover.jpg"
COVER_SOURCE_TAG = "COVER_SOURCE"
COVER_SOURCE_APPLE = "Apple/iTunes"  # mesmo texto do comando `tags` e da busca Apple
COVER_SOURCE_QOBUZ = "Qobuz"
COVER_SOURCE_UNKNOWN = "Desconhecida"

# # Limite máximo de um bloco de metadados FLAC: 0xFFFFFF bytes.
LOCAL_GENRE_MAP = {
    "Électronique": "Electronic",
    "Ambiance": "Ambient",
    "Classique": "Classical",
    "Musique de chambre": "Chamber Music",
    "Opéra": "Opera",
    "Chorale": "Choral",
    "Symphonique": "Symphonic",
    "Bande Originale": "Soundtrack",
    "Musique de film": "Soundtrack",
    "Comédie Musicale": "Musical",
    "Bande originale de jeu vidéo": "Video Game Soundtrack",
    "Jazz Vocal": "Vocal Jazz",
    "Jazz Contemporain": "Contemporary Jazz",
    "Musiques du monde": "World",
    "Musique celtique": "Celtic",
    "Musique latine": "Latin",
    "Variété Française": "French Pop",
    "Alternatif et Indé": "Alternative & Indie",
    "Enfants": "Children's Music",
    "Berceuses": "Lullabies",
    "Poésie et Littérature": "Spoken Word",
    "Livres Audio": "Audiobooks",
    "Humour": "Comedy",
    "Religieux": "Religious",
    "Détente": "Relaxation",
    "Fêtes": "Holiday",
}


# # Move artigos iniciais para o final: "The Beatles" -> "Beatles, The".
def _make_sort_name(name) -> str:
    """
    Deriva o nome de ordenacao movendo artigos iniciais pro fim.
    "The Beatles" -> "Beatles, The"
    Suporta listas de artistas.
    """
    if not name:
        return ""

    if isinstance(name, list):
        return ", ".join(_make_sort_name(n) for n in name if n)

    name = str(name)
    articles = (
        "The ",
        "A ",
        "An ",
        "Os ",
        "As ",
        "O ",
        "A ",
        "Los ",
        "Las ",
        "El ",
        "La ",
        "Les ",
        "Le ",
        "L'",
        "Die ",
        "Das ",
        "Der ",
        "Gli ",
        "Le ",
        "I ",
        "Il ",
    )
    for art in articles:
        if name.startswith(art):
            return name[len(art) :].strip() + ", " + art.strip()
    return name


# # Acrescenta a versão sem duplicá-la quando ela já faz parte do título.
def _get_title_with_version(title: str = "", version: str = "") -> str:
    """Get track title with version suffix if present."""
    item_title = title
    if version:
        item_title = (
            f"{title} ({version})" if version.lower() not in title.lower() else title
        )
    return item_title


# # Monta o título de uma faixa, incluindo versão e obra clássica quando existir.
def _get_title(track_dict):
    """Get base track title without version."""
    title = track_dict["title"]
    version = track_dict.get("version")
    if version:
        title = f"{title} ({version})"
    if track_dict.get("work"):
        title = f"{track_dict['work']}: {title}"

    return title


# # Converte marcadores (P)/(C) nos símbolos Unicode usados nas tags.
def _format_copyright(s: str) -> str:
    """Format copyright string for tags."""
    if s:
        s = s.replace("(P)", PHON_COPYRIGHT)
        s = s.replace("(C)", COPYRIGHT)
    return s


def _eh_various_artists(qobuz_album: dict, settings=None) -> bool:
    """True se o álbum-artista está na lista de coletâneas (various_artists_aliases)."""
    aliases = getattr(settings, "various_artists_aliases", None) or frozenset()
    if not aliases:
        return False
    nomes = get_album_artist(qobuz_album) or []
    return any(str(n).strip().casefold() in aliases for n in nomes)


def _tracktotal_do_disco(qobuz_album: dict, qobuz_item: dict) -> str:
    """TRACKTOTAL por disco: total de faixas do disco atual, não do álbum inteiro.

    Só aplica quando o álbum tem mais de um disco e a lista de faixas está
    completa; caso contrário, mantém o total do álbum (faixa avulsa, playlist,
    álbum parcialmente listado).
    """
    total = qobuz_album.get("tracks_count") or 1
    items = (qobuz_album.get("tracks") or {}).get("items") or []
    if (qobuz_album.get("media_count") or 1) > 1 and len(items) == total:
        disco = qobuz_item.get("media_number", 1)
        no_disco = sum(1 for t in items if t.get("media_number", 1) == disco)
        if no_disco:
            return str(no_disco)
    return str(total)


def _numeracao_continua(qobuz_album: dict, qobuz_item: dict):
    """Numeração contínua (1..N) das faixas de um álbum com vários discos.

    Retorna (numero, total) quando o álbum tem mais de um disco E a lista de
    faixas está completa (len(items) == tracks_count) E a faixa atual está
    nessa lista. Nos demais casos (disco único, faixa avulsa, playlist, lista
    parcial) retorna None e o chamador mantém o comportamento anterior.

    Só é usada com `continuous_track_numbers` ligado (padrão: desligado, e o
    TRACKNUMBER fica por disco). Só afeta TRACKNUMBER/TRACKTOTAL/TRCK. DISCNUMBER/DISCTOTAL/TPOS continuam
    sendo gravados (é por eles que o player divide a lista por disco) e o nome
    do arquivo continua usando o número por disco.
    """
    total = qobuz_album.get("tracks_count") or 0
    items = (qobuz_album.get("tracks") or {}).get("items") or []
    if (qobuz_album.get("media_count") or 1) <= 1 or not total:
        return None
    if len(items) != total:
        return None

    def _ordem(t):
        try:
            disco = int(t.get("media_number") or 1)
        except (TypeError, ValueError):
            disco = 1
        try:
            faixa = int(t.get("track_number") or 0)
        except (TypeError, ValueError):
            faixa = 0
        return (disco, faixa)

    ordenadas = sorted(items, key=_ordem)
    atual = qobuz_item.get("id")
    posicao = None
    if atual is not None:
        for i, t in enumerate(ordenadas, 1):
            if t.get("id") == atual:
                posicao = i
                break
    if posicao is None:
        chave = _ordem(qobuz_item)
        for i, t in enumerate(ordenadas, 1):
            if _ordem(t) == chave:
                posicao = i
                break
    if posicao is None:
        return None
    return posicao, total


# # Remove caminhos/setas de gênero e elimina gêneros repetidos.
def _format_genres(genres: list) -> str:
    """Format genre list for tags."""
    genres = re.findall(r"([^\u2192\/]+)", "/".join(genres))
    no_repeats = []
    for g in genres:
        if g not in no_repeats:
            no_repeats.append(g)
    return ", ".join(no_repeats)


# # Procura a imagem temporária de embed na pasta atual e na pasta pai.
# Funções do campo `performers` da API -> tag de crédito. As funções são
# comparadas sem maiúsculas/símbolos ("Mixing Engineer" == "MixingEngineer").
_CREDIT_ROLES = {
    "LYRICIST": {"lyricist", "composerlyricist"},
    "PRODUCER": {"producer", "coproducer", "executiveproducer"},
    "MIXER": {"mixer", "mixingengineer"},
    "ENGINEER": {"engineer", "recordingengineer", "soundengineer"},
    "ARRANGER": {"arranger"},
}


def _extract_credits(performers_str, protegidos=()) -> dict:
    """Extrai créditos de produção do texto `performers` da API.

    O texto vem como "Nome, Funcao1, Funcao2 - Outro Nome, Funcao". Devolve
    {TAG: [nomes]} só para as tags que têm pelo menos um nome.
    """
    if not performers_str or not isinstance(performers_str, str):
        return {}
    encontrados: dict = {tag: [] for tag in _CREDIT_ROLES}
    for bloco in performers_str.split(" - "):
        partes = _split_protegido(bloco, ",", protegidos)
        if len(partes) <= 1:
            continue
        nome = partes[0].strip()
        if not nome:
            continue
        funcoes = {re.sub(r"[^a-z]", "", f.lower()) for f in partes[1:]}
        for tag, aceitas in _CREDIT_ROLES.items():
            if funcoes & aceitas and nome not in encontrados[tag]:
                encontrados[tag].append(nome)
    return {tag: nomes for tag, nomes in encontrados.items() if nomes}


def track_artist_names(qobuz_album: dict, qobuz_item: dict) -> list:
    """Lista de artistas da faixa (principal + participações), como na tag ARTIST."""
    multi: dict = {}
    _get_tags_to_add(qobuz_album, qobuz_item, QobuzDLSettings(), multi_out=multi)
    return list(multi.get("ARTIST") or [])


def _escrever_ids_musicbrainz_id3(audio, mb_tags: dict) -> None:
    """Frames ID3 no padrão do Picard: UFID da gravação e TXXX de álbum/artista."""
    faixa = mb_tags.get("MUSICBRAINZ_TRACKID")
    if faixa:
        audio.add(
            id3.UFID(owner="http://musicbrainz.org", data=str(faixa).encode("ascii"))
        )
    album = mb_tags.get("MUSICBRAINZ_ALBUMID")
    if album:
        audio.add(id3.TXXX(encoding=3, desc="MusicBrainz Album Id", text=str(album)))
    artistas = mb_tags.get("MUSICBRAINZ_ARTISTID")
    if artistas:
        valores = (
            [str(a) for a in artistas]
            if isinstance(artistas, (list, tuple))
            else [str(artistas)]
        )
        audio.add(id3.TXXX(encoding=3, desc="MusicBrainz Artist Id", text=valores))


def _tem_valor(valor) -> bool:
    """True se a API trouxe um valor utilizável (None e texto vazio não contam)."""
    return valor is not None and str(valor).strip() != ""


def _numero_de_total(numero, total):
    """Formata "n/total" para TRCK/TPOS; só "n" sem total; None sem número."""
    if not _tem_valor(numero):
        return None
    if not _tem_valor(total):
        return str(numero)
    return f"{numero}/{total}"


def _get_cover_path(root_dir, override=None):
    """
    Auxiliary function to locate the embedded cover art path.
    """
    if override and os.path.isfile(override):
        return override
    emb_image = os.path.join(root_dir, EMB_COVER_NAME)
    multi_emb_image = os.path.join(
        os.path.abspath(os.path.join(root_dir, os.pardir)), EMB_COVER_NAME
    )
    if os.path.isfile(emb_image):
        return emb_image
    elif os.path.isfile(multi_emb_image):
        return multi_emb_image
    return None


# # Normaliza nomes para comparação de duplicidade sem diferença de acentos/caixa.
def _normalize_name(name) -> str:
    """Removes accents, invisible spaces, and converts to lowercase for strict duplicate checking."""
    if isinstance(name, list):
        name = ", ".join(str(n) for n in name if n)
    return (
        unicodedata.normalize("NFKD", str(name))
        .encode("ASCII", "ignore")
        .decode("utf-8")
        .lower()
        .strip()
    )


# # Sem Pillow (a-Shell): recompacta com o ffmpeg, que o projeto já usa.
# -q:v do encoder MJPEG: 2 = melhor qualidade, 31 = pior. Espelha 95 -> 50 do Pillow.
_FFMPEG_QUALITIES = (2, 3, 4, 5, 7, 9, 12, 16)
_FFMPEG_SCALES = (0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2)


def _run_ffmpeg_jpeg(ffmpeg, image_path, out_path, quality, scale=None):
    """Roda o ffmpeg uma vez e devolve os bytes do JPEG gerado (ou None)."""
    import subprocess

    filters = ["-vf", f"scale=trunc(iw*{scale}):trunc(ih*{scale})"] if scale else []
    try:
        subprocess.run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-i",
                image_path,
                "-frames:v",
                "1",
                *filters,
                "-q:v",
                str(quality),
                out_path,
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )
        with open(out_path, "rb") as handle:
            return handle.read()
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("ffmpeg não conseguiu recompactar a capa: %s", exc)
        return None


def _shrink_with_ffmpeg(image_path, max_bytes):
    """Mesma estratégia do Pillow (qualidade primeiro, depois dimensões), via ffmpeg.

    Devolve os bytes do JPEG ou None se não houver ffmpeg, se ele falhar ou se
    nem na menor escala a imagem couber.
    """
    ffmpeg = encontrar_binario("ffmpeg")
    if not ffmpeg:
        return None
    import tempfile

    with tempfile.TemporaryDirectory(prefix="qdl_cover_") as workdir:
        out_path = os.path.join(workdir, "capa.jpg")
        for quality in _FFMPEG_QUALITIES:
            data = _run_ffmpeg_jpeg(ffmpeg, image_path, out_path, quality)
            if data is None:
                return None  # o ffmpeg em si falhou: não adianta insistir
            if len(data) <= max_bytes:
                return data
        for scale in _FFMPEG_SCALES:
            data = _run_ffmpeg_jpeg(ffmpeg, image_path, out_path, 4, scale)
            if data is None:
                return None
            if len(data) <= max_bytes:
                return data
    return None


# # Recompacta a capa em memória para respeitar o limite do bloco FLAC.
def _shrink_image_to_fit(image_path, max_bytes):
    """
    Recompacta/redimensiona uma imagem ate' caber em max_bytes, priorizando
    a qualidade -- so' reduz o minimo necessario pra caber no limite.

    Estrategia em 2 fases, sempre tentando a opcao menos destrutiva primeiro:
      1. Mantem a resolucao original, so' reduz a qualidade de compressao
         JPEG em passos (95 -> 50). Isso sozinho ja resolve a grande maioria
         dos casos sem perda perceptivel nenhuma, porque a capa "org" da
         Qobuz normalmente nao vem no JPEG mais compacto possivel.
      2. So' se a fase 1 nao for suficiente (imagem com resolucao MUITO
         alta), reduz as dimensoes gradualmente (90% -> 20%, em passos de
         10%) mantendo qualidade 85, ate' caber.

    Retorna os bytes da imagem re-encodada (JPEG), ou None se o Pillow nao
    estiver disponivel ou a imagem nao puder ser processada -- nesses casos
    o chamador decide o que fazer (ex: pular o embed, como ja fazia antes).
    """
    try:
        from PIL import Image

        Image.new("RGB", (1, 1))  # no a-Shell o import passa e o núcleo C falha aqui
    except Exception:  # noqa: BLE001 -- ImportError, OSError do dlopen etc.
        data = _shrink_with_ffmpeg(image_path, max_bytes)
        if data is not None:
            logger.info("Capa recompactada com o ffmpeg (Pillow indisponível).")
            return data
        logger.warning(
            "Nao e' possivel recompactar a capa que excede o limite de 16MB do "
            "FLAC: o Pillow nao esta disponivel e o ffmpeg nao conseguiu. "
            "Instale o ffmpeg ou o Pillow (pip install Pillow)."
        )
        return None

    try:
        with Image.open(image_path) as src:
            image: Image.Image = src
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            resampling = getattr(Image, "Resampling", Image)
            lanczos = resampling.LANCZOS

            for quality in (95, 90, 85, 80, 75, 70, 60, 50):
                buf = io.BytesIO()
                image.save(buf, format="JPEG", quality=quality, optimize=True)
                if buf.tell() <= max_bytes:
                    return buf.getvalue()

            width, height = image.size
            scale = 0.9
            while scale > 0.19:
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                resized = image.resize(new_size, lanczos)
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=85, optimize=True)
                if buf.tell() <= max_bytes:
                    return buf.getvalue()
                scale -= 0.1

            return None
    except Exception as e:
        logger.error(f"Falha ao recompactar a capa: {e}", exc_info=True)
        return None


def _get_cover_source(tags) -> str:
    """Lê a origem da capa armazenada nas tags"""
    if not tags:
        return COVER_SOURCE_UNKNOWN

    value = tags.get(COVER_SOURCE_TAG)

    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""

    return str(value or COVER_SOURCE_UNKNOWN).strip()


def _set_cover_source(tags, source: Optional[str]) -> None:
    """Grava ou remove a origem da capa"""
    if source:
        tags[COVER_SOURCE_TAG] = str(source).strip()
    else:
        tags.pop(COVER_SOURCE_TAG, None)


def _cover_comment_line(source: Optional[str]):
    """Gera a linha legível da origem da capa"""
    return f"Capa: {source or COVER_SOURCE_UNKNOWN}"


# Linhas/trechos do COMMENT que descrevem a capa embutida. No FLAC cada item é
# uma linha ("Capa embutida: ..." / "Capa: ..."); no MP3 o comentário é uma
# linha única separada por " | " ("Cover: ..." / "Capa: ...").
_FLAC_COVER_COMMENT_PREFIXES = ("Capa embutida:", "Capa:")
_MP3_COVER_COMMENT_PREFIXES = ("Cover:", "Capa:")


def _extract_flac_cover_comment_lines(values) -> list:
    """Linhas de capa de um COMMENT Vorbis (str ou lista de str)."""
    if not values:
        return []
    if isinstance(values, str):
        values = [values]
    lines = []
    for value in values:
        for line in str(value).splitlines():
            line = line.strip()
            if line.startswith(_FLAC_COVER_COMMENT_PREFIXES) and line not in lines:
                lines.append(line)
    return lines


def _extract_mp3_cover_comment_segments(text) -> list:
    """Trechos de capa de um COMM ID3 (linha única separada por ' | ')."""
    if not text:
        return []
    if not isinstance(text, str):
        text = " | ".join(str(part) for part in text)
    segments = []
    for segment in text.split(" | "):
        segment = segment.strip()
        if segment.startswith(_MP3_COVER_COMMENT_PREFIXES) and segment not in segments:
            segments.append(segment)
    return segments


def _id3_comment_frame(audio):
    """Primeiro frame COMM com texto (None se não houver)."""
    try:
        frames = audio.getall("COMM")
    except Exception:
        return None
    for frame in frames:
        for part in getattr(frame, "text", None) or []:
            if str(part).strip():
                return frame
    return None


def _id3_comment_text(audio) -> str:
    """Texto do primeiro frame COMM não vazio ('' se não houver)."""
    frame = _id3_comment_frame(audio)
    if frame is None:
        return ""
    for part in frame.text:
        if str(part).strip():
            return str(part)
    return ""


# # Embute a capa no FLAC; se necessário, usa uma cópia recompactada sem alterar cover.jpg.
def _dimensions_from_header(data):
    """(largura, altura) lidas só do cabeçalho de JPEG, PNG, GIF ou WebP.

    Python puro, sem Pillow: no a-Shell o Pillow é opcional (usa-se o que já vem
    instalado) e o comando `tags` não pode deixar de comparar capas por causa
    dele. Devolve None para dados truncados, corrompidos ou de outro formato.
    """
    largura = altura = 0
    try:
        if not data or len(data) < 12:
            return None
        if data[:8] == b"\x89PNG\r\n\x1a\n":
            if len(data) < 24 or data[12:16] != b"IHDR":
                return None
            largura, altura = struct.unpack(">II", data[16:24])
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            largura, altura = struct.unpack("<HH", data[6:10])
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            tipo = data[12:16]
            if tipo == b"VP8 " and len(data) >= 30:
                largura, altura = struct.unpack("<HH", data[26:30])
                largura, altura = largura & 0x3FFF, altura & 0x3FFF
            elif tipo == b"VP8L" and len(data) >= 25:
                b0, b1, b2, b3 = data[21:25]
                largura = 1 + (((b1 & 0x3F) << 8) | b0)
                altura = 1 + (((b3 & 0x0F) << 10) | (b2 << 2) | ((b1 & 0xC0) >> 6))
            elif tipo == b"VP8X" and len(data) >= 30:
                largura = 1 + int.from_bytes(data[24:27], "little")
                altura = 1 + int.from_bytes(data[27:30], "little")
            else:
                return None
        elif data[:2] == b"\xff\xd8":
            i, n = 2, len(data)
            while i + 4 <= n:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marcador = data[i + 1]
                if marcador == 0xFF:
                    i += 1
                    continue
                if marcador in (0x01, 0xD8) or 0xD0 <= marcador <= 0xD7:
                    i += 2
                    continue
                if marcador == 0xD9:
                    return None
                tamanho = struct.unpack(">H", data[i + 2 : i + 4])[0]
                # SOF0..SOF15, exceto DHT (C4), JPG (C8) e DAC (CC).
                if 0xC0 <= marcador <= 0xCF and marcador not in (0xC4, 0xC8, 0xCC):
                    if i + 9 > n:
                        return None
                    altura, largura = struct.unpack(">HH", data[i + 5 : i + 9])
                    break
                i += 2 + tamanho
            else:
                return None
        else:
            return None
        if largura > 0 and altura > 0:
            return (largura, altura)
    except Exception:
        return None
    return None


def _image_dimensions(image_data):
    """Retorna (largura, altura) de bytes de imagem; None se inválidos.

    Usa o Pillow quando ele funciona e, sem ele, lê o cabeçalho direto
    (`_dimensions_from_header`): Pillow é opcional e a comparação de capas do
    `tags` não depende dele.
    """
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_data)) as image:
            return image.size
    except Exception:
        return _dimensions_from_header(image_data)


_COVER_COMPARE = threading.local()
_SQUARE_TOLERANCE = 0.03  # 3% de diferença entre largura e altura ainda é "quadrada"


def _is_square(dimensions) -> bool:
    """True se (largura, altura) é praticamente quadrada."""
    largura, altura = dimensions
    return abs(largura - altura) <= _SQUARE_TOLERANCE * max(largura, altura)


def last_cover_compare_reason() -> Optional[str]:
    """Motivo da última comparação de capas NESTA thread, quando não decisiva.

    "unreadable": não deu para ler as dimensões (da candidata ou da capa atual),
    então a comparação não pôde ser feita e a capa atual foi preservada.
    "not_square": a candidata não é quadrada e a capa atual é. None: a
    comparação foi feita normalmente (trocou ou a atual já era melhor).
    """
    return getattr(_COVER_COMPARE, "reason", None)


def should_replace_embedded_cover(filename, candidate_path):
    """Decide se a capa candidata tem resolução superior à já embutida.

    A política é conservadora: se já existe uma capa e não for possível
    comparar as dimensões com segurança, preserva a existente. Uma candidata
    só substitui a capa atual quando tem mais pixels (área total).
    Quando a comparação não é possível, ``last_cover_compare_reason()`` devolve
    "unreadable" (para o log não dizer que a atual "já é melhor").
    """
    _COVER_COMPARE.reason = None
    if not candidate_path or not os.path.isfile(candidate_path):
        return False

    try:
        with open(candidate_path, "rb") as cover_file:
            candidate_dimensions = _image_dimensions(cover_file.read())
        if not candidate_dimensions:
            _COVER_COMPARE.reason = "unreadable"
            return False

        extension = os.path.splitext(filename)[1].lower()
        existing_images = []
        if extension == ".mp3":
            try:
                existing_tags = id3.ID3(filename)
                existing_images = [
                    getattr(frame, "data", b"")
                    for frame in existing_tags.values()
                    if isinstance(frame, id3.APIC) and getattr(frame, "data", None)
                ]
            except (ID3NoHeaderError, OSError):
                existing_images = []
        else:
            try:
                existing_audio = FLAC(filename)
                existing_images = [
                    pic.data for pic in existing_audio.pictures if pic.data
                ]
            except Exception:
                existing_images = []

        # Sem capa atual, a candidata pode ser embutida normalmente.
        if not existing_images:
            return True

        candidate_area = candidate_dimensions[0] * candidate_dimensions[1]
        existing_areas = []
        existing_square = False
        for image_data in existing_images:
            dimensions = _image_dimensions(image_data)
            if not dimensions:
                # Não destrói uma capa existente cuja qualidade não pôde ser lida.
                _COVER_COMPARE.reason = "unreadable"
                return False
            existing_areas.append(dimensions[0] * dimensions[1])
            existing_square = existing_square or _is_square(dimensions)

        # Capa de álbum é quadrada: uma candidata cortada/esticada não troca uma
        # capa atual quadrada, mesmo tendo mais pixels.
        if existing_square and not _is_square(candidate_dimensions):
            _COVER_COMPARE.reason = "not_square"
            return False

        # Só substitui se superar todas as capas já embutidas.
        return candidate_area > max(existing_areas)
    except Exception as exc:
        logger.debug("Não foi possível comparar capas de '%s': %s", filename, exc)
        _COVER_COMPARE.reason = "unreadable"
        return False


def _embed_flac_img(root_dir, audio: FLAC, cover_override=None):
    """Embed cover art in FLAC file."""
    cover_image = _get_cover_path(root_dir, override=cover_override)

    if not cover_image or not os.path.isfile(cover_image):
        logger.warning(
            "Capa não embutida: arquivo de capa não encontrado "
            f"(procurado em '{root_dir}' e na pasta pai) -- faixa segue sem capa."
        )
        return

    try:
        # Remove imagens anteriores para não acumular capas duplicadas
        audio.clear_pictures()

        original_size = os.path.getsize(cover_image)
        image_data = None

        # # O arquivo salvo continua original; somente os bytes enviados ao embed são reduzidos.
        if original_size > FLAC_MAX_BLOCKSIZE:
            logger.info(
                f"Capa ({humanize.naturalsize(original_size, binary=True)}) "
                "excede o limite de 16MB de embed do FLAC -- recompactando apenas "
                "os bytes enviados ao embed."
            )
            image_data = _shrink_image_to_fit(cover_image, FLAC_MAX_BLOCKSIZE)
            if image_data is None:
                raise RuntimeError("falha ao recompactar a capa para o limite do FLAC")
        else:
            with open(cover_image, "rb") as image_file:
                image_data = image_file.read()

        image = Picture()
        image.type = 3
        image.mime = "image/jpeg"
        image.desc = "cover"
        image.data = image_data
        audio.add_picture(image)
    except Exception as e:
        logger.error(f"Erro ao substituir capa no FLAC: {e}", exc_info=True)


# # Adiciona a capa como frame APIC no ID3 do MP3.
def _embed_id3_img(root_dir, audio: id3.ID3, cover_override=None):
    """Embed cover art in MP3 file."""
    cover_image = _get_cover_path(root_dir, override=cover_override)

    if not cover_image or not os.path.isfile(cover_image):
        logger.warning(
            "Capa não embutida: arquivo de capa não encontrado "
            f"(procurado em '{root_dir}' e na pasta pai) -- faixa segue sem capa."
        )
        return

    try:
        for key in list(audio.keys()):
            if key.startswith("APIC"):
                del audio[key]

        with open(cover_image, "rb") as cover_file:
            audio.add(
                id3.APIC(
                    encoding=3,
                    mime="image/jpeg",
                    type=3,
                    desc="cover",
                    data=cover_file.read(),
                )
            )
    except OSError as e:
        logger.warning(f"Capa não embutida: falha ao ler '{cover_image}': {e}")


# # Aplica tags Vorbis, comentário técnico, capa e salva o FLAC final.
def tag_flac(
    filename,
    root_dir,
    final_name,
    d: dict,
    album,
    istrack=True,
    em_image=False,
    settings: Optional[QobuzDLSettings] = None,
    embed_cover_path=None,
    musicbrainz_ids=None,
    cover_source: Optional[str] = None,
    preserve_cover_info: bool = False,
):
    """Apply tags to FLAC file.

    preserve_cover_info: quando a capa NÃO é regravada (em_image=False), mantém
    no COMMENT as linhas "Capa embutida:"/"Capa:" já existentes. Sem isso, um
    retag que preserva a capa apagava a origem registrada no comentário.
    """
    audio = FLAC(filename)
    if settings is None:
        settings = QobuzDLSettings()

    if istrack:
        qobuz_item = d
        qobuz_album = d.get("album", {})
    else:
        qobuz_item = d
        qobuz_album = album

    # # Centraliza a montagem para manter FLAC e MP3 com o mesmo conteúdo lógico.
    multi_out: dict = {}
    tags = _get_tags_to_add(
        qobuz_album,
        qobuz_item,
        settings=settings,
        musicbrainz_ids=musicbrainz_ids,
        multi_out=multi_out,
        istrack=istrack,
    )
    if em_image:
        _set_cover_source(tags, cover_source)

    # Numeracao so e' gravada quando a API informou o valor: um "1" inventado
    # seria um dado errado, pior que tag ausente.
    # Álbum com vários discos e lista completa: o player mostra 1..N, e
    # DISCNUMBER/DISCTOTAL (abaixo) continuam dividindo a lista por disco.
    continua = (
        _numeracao_continua(qobuz_album, qobuz_item)
        if getattr(settings, "continuous_track_numbers", False)
        else None
    )
    if continua:
        if not settings.no_track_number_tag:
            tags["TRACKNUMBER"] = str(continua[0])
        if not settings.no_track_total_tag:
            tags["TRACKTOTAL"] = str(continua[1])
    else:
        if not settings.no_track_number_tag and _tem_valor(
            qobuz_item.get("track_number")
        ):
            tags["TRACKNUMBER"] = str(qobuz_item["track_number"])
        if not settings.no_track_total_tag and _tem_valor(
            qobuz_album.get("tracks_count")
        ):
            tags["TRACKTOTAL"] = _tracktotal_do_disco(qobuz_album, qobuz_item)
    if not settings.no_disc_number_tag and _tem_valor(qobuz_item.get("media_number")):
        tags["DISCNUMBER"] = str(qobuz_item["media_number"])
    if not settings.no_disc_total_tag and _tem_valor(qobuz_album.get("media_count")):
        tags["DISCTOTAL"] = str(qobuz_album["media_count"])

    _bit = qobuz_item.get("maximum_bit_depth", 16)
    _rate = qobuz_item.get("maximum_sampling_rate", 44.1)

    _ch = qobuz_item.get("maximum_channel_count", 2)
    _ch_map = {1: "Mono", 2: "Estéreo", 4: "4.0", 6: "5.1", 8: "7.1"}
    _channels = _ch_map.get(_ch, f"{_ch}ch")

    _hires = "sim" if qobuz_item.get("hires_streamable") else "nao"

    _dur_s = int(qobuz_item.get("duration") or 0)
    _duration = f"{_dur_s // 60}:{_dur_s % 60:02d}" if _dur_s else "?"

    # BUGFIX: usava qobuz_album.get("release_type") cru -- esse campo
    # vem errado da API com frequencia (mesmo motivo que existe
    # classify_release_type() em utils.py). So' aparece num log
    # verboso ("Tipo: ..."), mas o valor errado aqui e' exatamente o
    # tipo de coisa que classify_release_type() foi feito pra evitar.
    _rtype = classify_release_type(
        title=qobuz_album.get("title"),
        version=qobuz_album.get("version"),
        track_count=qobuz_album.get("tracks_count", 0),
        duration_seconds=qobuz_album.get("duration", 0),
        api_release_type=qobuz_album.get("release_type"),
    )

    _raw_date = qobuz_album.get("release_date_original", "") or ""
    try:
        if "-" in _raw_date and len(_raw_date) >= 10:
            _y, _m, _d = _raw_date[:10].split("-")
            _rel_date = f"{_d}/{_m}/{_y}"
        else:
            _rel_date = _raw_date or "?"
    except Exception:
        _rel_date = _raw_date or "?"

    _trk_id = qobuz_item.get("id", "?")

    _rate_text = (
        str(int(_rate))
        if isinstance(_rate, float) and _rate.is_integer()
        else str(_rate)
    )

    # # Comentário legível com qualidade, canais, duração, tipo, data e ID Qobuz.
    comment_lines = [
        "Fonte: Qobuz",
        f"Qualidade: FLAC {_bit}-bit / {_rate_text} kHz / {_channels}",
        f"Hi-res: {'Sim' if qobuz_item.get('hires_streamable') else 'Não'}",
        f"Duração: {_duration}",
        f"Tipo: {_rtype.capitalize()}",
        f"Lançamento: {_rel_date}",
        f"Faixa Qobuz: https://open.qobuz.com/track/{_trk_id}",
    ]

    _album_id = qobuz_album.get("id")
    if _album_id:
        comment_lines.append(f"Álbum Qobuz: https://open.qobuz.com/album/{_album_id}")

    if em_image:
        cover_path = _get_cover_path(root_dir, override=embed_cover_path)
        if cover_path:
            img_size_bytes = os.path.getsize(cover_path)
            req_size = getattr(settings, "embedded_art_size", "unknown")
            cover_kind = "Original" if req_size == "org" else "Redimensionada"
            comment_lines.append(
                "Capa embutida: "
                f"{humanize.naturalsize(img_size_bytes, binary=True)}"
                f" | {cover_kind}"
                f" | Solicitada: {req_size}"
            )
        if cover_source:
            comment_lines.append(_cover_comment_line(cover_source))
    elif preserve_cover_info:
        for line in _extract_flac_cover_comment_lines(audio.get("COMMENT")):
            if line not in comment_lines:
                comment_lines.append(line)
    # A origem da letra ("Letra: Qobuz (...)") e' gravada pelo motor de letras,
    # nao por este modulo: sempre preservada ao reescrever o COMMENT.
    for line in lyrics_comment.extract_flac_lines(audio.get("COMMENT")):
        if line not in comment_lines:
            comment_lines.append(line)
    tags["COMMENT"] = "\n".join(comment_lines)

    # # Só grava valores preenchidos. Com multi_value_tags, ARTIST/COMPOSER/
    # # GENRE etc. viram tags multivaloradas de verdade -- uma entrada
    # # VORBIS_COMMENT por valor, e não um texto único "A ; B". A maioria dos
    # # players que entendem tags multivaloradas junta os valores com ", " ao
    # # exibir; quem não entende, mostra só o primeiro valor.
    for k, v in tags.items():
        if not v:
            continue

        # # Lista exata (respeita nomes com vírgula, ex.: "Tyler, The Creator").
        multi_values = (
            _valores_multi(k, v, multi_out)
            if getattr(settings, "multi_value_tags", False)
            else None
        )
        if multi_values and len(multi_values) > 1:
            audio[k] = multi_values
        else:
            audio[k] = v

    if em_image:
        _embed_flac_img(root_dir, audio, cover_override=embed_cover_path)

    for junk_tag in ["ENCODER", "ENCODED-BY", "ENCODED_BY"]:
        if junk_tag in audio:
            del audio[junk_tag]

    if audio.tags is not None:
        cast(VCFLACDict, audio.tags).vendor = ""

    audio.save(padding=lambda info: 8192)
    # Origem == destino quando o arquivo e' retagueado no lugar (retro_tags).
    if os.path.abspath(filename) != os.path.abspath(final_name):
        os.rename(filename, final_name)


# # Aplica frames ID3, comentário técnico, capa e salva o MP3 final.
def tag_mp3(
    filename,
    root_dir,
    final_name,
    d,
    album,
    istrack=True,
    em_image=False,
    settings: Optional[QobuzDLSettings] = None,
    embed_cover_path=None,
    musicbrainz_ids=None,
    cover_source: Optional[str] = None,
    preserve_cover_info: bool = False,
):
    """Apply tags to MP3 file (preserve_cover_info: ver tag_flac)."""
    if settings is None:
        settings = QobuzDLSettings()
    try:
        audio = id3.ID3(filename)
    except ID3NoHeaderError:
        audio = id3.ID3()

    if istrack:
        qobuz_item = d
        qobuz_album = d.get("album", {})
    else:
        qobuz_item = d
        qobuz_album = album

    # # Centraliza a montagem para manter FLAC e MP3 com o mesmo conteúdo lógico.
    multi_out: dict = {}
    tags = _get_tags_to_add(
        qobuz_album,
        qobuz_item,
        settings=settings,
        musicbrainz_ids=musicbrainz_ids,
        multi_out=multi_out,
        istrack=istrack,
    )
    if em_image:
        _set_cover_source(tags, cover_source)

    _bit = qobuz_item.get("maximum_bit_depth", 16)
    _rate = qobuz_item.get("maximum_sampling_rate", 44.1)

    _ch = qobuz_item.get("maximum_channel_count", 2)
    _ch_map = {1: "Mono", 2: "Estéreo", 4: "4.0", 6: "5.1", 8: "7.1"}
    _channels = _ch_map.get(_ch, f"{_ch}ch")

    _hires = "sim" if qobuz_item.get("hires_streamable") else "nao"

    _dur_s = int(qobuz_item.get("duration") or 0)
    _duration = f"{_dur_s // 60}:{_dur_s % 60:02d}" if _dur_s else "?"

    # BUGFIX: usava qobuz_album.get("release_type") cru -- esse campo
    # vem errado da API com frequencia (mesmo motivo que existe
    # classify_release_type() em utils.py). So' aparece num log
    # verboso ("Tipo: ..."), mas o valor errado aqui e' exatamente o
    # tipo de coisa que classify_release_type() foi feito pra evitar.
    _rtype = classify_release_type(
        title=qobuz_album.get("title"),
        version=qobuz_album.get("version"),
        track_count=qobuz_album.get("tracks_count", 0),
        duration_seconds=qobuz_album.get("duration", 0),
        api_release_type=qobuz_album.get("release_type"),
    )

    _raw_date = qobuz_album.get("release_date_original", "") or ""
    try:
        if "-" in _raw_date and len(_raw_date) >= 10:
            _y, _m, _d = _raw_date[:10].split("-")
            _rel_date = f"{_d}/{_m}/{_y}"
        else:
            _rel_date = _raw_date or "?"
    except Exception:
        _rel_date = _raw_date or "?"

    _trk_id = qobuz_item.get("id", "?")

    # # Comentário legível com qualidade, canais, duração, tipo, data e ID Qobuz.
    base_comment = (
        f"Qobuz | {_bit}b/{_rate}kHz | {_channels} | HiRes: {_hires}"
        f" | Duração: {_duration} | Tipo: {_rtype}"
        f" | Rel: {_rel_date} | Trk ID: {_trk_id}"
    )

    if em_image:
        cover_path = _get_cover_path(
            root_dir,
            override=embed_cover_path,
        )
        if cover_path:
            img_size_bytes = os.path.getsize(cover_path)
            req_size = getattr(settings, "embedded_art_size", "unknown")
            is_org = "YES" if req_size == "org" else "NO"
            base_comment += (
                f" | Cover: {humanize.naturalsize(img_size_bytes, binary=True)}"
                f" (Req: {req_size}, Org: {is_org})"
            )
        if cover_source:
            base_comment += f" | Capa: {cover_source}"
    elif preserve_cover_info:
        for segment in _extract_mp3_cover_comment_segments(_id3_comment_text(audio)):
            base_comment += f" | {segment}"
    # Origem da letra: sempre preservada (ver tag_flac).
    for segment in lyrics_comment.extract_mp3_segments(_id3_comment_text(audio)):
        base_comment += f" | {segment}"

    tags["COMMENT"] = base_comment

    # IDs do MusicBrainz não passam pelo ID3_LEGEND (descrições do Picard).
    _escrever_ids_musicbrainz_id3(
        audio, {k: tags.pop(k) for k in list(tags) if k.startswith("MUSICBRAINZ_")}
    )

    # Grava os frames ID3.
    #
    # Com multi_value_tags desativado:
    #   TPE1 = "A, B"
    #
    # Com multi_value_tags ativado:
    #   TPE1 recebe ["A", "B"] em ID3v2.4.
    #
    # O campo principal continua sendo TPE1/TPE2/TCO/etc.; não dependemos dos campos personalizados ARTISTS ou ALBUMARTISTS para indexação.
    for k, v in tags.items():
        if not v:
            continue

        # # Lista exata (respeita nomes com vírgula, ex.: "Tyler, The Creator").
        multi_values = (
            _valores_multi(k, v, multi_out)
            if getattr(settings, "multi_value_tags", False)
            else None
        )
        is_multi = bool(multi_values and len(multi_values) > 1)

        write_values = multi_values if is_multi else [v]
        id3tag = ID3_LEGEND.get(k.lower()) or ID3_LEGEND.get(k)

        if not id3tag:
            continue

        if id3tag == id3.TXXX:
            audio.add(
                id3tag(
                    encoding=3,
                    desc=k,
                    text=write_values if is_multi else v,
                )
            )
        elif id3tag == id3.COMM:
            audio.add(
                id3tag(
                    encoding=3,
                    lang="eng",
                    desc="",
                    text=[v],
                )
            )
        else:
            audio[id3tag.__name__] = id3tag(
                encoding=3,
                text=write_values,
            )

    # # TRCK/TPOS usam o formato número/total reconhecido por players. Sem o
    # # número da faixa/disco, o frame não é gravado; sem o total, vai só o número.
    continua = (
        _numeracao_continua(qobuz_album, qobuz_item)
        if getattr(settings, "continuous_track_numbers", False)
        else None
    )
    if continua:
        # Multi-disco completo: TRCK 1..N; o TPOS (abaixo) segue dividindo por disco.
        track = str(continua[0])
        if not settings.no_track_total_tag:
            track += f"/{continua[1]}"
    else:
        track = str(qobuz_item.get("track_number", "1"))
        if not settings.no_track_total_tag and _tem_valor(
            qobuz_album.get("media_count")
        ):
            track += f"/{_tracktotal_do_disco(qobuz_album, qobuz_item)}"
    audio["TRCK"] = id3.TRCK(encoding=3, text=track)

    _tpos = _numero_de_total(
        qobuz_item.get("media_number"), qobuz_album.get("media_count")
    )
    if _tpos:
        audio["TPOS"] = id3.TPOS(encoding=3, text=_tpos)

    if em_image:
        _embed_id3_img(root_dir, audio, cover_override=embed_cover_path)

    audio.pop("TENC", None)
    audio.pop("TSSE", None)

    # # v2.4 só quando multi_value_tags está ativo (precisa dele para
    # # frames de texto multivalor de verdade). Sem essa opção, mantém
    # # v2.3 para não mudar a compatibilidade padrão com players antigos.
    audio.save(
        filename,
        v2_version=4 if getattr(settings, "multi_value_tags", False) else 3,
    )
    # Origem == destino quando o arquivo e' retagueado no lugar (retro_tags).
    if os.path.abspath(filename) != os.path.abspath(final_name):
        os.rename(filename, final_name)


def replace_cover_only(
    filename,
    cover_path,
    cover_source: Optional[str] = None,
    settings: Optional[QobuzDLSettings] = None,
) -> bool:
    """Troca SOMENTE a imagem embutida (modo `tags --covers-only`).

    Não regrava nenhuma tag de metadados. Só acompanha a capa: a origem
    ("Capa: ...", COVER_SOURCE) e o tamanho no COMMENT são atualizados; o
    resto do comentário e das tags fica intacto. Devolve True se gravou.
    """
    if not cover_path or not os.path.isfile(cover_path):
        return False

    req_size = getattr(settings, "embedded_art_size", "unknown")
    size_text = humanize.naturalsize(os.path.getsize(cover_path), binary=True)
    root_dir = os.path.dirname(os.path.abspath(filename))
    extension = os.path.splitext(filename)[1].lower()

    if extension == ".mp3":
        try:
            audio = id3.ID3(filename)
            had_header = True
        except ID3NoHeaderError:
            audio = id3.ID3()
            had_header = False

        previous_apic = [f for k, f in audio.items() if k.startswith("APIC")]
        _embed_id3_img(root_dir, audio, cover_override=cover_path)
        if not [k for k in audio.keys() if k.startswith("APIC")]:
            # Falha ao ler a nova imagem: devolve a capa que já existia.
            for frame in previous_apic:
                audio.add(frame)
            return False

        is_org = "YES" if req_size == "org" else "NO"
        segments = [
            part
            for part in _id3_comment_text(audio).split(" | ")
            if part.strip() and not part.strip().startswith(_MP3_COVER_COMMENT_PREFIXES)
        ]
        segments.append(f"Cover: {size_text} (Req: {req_size}, Org: {is_org})")
        if cover_source:
            segments.append(_cover_comment_line(cover_source))
        new_comment = " | ".join(segments)

        comm_frame = _id3_comment_frame(audio)
        if comm_frame is not None:
            comm_frame.text = [new_comment]
        else:
            audio.add(id3.COMM(encoding=3, lang="eng", desc="", text=[new_comment]))

        # Mantém a versão ID3 do arquivo: baixar de v2.4 para v2.3 mexeria nos
        # frames multivalorados.
        version = audio.version[1] if had_header else 3
        audio.save(filename, v2_version=version if version in (3, 4) else 4)
        return True

    flac_audio = FLAC(filename)
    previous_pictures = list(flac_audio.pictures)
    _embed_flac_img(root_dir, flac_audio, cover_override=cover_path)
    if not flac_audio.pictures:
        # _embed_flac_img limpa as imagens antes de embutir; se falhou, restaura.
        for picture in previous_pictures:
            flac_audio.add_picture(picture)
        return False

    cover_kind = "Original" if req_size == "org" else "Redimensionada"
    lines = []
    current_comment = flac_audio.get("COMMENT") or []
    if isinstance(current_comment, str):
        current_comment = [current_comment]
    for value in current_comment:
        for line in str(value).splitlines():
            if line.strip() and not line.strip().startswith(
                _FLAC_COVER_COMMENT_PREFIXES
            ):
                lines.append(line)
    lines.append(f"Capa embutida: {size_text} | {cover_kind} | Solicitada: {req_size}")
    if cover_source:
        lines.append(_cover_comment_line(cover_source))
        flac_audio[COVER_SOURCE_TAG] = cover_source
    flac_audio["COMMENT"] = "\n".join(lines)
    flac_audio.save(padding=lambda info: 8192)
    return True


# # Constrói o dicionário unificado de tags a partir dos metadados Qobuz.
def _get_tags_to_add(
    qobuz_album: dict,
    qobuz_item: dict,
    settings: Optional[QobuzDLSettings] = None,
    musicbrainz_ids=None,
    multi_out: Optional[dict] = None,
    istrack: bool = True,
):
    """Build dictionary of tags to add based on settings.

    multi_out: se informado (dict), recebe as LISTAS exatas dos campos
    multivalorados (ARTIST, ALBUMARTIST, COMPOSER, GENRE e *SORT). As tags de
    texto continuam "A, B" (compatível); a lista evita que um nome com vírgula,
    como "Tyler, The Creator", seja quebrado ao gravar multi-tags.
    """
    tags: dict[str, Any] = {}
    if multi_out is None:
        multi_out = {}
    if settings is None:
        settings = QobuzDLSettings()
    if not qobuz_album or not qobuz_item:
        return tags

    protegidos = _nomes_protegidos(qobuz_album, qobuz_item, settings)

    if not settings.no_album_title_tag:
        tags["ALBUM"] = _get_title_with_version(
            title=qobuz_album.get("title", ""), version=qobuz_album.get("version", "")
        )

    # Flag de conteúdo explícito SOMENTE da faixa atual
    is_explicit_track = bool(qobuz_item.get("parental_warning", False))

    if not settings.no_track_title_tag:
        track_title = _get_title_with_version(
            title=qobuz_item.get("title", ""), version=qobuz_item.get("version", "")
        )
        if is_explicit_track:
            track_title += " 🅴"
        tags["TITLE"] = track_title

    if not settings.no_album_artist_tag:
        # Faixa individual: prioriza o performer da faixa.
        # Álbum completo: usa o artista principal do álbum, sem herdar o performer.
        _performer = (qobuz_item.get("performer") or {}).get("name", "")
        if _tem_valor(_performer) and (
            istrack or _eh_various_artists(qobuz_album, settings)
        ):
            _albumartist_values = [_performer.strip()]
        else:
            _albumartist_values = get_album_artist(qobuz_album)
        _albumartist_text = ", ".join(
            str(value).strip() for value in _albumartist_values if str(value).strip()
        )
        tags["ALBUMARTIST"] = _albumartist_text
        tags["ALBUMARTISTSORT"] = ", ".join(
            _make_sort_name(value)
            for value in _albumartist_values
            if str(value).strip()
        )
        multi_out["ALBUMARTIST"] = [
            str(value).strip() for value in _albumartist_values if str(value).strip()
        ]
        multi_out["ALBUMARTISTSORT"] = [
            _make_sort_name(str(value).strip())
            for value in _albumartist_values
            if str(value).strip()
        ]

    if not settings.no_track_artist_tag:
        artists = []
        seen_artists = set()

        structured_artist_lookup = {}
        for artist_source in (
            qobuz_album.get("artists"),
            qobuz_item.get("artists"),
        ):
            if not isinstance(artist_source, list):
                continue

            for artist_data in artist_source:
                if not isinstance(artist_data, dict):
                    continue

                artist_name = artist_data.get("name")
                if not isinstance(artist_name, str):
                    continue
                if not artist_name.strip():
                    continue

                normalized_name = _normalize_name(artist_name)
                if normalized_name and normalized_name not in structured_artist_lookup:
                    structured_artist_lookup[normalized_name] = artist_name.strip()

        def restore_artist_spelling(name):
            """Restores accents from structured Qobuz artist data."""
            clean_name = name.strip()
            normalized_name = _normalize_name(clean_name)
            return structured_artist_lookup.get(normalized_name, clean_name)

        def add_unique_artist(name):
            """Adds an artist once while preserving API spelling."""
            if not name or not name.strip():
                return

            restored_name = restore_artist_spelling(name)
            normalized_name = _normalize_name(restored_name)

            if normalized_name and normalized_name not in seen_artists:
                seen_artists.add(normalized_name)
                artists.append(restored_name)

        main_artist_raw = qobuz_item.get("performer", {}).get(
            "name", ""
        ) or qobuz_album.get("artist", {}).get("name", "")

        if main_artist_raw:
            for part in _split_protegido(main_artist_raw, ",", protegidos):
                add_unique_artist(part)

        performers_str = qobuz_item.get("performers", "")
        if performers_str:
            for performer_block in performers_str.split(" - "):
                parts = _split_protegido(performer_block, ",", protegidos)
                if len(parts) <= 1:
                    continue

                name = parts[0]
                roles = parts[1:]

                if (
                    "FeaturedArtist" in roles
                    or "MainArtist" in roles
                    or "PrimaryArtist" in roles
                ):
                    add_unique_artist(name)

        if artists:
            tags["ARTIST"] = ", ".join(artists)
            tags["ARTISTSORT"] = ", ".join(
                _make_sort_name(artist) for artist in artists
            )
            multi_out["ARTIST"] = list(artists)
            multi_out["ARTISTSORT"] = [_make_sort_name(a) for a in artists]
        else:
            tags["ARTIST"] = ""

    if not settings.no_composer_tag:
        composers = []
        performers_str = qobuz_item.get("performers", "")

        if performers_str:
            for performer_block in performers_str.split(" - "):
                parts = _split_protegido(performer_block, ",", protegidos)
                if len(parts) > 1:
                    name = parts[0]
                    roles = parts[1:]

                    if "Composer" in roles or "ComposerLyricist" in roles:
                        if name not in composers:
                            composers.append(name)

        if not composers:
            main_composer = qobuz_item.get("composer", {}).get("name", "")
            if main_composer:
                composers.append(main_composer)

        if len(composers) > 0:
            tags["COMPOSER"] = ", ".join(composers)
            multi_out["COMPOSER"] = list(composers)
        else:
            tags["COMPOSER"] = ""

    release_date = qobuz_album.get("release_date_original", "")
    if not settings.no_release_date_tag:
        tags["DATE"] = release_date
    if not settings.no_genre_tag:
        # # Substitui o gênero principal pelo equivalente LOCAL_GENRE_MAP antes de juntar a lista.
        raw_main_genre = qobuz_album.get("genre", {}).get("name")
        main_genre = (
            LOCAL_GENRE_MAP.get(raw_main_genre, raw_main_genre)
            if raw_main_genre
            else None
        )

        # Fallback: usa o último nome do primeiro caminho de genres.list
        if not main_genre:
            lista = qobuz_item.get("genres_list") or []
            primeiro = str(lista[0]).strip() if lista else ""
            primeiro = primeiro.split("\u2192")[-1].strip()
            main_genre = LOCAL_GENRE_MAP.get(primeiro, primeiro) or None

        if main_genre:
            tags["GENRE"] = main_genre
            multi_out["GENRE"] = [main_genre]
    if not settings.no_copyright_tag:
        tags["COPYRIGHT"] = _format_copyright(qobuz_album.get("copyright", "n/a"))
    if not settings.no_label_tag:
        tags["LABEL"] = re.sub(
            r"\s+", " ", qobuz_album.get("label", {}).get("name", "")
        )
    if not settings.no_isrc_tag:
        tags["ISRC"] = qobuz_item.get("isrc", "")
    if not settings.no_upc_tag:
        tags["BARCODE"] = qobuz_album.get("upc", "")

    if not settings.no_media_type_tag:
        tags["MEDIATYPE"] = qobuz_album.get("product_type", "").upper()
    if not settings.no_explicit_tag:
        tags["ITUNESADVISORY"] = "1" if is_explicit_track else ""
        # EXPLICIT E RATING garantem suporte do icone nativo E em players FLAC
        tags["EXPLICIT"] = "1" if is_explicit_track else ""
        tags["RATING"] = "Explicit" if is_explicit_track else ""

    # BUGFIX: antes lia qobuz_album.get("release_type") cru -- o mesmo
    # campo que classify_release_type() existe justamente pra corrigir
    # (a API erra esse campo com frequência). Isso podia deixar a tag
    # COMPILATION do arquivo em desacordo com a pasta onde ele é salvo
    # (que já usa a versão corrigida via format_release_type() em
    # downloader.py) -- uma coletânea podia ir pra pasta "Compilation/"
    # e mesmo assim não ganhar COMPILATION=1 no arquivo, ou vice-versa.
    release_type = classify_release_type(
        title=qobuz_album.get("title"),
        version=qobuz_album.get("version"),
        track_count=qobuz_album.get("tracks_count", 0),
        duration_seconds=qobuz_album.get("duration", 0),
        api_release_type=qobuz_album.get("release_type"),
    )
    if _eh_various_artists(qobuz_album, settings) or release_type == "compilation":
        tags["COMPILATION"] = "1"

    if not getattr(settings, "no_replaygain_tag", False):
        audio_info = qobuz_item.get("audio_info", {})
        if audio_info:
            rg_gain = audio_info.get("replaygain_track_gain")
            rg_peak = audio_info.get("replaygain_track_peak")

            if rg_gain is not None:
                tags["REPLAYGAIN_TRACK_GAIN"] = f"{rg_gain} dB"
            if rg_peak is not None:
                tags["REPLAYGAIN_TRACK_PEAK"] = str(rg_peak)

            rg_album_gain = audio_info.get("replaygain_album_gain")
            rg_album_peak = audio_info.get("replaygain_album_peak")
            if rg_album_gain is not None:
                tags["REPLAYGAIN_ALBUM_GAIN"] = f"{rg_album_gain} dB"
            if rg_album_peak is not None:
                tags["REPLAYGAIN_ALBUM_PEAK"] = str(rg_album_peak)

    work = qobuz_item.get("work")
    if work and not getattr(settings, "no_work_tag", False):
        tags["WORK"] = work

    conductors = []
    ensembles = []
    performers_str = qobuz_item.get("performers", "")

    if performers_str:
        for performer_block in performers_str.split(" - "):
            parts = _split_protegido(performer_block, ",", protegidos)
            if len(parts) > 1:
                name = parts[0]
                roles = parts[1:]

                if "Conductor" in roles:
                    conductors.append(name)
                if any(role in roles for role in ["Orchestra", "Ensemble", "Choir"]):
                    ensembles.append(name)

    if not getattr(settings, "no_credits_tag", False):
        for _tag, _nomes in _extract_credits(performers_str, protegidos).items():
            tags[_tag] = ", ".join(_nomes)
            multi_out[_tag] = list(_nomes)

    if conductors and not getattr(settings, "no_conductor_tag", False):
        tags["CONDUCTOR"] = conductors if len(conductors) > 1 else conductors[0]
    if ensembles and not getattr(settings, "no_ensemble_tag", False):
        tags["ENSEMBLE"] = ensembles if len(ensembles) > 1 else ensembles[0]

    # # IDs são persistidos para sincronização posterior com o banco de dados.
    track_id = qobuz_item.get("id")
    if track_id:
        tags["QOBUZTRACKID"] = str(track_id)

    album_id = qobuz_album.get("id")
    if album_id:
        tags["QOBUZALBUMID"] = str(album_id)

    if not getattr(settings, "no_album_url_tag", False):
        if album_id:
            raw_title = str(qobuz_album.get("title", "album"))
            slug = re.sub(r"[^a-z0-9]+", "-", raw_title.lower()).strip("-")
            tags["QOBUZ ALBUM URL"] = f"https://www.qobuz.com/album/{slug}/{album_id}"

    # IDs do MusicBrainz (modo --musicbrainz): so' chegam aqui se a busca
    # estrita confirmou titulo, versao, album e artista.
    if musicbrainz_ids:
        if musicbrainz_ids.get("track"):
            tags["MUSICBRAINZ_TRACKID"] = str(musicbrainz_ids["track"])
        if musicbrainz_ids.get("album"):
            tags["MUSICBRAINZ_ALBUMID"] = str(musicbrainz_ids["album"])
        _ids_artistas = [str(a) for a in musicbrainz_ids.get("artists") or [] if a]
        if _ids_artistas:
            tags["MUSICBRAINZ_ARTISTID"] = (
                _ids_artistas if len(_ids_artistas) > 1 else _ids_artistas[0]
            )

    for _k in MULTI_VALUE_FIELDS:
        _v = tags.get(_k)
        if isinstance(_v, (list, tuple)):
            multi_out[_k] = [str(p).strip() for p in _v if str(p).strip()]
            tags[_k] = ", ".join(multi_out[_k])

    return tags
