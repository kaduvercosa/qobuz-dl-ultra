"""Vigia do modo Atalho: um download travado não pode segurar o Long Command."""

from __future__ import annotations

import threading

from qobuz_dl import events, vigia


class _Relogio:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _vigia(relogio: _Relogio, limite: float = 60.0, graca: float = 20.0):
    chamadas: list[tuple[str, float]] = []
    v = vigia.Vigia(
        ao_travar=lambda s: chamadas.append(("travar", s)),
        ao_forcar=lambda s: chamadas.append(("forcar", s)),
        limite=limite,
        graca=graca,
        intervalo=0.01,
        relogio=relogio,
    )
    vigia._pulso[0] = relogio.t  # o relógio do vigia começa agora
    return v, chamadas


def test_pulso_zera_o_tempo_parado() -> None:
    vigia._pulso[0] = 10.0
    assert vigia.segundos_parado(25.0) == 15.0
    vigia.pulso()
    assert vigia.segundos_parado() < 5.0


def test_limite_do_ambiente() -> None:
    assert vigia.limite_do_ambiente({}) == vigia.LIMITE_PADRAO_S
    assert vigia.limite_do_ambiente({vigia.ENV_LIMITE: "90"}) == 90.0
    assert vigia.limite_do_ambiente({vigia.ENV_LIMITE: "1,5"}) == 1.5
    assert vigia.limite_do_ambiente({vigia.ENV_LIMITE: "0"}) == 0.0
    assert vigia.limite_do_ambiente({vigia.ENV_LIMITE: "-3"}) == 0.0
    assert vigia.limite_do_ambiente({vigia.ENV_LIMITE: "abc"}) == vigia.LIMITE_PADRAO_S


def test_duracao_texto() -> None:
    assert vigia.duracao_texto(42) == "42 s"
    assert vigia.duracao_texto(185) == "3 min 05 s"


def test_vigia_nao_faz_nada_enquanto_ha_sinal_de_vida() -> None:
    r = _Relogio()
    v, chamadas = _vigia(r)
    r.t += 50
    assert v.tique() is False
    vigia._pulso[0] = r.t  # algo andou
    r.t += 50
    assert v.tique() is False
    assert chamadas == []


def test_vigia_cancela_uma_vez_e_depois_mata_se_o_processo_continua() -> None:
    r = _Relogio()
    v, chamadas = _vigia(r, limite=60, graca=20)
    r.t += 61
    assert v.tique() is False
    assert [c[0] for c in chamadas] == ["travar"]
    assert chamadas[0][1] >= 60
    r.t += 10  # dentro da graça: não repete o cancelamento nem mata
    assert v.tique() is False
    assert [c[0] for c in chamadas] == ["travar"]
    r.t += 11  # graça esgotada e o processo ainda aqui: mata
    assert v.tique() is True
    assert [c[0] for c in chamadas] == ["travar", "forcar"]


def test_vigia_sobrevive_a_um_callback_que_levanta_erro() -> None:
    r = _Relogio()

    def quebra(_: float) -> None:
        raise RuntimeError("boom")

    v = vigia.Vigia(quebra, quebra, limite=10, graca=5, relogio=r)
    vigia._pulso[0] = r.t
    r.t += 11
    assert v.tique() is False  # o ao_travar quebrou, mas o vigia segue
    r.t += 6
    assert v.tique() is True


def test_vigia_com_limite_zero_nem_cria_thread() -> None:
    v = vigia.Vigia(lambda s: None, lambda s: None, limite=0)
    v.iniciar()
    assert not v.ativo


def test_vigia_roda_numa_thread_daemon_e_para() -> None:
    v = vigia.Vigia(lambda s: None, lambda s: None, limite=1000, intervalo=0.01)
    v.iniciar()
    assert v.ativo and v._thread is not None and v._thread.daemon
    v.parar()
    v._thread.join(timeout=2)
    assert not v.ativo


def test_armar_saida_chama_forcar_depois_da_graca() -> None:
    feito = threading.Event()
    codigos: list[int] = []

    def forcar(codigo: int) -> None:
        codigos.append(codigo)
        feito.set()

    t = vigia.armar_saida(3, graca=0.05, forcar=forcar)
    assert t.daemon  # não impede o Python de sair sozinho
    assert feito.wait(timeout=2)
    assert codigos == [3]


def test_todo_evento_conta_como_sinal_de_vida() -> None:
    vigia._pulso[0] = 0.0
    events.emitir("teste")
    assert vigia.segundos_parado() < 5.0
