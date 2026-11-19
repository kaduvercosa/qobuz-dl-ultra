"""Anel do iOS no modo Atalho: bytes dentro da faixa e batida contra o "sem progresso".

O iOS pode cancelar o Long Command cujo anel não anda; o anel anda com os bytes
baixados (`progresso_bytes`, 0-85 % da faixa) e, se mesmo assim parar, com uma
batida lenta que SEMPRE anda (a reserva de 15 % cobre o pós-processamento).
"""

from __future__ import annotations

import asyncio
import os
from unittest import mock

from qobuz_dl import _partida, atalho, events, progresso_bytes


class _Gravador:
    def __init__(self) -> None:
        self.comandos: list[str] = []

    def __call__(self, comando: str) -> int:
        self.comandos.append(comando)
        return 0


def _relogio_manual(inicio: float = 100.0):
    agora = [inicio]

    def relogio() -> float:
        return agora[0]

    return agora, relogio


def _novo(**kw):
    events.reiniciar()
    progresso_bytes.reiniciar()
    a = atalho.Atalho(**kw)
    events.assinar(a.ao_evento)
    return a


def _fim(a) -> None:
    events.desassinar(a.ao_evento)
    progresso_bytes.reiniciar()


# --- bytes dentro da faixa --------------------------------------------------
def test_bytes_baixados_movem_o_anel_dentro_da_faixa() -> None:
    a = _novo(executar=_Gravador(), escala=100, batida=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    assert a._anel_locked() == (0, 200)
    progresso_bytes.registrar("f1", 50, 100)
    assert a._anel_locked() == (42, 200)  # bytes ocupam 0-85 % da faixa
    progresso_bytes.registrar("f1", 100, 100)
    assert a._anel_locked() == (84, 200)  # o resto é reserva do pós-processo
    progresso_bytes.concluir("f1")
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    assert a._anel_locked() == (100, 200)
    _fim(a)


def test_anel_nunca_anda_para_tras_quando_o_download_recomeca() -> None:
    a = _novo(executar=_Gravador(), escala=100, batida=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    progresso_bytes.registrar("f1", 80, 100)
    assert a._anel_locked() == (68, 100)
    progresso_bytes.registrar("f1", 20, 100)  # nova tentativa do mesmo arquivo
    assert a._anel_locked() == (68, 100)
    progresso_bytes.registrar("f1", 90, 100)
    assert a._anel_locked() == (76, 100)
    _fim(a)


def test_faixas_em_paralelo_somam_no_anel() -> None:
    a = _novo(executar=_Gravador(), escala=100, batida=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 4)
    progresso_bytes.registrar("f1", 50, 100)
    progresso_bytes.registrar("f2", 50, 100)
    assert a._anel_locked() == (85, 400)
    _fim(a)


def test_album_pronto_fecha_o_anel() -> None:
    a = _novo(executar=_Gravador(), escala=100, batida=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 3)
    progresso_bytes.registrar("f1", 60, 100)
    events.lancamento_concluido("/m/A", True, _relatorio(3))
    progresso_bytes.concluir("f1")
    assert a._anel_locked() == (300, 300)
    _fim(a)


def _relatorio(total: int) -> dict:
    return {
        "identificacao": {"titulo": "Disco", "artista": "Artista"},
        "progresso": {
            "resumo": {
                "total": total,
                "concluidas": total,
                "puladas": 0,
                "falhas": 0,
                "pendentes": 0,
            }
        },
    }


def test_escala_1_e_o_modo_antigo_so_faixas_prontas() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=1, batida=1)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    progresso_bytes.registrar("f1", 50, 100)
    agora[0] += 100  # nem os bytes nem a batida mexem no modo antigo
    assert a._anel_locked() == a._totais() == (0, 2)
    _fim(a)


def test_enviar_manda_o_anel_fino_ao_progress() -> None:
    gravador = _Gravador()
    a = _novo(executar=gravador, escala=100, batida=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    a._enviar(forcar=True)
    assert gravador.comandos[-1] == "progress 0/200"
    progresso_bytes.registrar("f1", 50, 100)
    a._enviar()
    assert gravador.comandos[-1] == "progress 42/200"
    n = len(gravador.comandos)
    a._enviar()
    assert len(gravador.comandos) == n  # nada mudou, nada enviado
    _fim(a)


# --- batida -----------------------------------------------------------------
def test_batida_anda_uma_unidade_quando_o_anel_para() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=8)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    assert a._anel_locked() == (0, 100)
    agora[0] += 7.0
    assert a._anel_locked() == (0, 100)  # ainda dentro do prazo
    agora[0] += 1.5
    assert a._anel_locked() == (1, 100)
    agora[0] += 8.5
    assert a._anel_locked() == (2, 100)
    _fim(a)


def test_batida_nunca_fecha_o_anel_e_o_total_cresce_quando_a_reserva_acaba() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=1)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    anterior = (0, 100)
    for _ in range(400):
        agora[0] += 1.5
        n, m = a._anel_locked()
        assert n < m  # a batida nunca fecha o anel
        assert n >= anterior[0] and m >= anterior[1]  # e nada volta atrás
        anterior = (n, m)
    assert anterior[0] == 400  # o anel andou a cada batida, sem travar
    assert anterior[1] > 100  # a reserva acabou e o total cresceu
    _fim(a)


def test_batida_nao_mexe_em_anel_ja_fechado() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=1)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    assert a._anel_locked() == (100, 100)
    agora[0] += 50
    assert a._anel_locked() == (100, 100)
    _fim(a)


def test_progresso_real_assume_e_o_anel_nao_volta() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=8)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    a._anel_locked()
    for _ in range(3):
        agora[0] += 8.5
        a._anel_locked()
    assert a._anel_locked() == (3, 100)  # 3 batidas
    progresso_bytes.registrar("f1", 2, 100)  # o real (1) ainda está atrás
    assert a._anel_locked() == (3, 100)
    progresso_bytes.registrar("f1", 40, 100)
    assert a._anel_locked() == (34, 100)
    _fim(a)


def test_batida_antes_do_primeiro_lancamento_continua_da_partida() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=8)
    assert a._anel_locked() == (0, 0)  # sem total: segue o `--indeterminate`
    agora[0] += 8.5
    base = len(_partida.ETAPAS) + 1
    assert a._anel_locked() == (base + 1, base + 1 + 100)
    # o 1º lançamento chega: o total real aparece e o que foi enviado não volta
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    n, m = a._anel_locked()
    assert (n, m) == (base + 1, 200)
    _fim(a)


def test_batida_zero_desliga() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    a._anel_locked()
    agora[0] += 1000
    assert a._anel_locked() == (0, 100)
    _fim(a)


def _maior_parada(a, agora, segundos: float, acao=None) -> float:
    """Maior tempo seguido SEM o anel (feito) andar, avançando o relógio de 0,5 em 0,5 s."""
    ultimo_n, desde, maior = None, agora[0], 0.0
    fim = agora[0] + segundos
    while agora[0] <= fim:
        if acao:
            acao(agora[0] - (fim - segundos))
        n, _ = a._anel_locked()
        if n != ultimo_n:
            maior = max(maior, agora[0] - desde)
            ultimo_n, desde = n, agora[0]
        agora[0] += 0.5
    return max(maior, agora[0] - desde)


def test_pos_processamento_longo_nao_deixa_o_anel_parar() -> None:
    # Regressão: com os bytes em 99 % o anel não tinha mais folga e ficava parado.
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=1000, batida=8)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    progresso_bytes.registrar("f1", 100, 100)
    assert a._anel_locked()[0] == 841  # bytes todos baixados: 85 % da faixa
    progresso_bytes.concluir("f1")  # falta tag/capa/letras
    assert _maior_parada(a, agora, 900) <= 8.5  # 15 min de pós-processo
    n, m = a._anel_locked()
    assert n < m
    _fim(a)


def test_rede_parada_e_retomada_nao_congela_o_anel() -> None:
    # Regressão: as batidas da parada inflavam o anel; ao retomar, ele congelava
    # até o progresso real alcançá-lo (minutos com o download andando).
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=1000, batida=8)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    progresso_bytes.registrar("f1", 20_000_000, 600_000_000)
    assert _maior_parada(a, agora, 300) <= 8.5  # rede parada 5 min

    def baixando(t: float) -> None:  # volta a 1 MB/s
        progresso_bytes.registrar("f1", 20_000_000 + int(t) * 1_000_000, 600_000_000)

    assert _maior_parada(a, agora, 300, baixando) <= 8.5
    _fim(a)


def test_sem_tamanho_total_o_anel_continua_andando() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=1000, batida=8)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)

    def baixando(t: float) -> None:  # sem Content-Length: curva que achata
        progresso_bytes.registrar("f1", int(t) * 100_000, None)

    assert _maior_parada(a, agora, 1800, baixando) <= 8.5
    _fim(a)


def test_anel_fecha_cheio_no_fim_mesmo_depois_de_crescer() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio, escala=100, batida=1)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    for _ in range(300):  # esgota a reserva e faz o total crescer
        agora[0] += 1.5
        a._anel_locked()
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    n, m = a._anel_locked()
    assert n == m and m > 100
    _fim(a)


# --- ambiente ---------------------------------------------------------------
def test_escala_do_ambiente() -> None:
    assert atalho.escala_do_ambiente({}) == atalho.ESCALA_PADRAO == 1000
    assert atalho.escala_do_ambiente({"QOBUZ_DL_ATALHO_ESCALA": "1"}) == 1
    assert atalho.escala_do_ambiente({"QOBUZ_DL_ATALHO_ESCALA": "20"}) == 20
    assert atalho.escala_do_ambiente({"QOBUZ_DL_ATALHO_ESCALA": "0"}) == 1
    assert atalho.escala_do_ambiente({"QOBUZ_DL_ATALHO_ESCALA": "-7"}) == 1
    assert atalho.escala_do_ambiente({"QOBUZ_DL_ATALHO_ESCALA": "5000"}) == 1000
    for ruim in ("abc", "inf", "nan", " "):
        assert atalho.escala_do_ambiente({"QOBUZ_DL_ATALHO_ESCALA": ruim}) == 1000


def test_batida_do_ambiente() -> None:
    assert atalho.batida_do_ambiente({}) == atalho.BATIDA_PADRAO_S == 8.0
    assert atalho.batida_do_ambiente({"QOBUZ_DL_ATALHO_BATIDA_S": "0"}) == 0.0
    assert atalho.batida_do_ambiente({"QOBUZ_DL_ATALHO_BATIDA_S": "12,5"}) == 12.5
    assert atalho.batida_do_ambiente({"QOBUZ_DL_ATALHO_BATIDA_S": "-3"}) == 0.0
    for ruim in ("abc", "nan", "inf", " "):
        assert atalho.batida_do_ambiente({"QOBUZ_DL_ATALHO_BATIDA_S": ruim}) == 8.0


def test_construtor_le_o_ambiente_quando_nao_recebe_valores() -> None:
    amb = {"QOBUZ_DL_ATALHO_ESCALA": "50", "QOBUZ_DL_ATALHO_BATIDA_S": "3"}
    with mock.patch.dict(os.environ, amb):
        events.reiniciar()
        a = atalho.Atalho(executar=_Gravador())
        assert (a.escala, a.batida) == (50, 3.0)


# --- simulação --------------------------------------------------------------
def test_simulacao_com_escala_fina_fecha_o_anel_e_nunca_volta() -> None:
    gravador = _Gravador()
    with mock.patch.dict(os.environ, {"QOBUZ_DL_ATALHO_ESCALA": "100"}):
        asyncio.run(atalho.simular(pausa=0.01, executar=gravador, emitir_resumo=False))
    pares = [
        tuple(int(x) for x in c.split()[1].split("/"))
        for c in gravador.comandos
        if c.startswith("progress ") and "/" in c and "--" not in c
    ]
    assert pares
    assert pares[-1] == (1300, 1300)  # 13 faixas x 100
    feitos = [n for n, _ in pares]
    assert feitos == sorted(feitos)
    assert progresso_bytes.em_andamento() == 0
