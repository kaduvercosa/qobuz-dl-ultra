#!/usr/bin/env python3
"""Gera o requirements.txt a partir do pyproject.toml.

POR QUE ISTO EXISTE
-------------------
O projeto tinha tres fontes de verdade de dependencia (pyproject.toml,
setup.py e requirements.txt) e elas JA' divergiram na pratica: o Pillow
estava como ``>10.0.0`` num arquivo e ``>=10.0.0`` nos outros -- o primeiro
EXCLUI a propria 10.0.0.

O setup.py virou um shim que le' o pyproject. Este script fecha o ultimo
buraco: o requirements.txt passa a ser gerado, nao mantido a mao.

Uso:
    python tools/gerar_requirements.py          # escreve o arquivo
    python tools/gerar_requirements.py --check  # so' verifica (CI/pre-commit)

O modo --check sai com codigo 1 se o arquivo estiver fora de sincronia, e e'
exatamente o que tests/test_dependencias.py chama.
"""

import argparse
import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib  # type: ignore

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
    with PYPROJECT.open("rb") as f:
        dados = tomllib.load(f)
    deps = dados["project"]["dependencies"]
    return CABECALHO + "\n".join(deps) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--check",
        action="store_true",
        help="nao escreve; falha se estiver fora de sincronia",
    )
    args = p.parse_args()

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
    n = len(esperado.strip().splitlines()) - len(CABECALHO.strip().splitlines())
    print(f"requirements.txt gerado com {n} dependencias de nucleo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
