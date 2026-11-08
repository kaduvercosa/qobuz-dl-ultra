# ==============================================================================
# duplicates_cmd.py
# ------------------------------------------------------------------------------
# Comando `qobuz-dl duplicates [PASTA]`: acha faixas repetidas e, se pedido, tira
# as sobras do caminho SEM apagar nada.
#
#   duplicates [PASTA]             só lista os grupos e qual cópia ficaria
#   duplicates [PASTA] --mover     move as sobras para PASTA/_duplicatas/
#   duplicates [PASTA] --desfazer  devolve ao lugar o que o último --mover tirou
#
# A detecção é a de `sync.find_duplicate_tracks` (MD5 do áudio FLAC, ID/ISRC,
# metadados). Este módulo só decide QUAL cópia fica e cuida da movimentação.
# Nada é apagado: o diário `_duplicatas/.movidos.json` registra origem e destino
# de cada arquivo, e `--desfazer` usa esse diário.
#
# Qual cópia fica (nesta ordem): FLAC antes de outros formatos; maior arquivo;
# caminho mais curto; ordem alfabética (para o resultado ser sempre o mesmo).
#
# ONDE PROCURAR:
#   - regra de escolha da cópia   -> `escolher_copia`
#   - movimentação e diário       -> `mover_sobras`, `desfazer_movimentos`
# ==============================================================================

from __future__ import annotations

import json
import os
import shutil
from typing import Any, Awaitable, Callable, Optional

from qobuz_dl import ui

PASTA_SOBRAS = "_duplicatas"
DIARIO = ".movidos.json"
_LISTA_MAX = 10


def _chave_de_escolha(caminho: str) -> tuple[int, int, int, str]:
    ext = os.path.splitext(caminho)[1].lower()
    try:
        tamanho = os.path.getsize(caminho)
    except OSError:
        tamanho = 0
    return (0 if ext == ".flac" else 1, -tamanho, len(caminho), caminho)


def escolher_copia(caminhos: list[str]) -> tuple[str, list[str]]:
    """Devolve ``(copia_que_fica, sobras)`` de um grupo de arquivos repetidos."""
    ordenados = sorted(caminhos, key=_chave_de_escolha)
    return ordenados[0], ordenados[1:]


def planejar(grupos: dict[str, list[str]]) -> list[tuple[str, list[str]]]:
    """Uma entrada ``(fica, sobras)`` por grupo, em ordem estável."""
    plano = [escolher_copia(caminhos) for caminhos in grupos.values()]
    return sorted(plano, key=lambda item: item[0])


def _destino_livre(pasta: str, nome: str) -> str:
    base, ext = os.path.splitext(nome)
    candidato = os.path.join(pasta, nome)
    n = 2
    while os.path.exists(candidato):
        candidato = os.path.join(pasta, f"{base} ({n}){ext}")
        n += 1
    return candidato


def _caminho_diario(diretorio: str) -> str:
    return os.path.join(diretorio, PASTA_SOBRAS, DIARIO)


def _ler_diario(diretorio: str) -> list[dict[str, str]]:
    try:
        with open(_caminho_diario(diretorio), encoding="utf-8") as arquivo:
            dados = json.load(arquivo)
    except (OSError, ValueError):
        return []
    return dados if isinstance(dados, list) else []


def _gravar_diario(diretorio: str, itens: list[dict[str, str]]) -> None:
    caminho = _caminho_diario(diretorio)
    if not itens:
        try:
            os.unlink(caminho)
        except FileNotFoundError:
            # Já não existe: é o estado que queríamos.
            pass
        return
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    temporario = caminho + ".tmp"
    with open(temporario, "w", encoding="utf-8") as arquivo:
        json.dump(itens, arquivo, ensure_ascii=False, indent=2)
    os.replace(temporario, caminho)


def mover_sobras(
    diretorio: str, plano: list[tuple[str, list[str]]]
) -> tuple[int, list[str]]:
    """Move as sobras para ``diretorio/_duplicatas``; devolve ``(movidos, erros)``."""
    pasta = os.path.join(diretorio, PASTA_SOBRAS)
    os.makedirs(pasta, exist_ok=True)
    diario = _ler_diario(diretorio)
    movidos = 0
    erros: list[str] = []
    try:
        for fica, sobras in plano:
            for origem in sobras:
                destino = _destino_livre(pasta, os.path.basename(origem))
                try:
                    shutil.move(origem, destino)
                except OSError as erro:
                    erros.append(f"{origem}: {erro}")
                    continue
                diario.append({"origem": origem, "destino": destino, "fica": fica})
                movidos += 1
    finally:
        # Grava mesmo se algo estourar no meio: o que já saiu precisa ser desfazível.
        _gravar_diario(diretorio, diario)
    return movidos, erros


def desfazer_movimentos(diretorio: str) -> tuple[int, list[str]]:
    """Devolve ao lugar original o que ``mover_sobras`` tirou; ``(devolvidos, avisos)``."""
    diario = _ler_diario(diretorio)
    devolvidos = 0
    avisos: list[str] = []
    restantes: list[dict[str, str]] = []
    for item in diario:
        origem, destino = item.get("origem", ""), item.get("destino", "")
        if not os.path.exists(destino):
            avisos.append(f"já não está em _duplicatas: {os.path.basename(destino)}")
            continue
        if os.path.exists(origem):
            avisos.append(f"o lugar original está ocupado: {origem}")
            restantes.append(item)
            continue
        try:
            os.makedirs(os.path.dirname(origem), exist_ok=True)
            shutil.move(destino, origem)
        except OSError as erro:
            avisos.append(f"{origem}: {erro}")
            restantes.append(item)
            continue
        devolvidos += 1
    _gravar_diario(diretorio, restantes)
    if not restantes:
        try:
            os.rmdir(os.path.join(diretorio, PASTA_SOBRAS))
        except OSError:
            # Pasta não vazia (sobrou algo do usuário): deixa como está.
            pass
    return devolvidos, avisos


def _painel_plano(plano: list[tuple[str, list[str]]], diretorio: str) -> None:
    sobras = sum(len(s) for _f, s in plano)
    painel = ui.Painel("🔁 FAIXAS DUPLICADAS")
    painel.kv("Pasta", diretorio)
    painel.kv("Grupos", str(len(plano)))
    painel.kv("Cópias a mais", str(sobras))
    painel.emit()
    for fica, outras in plano[:_LISTA_MAX]:
        grupo = ui.Painel(os.path.basename(fica))
        grupo.ok(f"fica: {os.path.relpath(fica, diretorio)}")
        for caminho in outras:
            grupo.warn(f"sobra: {os.path.relpath(caminho, diretorio)}")
        grupo.emit(lead=False)
    if len(plano) > _LISTA_MAX:
        ui.emit_always(f"   … e mais {len(plano) - _LISTA_MAX} grupo(s).")


async def cmd_duplicates(
    args: Any,
    *,
    directory: str,
    buscar: Optional[Callable[[str], Awaitable[dict[str, list[str]]]]] = None,
) -> int:
    """Executa ``duplicates``; devolve o código de saída (0 ok, 1 erro parcial)."""
    diretorio = os.path.abspath(os.path.expanduser(directory))
    if not os.path.isdir(diretorio):
        ui.error(f"Pasta não encontrada: {diretorio}")
        return 1

    if getattr(args, "desfazer", False):
        devolvidos, avisos = desfazer_movimentos(diretorio)
        painel = ui.Painel("↩️  DUPLICATAS DEVOLVIDAS")
        painel.kv("Arquivos devolvidos", str(devolvidos))
        for aviso in avisos:
            painel.warn(aviso)
        painel.emit()
        return 1 if avisos else 0

    if buscar is None:
        from qobuz_dl.sync import find_duplicate_tracks

        buscar = find_duplicate_tracks

    grupos = await buscar(diretorio)
    if not grupos:
        ui.Painel("🔁 FAIXAS DUPLICADAS").kv("Pasta", diretorio).ok(
            "Nenhuma duplicata encontrada."
        ).emit()
        return 0

    plano = planejar(grupos)
    como_json = bool(getattr(args, "json", False))
    if como_json:
        ui.emit_json([{"fica": fica, "sobras": sobras} for fica, sobras in plano])
    else:
        _painel_plano(plano, diretorio)

    if not getattr(args, "mover", False):
        if not como_json:
            ui.info("Nada foi alterado. Para tirar as sobras do caminho: --mover")
        return 0

    movidos, erros = mover_sobras(diretorio, plano)
    resumo = ui.Painel("📦 SOBRAS MOVIDAS")
    resumo.kv("Movidas", str(movidos))
    resumo.kv("Para", os.path.join(diretorio, PASTA_SOBRAS))
    resumo.info("Para devolver tudo: qobuz-dl duplicates --desfazer")
    for erro in erros[:_LISTA_MAX]:
        resumo.error(erro)
    resumo.emit()
    return 1 if erros else 0


def pasta_alvo(args: Any, padrao: str) -> Optional[str]:
    """Pasta pedida na linha de comando ou, se não houve, a do config.ini."""
    return getattr(args, "DIR", None) or padrao
