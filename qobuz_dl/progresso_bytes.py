# ==============================================================================
# MÓDULO: progresso_bytes.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Bytes já baixados de cada faixa em andamento, para o ANEL do Atalho
#            do iOS andar DENTRO de uma faixa grande (não só quando ela acaba).
#
# Por quê
#   O iOS cancela o "Execute Long Command" cujo progresso não anda. O anel só
#   contava faixas prontas: numa faixa grande (Hi-Res) ou num lote lento ele
#   ficava parado por minutos, o iOS desistia (erro/timeout no Atalho) e o
#   download seguia vivo em segundo plano no a-Shell.
#
# Como
#   * O downloader chama `registrar(chave, baixado, total)` a cada pedaço e
#     `concluir(chave)` ao terminar (com sucesso ou não).
#   * `modo Atalho` (`atalho.py`) chama `parcial()` a cada tique: a soma das
#     frações das faixas em andamento (cada uma sempre < 1).
#
# Só usa a biblioteca padrão e uma trava curta: pode ser chamado de qualquer
# thread, em cada pedaço baixado, sem custo perceptível.
# ==============================================================================

from __future__ import annotations

import threading

# Sem o tamanho total (o servidor não informou) a fração segue uma curva que
# só cresce e nunca chega a 1: b / (b + DESCONHECIDO_BYTES).
DESCONHECIDO_BYTES = 32 * 1024 * 1024
# Uma faixa em andamento nunca passa daqui: só o evento de "faixa pronta" fecha.
TETO_FRACAO = 0.99

_trava = threading.Lock()
_faixas: dict[str, tuple[int, int | None]] = {}


def registrar(chave: str, baixado: int, total: int | None) -> None:
    """Anota quantos bytes a faixa `chave` já baixou (e o total, se conhecido)."""
    try:
        feitos = max(int(baixado), 0)
        esperado = int(total) if total else None
    except (TypeError, ValueError):
        return  # dado estranho nunca pode atrapalhar o download
    with _trava:
        _faixas[str(chave)] = (feitos, esperado if esperado and esperado > 0 else None)


def concluir(chave: str) -> None:
    """A faixa `chave` acabou de baixar (ou falhou): sai da conta."""
    with _trava:
        _faixas.pop(str(chave), None)


def _fracao(baixado: int, total: int | None) -> float:
    if total:
        return min(baixado / total, TETO_FRACAO)
    return min(baixado / (baixado + DESCONHECIDO_BYTES), TETO_FRACAO)


def parcial() -> float:
    """Soma das frações (0 a <1 cada) das faixas que estão baixando agora."""
    with _trava:
        return sum(_fracao(b, t) for b, t in _faixas.values())


def em_andamento() -> int:
    """Quantas faixas estão baixando agora."""
    with _trava:
        return len(_faixas)


def reiniciar() -> None:
    """Esquece tudo (início de uma sessão e testes)."""
    with _trava:
        _faixas.clear()
