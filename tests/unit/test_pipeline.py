"""Pipeline de download: estágios rede/pós, limitador adaptativo e resolução."""

import asyncio

import pytest

from qobuz_dl import pipeline as pl
from qobuz_dl.pipeline import (
    AdaptiveLimiter,
    Pipeline,
    build_slots,
    current_handoff,
    resolve_pending,
    run_in_pipeline,
)

pytestmark = pytest.mark.unit


class _Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


# ------------------------------------------------------------ AdaptiveLimiter
async def test_limitador_segura_o_teto():
    lim = AdaptiveLimiter(2)
    pico = atual = 0

    async def uso():
        nonlocal pico, atual
        async with lim:
            atual += 1
            pico = max(pico, atual)
            await asyncio.sleep(0.01)
            atual -= 1

    await asyncio.gather(*(uso() for _ in range(8)))
    assert pico == 2 and lim.in_use == 0


async def test_throttle_corta_pela_metade_com_cooldown_e_minimo():
    agora = [0.0]
    mudancas = []
    lim = AdaptiveLimiter(
        8,
        cooldown=5.0,
        clock=lambda: agora[0],
        on_change=lambda o, n, m: mudancas.append((o, n)),
    )
    lim.on_throttle()
    lim.on_throttle()  # dentro do cooldown: ignorado
    assert lim.limit == 4
    agora[0] = 6.0
    lim.on_throttle()
    agora[0] = 12.0
    lim.on_throttle()
    agora[0] = 18.0
    lim.on_throttle()  # já está no mínimo
    assert lim.limit == 1
    assert mudancas == [(8, 4), (4, 2), (2, 1)]


async def test_sucessos_sobem_um_por_vez_ate_o_maximo():
    lim = AdaptiveLimiter(4, grow_after=2, cooldown=0)
    lim.on_throttle()
    assert lim.limit == 2
    for _ in range(4):
        lim.on_success()
    assert lim.limit == 4
    for _ in range(10):
        lim.on_success()
    assert lim.limit == 4  # nunca passa do configurado


async def test_falha_zera_a_sequencia_de_sucessos():
    lim = AdaptiveLimiter(4, grow_after=3, cooldown=0)
    lim.on_throttle()  # 4 -> 2
    lim.on_success()
    lim.on_success()
    lim.on_throttle()  # 2 -> 1 e zera a sequência
    lim.on_success()
    lim.on_success()
    assert lim.limit == 1


async def test_cortar_o_teto_nao_derruba_quem_ja_esta_em_uso():
    lim = AdaptiveLimiter(4, cooldown=0)
    for _ in range(4):
        await lim.acquire()
    lim.on_throttle()
    assert lim.limit == 2 and lim.in_use == 4
    esperando = asyncio.ensure_future(lim.acquire())
    await asyncio.sleep(0)
    assert not esperando.done()
    lim.release()
    lim.release()
    lim.release()  # em uso: 1 -> abre vaga (limite 2)
    await asyncio.sleep(0)
    assert esperando.done() and lim.in_use == 2


async def test_cancelar_quem_espera_nao_vaza_vaga():
    lim = AdaptiveLimiter(1)
    await lim.acquire()
    esperando = asyncio.ensure_future(lim.acquire())
    await asyncio.sleep(0)
    esperando.cancel()
    with pytest.raises(asyncio.CancelledError):
        await esperando
    lim.release()
    assert lim.in_use == 0
    await asyncio.wait_for(lim.acquire(), 1)  # a fila não ficou travada


# ------------------------------------------------------------------- Pipeline
async def test_vaga_de_rede_e_liberada_no_fim_do_download():
    """Com 1 vaga de rede, a 2ª faixa baixa enquanto a 1ª ainda faz o pós."""
    pipe = Pipeline(1)
    vaga = asyncio.Semaphore(1)
    linha = []
    soltar_pos = asyncio.Event()

    async def faixa(nome):
        h = current_handoff.get()
        linha.append(f"{nome}:baixando")
        await asyncio.sleep(0.01)
        await h.downloaded()
        linha.append(f"{nome}:pos")
        if nome == "a":
            await soltar_pos.wait()
        return nome

    async def worker(nome):
        async with vaga:
            return await pipe.run(lambda: faixa(nome))

    r_a = await worker("a")  # volta assim que baixou (pós ainda preso)
    assert isinstance(r_a, asyncio.Task) and not r_a.done()
    r_b = await asyncio.wait_for(worker("b"), 1)  # a vaga de rede já estava livre
    assert "b:baixando" in linha and not r_a.done()
    soltar_pos.set()
    final = await resolve_pending([r_a, r_b])
    assert final == ["a", "b"]


async def test_faixa_que_acaba_antes_de_baixar_devolve_o_resultado_direto():
    pipe = Pipeline(2)

    async def pulada():
        return "skipped"

    assert await pipe.run(pulada) == "skipped"


async def test_excecao_antes_do_download_sobe_normalmente():
    pipe = Pipeline(2)

    async def quebra():
        raise ValueError("falhou cedo")

    with pytest.raises(ValueError, match="falhou cedo"):
        await pipe.run(quebra)
    assert pipe._inflight._value == 2 + pipe.post_limit  # nada vazou


async def test_excecao_no_pos_vira_valor_em_resolve_pending():
    pipe = Pipeline(2)

    async def faixa():
        await current_handoff.get().downloaded()
        await asyncio.sleep(0.01)  # a falha acontece de fato no pós
        raise RuntimeError("falhou no pós")

    tarefa = await pipe.run(faixa)
    assert isinstance(tarefa, asyncio.Task)
    final = await resolve_pending([True, tarefa, "existing"])
    assert final[0] is True and final[2] == "existing"
    assert isinstance(final[1], RuntimeError)


async def test_vagas_do_pos_e_do_total_sao_devolvidas():
    pipe = Pipeline(2)

    async def faixa():
        await current_handoff.get().downloaded()
        await asyncio.sleep(0.01)
        return True

    tarefas = [await pipe.run(faixa) for _ in range(5)]
    assert await resolve_pending(tarefas) == [True] * 5
    assert pipe._post._value == pipe.post_limit
    assert pipe._inflight._value == 2 + pipe.post_limit


async def test_limite_de_faixas_no_pos():
    pipe = Pipeline(8)
    assert pipe.post_limit == pl.MAX_POST_WORKERS
    simultaneas = pico = 0

    async def faixa():
        nonlocal simultaneas, pico
        await current_handoff.get().downloaded()
        simultaneas += 1
        pico = max(pico, simultaneas)
        await asyncio.sleep(0.01)
        simultaneas -= 1
        return True

    tarefas = [await pipe.run(faixa) for _ in range(10)]
    await resolve_pending(tarefas)
    assert pico <= pl.MAX_POST_WORKERS


async def test_cancel_all_cancela_o_pos_pendente():
    pipe = Pipeline(2)
    travada = asyncio.Event()

    async def faixa():
        await current_handoff.get().downloaded()
        await travada.wait()

    tarefa = await pipe.run(faixa)
    await pipe.cancel_all()
    assert tarefa.cancelled()
    assert pipe._post._value == pipe.post_limit


async def test_handoff_nao_vaza_para_fora_do_run():
    pipe = Pipeline(2)

    async def faixa():
        await current_handoff.get().downloaded()
        return True

    await resolve_pending([await pipe.run(faixa)])
    assert current_handoff.get() is None


async def test_run_in_pipeline_sem_pipeline_so_executa():
    async def faixa():
        assert current_handoff.get() is None
        return 7

    assert await run_in_pipeline(None, faixa) == 7


# ------------------------------------------------------------- build_slots
async def test_build_slots_so_liga_com_paralelismo_e_configuracao():
    ligado = _Cfg(pipeline=True, adaptive_workers=False)
    sem, pipe = build_slots(1, ligado, parallel=True)
    assert pipe is None and isinstance(sem, asyncio.Semaphore)
    sem, pipe = build_slots(4, ligado, parallel=False)
    assert pipe is None
    sem, pipe = build_slots(4, _Cfg(pipeline=False), parallel=True)
    assert pipe is None
    sem, pipe = build_slots(4, _Cfg(), parallel=True)  # settings sem o campo
    assert pipe is None
    sem, pipe = build_slots(4, ligado, parallel=True)
    assert isinstance(pipe, Pipeline) and isinstance(sem, asyncio.Semaphore)


async def test_build_slots_adaptativo_usa_o_limitador():
    cfg = _Cfg(pipeline=True, adaptive_workers=True)
    sem, pipe = build_slots(4, cfg, parallel=True, on_change=None)
    assert isinstance(sem, AdaptiveLimiter) and pipe.limiter is sem and sem.limit == 4


async def test_faixa_que_baixou_conta_como_sucesso_e_falha_corta_o_teto():
    cfg = _Cfg(pipeline=True, adaptive_workers=True)
    sem, pipe = build_slots(4, cfg, parallel=True, on_change=None)
    sem._cooldown = 0

    async def faixa():
        h = current_handoff.get()
        h.report_throttle("teste")
        await h.downloaded()
        return True

    await resolve_pending([await pipe.run(faixa)])
    assert sem.limit == 2


async def test_falha_no_mesmo_instante_do_download_sobe_para_o_chamador():
    pipe = Pipeline(2)

    async def faixa():
        await current_handoff.get().downloaded()
        raise RuntimeError("falhou logo após baixar")

    with pytest.raises(RuntimeError, match="logo após baixar"):
        await pipe.run(faixa)
    assert pipe._inflight._value == 2 + pipe.post_limit
    assert pipe._post._value == pipe.post_limit
