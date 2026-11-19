# ==============================================================================
# MÓDULO: vigia.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Impede que um download TRAVADO segure o Long Command do Atalho.
#
# No a-Shell, quando o Atalho dá erro ou o iOS desiste do comando, o processo
# Python pode continuar vivo em segundo plano (esperando uma rede que não
# responde, uma thread presa...), renovando a marca "download em andamento" da
# fila. O Atalho seguinte então recusa começar. Este módulo evita isso:
#
#   * `pulso()` é chamado sempre que algo anda (bytes baixados, evento, linha de
#     progresso). Custa uma atribuição.
#   * `Vigia` roda numa thread à parte (funciona mesmo com o laço asyncio
#     bloqueado). Sem pulso por `limite` segundos, pede o cancelamento do
#     download (`ao_travar`); se o processo ainda estiver vivo `graca` segundos
#     depois, encerra à força (`ao_forcar`).
#   * `armar_saida()` é a rede de segurança do FIM: terminada a sessão, se o
#     Python não sair em `graca` segundos (thread presa no desligamento), sai à
#     força.
#
# Só usa a biblioteca padrão. O modo Atalho é quem liga tudo isto.
# ==============================================================================

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

ENV_LIMITE = "QOBUZ_DL_ATALHO_LIMITE_S"
LIMITE_PADRAO_S = 420.0  # sem nenhum sinal de vida por tanto tempo = travado
GRACA_CANCELAMENTO_S = 20.0  # tempo para o cancelamento limpo antes de matar
GRACA_SAIDA_S = 15.0  # tempo para o Python sair sozinho depois do fim
INTERVALO_S = 5.0  # de quanto em quanto tempo o vigia olha

_pulso: list[float] = [time.monotonic()]


def pulso() -> None:
    """Avisa que o programa está andando (barato, de qualquer thread)."""
    _pulso[0] = time.monotonic()


def segundos_parado(agora: float | None = None) -> float:
    return (time.monotonic() if agora is None else agora) - _pulso[0]


def limite_do_ambiente(ambiente: Mapping[str, str] | None = None) -> float:
    """`QOBUZ_DL_ATALHO_LIMITE_S` em segundos; 0 desliga o vigia."""
    env = os.environ if ambiente is None else ambiente
    bruto = str(env.get(ENV_LIMITE, "")).strip()
    if not bruto:
        return LIMITE_PADRAO_S
    try:
        valor = float(bruto.replace(",", "."))
    except ValueError:
        return LIMITE_PADRAO_S
    return max(valor, 0.0)


def _liberar_fila() -> None:
    """Solta a marca "download em andamento" para o próximo Atalho não esperar."""
    try:
        from qobuz_dl import fila

        leitor = fila.Fila()
        leitor.limpar_ativo()
        leitor.limpar_andamento()
    except Exception as exc:  # a fila é só cortesia: sair vem antes
        logger.debug("vigia: não consegui soltar a fila: %s", exc)


def forcar_saida(codigo: int, mensagem: str = "") -> None:
    """Escreve o motivo, solta a fila e encerra o processo SEM esperar ninguém."""
    try:
        if mensagem:
            print(mensagem, flush=True)
        _liberar_fila()
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception as exc:  # o que importa é sair
        logger.debug("vigia: falha ao descarregar a saída: %s", exc)
    os._exit(codigo)


def duracao_texto(segundos: float) -> str:
    total = max(int(segundos), 0)
    minutos, resto = divmod(total, 60)
    return f"{minutos} min {resto:02d} s" if minutos else f"{resto} s"


class Vigia:
    """Observa `pulso()`; cancela o que travou e, se preciso, mata o processo."""

    def __init__(
        self,
        ao_travar: Callable[[float], None],
        ao_forcar: Callable[[float], None],
        limite: float = LIMITE_PADRAO_S,
        graca: float = GRACA_CANCELAMENTO_S,
        intervalo: float = INTERVALO_S,
        relogio: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ao_travar = ao_travar
        self.ao_forcar = ao_forcar
        self.limite = limite
        self.graca = graca
        self.intervalo = intervalo
        self._relogio = relogio
        self._parar = threading.Event()
        self._thread: threading.Thread | None = None
        self._cancelou_em: float | None = None

    @property
    def ativo(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def iniciar(self) -> None:
        if self.limite <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._rodar, name="qobuz-vigia", daemon=True
        )
        self._thread.start()

    def parar(self) -> None:
        self._parar.set()

    def _rodar(self) -> None:
        while not self._parar.wait(self.intervalo):
            if self.tique():
                return

    def tique(self) -> bool:
        """Uma olhada. True quando o vigia já fez tudo o que podia (acabou)."""
        agora = self._relogio()
        parado = segundos_parado(agora)
        if self._cancelou_em is None:
            if parado >= self.limite:
                self._cancelou_em = agora
                self._chamar(self.ao_travar, parado)
            return False
        if agora - self._cancelou_em >= self.graca:
            self._chamar(self.ao_forcar, parado)
            return True
        return False

    @staticmethod
    def _chamar(funcao: Callable[[float], None], parado: float) -> None:
        try:
            funcao(parado)
        except Exception as exc:  # o vigia nunca pode cair antes de agir
            logger.debug("vigia: %s falhou: %s", getattr(funcao, "__name__", "?"), exc)


def armar_saida(
    codigo: int = 0,
    graca: float = GRACA_SAIDA_S,
    forcar: Callable[[int], None] = forcar_saida,
) -> threading.Timer:
    """Rede de segurança do fim: se o Python não sair em `graca` s, sai à força.

    O timer é daemon: se o Python sair sozinho, ele some junto, sem custo.
    """
    temporizador = threading.Timer(graca, forcar, args=(codigo,))
    temporizador.daemon = True
    temporizador.start()
    return temporizador
