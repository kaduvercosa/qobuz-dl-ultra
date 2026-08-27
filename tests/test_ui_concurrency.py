"""Reproduz o problema original: logging + prints concorrentes durante barras
de progresso ativas do tqdm.

ANTES: `logging.basicConfig()` escrevia direto em stderr e `print()` direto em
stdout, sem nenhum lock e sem passar pelo tqdm -- entao as linhas apareciam
picadas ao meio e as barras eram redesenhadas por cima do texto.

AGORA: tudo (logging de qualquer modulo, mensagens semanticas e os prints
internos) atravessa `ui.emit()`, que usa `tqdm.write` sob um unico lock
global.

Critério de aprovação: nenhuma linha de log pode aparecer partida, e a
contagem de mensagens recebidas tem de bater exatamente com a enviada.
"""

import logging
import os
import re
import threading
import time

import pytest

from tqdm.rich import tqdm

from qobuz_dl import ui

N_WORKERS = 6
N_MSGS = 40

# Modulos diferentes logando ao mesmo tempo, como core.py / sync_playlist.py /
# downloader.py fazem na vida real.
loggers = [logging.getLogger(f"qobuz_dl.fake_module_{i}") for i in range(3)]


def worker(idx, bar):
    log = loggers[idx % len(loggers)]
    for n in range(N_MSGS):
        if n % 3 == 0:
            log.info(f"LINHA worker={idx} seq={n:03} via-logging")
        elif n % 3 == 1:
            ui.ok(f"LINHA worker={idx} seq={n:03} via-ui.ok")
        else:
            ui.detail(f"LINHA worker={idx} seq={n:03} via-ui.detail", indent=2)
        bar.update(1)
        time.sleep(0.001)


def main():
    ui.configure(color=False)
    ui.install_logging(level=logging.INFO)

    bars = [
        tqdm(total=N_MSGS, position=i, ncols=ui.progress_ncols(), leave=False)
        for i in range(N_WORKERS)
    ]
    threads = [
        threading.Thread(target=worker, args=(i, bars[i])) for i in range(N_WORKERS)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for b in bars:
        b.close()
    return N_WORKERS * N_MSGS


# ----------------------------------------------------------------------
# TESTE AUTOMATIZADO
#
# CONVERSAO PARA PYTEST: antes este arquivo so' tinha o bloco `__main__`
# acima. A verificacao era MANUAL -- rodar o script e conferir a olho, com
# `grep -c 'LINHA worker='`, se o resultado dava 240. Nada disso rodava em
# `pytest`, entao a protecao contra a regressao de linhas picadas dependia de
# alguem lembrar de fazer a conferencia na mao.
#
# Agora o proprio pytest roda o cenario num subprocesso (necessario: o tqdm
# escreve direto no terminal e brigaria com a captura do pytest) e verifica as
# duas condicoes que importam.
# ----------------------------------------------------------------------

LINHA_COMPLETA = re.compile(r"LINHA worker=\d+ seq=\d{3} via-[\w.]+")
MENCAO_A_LINHA = re.compile(r"LINHA|worker=|seq=|via-")


def _rodar_cenario():
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, __file__],
        capture_output=True,
        text=True,
        env=dict(os.environ, NO_COLOR="1", COLUMNS="100"),
        timeout=180,
    )
    assert r.returncode == 0, f"o cenario falhou:\n{r.stderr[-2000:]}"
    return r.stdout + r.stderr


@pytest.mark.slow
def test_nenhuma_mensagem_e_perdida():
    """Toda mensagem enviada tem de chegar inteira -- 6 workers x 40 = 240."""
    saida = _rodar_cenario()

    completas = LINHA_COMPLETA.findall(saida)

    assert len(completas) == N_WORKERS * N_MSGS, (
        f"esperado {N_WORKERS * N_MSGS} mensagens inteiras, "
        f"encontrado {len(completas)}"
    )


@pytest.mark.slow
def test_nenhuma_linha_sai_picada():
    """A regressao original: sem o lock unico do `ui.emit()`, uma mensagem
    aparecia partida ao meio porque outra thread escrevia no meio dela.

    O teste procura por linhas que MENCIONAM o padrao mas nao casam com ele
    inteiro -- exatamente a assinatura de um texto cortado.
    """
    saida = _rodar_cenario()

    picadas = []
    for linha in saida.splitlines():
        limpa = linha.strip()
        if not limpa or not MENCAO_A_LINHA.search(limpa):
            continue
        # Remove as mensagens inteiras; o que sobrar mencionando o padrao e'
        # fragmento.
        resto = LINHA_COMPLETA.sub("", limpa)
        if MENCAO_A_LINHA.search(resto):
            picadas.append(limpa)

    assert not picadas, "linhas picadas encontradas:\n" + "\n".join(
        repr(x) for x in picadas[:10]
    )


@pytest.mark.slow
def test_todos_os_tres_caminhos_de_saida_aparecem():
    """Controle de sanidade: os testes acima poderiam passar se um dos tres
    caminhos (logging, ui.ok, ui.detail) tivesse parado de emitir. O ponto do
    cenario e' justamente misturar os tres."""
    saida = _rodar_cenario()

    for via in ("via-logging", "via-ui.ok", "via-ui.detail"):
        assert via in saida, f"nenhuma mensagem {via} chegou na saida"


if __name__ == "__main__":
    expected = main()
    print(f"__ESPERADO__={expected}")
