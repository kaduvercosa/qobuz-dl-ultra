"""Busca de letras em modo paralelo: sem travar as outras faixas.

Regressão do travamento no a-Shell: a busca de letra rodava dentro do
`print_lock` e, com uma fonte em cooldown (429), dormia até 60 s por tentativa
com o lock preso -- as outras faixas e o loop (que imprime pelo mesmo lock)
ficavam parados, embora os arquivos já estivessem baixados.
"""

from unittest.mock import MagicMock

import pytest

from qobuz_dl import lyrics_engine as le
from qobuz_dl import resilient_http as rh
from qobuz_dl import ui
from qobuz_dl.lyrics_engine import LyricsCandidate, LyricsEngine

pytestmark = pytest.mark.unit

SYNC = "[00:01.00]linha um\n[00:05.00]linha dois"


class _Resp:
    status_code = 429
    headers = {"Retry-After": "60"}

    def close(self):
        pass


def _engine():
    eng = LyricsEngine(session=MagicMock())
    eng._inject_metadata = MagicMock(return_value=True)
    eng._save_lrc_file = MagicMock(return_value=True)
    eng._inject_instrumental_pauses = lambda t: t
    return eng


def _run(eng, **kw):
    return eng.fetch_and_inject("/m/a.flac", "Art", "Tit", "Alb", **kw)


def _captura(monkeypatch):
    saidas = []
    monkeypatch.setattr(le.tqdm, "write", lambda m: saidas.append(m))
    return saidas


def test_busca_nao_segura_o_print_lock(monkeypatch):
    _captura(monkeypatch)
    eng = _engine()
    estados = []

    def fonte(query):
        estados.append(ui.print_lock.locked())
        return LyricsCandidate(SYNC, "LRCLIB", True)

    eng.PROVIDERS = ("fonte",)
    eng.fonte = fonte

    assert _run(eng, buffer_output=True)["success"] is True
    assert estados == [False]
    assert ui.print_lock.locked() is False


def test_buffer_output_so_escreve_no_fim_e_em_bloco(monkeypatch):
    saidas = _captura(monkeypatch)
    eng = _engine()
    durante = []

    def fonte(query):
        durante.append(list(saidas))
        return LyricsCandidate(SYNC, "LRCLIB", True)

    eng.PROVIDERS = ("fonte",)
    eng.fonte = fonte

    _run(eng, buffer_output=True, track_number=3)

    assert durante == [[]]  # nada escrito enquanto a rede trabalha
    assert saidas and all("[3]" in linha for linha in saidas)


def test_sem_buffer_escreve_na_hora(monkeypatch):
    saidas = _captura(monkeypatch)
    eng = _engine()
    durante = []

    def fonte(query):
        durante.append(len(saidas))
        return LyricsCandidate(SYNC, "LRCLIB", True)

    eng.PROVIDERS = ("fonte",)
    eng.fonte = fonte

    _run(eng)

    assert durante[0] >= 1  # "Procurando letras" já saiu (modo sequencial)


def test_fonte_limitada_e_pulada_e_a_proxima_e_usada(monkeypatch):
    saidas = _captura(monkeypatch)
    eng = _engine()

    def limitada(query):
        raise rh.HttpBudgetExceeded("host", 60.0)

    eng.PROVIDERS = ("limitada", "boa")
    eng.PROVIDER_LABELS = {"limitada": "Musixmatch"}
    eng.limitada = limitada
    eng.boa = lambda query: LyricsCandidate(SYNC, "LRCLIB", True)

    resultado = _run(eng, buffer_output=True)

    assert resultado["success"] is True and resultado["source"] == "LRCLIB"
    assert resultado["error"] is None  # limite não é erro da faixa
    texto = "\n".join(saidas)
    assert "limite de requisições em Musixmatch" in texto
    assert "usando outra fonte" in texto


def test_todas_limitadas_avisa_sem_marcar_erro(monkeypatch):
    saidas = _captura(monkeypatch)
    eng = _engine()

    def limitada(query):
        raise rh.HttpBudgetExceeded("host", 60.0)

    eng.PROVIDERS = ("a", "b")
    eng.PROVIDER_LABELS = {"a": "Musixmatch", "b": "LRCLIB"}
    eng.a = limitada
    eng.b = limitada

    resultado = _run(eng)

    assert resultado["success"] is False and resultado["error"] is None
    assert "limite de requisições em Musixmatch, LRCLIB" in "\n".join(saidas)


def test_erro_comum_de_fonte_continua_sendo_erro(monkeypatch):
    saidas = _captura(monkeypatch)
    eng = _engine()

    def quebrada(query):
        raise RuntimeError("boom")

    eng.PROVIDERS = ("quebrada",)
    eng.quebrada = quebrada

    resultado = _run(eng)

    assert resultado["error"] == "boom"
    assert any("boom" in linha for linha in saidas)


def test_network_budget_pula_fontes_reais_em_cooldown_sem_dormir(monkeypatch):
    """Ponta a ponta: 429 com Retry-After 60 não pode parar a faixa."""
    rh._reset_state_for_tests()
    dormiu = []
    monkeypatch.setattr(rh.time, "sleep", dormiu.append)
    saidas = _captura(monkeypatch)
    eng = _engine()
    eng.session.get.return_value = _Resp()

    resultado = _run(
        eng,
        buffer_output=True,
        network_budget=le.LYRICS_NETWORK_BUDGET,
        duration=200,
    )

    assert dormiu == []
    assert resultado["success"] is False and resultado["error"] is None
    texto = "\n".join(saidas)
    assert "Musixmatch" in texto and "LRCLIB" in texto


def test_sem_network_budget_o_comportamento_antigo_e_mantido(monkeypatch):
    rh._reset_state_for_tests()
    dormiu = []
    monkeypatch.setattr(rh.time, "sleep", dormiu.append)
    _captura(monkeypatch)
    eng = _engine()
    eng.PROVIDERS = ("_provider_lrclib",)
    eng.session.get.side_effect = [_Resp(), MagicMock(status_code=404)]

    _run(eng)

    assert dormiu and dormiu[0] > 5  # esperou o cooldown inteiro, como antes
