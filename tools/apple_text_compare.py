"""Compara os TEXTOS do Qobuz com os da Apple/iTunes e mostra onde divergem.

Serve para ver, antes de rodar `tags`, se acentos, espaços, aspas, travessões,
"&" x "and", letras como ø/ł/ß e alfabetos não latinos fazem a capa certa ser
rejeitada (ou a errada ser aceita). Usa as MESMAS funções de normalização e os
MESMOS limites do `get_apple_hq_cover` (apple_cover.py).
"""

from __future__ import annotations

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
from typing import Any, Literal, TextIO, TypedDict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qobuz_dl.apple_cover import titulo_album_comparavel  # noqa: E402
from qobuz_dl.utils import (  # noqa: E402
    extrair_essencia,
    extrair_titulo_comparavel,
    extrair_titulo_completo,
    marcadores_versao,
    score_artista,
)

Campo = Literal["artist", "album", "album_full", "track"]
ResultadoApple = dict[str, Any]
CasoComparacao = tuple[str, str, str, str] | tuple[str, str, str, str, bool]


class ComparacaoCampo(TypedDict):
    campo: str
    score: float
    limite: float
    ok: bool
    norm: tuple[str, str]
    avisos: list[str]


class ComparacaoCandidato(TypedDict):
    res: list[tuple[Campo, str, str, ComparacaoCampo]]
    ok: bool
    proximidade: float
    apple: tuple[str, str, str]
    tipo: str


_TERMINAL: TextIO | None = None
_ESPELHAR = False


def resumo(msg: str = "") -> None:
    """Linha curta que SEMPRE aparece no terminal."""
    if not _ESPELHAR:
        destino = _TERMINAL or sys.stdout
        destino.write(msg + "\n")
        destino.flush()


def ambos(msg: str = "") -> None:
    """Vai para o relatório (print) e para o terminal."""
    print(msg)
    resumo(msg)


class _Tee:
    def __init__(
        self,
        arquivo: TextIO,
        terminal: TextIO,
        espelhar: bool,
    ) -> None:
        self.arquivo = arquivo
        self.terminal = terminal
        self.espelhar = espelhar

    def write(self, texto: str) -> int:
        self.arquivo.write(texto)
        if self.espelhar:
            self.terminal.write(texto)
        return len(texto)

    def flush(self) -> None:
        self.arquivo.flush()
        if self.espelhar:
            self.terminal.flush()


def caminho_relatorio(comando: str, saida: str | None = None) -> str:
    """--saida, ou a pasta do config.ini, ou a pasta atual."""
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


THRESH: dict[str, float] = {
    "artist": 0.85,
    "album": 0.80,
    "track": 0.80,
    "full_album": 0.87,
    "full_track": 0.85,
}

UA = {"User-Agent": "Mozilla/5.0 (qobuz-dl-ultra text compare)"}


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

    partes: list[str] = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if op == "equal":
            continue

        ta, tb = a[i1:i2], b[j1:j2]
        ca = " ".join(_cp(c) for c in ta) or "∅"
        cb = " ".join(_cp(c) for c in tb) or "∅"
        partes.append(f"{ta!r}({ca}) → {tb!r}({cb})")

    return "; ".join(partes[:4]) + ("; …" if len(partes) > 4 else "")


def caracteres_suspeitos(texto: str) -> list[str]:
    """Espaços especiais, caracteres invisíveis e acentos decompostos."""
    achados: list[str] = []

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


def comparar_campo(
    campo: Campo,
    qobuz: str,
    apple: str,
) -> ComparacaoCampo:
    """Compara um campo entre Qobuz e Apple, aplicando as travas do projeto."""
    avisos: list[str] = []

    if campo == "artist":
        nq, na = extrair_essencia(qobuz), extrair_essencia(apple)
        score = score_artista(qobuz, apple)
        limite = THRESH["artist"]

        if not nq:
            avisos.append(
                "Qobuz normaliza para VAZIO: artista aprovado automaticamente"
            )

    elif campo == "album":
        nq, na = extrair_essencia(qobuz), extrair_essencia(apple)
        score = _ratio(nq, na) if nq else 1.0
        limite = THRESH["album"]

        if not nq:
            avisos.append("Qobuz normaliza para VAZIO: álbum aprovado automaticamente")

    elif campo == "album_full":
        nq = titulo_album_comparavel(qobuz)
        na = titulo_album_comparavel(apple)
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
            avisos.append(
                "os dois lados normalizam para VAZIO: "
                "comparação textual inconclusiva (REJEITA)"
            )
        else:
            avisos.append(
                "um lado normaliza para VAZIO "
                "(alfabeto não latino): comparação textual inconclusiva (REJEITA)"
            )

    if not nq or not na:
        score = 0.0

    ok = score >= limite and not any("REJEITA" in aviso for aviso in avisos)

    for lado, texto in (("Qobuz", qobuz), ("Apple", apple)):
        for suspeito in caracteres_suspeitos(texto):
            avisos.append(f"{lado}: {suspeito}")

    return {
        "campo": campo,
        "score": score,
        "limite": limite,
        "ok": ok,
        "norm": (nq, na),
        "avisos": avisos,
    }


def imprimir_comparacao(
    res: ComparacaoCampo,
    qobuz: str,
    apple: str,
    prefixo: str = "",
    esperado: bool | None = None,
) -> None:
    """Imprime uma comparação campo a campo."""
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
        f"{prefixo}[{marca.strip()}] {res['campo']:<10} "
        f"{qobuz!r} ↔ {apple!r}  "
        f"{res['score']:.2f} {sinal} {res['limite']:.2f}"
    )
    print(f"{prefixo}         norm: {res['norm'][0]!r} ↔ {res['norm'][1]!r}")

    if qobuz != apple:
        print(f"{prefixo}         dif : {explicar_diferenca(qobuz, apple)}")

    for aviso in res["avisos"]:
        print(f"{prefixo}         ! {aviso}")


CASOS: list[CasoComparacao] = [
    ("acentos", "artist", "Beyoncé", "Beyonce"),
    ("acentos", "track", "Anti-Herói", "Anti-Heroi"),
    ("acentos", "artist", "Jão", "Ja\u0303o"),
    ("acentos", "artist", "Sigur Rós", "Sigur Ros"),
    ("espaços", "artist", "Café Tacvba", "Café Tacvba"),
    ("espaços", "track", "Anti Herói", "Anti-Herói"),
    ("espaços", "track", "Foo  Bar", "Foo Bar"),
    ("espaços", "track", "Faixa ", "Faixa"),
    ("aspas", "track", "Don’t Stop", "Don't Stop"),
    ("aspas", "track", "I’m", "I'm"),
    ("aspas", "track", "I’m Yours", "I'm Yours"),
    ("aspas", "track", "\u201cAspas\u201d", '"Aspas"'),
    ("travessões", "track", "Anti–Herói", "Anti-Herói"),
    ("travessões", "track", "Faixa -- Remix", "Faixa - Remix"),
    ("& e and", "artist", "Simon & Garfunkel", "Simon and Garfunkel"),
    ("& e and", "track", "Rock & Roll", "Rock and Roll"),
    ("& e and", "artist", "Cardi B", "Cardi B & Bruno Mars"),
    ("abreviações", "track", "Pt. 2", "Part 2"),
    ("abreviações", "album", "Vol. 1", "Volume 1", False),
    ("abreviações", "track", "No. 5", "Number 5"),
    ("sem decomposição", "artist", "Mø", "Mø"),
    ("sem decomposição", "artist", "Mø", "Mo"),
    ("sem decomposição", "artist", "Łukasz Gall", "Lukasz Gall"),
    ("sem decomposição", "track", "Æon", "Aeon"),
    ("sem decomposição", "track", "Straße", "Strasse"),
    ("sem decomposição", "track", "Œuvre", "Oeuvre"),
    ("sem decomposição", "artist", "Đorđe", "Dorde"),
    ("símbolos", "artist", "P!nk", "Pink"),
    ("símbolos", "artist", "Ke$ha", "Kesha"),
    ("símbolos", "artist", "AC/DC", "ACDC"),
    ("símbolos", "artist", "$uicideboy$", "Suicideboys"),
    ("símbolos", "artist", "blink-182", "blink 182"),
    ("símbolos", "artist", "Panic! at the Disco", "Panic at the Disco"),
    ("símbolos", "track", "¿Qué Pasa?", "Que Pasa"),
    ("símbolos", "artist", "t.A.T.u.", "tATu"),
    # CJK x latino: a normalização textual não pode confirmar a equivalência.
    # Um lado vira VAZIO; a decisão segura fica para UPC/ISRC.
    ("não latino", "artist", "宇多田ヒカル", "Utada Hikaru", False),
    ("não latino", "artist", "宇多田ヒカル", "椎名林檎", False),
    ("não latino", "track", "夜に駆ける", "群青", False),
    ("não latino", "artist", "Кино", "Kino", False),
    ("não latino", "artist", "방탄소년단", "BTS", False),
    ("não latino", "artist", "Utada Hikaru", "宇多田ヒカル", False),
    ("não latino", "album", "First Love", "初恋", False),
    ("versões", "track", "Ressaca (Ao Vivo)", "Ressaca - Ao Vivo"),
    ("versões", "track", "Ressaca (Ao Vivo)", "Ressaca", False),
    ("versões", "track", "Song (Radio Edit)", "Song - Radio Edit"),
    ("versões", "track", "Song (feat. X)", "Song (with X)"),
    ("versões", "track", "Song [Remastered 2011]", "Song - Remastered 2011"),
    ("versões", "track", "Song (Remastered 2011)", "Song (2011 Remaster)"),
    ("versões", "track", "Ressaca", "Ressaca (Bonus Track)"),
    ("versões", "album_full", "After Hours (Explicit)", "After Hours"),
    ("versões", "album_full", "Planet Her (Explicit)", "Planet Her"),
    ("versões", "album_full", "Dawn FM (Explicit)", "Dawn FM"),
    (
        "versões",
        "album_full",
        "Beauty Behind The Madness (Explicit Version)",
        "Beauty Behind the Madness",
    ),
    ("versões", "album_full", "Lobos", "Lobos - Single"),
    ("versões", "album_full", "Lobos (Deluxe)", "Lobos", False),
]


def cmd_selftest(args: argparse.Namespace) -> int:
    """Executa os casos offline e mostra eventuais divergências."""
    bugs_rejeita = bugs_aprova = vazios = total = 0
    grupo_atual: str | None = None

    for caso in CASOS:
        grupo, campo, qobuz, apple = caso[:4]
        esperado = caso[4] if len(caso) > 4 else True

        resultado = comparar_campo(
            campo,  # type: ignore[arg-type]
            qobuz,
            apple,
        )

        total += 1
        tem_vazio = any("VAZIO" in aviso for aviso in resultado["avisos"])
        vazios += int(tem_vazio)

        bug = resultado["ok"] != esperado
        if bug and esperado:
            bugs_rejeita += 1
        elif bug:
            bugs_aprova += 1

        if args.so_falhas and not bug and not tem_vazio:
            continue

        if grupo != grupo_atual:
            print(f"\n=== {grupo.upper()} ===")
            grupo_atual = grupo

        imprimir_comparacao(
            resultado,
            qobuz,
            apple,
            esperado=esperado,
        )

    ambos(f"\nTotal: {total} casos")
    ambos(
        f"  BUG rejeita o par certo : {bugs_rejeita}  "
        "(capa certa deixaria de ser trocada)"
    )
    ambos(f"  BUG aprova o par errado : {bugs_aprova}  (risco de capa errada)")
    ambos(f"  texto virou VAZIO       : {vazios}  (comparação anulada)")
    return 0


INTERVALO = 3.5
_ultima_consulta: list[float] = [0.0]


def itunes_search(
    term: str,
    entity: str,
    country: str,
    limit: int = 15,
) -> list[ResultadoApple]:
    """Consulta a iTunes Search API com pausa e retry para HTTP 429."""
    qs = urllib.parse.urlencode(
        {
            "term": term,
            "entity": entity,
            "country": country,
            "limit": limit,
        }
    )
    req = urllib.request.Request(
        f"https://itunes.apple.com/search?{qs}",
        headers=UA,
    )

    for tentativa in range(3):
        espera = INTERVALO - (time.time() - _ultima_consulta[0])
        if espera > 0:
            time.sleep(espera)

        _ultima_consulta[0] = time.time()

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                dados = json.loads(resp.read().decode("utf-8"))
                return dados.get("results", [])
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or tentativa == 2:
                raise

            pausa = 30 * (tentativa + 1)
            ambos(f"  ... Apple pediu calma (429); aguardando {pausa}s")
            time.sleep(pausa)

    return []


def limpar_tag(texto: str) -> str:
    """Remove a marca de explícito acrescentada pelo projeto."""
    return (texto or "").replace("\U0001f174", "").strip()


def candidatos_apple(
    artista: str,
    album: str,
    faixa: str,
    paises: list[str],
    limite: int = 15,
) -> tuple[list[ResultadoApple], int]:
    """Busca candidatos na Apple por faixa e álbum."""
    vistos: set[tuple[int | str | None, str]] = set()
    saida: list[ResultadoApple] = []
    erros = 0
    consultas: list[tuple[str, str]] = []

    if faixa:
        consultas.append((f"{artista} {faixa}".strip(), "song"))
    if album:
        consultas.append((f"{artista} {album}".strip(), "album"))

    for pais in paises:
        for termo, entidade in consultas:
            try:
                resultados = itunes_search(termo, entidade, pais, limite)
            except Exception as exc:
                print(f"  ! consulta falhou ({pais}/{entidade}): {exc}")
                erros += 1
                continue

            for resultado in resultados:
                chave = (
                    resultado.get("trackId") or resultado.get("collectionId"),
                    entidade,
                )

                if chave in vistos:
                    continue

                vistos.add(chave)
                resultado["_pais"] = pais
                saida.append(resultado)

    return saida, erros


def comparar_candidato(
    artista: str,
    album: str,
    faixa: str,
    resultado: ResultadoApple,
) -> ComparacaoCandidato:
    """Aplica as travas de campo a um resultado da Apple."""
    c_art = str(resultado.get("artistName") or "")
    c_alb = str(resultado.get("collectionName") or "")
    c_trk = str(resultado.get("trackName") or "")
    tipo = "faixa" if c_trk else "álbum"

    campos: list[tuple[Campo, str, str]] = [("artist", artista, c_art)]

    if album and c_alb:
        campos.append(("album", album, c_alb))

        if tipo == "álbum":
            campos.append(("album_full", album, c_alb))

    if tipo == "faixa" and faixa:
        campos.append(("track", faixa, c_trk))

    comparacoes = [
        (campo, qobuz, apple, comparar_campo(campo, qobuz, apple))
        for campo, qobuz, apple in campos
    ]

    proximidade = sum(item[3]["score"] for item in comparacoes) / len(comparacoes)

    return {
        "res": comparacoes,
        "ok": all(item[3]["ok"] for item in comparacoes),
        "proximidade": proximidade,
        "apple": (c_art, c_alb, c_trk),
        "tipo": tipo,
    }


def chave_ordem(
    info: ComparacaoCandidato,
    faixa: str,
) -> tuple[bool, bool, float]:
    """Ordena candidatos aceitos, do tipo preferido e mais próximos primeiro."""
    preferido = "faixa" if faixa else "álbum"
    return (
        not info["ok"],
        info["tipo"] != preferido,
        -info["proximidade"],
    )


def cmd_live(args: argparse.Namespace) -> int:
    """Consulta a Apple e compara os textos informados."""
    paises = [pais.strip() for pais in args.paises.split(",") if pais.strip()]

    print(
        f"Qobuz (digitado): artista={args.artista!r} "
        f"álbum={args.album!r} faixa={args.faixa!r}"
    )

    cands, erros = candidatos_apple(
        limpar_tag(args.artista),
        limpar_tag(args.album),
        limpar_tag(args.faixa),
        paises,
        args.limite,
    )

    if not cands:
        ambos("Apple não devolveu resultados (ou a rede falhou).")
        return 1

    if erros:
        ambos(f"ATENÇÃO: {erros} consulta(s) falharam; a lista pode estar incompleta.")

    avaliados: list[tuple[ComparacaoCandidato, ResultadoApple]] = [
        (
            comparar_candidato(args.artista, args.album, args.faixa, resultado),
            resultado,
        )
        for resultado in cands
    ]

    avaliados.sort(key=lambda item: chave_ordem(item[0], args.faixa))

    for info, resultado in avaliados[: args.mostrar]:
        a_art, a_alb, a_trk = info["apple"]
        status = "ACEITARIA" if info["ok"] else "rejeitaria"

        print(
            f"\n--- {status} | {info['tipo']} | {resultado['_pais']} | "
            f"média {info['proximidade']:.2f} | "
            f"Apple: {a_art!r} / {a_alb!r} / {a_trk!r}"
        )

        for _campo, qobuz, apple, comparacao in info["res"]:
            imprimir_comparacao(
                comparacao,
                qobuz,
                apple,
                prefixo="  ",
            )

    resumo(
        f"{len(avaliados)} candidato(s) da Apple; "
        f"{sum(1 for info, _ in avaliados if info['ok'])} "
        "passariam nas travas de texto."
    )

    print(
        "\nPara o veredito completo (tipo single/álbum, termos indesejados, "
        "UPC/ISRC): python tools/apple_cover_check.py ARTISTA ÁLBUM FAIXA --all"
    )
    return 0


def pasta_padrao() -> tuple[str, str]:
    """Retorna a pasta de downloads e o config.ini usados pelo projeto."""
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
    """Conta arquivos de áudio até uma profundidade máxima."""
    base = pasta.rstrip(os.sep).count(os.sep)
    total = 0

    for atual, subdirs, nomes in os.walk(pasta):
        if atual.count(os.sep) - base >= profundidade:
            subdirs[:] = []

        subdirs[:] = [d for d in subdirs if not d.startswith(".")]
        total += sum(1 for nome in nomes if nome.lower().endswith((".flac", ".mp3")))

    return total


def pastas_com_audio(
    raizes: list[str],
    maximo: int = 8,
) -> list[tuple[str, int]]:
    """Encontra pastas candidatas com arquivos de áudio."""
    achadas: dict[str, int] = {}

    for raiz_original in raizes:
        raiz = os.path.abspath(os.path.expanduser(raiz_original))

        if not os.path.isdir(raiz):
            continue

        try:
            filhos = [
                os.path.join(raiz, nome)
                for nome in sorted(os.listdir(raiz))
                if not nome.startswith(".") and os.path.isdir(os.path.join(raiz, nome))
            ]
        except OSError:
            continue

        for pasta in [raiz, *filhos]:
            if pasta in achadas:
                continue

            total = (
                sum(
                    1
                    for nome in os.listdir(pasta)
                    if nome.lower().endswith((".flac", ".mp3"))
                )
                if pasta == raiz
                else _contar_audios(pasta)
            )

            if total:
                achadas[pasta] = total

    achadas = {
        caminho: total
        for caminho, total in achadas.items()
        if not any(
            outra != caminho and outra.startswith(caminho + os.sep) for outra in achadas
        )
    }

    return sorted(achadas.items(), key=lambda item: -item[1])[:maximo]


def cmd_onde(args: argparse.Namespace) -> int:
    """Mostra a pasta configurada e procura pastas com áudio."""
    try:
        pasta, config = pasta_padrao()
        print(f"config.ini        : {config}")
        print(
            f"pasta de downloads: {pasta}  "
            f"({'existe' if os.path.isdir(pasta) else 'NÃO existe'})"
        )
    except Exception as exc:
        print(f"Não consegui ler o config.ini: {exc}")
        pasta = ""

    home = os.path.expanduser("~")
    raizes = [home, os.path.join(home, "Documents"), os.getcwd()]

    print("\nPastas com .flac/.mp3 encontradas (HOME, Documents e pasta atual):")
    achadas = pastas_com_audio(raizes)

    if not achadas:
        print(
            "  nenhuma. Confira com `pwd` e `ls`, ou use o caminho "
            "que o app mostra ao baixar."
        )
        return 1

    for caminho, total in achadas:
        print(f"  {total:>5} arquivos  {caminho}")

    print('\nUse: python tools/apple_text_compare.py scan "CAMINHO"')
    return 0


_ESPECIAIS = set(
    "&\u2019\u2018\u201c\u201d\u2013\u2014\u2026/!$@#*:+\u00bf\u00a1\u00b7"
)


def tem_texto_dificil(*textos: str) -> bool:
    """Identifica textos com acentos, símbolos ou espaços incomuns."""
    for texto in textos:
        if not texto:
            continue

        if any(ord(ch) > 127 or ch in _ESPECIAIS for ch in texto):
            return True

        if "  " in texto or texto != texto.strip():
            return True

    return False


def ler_tags(caminho: str) -> tuple[str, str, str] | None:
    """Lê artista, álbum e título das tags do arquivo."""
    import mutagen

    try:
        audio = mutagen.File(caminho, easy=True)
        if audio is None or not audio.tags:
            return None
    except Exception:
        return None

    def pega(chave: str) -> str:
        valor = audio.tags.get(chave)
        return str(valor[0]) if valor else ""

    return pega("artist"), pega("album"), pega("title")


def achar_audios(pasta: str) -> list[str]:
    """Encontra arquivos .flac e .mp3 recursivamente."""
    saida: list[str] = []

    for raiz, _, nomes in os.walk(pasta):
        for nome in sorted(nomes):
            if nome.lower().endswith((".flac", ".mp3")):
                saida.append(os.path.join(raiz, nome))

    return saida


def cmd_scan(args: argparse.Namespace) -> int:
    """Compara as tags locais com o melhor resultado textual da Apple."""
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
        ambos(
            "Rode `python tools/apple_text_compare.py onde` "
            "para procurar a pasta certa."
        )
        return 1

    paises = [pais.strip() for pais in args.paises.split(",") if pais.strip()]
    linhas_csv: list[list[str]] = []
    analisados = rejeitados = inconclusivos = 0
    vistos: set[tuple[str, str, str]] = set()

    for caminho in arquivos:
        tags = ler_tags(caminho)

        if not tags or not tags[0] or not tags[2]:
            continue

        artista, album, titulo = (limpar_tag(tag) for tag in tags)

        if not args.todos and not tem_texto_dificil(artista, album, titulo):
            continue

        if (artista, album, titulo) in vistos:
            continue

        vistos.add((artista, album, titulo))

        if analisados >= args.limite:
            ambos(
                f"\n(limite de {args.limite} faixas atingido; use --limite para mais)"
            )
            break

        analisados += 1
        print(f"\n### {os.path.relpath(caminho, pasta)}")
        print(f"  Qobuz: {artista!r} / {album!r} / {titulo!r}")

        todos_cands: list[ResultadoApple] = []
        erros = 0
        melhor: ComparacaoCandidato | None = None

        for pais in paises:
            cands, erro_pais = candidatos_apple(
                artista,
                album,
                titulo,
                [pais],
                args.consulta,
            )
            erros += erro_pais
            todos_cands.extend(cands)

            if cands:
                atual = min(
                    (
                        comparar_candidato(artista, album, titulo, resultado)
                        for resultado in todos_cands
                    ),
                    key=lambda info: chave_ordem(info, titulo),
                )
                melhor = atual

                if atual["ok"]:
                    break

        if melhor is None:
            if erros:
                inconclusivos += 1
                print(
                    f"  => INCONCLUSIVO: {erros} consulta(s) falharam "
                    "(rede/limite da Apple)"
                )
                veredito = "inconclusivo"
            else:
                print("  Apple: sem resultados")
                veredito = "sem resultados"

            linhas_csv.append(
                [
                    caminho,
                    artista,
                    album,
                    titulo,
                    "",
                    "",
                    "",
                    veredito,
                    "",
                ]
            )
            resumo(f"[{analisados}] {veredito.upper():<14} {artista} - {titulo}")
            continue

        a_art, a_alb, a_trk = melhor["apple"]
        print(f"  Apple ({melhor['tipo']}): {a_art!r} / {a_alb!r} / {a_trk!r}")

        problemas: list[str] = []

        for campo, qobuz, apple, comparacao in melhor["res"]:
            if not comparacao["ok"] or comparacao["avisos"]:
                imprimir_comparacao(
                    comparacao,
                    qobuz,
                    apple,
                    prefixo="  ",
                )

            if not comparacao["ok"]:
                problemas.append(
                    f"{campo} {comparacao['score']:.2f}<{comparacao['limite']:.2f}"
                )

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
            [
                caminho,
                artista,
                album,
                titulo,
                a_art,
                a_alb,
                a_trk,
                veredito,
                "; ".join(problemas),
            ]
        )

        resumo(
            f"[{analisados}] {veredito.upper():<14} {artista} - {titulo}"
            + (f"  ({', '.join(problemas)})" if problemas else "")
        )

    ambos(
        f"\nAnalisadas: {analisados} | "
        f"rejeitariam pela busca textual: {rejeitados} | "
        f"inconclusivas (consulta falhou): {inconclusivos}"
    )

    if args.csv and linhas_csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as arquivo_csv:
            writer = csv.writer(arquivo_csv)
            writer.writerow(
                [
                    "arquivo",
                    "q_artista",
                    "q_album",
                    "q_titulo",
                    "a_artista",
                    "a_album",
                    "a_faixa",
                    "veredito",
                    "problemas",
                ]
            )
            writer.writerows(linhas_csv)

        ambos(f"CSV salvo em {args.csv}")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Executa a interface de linha de comando."""
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
    )
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    comum = argparse.ArgumentParser(add_help=False)
    comum.add_argument(
        "--saida",
        metavar="ARQ.txt",
        help="onde gravar o relatório completo",
    )
    comum.add_argument(
        "--terminal",
        action="store_true",
        help="mostra o relatório completo também na tela",
    )

    selftest = subparsers.add_parser(
        "selftest",
        help="casos típicos, offline",
        parents=[comum],
    )
    selftest.add_argument(
        "--so-falhas",
        action="store_true",
        help="mostra só falhas e avisos de texto vazio",
    )
    selftest.set_defaults(fn=cmd_selftest)

    live = subparsers.add_parser(
        "live",
        help="compara com a Apple de verdade",
        parents=[comum],
    )
    live.add_argument("artista")
    live.add_argument("album")
    live.add_argument("faixa")
    live.add_argument("--paises", default="br,us")
    live.add_argument(
        "--limite",
        type=int,
        default=15,
        help="resultados por consulta",
    )
    live.add_argument(
        "--mostrar",
        type=int,
        default=6,
        help="candidatos exibidos",
    )
    live.set_defaults(fn=cmd_live)

    scan = subparsers.add_parser(
        "scan",
        help="varre uma pasta já baixada",
        parents=[comum],
    )
    scan.add_argument(
        "pasta",
        nargs="?",
        help="padrão: pasta de downloads do config.ini",
    )
    scan.add_argument(
        "--todos",
        action="store_true",
        help="inclui faixas sem acento/símbolo",
    )
    scan.add_argument(
        "--limite",
        type=int,
        default=20,
        help="máximo de faixas analisadas",
    )
    scan.add_argument(
        "--consulta",
        type=int,
        default=10,
        help="resultados por consulta",
    )
    scan.add_argument("--paises", default="br,us")
    scan.add_argument(
        "--pausa",
        type=float,
        default=3.5,
        help="segundos entre consultas à Apple",
    )
    scan.add_argument("--csv", help="salva o resultado em CSV")
    scan.set_defaults(fn=cmd_scan)

    onde = subparsers.add_parser(
        "onde",
        help="descobre a pasta das músicas",
    )
    onde.set_defaults(fn=cmd_onde)

    args = parser.parse_args(argv)

    global INTERVALO, _TERMINAL, _ESPELHAR
    INTERVALO = getattr(args, "pausa", INTERVALO)

    if args.cmd == "onde":
        return args.fn(args)

    destino = caminho_relatorio(args.cmd, args.saida)

    try:
        arquivo = open(destino, "w", encoding="utf-8")
    except OSError as exc:
        print(f"Não consegui criar {destino}: {exc}")
        return 1

    _TERMINAL = sys.stdout
    _ESPELHAR = bool(args.terminal)
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
