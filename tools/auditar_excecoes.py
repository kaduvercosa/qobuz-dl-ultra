#!/usr/bin/env python3
"""Audita `except Exception` (e `except:`) que engolem o erro sem deixar rastro.

Uso (na raiz do projeto):

    python tools/auditar_excecoes.py            # resumo + os 40 primeiros silenciosos
    python tools/auditar_excecoes.py --tudo     # lista todos os silenciosos
    python tools/auditar_excecoes.py --arquivo qobuz_dl/downloader.py

Um handler e' "silencioso" quando o corpo NAO relanca (`raise`), NAO usa a excecao
(`except X as exc` e depois `exc` em algum lugar) e NAO chama nada que registre/mostre
o erro (log.*, logger.*, logging.*, print, ui.*). Tipos: pass, continue, return,
fallback (so' atribui um valor padrao) e outro. Nao e'
necessariamente um bug: a politica "nunca levantar" de varios modulos e' valida.
A ideia e' decidir caso a caso: ou registrar em nivel debug (`exc_info=True`), ou
estreitar o tipo (OSError, ValueError...), ou deixar um comentario do porque.

Handlers silenciosos que JA tem um comentario (no `except` ou no corpo) contam como
"documentados" e ficam no fim da lista; o que interessa primeiro sao os SEM comentario.
"""
# auditor v3

from __future__ import annotations

import argparse
import ast
import io
import sys
import tokenize
from collections import Counter
from pathlib import Path

try:  # `| head` nao deve gerar BrokenPipeError
    import signal

    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError, OSError):
    pass

LOGADORES = {"log", "logger", "logging", "ui", "print", "warnings"}
METODOS = {
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "exception",
    "critical",
    "print",
    "emit",
    "emit_always",
    "write",
    "echo",
    "format_exc",
    "print_exc",
}


def _nomes_da_chamada(no: ast.Call) -> set[str]:
    nomes: set[str] = set()
    alvo = no.func
    while isinstance(alvo, ast.Attribute):
        nomes.add(alvo.attr)
        alvo = alvo.value
    if isinstance(alvo, ast.Name):
        nomes.add(alvo.id)
    return nomes


def _largo(tipo: ast.expr | None) -> bool:
    if tipo is None:
        return True
    if isinstance(tipo, ast.Name):
        return tipo.id in {"Exception", "BaseException"}
    if isinstance(tipo, ast.Tuple):
        return any(_largo(e) for e in tipo.elts)
    return False


def _classe(corpo: list[ast.stmt], nome_exc: str | None = None) -> str | None:
    """None = o handler reporta/relanca/guarda o erro; senao devolve o 'tipo' do silencio."""
    for no in ast.walk(ast.Module(body=corpo, type_ignores=[])):
        if isinstance(no, ast.Raise):
            return None
        if isinstance(no, ast.Name) and nome_exc and no.id == nome_exc:
            return None  # `except X as exc` e usa `exc` (guarda, formata, anexa...)
        if isinstance(no, ast.Call):
            nomes = _nomes_da_chamada(no)
            if nomes & LOGADORES or nomes & METODOS:
                return None
    if all(isinstance(s, ast.Pass) for s in corpo):
        return "pass"
    primeiro = corpo[0]
    if isinstance(primeiro, ast.Continue):
        return "continue"
    if isinstance(primeiro, ast.Return):
        return "return"
    if all(isinstance(s, (ast.Assign, ast.AnnAssign, ast.AugAssign)) for s in corpo):
        return "fallback"  # so' atribui um valor padrao e segue
    return "outro"


def _linhas_com_comentario(fonte: str) -> set[int]:
    achadas: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(fonte).readline):
            if tok.type == tokenize.COMMENT:
                achadas.add(tok.start[0])
    except (tokenize.TokenError, IndentationError):
        pass
    return achadas


def varrer(raiz: Path, so_arquivo: str | None):
    achados = []
    total = 0
    for caminho in sorted(raiz.rglob("*.py")):
        if so_arquivo and str(caminho) != so_arquivo:
            continue
        fonte = caminho.read_text(encoding="utf-8")
        try:
            arvore = ast.parse(fonte)
        except SyntaxError:
            continue
        comentadas = _linhas_com_comentario(fonte)
        funcao_de: dict[int, str] = {}
        for no in ast.walk(arvore):
            if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for filho in ast.walk(no):
                    funcao_de[id(filho)] = no.name
        for no in ast.walk(arvore):
            if isinstance(no, ast.ExceptHandler) and _largo(no.type):
                total += 1
                tipo = _classe(no.body, no.name)
                if tipo is not None:
                    fim = getattr(no, "end_lineno", no.lineno) or no.lineno
                    doc = any(n in comentadas for n in range(no.lineno, fim + 1))
                    achados.append(
                        (
                            str(caminho),
                            no.lineno,
                            funcao_de.get(id(no), "<modulo>"),
                            tipo,
                            doc,
                        )
                    )
    return total, achados


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--tudo", action="store_true", help="lista todos, nao so' os 40 primeiros"
    )
    ap.add_argument(
        "--arquivo", help="so' este arquivo (caminho como aparece na lista)"
    )
    ap.add_argument("--pasta", default="qobuz_dl")
    args = ap.parse_args()
    raiz = Path(args.pasta)
    if not raiz.is_dir():
        print(f"Pasta '{raiz}' nao encontrada: rode na raiz do projeto.")
        return 1
    total, achados = varrer(raiz, args.arquivo)
    sem_doc = [a for a in achados if not a[4]]
    por_tipo = Counter(a[3] for a in sem_doc)
    print(
        f"except largos: {total} | silenciosos: {len(achados)} "
        f"(documentados com comentario: {len(achados) - len(sem_doc)}, "
        f"SEM comentario: {len(sem_doc)} {dict(por_tipo)})"
    )
    por_arquivo = Counter(a[0] for a in sem_doc)
    if por_arquivo:
        print(
            "sem comentario, por arquivo:",
            ", ".join(f"{a.split('/')[-1]}={n}" for a, n in por_arquivo.most_common(6)),
        )
    print()
    ordem = {"pass": 0, "continue": 1, "fallback": 2, "return": 3, "outro": 4}
    lista = sorted(achados, key=lambda x: (x[4], ordem[x[3]], x[0], x[1]))
    for arq, linha, funcao, tipo, doc in lista if args.tudo else lista[:40]:
        marca = "ok " if doc else "SEM"
        print(f"{marca} {tipo:8s} {arq}:{linha}  ({funcao})")
    if not args.tudo and len(lista) > 40:
        print(f"... e mais {len(lista) - 40} (use --tudo)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
