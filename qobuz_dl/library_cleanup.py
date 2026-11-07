# ==============================================================================
# library_cleanup.py
# ------------------------------------------------------------------------------
# Comando `qobuz-dl cleanup [PASTA]`: limpa o que ficou para trás na biblioteca.
#
#   cleanup [PASTA]            só mostra o que seria removido (simulação)
#   cleanup [PASTA] --apagar   remove de verdade
#   cleanup --idade HORAS      idade mínima dos temporários (padrão: 24)
#
# Remove dois tipos de lixo, e nada além deles:
#   1. temporários de download abandonados (`*.part`, `*.atomic.tmp`...), a
#      lista vem de `fileio.STALE_PATTERNS`; só entram os parados há mais de
#      `--idade` horas, então um download em andamento nunca é tocado;
#   2. pastas que ficaram completamente vazias (sem arquivo nenhum, nem oculto).
#
# Nunca apaga áudio, capa, letra, relatório nem a própria pasta raiz.
#
# ONDE PROCURAR:
#   - o que conta como pasta vazia   -> `pastas_vazias`
#   - fluxo e telas                  -> `cmd_cleanup`
# ==============================================================================

from __future__ import annotations

import os
from typing import Any

from qobuz_dl import fileio, ui

IDADE_PADRAO_HORAS = 24.0
_LISTA_MAX = 15


def pastas_vazias(raiz: str, ignorar: frozenset = frozenset()) -> list[str]:
    """Pastas sob ``raiz`` sem nenhum arquivo, nem em subpastas.

    ``ignorar`` são arquivos que serão apagados antes (os temporários): pastas
    que só tinham eles contam como vazias. Links simbólicos nunca contam. A
    própria ``raiz`` nunca entra. A ordem é de baixo para cima, então apagar na
    sequência esvazia os pais.
    """
    vazias: set[str] = set()
    resultado: list[str] = []
    for dirpath, dirnames, filenames in os.walk(raiz, topdown=False):
        if os.path.abspath(dirpath) == os.path.abspath(raiz):
            continue
        if os.path.islink(dirpath):
            continue
        tem_arquivo = any(
            os.path.join(dirpath, nome) not in ignorar for nome in filenames
        )
        subpastas_ok = all(os.path.join(dirpath, d) in vazias for d in dirnames)
        if not tem_arquivo and subpastas_ok:
            vazias.add(dirpath)
            resultado.append(dirpath)
    return resultado


def _remover_pastas(pastas: list[str]) -> list[str]:
    removidas = []
    for pasta in pastas:
        try:
            os.rmdir(pasta)
        except OSError:
            # Ganhou arquivo desde a varredura, ou sem permissão: deixa.
            continue
        removidas.append(pasta)
    return removidas


async def cmd_cleanup(args: Any, *, directory: str) -> int:
    """Executa ``cleanup``; devolve 0 (ok) ou 1 (pasta inexistente)."""
    raiz = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(raiz):
        ui.error(f"Pasta não encontrada: {raiz}")
        return 1

    apagar = bool(getattr(args, "apagar", False))
    horas = getattr(args, "idade", None)
    horas = IDADE_PADRAO_HORAS if horas is None else float(horas)

    temporarios = fileio.cleanup_stale_parts(
        raiz, max_age_seconds=horas * 3600, dry_run=not apagar
    )
    # Em simulação os temporários ainda existem: conte-os como já removidos.
    vazias = pastas_vazias(raiz, ignorar=frozenset(temporarios))
    if apagar:
        vazias = _remover_pastas(vazias)

    titulo = "🧹 LIMPEZA" if apagar else "🧹 LIMPEZA (simulação)"
    painel = ui.Painel(titulo)
    painel.kv("Pasta", raiz)
    verbo = "Removidos" if apagar else "A remover"
    painel.kv(f"{verbo}: temporários", str(len(temporarios)))
    painel.kv(f"{verbo}: pastas vazias", str(len(vazias)))
    if not temporarios and not vazias:
        painel.ok("Nada para limpar.")
    painel.emit()

    for rotulo, itens in (("Temporários", temporarios), ("Pastas vazias", vazias)):
        if not itens:
            continue
        lista = ui.Painel(rotulo)
        for caminho in itens[:_LISTA_MAX]:
            lista.detail(os.path.relpath(caminho, raiz))
        if len(itens) > _LISTA_MAX:
            lista.detail(f"… e mais {len(itens) - _LISTA_MAX}")
        lista.emit(lead=False)

    if (temporarios or vazias) and not apagar:
        ui.info("Nada foi apagado. Para remover de verdade: qobuz-dl cleanup --apagar")
    return 0
