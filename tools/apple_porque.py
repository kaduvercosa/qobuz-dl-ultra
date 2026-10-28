"""Por que a capa Apple NÃO foi (ou não seria) trocada?

Roda a MESMA busca do `tags` (`get_apple_hq_cover`, com UPC/ISRC lidos das tags
do arquivo) e explica o resultado em linguagem simples, com um checklist:

    Artista na Apple   ✔/✘
    Álbum na Apple     ✔/✘
    Música na Apple    ✔/✘
    Mesma versão       ✔/✘
    Mesmo tipo         ✔/✘  (single x álbum)

e UM veredito:

    TROCARIA     a capa Apple foi aceita
    NAO TEM      a Apple não devolveu esta música (artista+título)
    OUTRO ALBUM  a Apple tem a música, mas só em outro álbum/lançamento
    OUTRA VERSAO a Apple tem a música, mas só em outra versão (ao vivo, remix...)
    TITULO       a Apple tem faixa PARECIDA no mesmo álbum, título não bate
    FILTRO       a Apple tem a música no álbum certo, mas um filtro recusou
                 (aqui está o possível bug: o motivo exato é mostrado)
    INCONCLUSIVO rede/limite da Apple: não dá para afirmar nada

Três usos (só isso):

    python tools/apple_porque.py                    # pasta de downloads do config.ini
    python tools/apple_porque.py ~/Music/Pasta      # outra pasta
    python tools/apple_porque.py "Belly" "See You Next Wednesday" "Die For It"   # uma faixa

Filtros úteis na pasta:  --filtro "Die For It"  (só faixas que contêm o texto)
                         --so-problemas         (esconde as que trocariam)

Limite conhecido: simula o caminho POR FAIXA do `tags`. O `tags` real ainda
tenta antes a capa do ÁLBUM inteiro (lista de faixas, lojas extras). Uma faixa
que aqui "não trocaria" ainda pode receber a capa do álbum no `tags`. Use
--lojas-extras para incluir também gb, ca, au, de, fr, es, mx, jp.
"""

import argparse
import asyncio
import csv
import difflib
import os
import sys
import textwrap
import time
import unicodedata
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qobuz_dl.utils import (  # noqa: E402
    apple_release_kind,
    classify_release_type,
    extrair_essencia,
    extrair_titulo_comparavel,
    get_apple_hq_cover,
    marcadores_versao,
    score_artista,
)

# Mesmos limites do get_apple_hq_cover (utils.py). "TITULO_PARECIDO" só serve
# para diagnosticar (achar a faixa que um humano chamaria de "a mesma").
LIM_ARTISTA = 0.85
LIM_ALBUM = 0.80
LIM_TITULO = 0.85
LIM_TITULO_PARECIDO = 0.60

VEREDITOS = {
    "troca": ("TROCARIA", "a capa Apple foi aceita"),
    "nao_tem": ("NAO TEM", "a Apple não devolveu esta música"),
    "outro_album": (
        "OUTRO ALBUM",
        "a Apple tem a música, mas só em outro álbum/lançamento",
    ),
    "versao": ("OUTRA VERSAO", "a Apple tem a música, mas só em outra versão"),
    "titulo": (
        "TITULO",
        "a Apple tem faixa parecida no mesmo álbum, mas o título não bate",
    ),
    "filtro": ("FILTRO", "a Apple tem a música no álbum certo, mas um filtro recusou"),
    "rede": ("INCONCLUSIVO", "a Apple não respondeu direito (rede/limite)"),
}

O_QUE_FAZER = {
    "troca": "",
    "nao_tem": (
        "Se a música EXISTE no Apple Music, a busca não a trouxe: rode "
        'python tools/apple_cover_check.py "{artista}" "{album}" "{faixa}" --all '
        "para ver tudo que a Apple devolve. Se não existe no catálogo, manter a "
        "capa do Qobuz é o comportamento correto."
    ),
    "outro_album": (
        "Intencional: a capa de OUTRO lançamento nunca é usada (podem ser artes "
        "diferentes). Só mude se aceitar usar a capa desse outro álbum."
    ),
    "versao": (
        "Intencional: ao vivo/remix/acústica são outra gravação. Se a Apple escreve "
        "a versão de outro jeito, é falha de normalização."
    ),
    "titulo": (
        "Compare os dois títulos. Se forem a mesma música escrita diferente, é "
        "falha de normalização em utils.py (extrair_titulo_comparavel)."
    ),
    "filtro": (
        "Veja o motivo e as formas normalizadas acima. Título igual com score baixo "
        "= BUG de normalização; 'tipo incompatível' = regra single x álbum."
    ),
    "rede": "Rode de novo (ou use --pausa 6). Nada foi concluído sobre a música.",
}


# --------------------------------------------------------------------------
# Análise (pura: recebe o trace do get_apple_hq_cover, não usa rede)
# --------------------------------------------------------------------------
def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def limpar_tag(texto) -> str:
    """Tira da tag o que o PROJETO acrescenta e o Qobuz não manda (flag de explícito)."""
    return (str(texto or "")).replace("\U0001f174", "").strip()


def linhas_da_apple(trace) -> list:
    """Todos os resultados que a Apple devolveu (sem repetir), vistos no trace."""
    vistos, linhas = set(), []
    for entrada in trace or []:
        if entrada.get("type") != "request":
            continue
        for r in entrada.get("results", []):
            chave = (r.get("artista"), r.get("album"), r.get("faixa"))
            if chave in vistos:
                continue
            vistos.add(chave)
            linhas.append(r)
    return linhas


def _rotulo(r: dict) -> str:
    # Mesmo formato do rótulo que get_apple_hq_cover grava nos vereditos.
    return (
        f"artist={str(r.get('artista') or '')!r}, "
        f"album={str(r.get('album') or '')!r}, "
        f"track={str(r.get('faixa') or '')!r}"
    )


def _motivos_de_rejeicao(trace, linha: dict) -> list:
    rotulo = _rotulo(linha)
    vistos, motivos = set(), []
    for e in trace or []:
        if e.get("type") == "reject" and e.get("label") == rotulo:
            if e["reason"] not in vistos:
                vistos.add(e["reason"])
                motivos.append(e["reason"])
    return motivos


def _medir(linha: dict, artista: str, album: str, faixa: str) -> dict:
    """Notas de UMA linha da Apple contra o que o Qobuz pediu."""
    art = (
        score_artista(artista, str(linha.get("artista") or ""))
        if (extrair_essencia(artista))
        else 1.0
    )
    q_alb, a_alb = (
        extrair_essencia(album),
        extrair_essencia(str(linha.get("album") or "")),
    )
    alb = _ratio(q_alb, a_alb) if q_alb else 1.0
    a_faixa = str(linha.get("faixa") or "")
    solto = (
        _ratio(extrair_essencia(faixa), extrair_essencia(a_faixa)) if a_faixa else 0.0
    )
    nq, na = extrair_titulo_comparavel(faixa), extrair_titulo_comparavel(a_faixa)
    estrito = _ratio(nq, na) if a_faixa else 0.0
    return {
        "linha": linha,
        "art": art,
        "alb": alb,
        "solto": solto,
        "estrito": estrito,
        "norm": (nq, na),
        "mesma_versao": marcadores_versao(faixa) | marcadores_versao(album)
        == marcadores_versao(a_faixa)
        | marcadores_versao(str(linha.get("album") or "")),
        "art_ok": art >= LIM_ARTISTA,
        "alb_ok": alb >= LIM_ALBUM,
    }


def _txt(linha: dict) -> str:
    return (
        f"{linha.get('artista') or '?'!r} / {linha.get('album') or '?'!r} / "
        f"{linha.get('faixa') or '(álbum)'!r}"
    )


def analisar(trace, url, motivo, artista, album, faixa, tipo=None) -> dict:
    """Veredito + checklist a partir do que a Apple devolveu e do que foi decidido.

    tipo: 'single' | 'full' | None (como o `tags` passa para a busca).
    """
    linhas = linhas_da_apple(trace)
    musicas = [r for r in linhas if r.get("faixa")]
    notas = [_medir(r, artista, album, faixa) for r in linhas]
    notas_musica = [n for n in notas if n["linha"].get("faixa")]

    # ---- checklist (o que a Apple TEM, independente do veredito) ----
    melhor_art = max(notas, key=lambda n: n["art"], default=None)
    do_artista = [n for n in notas if n["art_ok"]]
    melhor_alb = max(do_artista, key=lambda n: n["alb"], default=None)
    parecidas = [n for n in notas_musica if n["art_ok"] and n["solto"] >= LIM_TITULO]
    melhor_mus = max(
        (n for n in notas_musica if n["art_ok"]), key=lambda n: n["solto"], default=None
    )
    no_album = [n for n in parecidas if n["alb_ok"]]
    na_versao = [n for n in no_album if n["mesma_versao"]]
    no_tipo = [
        n
        for n in na_versao
        if tipo not in ("single", "full")
        or apple_release_kind(str(n["linha"].get("album") or "")) == tipo
    ]

    check = [
        (
            bool(melhor_art and melhor_art["art_ok"]),
            "Artista",
            f"{melhor_art['linha'].get('artista')!r}"
            if melhor_art and melhor_art["art_ok"]
            else "nenhum resultado com artista parecido"
            + (
                f" (mais perto: {melhor_art['linha'].get('artista')!r} {melhor_art['art']:.2f})"
                if melhor_art
                else ""
            ),
        ),
        (
            bool(melhor_alb and melhor_alb["alb_ok"]),
            "Álbum",
            f"{melhor_alb['linha'].get('album')!r}"
            if melhor_alb and melhor_alb["alb_ok"]
            else "nenhum álbum parecido com o do artista"
            + (
                f" (mais perto: {melhor_alb['linha'].get('album')!r} {melhor_alb['alb']:.2f})"
                if melhor_alb
                else ""
            ),
        ),
        (
            bool(parecidas),
            "Música",
            f"{parecidas[0]['linha'].get('faixa')!r}"
            if parecidas
            else "nenhuma faixa do artista com esse título"
            + (
                f" (mais perto: {melhor_mus['linha'].get('faixa')!r} {melhor_mus['solto']:.2f})"
                if melhor_mus
                else ""
            ),
        ),
        (
            (bool(na_versao) if no_album else None),
            "Versão",
            "mesma versão"
            if na_versao
            else (
                "só em outra versão"
                if no_album
                else "(só vale se a música estiver no álbum certo)"
            ),
        ),
        (
            (bool(no_tipo) if na_versao else None),
            "Tipo",
            "single x álbum compatível"
            if no_tipo
            else (
                "tipo diferente do Qobuz"
                if na_versao
                else "(só vale se a versão bater)"
            ),
        ),
    ]

    base = {
        "checklist": check,
        "n_resultados": len(linhas),
        "n_musicas": len(musicas),
        "norm": None,
        "motivos": [],
    }

    # ---- veredito ----
    if url:
        tem_aceito = any(e.get("type") == "accept" for e in trace or [])
        via = "busca por texto" if tem_aceito else "identificador exato (UPC/ISRC)"
        cand = next(
            (e["label"] for e in reversed(trace or []) if e.get("type") == "accept"), ""
        )
        return {
            **base,
            "veredito": "troca",
            "por_que": [f"Aceita por {via}." + (f" Candidato: {cand}" if cand else "")],
        }

    erros_de_rede = "erros de rede" in (motivo or "") or "sem resposta" in (
        motivo or ""
    )
    if not linhas:
        return {
            **base,
            "veredito": "rede" if erros_de_rede else "nao_tem",
            "por_que": [
                "A Apple não devolveu NENHUM resultado "
                + (
                    "(houve erro de rede/limite)."
                    if erros_de_rede
                    else "para as consultas feitas."
                )
            ],
        }

    if parecidas:
        melhor = max(
            parecidas,
            key=lambda n: (
                n["alb_ok"],
                n["mesma_versao"],
                n["estrito"] >= LIM_TITULO,
                n["solto"],
            ),
        )
        l = melhor["linha"]
        if not melhor["alb_ok"]:
            albuns = sorted({str(n["linha"].get("album")) for n in parecidas})
            por_que = [
                f"A Apple tem {l.get('faixa')!r} de {l.get('artista')!r}, mas em outro álbum.",
                f"Qobuz: álbum {album!r}.  Apple: {', '.join(repr(a) for a in albuns[:4])}"
                + (" ..." if len(albuns) > 4 else ""),
            ]
            if not melhor["mesma_versao"]:
                por_que.append("(e a versão também difere: ao vivo/remix etc.)")
            veredito = "outro_album"
        elif not melhor["mesma_versao"]:
            veredito = "versao"
            por_que = [
                f"No álbum certo só há outra versão: {l.get('faixa')!r}.",
                f"Qobuz: {sorted(marcadores_versao(faixa)) or 'estúdio'}  "
                f"Apple: {sorted(marcadores_versao(str(l.get('faixa')))) or 'estúdio'}",
            ]
        else:
            veredito = "filtro"
            motivos = _motivos_de_rejeicao(trace, l)
            por_que = [f"Apple tem: {_txt(l)}"]
            por_que += [f"Recusada: {m}" for m in motivos[:3]] or [
                "Recusada sem motivo registrado: " + (motivo or "?")
            ]
            nq, na = melhor["norm"]
            por_que.append(
                f"norm do título: Qobuz {nq!r} ↔ Apple {na!r}  ({melhor['estrito']:.2f})"
            )
            base["norm"] = (nq, na)
            base["motivos"] = motivos
    else:
        quase = [
            n
            for n in notas_musica
            if n["art_ok"]
            and n["alb_ok"]
            and LIM_TITULO_PARECIDO <= n["solto"] < LIM_TITULO
        ]
        if quase:
            n = max(quase, key=lambda n: n["solto"])
            veredito = "titulo"
            por_que = [
                f"Mesmo artista e álbum, título parecido mas não igual ({n['solto']:.2f}):",
                f"Qobuz {faixa!r} ↔ Apple {n['linha'].get('faixa')!r}",
            ]
        else:
            veredito = "nao_tem"
            parecida = max(
                (n for n in notas_musica),
                key=lambda n: (n["solto"], n["art"]),
                default=None,
            )
            por_que = [
                f"Nenhum dos {len(linhas)} resultados tem o artista {artista!r} "
                f"com o título {faixa!r}."
            ]
            if parecida:
                por_que.append(
                    f"Mais parecido: {_txt(parecida['linha'])}  "
                    f"(artista {parecida['art']:.2f}, título {parecida['solto']:.2f})"
                )

    if erros_de_rede and veredito in ("nao_tem", "titulo"):
        por_que.append(
            "ATENÇÃO: houve erro de rede; a lista da Apple pode estar incompleta."
        )
        veredito = "rede"
    return {**base, "veredito": veredito, "por_que": por_que}


def evidencias(trace, artista, album, faixa, maximo=8) -> list:
    """Os resultados da Apple mais próximos do pedido (para ver o que ela devolveu)."""
    # Só músicas: as linhas de "álbum" não dizem nada sobre a faixa.
    notas = [
        _medir(r, artista, album, faixa)
        for r in linhas_da_apple(trace)
        if r.get("faixa")
    ]
    notas.sort(
        key=lambda n: (
            -(n["art"] + n["solto"] + n["alb"]),
            n["linha"].get("faixa") or "",
        )
    )
    saida = []
    for n in notas[:maximo]:
        saida.append(
            f"{_txt(n['linha'])}   [artista {n['art']:.2f} | álbum {n['alb']:.2f} | título {n['solto']:.2f}]"
        )
    return saida


# --------------------------------------------------------------------------
# Rede: o MESMO get_apple_hq_cover, com pausa entre consultas
# --------------------------------------------------------------------------
class ClienteComPausa:
    """Embrulha o cliente HTTP: espera entre consultas à Apple (limite ~20/min),
    tenta de novo em 429 e guarda as respostas entre faixas (sem repetir rede).
    O cache INTERNO do get_apple_hq_cover continua por faixa (senão o trace
    perderia as consultas repetidas)."""

    def __init__(self, real, intervalo=3.5, aviso=None):
        self._real, self._intervalo, self._aviso = real, intervalo, aviso
        self._ultima = 0.0
        self._respostas = {}

    async def get(self, url, **kw):
        params = kw.get("params")
        chave = (url, tuple(sorted(params.items())) if params else None)
        if chave in self._respostas:
            return self._respostas[chave]
        resposta = None
        for tentativa in range(3):
            espera = self._intervalo - (time.time() - self._ultima)
            if espera > 0:
                await asyncio.sleep(espera)
            self._ultima = time.time()
            resposta = await self._real.get(url, **kw)
            if resposta.status_code != 429 or tentativa == 2:
                break
            pausa = 30 * (tentativa + 1)
            if self._aviso:
                self._aviso(f"  ... Apple pediu calma (429); aguardando {pausa}s")
            await asyncio.sleep(pausa)
        if resposta is not None and resposta.status_code == 200:
            self._respostas[chave] = resposta
        return resposta

    async def head(self, url, **kw):
        return await self._real.head(url, **kw)

    async def aclose(self):
        await self._real.aclose()


async def diagnosticar(
    sessao,
    artista,
    album,
    faixa,
    upc=None,
    isrc=None,
    tipo=None,
    paises=None,
    lojas_extras=False,
) -> dict:
    sessao._apple_json_cache = {}  # cache interno POR FAIXA (ver ClienteComPausa)
    trace: list[dict[str, Any]] = []
    try:
        url, _fonte, motivo = await get_apple_hq_cover(
            sessao,
            upc=upc,
            isrc=isrc,
            artist=artista,
            album=album,
            track_title=faixa,
            trace=trace,
            release_kind=tipo,
            countries=tuple(paises) if paises else None,
            fallback_countries=lojas_extras,
        )
    except Exception as exc:  # rede, JSON...
        url, motivo = None, f"sem resposta: {type(exc).__name__}: {exc}"
    resultado = analisar(trace, url, motivo, artista, album, faixa, tipo)
    resultado.update(url=url, motivo=motivo, trace=trace)
    return resultado


# --------------------------------------------------------------------------
# Tags do arquivo (o que o Qobuz gravou)
# --------------------------------------------------------------------------
def tipo_do_lancamento(album: str, total):
    """'single'/'full'/None, pela mesma regra do `tags` (<=3 faixas = single)."""
    try:
        total = int(total or 0)
    except (TypeError, ValueError):
        total = 0
    if total <= 0:
        return None
    classe = classify_release_type(title=album, version=None, track_count=total)
    if classe == "single":
        return "single"
    return "full" if classe in ("ep", "album", "live", "compilation") else None


def ler_tags(caminho: str):
    """dict(artista, album, faixa, isrc, upc, total) ou None (sem artista/título)."""
    ext = os.path.splitext(caminho)[1].lower()
    try:
        if ext == ".flac":
            from mutagen.flac import FLAC

            audio = FLAC(caminho)

            def pega(*chaves):
                for c in chaves:
                    valor = audio.get(c)
                    if valor and str(valor[0]).strip():
                        return str(valor[0]).strip()
                return ""

            artista, album, faixa = pega("ARTIST"), pega("ALBUM"), pega("TITLE")
            isrc, upc = pega("ISRC"), pega("BARCODE", "UPC")
            total = pega("TRACKTOTAL", "TOTALTRACKS")
            if not total and "/" in pega("TRACKNUMBER"):
                total = pega("TRACKNUMBER").split("/")[1]
        elif ext == ".mp3":
            from mutagen.id3 import ID3

            t = ID3(caminho)

            def quadro(nome):
                q = t.get(nome)
                return str(q.text[0]).strip() if q is not None and q.text else ""

            artista, album, faixa = quadro("TPE1"), quadro("TALB"), quadro("TIT2")
            isrc = quadro("TSRC")
            upc = ""
            for q in t.getall("TXXX"):
                if q.desc.upper() in ("BARCODE", "UPC") and q.text:
                    upc = str(q.text[0]).strip()
            trck = quadro("TRCK")
            total = trck.split("/")[1] if "/" in trck else ""
        else:
            return None
    except Exception:  # arquivo ilegível/sem tags
        return None
    if not artista or not faixa:
        return None
    return {
        "artista": limpar_tag(artista),
        "album": limpar_tag(album),
        "faixa": limpar_tag(faixa),
        "isrc": isrc,
        "upc": upc,
        "total": total,
    }


def achar_audios(pasta: str) -> list:
    saida = []
    for raiz, _, nomes in os.walk(pasta):
        for nome in sorted(nomes):
            if nome.lower().endswith((".flac", ".mp3")):
                saida.append(os.path.join(raiz, nome))
    return saida


def pasta_padrao():
    from qobuz_dl.doctor import resolve_directory
    from qobuz_dl.retro_tags import resolve_library_dir
    from qobuz_dl.utils import get_config_paths

    config_file = get_config_paths()["config_file"]
    pasta = resolve_directory(config_file) or "Qobuz Downloads"
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    if ios_home and not os.path.isabs(pasta):
        pasta = os.path.join(ios_home, pasta)
    return resolve_library_dir(pasta)


def caminho_relatorio(saida=None) -> str:
    if saida:
        return os.path.abspath(os.path.expanduser(saida))
    pasta = os.getcwd()
    try:
        from qobuz_dl.utils import get_config_paths

        cfg_dir = os.path.dirname(get_config_paths()["config_file"])
        if os.path.isdir(cfg_dir) and os.access(cfg_dir, os.W_OK):
            pasta = cfg_dir
    except Exception:
        pass
    return os.path.join(pasta, f"apple_porque_{time.strftime('%Y%m%d_%H%M%S')}.txt")


# --------------------------------------------------------------------------
# Saída
# --------------------------------------------------------------------------
class Saida:
    """Tudo vai para o relatório (.txt); o terminal recebe o que não é 'detalhe'."""

    def __init__(self, arquivo):
        self.arquivo = arquivo

    def linha(self, texto="", detalhe=False):
        self.arquivo.write(texto + "\n")
        if not detalhe:
            print(texto)


def _quebrar(texto: str, recuo: int, largura: int = 96) -> list:
    """Quebra um texto longo em linhas recuadas (terminal estreito, a-Shell)."""
    espaco = " " * recuo
    return textwrap.wrap(
        texto, width=largura, initial_indent=espaco, subsequent_indent=espaco + "  "
    ) or [espaco]


def blocos(item: dict, diag: dict, ordem: str, tipo) -> list:
    """Linhas (texto, detalhe?) do bloco de UMA faixa."""
    nome, descricao = VEREDITOS[diag["veredito"]]
    kind = {"single": "single", "full": "álbum/EP"}.get(tipo, "tipo ?")
    sai = [
        (f"{ordem} {item['artista']} - {item['faixa']}", False),
        (
            f"    Qobuz : {item['artista']!r} / {item['album']!r} / {item['faixa']!r}  "
            f"({kind}; ISRC {item.get('isrc') or '-'}; UPC {item.get('upc') or '-'})",
            False,
        ),
    ]
    if diag["veredito"] != "troca":
        for ok, rotulo, texto in diag["checklist"]:
            marca = {True: "✔", False: "✘", None: "–"}[ok]
            sai.append((f"    {marca} {rotulo:<8} {texto}", False))
    sai.append((f"    ==> {nome}: {descricao}", False))
    for linha in diag["por_que"]:
        for parte in _quebrar(linha, 8):
            sai.append((parte, False))
    fazer = O_QUE_FAZER[diag["veredito"]].format(
        artista=item["artista"], album=item["album"], faixa=item["faixa"]
    )
    if fazer:
        for parte in _quebrar(f"O que fazer: {fazer}", 8):
            sai.append((parte, False))
    ev = evidencias(diag["trace"], item["artista"], item["album"], item["faixa"], 8)
    if ev and diag["veredito"] != "troca":
        sai.append(
            (
                f"        A Apple devolveu {diag['n_resultados']} resultado(s); os mais próximos:",
                False,
            )
        )
        # 3 na tela; o relatório (.txt) guarda 8.
        sai += [(f"          - {e}", i >= 3) for i, e in enumerate(ev)]
    if diag.get("motivo") and diag["veredito"] != "troca":
        sai.append(
            (f"        Motivo bruto do get_apple_hq_cover: {diag['motivo']}", True)
        )
    sai.append(("", False))
    return sai


def _chave_busca(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto or "").casefold()


async def executar(args) -> int:
    from qobuz_dl.http_download import make_client

    alvo = args.alvo
    itens = []  # (caminho|None, tags)
    if len(alvo) == 3:
        artista, album, faixa = (limpar_tag(x) for x in alvo)
        total = {"single": 1, "album": 8}.get(args.tipo)
        itens.append(
            (
                None,
                {
                    "artista": artista,
                    "album": album,
                    "faixa": faixa,
                    "isrc": args.isrc or "",
                    "upc": args.upc or "",
                    "total": total or "",
                },
            )
        )
        tipo_fixo = {"single": "single", "album": "full"}.get(args.tipo)
    elif len(alvo) <= 1:
        tipo_fixo = None
        if alvo:
            pasta = os.path.expanduser(alvo[0])
        else:
            try:
                pasta = pasta_padrao()
            except Exception as exc:
                print(
                    f"Não consegui descobrir a pasta de downloads ({exc}). Informe a PASTA."
                )
                return 1
            print(f"Usando a pasta de downloads do config.ini: {pasta}")
        if not os.path.isdir(pasta):
            print(f"Pasta não existe: {pasta}")
            return 1
        vistos = set()
        for caminho in achar_audios(pasta):
            tags = ler_tags(caminho)
            if not tags:
                continue
            if args.filtro and _chave_busca(args.filtro) not in _chave_busca(
                f"{os.path.relpath(caminho, pasta)} {tags['artista']} {tags['faixa']}"
            ):
                continue
            chave = (tags["artista"], tags["album"], tags["faixa"])
            if chave in vistos:
                continue
            vistos.add(chave)
            itens.append((os.path.relpath(caminho, pasta), tags))
        if not itens:
            print(
                "Nenhuma faixa com tags encontrada"
                + (" para esse --filtro." if args.filtro else ".")
            )
            return 1
    else:
        print(
            'Use: apple_porque.py  |  apple_porque.py PASTA  |  apple_porque.py "Artista" "Álbum" "Faixa"'
        )
        return 2

    if args.limite:
        itens = itens[: args.limite]
    destino = caminho_relatorio(args.saida)
    paises = [p.strip() for p in args.paises.split(",") if p.strip()]
    contagem: dict[str, int] = {}
    linhas_csv: list[list[str]] = []
    with open(destino, "w", encoding="utf-8") as arquivo:
        saida = Saida(arquivo)
        saida.linha(
            f"{len(itens)} faixa(s). Pausa de {args.pausa}s entre consultas à Apple; "
            "faixas com UPC/ISRC reconhecido terminam rápido."
        )
        saida.linha("")
        cliente = ClienteComPausa(
            make_client(follow_redirects=True),
            args.pausa,
            aviso=lambda m: saida.linha(m),
        )
        try:
            for n, (caminho, tags) in enumerate(itens, 1):
                tipo = (
                    tipo_fixo
                    if len(alvo) == 3
                    else tipo_do_lancamento(tags["album"], tags["total"])
                )
                diag: dict[str, Any]
                try:
                    diag = await diagnosticar(
                        cliente,
                        tags["artista"],
                        tags["album"],
                        tags["faixa"],
                        upc=tags["upc"] or None,
                        isrc=tags["isrc"] or None,
                        tipo=tipo,
                        paises=paises,
                        lojas_extras=args.lojas_extras,
                    )
                except KeyboardInterrupt:
                    raise
                contagem[diag["veredito"]] = contagem.get(diag["veredito"], 0) + 1
                linhas_csv.append(
                    [
                        caminho or "",
                        tags["artista"],
                        tags["album"],
                        tags["faixa"],
                        tags["isrc"],
                        tags["upc"],
                        diag["veredito"],
                        " | ".join(diag["por_que"]),
                    ]
                )
                if args.so_problemas and diag["veredito"] == "troca":
                    saida.linha(
                        f"[{n}/{len(itens)}] TROCARIA  {tags['artista']} - {tags['faixa']}",
                        detalhe=True,
                    )
                    continue
                for texto, detalhe in blocos(tags, diag, f"[{n}/{len(itens)}]", tipo):
                    saida.linha(texto, detalhe=detalhe)
        except KeyboardInterrupt:
            saida.linha("\n(interrompido: o resumo abaixo é parcial)")
        finally:
            await cliente.aclose()

        saida.linha("RESUMO")
        for chave_veredito, (nome, descricao) in VEREDITOS.items():
            if contagem.get(chave_veredito):
                saida.linha(f"  {contagem[chave_veredito]:>4}  {nome:<13} {descricao}")
        saida.linha(
            f"\nRelatório completo (com o 'O que fazer' e os resultados da Apple): {destino}"
        )
        if args.csv and linhas_csv:
            with open(args.csv, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(
                    [
                        "arquivo",
                        "artista",
                        "album",
                        "faixa",
                        "isrc",
                        "upc",
                        "veredito",
                        "por_que",
                    ]
                )
                w.writerows(linhas_csv)
            saida.linha(f"CSV salvo em {args.csv}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Explica por que a capa Apple não seria trocada.",
        epilog="Exemplos:  apple_porque.py   |   apple_porque.py PASTA   |   "
        'apple_porque.py "Artista" "Álbum" "Faixa"',
    )
    ap.add_argument(
        "alvo",
        nargs="*",
        help='(nada) = pasta do config.ini | PASTA | "Artista" "Álbum" "Faixa"',
    )
    ap.add_argument(
        "--filtro", help="só faixas cujo caminho/artista/título contém este texto"
    )
    ap.add_argument(
        "--so-problemas", action="store_true", help="não detalha as que trocariam"
    )
    ap.add_argument("--limite", type=int, default=0, help="máx. de faixas (0 = todas)")
    ap.add_argument(
        "--lojas-extras",
        action="store_true",
        help="se br/us não resolverem, tenta gb, ca, au, de, fr, es, mx, jp",
    )
    ap.add_argument(
        "--paises", default="br,us", help="lojas principais (padrão: br,us)"
    )
    ap.add_argument(
        "--pausa", type=float, default=3.5, help="segundos entre consultas à Apple"
    )
    ap.add_argument("--saida", metavar="ARQ.txt", help="onde gravar o relatório")
    ap.add_argument("--csv", metavar="ARQ.csv", help="também salva uma tabela")
    # só no modo "uma faixa":
    ap.add_argument(
        "--tipo",
        choices=("single", "album"),
        help="(uma faixa) tipo do lançamento no Qobuz",
    )
    ap.add_argument("--isrc", help="(uma faixa) ISRC do Qobuz")
    ap.add_argument("--upc", help="(uma faixa) UPC do Qobuz")
    args = ap.parse_args(argv)
    try:
        return asyncio.run(executar(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
