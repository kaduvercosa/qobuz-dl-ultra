# Capa em alta resolução via Apple/iTunes: normalização de títulos/artistas,
# comparação de edições e a busca em si (``get_apple_hq_cover``).
#
# Movido de ``qobuz_dl/utils.py`` para este módulo. ``utils`` reexporta todos os
# nomes daqui, então ``from qobuz_dl.utils import get_apple_hq_cover`` (e
# ``monkeypatch.setattr("qobuz_dl.utils.get_apple_hq_cover", ...)``) continuam
# funcionando.

import difflib
import re
import unicodedata
import urllib.parse
from typing import Optional

from qobuz_dl.http_download import make_client


# ------------------------------------------------------------------------
# Capa em alta resolucao via Apple/iTunes.
#
# A Qobuz normalmente entrega a capa em ate 600x600 (ou "org", que varia
# de album pra album). A API de busca do iTunes frequentemente tem a
# mesma capa em ate 10000x10000 -- esta funcao tenta achar essa versao e
# so' a devolve se, por similaridade de texto, tiver uma confianca alta
# de que e' realmente a MESMA capa (mesmo artista/album/faixa), pra nunca
# arriscar colar a capa errada num album.
# ------------------------------------------------------------------------
def extrair_essencia(texto: str) -> str:
    # Limpa acentos, pontuacao e qualquer coisa entre parenteses/colchetes,
    # pra uma comparacao "larga": acha candidatos com o mesmo
    # artista/album base, ignorando qual edicao especifica e' essa.
    """Extract the essence of a text for fuzzy matching."""
    if not texto:
        return ""
    texto = (
        unicodedata.normalize("NFKD", texto)
        .encode("ASCII", "ignore")
        .decode("utf-8")
        .lower()
    )
    # A Apple acrescenta " - Single"/" - EP" ao titulo do album; a Qobuz nao.
    texto = re.sub(r"\s+-\s+(?:single|ep)\s*$", "", texto)
    texto = re.sub(r"[\(\[].*?[\)\]]", "", texto)
    texto = re.sub(r"[^\w\s]", " ", texto)
    return " ".join(texto.split())


def extrair_titulo_completo(texto: str) -> str:
    # Normaliza o texto SEM remover o conteudo de parenteses/colchetes --
    # preserva a info de versao (Deluxe, Live, Remaster, Tour Edition
    # etc.). Usado como trava final: comparar o titulo INTEIRO por
    # similaridade derruba sozinho qualquer edicao diferente da que foi
    # pedida, sem precisar manter uma lista fixa de palavras-chave.
    """Extract full title including version/parenthetical info."""
    if not texto:
        return ""
    texto = (
        unicodedata.normalize("NFKD", texto)
        .encode("ASCII", "ignore")
        .decode("utf-8")
        .lower()
    )
    texto = texto.replace("[", "(").replace("]", ")")
    # Participacoes ("feat."/"with") nao mudam a edicao da musica, e Apple e
    # Qobuz nem sempre concordam em coloca-las no titulo.
    texto = re.sub(r"\s*\((?:feat|ft|featuring|with|com)\b[^)]*\)", " ", texto)
    texto = re.sub(r"[^\w\s\(\)]", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    # Sufixos editorial frequente no Apple Music. Ele identifica o formato
    # do release, não uma edição artística diferente da música.
    texto = re.sub(r"[-\s]*single\s*$", "", texto).strip()

    return texto


# --------------------------------------------------------------------------
# EDIÇÃO DO ÁLBUM (Deluxe x padrão etc.)
#
# A capa do Deluxe costuma ser outra, então a edição pedida TEM que ser a
# mesma do candidato da Apple. Isso é conferido por CONJUNTO de palavras de
# edição, e não por similaridade de texto (que deixa "Rare" casar com
# "Rare (Deluxe)"). "Explicit"/"Clean" não mudam a capa: são neutros.
# --------------------------------------------------------------------------
_EDICOES_ALBUM = {
    "deluxe": re.compile(r"\bdeluxe\b"),
    "super": re.compile(r"\bsuper\b"),
    "expanded": re.compile(r"\b(?:expanded|expandida)\b"),
    "platinum": re.compile(r"\bplatinum\b"),
    "collector": re.compile(r"\bcollectors?\b"),
    "anniversary": re.compile(r"\b(?:anniversary|aniversario)\b"),
    "tour": re.compile(r"\btour\b"),
    "special": re.compile(r"\bspecial\b"),
    "complete": re.compile(r"\bcomplete\b"),
    "ultimate": re.compile(r"\bultimate\b"),
    "remaster": re.compile(r"\bremaster(?:ed)?\b"),
    "bonus": re.compile(r"\bbonus\b"),
}
_PALAVRAS_NEUTRAS_ALBUM = re.compile(
    r"\b(?:explicit|explicito|clean|edited|censored)\b"
)


def edicao_album(texto: str) -> frozenset:
    """Palavras de edição do ÁLBUM (deluxe, expanded, anniversary...)."""
    base = extrair_titulo_completo(texto)
    return frozenset(nome for nome, rx in _EDICOES_ALBUM.items() if rx.search(base))


def titulo_album_comparavel(texto: str) -> str:
    """Título de álbum para comparar por similaridade: sem Explicit/Clean e
    sem parênteses repetidos ("Rare (Deluxe - Explicit) (Deluxe)" vira
    "rare (deluxe)"). A edição em si é conferida em edicao_album()."""
    t = _PALAVRAS_NEUTRAS_ALBUM.sub(" ", extrair_titulo_completo(texto))
    t = re.sub(r"\(\s+", "(", t)
    t = re.sub(r"\s+\)", ")", t)
    t = re.sub(r"\(\s*\)", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    for _ in range(3):
        novo = re.sub(r"(\([^()]*\))(?:\s*\1)+", r"\1", t)
        if novo == t:
            break
        t = novo
    return t


def album_titulo_completo(album: dict) -> str:
    """Título do álbum JUNTO com a versão ("Rare" + "Deluxe" -> "Rare (Deluxe)").

    A Qobuz guarda título e versão em campos separados. Sem a versão, a busca
    da capa na Apple procurava "Rare" e voltava a capa da edição padrão.
    """
    album = album or {}
    titulo = (album.get("title") or "").strip()
    versao = (album.get("version") or "").strip()
    if versao and versao.casefold() not in titulo.casefold():
        titulo = f"{titulo} ({versao})"
    return titulo


# Sufixos de edicao que NAO mudam a gravacao: so' dizem em qual edicao do
# album a faixa aparece (a Apple escreve "Ressaca (Bonus Track)" no Deluxe;
# a Qobuz guarda so' "Ressaca"). Ficam de fora de proposito: Remaster,
# Radio Edit, Tour Edition etc., que identificam outra versao/edicao.
_SUFIXO_EDICAO_INOFENSIVO = re.compile(
    r"\s*\(\s*(?:"
    r"bonus\s+tracks?|faixa\s+bonus|"
    r"(?:deluxe|expanded)(?:\s+(?:edition|version|edicao|versao))?|"
    r"edicao\s+deluxe|explicit|"
    # Procedencia da faixa, nao outra gravacao: "(From The "X" Soundtrack)".
    r"from\s+[^()]*|"
    # "Single Version"/"Album Version": a Qobuz escreve, a Apple costuma omitir.
    r"(?:single|album|original)\s+version"
    r")\s*\)"
)

# Parenteses que contem SOMENTE um marcador de versao ("(Ao Vivo)",
# "(Live)", "(Acoustic Version)"). A identidade da versao e' conferida a
# parte, por conjunto de marcadores, e nao pela similaridade de texto.
_PAREN_MARCADOR_VERSAO = re.compile(
    r"\s*\(\s*(?:live|ao vivo|en vivo|en directo|acoustic|acustico|acustica|"
    r"unplugged|demo|remix|instrumental|extended)"
    r"(?:\s+(?:version|versao|session|sessao|mix))?\s*\)"
)

# Marcadores que mudam a gravacao (ao vivo, acustica, demo...). Se o pedido
# e o candidato nao tiverem o MESMO conjunto, a capa da Apple e' recusada.
_MARCADORES_VERSAO = {
    "live": re.compile(r"\b(?:live|ao vivo|en vivo|en directo)\b"),
    "acoustic": re.compile(r"\b(?:acoustic|acustico|acustica|unplugged)\b"),
    "demo": re.compile(r"\bdemo\b"),
    "remix": re.compile(r"\b(?:remix|rmx)\b"),
    "instrumental": re.compile(r"\binstrumental\b"),
    "extended": re.compile(r"\bextended\b"),
}


# A Apple identifica singles pelo sufixo do nome do álbum ("Ressaca - Single").
# EPs ("- EP") e álbuns são tratados como "completos": a regra de contagem de
# faixas da Qobuz e a da Apple divergem justamente entre EP e álbum, mas
# concordam em quem é single (<= 3 faixas).
_APPLE_SINGLE = re.compile(
    r"\s[-\u2013\u2014]\s*(?:single|sencillo|sencilla)\s*$", re.I
)


def apple_release_kind(collection_name: str) -> str:
    """'single' para "X - Single" da Apple; 'full' para o resto (álbum/EP)."""
    return "single" if _APPLE_SINGLE.search(collection_name or "") else "full"


# Lojas extras, consultadas só quando as principais não resolvem: o catálogo
# varia por país, mas a capa do mesmo lançamento é a mesma.
_APPLE_FALLBACK_COUNTRIES = ("gb", "ca", "au", "de", "fr", "es", "mx", "jp")


def termo_de_busca(texto: Optional[str]) -> str:
    """Texto sem parênteses/colchetes e sem aspas, para usar como termo.
    ...
    """
    limpo = texto or ""
    while True:
        novo = re.sub(r"\s*[\(\[][^\(\)\[\]]*[\)\]]", " ", limpo)
        if novo == limpo:
            break
        limpo = novo
    limpo = limpo.replace('"', " ").replace("\u201c", " ").replace("\u201d", " ")
    return " ".join(limpo.split())


def extrair_titulo_comparavel(texto: str) -> str:
    # Titulo completo SEM os sufixos que nao mudam a gravacao (Bonus Track,
    # Deluxe...) e sem parenteses que sao so' marcador de versao (Ao Vivo,
    # Live...). Os marcadores sao comparados em marcadores_versao().
    """Full title minus harmless edition suffixes and pure version markers."""
    base = extrair_titulo_completo(texto)
    limpo = base
    for _ in range(4):
        novo = _SUFIXO_EDICAO_INOFENSIVO.sub(" ", limpo)
        novo = _PAREN_MARCADOR_VERSAO.sub(" ", novo)
        novo = re.sub(r"\s+", " ", novo).strip()
        if novo == limpo:
            break
        limpo = novo
    limpo = re.sub(r"\s+bonus\s+tracks?\s*$", "", limpo)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    return limpo or base


def faixas_equivalentes(
    pedido: str,
    candidata: str,
    album_pedido: str = "",
    album_candidata: str = "",
    minimo: float = 0.85,
) -> bool:
    """Duas faixas são a mesma gravação? (título comparável + mesma versão)."""
    pedida = marcadores_versao(pedido) | marcadores_versao(album_pedido)
    achada = marcadores_versao(candidata) | marcadores_versao(album_candidata)
    if pedida != achada:
        return False
    a, b = extrair_titulo_comparavel(pedido), extrair_titulo_comparavel(candidata)
    if not a and not b:  # títulos só de pontuação, como ":("
        return (pedido or "").strip().casefold() == (candidata or "").strip().casefold()
    return difflib.SequenceMatcher(None, a, b).ratio() >= minimo


def contar_faixas_em_comum(
    titulos_pedidos, titulos_candidatos, album_pedido="", album_candidata=""
) -> int:
    """Quantas faixas pedidas existem na lista candidata (uma a uma, sem repetir)."""
    livres = list(titulos_candidatos)
    achadas = 0
    for pedido in titulos_pedidos:
        for i, candidata in enumerate(livres):
            if faixas_equivalentes(pedido, candidata, album_pedido, album_candidata):
                achadas += 1
                del livres[i]
                break
    return achadas


def marcadores_versao(texto: str) -> frozenset:
    """Version markers (live, acoustic, demo, remix, instrumental) in a title."""
    base = extrair_titulo_completo(texto)
    return frozenset(nome for nome, rx in _MARCADORES_VERSAO.items() if rx.search(base))


# Separadores que a Apple usa para listar varios artistas no mesmo campo
# (ex.: "Cardi B & Bruno Mars", "J Balvin, Dua Lipa, Bad Bunny & Tainy").
_SEPARADORES_ARTISTAS = re.compile(
    r"\s*(?:,|;|&|\+|/|\band\b|\be\b|\by\b|\bx\b|\bfeat\.?|\bft\.?"
    r"|\bfeaturing\b|\bwith\b|\bcom\b)\s*",
    re.IGNORECASE,
)


def _partes_artista(texto: str) -> set:
    # Devolve o artista inteiro + cada artista individual (normalizados).
    partes = {extrair_essencia(texto)}
    for parte in _SEPARADORES_ARTISTAS.split(texto or ""):
        partes.add(extrair_essencia(parte))
    partes.discard("")
    return partes


def score_artista(pedido: str, candidato: str) -> float:
    # A Qobuz costuma guardar so' o artista principal ("Cardi B") enquanto a
    # Apple lista todos ("Cardi B & Bruno Mars"). Comparar as strings inteiras
    # derrubava o score (0.56) e rejeitava a musica certa. Aqui comparamos
    # cada artista individual dos dois lados e ficamos com o melhor par.
    """Best similarity between any artist of the request and of the candidate."""
    if not extrair_essencia(pedido):
        return 1.0
    melhor = 0.0
    for q in _partes_artista(pedido):
        for c in _partes_artista(candidato):
            melhor = max(melhor, difflib.SequenceMatcher(None, q, c).ratio())
    return melhor


def faixas_mesma_lista(pedidas: int, em_comum: int, na_apple: int) -> bool:
    """True se a lista de faixas da Apple é a MESMA da Qobuz (com tolerância de
    1 faixa por diferença de título). Deluxe x padrão tem faixas a mais, então
    "metade das faixas conferem" não basta: 12 de 16 faixas já é outra edição."""
    if pedidas <= 0:
        return False
    tol = 0 if pedidas <= 3 else (1 if pedidas <= 15 else pedidas // 15)
    return em_comum >= pedidas - tol and abs(na_apple - pedidas) <= tol


async def get_apple_hq_cover(
    session=None,
    upc: Optional[str] = None,
    isrc: Optional[str] = None,
    artist: Optional[str] = None,
    album: Optional[str] = None,
    track_title: Optional[str] = None,
    trace: Optional[list] = None,
    release_kind: Optional[str] = None,
    deep_album_search: bool = False,
    album_tracks: Optional[list] = None,
    countries: Optional[tuple] = None,
    fallback_countries: bool = False,
    strict_edition: bool = False,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Busca uma capa Apple e devolve URL, fonte e motivo se não encontrar.

    trace: lista opcional que recebe TUDO o que foi consultado e decidido
    (requisições com todos os resultados, candidatos aceitos e rejeitados).
    É o que `tools/apple_cover_check.py --all` imprime.

    release_kind: "single" ou "full" (álbum/EP), vindo da Qobuz. Quando
    informado, um single só casa com "X - Single" da Apple e um álbum/EP
    nunca recebe a capa de um single (as artes costumam ser diferentes).
    deep_album_search: na busca por ÁLBUM (sem faixa), também procura nas
    demais fontes de álbuns (só o álbum, só o artista, catálogo do artista).
    album_tracks: títulos das faixas do álbum na Qobuz. Na busca por ÁLBUM, a
    versão certa é confirmada pela LISTA DE FAIXAS (cada álbum candidato da
    Apple tem as faixas listadas e comparadas): é assim que se distingue o
    single do álbum, o Deluxe do padrão e a versão estendida da normal.
    countries: lojas consultadas (padrão: br e us).
    fallback_countries: se as lojas principais não resolverem, tenta também
    gb, ca, au, de, fr, es, mx e jp.
    strict_edition: a edição (Deluxe, Expanded, Remaster...) tem que ser a
    MESMA nos dois sentidos, sem presumir nada: pedido sem edição só aceita
    candidato sem edição, e Deluxe só aceita Deluxe. Explicit/Clean seguem
    neutros. Sem isso, um pedido padrão aceitava a Deluxe quando a faixa só
    aparecia nela. Sem candidato da edição certa, devolve "não encontrada"
    (quem chamou mantém a capa atual) em vez de arriscar a capa errada.

    Resultado: (url, fonte, motivo). Em sucesso, motivo é None. Em falha,
    url/fonte são None e motivo explica a principal causa da rejeição.

    UPC/ISRC são identificadores exatos: quando a Apple devolve um resultado
    por UPC (ou por ISRC no mesmo álbum), ele é aceito sem a checagem fuzzy de
    artista/título, que só vale para a busca textual.
    """

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/113.0.0.0 Safari/537.36"
        )
    }
    countries = tuple(countries) if countries else ("br", "us")
    fallback = (
        tuple(code for code in _APPLE_FALLBACK_COUNTRIES if code not in countries)
        if fallback_countries
        else ()
    )
    resolutions = ("10000x10000bb", "3000x3000bb", "1400x1400bb")
    thresholds = {
        "artist": 0.85,
        "album": 0.80,
        "track": 0.80,
        "full_album": 0.87,
        "full_track": 0.85,
    }

    q_artist = extrair_essencia(artist or "")
    q_album = extrair_essencia(album or "")
    q_track = extrair_essencia(track_title or "")
    q_album_full = titulo_album_comparavel(album or "")
    q_edition = edicao_album(album or "")
    # Titulo da faixa comparavel: sem "(Bonus Track)"/"(Deluxe)" e sem o
    # parenteses "(Ao Vivo)". Um album ao vivo implica faixa ao vivo mesmo
    # quando a Qobuz omite o sufixo no titulo da faixa.
    q_track_full = extrair_titulo_comparavel(track_title or "")
    q_markers = marcadores_versao(track_title or "") | marcadores_versao(album or "")

    # (proximidade, motivo, descricao do candidato) de cada rejeicao. A
    # proximidade permite explicar a falha pelo candidato MAIS PARECIDO, em
    # vez do motivo mais frequente (que vinha de faixas de outros albuns).
    rejections: list[tuple[float, str, str]] = []
    search_had_results = False
    network_errors: list[str] = []
    # Candidatos ACEITOS da busca textual (de todas as etapas e países):
    # (rank, ordem, url, perfeito). A escolha só acontece no fim, entre todos.
    accepted: list[tuple[float, int, str, bool]] = []
    # Resumo da etapa "faixa dentro do álbum" (aparece no motivo da falha, para
    # o log dizer se o álbum foi achado e se a faixa estava nele).
    album_notes: list[str] = []

    def note_album(text: str) -> None:
        if text not in album_notes:
            album_notes.append(text)

    def trace_add(entry: dict) -> None:
        if trace is not None:
            trace.append(entry)

    def record_request(tag: str, url: str, params, data) -> None:
        if trace is None:
            return
        rows = [
            {
                "tipo": r.get("wrapperType") or r.get("kind") or "?",
                "artista": r.get("artistName"),
                "album": r.get("collectionName"),
                "faixa": r.get("trackName"),
                "id": r.get("collectionId") or r.get("artistId"),
            }
            for r in (data or {}).get("results", [])
        ]
        trace.append(
            {
                "type": "request",
                "tag": tag,
                "url": url,
                "params": dict(params or {}),
                "results": rows,
            }
        )

    def reject(reason: str, candidate: str = "", closeness: float = 0.0) -> None:
        # O mesmo candidato pode chegar por mais de uma etapa/consulta.
        if (closeness, reason, candidate) in rejections:
            return
        rejections.append((closeness, reason, candidate))
        trace_add({"type": "reject", "reason": reason, "label": candidate})

    def final_reason() -> str:
        if not search_had_results:
            if network_errors:
                return "Apple API sem resposta válida: " + "; ".join(network_errors[:2])
            return "Apple não retornou resultados nas lojas BR/US para UPC/ISRC e busca textual"
        extras = ""
        if album_notes:
            extras += "; etapa do álbum: " + "; ".join(album_notes[:4])
        if network_errors:
            extras += "; erros de rede: " + "; ".join(network_errors[:2])
        if rejections:
            ordered = sorted(rejections, key=lambda item: item[0], reverse=True)
            detail = (
                f"{ordered[0][1]} (candidato mais próximo; "
                f"{len(rejections)} rejeitado(s) no total)" + extras
            )
            examples = [
                f"{reason}: {label}" for _, reason, label in ordered[:3] if label
            ]
            if examples:
                detail += "; exemplos: " + " | ".join(examples)
            return detail
        return (
            "Apple retornou resultados, mas nenhum tinha capa em resolução acessível"
            + extras
        )

    async def artwork_url(client, artwork: str) -> tuple[Optional[str], Optional[str]]:
        if not artwork:
            return None, "resultado sem artworkUrl100"
        last_error = "nenhuma resolução foi verificada"
        for resolution in resolutions:
            url = artwork.replace("100x100bb", resolution)
            try:
                response = await client.head(
                    url, headers=headers, timeout=5, follow_redirects=True
                )
                if response.status_code == 200:
                    return url, None
                last_error = f"HTTP {response.status_code} em {resolution}"
            except Exception as exc:
                last_error = f"{type(exc).__name__} em {resolution}"
        return None, last_error

    async def evaluate(
        data, client, exact: str = "", country: str = ""
    ) -> Optional[str]:
        # exact: "" = busca textual (fuzzy), "upc" ou "isrc" = lookup exato.
        nonlocal search_had_results
        results = data.get("results", [])
        if results:
            search_had_results = True

        best_url = None
        best_score = 0.0
        junk_words = ("karaoke", "tribute", "cover", "instrumental", "mixed", "remix")
        # (rank, ordem, score, rótulo, proximidade, artwork, perfeito)
        fuzzy_candidates: list = []

        for order, result in enumerate(results):
            candidate_artist = str(result.get("artistName") or "")
            candidate_album = str(result.get("collectionName") or "")
            candidate_track = str(result.get("trackName") or "")
            label = (
                f"artist={candidate_artist!r}, album={candidate_album!r}, "
                f"track={candidate_track!r}"
            )

            if not candidate_album:
                reject("resultado sem collectionName", label)
                continue

            candidate_album_norm = extrair_essencia(candidate_album)
            candidate_track_norm = extrair_essencia(candidate_track)
            artist_score = (
                score_artista(artist or "", candidate_artist) if q_artist else 1.0
            )
            album_score = (
                difflib.SequenceMatcher(None, q_album, candidate_album_norm).ratio()
                if q_album
                else 1.0
            )
            compare_track = bool(track_title and candidate_track)
            track_full_score = track_score = 1.0
            if compare_track:
                track_full_score = difflib.SequenceMatcher(
                    None, q_track_full, extrair_titulo_comparavel(candidate_track)
                ).ratio()
                track_score = difflib.SequenceMatcher(
                    None, q_track, candidate_track_norm
                ).ratio()
            scores = [artist_score, album_score] + (
                [track_full_score] if compare_track else []
            )
            closeness = sum(scores) / len(scores)

            # --- Edição do álbum (Deluxe x padrão...): a capa muda de uma
            # edição pra outra. O UPC identifica a release exata e fica de
            # fora. O ISRC identifica a GRAVAÇÃO (que está em várias edições),
            # então exige edição idêntica nos dois sentidos. Na busca textual,
            # se o pedido tem edição (Deluxe...), o candidato tem que ser a
            # mesma; pedido SEM edição continua aceitando a Deluxe quando a
            # faixa só existe nela (ex.: Jão, "Ressaca (Bonus Track)"), com
            # preferência pela edição idêntica.
            if (
                q_album
                and exact != "upc"
                and (exact == "isrc" or q_edition or strict_edition)
            ):
                candidate_edition = edicao_album(candidate_album)
                if candidate_edition != q_edition:
                    reject(
                        f"edição do álbum incompatível "
                        f"(pedido={sorted(q_edition) or 'padrão'}, "
                        f"Apple={sorted(candidate_edition) or 'padrão'}; "
                        f"álbum Apple={candidate_album!r})",
                        label,
                        closeness,
                    )
                    continue

            # --- Lookup exato por UPC/ISRC: o identificador ja' diz que e' a
            # mesma release/gravacao, entao nao reprovamos por artista/titulo
            # (colaboracoes, "- Single", versao no titulo etc.).
            if exact:
                if exact == "isrc" and q_album and album_score < 0.45:
                    # O mesmo ISRC pode aparecer em coletaneas: nesse caso so'
                    # aceitamos se o album tiver pelo menos alguma relacao.
                    reject(
                        f"ISRC encontrado em outro álbum ({album_score:.2f} < 0.45; "
                        f"pedido={album!r}, Apple={candidate_album!r})",
                        label,
                        closeness,
                    )
                    continue
                if (
                    exact == "isrc"
                    and q_album
                    and release_kind in ("single", "full")
                    and apple_release_kind(candidate_album) != release_kind
                ):
                    # A mesma gravação (mesmo ISRC) aparece no single, no álbum
                    # e em coletâneas: só vale o lançamento do mesmo tipo.
                    reject(
                        f"ISRC encontrado em lançamento de outro tipo "
                        f"(pedido={'single' if release_kind == 'single' else 'álbum/EP'}"
                        f"; álbum Apple={candidate_album!r})",
                        label,
                        closeness,
                    )
                    continue
                # Edição estrita: o UPC da Qobuz nem sempre é o da edição que a
                # Apple vende com esse código. Antes de aceitar, a lista de
                # faixas da Apple tem que ser a mesma da Qobuz.
                if (
                    strict_edition
                    and exact == "upc"
                    and album_tracks
                    and result.get("collectionId")
                ):
                    lookup_tracks = await cached_get(
                        client,
                        "https://itunes.apple.com/lookup",
                        {
                            "id": result.get("collectionId"),
                            "entity": "song",
                            "country": country or "us",
                            "limit": 200,
                        },
                        f"lookup álbum {country or 'us'}",
                    )
                    apple_titles = [
                        str(t.get("trackName"))
                        for t in (lookup_tracks or {}).get("results", [])
                        if t.get("trackName")
                    ]
                    common = contar_faixas_em_comum(
                        album_tracks, apple_titles, album or "", candidate_album
                    )
                    if apple_titles and not faixas_mesma_lista(
                        len(album_tracks), common, len(apple_titles)
                    ):
                        reject(
                            f"UPC aponta para outra edição (faixas diferentes: "
                            f"{common}/{len(album_tracks)} da Qobuz, Apple tem "
                            f"{len(apple_titles)}; álbum Apple={candidate_album!r})",
                            label,
                            closeness,
                        )
                        continue
                valid_url, url_error = await artwork_url(
                    client, str(result.get("artworkUrl100") or "")
                )
                if not valid_url:
                    reject(
                        f"URL da imagem não validada ({url_error})", label, closeness
                    )
                    continue
                score = (artist_score + album_score) / 2.0
                if exact == "isrc" and q_album_full:
                    # Entre os álbuns com a mesma gravação, vence o de título
                    # idêntico ao pedido (edição certa).
                    score += (
                        0.05
                        * difflib.SequenceMatcher(
                            None, q_album_full, titulo_album_comparavel(candidate_album)
                        ).ratio()
                    )
                if score > best_score or best_url is None:
                    best_score = score
                    best_url = valid_url
                continue

            # --- Busca textual (fuzzy): mantem todas as travas.
            haystack = f"{candidate_album} {candidate_track}".casefold()
            requested_text = f"{album or ''} {track_title or ''}".casefold()
            junk = [
                word
                for word in junk_words
                if word in haystack and word not in requested_text
            ]
            if junk:
                reject(f"termo indesejado {junk[0]!r}", label, closeness)
                continue

            if release_kind in ("single", "full"):
                candidate_kind = apple_release_kind(candidate_album)
                if candidate_kind != release_kind:
                    reject(
                        f"tipo de lançamento incompatível "
                        f"(pedido={'single' if release_kind == 'single' else 'álbum/EP'}, "
                        f"Apple={'single' if candidate_kind == 'single' else 'álbum/EP'}; "
                        f"álbum Apple={candidate_album!r})",
                        label,
                        closeness,
                    )
                    continue

            if compare_track:
                candidate_markers = marcadores_versao(
                    candidate_track
                ) | marcadores_versao(candidate_album)
                if candidate_markers != q_markers:
                    reject(
                        f"versão da faixa incompatível "
                        f"(pedido={sorted(q_markers) or 'estúdio'}, "
                        f"Apple={sorted(candidate_markers) or 'estúdio'}; "
                        f"pedido={track_title!r}, Apple={candidate_track!r})",
                        label,
                        closeness,
                    )
                    continue
                if track_full_score < thresholds["full_track"]:
                    reject(
                        f"título completo da faixa incompatível "
                        f"({track_full_score:.2f} < {thresholds['full_track']:.2f}; "
                        f"pedido={track_title!r}, Apple={candidate_track!r})",
                        label,
                        closeness,
                    )
                    continue
                if track_score < thresholds["track"]:
                    reject(
                        f"similaridade da faixa insuficiente "
                        f"({track_score:.2f} < {thresholds['track']:.2f})",
                        label,
                        closeness,
                    )
                    continue
            else:
                track_score = 1.0
                if not track_title:
                    album_full_score = difflib.SequenceMatcher(
                        None,
                        q_album_full,
                        titulo_album_comparavel(candidate_album),
                    ).ratio()
                    if album_full_score < thresholds["full_album"]:
                        reject(
                            f"edição/título do álbum incompatível "
                            f"({album_full_score:.2f} < {thresholds['full_album']:.2f})",
                            label,
                            closeness,
                        )
                        continue

            if artist_score < thresholds["artist"]:
                reject(
                    f"artista incompatível ({artist_score:.2f} < "
                    f"{thresholds['artist']:.2f}; pedido={artist!r}, "
                    f"Apple={candidate_artist!r})",
                    label,
                    closeness,
                )
                continue

            if track_title:
                if album_score < 0.45:
                    reject(
                        f"álbum muito diferente ({album_score:.2f} < 0.45; "
                        f"pedido={album!r}, Apple={candidate_album!r})",
                        label,
                        closeness,
                    )
                    continue
            elif album_score < thresholds["album"]:
                reject(
                    f"álbum incompatível ({album_score:.2f} < "
                    f"{thresholds['album']:.2f}; pedido={album!r}, "
                    f"Apple={candidate_album!r})",
                    label,
                    closeness,
                )
                continue

            divisor = 3.0 if track_title else 2.0
            score = (
                artist_score + album_score + (track_score if track_title else 0)
            ) / divisor
            if score < 0.80:
                reject(
                    f"pontuação combinada baixa ({score:.2f} < 0.80)", label, closeness
                )
                continue

            # Edição do álbum: entre candidatos igualmente válidos, o álbum de
            # título idêntico ao pedido (ex.: "Lobos" e não "LOBOS (Deluxe)")
            # vence. A validação da imagem (HEAD) fica para o melhor colocado.
            album_full_sim = (
                difflib.SequenceMatcher(
                    None, q_album_full, titulo_album_comparavel(candidate_album)
                ).ratio()
                if q_album_full
                else 1.0
            )
            fuzzy_candidates.append(
                (
                    score + 0.05 * album_full_sim,
                    order,
                    score,
                    label,
                    closeness,
                    str(result.get("artworkUrl100") or ""),
                    score >= 0.999 and album_full_sim >= 0.999,
                )
            )

        if not exact:
            fuzzy_candidates.sort(key=lambda item: (-item[0], item[1]))
            for (
                rank,
                _order,
                _score,
                label,
                closeness,
                artwork,
                perfect,
            ) in fuzzy_candidates:
                valid_url, url_error = await artwork_url(client, artwork)
                if not valid_url:
                    reject(
                        f"URL da imagem não validada ({url_error})", label, closeness
                    )
                    continue
                accepted.append((rank, len(accepted), valid_url, perfect))
                trace_add(
                    {"type": "accept", "rank": rank, "perfect": perfect, "label": label}
                )
                best_url = valid_url
                break

        return best_url

    def pick_best() -> tuple[Optional[str], Optional[str], Optional[str]]:
        # Maior rank; em empate vale o primeiro avaliado (a Apple já ordena
        # por relevância).
        best = max(accepted, key=lambda item: (item[0], -item[1]))
        return best[2], "Apple/iTunes", None

    async def cached_get(client, url: str, params: dict, tag: str):
        # Respostas de álbum são reaproveitadas na mesma sessão: um retag de
        # álbum inteiro consulta o mesmo álbum uma vez, não uma por faixa.
        cache = getattr(client, "_apple_json_cache", None)
        if cache is None:
            cache = {}
            try:
                client._apple_json_cache = cache
            except Exception:
                # Cliente sem atributo gravável: segue sem cache.
                pass
        key = (url, tuple(sorted(params.items())))
        if key in cache:
            return cache[key]
        response = await client.get(url, params=params, headers=headers, timeout=8)
        if response.status_code != 200:
            network_errors.append(f"{tag}: HTTP {response.status_code}")
            return None
        data = response.json()
        cache[key] = data
        record_request(tag, url, params, data)
        return data

    async def collect_album_results(client, country: str) -> list:
        # A busca da Apple é irregular (um termo "Artista Álbum" pode devolver
        # só o álbum ao vivo), então os álbuns vêm de várias fontes: busca por
        # artista+álbum, só o álbum, só o artista e o catálogo do artista
        # (musicArtist -> lookup).
        search_url = "https://itunes.apple.com/search"
        lookup_url = "https://itunes.apple.com/lookup"
        results: list = []
        for params, tag in (
            (
                {
                    "term": f"{artist} {album}".strip(),
                    "entity": "album",
                    "country": country,
                    "limit": 25,
                },
                f"álbuns: artista+álbum {country}",
            ),
            (
                {"term": album, "entity": "album", "country": country, "limit": 50},
                f"álbuns: só o álbum {country}",
            ),
            (
                {
                    "term": f"{artist} {termo_de_busca(album)}".strip(),
                    "entity": "album",
                    "country": country,
                    "limit": 25,
                },
                f"álbuns: artista+álbum sem parênteses {country}",
            ),
            (
                {"term": artist, "entity": "album", "country": country, "limit": 200},
                f"álbuns: só o artista {country}",
            ),
        ):
            data = await cached_get(client, search_url, params, f"search {tag}")
            results.extend((data or {}).get("results", []))

        artist_data = await cached_get(
            client,
            search_url,
            {"term": artist, "entity": "musicArtist", "country": country, "limit": 5},
            f"search artista {country}",
        )
        artist_ids = [
            item["artistId"]
            for item in (artist_data or {}).get("results", [])
            if item.get("artistId")
            and score_artista(artist or "", str(item.get("artistName") or ""))
            >= thresholds["artist"]
        ][:2]
        for artist_id in artist_ids:
            data = await cached_get(
                client,
                lookup_url,
                {
                    "id": artist_id,
                    "entity": "album",
                    "country": country,
                    "limit": 200,
                },
                f"lookup catálogo do artista {country}",
            )
            results.extend((data or {}).get("results", []))
        return results

    async def accept_best(client, candidates: list) -> Optional[str]:
        # candidates: (rank, ordem, rótulo, proximidade, artwork, perfeito).
        # Valida a imagem só do melhor colocado e registra o aceito.
        best_url = None
        for rank, _order, label, closeness, artwork, perfect in sorted(
            candidates, key=lambda item: (-item[0], item[1])
        ):
            valid_url, url_error = await artwork_url(client, artwork)
            if not valid_url:
                reject(f"URL da imagem não validada ({url_error})", label, closeness)
                continue
            accepted.append((rank, len(accepted), valid_url, perfect))
            trace_add(
                {"type": "accept", "rank": rank, "perfect": perfect, "label": label}
            )
            best_url = valid_url
            break
        return best_url

    async def verify_albums_by_tracklist(client, country: str) -> None:
        # Álbum: cada álbum candidato da Apple (mesmo artista, nome, tipo e
        # versão) tem as faixas listadas e comparadas com as da Qobuz. Só vale
        # o álbum cuja lista bate (single x álbum, Deluxe x padrão, versão
        # estendida x normal), e vence o que mais se parece.
        nonlocal search_had_results
        results = await collect_album_results(client, country)
        unique_albums = {}
        for result in results:
            collection_id = result.get("collectionId")
            if collection_id and collection_id not in unique_albums:
                unique_albums[collection_id] = result
        if unique_albums:
            search_had_results = True
        else:
            note_album(f"{country}: a busca de álbum não retornou resultados")
            return

        junk = ("karaoke", "tribute", "cover", "instrumental", "mixed", "remix")
        requested_text = (album or "").casefold()
        requested_markers = marcadores_versao(album or "")
        picks = []
        for order, (collection_id, result) in enumerate(unique_albums.items()):
            name = str(result.get("collectionName") or "")
            if not name:
                continue
            if (
                score_artista(artist or "", str(result.get("artistName") or ""))
                < (thresholds["artist"])
            ):
                continue
            if (
                difflib.SequenceMatcher(None, q_album, extrair_essencia(name)).ratio()
                < thresholds["album"]
            ):
                continue
            if marcadores_versao(name) != requested_markers:
                continue
            if (q_edition or strict_edition) and edicao_album(name) != q_edition:
                continue
            if release_kind in ("single", "full") and (
                apple_release_kind(name) != release_kind
            ):
                continue
            if any(w in name.casefold() and w not in requested_text for w in junk):
                continue
            similarity = difflib.SequenceMatcher(
                None, q_album_full, titulo_album_comparavel(name)
            ).ratio()
            picks.append((-similarity, order, collection_id, name, result))

        if not picks:
            note_album(
                f"{country}: nenhum álbum compatível com {album!r} "
                f"({len(unique_albums)} vistos)"
            )
            return

        picks.sort(key=lambda item: (item[0], item[1]))
        wanted = len(album_tracks or [])
        candidates = []
        for neg_similarity, order, collection_id, name, result in picks[:8]:
            data = await cached_get(
                client,
                "https://itunes.apple.com/lookup",
                {
                    "id": collection_id,
                    "entity": "song",
                    "country": country,
                    "limit": 200,
                },
                f"lookup álbum {country}",
            )
            titles = [
                str(item.get("trackName"))
                for item in (data or {}).get("results", [])
                if item.get("trackName")
            ]
            matched = contar_faixas_em_comum(album_tracks, titles, album or "", name)
            union = wanted + len(titles) - matched
            jaccard = matched / union if union else 0.0
            note_album(
                f"{country}: {name!r} ({len(titles)} faixas, "
                f"{matched}/{wanted} conferem)"
            )
            label = (
                f"artist={result.get('artistName')!r}, album={name!r}, "
                f"faixas Apple={len(titles)}"
            )
            if (
                not faixas_mesma_lista(wanted, matched, len(titles))
                if strict_edition
                else (matched < max(1, -(-wanted // 2)) or jaccard < 0.5)
            ):
                reject(
                    f"faixas do álbum não conferem ({matched}/{wanted} da Qobuz "
                    f"estão no álbum Apple, que tem {len(titles)})",
                    label,
                    jaccard,
                )
                continue
            similarity = -neg_similarity
            candidates.append(
                (
                    jaccard + 0.05 * similarity,
                    order,
                    label,
                    jaccard,
                    str(result.get("artworkUrl100") or ""),
                    jaccard >= 0.999 and similarity >= 0.999,
                )
            )
        await accept_best(client, candidates)

    async def search_inside_album(client, country: str) -> None:
        # Acha os álbuns Apple que são o álbum pedido (mesmo artista, mesmo
        # nome, mesmo tipo de versão), lista as faixas de cada um e avalia
        # TODAS contra a faixa pedida.
        nonlocal search_had_results
        if not (track_title and q_artist and q_album):
            return
        lookup_url = "https://itunes.apple.com/lookup"
        results = await collect_album_results(client, country)

        unique_albums = {}
        for result in results:
            collection_id = result.get("collectionId")
            if collection_id and collection_id not in unique_albums:
                unique_albums[collection_id] = result
        if unique_albums:
            search_had_results = True
        else:
            note_album(f"{country}: a busca de álbum não retornou resultados")

        album_junk = ("karaoke", "tribute", "cover", "instrumental", "mixed", "remix")
        requested_text = f"{album or ''} {track_title or ''}".casefold()
        requested_album_markers = marcadores_versao(album or "")
        picks = []
        for order, (collection_id, result) in enumerate(unique_albums.items()):
            candidate_album = str(result.get("collectionName") or "")
            if not candidate_album:
                continue
            if (
                score_artista(artist or "", str(result.get("artistName") or ""))
                < (thresholds["artist"])
            ):
                continue
            if (
                difflib.SequenceMatcher(
                    None, q_album, extrair_essencia(candidate_album)
                ).ratio()
                < thresholds["album"]
            ):
                continue
            if marcadores_versao(candidate_album) != requested_album_markers:
                continue
            if (q_edition or strict_edition) and (
                edicao_album(candidate_album) != q_edition
            ):
                continue
            if release_kind in ("single", "full") and (
                apple_release_kind(candidate_album) != release_kind
            ):
                continue
            lowered = candidate_album.casefold()
            if any(w in lowered and w not in requested_text for w in album_junk):
                continue
            similarity = difflib.SequenceMatcher(
                None, q_album_full, titulo_album_comparavel(candidate_album)
            ).ratio()
            picks.append((-similarity, order, collection_id, candidate_album))

        if unique_albums and not picks:
            note_album(
                f"{country}: nenhum álbum compatível com {album!r} "
                f"({len(unique_albums)} vistos)"
            )
        picks.sort(key=lambda item: (item[0], item[1]))
        for _similarity, _order, collection_id, collection_name in picks[:5]:
            tracks_data = await cached_get(
                client,
                lookup_url,
                {
                    "id": collection_id,
                    "entity": "song",
                    "country": country,
                    "limit": 200,
                },
                f"lookup álbum {country}",
            )
            tracks = [
                item
                for item in (tracks_data or {}).get("results", [])
                if item.get("trackName")
            ]
            note_album(f"{country}: {collection_name!r} ({len(tracks)} faixas)")
            if tracks:
                await evaluate({"results": tracks}, client)

    async def run_group(
        client, group_countries
    ) -> Optional[tuple[Optional[str], Optional[str], Optional[str]]]:
        # Primeiro lookup exato por UPC/ISRC.
        for code, kind in ((upc, "upc"), (isrc, "isrc")):
            if not code or str(code).strip().lower() in ("n/a", "none"):
                continue
            for country in group_countries:
                try:
                    response = await client.get(
                        f"https://itunes.apple.com/lookup?{kind}="
                        f"{urllib.parse.quote(str(code))}&country={country}",
                        headers=headers,
                        timeout=8,
                    )
                    if response.status_code != 200:
                        network_errors.append(
                            f"lookup {kind}/{country}: HTTP {response.status_code}"
                        )
                        continue
                    data = response.json()
                    record_request(
                        f"lookup {kind} {country}",
                        f"https://itunes.apple.com/lookup?{kind}={code}&country={country}",
                        None,
                        data,
                    )
                    url = await evaluate(data, client, exact=kind, country=country)
                    if url:
                        return url, "Apple/iTunes", None
                except Exception as exc:
                    network_errors.append(
                        f"lookup {kind}/{country}: {type(exc).__name__}: {exc}"
                    )

        # Busca textual. Nada aqui devolve "o primeiro que apareceu": todos os
        # candidatos de todas as etapas e países são avaliados e a escolha
        # acontece no fim. Só um candidato PERFEITO (artista, álbum e título
        # idênticos, sem o que melhorar) encerra a busca antes.
        query = f"{artist or ''} {track_title if track_title else album or ''}".strip()
        verify_mode = bool(album_tracks) and not track_title and q_artist and q_album
        if query:
            entity = "song" if track_title else "album"
            for country in group_countries:
                # Álbum com a lista de faixas da Qobuz: a versão certa é
                # confirmada pela lista (a busca por nome sozinha não basta).
                if verify_mode:
                    try:
                        await verify_albums_by_tracklist(client, country)
                    except Exception as exc:
                        network_errors.append(
                            f"álbuns {country}: {type(exc).__name__}: {exc}"
                        )
                    if any(item[3] for item in accepted):
                        return pick_best()
                    continue

                try:
                    response = await client.get(
                        "https://itunes.apple.com/search",
                        params={
                            "term": query,
                            "entity": entity,
                            "country": country,
                            "limit": 50 if track_title else 25,
                        },
                        headers=headers,
                        timeout=8,
                    )
                    if response.status_code != 200:
                        network_errors.append(
                            f"search {country}: HTTP {response.status_code}"
                        )
                    else:
                        main_data = response.json()
                        record_request(
                            f"busca principal {entity} {country}",
                            "https://itunes.apple.com/search",
                            {
                                "term": query,
                                "entity": entity,
                                "country": country,
                                "limit": 50 if track_title else 25,
                            },
                            main_data,
                        )
                        await evaluate(main_data, client)
                except Exception as exc:
                    network_errors.append(
                        f"search {country}: {type(exc).__name__}: {exc}"
                    )
                if any(item[3] for item in accepted):
                    return pick_best()

                # Faixa: segunda busca de músicas, agora pelo nome do ÁLBUM
                # (traz as faixas do álbum, não só as que casam com o título).
                if track_title and album:
                    try:
                        album_songs = await cached_get(
                            client,
                            "https://itunes.apple.com/search",
                            {
                                "term": f"{artist or ''} {album}".strip(),
                                "entity": "song",
                                "country": country,
                                "limit": 200,
                            },
                            f"search faixas do álbum {country}",
                        )
                        if album_songs:
                            await evaluate(album_songs, client)
                    except Exception as exc:
                        network_errors.append(
                            f"faixas do álbum {country}: {type(exc).__name__}: {exc}"
                        )
                    if any(item[3] for item in accepted):
                        return pick_best()

                # Faixa: termo limpo. Títulos com "(From The "X" Soundtrack)" e
                # aspas pioram a busca; o que está entre parênteses é conferido
                # depois, na avaliação.
                clean_title = termo_de_busca(track_title) if track_title else ""
                if clean_title and clean_title != (track_title or "").strip():
                    try:
                        clean_songs = await cached_get(
                            client,
                            "https://itunes.apple.com/search",
                            {
                                "term": f"{artist or ''} {clean_title}".strip(),
                                "entity": "song",
                                "country": country,
                                "limit": 50,
                            },
                            f"search faixa sem parênteses {country}",
                        )
                        if clean_songs:
                            await evaluate(clean_songs, client)
                    except Exception as exc:
                        network_errors.append(
                            f"faixa limpa {country}: {type(exc).__name__}: {exc}"
                        )
                    if any(item[3] for item in accepted):
                        return pick_best()

                # Álbum (usado pelo retag, que decide a capa por álbum): as
                # demais fontes de álbuns, avaliadas com as mesmas travas.
                if deep_album_search and not track_title and q_artist and q_album:
                    try:
                        deep_results = {
                            item["collectionId"]: item
                            for item in await collect_album_results(client, country)
                            if item.get("collectionId")
                        }
                        if deep_results:
                            await evaluate(
                                {"results": list(deep_results.values())}, client
                            )
                    except Exception as exc:
                        network_errors.append(
                            f"álbuns {country}: {type(exc).__name__}: {exc}"
                        )
                    if any(item[3] for item in accepted):
                        return pick_best()

                # Faixa: procura o ÁLBUM certo e confere a faixa dentro dele.
                # A busca por faixa pode trazer só outra versão (ex.: ao vivo)
                # e nunca listar a gravação do álbum pedido.
                if track_title:
                    try:
                        await search_inside_album(client, country)
                    except Exception as exc:
                        network_errors.append(
                            f"álbum {country}: {type(exc).__name__}: {exc}"
                        )
                    if any(item[3] for item in accepted):
                        return pick_best()

        if accepted:
            return pick_best()
        return None

    async def search(client) -> tuple[Optional[str], Optional[str], Optional[str]]:
        # Lojas principais primeiro; as extras só se nada foi aceito.
        for group in [countries] + ([fallback] if fallback else []):
            found = await run_group(client, group)
            if found:
                return found
        return None, None, final_reason()

    if session is not None:
        return await search(session)

    async with make_client() as client:
        return await search(client)
