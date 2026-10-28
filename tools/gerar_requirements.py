#!/usr/bin/env python3
"""Gera o requirements.txt a partir do pyproject.toml.

Uso:
    python tools/gerar_requirements.py          # escreve o arquivo
    python tools/gerar_requirements.py --check  # só verifica (CI/pre-commit)
"""

import argparse
import sys
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PYPROJECT = RAIZ / "pyproject.toml"
REQUIREMENTS = RAIZ / "requirements.txt"

CABECALHO = """\
# ARQUIVO GERADO -- NAO EDITE A MAO.
#
# A fonte de verdade das dependencias e' o pyproject.toml. Este arquivo e'
# gerado por `python tools/gerar_requirements.py` e existe apenas para
# `pip install -r` em ambientes de dev/Docker.
#
# Contem SOMENTE o nucleo. As features opcionais sao extras:
#   pip install "qobuz-dl-ultra[all]"          # tudo
#   pip install "qobuz-dl-ultra[speed]"        # rapidfuzz + brotli
#   pip install "qobuz-dl-ultra[covers]"       # Pillow
#   pip install "qobuz-dl-ultra[watch]"        # watchdog (--watch)
#   pip install "qobuz-dl-ultra[duplicates]"   # pyacoustid (--find-duplicates)
#   pip install "qobuz-dl-ultra[genius]"       # lyricsgenius
#   pip install "qobuz-dl-ultra[dev]"          # pytest, ruff
"""


def gerar() -> str:
    """Gera o conteúdo do requirements.txt a partir do pyproject.toml."""
    with PYPROJECT.open("rb") as arquivo:
        dados = tomllib.load(arquivo)

    dependencias: list[str] = dados["project"]["dependencies"]
    return CABECALHO + "\n".join(dependencias) + "\n"


def main() -> int:
    """Executa a geração ou a verificação do requirements.txt."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="nao escreve; falha se estiver fora de sincronia",
    )
    args = parser.parse_args()

    esperado = gerar()

    if args.check:
        atual = (
            REQUIREMENTS.read_text(encoding="utf-8") if REQUIREMENTS.exists() else ""
        )

        if atual != esperado:
            print(
                "requirements.txt esta fora de sincronia com o pyproject.toml.\n"
                "Rode: python tools/gerar_requirements.py",
                file=sys.stderr,
            )
            return 1

        print("requirements.txt em sincronia com o pyproject.toml.")
        return 0

    REQUIREMENTS.write_text(esperado, encoding="utf-8")
    total = len(esperado.strip().splitlines()) - len(CABECALHO.strip().splitlines())
    print(f"requirements.txt gerado com {total} dependencias de nucleo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
