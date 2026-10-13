"""Compara os TEXTOS do Qobuz com os da Apple/iTunes e mostra onde divergem.

Serve para ver, antes de rodar `tags`, se acentos, espaços, aspas, travessões,
"&" x "and", letras como ø/ł/ß e alfabetos não latinos fazem a capa certa ser
rejeitada (ou a errada ser aceita). Usa as MESMAS funções de normalização e os
MESMOS limites do `get_apple_hq_cover` (utils.py).

Três modos:

  selftest   (offline) tabela de casos típicos Qobuz x Apple, com veredito
      python tools/apple_text_compare.py selftest
      python tools/apple_text_compare.py selftest --so-falhas

  live       (rede) consulta a Apple e compara campo a campo com o que você
             digitou como sendo o texto do Qobuz
      python tools/apple_text_compare.py live "Mø" "No Mythologies to Follow" "Pilgrim"

  scan       (rede) lê as tags de uma pasta já baixada (artista/álbum/título
             vêm do Qobuz) e compara com o melhor resultado da Apple.
             Sem PASTA, usa a pasta de downloads do config.ini (a mesma do `tags`)
      python tools/apple_text_compare.py scan --limite 30
      python tools/apple_text_compare.py scan ~/Music --todos --csv saida.csv

  onde       não sabe onde estão as músicas? mostra a pasta do config.ini e
             procura pastas com .flac/.mp3 no seu HOME
      python tools/apple_text_compare.py onde

Se mudar algum limite em utils.py, atualize THRESH abaixo.
"""

import argparse
import csv
import difflib
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qobuz_dl.utils import (  # noqa: E402
    extrair_essencia,
    extrair_titulo_completo,
    extrair_titulo_comparavel,
    marcadores_versao,
    score_artista,
)

# --------------------------------------------------------------------------
# Saída: relatório completo em .txt; terminal só com o resumo
# --------------------------------------------------------------------------
_TERMINAL = None  # stdout real (o print() comum vai para o arquivo)
_ESPELHAR = False  # True com --terminal: tudo também aparece na tela


def resumo(msg: str = "") -> None:
    """Linha curta que SEMPRE aparece no terminal (sem duplicar se espelhando)."""
    if not _ESPELHAR:
        destino = _TERMINAL or sys.stdout
        destino.write(msg + "\n")
        destino.flush()


def ambos(msg: str = "") -> None:
    """Vai para o relatório (print) e para o terminal."""
    print(msg)
    resumo(msg)


class _Tee:
    def __init__(self, arquivo, terminal, espelhar):
        self.arquivo, self.terminal, self.espelhar = arquivo, terminal, espelhar

    def write(self, texto):
        self.arquivo.write(texto)
        if self.espelhar:
            self.terminal.write(texto)
        return len(texto)

    def flush(self):
        self.arquivo.flush()
        if self.espelhar:
            self.terminal.flush()


def caminho_relatorio(comando: str, saida=None) -> str:
    """--saida, ou a pasta do config.ini (a mesma do qobuz-dl), ou a pasta atual."""
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
    nome = f"apple_text_compare_{comando}_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    return os.path.join(pasta, nome)


# Mesmos valores de get_apple_hq_cover (utils.py).
THRESH = {
    "artist": 0.85,
    "album": 0.80,
    "track": 0.80,
    "full_album": 0.87,
    "full_track": 0.85,
}

UA = {"User-Agent": "Mozilla/5.0 (qobuz-dl-ultra text compare)"}


# --------------------------------------------------------------------------
# Comparação de um par de textos (reproduz as travas da busca textual)
# --------------------------------------------------------------------------
def _ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b).ratio()


def _cp(ch: str) -> str:
    return f"U+{ord(ch):04X}"


def _alfabeto(texto: str) -> str:
    for ch in texto:
        if ch.isalpha():
            try:
                return unicodedata.name(ch).split()[0]
            except ValueError:
                return "?"
    return "?"


def explicar_diferenca(a: str, b: str) -> str:
    """Lista, caractere a caractere, o que muda entre os dois textos brutos."""
    if difflib.SequenceMatcher(None, a, b).ratio() < 0.5 and a and b:
        return f"textos muito diferentes (alfabetos: {_alfabeto(a)} ↔ {_alfabeto(b)})"
    partes = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op == "equal":
            continue
        ta, tb = a[i1:i2], b[j1:j2]
        ca = " ".join(_cp(c) for c in ta) or "∅"
        cb = " ".join(_cp(c) for c in tb) or "∅"
        partes.append(f"{ta!r}({ca}) → {tb!r}({cb})")
    return "; ".join(partes[:4]) + ("; …" if len(partes) > 4 else "")


def caracteres_suspeitos(texto: str) -> list:
    """Espaços especiais, caracteres invisíveis, acentos decompostos (NFD)."""
    achados = []
    for ch in texto:
        cat = unicodedata.category(ch)
        if cat.startswith("Z") and ch != " ":
            achados.append(f"espaço especial {_cp(ch)}")
        elif cat == "Cf":
            achados.append(f"invisível {_cp(ch)}")
    if "  " in texto:
        achados.append("espaço duplo")
    if texto != texto.strip():
        achados.append("espaço nas pontas")
    if texto != unicodedata.normalize("NFC", texto):
        achados.append("acento decomposto (NFD)")
    return achados


def comparar_campo(campo: str, qobuz: str, apple: str) -> dict:
    """campo: artist | album | album_full | track.

    Devolve score, limite, ok, norm (qobuz, apple) e avisos. 'vazio' marca
    normalização que apagou o texto inteiro (alfabeto não latino): nesse caso
    o código atual aprova de graça (score 1.0).
    """
    avisos = []
    if campo == "artist":
        nq, na = extrair_essencia(qobuz), extrair_essencia(apple)
        score = score_artista(qobuz, apple)
        limite = THRESH["artist"]
        if not nq:
            avisos.append("Qobuz normaliza para VAZIO: artista aprovado automaticamente")
    elif campo == "album":
        nq, na = extrair_essencia(qobuz), extrair_essencia(apple)
        score = _ratio(nq, na) if nq else 1.0
        limite = THRESH["album"]
        if not nq:
            avisos.append("Qobuz normaliza para VAZIO: álbum aprovado automaticamente")
    elif campo == "album_full":
        nq, na = extrair_titulo_completo(qobuz), extrair_titulo_completo(apple)
        score = _ratio(nq, na)
        limite = THRESH["full_album"]
    elif campo == "track":
        nq, na = extrair_titulo_comparavel(qobuz), extrair_titulo_comparavel(apple)
        score = _ratio(nq, na)
        limite = THRESH["full_track"]
        mq, ma = marcadores_versao(qobuz), marcadores_versao(apple)
        if mq != ma:
            avisos.append(
                f"versão diferente: Qobuz={sorted(mq) or 'estúdio'} "
                f"Apple={sorted(ma) or 'estúdio'} (REJEITA)"
            )
    else:
        raise ValueError(campo)

    if (not nq) or (not na):
        if nq == na:
            avisos.append("os dois lados normalizam para VAZIO: qualquer texto 'bate'")
        elif not na:
            avisos.append("Apple normaliza para VAZIO (alfabeto não latino)")
    ok = score >= limite and not any("REJEITA" in a for a in avisos)
    for lado, txt in (("Qobuz", qobuz), ("Apple", apple)):
        for s in caracteres_suspeitos(txt):
            avisos.append(f"{lado}: {s}")
    return {
        "campo": campo,
        "score": score,
        "limite": limite,
        "ok": ok,
        "norm": (nq, na),
        "avisos": avisos,
    }


def imprimir_comparacao(
    res: dict, qobuz: str, apple: str, prefixo="", esperado=None
) -> None:
    """esperado: None (sem gabarito), True (deveria aprovar) ou False (deveria rejeitar)."""
    if esperado is None:
        marca = "passa   " if res["ok"] else "reprova "
    elif res["ok"] == esperado:
        marca = "OK      "
    elif esperado:
        marca = "BUG:rejeita o certo "
    else:
        marca = "BUG:aprova o errado "
    sinal = ">=" if res["score"] >= res["limite"] else "<"
    print(
        f"{prefixo}[{marca.strip()}] {res['campo']:<10} {qobuz!r} ↔ {apple!r}  "
        f"{res['score']:.2f} {sinal} {res['limite']:.2f}"
    )
    print(f"{prefixo}         norm: {res['norm'][0]!r} ↔ {res['norm'][1]!r}")
    if qobuz != apple:
        print(f"{prefixo}         dif : {explicar_diferenca(qobuz, apple)}")
    for aviso in res["avisos"]:
        print(f"{prefixo}         ! {aviso}")


# --------------------------------------------------------------------------
# selftest
# --------------------------------------------------------------------------
# (grupo, campo, texto no Qobuz, texto na Apple[, deveria_aprovar])
# O 5o item so' aparece quando o par e' DIFERENTE de verdade e deve ser rejeitado.
CASOS = [
    ("acentos", "artist", "Beyonc\u00e9", "Beyonce"),
    ("acentos", "track", "Anti-Her\u00f3i", "Anti-Heroi"),
    ("acentos", "artist", "J\u00e3o", "Ja\u0303o"),  # Apple devolvendo NFD
    ("acentos", "artist", "Sigur R\u00f3s", "Sigur Ros"),
    ("espa\u00e7os", "artist", "Caf\u00e9 Tacvba", "Caf\u00e9\u00a0Tacvba"),
    ("espa\u00e7os", "track", "Anti Her\u00f3i", "Anti-Her\u00f3i"),
    ("espa\u00e7os", "track", "Foo  Bar", "Foo Bar"),
    ("espa\u00e7os", "track", "Faixa ", "Faixa"),
    ("aspas", "track", "Don\u2019t Stop", "Don't Stop"),
    ("aspas", "track", "I\u2019m", "I'm"),
    ("aspas", "track", "I\u2019m Yours", "I'm Yours"),
    ("aspas", "track", "\u201cAspas\u201d", "\"Aspas\""),
    ("travess\u00f5es", "track", "Anti\u2013Her\u00f3i", "Anti-Her\u00f3i"),
    ("travess\u00f5es", "track", "Faixa \u2014 Remix", "Faixa - Remix"),
    ("& e and", "artist", "Simon & Garfunkel", "Simon and Garfunkel"),
    ("& e and", "track", "Rock & Roll", "Rock and Roll"),
    ("& e and", "artist", "Cardi B", "Cardi B & Bruno Mars"),
    ("abrevia\u00e7\u00f5es", "track", "Pt. 2", "Part 2"),
    ("abrevia\u00e7\u00f5es", "album", "Vol. 1", "Volume 1"),
    ("abrevia\u00e7\u00f5es", "track", "No. 5", "Number 5"),
    ("sem decomposi\u00e7\u00e3o", "artist", "M\u00f8", "M\u00d8"),
    ("sem decomposi\u00e7\u00e3o", "artist", "M\u00f8", "Mo"),
    ("sem decomposi\u00e7\u00e3o", "artist", "\u0141ukasz Gall", "Lukasz Gall"),
    ("sem decomposi\u00e7\u00e3o", "track", "\u00c6on", "Aeon"),
    ("sem decomposi\u00e7\u00e3o", "track", "Stra\u00dfe", "Strasse"),
    ("sem decomposi\u00e7\u00e3o", "track", "\u0152uvre", "Oeuvre"),
    ("sem decomposi\u00e7\u00e3o", "artist", "\u0110or\u0111e", "Dorde"),
    ("s\u00edmbolos", "artist", "P!nk", "Pink"),
    ("s\u00edmbolos", "artist", "Ke$ha", "Kesha"),
    ("s\u00edmbolos", "artist", "AC/DC", "ACDC"),
    ("s\u00edmbolos", "artist", "$uicideboy$", "Suicideboys"),
    ("s\u00edmbolos", "artist", "blink-182", "blink 182"),
    ("s\u00edmbolos", "artist", "Panic! at the Disco", "Panic at the Disco"),
    ("s\u00edmbolos", "track", "\u00bfQu\u00e9 Pasa?", "Que Pasa"),
    ("s\u00edmbolos", "artist", "t.A.T.u.", "tATu"),
    ("n\u00e3o latino", "artist", "\u5b87\u591a\u7530\u30d2\u30ab\u30eb", "Utada Hikaru"),
    ("n\u00e3o latino", "artist", "\u5b87\u591a\u7530\u30d2\u30ab\u30eb", "\u690e\u540d\u6797\u6a8e", False),  # artistas DIFERENTES
    ("n\u00e3o latino", "track", "\u591c\u306b\u99c6\u3051\u308b", "\u7fa4\u9752", False),  # faixas DIFERENTES
    ("n\u00e3o latino", "artist", "\u041a\u0438\u043d\u043e", "Kino"),
    ("n\u00e3o latino", "artist", "\ubc29\ud0c4\uc18c\ub144\ub2e8", "BTS"),
    ("n\u00e3o latino", "artist", "Utada Hikaru", "\u5b87\u591a\u7530\u30d2\u30ab\u30eb"),
    ("n\u00e3o latino", "album", "First Love", "\u521d\u604b"),
    ("vers\u00f5es", "track", "Ressaca (Ao Vivo)", "Ressaca - Ao Vivo"),
    ("vers\u00f5es", "track", "Ressaca (Ao Vivo)", "Ressaca", False),
    ("vers\u00f5es", "track", "Song (Radio Edit)", "Song - Radio Edit"),
    ("vers\u00f5es", "track", "Song (feat. X)", "Song (with X)"),
    ("vers\u00f5es", "track", "Song [Remastered 2011]", "Song - Remastered 2011"),
    ("vers\u00f5es", "track", "Song (Remastered 2011)", "Song (2011 Remaster)"),
    ("vers\u00f5es", "track", "Ressaca", "Ressaca (Bonus Track)"),
    ("vers\u00f5es", "album_full", "After Hours (Explicit)", "After Hours"),
    ("vers\u00f5es", "album_full", "Planet Her (Explicit)", "Planet Her"),
    ("vers\u00f5es", "album_full", "Dawn FM (Explicit)", "Dawn FM"),
    ("vers\u00f5es", "album_full", "Beauty Behind The Madness (Explicit Version)", "Beauty Behind the Madness"),
    ("vers\u00f5es", "album_full", "Lobos", "Lobos - Single"),
    ("vers\u00f5es", "album_full", "Lobos (Deluxe)", "Lobos", False),
]


def cmd_selftest(args) -> int:
    bugs_rejeita = bugs_aprova = vazios = total = 0
    grupo_atual = None
    for caso in CASOS:
        grupo, campo, q, a = caso[:4]
        esperado = caso[4] if len(caso) > 4 else True
        res = comparar_campo(campo, q, a)
        total += 1
        tem_vazio = any("VAZIO" in x for x in res["avisos"])
        vazios += tem_vazio
        bug = res["ok"] != esperado
        if bug and esperado:
            bugs_rejeita += 1
        elif bug:
            bugs_aprova += 1
        if args.so_falhas and not bug and not tem_vazio:
            continue
        if grupo != grupo_atual:
            print(f"\n=== {grupo.upper()} ===")
            grupo_atual = grupo
        imprimir_comparacao(res, q, a, esperado=esperado)
    ambos(f"\nTotal: {total} casos")
    ambos(f"  BUG rejeita o par certo : {bugs_rejeita}  (capa certa deixaria de ser trocada)")
    ambos(f"  BUG aprova o par errado : {bugs_aprova}  (risco de capa errada)")
    ambos(f"  texto virou VAZIO       : {vazios}  (comparação anulada)")
    return 0


# --------------------------------------------------------------------------
# Consulta à Apple (iTunes Search API, pública)
# --------------------------------------------------------------------------
# Intervalo mínimo entre consultas à API pública da Apple (limite ~20/min).
INTERVALO = 3.5
_ultima_consulta = [0.0]


def itunes_search(term: str, entity: str, country: str, limit: int = 15) -> list:
    qs = urllib.parse.urlencode(
        {"term": term, "entity": entity, "country": country, "limit": limit}
    )
    req = urllib.request.Request(f"https://itunes.apple.com/search?{qs}", headers=UA)
    for tentativa in range(3):
        espera = INTERVALO - (time.time() - _ultima_consulta[0])
        if espera > 0:
            time.sleep(espera)
        _ultima_consulta[0] = time.time()
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8")).get("results", [])
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or tentativa == 2:
                raise
            pausa = 30 * (tentativa + 1)
            ambos(f"  ... Apple pediu calma (429); aguardando {pausa}s")
            time.sleep(pausa)
    return []


def limpar_tag(texto: str) -> str:
    """Tira da tag o que o PROJETO acrescenta e o Qobuz não manda (flag de explícito)."""
    return (texto or "").replace("\U0001f174", "").strip()


def candidatos_apple(artista, album, faixa, paises, limite=15) -> list:
    vistos, saida, erros = set(), [], 0
    consultas = []
    if faixa:
        consultas.append((f"{artista} {faixa}".strip(), "song"))
    if album:
        consultas.append((f"{artista} {album}".strip(), "album"))
    for pais in paises:
        for termo, entidade in consultas:
            try:
                resultados = itunes_search(termo, entidade, pais, limite)
            except Exception as exc:  # rede, JSON, 403/429...
                print(f"  ! consulta falhou ({pais}/{entidade}): {exc}")
                erros += 1
                continue
            for r in resultados:
                chave = (r.get("trackId") or r.get("collectionId"), entidade)
                if chave in vistos:
                    continue
                vistos.add(chave)
                r["_pais"] = pais
                saida.append(r)
    return saida, erros


def comparar_candidato(artista, album, faixa, r) -> dict:
    """Aplica as travas de campo a um resultado cru da Apple.

    Resultado de FAIXA (tem trackName): artista + álbum + faixa (busca por faixa).
    Resultado de ÁLBUM (sem trackName): artista + álbum + edição completa do
    álbum, como na busca por álbum do `tags`.
    """
    c_art = str(r.get("artistName") or "")
    c_alb = str(r.get("collectionName") or "")
    c_trk = str(r.get("trackName") or "")
    tipo = "faixa" if c_trk else "álbum"
    campos = [("artist", artista, c_art)]
    if album and c_alb:
        campos.append(("album", album, c_alb))
        if tipo == "álbum":
            campos.append(("album_full", album, c_alb))
    if tipo == "faixa" and faixa:
        campos.append(("track", faixa, c_trk))
    resultados = [(c, q, a, comparar_campo(c, q, a)) for c, q, a in campos]
    proximidade = sum(x[3]["score"] for x in resultados) / len(resultados)
    return {
        "res": resultados,
        "ok": all(x[3]["ok"] for x in resultados),
        "proximidade": proximidade,
        "apple": (c_art, c_alb, c_trk),
        "tipo": tipo,
    }


def chave_ordem(info: dict, faixa: str):
    """Aceitos primeiro; depois o tipo de resultado que corresponde à busca; depois a proximidade."""
    preferido = "faixa" if faixa else "álbum"
    return (not info["ok"], info["tipo"] != preferido, -info["proximidade"])


# --------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------
def cmd_live(args) -> int:
    paises = [p.strip() for p in args.paises.split(",") if p.strip()]
    print(f"Qobuz (digitado): artista={args.artista!r} álbum={args.album!r} faixa={args.faixa!r}")
    cands, erros = candidatos_apple(
        limpar_tag(args.artista), limpar_tag(args.album), limpar_tag(args.faixa),
        paises, args.limite,
    )
    if not cands:
        ambos("Apple não devolveu resultados (ou a rede falhou).")
        return 1
    if erros:
        ambos(f"ATENÇÃO: {erros} consulta(s) falharam; a lista pode estar incompleta.")
    avaliados = [
        (comparar_candidato(args.artista, args.album, args.faixa, r), r) for r in cands
    ]
    avaliados.sort(key=lambda x: chave_ordem(x[0], args.faixa))
    for info, r in avaliados[: args.mostrar]:
        a_art, a_alb, a_trk = info["apple"]
        status = "ACEITARIA" if info["ok"] else "rejeitaria"
        print(
            f"\n--- {status} | {info['tipo']} | {r['_pais']} | média {info['proximidade']:.2f} | "
            f"Apple: {a_art!r} / {a_alb!r} / {a_trk!r}"
        )
        for campo, q, a, res in info["res"]:
            imprimir_comparacao(res, q, a, prefixo="  ")
    resumo(
        f"{len(avaliados)} candidato(s) da Apple; "
        f"{sum(1 for i, _ in avaliados if i['ok'])} passariam nas travas de texto."
    )
    print(
        "\nPara o veredito completo (tipo single/álbum, termos indesejados, UPC/ISRC): "
        "python tools/apple_cover_check.py ARTISTA ÁLBUM FAIXA --all"
    )
    return 0


# --------------------------------------------------------------------------
# Descobrir a pasta das músicas
# --------------------------------------------------------------------------
def pasta_padrao():
    """(pasta, config.ini) como o `tags` resolve quando você não informa DIR."""
    from qobuz_dl.doctor import resolve_directory
    from qobuz_dl.retro_tags import resolve_library_dir
    from qobuz_dl.utils import get_config_paths

    config_file = get_config_paths()["config_file"]
    pasta = resolve_directory(config_file) or "Qobuz Downloads"
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    if ios_home and not os.path.isabs(pasta):
        pasta = os.path.join(ios_home, pasta)
    return resolve_library_dir(pasta), config_file


def _contar_audios(pasta: str, profundidade: int = 5) -> int:
    base = pasta.rstrip(os.sep).count(os.sep)
    total = 0
    for atual, subdirs, nomes in os.walk(pasta):
        if atual.count(os.sep) - base >= profundidade:
            subdirs[:] = []
        subdirs[:] = [d for d in subdirs if not d.startswith(".")]
        total += sum(1 for x in nomes if x.lower().endswith((".flac", ".mp3")))
    return total


def pastas_com_audio(raizes, maximo=8) -> list:
    """Pastas candidatas: cada subpasta direta das raízes (e a própria raiz) com áudio."""
    achadas = {}
    for raiz in raizes:
        raiz = os.path.abspath(os.path.expanduser(raiz))
        if not os.path.isdir(raiz):
            continue
        try:
            filhos = [
                os.path.join(raiz, d)
                for d in sorted(os.listdir(raiz))
                if not d.startswith(".") and os.path.isdir(os.path.join(raiz, d))
            ]
        except OSError:
            continue
        for pasta in [raiz] + filhos:
            if pasta in achadas:
                continue
            n = (
                sum(1 for x in os.listdir(pasta) if x.lower().endswith((".flac", ".mp3")))
                if pasta == raiz
                else _contar_audios(pasta)
            )
            if n:
                achadas[pasta] = n
    # Descarta pastas que apenas contêm outras candidatas (ex.: Documents).
    achadas = {
        c: n
        for c, n in achadas.items()
        if not any(o != c and o.startswith(c + os.sep) for o in achadas)
    }
    return sorted(achadas.items(), key=lambda kv: -kv[1])[:maximo]


def cmd_onde(args) -> int:
    try:
        pasta, cfg = pasta_padrao()
        print(f"config.ini        : {cfg}")
        print(f"pasta de downloads: {pasta}  ({'existe' if os.path.isdir(pasta) else 'NÃO existe'})")
    except Exception as exc:
        print(f"Não consegui ler o config.ini: {exc}")
        pasta = None
    home = os.path.expanduser("~")
    raizes = [home, os.path.join(home, "Documents"), os.getcwd()]
    print("\nPastas com .flac/.mp3 encontradas (HOME, Documents e pasta atual):")
    achadas = pastas_com_audio(raizes)
    if not achadas:
        print("  nenhuma. Confira com `pwd` e `ls`, ou use o caminho que o app mostra ao baixar.")
        return 1
    for caminho, n in achadas:
        print(f"  {n:>5} arquivos  {caminho}")
    print("\nUse: python tools/apple_text_compare.py scan \"CAMINHO\"")
    return 0


# --------------------------------------------------------------------------
# scan
# --------------------------------------------------------------------------
_ESPECIAIS = set("&\u2019\u2018\u201c\u201d\u2013\u2014\u2026/!$@#*:+\u00bf\u00a1\u00b7")


def tem_texto_dificil(*textos) -> bool:
    for t in textos:
        if not t:
            continue
        if any(ord(c) > 127 or c in _ESPECIAIS for c in t):
            return True
        if "  " in t or t != t.strip():
            return True
    return False


def ler_tags(caminho: str):
    """(artista, álbum, título) das tags do arquivo (gravadas a partir do Qobuz)."""
    import mutagen

    try:
        audio = mutagen.File(caminho, easy=True)
        if audio is None or not audio.tags:
            return None
    except Exception:  # arquivo corrompido/ilegível: pula
        return None

    def pega(chave):
        valor = audio.tags.get(chave)
        return str(valor[0]) if valor else ""

    return pega("artist"), pega("album"), pega("title")


def achar_audios(pasta: str) -> list:
    saida = []
    for raiz, _, nomes in os.walk(pasta):
        for nome in sorted(nomes):
            if nome.lower().endswith((".flac", ".mp3")):
                saida.append(os.path.join(raiz, nome))
    return saida


def cmd_scan(args) -> int:
    if args.pasta:
        pasta = os.path.expanduser(args.pasta)
    else:
        try:
            pasta, _ = pasta_padrao()
        except Exception as exc:
            ambos(f"Não consegui descobrir a pasta padrão ({exc}). Rode: ... onde")
            return 1
        ambos(f"Usando a pasta de downloads do config.ini: {pasta}")
    arquivos = achar_audios(pasta)
    if not arquivos:
        ambos(f"Nenhum .flac/.mp3 em {pasta}")
        ambos("Rode `python tools/apple_text_compare.py onde` para procurar a pasta certa.")
        return 1
    paises = [p.strip() for p in args.paises.split(",") if p.strip()]
    linhas_csv, analisados, rejeitados, inconclusivos = [], 0, 0, 0
    vistos = set()
    for caminho in arquivos:
        tags = ler_tags(caminho)
        if not tags or not tags[0] or not tags[2]:
            continue
        # O projeto acrescenta " 🅴" ao TITLE de faixas explícitas; o Qobuz não
        # manda isso, então tiramos antes de filtrar e de consultar a Apple.
        artista, album, titulo = (limpar_tag(x) for x in tags)
        if not args.todos and not tem_texto_dificil(artista, album, titulo):
            continue
        if (artista, album, titulo) in vistos:
            continue
        vistos.add((artista, album, titulo))
        if analisados >= args.limite:
            ambos(f"\n(limite de {args.limite} faixas atingido; use --limite para mais)")
            break
        analisados += 1
        print(f"\n### {os.path.relpath(caminho, pasta)}")
        print(f"  Qobuz: {artista!r} / {album!r} / {titulo!r}")

        # Um país por vez; para no primeiro que tiver candidato aceito (menos consultas).
        todos_cands, erros, melhor = [], 0, None
        for pais in paises:
            cands, e = candidatos_apple(artista, album, titulo, [pais], args.consulta)
            erros += e
            todos_cands += cands
            if cands:
                atual = min(
                    (comparar_candidato(artista, album, titulo, r) for r in todos_cands),
                    key=lambda i: chave_ordem(i, titulo),
                )
                melhor = atual
                if atual["ok"]:
                    break

        if melhor is None:
            if erros:
                inconclusivos += 1
                print(f"  => INCONCLUSIVO: {erros} consulta(s) falharam (rede/limite da Apple)")
                veredito = "inconclusivo"
            else:
                print("  Apple: sem resultados")
                veredito = "sem resultados"
            linhas_csv.append([caminho, artista, album, titulo, "", "", "", veredito, ""])
            resumo(f"[{analisados}] {veredito.upper():<14} {artista} - {titulo}")
            continue
        a_art, a_alb, a_trk = melhor["apple"]
        print(f"  Apple ({melhor['tipo']}): {a_art!r} / {a_alb!r} / {a_trk!r}")
        problemas = []
        for campo, q, a_txt, res in melhor["res"]:
            if not res["ok"] or res["avisos"]:
                imprimir_comparacao(res, q, a_txt, prefixo="  ")
            if not res["ok"]:
                problemas.append(f"{campo} {res['score']:.2f}<{res['limite']:.2f}")
        if melhor["ok"]:
            veredito = "aceitaria"
            print("  => ACEITARIA")
        elif erros:
            inconclusivos += 1
            veredito = "inconclusivo"
            print(
                f"  => INCONCLUSIVO ({erros} consulta(s) falharam): "
                + ", ".join(problemas)
            )
        else:
            rejeitados += 1
            veredito = "rejeitaria"
            print("  => REJEITARIA: " + ", ".join(problemas))
        linhas_csv.append(
            [caminho, artista, album, titulo, a_art, a_alb, a_trk, veredito, "; ".join(problemas)]
        )
        resumo(
            f"[{analisados}] {veredito.upper():<14} {artista} - {titulo}"
            + (f"  ({', '.join(problemas)})" if problemas else "")
        )
    ambos(
        f"\nAnalisadas: {analisados} | rejeitariam pela busca textual: {rejeitados} "
        f"| inconclusivas (consulta falhou): {inconclusivos}"
    )
    if args.csv and linhas_csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["arquivo", "q_artista", "q_album", "q_titulo",
                        "a_artista", "a_album", "a_faixa", "veredito", "problemas"])
            w.writerows(linhas_csv)
        ambos(f"CSV salvo em {args.csv}")
    return 0


# --------------------------------------------------------------------------
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument(
        "--saida",
        metavar="ARQ.txt",
        help="onde gravar o relatório completo (padrão: pasta do config.ini)",
    )
    comum.add_argument(
        "--terminal",
        action="store_true",
        help="mostra o relatório completo também na tela (padrão: só o resumo)",
    )

    st = sub.add_parser("selftest", help="casos típicos, offline", parents=[comum])
    st.add_argument("--so-falhas", action="store_true",
                    help="mostra só falhas e avisos de texto vazio")
    st.set_defaults(fn=cmd_selftest)

    lv = sub.add_parser("live", help="compara com a Apple de verdade", parents=[comum])
    lv.add_argument("artista")
    lv.add_argument("album")
    lv.add_argument("faixa")
    lv.add_argument("--paises", default="br,us")
    lv.add_argument("--limite", type=int, default=15, help="resultados por consulta")
    lv.add_argument("--mostrar", type=int, default=6, help="candidatos exibidos")
    lv.set_defaults(fn=cmd_live)

    sc = sub.add_parser("scan", help="varre uma pasta já baixada", parents=[comum])
    sc.add_argument("pasta", nargs="?", help="padrão: pasta de downloads do config.ini")
    sc.add_argument("--todos", action="store_true",
                    help="inclui faixas sem acento/símbolo (padrão: só as 'difíceis')")
    sc.add_argument("--limite", type=int, default=20, help="máx. de faixas analisadas")
    sc.add_argument("--consulta", type=int, default=10, help="resultados por consulta")
    sc.add_argument("--paises", default="br,us")
    sc.add_argument("--pausa", type=float, default=3.5, help="segundos entre CONSULTAS à Apple")
    sc.add_argument("--csv", help="salva o resultado em CSV")
    sc.set_defaults(fn=cmd_scan)

    od = sub.add_parser("onde", help="descobre a pasta das músicas")
    od.set_defaults(fn=cmd_onde)

    args = ap.parse_args(argv)
    global INTERVALO, _TERMINAL, _ESPELHAR
    INTERVALO = getattr(args, "pausa", INTERVALO)
    if args.cmd == "onde":  # saída curta: direto na tela
        return args.fn(args)

    destino = caminho_relatorio(args.cmd, args.saida)
    try:
        arquivo = open(destino, "w", encoding="utf-8")
    except OSError as exc:
        print(f"Não consegui criar {destino}: {exc}")
        return 1
    _TERMINAL, _ESPELHAR = sys.stdout, bool(args.terminal)
    original = sys.stdout
    sys.stdout = _Tee(arquivo, original, _ESPELHAR)
    try:
        codigo = args.fn(args)
    finally:
        sys.stdout = original
        arquivo.close()
    print(f"\nRelatório completo: {destino}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
