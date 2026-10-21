# ============================================================================
# artist_role.py -- descobre a FUNCAO do artista pesquisado numa faixa
# (Principal, Feat., Compositor, Produtor...) a partir do JSON do Qobuz.
#
# Por que existe: na tela de resultados de faixas a coluna "TIPO" mostrava
# sempre "Track" (informacao inutil). Pesquisando "The Weeknd" o usuario
# nao sabia se a faixa era DELE ou so' continha o nome no titulo/album
# (ex.: "Hymn for the Weekend", "Lost Weekend"), nem se ele so' participou.
#
# Modulo puro (sem rede, sem prompt_toolkit) pra ser testado isoladamente e
# reaproveitado por qualquer tela que liste faixas.
#
# Fontes usadas, da mais rica pra mais pobre:
#   1) campo `performers` ("Nome, Role, Role - Outro Nome, Role"): so' vem
#      quando a API manda os creditos completos;
#   2) `performer.name` (artista principal), `composer.name`;
#   3) "(feat. X)" no titulo/versao.
# Se a API nao mandar `performers` na busca, os itens 2 e 3 ainda cobrem o
# caso mais comum (principal e feat.). Compositor/produtor so' aparecem
# quando os creditos completos estao no JSON.
# ============================================================================
import re
import unicodedata

from qobuz_dl.fuzzy import ratio

SEM_FUNCAO = "--"

# Ordem de prioridade: a primeira funcao encontrada e' a exibida na tabela.
_ORDEM = ["Principal", "Feat.", "Compositor", "Produtor", "Mixagem", "Músico", "Outro"]

# Roles conhecidos do Qobuz (minusculos). Usado so' pra separar o NOME dos
# roles no bloco "Nome, Role, Role" sem quebrar nomes com virgula
# ("Tyler, The Creator").
_ROLES_CONHECIDOS = {
    "mainartist",
    "primaryartist",
    "featuredartist",
    "composer",
    "lyricist",
    "composerlyricist",
    "writer",
    "author",
    "producer",
    "coproducer",
    "executiveproducer",
    "mixer",
    "mixingengineer",
    "recordingengineer",
    "masteringengineer",
    "engineer",
    "studiopersonnel",
    "associatedperformer",
    "performer",
    "vocals",
    "guitar",
    "bass",
    "drums",
    "keyboards",
    "piano",
    "arranger",
    "remixer",
    "assistant",
    "other",
}
_RE_ROLE_COMPOSTO = re.compile(r"^[A-Z][a-z]+(?:[A-Z][a-z]+)+$")

_RE_FEAT = re.compile(
    r"[\(\[]\s*(?:feat\.?|ft\.?|featuring|with|com|part\.?)\s+([^\)\]]+)[\)\]]",
    re.IGNORECASE,
)
_RE_SEP_NOMES = re.compile(r"\s*(?:,|&|\be\b|\band\b|\bx\b)\s*", re.IGNORECASE)


def _norm(texto):
    """minusculo, sem acento, so' letras/numeros/espaco, sem 'the ' inicial."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = re.sub(r"[^a-z0-9]+", " ", t).strip()
    if t.startswith("the "):
        t = t[4:]
    return t


def nome_casa_com_consulta(nome, consulta):
    """True se `nome` (artista) e' o artista procurado em `consulta`.

    Casa por igualdade, por palavra inteira (nome dentro da consulta, ex.:
    "blinding lights the weeknd", ou consulta dentro do nome, ex.: "weeknd"
    em "The Weeknd") e, como ultimo recurso, por similaridade >= 0.85 pra
    tolerar erro de digitacao. Nao usa substring solta: "art" nao casa com
    "Arctic Monkeys".
    """
    n, q = _norm(nome), _norm(consulta)
    if not n or not q:
        return False
    if n == q:
        return True
    if f" {n} " in f" {q} ":
        return True
    if len(q) >= 3 and f" {q} " in f" {n} ":
        return True
    if min(len(n), len(q)) >= 4 and ratio(n, q) >= 0.85:
        return True
    return False


def _grupo_do_role(role):
    r = re.sub(r"[^a-z]", "", str(role).lower())
    if r in ("mainartist", "primaryartist", "artist"):
        return "Principal"
    if r in ("featuredartist", "featuring"):
        return "Feat."
    if r in (
        "composer",
        "lyricist",
        "composerlyricist",
        "writer",
        "author",
        "songwriter",
    ):
        return "Compositor"
    if "producer" in r:
        return "Produtor"
    if "mix" in r or "engineer" in r or "master" in r:
        return "Mixagem"
    if r in ("associatedperformer", "performer", "vocals", "guitar", "bass", "drums"):
        return "Músico"
    return "Outro"


def _parse_performers(texto):
    """'A, MainArtist - B, Producer, Composer' -> [('A',['MainArtist']), ...]"""
    saida = []
    for bloco in str(texto or "").split(" - "):
        partes = [p.strip() for p in bloco.split(",") if p.strip()]
        if len(partes) < 2:
            continue
        nome = [partes[0]]
        roles = []
        for p in partes[1:]:
            if p.lower() in _ROLES_CONHECIDOS or _RE_ROLE_COMPOSTO.match(p):
                roles.append(p)
            elif not roles:
                nome.append(p)  # parte do nome ("Tyler, The Creator")
        if roles:
            saida.append((", ".join(nome), roles))
    return saida


def _pessoas(item):
    """Lista de (nome, [grupos]) extraida de todas as fontes do item."""
    pessoas = []
    for nome, roles in _parse_performers(item.get("performers")):
        pessoas.append((nome, [_grupo_do_role(r) for r in roles]))

    performer = item.get("performer")
    if isinstance(performer, dict) and performer.get("name"):
        pessoas.append((performer["name"], ["Principal"]))

    composer = item.get("composer")
    if isinstance(composer, dict) and composer.get("name"):
        pessoas.append((composer["name"], ["Compositor"]))

    # Resultados de álbum (inclusive Singles) normalmente não trazem
    # `performer`; a API usa `artist`. Mantemos a mesma semântica: quando o
    # artista pesquisado é o artista do lançamento, ele é o Principal.
    artista = item.get("artist")
    if isinstance(artista, dict) and artista.get("name"):
        pessoas.append((artista["name"], ["Principal"]))
    elif isinstance(artista, str) and artista.strip():
        pessoas.append((artista, ["Principal"]))

    album = item.get("album")
    if isinstance(album, dict):
        album_artista = album.get("artist")
        if isinstance(album_artista, dict) and album_artista.get("name"):
            pessoas.append((album_artista["name"], ["Principal"]))

    titulo = f"{item.get('title') or ''} {item.get('version') or ''}"
    for m in _RE_FEAT.finditer(titulo):
        for nome in _RE_SEP_NOMES.split(m.group(1)):
            if nome.strip():
                pessoas.append((nome.strip(), ["Feat."]))
    return pessoas


def funcao_do_artista(item, consulta):
    """Retorna (rotulo_curto, funcoes_completas) do artista `consulta` em `item`.

    Devolve apenas UMA funcao, a de maior prioridade na cadeia
    Principal > Feat. > Compositor > Produtor > Mixagem > Músico > Outro.
    Os dois valores sao iguais (mantidos separados so' pela API do meta).
    Sem consulta (favoritos, playlists) ou sem correspondencia: ("--", "--").
    """
    if not consulta or not isinstance(item, dict):
        return SEM_FUNCAO, SEM_FUNCAO

    achadas = set()
    for nome, grupos in _pessoas(item):
        if nome_casa_com_consulta(nome, consulta):
            achadas.update(grupos)

    if not achadas:
        return SEM_FUNCAO, SEM_FUNCAO

    # Cadeia de prioridade (_ORDEM): mostra SÓ a função de maior prioridade
    # entre as que o artista tem na faixa.
    principal = next(g for g in _ORDEM if g in achadas)
    return principal, principal
