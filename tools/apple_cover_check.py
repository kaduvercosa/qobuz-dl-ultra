"""Testa a busca de capa da Apple/iTunes para UMA faixa ou UM álbum, sem rodar o `tags`.

Uso (rápido, no a-Shell também):

    # a faixa sozinha (como o `tags` faz quando não há capa única de álbum)
    python tools/apple_cover_check.py "Jão" "Anti-Herói" "VSF"

    # exatamente o que o `tags` decide para o ÁLBUM (lista de faixas, tipo,
    # lojas extras): use --kind e --album-track com os dados da Qobuz
    python tools/apple_cover_check.py "The Weeknd" "Take My Breath" \
        "Take My Breath (Single Version)" --kind single --decide

    python tools/apple_cover_check.py "Jão" "Anti-Herói" "VSF" --decide \
        --kind album --album-track "Fim de Festa" --album-track "VSF" --all

Opções úteis:
  --kind single|album   tipo do lançamento na Qobuz (single só casa com "- Single")
  --album-track TÍTULO  (repetível) faixas do álbum na Qobuz, com a versão
                        entre parênteses; usadas para confirmar a versão certa
  --decide              roda a decisão de capa do ÁLBUM, como o `tags`
  --countries br,us     lojas consultadas (padrão: br,us)
  --fallback            se as lojas principais não resolverem, tenta gb, ca, au,
                        de, fr, es, mx e jp
  --all                 lista TODAS as consultas, todos os resultados e o
                        veredito (aceito/rejeitado e por quê) de cada candidato
"""

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def check(
    artist,
    album,
    track,
    upc=None,
    isrc=None,
    session=None,
    trace=None,
    kind=None,
    countries=None,
    fallback=False,
):
    from qobuz_dl.utils import get_apple_hq_cover

    return await get_apple_hq_cover(
        session,
        upc=upc,
        isrc=isrc,
        artist=artist,
        album=album,
        track_title=track,
        trace=trace,
        release_kind=None
        if kind is None
        else ("single" if kind == "single" else "full"),
        countries=tuple(countries) if countries else None,
        fallback_countries=fallback,
    )


async def decide(
    artist,
    album,
    album_tracks,
    upc=None,
    kind=None,
    session=None,
    trace=None,
):
    """Roda a MESMA decisão de capa do álbum que o `tags` usa."""
    from qobuz_dl import retro_tags

    own_session = session is None
    if own_session:
        from qobuz_dl.http_download import make_client

        session = make_client()
    items = [{"id": n, "title": title} for n, title in enumerate(album_tracks, 1)]
    qobuz_album = {
        "id": "CHECK",
        "title": album,
        "upc": upc,
        "artist": {"name": artist},
        "tracks_count": len(items),
        "tracks": {"items": items},
    }
    if kind:
        qobuz_album["release_type"] = kind
    try:
        return await retro_tags._decide_album_cover(
            qobuz_album, session, {}, trace=trace
        )
    finally:
        if own_session:
            await session.aclose()


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
    parser.add_argument("--kind", choices=("single", "album"))
    parser.add_argument("--album-track", action="append", dest="album_tracks")
    parser.add_argument("--decide", action="store_true")
    parser.add_argument("--countries")
    parser.add_argument("--fallback", action="store_true")
    parser.add_argument(
        "--all",
        action="store_true",
        help="lista todas as consultas, todos os resultados e todos os vereditos",
    )
    args = parser.parse_args(argv)

    trace = [] if args.all else None
    countries = (
        [c.strip() for c in args.countries.split(",") if c.strip()]
        if args.countries
        else None
    )

    if args.decide:
        decision = asyncio.run(
            decide(
                args.artist,
                args.album,
                args.album_tracks or [args.track],
                upc=args.upc,
                kind=args.kind,
                trace=trace,
            )
        )
        url, source, reason = (
            decision.get("url"),
            decision.get("source"),
            decision.get("note"),
        )
        method = decision.get("method")
    else:
        url, source, reason = asyncio.run(
            check(
                args.artist,
                args.album,
                args.track,
                args.upc,
                args.isrc,
                trace=trace,
                kind=args.kind,
                countries=countries,
                fallback=args.fallback,
            )
        )
        method = None

    if trace is not None:
        print("\n".join(format_trace(trace)) + "\n")
    if url:
        extra = f" [{method}]" if method else ""
        print(f"ENCONTRADA ({source}){extra}: {url}")
        return 0
    print(f"NÃO ENCONTRADA: {reason}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
