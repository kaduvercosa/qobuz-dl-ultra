"""
musicbrainz.py -- Lookup de IDs do MusicBrainz via ISRC

Usa a API pública do MusicBrainz (sem autenticação, sem custo) para
enriquecer os metadados das faixas com MUSICBRAINZ_TRACKID,
MUSICBRAINZ_ALBUMID e MUSICBRAINZ_ARTISTID.

O ISRC é o identificador universal de gravação (já gravado pelo qobuz-dl
na tag ISRC) e permite um match EXATO -- não fuzzy -- contra o catálogo do
MusicBrainz. Isso ativa matching automático no Beets, MusicBrainz Picard,
Navidrome e Plex sem qualquer intervenção manual.

Rate limit: MusicBrainz permite 1 req/s sem autenticação. O semaphore
_MB_SEM garante que nunca disparamos mais que isso, mesmo com max_workers
alto. Resultados ficam em _MB_CACHE (dict em memória) para a sessão
inteira -- um álbum de 12 faixas com os mesmos artistas/álbum dispara
apenas as consultas necessárias, sem repetição.
"""

import asyncio
import logging
import httpx

logger = logging.getLogger(__name__)

_MB_BASE = "https://musicbrainz.org/ws/2"
_MB_HEADERS = {
    # MusicBrainz exige User-Agent identificando a app e versao.
    # Sem isso a resposta e' 403.
    "User-Agent": "qobuz-dl-ultra/2.0 (https://github.com/user/qobuz-dl-ultra)",
    "Accept": "application/json",
}

# Cache global por sessao: ISRC → (track_mbid, album_mbid, artist_mbid)
_MB_CACHE: dict[str, tuple] = {}

# Semaphore de 1 slot: garante no maximo 1 req/s ao MusicBrainz globalmente,
# independente de quantas faixas estao baixando em paralelo.
_MB_SEM: asyncio.Semaphore | None = None


def _get_sem() -> asyncio.Semaphore:
    """Cria o semaphore na primeira chamada (precisa do event loop ativo)."""
    global _MB_SEM
    if _MB_SEM is None:
        _MB_SEM = asyncio.Semaphore(1)
    return _MB_SEM


async def lookup_by_isrc(
    isrc: str,
    session: httpx.AsyncClient | None = None,
) -> tuple[str | None, str | None, str | None]:
    """
    Busca MUSICBRAINZ_TRACKID, MUSICBRAINZ_ALBUMID e MUSICBRAINZ_ARTISTID
    para um ISRC.

    Args:
        isrc: Código ISRC da faixa (ex: "GBAYE0000001").
        session: httpx.AsyncClient opcional. Se None, cria um temporario.

    Returns:
        Tupla (track_mbid, album_mbid, artist_mbid).
        Qualquer valor pode ser None se nao for encontrado.
    """
    if not isrc:
        return None, None, None

    isrc = isrc.strip().upper()

    if isrc in _MB_CACHE:
        return _MB_CACHE[isrc]

    sem = _get_sem()

    async with sem:
        # Re-checar apos pegar o semaphore: outra task pode ter preenchido
        # o cache enquanto esperavamos.
        if isrc in _MB_CACHE:
            return _MB_CACHE[isrc]

        try:
            own_session = session is None
            client = session or httpx.AsyncClient(
                headers=_MB_HEADERS, timeout=httpx.Timeout(10.0, connect=5.0)
            )

            try:
                resp = await client.get(
                    f"{_MB_BASE}/recording/",
                    params={"query": f"isrc:{isrc}", "fmt": "json", "limit": 1},
                )
                resp.raise_for_status()
                data = resp.json()

                recordings = data.get("recordings", [])
                if not recordings:
                    _MB_CACHE[isrc] = (None, None, None)
                    return None, None, None

                rec = recordings[0]
                track_mbid = rec.get("id")
                album_mbid = None
                artist_mbid = None

                # Pegar album_mbid do primeiro release
                releases = rec.get("releases", [])
                if releases:
                    album_mbid = releases[0].get("id")

                # Pegar artist_mbid do primeiro artist-credit
                credits = rec.get("artist-credit", [])
                for credit in credits:
                    if isinstance(credit, dict) and "artist" in credit:
                        artist_mbid = credit["artist"].get("id")
                        break

                result = (track_mbid, album_mbid, artist_mbid)
                _MB_CACHE[isrc] = result

                logger.debug(
                    f"MusicBrainz: ISRC {isrc} → "
                    f"track={track_mbid} album={album_mbid} artist={artist_mbid}"
                )
                return result

            finally:
                if own_session:
                    await client.aclose()

            # Throttle: 1 req/s conforme guidelines do MusicBrainz
            await asyncio.sleep(1.0)

        except httpx.HTTPStatusError as e:
            logger.debug(f"MusicBrainz HTTP error para {isrc}: {e}")
        except Exception as e:
            logger.debug(f"MusicBrainz lookup falhou para {isrc}: {e}")

    _MB_CACHE[isrc] = (None, None, None)
    return None, None, None
