#!/usr/bin/env python3
"""Gera um CHANGELOG.md no formato Keep a Changelog a partir do git log.

Exemplos:
    python tools/gerar_changelog.py
    python tools/gerar_changelog.py --para v2.6.0
    python tools/gerar_changelog.py --de v2.5.0 --para v2.6.0
    python tools/gerar_changelog.py --para v2.6.0 --resumido
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

_FS = "\x1f"
_RS = "\x1e"

RUIDO = [
    r"^style:\s*correç(ões|oes) automátic",
    r"^Merge pull request",
    r"^Merge branch",
    r"^Auto-Sync by Working Copy$",
    r"pytest cache",
    r"nodeids",
    r"^Bump version to ",
]

CATEGORIAS = [
    ("Adicionado", [
        r"^feat(?:\([^)]*\))?[!:]",
        r"^Add\b",
        r"^Create\b",
        r"^Novo\b",
        r"^Implement",
    ]),
    ("Modificado", [
        r"^refactor(?:\([^)]*\))?[!:]",
        r"^Refactor\b",
        r"^update(?:\([^)]*\))?[!:]",
        r"^Update\b",
        r"^Modify\b",
        r"^Change\b",
        r"^Configure\b",
        r"^Rename\b",
        r"^deps(?:\([^)]*\))?[!:]",
        r"^chore\(deps\)",
        r"^ci:\s*bump the .* group",
        r"^ci:",
        r".*workflow.*",
        r"^docs(?:\([^)]*\))?[!:]",
        r".*README.*",
        r".*documenta",
    ]),
    ("Removido", [
        r"^Remove\b",
        r"^Delete\b",
        r"^Drop\b",
    ]),
    ("Corrigido", [
        r"^fix(?:\([^)]*\))?[!:]",
        r"^Fix\b",
        r"^Corrig",
        r"^Hotfix\b",
        r"^Bugfix\b",
    ]),
    ("Obsoleto", [
        r"^deprecat",
        r"^Deprecat",
        r"^Obsolet",
    ]),
    ("Segurança", [
        r"^security(?:\([^)]*\))?[!:]",
        r"^Security\b",
        r"^CVE[-:]",
        r".*vulnerabil",
    ]),
]


def _rodar_git(*args: str) -> str:
    resultado = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return resultado.stdout.strip()


def _tag_anterior(ref: str) -> str | None:
    try:
        return _rodar_git("describe", "--tags", "--abbrev=0", f"{ref}^")
    except subprocess.CalledProcessError:
        return None


def _e_ruido(mensagem: str) -> bool:
    return any(re.search(padrao, mensagem, re.IGNORECASE) for padrao in RUIDO)


def _categorizar(mensagem: str) -> str:
    for rotulo, padroes in CATEGORIAS:
        if any(re.search(padrao, mensagem, re.IGNORECASE) for padrao in padroes):
            return rotulo
    return "Modificado"


def _url_base_repositorio() -> str | None:
    try:
        remote = _rodar_git("config", "--get", "remote.origin.url")
    except subprocess.CalledProcessError:
        return None

    match = re.search(r"github\.com[:/]{1,2}([^/]+)/(.+?)(?:\.git)?$", remote)
    if not match:
        return None
    return f"https://github.com/{match.group(1)}/{match.group(2)}"


def _resumo_estatisticas(commit_hash: str) -> str | None:
    try:
        saida = _rodar_git("show", "--shortstat", "--format=", commit_hash)
    except subprocess.CalledProcessError:
        return None

    linhas = saida.splitlines()
    if not linhas:
        return None

    linha = linhas[-1]
    arquivos = re.search(r"(\d+) files? changed", linha)
    adicionadas = re.search(r"(\d+) insertions?\(\+\)", linha)
    removidas = re.search(r"(\d+) deletions?\(-\)", linha)
    if not arquivos:
        return None

    partes = [f"{arquivos.group(1)} arquivo(s) alterado(s)"]
    if adicionadas:
        partes.append(f"+{adicionadas.group(1)}")
    if removidas:
        partes.append(f"-{removidas.group(1)}")
    return ", ".join(partes)


def _coletar_commits(ref_de: str, ref_para: str) -> list[dict[str, str]]:
    formato = f"%h{_FS}%s{_FS}%b{_RS}"
    log_bruto = _rodar_git(
        "log",
        f"--pretty=format:{formato}",
        f"{ref_de}..{ref_para}",
    )

    commits = []
    for registro in log_bruto.split(_RS):
        registro = registro.strip("\n")
        if not registro.strip():
            continue
        campos = registro.split(_FS)
        if len(campos) < 2:
            continue
        commits.append({
            "hash": campos[0].strip(),
            "titulo": campos[1].strip(),
            "corpo": campos[2].strip() if len(campos) > 2 else "",
        })
    return commits


def gerar_changelog(
    ref_de: str,
    ref_para: str,
    detalhado: bool = True,
    versao: str | None = None,
    data_lancamento: str | None = None,
) -> str:
    commits = _coletar_commits(ref_de, ref_para)
    url_base = _url_base_repositorio() if detalhado else None
    grupos: dict[str, list[dict[str, str]]] = {}
    ignorados = 0

    for commit in commits:
        if _e_ruido(commit["titulo"]):
            ignorados += 1
            continue
        grupos.setdefault(_categorizar(commit["titulo"]), []).append(commit)

    versao = versao or ref_para
    data_lancamento = data_lancamento or date.today().isoformat()
    linhas = [f"## [{versao}] - {data_lancamento}", ""]

    for categoria, _ in CATEGORIAS:
        itens = grupos.get(categoria)
        if not itens:
            continue

        linhas.append(f"### {categoria}")
        linhas.append("")
        for item in itens:
            if url_base:
                referencia = (
                    f"[`{item['hash']}`]"
                    f"({url_base}/commit/{item['hash']})"
                )
            else:
                referencia = f"`{item['hash']}`"

            linhas.append(f"- {item['titulo']} ({referencia})")

            if detalhado and item["corpo"]:
                for texto in item["corpo"].splitlines():
                    texto = texto.strip()
                    if texto:
                        linhas.append(f"  {texto}")

            if detalhado:
                estatisticas = _resumo_estatisticas(item["hash"])
                if estatisticas:
                    linhas.append(f"  _{estatisticas}._")
            linhas.append("")

    if not any(grupos.values()):
        linhas.extend([
            "_Sem mudanças relevantes registradas._",
            "",
        ])

    total = len(commits)
    listados = total - ignorados
    linhas.extend([
        "<!--",
        f"Intervalo analisado: {ref_de}..{ref_para}",
        f"Commits analisados: {total}; listados: {listados}; "
        f"ignorados: {ignorados}.",
        "-->",
    ])
    return "\n".join(linhas).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--para",
        default="HEAD",
        help="Ref final (tag, branch ou HEAD). Padrão: HEAD.",
    )
    parser.add_argument(
        "--de",
        default=None,
        help="Ref inicial. Padrão: tag mais próxima antes de --para.",
    )
    parser.add_argument(
        "--versao",
        default=None,
        help="Versão exibida no cabeçalho. Padrão: valor de --para.",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Data no formato AAAA-MM-DD. Padrão: data atual.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Arquivo de saída. Sem esta opção, imprime no terminal.",
    )
    parser.add_argument(
        "--resumido",
        action="store_true",
        help="Omite corpo, estatísticas e links dos commits.",
    )
    args = parser.parse_args()

    ref_de = args.de or _tag_anterior(args.para)
    if ref_de is None:
        print(
            f"Não achei nenhuma tag antes de '{args.para}'. "
            "Use --de explicitamente.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    changelog = gerar_changelog(
        ref_de=ref_de,
        ref_para=args.para,
        detalhado=not args.resumido,
        versao=args.versao,
        data_lancamento=args.data,
    )

    if args.output:
        args.output.write_text(changelog, encoding="utf-8")
    else:
        print(changelog, end="")


if __name__ == "__main__":
    main()
