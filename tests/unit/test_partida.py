"""Avisos da partida do modo Atalho (_partida.py): progresso antes do login."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys

from qobuz_dl import _partida


class _Gravador:
    def __init__(self, retorno: int = 0) -> None:
        self.comandos: list[str] = []
        self.retorno = retorno

    def __call__(self, comando: str) -> int:
        self.comandos.append(comando)
        return self.retorno


def _limpo() -> None:
    _partida.reiniciar()


def test_ligar_avisa_na_hora_e_ja_mexe_o_anel() -> None:
    _limpo()
    g = _Gravador()
    assert _partida.ligar(executar=g) is True
    assert g.comandos[0].startswith("progress --title 'Qobuz · carregando · ")
    assert g.comandos[1] == "progress 1/6"  # o anel nunca fica vazio na partida
    _limpo()


def test_fora_do_ios_nao_liga_nem_chama_nada() -> None:
    _limpo()
    assert _partida.ligar(plataforma="linux") is False
    assert not _partida.ligado()
    _partida.fase("entrando na conta")  # sem efeito, sem erro
    _limpo()


def test_ligar_duas_vezes_nao_repete_o_aviso() -> None:
    _limpo()
    g = _Gravador()
    _partida.ligar(executar=g)
    total = len(g.comandos)
    assert _partida.ligar(executar=g) is True
    assert len(g.comandos) == total
    _limpo()


def test_fase_troca_o_titulo_so_quando_muda() -> None:
    _limpo()
    g = _Gravador()
    _partida.ligar(executar=g)
    g.comandos.clear()
    _partida.fase("entrando na conta")
    assert len(g.comandos) == 2  # título + anel
    assert "entrando na conta" in g.comandos[0]
    assert g.comandos[1] == "progress 4/6"
    _partida.fase("entrando na conta")  # igual: não repete
    assert len(g.comandos) == 2
    _limpo()


def test_anel_avanca_a_cada_etapa_e_nunca_fecha() -> None:
    _limpo()
    g = _Gravador()
    _partida.ligar(executar=g)
    for etapa in _partida.ETAPAS[1:]:
        _partida.fase(etapa)
    aneis = [c for c in g.comandos if c.startswith("progress ") and "/" in c]
    assert aneis == [f"progress {n}/6" for n in range(1, 6)]
    _limpo()


def test_titulo_traz_a_fase_e_os_segundos() -> None:
    _limpo()
    _partida.fase("lendo o link")
    texto = _partida.titulo(_partida.T0 + 13.4)
    assert texto == "Qobuz · lendo o link · 12 s"
    assert _partida.segundos(_partida.T0 + 1.9) == 0
    assert _partida.segundos(_partida.T0 + 2.0) == 2
    _limpo()


def test_sem_progress_desiste_sem_erro() -> None:
    _limpo()
    g = _Gravador(retorno=127)
    _partida.ligar(executar=g)
    assert not _partida.ligado()
    n = len(g.comandos)
    _partida.fase("entrando na conta")
    assert len(g.comandos) == n  # parou de tentar
    _limpo()


def test_executar_que_levanta_erro_nao_derruba_a_partida() -> None:
    _limpo()

    def explode(_c: str) -> int:
        raise OSError("sem ios_system")

    _partida.ligar(executar=explode)
    assert not _partida.ligado()
    _limpo()


def test_batimento_renova_o_titulo_com_os_segundos() -> None:
    _limpo()
    g = _Gravador()
    relogio = [_partida.T0]
    real = _partida._relogio
    _partida._relogio = lambda: relogio[0]
    antigo = _partida.INTERVALO_S
    _partida.INTERVALO_S = 0.01

    async def roda() -> None:
        _partida.ligar(executar=g)
        _partida.iniciar_batimento()
        for _ in range(3):
            relogio[0] += 2
            await asyncio.sleep(0.05)
        _partida.parar()

    try:
        asyncio.run(roda())
    finally:
        _partida._relogio = real
        _partida.INTERVALO_S = antigo
    titulos = [c for c in g.comandos if c.startswith("progress --title")]
    assert any("· 2 s" in t for t in titulos)
    assert any("· 6 s" in t for t in titulos)
    _limpo()


def test_parar_para_os_avisos() -> None:
    _limpo()
    g = _Gravador()
    _partida.ligar(executar=g)
    _partida.parar()
    n = len(g.comandos)
    _partida.fase("lendo o link")
    assert len(g.comandos) == n
    assert not _partida.ligado()
    _limpo()


def test_pacote_nao_avisa_sem_a_flag_atalho() -> None:
    """Importar o pacote sem `--atalho` não toca no `progress`."""
    codigo = (
        "import sys; sys.argv=['qobuz-dl','dl','x'];"
        "import qobuz_dl; from qobuz_dl import _partida;"
        "print(_partida.ligado())"
    )
    saida = subprocess.run(
        [sys.executable, "-c", codigo],
        capture_output=True,
        text=True,
        cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        check=True,
    ).stdout.strip()
    assert saida == "False"


class _Intermitente:
    """`progress` que funciona, falha uma vez (passageira) e volta a funcionar."""

    def __init__(self, falhar_na_chamada: int) -> None:
        self.comandos: list[str] = []
        self.falhar = falhar_na_chamada

    def __call__(self, comando: str) -> int:
        self.comandos.append(comando)
        return 1 if len(self.comandos) == self.falhar else 0


def test_falha_passageira_depois_de_funcionar_nao_desliga_o_banner() -> None:
    _limpo()
    g = _Intermitente(falhar_na_chamada=3)  # 1-2: partida ok; 3: falha
    _partida.ligar(executar=g)
    _partida.fase("entrando na conta")  # chamada 3: falha
    assert _partida.ligado()  # continua ligado
    _partida.fase("lendo o link")  # volta a funcionar
    assert any(
        c.startswith("progress --title 'Qobuz · lendo o link") for c in g.comandos[-2:]
    )
    _limpo()


def test_barra_indeterminada_que_falha_nao_derruba_o_titulo() -> None:
    _limpo()
    g = _Intermitente(falhar_na_chamada=2)  # o título saiu; só a barra falhou
    _partida.ligar(executar=g)
    assert _partida.ligado()
    _limpo()


def test_progress_que_nunca_funcionou_continua_desistindo() -> None:
    _limpo()
    _partida.ligar(executar=_Gravador(retorno=1))
    assert not _partida.ligado()
    _limpo()


def _rodar_partida_antecipada(monkeypatch, argv: list[str], ambiente: str | None):
    """Roda `cli._partida_antecipada` com um `progress` falso; devolve os comandos."""
    from qobuz_dl import cli

    _limpo()
    g = _Gravador()
    monkeypatch.setattr(sys, "argv", ["qobuz-dl", *argv])
    if ambiente is None:
        monkeypatch.delenv("QOBUZ_DL_ATALHO", raising=False)
    else:
        monkeypatch.setenv("QOBUZ_DL_ATALHO", ambiente)
    ligar_real = _partida.ligar
    monkeypatch.setattr(_partida, "ligar", lambda *a, **k: ligar_real(executar=g))

    async def _rodar() -> None:
        cli._partida_antecipada()
        _partida.parar()

    asyncio.run(_rodar())
    _limpo()
    return g.comandos


def test_partida_antecipada_com_a_flag_mostra_a_leitura_do_config(
    monkeypatch,
) -> None:
    cmds = _rodar_partida_antecipada(monkeypatch, ["dl", "--atalho", "L"], None)
    assert any("lendo a configuração" in c for c in cmds)


def test_partida_antecipada_vale_para_o_ambiente_sem_a_flag(monkeypatch) -> None:
    cmds = _rodar_partida_antecipada(monkeypatch, ["dl", "L"], "1")
    assert any("lendo a configuração" in c for c in cmds)


def test_partida_antecipada_nao_mexe_em_comando_que_nao_baixa(monkeypatch) -> None:
    assert _rodar_partida_antecipada(monkeypatch, ["stats"], "1") == []


def test_partida_antecipada_desligada_nao_faz_nada(monkeypatch) -> None:
    assert _rodar_partida_antecipada(monkeypatch, ["dl", "L"], None) == []
