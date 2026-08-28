# # ============================================================================
# # metadata.py -- normalização, tagging, arte e informações de áudio.
# # Fluxo principal: _get_tags_to_add() cria tags; tag_flac()/tag_mp3() grava-as.
# # Também cuida de capas embutidas, gêneros, IDs Qobuz, ReplayGain e tags clássicas.
# # ============================================================================
import re
import os
import io
import logging
import unicodedata
import humanize

from mutagen.flac import FLAC, Picture
import mutagen.id3 as id3
from mutagen.id3 import ID3NoHeaderError
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.utils import get_album_artist

logger = logging.getLogger(__name__)


COPYRIGHT, PHON_COPYRIGHT = "\u2117", "\u00a9"
# # Limite máximo de um bloco de metadados FLAC: 0xFFFFFF bytes.
FLAC_MAX_BLOCKSIZE = 16777215

# # Limite máximo de um bloco de metadados FLAC: 0xFFFFFF bytes.
ID3_LEGEND = {
    "albumartist": id3.TPE2,
    "album": id3.TALB,
    "artist": id3.TPE1,
    "title": id3.TIT2,
    "date": id3.TDAT,
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
    "QOBUZ TRACK ID": id3.TXXX,
    "QOBUZ ALBUM ID": id3.TXXX,
    "QOBUZ ALBUM URL": id3.TXXX,
    "replaygain_track_gain": id3.TXXX,
    "replaygain_track_peak": id3.TXXX,
    "conductor": id3.TPE3,
    "ensemble": id3.TXXX,
    "work": id3.TIT1,
}

EMB_COVER_NAME = "embed_cover.jpg"

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
            return name[len(art):].strip() + ", " + art.strip()
    return name


# # Acrescenta a versão sem duplicá-la quando ela já faz parte do título.
def _get_title_with_version(title: str = "", version: str = "") -> str:
    item_title = title
    if version:
        item_title = (
            f"{title} ({version})" if version.lower() not in title.lower() else title
        )
    return item_title


# # Monta o título de uma faixa, incluindo versão e obra clássica quando existir.
def _get_title(track_dict):
    title = track_dict["title"]
    version = track_dict.get("version")
    if version:
        title = f"{title} ({version})"
    if track_dict.get("work"):
        title = f"{track_dict['work']}: {title}"

    return title


# # Converte marcadores (P)/(C) nos símbolos Unicode usados nas tags.
def _format_copyright(s: str) -> str:
    if s:
        s = s.replace("(P)", PHON_COPYRIGHT)
        s = s.replace("(C)", COPYRIGHT)
    return s


# # Remove caminhos/setas de gênero e elimina gêneros repetidos.
def _format_genres(genres: list) -> str:
    genres = re.findall(r"([^\u2192\/]+)", "/".join(genres))
    no_repeats = []
    [no_repeats.append(g) for g in genres if g not in no_repeats]
    return ", ".join(no_repeats)


# # Procura a imagem temporária de embed na pasta atual e na pasta pai.
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
    except ImportError:
        logger.warning(
            "Pillow nao esta instalado -- nao e' possivel recompactar a capa "
            "que excede o limite de 16MB do FLAC. Instale com: pip install Pillow"
        )
        return None

    try:
        with Image.open(image_path) as src:
            if src.mode not in ("RGB", "L"):
                src = src.convert("RGB")

            for quality in (95, 90, 85, 80, 75, 70, 60, 50):
                buf = io.BytesIO()
                src.save(buf, format="JPEG", quality=quality, optimize=True)
                if buf.tell() <= max_bytes:
                    return buf.getvalue()

            width, height = src.size
            scale = 0.9
            while scale > 0.19:
                new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
                resized = src.resize(new_size, Image.LANCZOS)
                buf = io.BytesIO()
                resized.save(buf, format="JPEG", quality=85, optimize=True)
                if buf.tell() <= max_bytes:
                    return buf.getvalue()
                scale -= 0.1

            return None
    except Exception as e:
        logger.error(f"Falha ao recompactar a capa: {e}", exc_info=True)
        return None


# # Embute a capa no FLAC; se necessário, usa uma cópia recompactada sem alterar cover.jpg.
def _embed_flac_img(root_dir, audio: FLAC, cover_override=None):
    cover_image = _get_cover_path(root_dir, override=cover_override)

    if not cover_image or not os.path.isfile(cover_image):
        logger.debug("Cover image not found to embed.")
        return

    try:
        original_size = os.path.getsize(cover_image)
        image_data = None

    # # O arquivo salvo continua original; somente os bytes enviados ao embed são reduzidos.
        if original_size > FLAC_MAX_BLOCKSIZE:
            logger.info(
                f"Capa ({humanize.naturalsize(original_size, binary=True)}) excede o limite de "
                f"16MB de embed do FLAC -- recompactando so' o suficiente pra caber "
                f"(o arquivo salvo em disco continua em qualidade original)."
            )
            image_data = _shrink_image_to_fit(cover_image, FLAC_MAX_BLOCKSIZE)
            if image_data is None:
                raise Exception(
                    "capa muito grande pra embutir e a recompactacao automatica "
                    "falhou (Pillow ausente ou imagem ilegivel) -- pulando embed "
                    "dessa faixa, mas o download continua normalmente."
                )
        else:
            with open(cover_image, "rb") as img:
                image_data = img.read()

        image = Picture()
        image.type = 3
        image.mime = "image/jpeg"
        image.desc = "cover"
        image.data = image_data
        audio.add_picture(image)
    except Exception as e:
        logger.error(f"Error embedding image: {e}", exc_info=True)


# # Adiciona a capa como frame APIC no ID3 do MP3.
def _embed_id3_img(root_dir, audio: id3.ID3, cover_override=None):
    cover_image = _get_cover_path(root_dir, override=cover_override)

    if not cover_image or not os.path.isfile(cover_image):
        logger.debug("Cover image not found to embed.")
        return

    with open(cover_image, "rb") as cover:
        audio.add(id3.APIC(3, "image/jpeg", 3, "", cover.read()))


# # Aplica tags Vorbis, comentário técnico, capa e salva o FLAC final.
def tag_flac(
    filename,
    root_dir,
    final_name,
    d: dict,
    album,
    istrack=True,
    em_image=False,
    settings: QobuzDLSettings = None,
    embed_cover_path=None,
    musicbrainz_ids=None,
):
    audio = FLAC(filename)

    if istrack:
        qobuz_item = d
        qobuz_album = d.get("album", {})
    else:
        qobuz_item = d
        qobuz_album = album

    # # Centraliza a montagem para manter FLAC e MP3 com o mesmo conteúdo lógico.
    tags = _get_tags_to_add(
        qobuz_album, qobuz_item, settings=settings, musicbrainz_ids=musicbrainz_ids
    )

    if not settings.no_track_number_tag:
        tags["TRACKNUMBER"] = str(qobuz_item.get("track_number", "1"))
    if not settings.no_track_total_tag:
        tags["TRACKTOTAL"] = str(qobuz_album.get("tracks_count", "1"))
    if not settings.no_disc_number_tag:
        tags["DISCNUMBER"] = str(qobuz_item.get("media_number", "1"))
    if not settings.no_disc_total_tag:
        tags["DISCTOTAL"] = str(qobuz_album.get("media_count", "1"))

    _bit = qobuz_item.get("maximum_bit_depth", 16)
    _rate = qobuz_item.get("maximum_sampling_rate", 44.1)

    _ch = qobuz_item.get("maximum_channel_count", 2)
    _ch_map = {1: "Mono", 2: "Stereo", 4: "4.0", 6: "5.1", 8: "7.1"}
    _channels = _ch_map.get(_ch, f"{_ch}ch")

    _hires = "sim" if qobuz_item.get("hires_streamable") else "nao"

    _dur_s = int(qobuz_item.get("duration") or 0)
    _duration = f"{_dur_s // 60}:{_dur_s % 60:02d}" if _dur_s else "?"

    _rtype = (qobuz_album.get("release_type") or "album").lower()

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
        cover_path = _get_cover_path(root_dir, override=embed_cover_path)
        if cover_path:
            img_size_bytes = os.path.getsize(cover_path)
            req_size = getattr(settings, "embedded_art_size", "unknown")
            is_org = "YES" if req_size == "org" else "NO"
            base_comment += f" | Cover: {humanize.naturalsize(img_size_bytes, binary=True)} (Req: {req_size}, Org: {is_org})"

    tags["COMMENT"] = base_comment

    # # Só grava valores preenchidos; multi_value_tags troca separadores por " ; ".
    for k, v in tags.items():
        if v:
            if (
                getattr(settings, "multi_value_tags", False) and
                k
                in [
                    "ARTIST",
                    "ARTISTSORT",
                    "ALBUMARTIST",
                    "ALBUMARTISTSORT",
                    "COMPOSER",
                    "GENRE",
                ] and
                isinstance(v, str)
            ):
                if ", " in v:
                    v = v.replace(", ", " ; ")
            audio[k] = v

    if em_image:
        _embed_flac_img(root_dir, audio, cover_override=embed_cover_path)

    for junk_tag in ["ENCODER", "ENCODED-BY", "ENCODED_BY"]:
        if junk_tag in audio:
            del audio[junk_tag]

    if hasattr(audio, "tags") and audio.tags is not None:
        audio.tags.vendor = ""

    audio.save(padding=lambda info: 8192)
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
    settings: QobuzDLSettings = None,
    embed_cover_path=None,
    musicbrainz_ids=None,
):
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
    tags = _get_tags_to_add(
        qobuz_album, qobuz_item, settings=settings, musicbrainz_ids=musicbrainz_ids
    )

    _bit = qobuz_item.get("maximum_bit_depth", 16)
    _rate = qobuz_item.get("maximum_sampling_rate", 44.1)

    _ch = qobuz_item.get("maximum_channel_count", 2)
    _ch_map = {1: "Mono", 2: "Stereo", 4: "4.0", 6: "5.1", 8: "7.1"}
    _channels = _ch_map.get(_ch, f"{_ch}ch")

    _hires = "sim" if qobuz_item.get("hires_streamable") else "nao"

    _dur_s = int(qobuz_item.get("duration") or 0)
    _duration = f"{_dur_s // 60}:{_dur_s % 60:02d}" if _dur_s else "?"

    _rtype = (qobuz_album.get("release_type") or "album").lower()

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
        cover_path = _get_cover_path(root_dir, override=embed_cover_path)
        if cover_path:
            img_size_bytes = os.path.getsize(cover_path)
            req_size = getattr(settings, "embedded_art_size", "unknown")
            is_org = "YES" if req_size == "org" else "NO"
            base_comment += f" | Cover: {humanize.naturalsize(img_size_bytes, binary=True)} (Req: {req_size}, Org: {is_org})"

    tags["COMMENT"] = base_comment

    # # Só grava valores preenchidos; multi_value_tags troca separadores por " ; ".
    for k, v in tags.items():
        if v:
            if (
                getattr(settings, "multi_value_tags", False) and
                k
                in [
                    "ARTIST",
                    "ARTISTSORT",
                    "ALBUMARTIST",
                    "ALBUMARTISTSORT",
                    "COMPOSER",
                    "GENRE",
                ] and
                isinstance(v, str)
            ):
                if ", " in v:
                    v = v.replace(", ", " ; ")

            id3tag = ID3_LEGEND.get(k.lower()) or ID3_LEGEND.get(k)
            if id3tag:
                if id3tag == id3.TXXX:
                    audio.add(id3tag(encoding=3, desc=k, text=v))
                elif id3tag == id3.COMM:
                    audio.add(id3tag(encoding=3, lang="eng", desc="", text=[v]))
                else:
                    audio[id3tag.__name__] = id3tag(encoding=3, text=v)

    _trck_n = qobuz_item.get("track_number", "1")
    _trck_total = qobuz_album.get("tracks_count", "1")
    # # TRCK/TPOS usam o formato número/total reconhecido por players.
    audio["TRCK"] = id3.TRCK(encoding=3, text=f"{_trck_n}/{_trck_total}")

    _tpos_n = qobuz_item.get("media_number", "1")
    _tpos_total = qobuz_album.get("media_count", "1")
    audio["TPOS"] = id3.TPOS(encoding=3, text=f"{_tpos_n}/{_tpos_total}")

    if em_image:
        _embed_id3_img(root_dir, audio, cover_override=embed_cover_path)

    audio.pop("TENC", None)
    audio.pop("TSSE", None)

    audio.save(filename, v2_version=3)
    os.rename(filename, final_name)


# # Constrói o dicionário unificado de tags a partir dos metadados Qobuz.
def _get_tags_to_add(
    qobuz_album: dict,
    qobuz_item: dict,
    settings: QobuzDLSettings = None,
    musicbrainz_ids=None,
):
    tags = dict()
    if not qobuz_album or not qobuz_item:
        return tags

    if not settings.no_album_title_tag:
        tags["ALBUM"] = _get_title_with_version(
            title=qobuz_album.get("title", ""), version=qobuz_album.get("version", "")
        )
    if not settings.no_track_title_tag:
        tags["TITLE"] = _get_title_with_version(
            title=qobuz_item.get("title", ""), version=qobuz_item.get("version", "")
        )

    if not settings.no_album_artist_tag:
        _albumartist_val = get_album_artist(qobuz_album)
        tags["ALBUMARTIST"] = _albumartist_val
        tags["ALBUMARTISTSORT"] = _make_sort_name(_albumartist_val)

    # # Deduplica artistas por nome normalizado e preserva a ordem recebida.
    if not settings.no_track_artist_tag:
        artists = []
        seen_artists = set()

        def add_unique_artist(name):
            if not name:
                return
            norm_name = _normalize_name(name)
            if norm_name and norm_name not in seen_artists:
                seen_artists.add(norm_name)
                artists.append(name)

        main_artist_raw = qobuz_item.get("performer", {}).get(
            "name", ""
        ) or qobuz_album.get("artist", {}).get("name", "")

        if main_artist_raw:
            for part in main_artist_raw.split(","):
                add_unique_artist(part.strip())

        performers_str = qobuz_item.get("performers", "")
        if performers_str:
            for performer_block in performers_str.split(" - "):
                parts = [p.strip() for p in performer_block.split(", ")]
                if len(parts) > 1:
                    name = parts[0]
                    roles = parts[1:]

                    if (
                        "FeaturedArtist" in roles or
                        "MainArtist" in roles or
                        "PrimaryArtist" in roles
                    ):
                        add_unique_artist(name)

        if len(artists) > 0:
            tags["ARTIST"] = ", ".join(artists)
            tags["ARTISTSORT"] = ", ".join(_make_sort_name(a) for a in artists)
        else:
            tags["ARTIST"] = ""

    if not settings.no_composer_tag:
        composers = []
        performers_str = qobuz_item.get("performers", "")

        if performers_str:
            for performer_block in performers_str.split(" - "):
                parts = [p.strip() for p in performer_block.split(", ")]
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

        raw_genres = qobuz_album.get("genres_list", [])
        if main_genre:
            if raw_genres:
                raw_genres[0] = main_genre
            else:
                raw_genres = [main_genre]

        extracted_genres = re.findall(r"([^\u2192]+)", " \u2192 ".join(raw_genres))

        final_genres = []
        for g in extracted_genres:
            clean_g = g.strip()
            translated = LOCAL_GENRE_MAP.get(clean_g, clean_g)
            if translated not in final_genres:
                final_genres.append(translated)

        tags["GENRE"] = ", ".join(final_genres)
    if not settings.no_label_tag:
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
        tags["ITUNESADVISORY"] = (
            "1" if qobuz_item.get("parental_warning", False) else ""
        )

    release_type = qobuz_album.get("release_type", "") or ""
    if release_type.lower() == "compilation":
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
            parts = [p.strip() for p in performer_block.split(", ")]
            if len(parts) > 1:
                name = parts[0]
                roles = parts[1:]

                if "Conductor" in roles:
                    conductors.append(name)
                if any(role in roles for role in ["Orchestra", "Ensemble", "Choir"]):
                    ensembles.append(name)

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

    return tags
