"""Testa a busca de capa da Apple/iTunes para UMA faixa, sem rodar o `tags`.

Uso (rápido, no a-Shell também):

    python tools/apple_cover_check.py "Jão" "Anti-Herói" "VSF"
    python tools/apple_cover_check.py "Jão" "LOBOS" "Ressaca" --isrc BRXXX0000000
    python tools/apple_cover_check.py "Jão" "Anti-Herói" "VSF" --all

Mostra a URL da capa escolhida e, quando não acha, o motivo completo --
inclusive a "etapa do álbum" (álbuns consultados e quantas faixas cada um
tinha). Com --all, lista TODAS as consultas feitas à Apple, cada resultado que
ela devolveu e o veredito (aceito/rejeitado, e por quê) de cada candidato.
Útil para conferir por que uma faixa específica ficou sem capa.
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def check(artist, album, track, upc=None, isrc=None, session=None, trace=None):
    from qobuz_dl.utils import get_apple_hq_cover

    return await get_apple_hq_cover(
        session,
        upc=upc,
        isrc=isrc,
        artist=artist,
        album=album,
        track_title=track,
        trace=trace,
    )


def format_trace(trace) -> list:
    """Linhas legíveis com cada consulta, cada resultado e cada veredito."""
    lines = []
    requests = [e for e in trace if e.get("type") == "request"]
    lines.append(f"=== CONSULTAS À APPLE ({len(requests)}) ===")
    for number, entry in enumerate(requests, 1):
        params = " ".join(f"{k}={v}" for k, v in entry.get("params", {}).items())
        rows = entry.get("results", [])
        lines.append(f"[{number}] {entry['tag']}: {params or entry['url']}")
        lines.append(f"    {len(rows)} resultado(s)")
        for row in rows:
            parts = [
                f"{label}={row[chave]!r}"
                for chave, label in (
                    ("artista", "artista"),
                    ("album", "álbum"),
                    ("faixa", "faixa"),
                    ("id", "id"),
                )
                if row.get(chave) not in (None, "")
            ]
            lines.append(f"    - ({row.get('tipo')}) " + ", ".join(parts))
    verdicts = [e for e in trace if e.get("type") in ("accept", "reject")]
    lines.append(f"=== VEREDITOS ({len(verdicts)}) ===")
    for entry in verdicts:
        if entry["type"] == "accept":
            marca = "ACEITO (perfeito)" if entry.get("perfect") else "ACEITO"
            lines.append(f"  {marca} rank={entry['rank']:.3f}: {entry['label']}")
        else:
            lines.append(f"  REJEITADO: {entry['reason']}")
            if entry.get("label"):
                lines.append(f"      {entry['label']}")
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("artist")
    parser.add_argument("album")
    parser.add_argument("track")
    parser.add_argument("--upc")
    parser.add_argument("--isrc")
    parser.add_argument(
        "--all",
        action="store_true",
        help="lista todas as consultas, todos os resultados e todos os vereditos",
    )
    args = parser.parse_args(argv)

    trace = [] if args.all else None
    url, source, reason = asyncio.run(
        check(
            args.artist,
            args.album,
            args.track,
            args.upc,
            args.isrc,
            trace=trace,
        )
    )
    if trace is not None:
        print("\n".join(format_trace(trace)) + "\n")
    if url:
        print(f"ENCONTRADA ({source}): {url}")
        return 0
    print(f"NÃO ENCONTRADA: {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
