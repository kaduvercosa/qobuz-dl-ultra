#!/usr/bin/env python3
"""tools/gerar_changelog.py -- gera um changelog detalhado e categorizado a
partir do git log entre duas tags/refs.

Uso:
    python tools/gerar_changelog.py                     # última tag -> HEAD
    python tools/gerar_changelog.py --para v2.6.0        # última tag antes de v2.6.0 -> v2.6.0
    python tools/gerar_changelog.py --de v2.5.0 --para v2.5.4
    python tools/gerar_changelog.py --para v2.6.0 --resumido   # sem corpo/estatísticas

Por que um script em vez de uma ferramenta pronta (git-cliff, etc.): o
histórico deste repo não segue Conventional Commits de forma estrita --
tem "Add X", "Update X", "style: ...", "Merge pull request", "Auto-Sync
by Working Copy", commits de cache do pytest, tudo misturado. Ferramentas
que exigem "feat:"/"fix:" rígido não iam categorizar quase nada aqui. Este
script usa heurísticas calibradas no histórico REAL do projeto (ver
CATEGORIAS abaixo) e já filtra o ruído (auto-fix do Ruff, merges, commits
de cache) que não interessa pra quem só quer saber "o que mudou".

Modo detalhado (padrão): para cada commit relevante mostra, além do
título, o hash curto (com link pro GitHub se der pra descobrir o remote),
o corpo da mensagem (se houver) e um resumo de arquivos/linhas alteradas.
Use --resumido para voltar ao formato enxuto (só título por commit).
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

# Registro Unicode usado como separador de campo/registro no git log --
# improvável de aparecer numa mensagem de commit de verdade.
_FS = "\x1f"  # field separator
_RS = "\x1e"  # record separator


def _rodar_git(*args, check=True):
    """Roda um comando git e devolve a saída (stdout, já sem espaços nas pontas)."""
    resultado = subprocess.run(
        ["git", *args], capture_output=True, text=True, check=check
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
    """Verifica se a mensagem de commit é ruído (merge/bump/auto-fix)."""
    return any(re.search(p, mensagem, re.IGNORECASE) for p in RUIDO)


def _categorizar(mensagem: str) -> str:
    """Categoriza a mensagem de um commit."""
    for rotulo, padroes in CATEGORIAS:
        if any(re.search(p, mensagem, re.IGNORECASE) for p in padroes):
            return rotulo
    return "🗒️ Outros"


def _url_base_repositorio():
    """Tenta descobrir a URL do repositório no GitHub a partir do remote
    'origin', pra transformar hashes de commit em links clicáveis. Se não
    achar (sem remote, remote de outro provedor, sandbox sem rede etc.),
    devolve None e o changelog simplesmente não inclui links."""
    try:
        url = _rodar_git("config", "--get", "remote.origin.url")
    except subprocess.CalledProcessError:
        return None

    m = re.search(r"github\.com[:/]{1,2}([^/]+)/(.+?)(?:\.git)?$", url)
    if not m:
        return None
    dono, repo = m.group(1), m.group(2)
    return f"https://github.com/{dono}/{repo}"


def _resumo_estatisticas(commit_hash: str) -> str | None:
    """Devolve algo como '3 arquivo(s) alterado(s), +42 -7' pro commit dado,
    ou None se não conseguir calcular (ex.: commit raiz sem pai)."""
    try:
        saida = _rodar_git("show", "--shortstat", "--format=", commit_hash)
    except subprocess.CalledProcessError:
        return None
    linhas_saida = saida.strip().splitlines()
    if not linhas_saida:
        return None
    linha = linhas_saida[-1]

    m_arquivos = re.search(r"(\d+) files? changed", linha)
    m_add = re.search(r"(\d+) insertions?\(\+\)", linha)
    m_del = re.search(r"(\d+) deletions?\(-\)", linha)
    if not m_arquivos:
        return None

    partes = [f"{m_arquivos.group(1)} arquivo(s) alterado(s)"]
    if m_add:
        partes.append(f"+{m_add.group(1)}")
    if m_del:
        partes.append(f"-{m_del.group(1)}")
    return ", ".join(partes)


def _coletar_commits(ref_de: str, ref_para: str) -> list[dict]:
    """Coleta hash, título e corpo de cada commit no intervalo, na ordem
    em que aparecem no histórico (mais recente primeiro)."""
    formato = f"%h{_FS}%s{_FS}%b{_RS}"
    log_bruto = _rodar_git("log", f"--pretty=format:{formato}", f"{ref_de}..{ref_para}")

    commits = []
    for registro in log_bruto.split(_RS):
        registro = registro.strip("\n")
        if not registro.strip():
            continue
        campos = registro.split(_FS)
        if len(campos) < 2:
            continue
        h = campos[0].strip()
        titulo = campos[1].strip()
        corpo = campos[2].strip() if len(campos) > 2 else ""
        commits.append({"hash": h, "titulo": titulo, "corpo": corpo})
    return commits


def gerar_changelog(ref_de: str, ref_para: str, detalhado: bool = True) -> str:
    """Gera o changelog a partir do histórico do git.

    No modo detalhado (padrão), além do título de cada commit, inclui:
    hash curto (linkado pro GitHub quando o remote é identificável),
    corpo da mensagem de commit (se houver) e um resumo de arquivos/
    linhas alteradas. Também mostra a contagem de itens por categoria
    e um resumo final mais completo do que o formato original.
    """
    commits = _coletar_commits(ref_de, ref_para)
    url_base = _url_base_repositorio() if detalhado else None

    grupos: dict[str, list[dict]] = {}
    ignorados = 0
    for c in commits:
        if _e_ruido(c["titulo"]):
            ignorados += 1
            continue
        cat = _categorizar(c["titulo"])
        grupos.setdefault(cat, []).append(c)

    linhas = [f"## Mudanças desde `{ref_de}` até `{ref_para}`\n"]
    ordem = [c[0] for c in CATEGORIAS] + ["🗒️ Outros"]
    algo_impresso = False

    for cat in ordem:
        itens = grupos.get(cat)
        if not itens:
            continue
        algo_impresso = True
        linhas.append(f"### {cat} ({len(itens)})")
        for item in itens:
            hash_curto = item["hash"]
            if url_base:
                ref_commit = f"[`{hash_curto}`]({url_base}/commit/{hash_curto})"
            else:
                ref_commit = f"`{hash_curto}`"

            linhas.append(f"- **{item['titulo']}** ({ref_commit})")

            if detalhado:
                if item["corpo"]:
                    for linha_corpo in item["corpo"].splitlines():
                        linha_corpo = linha_corpo.strip()
                        if linha_corpo:
                            linhas.append(f"  > {linha_corpo}")

                stats = _resumo_estatisticas(item["hash"])
                if stats:
                    linhas.append(f"  - _{stats}_")
        linhas.append("")

    if not algo_impresso:
        linhas.append(
            "_Sem mudanças relevantes registradas (só commits de rotina/CI)._\n"
        )

    total = len(commits)
    linhas.append(
        f"---\n**Resumo:** {total} commit(s) no total analisados entre `{ref_de}` "
        f"e `{ref_para}`, sendo {ignorados} de rotina (CI/auto-fix/merge) "
        f"omitido(s) da lista acima e {total - ignorados} listados em "
        f"{len(grupos)} categoria(s)."
    )

    return "\n".join(linhas)


def main():
    """Ponto de entrada principal."""
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
    parser.add_argument(
        "--resumido",
        action="store_true",
        help="Gera só título por commit, sem corpo/estatísticas/links (formato antigo).",
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

    changelog = gerar_changelog(ref_de, args.para, detalhado=not args.resumido)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(changelog + "\n")
    else:
        print(changelog)


if __name__ == "__main__":
    main()
