"""Normalização de metadados para a GUI (sem dependências além da stdlib).

Fica separado de webapp.py de propósito: é lógica pura (dicts entram, dicts
saem), então dá pra testar sem FastAPI/Uvicorn instalados. Tudo aqui é
defensivo -- o Qobuz omite campos conforme o tipo de item, então nada assume
que uma chave existe.
"""

from __future__ import annotations

import configparser
import re
from typing import Any

# ---------------------------------------------------------------- capas


def _pick_image(image: Any) -> str | None:
    if isinstance(image, str) and image.startswith(("https://", "http://")):
        return image
    if isinstance(image, list):
        for value in image:
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
    if isinstance(image, dict):
        for key in ("mega", "extralarge", "large", "org", "medium", "small", "thumbnail"):
            value = image.get(key)
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
        for value in image.values():
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
    return None


_SIZE_SUFFIX = re.compile(r"_(?:\d{2,4}|org)\.(jpg|jpeg|png|webp)(\?.*)?$", re.I)


def cover_pair(item: dict[str, Any]) -> tuple[str | None, str | None]:
    """(miniatura 600px, original _org). Miniatura leve para grades e listas;
    original para capa grande (player, página do álbum)."""
    url = None
    for key in ("image", "cover", "images300", "images", "image_rectangle", "picture"):
        url = _pick_image(item.get(key))
        if url:
            break
    if not url and isinstance(item.get("album"), dict):
        return cover_pair(item["album"])
    if not url:
        return None, None
    if _SIZE_SUFFIX.search(url):
        return (
            _SIZE_SUFFIX.sub(r"_600.\1\2", url),
            _SIZE_SUFFIX.sub(r"_org.\1\2", url),
        )
    return url, url


# ---------------------------------------------------------------- artistas e créditos

ROLE_LABELS = {
    "mainartist": "Artista principal",
    "featuredartist": "Participação",
    "associatedperformer": "Intérprete",
    "performer": "Intérprete",
    "vocals": "Vocais",
    "vocalist": "Vocais",
    "backgroundvocals": "Backing vocals",
    "producer": "Produção",
    "coproducer": "Co-produção",
    "executiveproducer": "Produção executiva",
    "composer": "Compositor",
    "composerlyricist": "Compositor e letrista",
    "lyricist": "Letrista",
    "author": "Autor",
    "writer": "Autor",
    "arranger": "Arranjo",
    "orchestrator": "Orquestração",
    "conductor": "Regência",
    "mixer": "Mixagem",
    "mixingengineer": "Mixagem",
    "masteringengineer": "Masterização",
    "masterer": "Masterização",
    "recordingengineer": "Gravação",
    "engineer": "Engenharia de som",
    "studiopersonnel": "Equipe de estúdio",
    "programmer": "Programação",
    "guitar": "Guitarra",
    "bass": "Baixo",
    "drums": "Bateria",
    "piano": "Piano",
    "keyboards": "Teclados",
    "synthesizer": "Sintetizador",
    "strings": "Cordas",
    "violin": "Violino",
    "cello": "Violoncelo",
    "saxophone": "Saxofone",
    "trumpet": "Trompete",
    "percussion": "Percussão",
    "label": "Selo",
    "publisher": "Editora",
}


def role_label(role: str) -> str:
    key = re.sub(r"[^a-z]", "", role.lower())
    if key in ROLE_LABELS:
        return ROLE_LABELS[key]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", role.strip())


def parse_performers(text: Any) -> list[dict[str, Any]]:
    """'Nome, Papel, Papel - Outro, Papel' -> [{name, roles:[...]}]."""
    people: list[dict[str, Any]] = []
    if not isinstance(text, str) or not text.strip():
        return people
    for chunk in re.split(r"\s+-\s+", text):
        parts = [p.strip() for p in chunk.split(",") if p.strip()]
        if not parts:
            continue
        name, roles = parts[0], parts[1:]
        existing = next((p for p in people if p["name"].lower() == name.lower()), None)
        if existing:
            for role in roles:
                if role not in existing["roles"]:
                    existing["roles"].append(role)
        else:
            people.append({"name": name, "roles": roles})
    return people


def _name_id(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and value.get("name"):
        return {"id": value.get("id"), "name": str(value["name"])}
    if isinstance(value, str) and value.strip():
        return {"id": None, "name": value.strip()}
    return None


def artists_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Todos os artistas principais, na ordem, sem repetir."""
    found: list[dict[str, Any]] = []

    def add(entry: Any) -> None:
        parsed = _name_id(entry)
        if parsed and all(parsed["name"].lower() != x["name"].lower() for x in found):
            found.append(parsed)

    arts = item.get("artists")
    if isinstance(arts, list):
        mains = [
            a
            for a in arts
            if isinstance(a, dict)
            and any(
                re.sub(r"[^a-z]", "", str(r).lower()) in {"mainartist", "featuredartist"}
                for r in (a.get("roles") or ["main-artist"])
            )
        ]
        for a in mains or arts:
            add(a)
    if not found:
        people = parse_performers(item.get("performers"))
        for p in people:
            if any(re.sub(r"[^a-z]", "", r.lower()) == "mainartist" for r in p["roles"]):
                add({"id": None, "name": p["name"]})
    for key in ("performer", "artist"):
        if not found:
            add(item.get(key))
    return found


def credits_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Créditos agrupados por função: [{role, people:[{name}]}]."""
    people = parse_performers(item.get("performers"))
    composer = _name_id(item.get("composer"))
    if composer and all(composer["name"].lower() != p["name"].lower() for p in people):
        people.append({"name": composer["name"], "roles": ["Composer"]})
    elif composer:
        for p in people:
            if p["name"].lower() == composer["name"].lower() and "Composer" not in p["roles"]:
                p["roles"].append("Composer")
    groups: dict[str, list[str]] = {}
    for p in people:
        for role in p["roles"] or ["Performer"]:
            label = role_label(role)
            names = groups.setdefault(label, [])
            if p["name"] not in names:
                names.append(p["name"])
    return [{"role": role, "people": [{"name": n} for n in names]} for role, names in groups.items()]


def merge_credits(groups: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    merged: dict[str, list[str]] = {}
    for credits in groups:
        for group in credits:
            names = merged.setdefault(group["role"], [])
            for person in group["people"]:
                if person["name"] not in names:
                    names.append(person["name"])
    order = ["Artista principal", "Participação", "Compositor", "Letrista", "Produção", "Mixagem", "Masterização"]
    rank = {r: i for i, r in enumerate(order)}
    return [
        {"role": r, "people": [{"name": n} for n in merged[r]]}
        for r in sorted(merged, key=lambda k: (rank.get(k, 99), k))
    ]


# ---------------------------------------------------------------- formatação


def quality_label(item: dict[str, Any]) -> str:
    depth = item.get("maximum_bit_depth") or item.get("bit_depth")
    rate = item.get("maximum_sampling_rate") or item.get("sampling_rate")
    if depth and rate:
        rate_text = f"{rate:g}" if isinstance(rate, (int, float)) else str(rate)
        kind = "Hi-Res" if (depth or 0) > 16 or (rate or 0) > 44.1 else "FLAC"
        return f"{kind} {depth}-bit / {rate_text} kHz"
    return "FLAC 16-bit / 44.1 kHz" if item.get("streamable") else ""


def _date(item: dict[str, Any]) -> str:
    for key in ("release_date_original", "release_date_stream", "release_date_download"):
        if item.get(key):
            return str(item[key])
    stamp = item.get("released_at")
    if isinstance(stamp, (int, float)) and stamp > 0:
        import datetime as _dt

        return _dt.datetime.fromtimestamp(stamp, _dt.timezone.utc).strftime("%Y-%m-%d")
    return ""


def _title(item: dict[str, Any]) -> str:
    title = str(item.get("title") or item.get("name") or "").strip()
    version = str(item.get("version") or "").strip()
    return f"{title} ({version})" if version and version.lower() not in title.lower() else title


def _names(value: Any) -> dict[str, Any] | None:
    return _name_id(value)


def track_view(item: dict[str, Any], album: dict[str, Any] | None = None) -> dict[str, Any]:
    album = album if isinstance(album, dict) else (item.get("album") if isinstance(item.get("album"), dict) else {})
    thumb, org = cover_pair(item if item.get("image") else {"album": album} if album else item)
    arts = artists_of(item) or artists_of(album)
    return {
        "id": str(item.get("id", "")),
        "title": _title(item),
        "artists": arts,
        "artist": ", ".join(a["name"] for a in arts),
        "album": str(album.get("title") or ""),
        "albumId": str(album.get("id") or "") or None,
        "trackNumber": item.get("track_number"),
        "discNumber": item.get("media_number"),
        "duration": int(item.get("duration") or 0),
        "cover": thumb,
        "coverOrg": org,
        "quality": quality_label(item) or quality_label(album),
        "isrc": str(item.get("isrc") or ""),
        "explicit": bool(item.get("parental_warning")),
        "composer": _names(item.get("composer")),
        "copyright": str(item.get("copyright") or album.get("copyright") or ""),
        "work": str(item.get("work") or ""),
        "credits": credits_of(item),
        "streamable": item.get("streamable", True),
    }


def album_view(item: dict[str, Any]) -> dict[str, Any]:
    thumb, org = cover_pair(item)
    arts = artists_of(item)
    label = _names(item.get("label"))
    genre = _names(item.get("genre"))
    awards = [
        {"name": str(a.get("name") or ""), "year": str(a.get("awarded_at") or "")[:4]}
        for a in (item.get("awards") or [])
        if isinstance(a, dict) and a.get("name")
    ]
    date = _date(item)
    kind = str(item.get("release_type") or item.get("product_type") or "").lower()
    return {
        "id": str(item.get("id", "")),
        "title": _title(item),
        "artists": arts,
        "artist": ", ".join(a["name"] for a in arts),
        "year": date[:4],
        "releaseDate": date,
        "genre": genre["name"] if genre else "",
        "label": label["name"] if label else "",
        "labelId": label["id"] if label else None,
        "tracks_count": item.get("tracks_count") or 0,
        "discs": item.get("media_count") or 1,
        "duration": int(item.get("duration") or 0),
        "quality": quality_label(item),
        "hires": bool(item.get("hires") or item.get("hires_streamable")),
        "cover": thumb,
        "coverOrg": org,
        "type": kind,
        "upc": str(item.get("upc") or ""),
        "copyright": str(item.get("copyright") or ""),
        "description": str(item.get("description") or ""),
        "awards": awards,
        "explicit": bool(item.get("parental_warning")),
        "url": str(item.get("url") or ""),
    }


def playlist_view(item: dict[str, Any]) -> dict[str, Any]:
    thumb, org = cover_pair(item)
    owner = _names(item.get("owner"))
    return {
        "id": str(item.get("id", "")),
        "title": str(item.get("name") or item.get("title") or ""),
        "description": str(item.get("description") or ""),
        "owner": owner["name"] if owner else "",
        "tracks_count": item.get("tracks_count") or 0,
        "duration": int(item.get("duration") or 0),
        "cover": thumb,
        "coverOrg": org,
        "public": bool(item.get("is_public")),
        "updated": item.get("updated_at") or item.get("created_at") or 0,
    }


def artist_view(item: dict[str, Any]) -> dict[str, Any]:
    thumb, org = cover_pair(item)
    bio = item.get("biography") if isinstance(item.get("biography"), dict) else {}
    return {
        "id": str(item.get("id", "")),
        "name": str(item.get("name") or ""),
        "cover": thumb,
        "coverOrg": org,
        "albums_count": item.get("albums_count") or 0,
        "biography": str(bio.get("content") or bio.get("summary") or item.get("information") or ""),
        "category": str(item.get("artist_category") or ""),
    }


# ---------------------------------------------------------------- HTML seguro (descrição / biografia)

_ALLOWED_TAGS = {"a", "b", "strong", "i", "em", "br", "p", "ul", "ol", "li"}


def clean_html(html: str) -> str:
    """Mantém só formatação básica e links http(s). Sem scripts, estilos ou
    atributos além de href (que vira target=_blank rel=noopener)."""
    from html.parser import HTMLParser

    out: list[str] = []

    class P(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag not in _ALLOWED_TAGS:
                return
            if tag == "a":
                href = dict(attrs).get("href") or ""
                if re.match(r"^https?://", href, re.I):
                    safe = href.replace('"', "%22")
                    out.append(f'<a href="{safe}" target="_blank" rel="noopener noreferrer">')
                else:
                    out.append("<a>")
            else:
                out.append(f"<{tag}>")

        def handle_endtag(self, tag):
            if tag in _ALLOWED_TAGS and tag != "br":
                out.append(f"</{tag}>")

        def handle_data(self, data):
            out.append(data.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

    try:
        P(convert_charrefs=True).feed(html or "")
    except Exception:
        return ""
    return "".join(out)


# ---------------------------------------------------------------- preferências avançadas (config.ini)

# (chave no config.ini, tipo, grupo, rótulo, dica)
_TAGS = [
    ("no_album_artist_tag", "Artista do álbum"), ("no_album_title_tag", "Título do álbum"),
    ("no_track_artist_tag", "Artista da faixa"), ("no_track_title_tag", "Título da faixa"),
    ("no_release_date_tag", "Data de lançamento"), ("no_media_type_tag", "Tipo de mídia"),
    ("no_genre_tag", "Gênero"), ("no_track_number_tag", "Número da faixa"),
    ("no_track_total_tag", "Total de faixas"), ("no_disc_number_tag", "Número do disco"),
    ("no_disc_total_tag", "Total de discos"), ("no_composer_tag", "Compositor"),
    ("no_conductor_tag", "Regente"), ("no_ensemble_tag", "Conjunto"), ("no_work_tag", "Obra"),
    ("no_explicit_tag", "Conteúdo explícito"), ("no_copyright_tag", "Copyright"),
    ("no_label_tag", "Selo"), ("no_upc_tag", "UPC"), ("no_isrc_tag", "ISRC"),
    ("no_replaygain_tag", "ReplayGain"), ("no_album_url_tag", "URL do álbum"),
]

ADVANCED_SCHEMA: list[dict[str, Any]] = (
    [
        {"key": "og_cover", "type": "bool", "group": "Downloads", "label": "Capa em resolução original (_org)", "hint": "Baixa a capa no tamanho máximo do Qobuz."},
        {"key": "albums_only", "type": "bool", "group": "Downloads", "label": "Só álbuns completos", "hint": "Ignora singles e EPs em discografias."},
        {"key": "no_database", "type": "bool", "group": "Downloads", "label": "Não usar o banco de downloads", "hint": "Baixa de novo mesmo o que já foi baixado antes."},
        {"key": "write_sentinel", "type": "bool", "group": "Downloads", "label": "Marcar álbuns concluídos", "hint": "Grava um marcador na pasta quando o álbum termina."},
        {"key": "multiple_disc_prefix", "type": "text", "group": "Downloads", "label": "Prefixo de disco", "hint": "Ex.: CD, Disc."},
        {"key": "multiple_disc_one_dir", "type": "bool", "group": "Downloads", "label": "Todos os discos na mesma pasta"},
        {"key": "fallback_folder_format", "type": "text", "group": "Nomes", "label": "Padrão de pasta alternativo"},
        {"key": "default_limit", "type": "int", "group": "Downloads", "label": "Limite padrão de resultados", "min": 1, "max": 100},
        {"key": "only_synced_lyrics", "type": "bool", "group": "Letras", "label": "Só letras sincronizadas", "hint": "Ignora letras sem tempo."},
    ]
    + [
        {"key": key, "type": "bool", "group": "Tags nos arquivos", "label": f"Não gravar: {label}"}
        for key, label in _TAGS
    ]
)

_ADV_BY_KEY = {f["key"]: f for f in ADVANCED_SCHEMA}
_DEFAULTS = {"write_sentinel": True, "default_limit": 20, "multiple_disc_prefix": "CD"}


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def read_advanced(parser: configparser.ConfigParser, section: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in ADVANCED_SCHEMA:
        key, kind = field["key"], field["type"]
        raw = parser.get(section, key, fallback=None) if (section == "DEFAULT" or parser.has_section(section)) else None
        default = _DEFAULTS.get(key, False if kind == "bool" else 0 if kind == "int" else "")
        if raw is None or raw.strip() == "":
            values[key] = default
        elif kind == "bool":
            values[key] = _truthy(raw)
        elif kind == "int":
            try:
                values[key] = int(raw)
            except ValueError:
                values[key] = default
        else:
            values[key] = raw.strip()
    return values


def write_advanced(parser: configparser.ConfigParser, section: str, incoming: dict[str, Any]) -> dict[str, Any]:
    """Valida contra o esquema e grava no parser. Retorna o que foi aceito."""
    if not parser.has_section(section) and section != "DEFAULT":
        parser.add_section(section)
    accepted: dict[str, Any] = {}
    for key, value in incoming.items():
        field = _ADV_BY_KEY.get(key)
        if not field:
            continue
        kind = field["type"]
        if kind == "bool":
            parser.set(section, key, "true" if bool(value) else "false")
            accepted[key] = bool(value)
        elif kind == "int":
            number = max(field.get("min", 0), min(field.get("max", 10**6), int(value)))
            parser.set(section, key, str(number))
            accepted[key] = number
        else:
            text = str(value).strip()[:300]
            parser.set(section, key, text)
            accepted[key] = text
    return accepted
