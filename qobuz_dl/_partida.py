# ==============================================================================
# MÓDULO: _partida.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Primeiros avisos do modo Atalho do iOS, ANTES de o programa terminar
#            de carregar, entrar na conta e ler o link.
#
# Por quê
#   O iOS cancela o "Execute Long Command" que fica tempo demais sem informar
#   progresso. O modo completo (`atalho.py`) só nasce depois do login; a partida
#   (importar o programa, ler o config, entrar na conta) pode demorar dezenas de
#   segundos no iPhone, sem nenhum `progress`. Este módulo cobre esse buraco.
#
# Como
#   * `ligar()` manda o primeiro `progress` assim que o pacote é importado
#     (`qobuz_dl/__init__.py`, só com `--atalho` na linha de comando).
#   * `fase("entrando na conta")` troca o título nos pontos em que o programa
#     passa de uma etapa para outra.
#   * `iniciar_batimento()` renova o título a cada 2 s com os segundos corridos
#     ("Qobuz · entrando na conta · 9 s"), enquanto o login espera a rede.
#   * Ao nascer o modo completo, `parar()` entrega a barra para ele.
#
# Nada de threads: o ios_system só registra a primeira thread do processo; os
# avisos saem da thread principal (importação, laço assíncrono).
# Sem dependências: este módulo é importado antes de qualquer outro.
# ==============================================================================

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import time
from typing import Any, Callable

_relogio: Callable[[], float] = time.monotonic  # trocável nos testes
T0 = _relogio()  # início do processo, para contar os segundos da partida
INTERVALO_S = 2.0  # de quanto em quanto tempo o título é renovado
FASE_INICIAL = "carregando"
# Etapas da partida, na ordem. No iPad o banner do Atalho só mostra o botão de
# parar e o ANEL de progresso (o título não aparece); por isso cada etapa também
# avança o anel (1/6, 2/6...), que nunca fecha: o download assume depois.
ETAPAS = (
    FASE_INICIAL,
    "lendo a configuração",
    "preparando",
    "entrando na conta",
    "lendo o link",
)

_estado: dict[str, Any] = {
    "executar": None,
    "ligado": False,
    "fase": FASE_INICIAL,
    "ultimo": None,
    "tarefa": None,
    "funcionou": False,  # algum `progress` já deu certo (então uma falha é passageira)
    "ultima_fracao": None,
}


def _executar_padrao(comando: str) -> int:
    return int(os.system(f"{comando} >/dev/null 2>&1") or 0)


def segundos(agora: float | None = None) -> int:
    """Segundos desde o início do processo, de 2 em 2 (o título muda a cada 2 s)."""
    total = int((_relogio() if agora is None else agora) - T0)
    return total - total % 2


def titulo(agora: float | None = None) -> str:
    return f"Qobuz · {_estado['fase']} · {segundos(agora)} s"


def fracao() -> tuple[int, int]:
    """(etapa atual, total) do anel; fase desconhecida fica na etapa 1."""
    fase_atual = str(_estado["fase"])
    passo = ETAPAS.index(fase_atual) + 1 if fase_atual in ETAPAS else 1
    return passo, len(ETAPAS) + 1


def ligado() -> bool:
    return bool(_estado["ligado"])


def ligar(
    executar: Callable[[str], int] | None = None, plataforma: str | None = None
) -> bool:
    """Começa a avisar o iOS. Só age no iOS (ou com `executar` injetado)."""
    if _estado["ligado"]:
        return True
    if executar is None:
        if (plataforma or sys.platform) != "ios":
            return False
        executar = _executar_padrao
    _estado["executar"] = executar
    _estado["ligado"] = True
    _estado["ultimo"] = None
    _estado["ultima_fracao"] = None
    _enviar()
    return True


def _enviar() -> None:
    executar = _estado["executar"]
    if not _estado["ligado"] or executar is None:
        return
    texto = titulo()
    anel = fracao()
    try:  # o `listar` do Atalho mostra o que o banner diria (o iPad só mostra o anel)
        from qobuz_dl.fila import publicar_andamento

        publicar_andamento(texto)
    except Exception:  # informação de cortesia: o banner segue sem ela
        pass
    comandos = []
    if texto != _estado["ultimo"]:
        comandos.append(f"progress --title {shlex.quote(texto)}")
    if anel != _estado["ultima_fracao"]:
        comandos.append(f"progress {anel[0]}/{anel[1]}")
    if not comandos:
        return
    try:
        for comando in comandos:
            if executar(comando) != 0:
                # Sem `progress` nenhum (nunca funcionou): desiste, sem barulho.
                # Se já funcionou, a falha é passageira: tenta de novo no próximo
                # aviso em vez de deixar o banner parado em "carregando".
                if not _estado["funcionou"]:
                    _estado["ligado"] = False
                return
            _estado["funcionou"] = True
    except Exception:
        if not _estado["funcionou"]:
            _estado["ligado"] = False
        return
    _estado["ultimo"] = texto
    _estado["ultima_fracao"] = anel


def fase(nome: str) -> None:
    """Troca a etapa mostrada no título (ex.: `entrando na conta`)."""
    _estado["fase"] = nome
    _enviar()


def iniciar_batimento() -> None:
    """Renova o título a cada `INTERVALO_S` até `parar()`. Precisa de laço ativo."""
    if not _estado["ligado"] or _estado["tarefa"] is not None:
        return

    async def _laco() -> None:
        while True:
            await asyncio.sleep(INTERVALO_S)
            _enviar()

    try:
        _estado["tarefa"] = asyncio.get_running_loop().create_task(_laco())
    except RuntimeError:
        _estado["tarefa"] = None


def parar() -> None:
    """Encerra os avisos da partida (o modo completo assume, ou não há mais nada)."""
    tarefa = _estado["tarefa"]
    if tarefa is not None:
        tarefa.cancel()
    _estado["tarefa"] = None
    _estado["ligado"] = False


def reiniciar() -> None:
    """Volta ao estado inicial (testes)."""
    parar()
    _estado.update(
        executar=None,
        fase=FASE_INICIAL,
        ultimo=None,
        funcionou=False,
        ultima_fracao=None,
    )
