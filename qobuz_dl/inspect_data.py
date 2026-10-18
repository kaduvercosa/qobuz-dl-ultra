"""Dados do `qobuz-dl inspect`: lê UM arquivo de áudio e devolve tudo organizado.

Este módulo não imprime nada (quem mostra é o `inspector.py`), então dá para
testar com arquivos de verdade e também usar na saída `--json`.

O que sai de `ler_arquivo(caminho)`:

* ``tecnico``  -- codec, qualidade, bitrate real, encoder, MD5 do FLAC...
* ``campos``   -- as tags que o programa entende (título, ISRC, ID Qobuz...),
                  iguais para FLAC, MP3, M4A e Ogg/Opus (cada formato chama a
                  mesma coisa de um jeito: ``TITLE``, ``TIT2``, ``©nam``).
* ``tags``     -- TODAS as tags como estão no arquivo (nada é descartado).
* ``capas``    -- cada imagem embutida (tipo, formato, dimensões, quadrada?).
* ``letras``   -- se há letra embutida, se é sincronizada e se há `.lrc` ao lado.
* ``problemas``-- os mesmos avisos do `tags --report` para esta faixa.
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import time
from typing import Optional

from qobuz_dl.library_report import ISSUES, MIN_COVER_SIDE, diagnose

AUDIO_EXTS = (
    ".flac",
    ".mp3",
    ".m4a",
    ".alac",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".ape",
    ".wv",
)

# Formatos "sem perdas" (o resto é com perdas).
_LOSSLESS = {"FLAC", "WAVE", "AIFF", "MonkeysAudio", "WavPack", "TrueAudio"}

_CODEC_NOMES = {
    "FLAC": "FLAC",
    "MP3": "MP3",
    "OggVorbis": "Ogg Vorbis",
    "OggOpus": "Opus",
    "OggFLAC": "Ogg FLAC",
    "WAVE": "WAV (PCM)",
    "AIFF": "AIFF (PCM)",
    "MonkeysAudio": "Monkey's Audio",
    "WavPack": "WavPack",
}

# nome canônico -> nomes que cada formato usa (minúsculos)
_ALIASES = {
    "titulo": ("title", "tit2", "©nam"),
    "artista": ("artist", "tpe1", "©art"),
    "albumartista": ("albumartist", "album artist", "album_artist", "tpe2", "aart"),
    "album": ("album", "talb", "©alb"),
    "data": ("date", "year", "originaldate", "tdrc", "tyer", "tdor", "©day"),
    "genero": ("genre", "tcon", "©gen"),
    "compositor": ("composer", "tcom", "©wrt"),
    "faixa": ("tracknumber", "track", "trck", "trkn"),
    "faixa_total": ("tracktotal", "totaltracks", "track_total"),
    "disco": ("discnumber", "disc", "tpos", "disk"),
    "disco_total": ("disctotal", "totaldiscs", "disc_total"),
    "tipo_midia": ("mediatype", "media", "tmed"),
    "isrc": ("isrc", "tsrc"),
    "upc": ("upc", "barcode"),
    "gravadora": ("label", "organization", "publisher", "tpub"),
    "copyright": ("copyright", "tcop", "cprt"),
    "track_id": ("qobuztrackid", "qobuz track id", "qobuz_track_id", "track_id"),
    "album_id": ("qobuzalbumid", "qobuz album id", "album_id"),
    "album_url": ("qobuz album url", "qobuz_album_url", "qobuzalbumurl"),
    "rg_faixa_ganho": ("replaygain_track_gain",),
    "rg_faixa_pico": ("replaygain_track_peak",),
    "rg_album_ganho": ("replaygain_album_gain",),
    "rg_album_pico": ("replaygain_album_peak",),
    "letra": ("lyrics", "unsyncedlyrics", "uslt", "©lyr"),
    "letra_sinc": ("syncedlyrics", "sylt"),
    "comentario": ("comment", "description", "comm", "©cmt"),
}
_REVERSE = {nome: canon for canon, nomes in _ALIASES.items() for nome in nomes}

# Grupos do relatório: (título, [(campo canônico, rótulo)]).
GRUPOS = [
    (
        "🎼 IDENTIFICAÇÃO",
        [
            ("titulo", "Título"),
            ("artista", "Artista"),
            ("albumartista", "Artista do álbum"),
            ("album", "Álbum"),
            ("data", "Data"),
            ("genero", "Gênero"),
            ("compositor", "Compositor"),
        ],
    ),
    (
        "🔢 FAIXA E DISCO",
        [("faixa", "Faixa"), ("disco", "Disco"), ("tipo_midia", "Tipo de mídia")],
    ),
    (
        "🏷️  CÓDIGOS E SELOS",
        [
            ("isrc", "ISRC"),
            ("upc", "Código de barras (UPC)"),
            ("gravadora", "Gravadora"),
            ("copyright", "Copyright"),
        ],
    ),
    (
        "🆔 IDS DO QOBUZ",
        [
            ("track_id", "ID da faixa"),
            ("album_id", "ID do álbum"),
            ("album_url", "URL do álbum"),
        ],
    ),
    (
        "🔊 VOLUME (REPLAYGAIN)",
        [
            ("rg_faixa_ganho", "Ganho da faixa"),
            ("rg_faixa_pico", "Pico da faixa"),
            ("rg_album_ganho", "Ganho do álbum"),
            ("rg_album_pico", "Pico do álbum"),
        ],
    ),
]
# Campos tratados em seções próprias (letras/comentário não entram nos grupos).
_CAMPOS_AGRUPADOS = {c for _, itens in GRUPOS for c, _ in itens} | {
    "faixa_total",
    "disco_total",
}

_TIPOS_CAPA = {0: "outra", 3: "frente", 4: "verso", 5: "encarte", 6: "disco"}
_LRC = re.compile(r"^\s*\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", re.MULTILINE)


def formatar_tamanho(num_bytes) -> str:
    """Tamanho legível: 1536 -> '1.5 KB'."""
    num_bytes = float(num_bytes)
    for unidade in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return (
                f"{int(num_bytes)} B"
                if unidade == "B"
                else f"{num_bytes:.1f} {unidade}"
            )
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def formatar_duracao(segundos) -> str:
    """HH:MM:SS (ou MM:SS abaixo de 1 hora)."""
    try:
        total = int(round(float(segundos)))
    except (TypeError, ValueError):
        return "?"
    horas, resto = divmod(total, 3600)
    minutos, seg = divmod(resto, 60)
    return f"{horas}:{minutos:02d}:{seg:02d}" if horas else f"{minutos}:{seg:02d}"


# --------------------------------------------------------------------------
# Tags
# --------------------------------------------------------------------------
def _chave_canonica(chave) -> Optional[str]:
    """'TITLE' / 'TIT2' / 'TXXX:ISRC' / '----:com.apple.iTunes:ISRC' -> 'titulo'..."""
    baixa = str(chave).strip().lower()
    if baixa.startswith("txxx:"):
        baixa = baixa[5:]
    elif baixa.startswith("----:"):
        baixa = baixa.rsplit(":", 1)[-1]
    elif baixa.split(":", 1)[0] in ("comm", "uslt", "sylt"):
        baixa = baixa.split(":", 1)[0]
    return _REVERSE.get(baixa)


def _texto(valor) -> str:
    if isinstance(valor, bytes):
        return valor.decode("utf-8", "replace")
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if (
        isinstance(valor, tuple)
        and len(valor) == 2
        and all(isinstance(v, int) for v in valor)
    ):
        return f"{valor[0]}/{valor[1]}" if valor[1] else str(valor[0])
    if isinstance(valor, (list, tuple)):
        return " / ".join(_texto(v) for v in valor)
    return str(valor)


def _tags_brutas(audio) -> list:
    """Todas as tags como [(chave, texto)], sem as imagens (essas vão em `capas`)."""
    tags = getattr(audio, "tags", None)
    if not tags:
        return []
    saida = []
    if hasattr(tags, "getall"):  # ID3
        for chave, frame in tags.items():
            if chave.startswith("APIC"):
                continue
            if chave.startswith("SYLT"):
                texto = f"({len(getattr(frame, 'text', []))} linhas sincronizadas)"
            elif hasattr(frame, "text"):
                texto = _texto(frame.text)
            elif hasattr(frame, "url"):
                texto = str(frame.url)
            elif chave.startswith("PRIV"):
                texto = f"<{len(getattr(frame, 'data', b''))} bytes>"
            else:
                texto = str(frame)[:200]
            saida.append((chave, texto))
        return saida
    for chave, valor in tags.items():
        nome = str(chave)
        if nome == "covr" or nome.lower() == "metadata_block_picture":
            continue  # imagens
        saida.append((nome, _texto(valor)))
    return saida


def _fundir(tags: list) -> dict:
    """[(chave, texto)] -> {chave: texto}; chaves repetidas viram 'a / b'."""
    saida: dict = {}
    for chave, texto in tags:
        saida[chave] = f"{saida[chave]} / {texto}" if chave in saida else texto
    return saida


def _numero_total(texto: str):
    """'3/12' -> ('3', '12'); '3' -> ('3', '')."""
    atual, _, total = str(texto).partition("/")
    return atual.strip(), total.strip()


def _campos(tags: dict) -> dict:
    """Tags -> campos canônicos (os que o programa entende)."""
    campos = {}
    for chave, texto in tags.items():
        canon = _chave_canonica(chave)
        if canon and texto.strip() and canon not in campos:
            campos[canon] = texto.strip()
    for item in ("faixa", "disco"):
        if item in campos:
            atual, total = _numero_total(campos[item])
            campos[item] = atual
            if total and f"{item}_total" not in campos:
                campos[f"{item}_total"] = total
    return campos


def agrupar_tags(campos: dict, tags: dict) -> list:
    """[(título do grupo, [(rótulo, valor)])] com TODAS as tags do arquivo.

    O que o programa entende vai para os grupos (Identificação, Faixa e disco,
    Códigos, IDs do Qobuz, ReplayGain); o resto, sem exceção, vai para "Outras".
    """
    grupos = []
    for titulo, itens in GRUPOS:
        linhas = []
        for campo, rotulo in itens:
            valor = campos.get(campo)
            if not valor:
                continue
            if campo in ("faixa", "disco") and campos.get(f"{campo}_total"):
                valor = f"{valor} de {campos[f'{campo}_total']}"
            linhas.append((rotulo, valor))
        if linhas:
            grupos.append((titulo, linhas))
    # "Outras": tudo que não virou campo de grupo (nem letras, que têm seção própria)
    usados = set()
    for chave in tags:
        canon = _chave_canonica(chave)
        if canon in _CAMPOS_AGRUPADOS or canon in ("letra", "letra_sinc"):
            usados.add(chave)
    outras = [(chave, valor) for chave, valor in tags.items() if chave not in usados]
    if outras:
        grupos.append((f"📝 OUTRAS TAGS ({len(outras)})", outras))
    return grupos


# --------------------------------------------------------------------------
# Capas
# --------------------------------------------------------------------------
def _dimensoes(dados: bytes):
    from qobuz_dl.metadata import _image_dimensions

    try:
        return _image_dimensions(dados)
    except Exception:  # noqa: BLE001 -- imagem estranha: sem dimensões
        return None


def _capa(origem: str, tipo: str, mime: str, dados: bytes, dims=None) -> dict:
    dims = dims or _dimensoes(dados)
    capa = {
        "origem": origem,
        "tipo": tipo,
        "mime": mime,
        "bytes": len(dados),
        "largura": dims[0] if dims else None,
        "altura": dims[1] if dims else None,
        "quadrada": None,
    }
    if dims:
        capa["quadrada"] = abs(dims[0] - dims[1]) <= 0.03 * max(dims)
    return capa


def _capas(audio) -> list:
    capas = []
    nome = type(audio).__name__
    if nome == "FLAC":
        for i, pic in enumerate(audio.pictures, 1):
            dims = (pic.width, pic.height) if pic.width and pic.height else None
            tipo = _TIPOS_CAPA.get(pic.type, f"tipo {pic.type}")
            capas.append(_capa(f"[{i}]", tipo, pic.mime, bytes(pic.data), dims))
        return capas
    tags = getattr(audio, "tags", None)
    if not tags:
        return capas
    if hasattr(tags, "getall"):  # ID3
        for frame in tags.getall("APIC"):
            tipo = _TIPOS_CAPA.get(int(frame.type), f"tipo {int(frame.type)}")
            capas.append(_capa("APIC", tipo, frame.mime, bytes(frame.data)))
    elif "covr" in tags:  # MP4
        for i, cover in enumerate(tags["covr"], 1):
            mime = (
                "image/png" if getattr(cover, "imageformat", 13) == 14 else "image/jpeg"
            )
            capas.append(_capa(f"[{i}]", "frente", mime, bytes(cover)))
    else:  # Ogg/Opus: imagem FLAC em base64 numa tag
        from mutagen.flac import Picture

        for i, bruto in enumerate(tags.get("metadata_block_picture", []), 1):
            try:
                pic = Picture(base64.b64decode(bruto))
            except Exception:  # noqa: BLE001
                continue
            tipo = _TIPOS_CAPA.get(pic.type, f"tipo {pic.type}")
            capas.append(_capa(f"[{i}]", tipo, pic.mime, bytes(pic.data)))
    return capas


# --------------------------------------------------------------------------
# Técnico
# --------------------------------------------------------------------------
def _canais(n) -> str:
    nomes = {1: "mono", 2: "estéreo"}
    return f"{n} ({nomes[n]})" if n in nomes else str(n)


def _tecnico(audio, caminho: str, ext: str) -> dict:
    info = audio.info
    nome = type(audio).__name__
    tamanho = os.path.getsize(caminho)
    duracao = float(getattr(info, "length", 0) or 0)

    codec = _CODEC_NOMES.get(nome, nome)
    sem_perdas = nome in _LOSSLESS
    if nome == "MP4":
        cod = str(getattr(info, "codec", "") or "").lower()
        if cod == "alac":
            codec, sem_perdas = "ALAC", True
        else:
            codec = "AAC"
    tecnico = {
        "Arquivo": os.path.basename(caminho),
        "Tamanho": formatar_tamanho(tamanho),
        "Extensão": ext.lstrip(".").upper(),
        "Codec": codec,
        "Compressão": "sem perdas (lossless)" if sem_perdas else "com perdas (lossy)",
    }
    taxa = getattr(info, "sample_rate", None)
    bits = getattr(info, "bits_per_sample", None)
    if taxa:
        tecnico["Sample rate"] = f"{taxa} Hz"
    if bits:
        tecnico["Profundidade de bits"] = f"{bits} bits"
    canais = getattr(info, "channels", None)
    if canais:
        tecnico["Canais"] = _canais(canais)
    if duracao:
        tecnico["Duração"] = formatar_duracao(duracao)
    informado = getattr(info, "bitrate", 0) or 0
    if informado:
        tecnico["Bitrate informado"] = f"{int(informado) // 1000} kbps"
    if duracao > 0:
        tecnico["Bitrate real (arquivo)"] = f"{int(tamanho * 8 / duracao / 1000)} kbps"
    modo = getattr(info, "bitrate_mode", None)
    if modo is not None and getattr(modo, "name", "") not in ("", "UNKNOWN"):
        tecnico["Modo do bitrate"] = modo.name
    encoder = getattr(info, "encoder_info", "") or ""
    if not encoder:
        encoder = getattr(getattr(audio, "tags", None), "vendor", "") or ""
    if encoder:
        tecnico["Encoder"] = str(encoder)
    if nome == "FLAC":
        md5 = getattr(info, "md5_signature", 0)
        tecnico["Assinatura MD5 do áudio"] = (
            "presente" if md5 else "ausente (não dá para verificar o conteúdo)"
        )
        if getattr(info, "total_samples", 0):
            tecnico["Total de amostras"] = f"{info.total_samples:,}".replace(",", ".")
    if sem_perdas and taxa and bits:
        tecnico["Qualidade"] = f"{bits} bits / {taxa / 1000:g} kHz"
    elif informado:
        tecnico["Qualidade"] = f"{int(informado) // 1000} kbps"
    return tecnico


# --------------------------------------------------------------------------
# Letras
# --------------------------------------------------------------------------
def _letras(caminho: str, campos: dict) -> dict:
    texto = campos.get("letra", "")
    sinc = bool(campos.get("letra_sinc")) or len(_LRC.findall(texto)) >= 2
    previa = next((l.strip() for l in texto.splitlines() if l.strip()), "")
    previa = re.sub(r"^\[[^\]]*\]\s*", "", previa)
    base = os.path.splitext(caminho)[0]
    lrc = base + ".lrc"
    lado = None
    if os.path.isfile(lrc):
        try:
            with open(lrc, encoding="utf-8", errors="replace") as arq:
                conteudo = arq.read()
        except OSError:
            conteudo = ""
        lado = {
            "arquivo": os.path.basename(lrc),
            "linhas": len([l for l in conteudo.splitlines() if l.strip()]),
            "sincronizada": len(_LRC.findall(conteudo)) >= 2,
        }
    return {
        "embutida": bool(texto or campos.get("letra_sinc")),
        "sincronizada": sinc,
        "linhas": len([l for l in texto.splitlines() if l.strip()]),
        "previa": previa[:80],
        "lrc": lado,
    }


# --------------------------------------------------------------------------
# Diagnóstico
# --------------------------------------------------------------------------
def _diagnostico(caminho: str, campos: dict, capas: list) -> list:
    from qobuz_dl.retro_tagger import extract_track_id
    from qobuz_dl.retro_tags import extract_album_id

    track_id = campos.get("track_id", "")
    album_id = campos.get("album_id", "")
    if caminho.lower().endswith((".flac", ".mp3")):
        # os mesmos leitores do `tags`: entendem também o COMMENT ("Trk ID: ...")
        track_id = track_id or extract_track_id(caminho) or ""
        album_id = album_id or extract_album_id(caminho) or ""
    maior = max(
        (c for c in capas if c["largura"]),
        key=lambda c: c["largura"] * c["altura"],
        default=None,
    )
    campos_lib = {
        "artist": campos.get("artista", ""),
        "albumartist": campos.get("albumartista", ""),
        "album": campos.get("album", ""),
        "title": campos.get("titulo", ""),
        "date": campos.get("data", ""),
        "tracknumber": campos.get("faixa", ""),
        "isrc": campos.get("isrc", ""),
    }
    codigos = diagnose(
        campos_lib,
        track_id,
        album_id,
        bool(capas),
        (maior["largura"], maior["altura"]) if maior else None,
    )
    return [
        {"codigo": c, "rotulo": ISSUES[c][0], "dica": ISSUES[c][1]} for c in codigos
    ]


# --------------------------------------------------------------------------
# Leitura completa de um arquivo
# --------------------------------------------------------------------------
def ler_arquivo(caminho: str) -> dict:
    """Lê TUDO de um arquivo de áudio. Levanta a exceção original se ele é ilegível."""
    from mutagen import File as MutagenFile

    ext = os.path.splitext(caminho)[1].lower()
    audio = MutagenFile(caminho)
    if audio is None or getattr(audio, "info", None) is None:
        raise ValueError("Formato de áudio não reconhecido pelo mutagen.")

    brutas = _tags_brutas(audio)
    tags = _fundir(brutas)
    campos = _campos(tags)
    capas = _capas(audio)
    tecnico = _tecnico(audio, caminho, ext)
    info = audio.info
    return {
        "caminho": os.path.abspath(caminho),
        "tecnico": tecnico,
        "campos": campos,
        "tags": tags,
        "grupos": agrupar_tags(campos, tags),
        "capas": capas,
        "letras": _letras(caminho, campos),
        "problemas": _diagnostico(caminho, campos, capas),
        "_sample_rate": getattr(info, "sample_rate", None),
        "_duracao_s": getattr(info, "length", None),
    }


def resumo_curto(dados: dict) -> dict:
    """Linha de resumo de uma faixa (para `inspect PASTA --resumo`)."""
    campos, tec = dados["campos"], dados["tecnico"]
    maior = max(
        (c for c in dados["capas"] if c["largura"]),
        key=lambda c: c["largura"] * c["altura"],
        default=None,
    )
    return {
        "arquivo": tec["Arquivo"],
        "faixa": campos.get("faixa", ""),
        "titulo": campos.get("titulo", ""),
        "artista": campos.get("artista", ""),
        "album": campos.get("album", ""),
        "codec": tec["Codec"],
        "qualidade": tec.get("Qualidade", ""),
        "duracao": tec.get("Duração", ""),
        "capa": f"{maior['largura']}x{maior['altura']}" if maior else "",
        "problemas": [p["codigo"] for p in dados["problemas"]],
    }


def listar_audios(pasta: str) -> list:
    """Todos os arquivos de áudio da pasta e subpastas, em ordem."""
    achados = []
    for raiz, subpastas, arquivos in os.walk(pasta):
        subpastas.sort()
        for nome in sorted(arquivos):
            if nome.lower().endswith(AUDIO_EXTS) and not nome.startswith("."):
                achados.append(os.path.join(raiz, nome))
    return achados


# --------------------------------------------------------------------------
# ffmpeg (opcional): integridade e volume
# --------------------------------------------------------------------------
def verificar_integridade(caminho: str, timeout: int = 180) -> dict:
    """Decodifica o arquivo inteiro. ok=None quando o ffmpeg não está disponível."""
    from qobuz_dl.utils import encontrar_binario, verify_audio_integrity

    if not encontrar_binario("ffmpeg"):
        return {"ok": None, "mensagem": "ffmpeg não encontrado"}
    ok, mensagem = verify_audio_integrity(caminho, timeout=timeout)
    return {"ok": bool(ok), "mensagem": "" if ok else str(mensagem)}


_NUM = r"(-?\d+(?:\.\d+)?|-inf)"
_REGEXES_VOLUME = {
    "integrado_lufs": re.compile(rf"\bI:\s*{_NUM}\s*LUFS"),
    "faixa_dinamica_lu": re.compile(rf"\bLRA:\s*{_NUM}\s*LU\b"),
    "pico_dbfs": re.compile(rf"\bPeak:\s*{_NUM}\s*dBFS"),
}


def medir_volume(caminho: str, timeout: int = 300) -> Optional[dict]:
    """Loudness integrado (LUFS), faixa dinâmica (LU) e pico (dBFS) via ffmpeg ebur128.

    None se o ffmpeg não existe, deu erro ou não devolveu o resumo.
    """
    from qobuz_dl.utils import encontrar_binario

    ffmpeg = encontrar_binario("ffmpeg")
    if not ffmpeg:
        return None
    try:
        resultado = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-nostats",
                "-i",
                caminho,
                "-filter_complex",
                "ebur128=peak=true",
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    saida = resultado.stderr or ""
    # só o bloco "Summary" (o resto são medições a cada 100 ms)
    resumo = saida.rsplit("Summary:", 1)[-1] if "Summary:" in saida else ""
    medidas = {}
    for chave, regex in _REGEXES_VOLUME.items():
        achou = regex.search(resumo)
        if achou:
            texto = achou.group(1)
            medidas[chave] = None if texto == "-inf" else float(texto)
    return medidas or None


def completar(dados: dict) -> dict:
    """Acrescenta integridade e volume (ffmpeg) aos dados de `ler_arquivo`."""
    inicio = time.perf_counter()
    dados["integridade"] = verificar_integridade(dados["caminho"])
    dados["volume"] = medir_volume(dados["caminho"])
    dados["_ffmpeg_s"] = round(time.perf_counter() - inicio, 1)
    return dados


def para_json(dados: dict) -> dict:
    """Versão do relatório sem campos internos e com tudo serializável."""
    return {chave: valor for chave, valor in dados.items() if not chave.startswith("_")}


__all__ = [
    "AUDIO_EXTS",
    "GRUPOS",
    "MIN_COVER_SIDE",
    "agrupar_tags",
    "completar",
    "formatar_duracao",
    "formatar_tamanho",
    "ler_arquivo",
    "listar_audios",
    "medir_volume",
    "para_json",
    "resumo_curto",
    "verificar_integridade",
]
