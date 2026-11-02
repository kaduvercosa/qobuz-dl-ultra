# ==============================================================================
# quality_audit.py
# ------------------------------------------------------------------------------
# Auditoria de qualidade (`inspect PASTA --resumo --auditar`): confere o que a
# PASTA e o COMENTÁRIO de cada arquivo dizem sobre a qualidade contra o que o
# ÁUDIO realmente é. Só lê; nunca altera nada.
#
# Exemplos do que acha: pasta "[FLAC 24]" cheia de FLAC 16-bit; comentário
# "24-bit / 96 kHz" (o máximo do catálogo, gravado por versões antigas) num
# arquivo que é 16/44.1 ou num MP3.
# ==============================================================================

from __future__ import annotations

import os
import re
from typing import Any, Optional

_FOLDER_RE = re.compile(
    r"\[(FLAC|ALAC|MP3|AAC)(?:\s+(\d+))?\]\s*$",
    re.IGNORECASE,
)
_CMT_LOSSLESS_RE = re.compile(
    r"Qualidade:\s*(FLAC|ALAC)\s+(\d+)-bit\s*/\s*([\d.]+)\s*kHz",
    re.IGNORECASE,
)
_CMT_MP3_KBPS_RE = re.compile(r"Qualidade:\s*MP3\s+(\d+)\s*kbps", re.IGNORECASE)
# Formato antigo (e fallback do novo): "24b/96kHz", com ou sem "Qualidade:".
_CMT_BITS_RE = re.compile(
    r"(?:^|\|\s*|Qualidade:\s*)(\d+)b/([\d.]+)\s*kHz", re.IGNORECASE
)

_LOSSLESS = {"FLAC", "ALAC"}
_KBPS_TOLERANCE = 16  # MP3 VBR: o bitrate real oscila em torno do informado
_KHZ_TOLERANCE = 0.05


def folder_claim(folder_name: str) -> Optional[dict]:
    """O que o nome da pasta diz: `[FLAC 24]` -> {"codec": "FLAC", "bits": 24}."""
    match = _FOLDER_RE.search(folder_name or "")
    if not match:
        return None
    codec = match.group(1).upper()
    bits = int(match.group(2)) if match.group(2) else None
    return {"codec": codec, "bits": bits if codec in _LOSSLESS else None}


def comment_claim(text: Optional[str]) -> Optional[dict]:
    """O que o comentário diz sobre a qualidade (formatos novo e antigo)."""
    if not text:
        return None
    match = _CMT_LOSSLESS_RE.search(text)
    if match:
        return {
            "codec": match.group(1).upper(),
            "bits": int(match.group(2)),
            "khz": float(match.group(3)),
            "kbps": None,
        }
    match = _CMT_MP3_KBPS_RE.search(text)
    if match:
        return {"codec": "MP3", "bits": None, "khz": None, "kbps": int(match.group(1))}
    match = _CMT_BITS_RE.search(text)
    if match:  # "24b/96kHz": diz bits/taxa, sem dizer o formato
        return {
            "codec": None,
            "bits": int(match.group(1)),
            "khz": float(match.group(2)),
            "kbps": None,
        }
    return None


def real_quality(detalhes: dict) -> dict:
    """Qualidade real do arquivo, a partir de `inspect_data.ler_arquivo`."""
    codec = str(detalhes.get("tecnico", {}).get("Codec", "")).upper()
    taxa = detalhes.get("_sample_rate")
    bitrate = detalhes.get("_bitrate")
    bits = detalhes.get("_bits")
    return {
        "codec": codec,
        "bits": int(bits) if bits else None,
        "khz": float(taxa) / 1000 if taxa else None,
        "kbps": round(float(bitrate) / 1000) if bitrate else None,
    }


def describe_real(real: dict) -> str:
    """Qualidade real em texto: `FLAC 16-bit / 44.1 kHz` ou `MP3 320 kbps`."""
    codec = real.get("codec") or "?"
    if codec in _LOSSLESS and real.get("bits") and real.get("khz"):
        return f"{codec} {real['bits']}-bit / {real['khz']:g} kHz"
    if real.get("kbps"):
        return f"{codec} {real['kbps']} kbps"
    return codec


def describe_comment(claim: dict) -> str:
    """O que o comentário diz, em texto curto."""
    if claim.get("kbps"):
        return f"MP3 {claim['kbps']} kbps"
    if claim.get("bits") and claim.get("khz"):
        return f"{claim['bits']}-bit / {claim['khz']:g} kHz"
    return claim.get("codec") or "?"


def comment_mismatch(claim: Optional[dict], real: dict) -> bool:
    """O comentário descreve uma qualidade diferente da real?"""
    if not claim:
        return False
    rcodec = real.get("codec") or ""
    ccodec = claim.get("codec")
    if ccodec and rcodec and ccodec != rcodec:
        return True
    if claim.get("kbps"):
        real_kbps = real.get("kbps")
        return bool(real_kbps) and abs(real_kbps - claim["kbps"]) > _KBPS_TOLERANCE
    if claim.get("bits") and claim.get("khz"):
        if rcodec not in _LOSSLESS:
            return True  # promete hi-res/lossless num arquivo com perdas
        return claim["bits"] != real.get("bits") or (
            abs(claim["khz"] - (real.get("khz") or 0)) > _KHZ_TOLERANCE
        )
    return False


def folder_mismatch(claim: Optional[dict], real: dict) -> bool:
    """O nome da pasta descreve uma qualidade diferente da do arquivo?"""
    if not claim:
        return False
    rcodec = real.get("codec") or ""
    if rcodec and claim["codec"] != rcodec:
        return True
    return bool(claim.get("bits") and real.get("bits")) and (
        claim["bits"] != real["bits"]
    )


def comment_from_tags(tags: dict) -> str:
    """Texto do comentário dentro do dicionário de tags de `ler_arquivo`."""
    for chave, texto in tags.items():
        if str(chave).upper().split(":")[0] in ("COMMENT", "COMM", "COMMENTS"):
            return str(texto)
    return ""


def _folder_label(claim: Optional[dict]) -> str:
    """`[FLAC 24]` / `[MP3]` como a pasta mostra; vazio sem tag de qualidade."""
    if not claim:
        return ""
    bits = f" {claim['bits']}" if claim.get("bits") else ""
    return f"[{claim['codec']}{bits}]"


def audit_file(rel_path: str, detalhes: dict) -> dict:
    """Confere UM arquivo; `rel_path` é relativo à pasta auditada."""
    real = real_quality(detalhes)
    c_claim = comment_claim(comment_from_tags(detalhes.get("tags", {})))
    pasta = os.path.dirname(rel_path)
    f_claim = folder_claim(os.path.basename(pasta)) if pasta else None
    return {
        "arquivo": rel_path,
        "pasta": pasta,
        "real": describe_real(real),
        "comentario": describe_comment(c_claim) if c_claim else "",
        "comentario_errado": comment_mismatch(c_claim, real),
        "pasta_diz": _folder_label(f_claim),
        "pasta_errada": folder_mismatch(f_claim, real),
    }


def summarize(items: list[dict]) -> dict[str, Any]:
    """Totais e detalhes da auditoria a partir dos resultados de `audit_file`."""
    pastas_erradas: dict[str, dict] = {}
    for item in items:
        if item["pasta_errada"] and item["pasta"] not in pastas_erradas:
            pastas_erradas[item["pasta"]] = item
    comentarios = [item for item in items if item["comentario_errado"]]
    return {
        "conferidas": len(items),
        "pastas_erradas": [
            {"pasta": p, "pasta_diz": i["pasta_diz"], "real": i["real"]}
            for p, i in pastas_erradas.items()
        ],
        "comentarios_errados": [
            {"arquivo": i["arquivo"], "comentario": i["comentario"], "real": i["real"]}
            for i in comentarios
        ],
    }
