#!/usr/bin/env python3
"""tools/gerar_changelog.py -- gera um changelog categorizado a partir do
git log entre duas tags/refs.

Uso:
    python tools/gerar_changelog.py                  # última tag -> HEAD
    python tools/gerar_changelog.py --para v2.6.0     # última tag antes de v2.6.0 -> v2.6.0
    python tools/gerar_changelog.py --de v2.5.0 --para v2.5.4

Por que um script em vez de uma ferramenta pronta (git-cliff, etc.): o
histórico deste repo não segue Conventional Commits de forma estrita --
tem "Add X", "Update X", "style: ...", "Merge pull request", "Auto-Sync
by Working Copy", commits de cache do pytest, tudo misturado. Ferramentas
que exigem "feat:"/"fix:" rígido não iam categorizar quase nada aqui. Este
script usa heurísticas calibradas no histórico REAL do projeto (ver
CATEGORIAS abaixo) e já filtra o ruído (auto-fix do Ruff, merges, commits
de cache) que não interessa pra quem só quer saber "o que mudou".
"""

import argparse
import re
import subprocess
import sys

# --------------------------------------------------------------------------
# Commits que não aparecem no changelog de jeito nenhum -- ruído de
# ferramenta, não mudança de comportamento pro usuário final. Calibrado a
# partir de padrões reais encontrados no histórico deste projeto.
# --------------------------------------------------------------------------
RUIDO = [
    r"^style: correç(ões|oes) automátic",  # auto-fix do Ruff
    r"^Merge pull request",
    r"^Merge branch",
    r"^Auto-Sync by Working Copy$",
    r"pytest cache",
    r"nodeids",
    r"^Bump version to ",  # a própria versão já aparece no título do release
]

# --------------------------------------------------------------------------
# Categorias, em ordem de prioridade (a primeira que casar vence). Cada
# entrada: (rótulo exibido, lista de regexes aplicadas ao começo da
# mensagem do commit, case-insensitive).
# --------------------------------------------------------------------------
CATEGORIAS = [
    ("🐛 Correções", [r"^fix", r"^Fix\b", r"^Corrig"]),
    ("✨ Novidades", [r"^feat", r"^Add\b", r"^Create\b", r"^Novo\b"]),
    ("🔥 Removido", [r"^Remove\b", r"^Delete\b", r"^Drop\b"]),
    ("♻️ Refatoração", [r"^refactor", r"^Refactor\b"]),
    ("📦 Dependências", [r"^deps", r"^chore\(deps\)", r"^ci: bump the .* group"]),
    (
        "🔧 CI / Infraestrutura",
        [r"^ci:", r".*workflow.*", r"^Configure\b", r"^Rename\b.*\.yml"],
    ),
    ("📝 Documentação", [r"^docs", r".*README.*", r".*documenta"]),
    ("🔄 Alterações", [r"^update", r"^Update\b", r"^Modify\b", r"^Change\b"]),
]


def _rodar_git(*args):
    resultado = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    )
    return resultado.stdout.strip()


def _tag_anterior(ref):
    """Acha a tag mais próxima ANTES de `ref`, não importa o esquema de
    nome (com/sem "v", "V1.5.0", "2.4.5.1" etc. coexistem neste repo)."""
    try:
        return _rodar_git("describe", "--tags", "--abbrev=0", f"{ref}^")
    except subprocess.CalledProcessError:
        return None  # ref é a primeira tag do repo -- sem "anterior"


def _e_ruido(mensagem: str) -> bool:
    return any(re.search(p, mensagem, re.IGNORECASE) for p in RUIDO)


def _categorizar(mensagem: str) -> str:
    for rotulo, padroes in CATEGORIAS:
        if any(re.search(p, mensagem, re.IGNORECASE) for p in padroes):
            return rotulo
    return "🗒️ Outros"


def gerar_changelog(ref_de: str, ref_para: str) -> str:
    log_bruto = _rodar_git("log", "--pretty=format:%s", f"{ref_de}..{ref_para}")
    mensagens = [m for m in log_bruto.split("\n") if m.strip()]

    grupos: dict[str, list[str]] = {}
    ignorados = 0
    for msg in mensagens:
        if _e_ruido(msg):
            ignorados += 1
            continue
        cat = _categorizar(msg)
        grupos.setdefault(cat, []).append(msg)

    linhas = [f"## Mudanças desde `{ref_de}`\n"]
    ordem = [c[0] for c in CATEGORIAS] + ["🗒️ Outros"]
    algo_impresso = False
    for cat in ordem:
        itens = grupos.get(cat)
        if not itens:
            continue
        algo_impresso = True
        linhas.append(f"### {cat}")
        for item in itens:
            linhas.append(f"- {item}")
        linhas.append("")

    if not algo_impresso:
        linhas.append(
            "_Sem mudanças relevantes registradas (só commits de rotina/CI)._\n"
        )

    linhas.append(
        f"<sub>{len(mensagens)} commit(s) no total, {ignorados} de rotina "
        f"(CI/auto-fix/merge) omitido(s) acima.</sub>"
    )
    return "\n".join(linhas)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--para", default="HEAD", help="Ref final (tag, branch, HEAD). Padrão: HEAD."
    )
    parser.add_argument(
        "--de",
        default=None,
        help="Ref inicial. Padrão: a tag mais próxima antes de --para.",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Salva em arquivo em vez de imprimir no stdout.",
    )
    args = parser.parse_args()

    ref_de = args.de or _tag_anterior(args.para)
    if ref_de is None:
        print(
            f"Não achei nenhuma tag antes de '{args.para}' -- "
            "primeira release do projeto? Use --de explicitamente.",
            file=sys.stderr,
        )
        sys.exit(1)

    changelog = gerar_changelog(ref_de, args.para)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(changelog + "\n")
    else:
        print(changelog)


if __name__ == "__main__":
    main()
