"""
musicbrainz.py -- Lookup de IDs do MusicBrainz via ISRC

Usa a API pública do MusicBrainz (sem autenticação, sem custo) para
enriquecer os metadados das faixas com MUSICBRAINZ_TRACKID,
MUSICBRAINZ_ALBUMID e MUSICBRAINZ_ARTISTID.

O ISRC é o identificador universal de gravação (já gravado pelo qobuz-dl
na tag ISRC) é uma pista de busca, não uma garantia de edição, no catálogo do
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
import re
import unicodedata

import httpx

from qobuz_dl import __version__
from qobuz_dl.http_download import make_client

logger = logging.getLogger(__name__)

_MB_BASE = "https://musicbrainz.org/ws/2"
_MB_HEADERS = {
    # MusicBrainz exige User-Agent identificando a app e versao.
    # Sem isso a resposta e' 403. Usa __version__ do pacote em vez de
    # cravar um numero fixo, que inevitavelmente fica desatualizado (ja
    # aconteceu duas vezes: primeiro travado em 1.0, depois em 2.0).
    "User-Agent": f"qobuz-dl-ultra/{__version__} (https://github.com/kaduvercosa/qobuz-dl-ultra)",
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
            client = session or make_client(
                headers=_MB_HEADERS,
                timeout=httpx.Timeout(10.0, connect=5.0),
                max_connections=1,
                max_keepalive_connections=1,
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
                # Throttle: 1 req/s conforme guidelines do MusicBrainz.
                # ANTES este sleep ficava DEPOIS do bloco try/finally --
                # como todo caminho normal do try acima termina em
                # `return`, aquele sleep era código morto: nunca
                # executava, e o throttle real dependia só do semaphore
                # de 1 slot (que serializa requisições, mas não impõe
                # nenhum intervalo mínimo entre elas). Movido pra dentro
                # do `finally` pra rodar sempre que uma requisição foi de
                # fato tentada, ainda segurando o semaphore -- é isso que
                # garante o intervalo de verdade antes da próxima task
                # conseguir o slot.
                await asyncio.sleep(1.0)

        except httpx.HTTPStatusError as e:
            logger.debug(f"MusicBrainz HTTP error para {isrc}: {e}")
        except Exception as e:
            logger.debug(f"MusicBrainz lookup falhou para {isrc}: {e}")

    # ANTES: esta linha cacheava (None, None, None) tanto pra "MusicBrainz
    # respondeu e não achou nada" quanto pra "a requisição falhou"
    # (timeout, 500, erro de rede) -- os dois caindo aqui pelos `except`
    # acima. Resultado: um erro TRANSITÓRIO virava um "sem match"
    # PERMANENTE pro resto da sessão, e nenhuma faixa seguinte com o
    # mesmo ISRC tentava de novo. Motivo de espalhar em vez de reunir: o
    # cache positivo de "não encontrado" já é gravado explicitamente lá
    # em cima (`if not recordings: _MB_CACHE[isrc] = (None, None, None)`)
    # -- só o caminho de exceção não deve cachear.
    return None, None, None


# ----------------------------------------------------------------------------
# Correspondencia ESTRITA (modo --musicbrainz)
#
# O ISRC sozinho nao basta: ele pode ligar a faixa a uma gravacao/lancamento
# diferente (remaster, coletanea, edicao deluxe). So' gravamos IDs quando
# titulo, versao, album e artista da faixa conferem com os do MusicBrainz.
# Se qualquer um falhar, nada e' gravado.
# ----------------------------------------------------------------------------

_MB_MATCH_CACHE: dict[tuple, dict | None] = {}

_SEPARADOR_ARTISTAS = re.compile(
    r"\s*(?:,|;|/|&|\band\b|\bx\b|\bfeat\.?|\bft\.?|\bwith\b)\s*",
    re.IGNORECASE,
)


def _norm(texto) -> str:
    """Sem acentos, maiusculas ou pontuacao (so' letras e numeros)."""
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = "".join(c for c in base if not unicodedata.combining(c)).casefold()
    so_alnum = re.sub(r"[\W_]+", "", base)
    return so_alnum or base.strip()


def _com_versao(titulo, versao) -> str:
    titulo = str(titulo or "").strip()
    versao = str(versao or "").strip()
    return (
        f"{titulo} ({versao})"
        if versao and _norm(versao) not in _norm(titulo)
        else titulo
    )


def _titulo_confere(titulo, versao, mb_titulo, mb_desambiguacao) -> bool:
    """Titulo + versao do Qobuz contra titulo (+ desambiguacao) do MusicBrainz.

    A versao precisa estar presente dos dois lados: "Song (Remastered)" nao
    confere com "Song", e "Song" nao confere com "Song (Remastered)".
    """
    alvo = _norm(_com_versao(titulo, versao))
    if not alvo or not mb_titulo:
        return False
    if (
        not versao
        and mb_desambiguacao
        and re.search(
            r"\b(live|remaster\w*|remix|instrumental|acoustic|demo|edit|deluxe)\b",
            str(mb_desambiguacao),
            re.IGNORECASE,
        )
        and _norm(mb_desambiguacao) not in _norm(titulo)
    ):
        return False
    candidatos = {_norm(mb_titulo)}
    if mb_desambiguacao:
        candidatos.add(_norm(f"{mb_titulo} {mb_desambiguacao}"))
    return alvo in candidatos


def _creditos_mb(gravacao: dict) -> list[dict]:
    saida = []
    for credito in gravacao.get("artist-credit") or []:
        if not isinstance(credito, dict):
            continue
        artista = credito.get("artist") or {}
        nome = credito.get("name") or artista.get("name") or ""
        saida.append(
            {
                "name": nome,
                "id": artista.get("id"),
                "join": credito.get("joinphrase") or "",
            }
        )
    return saida


def _conjunto_artistas(nomes) -> set[str]:
    if isinstance(nomes, str):
        nomes = [nomes]
    return {_norm(nome) for nome in nomes if _norm(nome)}


def _artista_confere(artistas, artista_texto, creditos) -> bool:
    nomes_mb = [c["name"] for c in creditos if c["name"]]
    if not nomes_mb:
        return False
    esperado = _conjunto_artistas(artistas or [])
    if esperado:
        return esperado == _conjunto_artistas(nomes_mb)
    texto_mb = "".join(f"{c['name']}{c['join']}" for c in creditos)
    return bool(artista_texto) and _norm(artista_texto) == _norm(texto_mb)


def _barcode(value):
    texto = str(value or "").strip()
    return texto.lstrip("0") if texto.isdigit() else ""


def _duracao_confere(seconds, milliseconds):
    try:
        esperado, encontrado = float(seconds), float(milliseconds) / 1000
    except (ValueError, TypeError):
        return True
    return esperado <= 0 or encontrado <= 0 or abs(esperado - encontrado) <= 3


def escolher_correspondencia(
    gravacoes,
    *,
    title,
    version,
    album,
    album_version,
    artists,
    artist_text="",
    upc="",
    duration=None,
) -> dict | None:
    """Só aceita gravação única; edição exige UPC e candidato único.

    Sem barcode disponível na resposta, preserva apenas a gravação/artistas.
    Título de álbum incompatível continua sendo motivo de rejeição.
    """
    candidatos = {}
    for gravacao in gravacoes or []:
        if not isinstance(gravacao, dict) or not gravacao.get("id"):
            continue
        if not _titulo_confere(
            title, version, gravacao.get("title"), gravacao.get("disambiguation")
        ):
            continue
        if not _duracao_confere(duration, gravacao.get("length")):
            continue
        creditos = _creditos_mb(gravacao)
        if not _artista_confere(artists, artist_text, creditos):
            continue
        releases = [
            r
            for r in gravacao.get("releases") or []
            if isinstance(r, dict)
            and r.get("id")
            and _titulo_confere(
                album, album_version, r.get("title"), r.get("disambiguation")
            )
        ]
        if not releases:
            continue
        ids = [c["id"] for c in creditos]
        if not ids or not all(ids):
            continue
        key = gravacao["id"]
        entry = candidatos.setdefault(key, {"artists": ids, "releases": set()})
        if entry["artists"] != ids:
            return None
        for release in releases:
            if _barcode(upc) and _barcode(upc) == _barcode(release.get("barcode")):
                entry["releases"].add(release["id"])
    if len(candidatos) != 1:
        return None
    recording, entry = next(iter(candidatos.items()))
    releases = entry["releases"]
    return {
        "track": recording,
        "album": next(iter(releases)) if len(releases) == 1 else None,
        "artists": entry["artists"],
    }


async def lookup_matching(
    *,
    isrc: str,
    title: str,
    version: str = "",
    album: str,
    album_version: str = "",
    artists=(),
    artist_text: str = "",
    session: httpx.AsyncClient | None = None,
    upc: str = "",
    duration: float | None = None,
) -> dict | None:
    """Busca por ISRC e so' devolve IDs se titulo, versao, album e artista
    conferirem. Nunca levanta excecao: qualquer falha vira None.
    """
    isrc = (isrc or "").strip().upper()
    artists = (
        (artists,)
        if isinstance(artists, str)
        else tuple(a for a in (artists or ()) if a)
    )
    if not isrc or not title or not album or not (artists or artist_text):
        return None

    chave = (
        isrc,
        title,
        version,
        album,
        album_version,
        artists,
        artist_text,
        upc,
        duration,
    )
    if chave in _MB_MATCH_CACHE:
        return _MB_MATCH_CACHE[chave]

    async with _get_sem():
        if chave in _MB_MATCH_CACHE:
            return _MB_MATCH_CACHE[chave]
        try:
            own_session = session is None
            client = session or make_client(
                headers=_MB_HEADERS,
                timeout=httpx.Timeout(10.0, connect=5.0),
                max_connections=1,
                max_keepalive_connections=1,
            )
            try:
                resp = await client.get(
                    f"{_MB_BASE}/recording/",
                    params={"query": f"isrc:{isrc}", "fmt": "json", "limit": 100},
                )
                resp.raise_for_status()
                dados = resp.json()
            finally:
                if own_session:
                    await client.aclose()
                await asyncio.sleep(1.0)

            recordings = dados.get("recordings") or []
            if int(dados.get("count", len(recordings))) > len(recordings):
                return None
            resultado = escolher_correspondencia(
                dados.get("recordings"),
                title=title,
                version=version,
                album=album,
                album_version=album_version,
                artists=artists,
                artist_text=artist_text,
                upc=upc,
                duration=duration,
            )
            # Resposta valida sem correspondencia tambem entra no cache.
            _MB_MATCH_CACHE[chave] = resultado
            return resultado
        except Exception as exc:
            logger.debug("MusicBrainz (correspondencia estrita) falhou: %s", exc)
    return None
