#!/usr/bin/env python3
"""Gera a referência de comandos (docs/COMANDOS.md) a partir do parser real.

A fonte da verdade é `qobuz_dl/commands.py`: este script só lê o argparse
(`qobuz_dl_args()`) e escreve Markdown. Assim a referência nunca fica diferente
do que o programa aceita, e um teste (`tests/unit/test_referencia_comandos.py`)
falha se alguém mudar um comando e esquecer de regenerar.

Uso:
    python tools/gerar_referencia.py            # reescreve docs/COMANDOS.md
    python tools/gerar_referencia.py --check    # só confere (sai com 1 se estiver velho)
    python tools/gerar_referencia.py --stdout   # imprime em vez de gravar
"""

from __future__ import annotations

import argparse
import os
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARQUIVO = os.path.join(RAIZ, "docs", "COMANDOS.md")

if RAIZ not in sys.path:
    sys.path.insert(0, RAIZ)

CABECALHO = """\
# Referência de comandos

> Gerado por `python tools/gerar_referencia.py` a partir de `qobuz_dl/commands.py`.
> **Não edite à mão**: mude o parser e gere de novo.

Para a ajuda completa de um comando no terminal: `qobuz-dl <comando> --help`.
"""


def _subparsers(parser: argparse.ArgumentParser):
    for acao in parser._actions:
        if isinstance(acao, argparse._SubParsersAction):
            return acao
    return None


def _flags(parser: argparse.ArgumentParser):
    """``(opções, valor, ajuda, padrão)`` das flags e argumentos do parser."""
    linhas = []
    for acao in parser._actions:
        if (
            isinstance(acao, (argparse._HelpAction, argparse._SubParsersAction))
            or acao.help is argparse.SUPPRESS
        ):
            continue
        if acao.option_strings:
            nome = ", ".join(f"`{o}`" for o in acao.option_strings)
        else:
            nome = f"`{acao.metavar or acao.dest}`"
        if acao.nargs == 0:
            valor = ""
        elif acao.metavar:
            valor = str(acao.metavar)
        elif acao.choices:
            valor = "|".join(str(c) for c in acao.choices)
        else:
            valor = "" if not acao.option_strings else acao.dest.upper()
        padrao = acao.default
        if padrao in (None, False, argparse.SUPPRESS, ""):
            padrao_txt = ""
        else:
            padrao_txt = f"`{padrao}`"
        ajuda = " ".join((acao.help or "").split()).replace("|", "\\|")
        linhas.append((nome, f"`{valor}`" if valor else "", ajuda, padrao_txt))
    return linhas


def _tabela(linhas) -> list[str]:
    if not linhas:
        return []
    out = ["| Opção | Valor | O que faz | Padrão |", "| :--- | :--- | :--- | :--- |"]
    out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in linhas]
    return out


def gerar() -> str:
    from qobuz_dl.cli import _COMMAND_DESCRIPTIONS_PT
    from qobuz_dl.commands import (
        CODIGOS_DE_SAIDA,
        SAIDA_POR_COMANDO,
        qobuz_dl_args,
    )

    parser = qobuz_dl_args(default_folder="~/Qobuz Downloads")
    acao = _subparsers(parser)
    saida = [CABECALHO]

    saida.append("## Índice\n")
    nomes = []
    for escolha in acao._choices_actions:
        nomes.append(escolha.dest)
    saida += [f"- [`{n}`](#{n})" for n in nomes]
    saida.append("- [Flags globais](#flags-globais)")
    saida.append("- [Códigos de saída](#códigos-de-saída)\n")

    for nome in nomes:
        sub = acao.choices[nome]
        apelidos = [n for n, sp in acao.choices.items() if sp is sub and n != nome]
        saida.append(f"## {nome}\n")
        resumo = _COMMAND_DESCRIPTIONS_PT.get(nome) or " ".join(
            (sub.description or "").split()
        )
        if resumo:
            saida.append(resumo + "\n")
        saida.append(f"**Uso:** `{sub.usage or ''}`\n")
        if apelidos:
            saida.append(
                "**Apelidos:** " + ", ".join(f"`{a}`" for a in apelidos) + "\n"
            )
        if nome in SAIDA_POR_COMANDO:
            saida.append(f"**Saída diferente de 0:** {SAIDA_POR_COMANDO[nome]}\n")
        saida += _tabela(_flags(sub))
        saida.append("")

    saida.append("## Flags globais\n")
    saida.append(
        "Valem antes do comando (`qobuz-dl -v stats`) e, as de saída, também depois dele.\n"
    )
    saida += _tabela(_flags(parser))
    saida.append("")

    saida.append("## Códigos de saída\n")
    saida.append("| Código | Significado |")
    saida.append("| :--- | :--- |")
    saida += [f"| `{c}` | {t} |" for c, t in CODIGOS_DE_SAIDA]
    saida.append("")
    return "\n".join(saida).rstrip() + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="só confere")
    ap.add_argument("--stdout", action="store_true", help="imprime em vez de gravar")
    args = ap.parse_args(argv)
    texto = gerar()
    if args.stdout:
        sys.stdout.write(texto)
        return 0
    atual = None
    if os.path.isfile(ARQUIVO):
        with open(ARQUIVO, encoding="utf-8") as f:
            atual = f.read()
    if args.check:
        if atual != texto:
            print(
                "docs/COMANDOS.md está desatualizado: rode python tools/gerar_referencia.py"
            )
            return 1
        return 0
    os.makedirs(os.path.dirname(ARQUIVO), exist_ok=True)
    with open(ARQUIVO, "w", encoding="utf-8", newline="\n") as f:
        f.write(texto)
    print(f"gravado: {os.path.relpath(ARQUIVO, RAIZ)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
