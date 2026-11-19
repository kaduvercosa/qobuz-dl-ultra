"""Modo Atalho do iOS (atalho.py): banner via `progress` e resumo em texto puro."""

from __future__ import annotations

import argparse
import asyncio
import json
from types import SimpleNamespace

from qobuz_dl import atalho, events, ui


class _Gravador:
    """Faz as vezes do `progress` do a-Shell: guarda cada comando recebido."""

    def __init__(self, retorno: int = 0) -> None:
        self.comandos: list[str] = []
        self.retorno = retorno

    def __call__(self, comando: str) -> int:
        self.comandos.append(comando)
        return self.retorno


def _relatorio(
    total: int, falhas: int = 0, titulo: str = "Disco", artista: str = "Artista"
) -> dict:
    return {
        "identificacao": {"titulo": titulo, "artista": artista},
        "progresso": {
            "resumo": {
                "total": total,
                "concluidas": total - falhas,
                "puladas": 0,
                "falhas": falhas,
                "pendentes": 0,
            }
        },
    }


def _novo(**kw):
    events.reiniciar()
    a = atalho.Atalho(**kw)
    events.assinar(a.ao_evento)
    return a


# --- ligar ------------------------------------------------------------------
def test_ligado_por_flag_ou_ambiente() -> None:
    assert atalho.ligado(SimpleNamespace(atalho=True), {})
    assert atalho.ligado(SimpleNamespace(atalho=False), {"QOBUZ_DL_ATALHO": "1"})
    assert atalho.ligado(SimpleNamespace(), {"QOBUZ_DL_ATALHO": "sim"})
    assert not atalho.ligado(SimpleNamespace(atalho=False), {"QOBUZ_DL_ATALHO": "0"})
    assert not atalho.ligado(SimpleNamespace(), {})


def test_ambiente_nao_deixa_comando_que_nao_baixa_mudo() -> None:
    amb = {"QOBUZ_DL_ATALHO": "1"}
    assert atalho.aplicavel(SimpleNamespace(command="dl"), amb)
    assert not atalho.aplicavel(SimpleNamespace(command="stats"), amb)
    assert not atalho.aplicavel(SimpleNamespace(command="dl"), {})


def test_flags_registradas_no_parser() -> None:
    parser = argparse.ArgumentParser()
    atalho.adicionar_args(parser)
    assert parser.parse_args(["--atalho"]).atalho is True
    assert parser.parse_args([]).atalho is False
    assert parser.parse_args(["--atalho", "--atalho-json"]).atalho_json is True
    assert parser.parse_args([]).atalho_json is False


def test_links_de_faixa_dao_total_de_faixas_na_preparacao() -> None:
    args = SimpleNamespace(
        command="dl",
        atalho=True,
        SOURCE=["https://open.qobuz.com/track/1", "https://open.qobuz.com/track/2"],
    )

    async def roda() -> None:
        sessao = atalho.preparar(args, "9.9", {})
        assert sessao is not None
        assert sessao.faixas_esperadas == 2
        assert sessao.esperados == 2
        await atalho.encerrar(sessao, True)

    with ui.estado_temporario(quiet=True):
        asyncio.run(roda())


def test_preparar_desligado_nao_custa_nada() -> None:
    assert atalho.preparar(SimpleNamespace(command="dl", atalho=False), "x", {}) is None
    assert ui.estado.espelho is None


# --- links esperados --------------------------------------------------------
def test_esperados_so_quando_todos_os_links_sao_conhecidos() -> None:
    album = "https://play.qobuz.com/album/abc"
    faixa = "https://open.qobuz.com/track/123"
    assert atalho.esperados_de_fontes([album, faixa]) == 2
    assert atalho.esperados_de_fontes([album, "https://x/artist/1"]) == 0
    assert atalho.esperados_de_fontes(["lista.txt"]) == 0
    assert atalho.esperados_de_fontes([]) == 0
    assert atalho.esperados_de_fontes(None) == 0


def test_faixas_esperadas_so_quando_todos_os_links_sao_de_faixa() -> None:
    faixa = "https://open.qobuz.com/track/1"
    album = "https://play.qobuz.com/album/abc"
    assert atalho.faixas_esperadas_de_fontes([faixa, faixa + "0"]) == 2
    assert atalho.faixas_esperadas_de_fontes([faixa, album]) == 0
    assert atalho.faixas_esperadas_de_fontes([]) == 0
    assert atalho.faixas_esperadas_de_fontes(None) == 0


# --- texto ------------------------------------------------------------------
def test_limpar_linha_tira_cor_moldura_e_tag() -> None:
    colorida = "\x1b[32m  ├─ Baixando: Faixa 1\x1b[0m"
    assert atalho.limpar_linha(colorida) == "Baixando: Faixa 1"
    assert atalho.limpar_linha("[ok] Pronto") == "Pronto"
    assert atalho.limpar_linha("─────────") == ""
    assert atalho.limpar_linha("\n\n") == ""
    assert atalho.limpar_linha("linha 1\nÚltima linha boa\n  ") == "Última linha boa"


def test_limpar_linha_corta_texto_longo() -> None:
    saida = atalho.limpar_linha("x" * 200, limite=20)
    assert len(saida) == 20
    assert saida.endswith("…")


# --- banner -----------------------------------------------------------------
def test_total_soma_lancamentos_e_feito_nunca_volta() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/[IN PROGRESS] A", "Album", "A", "X", "1", 3)
    events.faixa("/m/[IN PROGRESS] A", 1, "t1", "Um", "X", "concluido")
    events.faixa("/m/[IN PROGRESS] A", 2, "t2", "Dois", "X", "pulada")
    assert a._totais() == (2, 3)
    events.lancamento_iniciado("/m/B", "Album", "B", "Y", "2", 4)
    assert a._totais() == (2, 7)  # o total cresce com o segundo álbum
    events.faixa("/m/B", 1, "u1", "Um", "Y", "concluido")
    assert a._totais() == (3, 7)
    events.desassinar(a.ao_evento)


def test_faixa_repetida_nao_conta_duas_vezes() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    for _ in range(3):
        events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    assert a._totais() == (1, 2)
    events.desassinar(a.ao_evento)


def test_album_encerrado_fecha_a_barra_mesmo_sem_evento_de_todas_as_faixas() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 5)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    assert a._totais() == (1, 5)
    events.lancamento_concluido("/m/A", True, _relatorio(5))
    assert a._totais() == (5, 5)
    events.desassinar(a.ao_evento)


def test_playlist_anunciada_antes_ja_tem_o_total_certo() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/Playlist/P", "playlist", "P", "", "7", 50)
    assert a._totais() == (0, 50)
    events.faixa("/m/Playlist/P", 1, "t1", "Um", "", "concluido")
    # o evento tardio do postprocess (faixas=1) não encolhe nem repete o total
    events.lancamento_iniciado("/m/Playlist/P", "playlist", "P", "", "7", 1)
    assert a._totais() == (1, 50)
    events.desassinar(a.ao_evento)


def test_faixas_esperadas_dao_o_total_desde_o_inicio() -> None:
    a = _novo(executar=_Gravador(), faixas_esperadas=4)
    assert a._totais() == (0, 4)
    events.lancamento_iniciado("/m/A", "faixa", "Um", "X", "1", 1)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    assert a._totais() == (1, 4)
    assert a.titulo().startswith("[ FAIXA ] 2/4")
    events.desassinar(a.ao_evento)


def _relogio_manual(inicio: float = 100.0):
    agora = [inicio]

    def relogio() -> float:
        return agora[0]

    return agora, relogio


def test_titulo_gira_entre_andamento_etapa_e_nome() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), esperados=3, relogio=relogio)
    assert a.titulo() == "Qobuz · lendo o link · 0 s"
    events.lancamento_iniciado("/m/A", "Album", "Disco", "Artista", "1", 12)
    events.faixa("/m/A", 1, "t1", "Um", "Artista", "concluido")
    a.espelhar("  ├─ Gravando tags e capa")
    assert a.titulo() == "[ ÁLBUM ] 1/3 · faixa 2/12"
    agora[0] += atalho.ROTACAO_S
    assert a.titulo() == "Gravando tags e capa"
    agora[0] += atalho.ROTACAO_S
    assert a.titulo() == "Artista — Disco"
    agora[0] += atalho.ROTACAO_S
    assert a.titulo() == "[ ÁLBUM ] 1/3 · faixa 2/12"  # volta ao início
    events.desassinar(a.ao_evento)


def test_titulo_sem_total_quando_o_numero_de_albuns_e_desconhecido() -> None:
    a = _novo(executar=_Gravador(), esperados=0)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 5)
    assert a.titulo() == "[ ÁLBUM ] 1 · faixa 1/5"
    events.desassinar(a.ao_evento)


def test_titulo_de_playlist_e_faixa_avulsa() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/P", "playlist", "P", "", "1", 8)
    assert a.titulo() == "[ PLAYLIST ] 1 · faixa 1/8"
    events.lancamento_iniciado("/m/F", "faixa", "F", "X", "2", 1)
    assert a.titulo() == "[ FAIXA ] 2"
    events.desassinar(a.ao_evento)


def test_falha_para_o_rodizio_e_depois_o_banner_volta() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 4)
    events.faixa("/m/A", 3, "t3", "Três", "X", "falha", "erro")
    assert a.titulo() == "⚠ Faixa 3 falhou · segue"
    agora[0] += atalho.ROTACAO_S + 0.1  # o rodízio não mexe no aviso
    assert a.titulo() == "⚠ Faixa 3 falhou · segue"
    agora[0] += atalho.AVISO_S
    assert not a.titulo().startswith("⚠")
    events.desassinar(a.ao_evento)


def test_album_incompleto_avisa_no_banner() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    events.lancamento_concluido("/m/A", False, _relatorio(2, falhas=1))
    assert a.titulo() == "⚠ Álbum incompleto"
    events.desassinar(a.ao_evento)


def test_titulo_nunca_passa_do_limite_do_banner() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio)
    events.lancamento_iniciado("/m/A", "Album", "A" * 90, "B" * 90, "1", 4)
    a.espelhar("y" * 300)
    for _ in range(4):
        assert len(a.titulo()) <= atalho.TITULO_MAX
        agora[0] += atalho.ROTACAO_S
    events.desassinar(a.ao_evento)


def test_envia_so_o_que_mudou() -> None:
    agora, relogio = _relogio_manual()
    gravador = _Gravador()
    a = _novo(executar=gravador, relogio=relogio)
    a._enviar(forcar=True)
    assert gravador.comandos == [
        "progress --title 'Qobuz · lendo o link · 0 s'",
        "progress --indeterminate",
    ]
    gravador.comandos.clear()
    a._enviar()
    assert gravador.comandos == []  # nada mudou
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    a._enviar()
    assert gravador.comandos == [
        "progress --title '[ ÁLBUM ] 1 · faixa 1/2'",
        "progress 0/2",
    ]
    gravador.comandos.clear()
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    a._enviar()
    assert gravador.comandos[-1] == "progress 1/2"
    events.desassinar(a.ao_evento)


def test_antes_do_primeiro_lancamento_o_titulo_conta_os_segundos() -> None:
    agora, relogio = _relogio_manual()
    a = _novo(executar=_Gravador(), relogio=relogio)
    assert a.titulo() == "Qobuz · lendo o link · 0 s"
    agora[0] += 1.9
    assert a.titulo() == "Qobuz · lendo o link · 0 s"  # só muda de 2 em 2
    agora[0] += 0.2
    assert a.titulo() == "Qobuz · lendo o link · 2 s"
    agora[0] += 11
    assert a.titulo() == "Qobuz · lendo o link · 12 s"
    events.desassinar(a.ao_evento)


def test_segundos_da_partida_continuam_de_onde_o_processo_comecou() -> None:
    agora, relogio = _relogio_manual(200.0)
    events.reiniciar()
    a = atalho.Atalho(executar=_Gravador(), relogio=relogio, inicio_processo=187.0)
    assert a.titulo() == "Qobuz · lendo o link · 12 s"  # 13 s desde o início
    events.desassinar(a.ao_evento)


def test_o_titulo_muda_sozinho_enquanto_espera_o_primeiro_lancamento() -> None:
    agora, relogio = _relogio_manual()
    gravador = _Gravador()
    a = _novo(executar=gravador, relogio=relogio)
    a._enviar(forcar=True)
    gravador.comandos.clear()
    agora[0] += 2
    a._enviar()
    assert gravador.comandos == ["progress --title 'Qobuz · lendo o link · 2 s'"]
    events.desassinar(a.ao_evento)


def test_aspas_no_titulo_nao_quebram_o_comando() -> None:
    import shlex

    gravador = _Gravador()
    a = _novo(executar=gravador)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    a._aviso, a._aviso_ate = '⚠ It\'s "Live"; rm -rf /', 1e12
    a._enviar()
    partes = shlex.split(gravador.comandos[0])
    assert partes[:2] == ["progress", "--title"]
    assert len(partes) == 3  # o título inteiro virou UM argumento
    events.desassinar(a.ao_evento)


def test_sem_comando_progress_o_banner_se_desliga_sem_erro() -> None:
    gravador = _Gravador(retorno=127)  # "command not found"
    a = _novo(executar=gravador)
    a._enviar(forcar=True)
    assert len(gravador.comandos) == 1  # tentou uma vez e desistiu
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 2)
    a._enviar()
    assert len(gravador.comandos) == 1
    events.desassinar(a.ao_evento)


def test_executar_que_levanta_erro_nao_derruba_o_download() -> None:
    def explode(_c: str) -> int:
        raise OSError("sem ios_system")

    a = _novo(executar=explode)
    a._enviar(forcar=True)  # não pode levantar
    events.desassinar(a.ao_evento)


def test_fora_do_ios_nao_chama_progress() -> None:
    import sys

    a = atalho.Atalho()
    if sys.platform != "ios":
        assert a._executar is None


# --- espelho do terminal ----------------------------------------------------
def test_ui_repassa_cada_mensagem_ao_espelho_mesmo_em_quiet() -> None:
    vistos: list[str] = []
    with ui.estado_temporario(quiet=True, espelho=lambda t: vistos.append(str(t))):
        ui.emit("  ├─ Baixando: Faixa 1")
        ui.emit_always("aviso")
    assert vistos == ["  ├─ Baixando: Faixa 1", "aviso"]
    assert ui.estado.espelho is None


def test_espelho_com_defeito_se_desliga_sozinho() -> None:
    def ruim(_t: object) -> None:
        raise RuntimeError("boom")

    with ui.estado_temporario(quiet=True, espelho=ruim):
        ui.emit("a")
        assert ui.estado.espelho is None
        ui.emit("b")  # segue funcionando


def test_estado_com_espelho_continua_copiavel() -> None:
    import dataclasses

    a = atalho.Atalho(executar=_Gravador())
    a.iniciar()
    try:
        dataclasses.asdict(ui.estado)  # o que estado_temporario faz
    finally:
        a.parar()
    assert ui.estado.espelho is None


# --- resumo -----------------------------------------------------------------
def _sessao_mista(com_json: bool = False):
    """1 álbum ok, 1 playlist com 2 falhas e 1 pulada, 3 faixas avulsas."""
    a = _novo(executar=_Gravador(), com_json=com_json)
    events.lancamento_iniciado("/m/A", "Album", "Disco A", "Art A", "1", 2)
    events.faixa("/m/A", 1, "a1", "Um", "Art A", "concluido")
    events.faixa("/m/A", 2, "a2", "Dois", "Art A", "concluido")
    events.lancamento_concluido(
        "/m/A", True, _relatorio(2, titulo="Disco A", artista="Art A")
    )
    pasta_pl = "/m/Playlist/P"
    events.lancamento_iniciado(pasta_pl, "playlist", "Minha Playlist", "", "9", 5)
    events.faixa(pasta_pl, 1, "p1", "Um", "", "concluido")
    events.faixa(pasta_pl, 2, "p2", "Dois", "", "pulada")
    events.faixa(pasta_pl, 3, "p3", "Três", "", "falha", "erro")
    events.faixa(pasta_pl, 4, "p4", "Quatro", "", "falha", "erro")
    events.faixa(pasta_pl, 5, "p5", "Cinco", "", "concluido")
    rel = _relatorio(5, falhas=2, titulo="Minha Playlist", artista="")
    rel["progresso"]["resumo"]["puladas"] = 1
    rel["tipo"] = "playlist"
    events.lancamento_concluido(pasta_pl, False, rel)
    for n in (1, 2, 3):
        pasta = f"/m/Single {n}"
        events.lancamento_iniciado(pasta, "faixa", f"S{n}", "Art", str(n), 1)
        events.faixa(pasta, 1, f"s{n}", f"S{n}", "Art", "concluido")
    return a


def test_resumo_comeca_pelo_status_e_agrupa_por_tipo() -> None:
    a = _sessao_mista()
    texto, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    linhas = texto.splitlines()
    assert linhas[0].startswith("Concluído com 2 falhas")
    assert linhas[1].endswith("novas · 1 pulada · 2 falhas")
    assert (
        linhas.index("[ ÁLBUM ]")
        < linhas.index("[ PLAYLIST ]")
        < linhas.index("[ FAIXAS AVULSAS ]")
    )
    assert "✓ Art A — Disco A" in linhas
    assert any(linha.startswith("  2 faixas") for linha in linhas)
    assert "⚠ Minha Playlist" in linhas
    assert "    falhou: 03 Três" in linhas
    assert "    falhou: 04 Quatro" in linhas
    assert "✓ 3 faixas" in linhas
    assert "\x1b" not in texto  # texto puro
    assert dados["falhas"] == 2
    assert dados["ok"] is False


def test_resumo_sem_json_por_padrao_e_com_json_quando_pedido() -> None:
    a = _sessao_mista(com_json=False)
    texto, _ = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert "{" not in texto

    b = _sessao_mista(com_json=True)
    texto, dados = b.resumo(ok=True)
    events.desassinar(b.ao_evento)
    assert json.loads(texto.splitlines()[-1]) == dados
    assert set(dados) == {"ok", "albuns", "novas", "falhas"}


def test_resumo_plural_dos_titulos_dos_blocos() -> None:
    a = _novo(executar=_Gravador())
    for n in (1, 2):
        pasta = f"/m/A{n}"
        events.lancamento_iniciado(pasta, "Album", f"D{n}", "X", str(n), 1)
        events.faixa(pasta, 1, f"t{n}", "Um", "X", "concluido")
        events.lancamento_concluido(pasta, True, _relatorio(1, titulo=f"D{n}"))
    texto, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert "[ ÁLBUNS ]" in texto.splitlines()
    assert texto.splitlines()[0].startswith("Concluído · 2 faixas")
    assert dados["ok"] is True


def test_etiqueta_poe_o_tipo_entre_colchetes_em_maiusculas() -> None:
    assert atalho.etiqueta("Álbum") == "[ ÁLBUM ]"
    assert atalho.etiqueta("playlist") == "[ PLAYLIST ]"
    assert atalho.etiqueta("Faixas  avulsas") == "[ FAIXAS AVULSAS ]"


def test_resumo_de_uma_faixa_avulsa_usa_o_tipo_no_singular() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/S", "faixa", "S", "Art", "1", 1)
    events.faixa("/m/S", 1, "s1", "S", "Art", "concluido")
    texto, _ = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    linhas = texto.splitlines()
    assert "[ FAIXA ]" in linhas
    assert "[ FAIXAS AVULSAS ]" not in linhas
    assert "✓ 1 faixa" in linhas


def test_resumo_alinha_detalhes_sob_o_nome_e_separa_blocos_por_linha_vazia() -> None:
    a = _sessao_mista()
    texto, _ = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    linhas = texto.splitlines()
    for rotulo in ("[ ÁLBUM ]", "[ PLAYLIST ]", "[ FAIXAS AVULSAS ]"):
        i = linhas.index(rotulo)
        assert linhas[i - 1] == ""  # linha vazia antes de cada bloco
        assert linhas[i + 1][:2] in (atalho.MARCA_OK + " ", atalho.MARCA_ATENCAO + " ")
    assert "\n\n[ ÁLBUM ]\n" in texto


def test_resumo_ok_quando_tudo_deu_certo() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    events.lancamento_concluido("/m/A", True, _relatorio(1))
    _, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert dados["ok"] is True


def test_resumo_sem_albuns_nao_e_sucesso() -> None:
    a = _novo(executar=_Gravador())
    texto, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert "Nenhum álbum foi processado." in texto
    assert dados == {"ok": False, "albuns": 0, "novas": 0, "falhas": 0}


def test_resumo_de_album_que_nao_terminou() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 5)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    texto, dados = a.resumo(ok=False, interrompida=True)
    events.desassinar(a.ao_evento)
    assert texto.splitlines()[0].startswith("Interrompido")
    assert "⚠ X — A" in texto
    assert "não terminou" in texto
    assert dados["ok"] is False


def test_lista_de_falhas_e_limitada() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 9)
    for n in range(1, 9):
        events.faixa("/m/A", n, f"t{n}", f"F{n}", "X", "falha", "erro")
    texto, _ = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert texto.count("falhou:") == atalho.FALHAS_POR_ALBUM
    assert "… e mais 3" in texto


def test_nome_longo_nao_quebra_o_resumo() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "T" * 200, "A" * 200, "1", 1)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    events.lancamento_concluido("/m/A", True, _relatorio(1, titulo="T" * 200))
    texto, _ = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert all(len(linha) <= atalho.NOME_MAX + 8 for linha in texto.splitlines())


# --- ciclo completo ---------------------------------------------------------
def test_simulacao_completa_gera_banner_e_resumo() -> None:
    gravador = _Gravador()
    a = asyncio.run(atalho.simular(pausa=0.01, executar=gravador, emitir_resumo=False))
    comandos = gravador.comandos
    assert comandos[0].startswith("progress --title 'Qobuz · lendo o link")
    assert "progress 13/13" in comandos
    # nunca anda para trás
    fracoes = [
        int(c.split()[1].split("/")[0])
        for c in comandos
        if c.startswith("progress ") and "/" in c and "--" not in c
    ]
    assert fracoes == sorted(fracoes)
    texto, dados = a.resumo(ok=False)
    assert dados == {"ok": False, "albuns": 4, "novas": 12, "falhas": 1}
    linhas = texto.splitlines()
    assert linhas.index("[ ÁLBUM ]") < linhas.index("[ PLAYLIST ]")
    assert "[ FAIXAS AVULSAS ]" in linhas
    assert ui.estado.espelho is None


def test_simulacao_com_json_acrescenta_a_linha_de_dados() -> None:
    a = asyncio.run(
        atalho.simular(
            pausa=0.01, executar=_Gravador(), emitir_resumo=False, com_json=True
        )
    )
    texto, dados = a.resumo(ok=False)
    assert json.loads(texto.splitlines()[-1]) == dados


def test_preparar_e_encerrar_abrem_e_fecham_a_sessao() -> None:
    gravador = _Gravador()
    args = SimpleNamespace(
        command="dl",
        atalho=True,
        atalho_json=True,
        SOURCE=["https://x/album/1"],
    )

    async def roda() -> None:
        sessao = atalho.preparar(args, "9.9", {})
        assert sessao is not None
        assert sessao.esperados == 1
        assert sessao.faixas_esperadas == 0  # link de álbum: faixas desconhecidas
        assert sessao.com_json is True
        sessao._executar = gravador
        assert sessao.emite_sessao is True
        assert ui.estado.espelho is not None
        events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
        events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
        events.lancamento_concluido("/m/A", True, _relatorio(1))
        await atalho.encerrar(sessao, True)
        assert ui.estado.espelho is None

    with ui.estado_temporario(quiet=True):
        asyncio.run(roda())
    assert gravador.comandos[-1] == "progress --done"


def test_com_hooks_ativos_o_atalho_nao_reinicia_os_eventos() -> None:
    events.reiniciar()
    marcador: list[dict] = []
    events.assinar(marcador.append)
    args = SimpleNamespace(command="dl", atalho=True, SOURCE=[])

    async def roda() -> None:
        sessao = atalho.preparar(args, "9.9", {}, automacao_ativa=True)
        assert sessao is not None
        assert sessao.emite_sessao is False
        events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)  # chega aos dois
        await atalho.encerrar(sessao, True)

    with ui.estado_temporario(quiet=True):
        asyncio.run(roda())
    events.desassinar(marcador.append)
    assert any(e["evento"] == "lancamento_iniciado" for e in marcador)


def test_falha_passageira_do_progress_repete_o_envio_no_proximo_tique() -> None:
    chamadas: list[str] = []

    def executar(comando: str) -> int:
        chamadas.append(comando)
        return 1 if len(chamadas) == 3 else 0  # a 3ª falha; o resto funciona

    a = _novo(executar=executar, rotacao=0)
    a._enviar(forcar=True)  # título + barra indeterminada (chamadas 1 e 2)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 4)
    a._enviar()  # título novo: chamada 3 falha
    antes = len(chamadas)
    a._enviar()  # mesmo título: tem de tentar de novo
    events.desassinar(a.ao_evento)
    assert len(chamadas) > antes
    assert a._executar is not None  # não se desligou


def test_progress_que_nunca_funcionou_desliga_o_banner() -> None:
    a = _novo(executar=lambda c: 1)
    a._enviar(forcar=True)
    events.desassinar(a.ao_evento)
    assert a._executar is None


def test_com_fila_o_titulo_antes_do_1o_lancamento_alterna_fila_e_etapa() -> None:
    relogio = [0.0]
    a = _novo(executar=_Gravador(), relogio=lambda: relogio[0], rotacao=3.0)
    a.definir_link(1, 1, 0)
    assert a.titulo() == "Fila · link 1/1"
    relogio[0] = 3.5
    assert a.titulo().startswith("Qobuz · lendo o link · ")
    relogio[0] = 6.5
    assert a.titulo() == "Fila · link 1/1"
    events.desassinar(a.ao_evento)


def test_resumo_com_travamento_vira_interrompido_e_avisa() -> None:
    a = _novo(executar=_Gravador())
    events.lancamento_iniciado("/m/A", "Album", "Disco", "Art", "1", 4)
    events.faixa("/m/A", 1, "a1", "Um", "Art", "concluido")
    a.travamento = "Travou: sem sinal de vida há 3 min 05 s; cancelado"
    texto, dados = a.resumo(ok=False)
    events.desassinar(a.ao_evento)
    linhas = texto.splitlines()
    assert linhas[0].startswith("Interrompido")
    assert "⚠ Travou: sem sinal de vida há 3 min 05 s; cancelado" in linhas
    assert dados["ok"] is False


def test_ligar_vigia_cancela_a_tarefa_e_pede_aborto_ao_travar() -> None:
    import asyncio
    import sys
    import threading
    import types

    falso = types.ModuleType("qobuz_dl.downloader")
    falso.abort_event = threading.Event()  # type: ignore[attr-defined]
    anterior = sys.modules.get("qobuz_dl.downloader")
    sys.modules["qobuz_dl.downloader"] = falso

    async def cena() -> tuple[str, str]:
        a = _novo(executar=_Gravador())
        atalho._ligar_vigia(a, {"QOBUZ_DL_ATALHO_LIMITE_S": "60"})
        assert a.vigia is not None
        a.vigia.parar()  # sem thread no teste: chamamos o callback à mão
        a.vigia.ao_travar(200.0)
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            return "cancelado", a.travamento
        return "seguiu", a.travamento

    try:
        estado, aviso = asyncio.run(cena())
    finally:
        if anterior is None:
            sys.modules.pop("qobuz_dl.downloader", None)
        else:
            sys.modules["qobuz_dl.downloader"] = anterior
    assert estado == "cancelado"
    assert aviso == "Travou: sem sinal de vida há 3 min 20 s; cancelado"
    assert falso.abort_event.is_set()  # type: ignore[attr-defined]


def test_ligar_vigia_desligado_com_limite_zero() -> None:
    a = _novo(executar=_Gravador())
    atalho._ligar_vigia(a, {"QOBUZ_DL_ATALHO_LIMITE_S": "0"})
    assert a.vigia is None


def test_espelhar_conta_como_sinal_de_vida() -> None:
    from qobuz_dl import vigia

    a = _novo(executar=_Gravador())
    vigia._pulso[0] = 0.0
    a.espelhar("Em Progresso: faixa")
    assert vigia.segundos_parado() < 5.0


def test_enviar_publica_o_andamento_com_o_nome_do_album_mesmo_sem_progress() -> None:
    publicados: list[tuple[str, str]] = []
    original = atalho.publicar_andamento
    atalho.publicar_andamento = lambda titulo, nome="": publicados.append(
        (titulo, nome)
    )
    try:
        a = _novo(executar=_Gravador())
        a._executar = None  # fora do iOS: sem banner, mas o andamento vale igual
        events.lancamento_iniciado("/m/A", "Album", "Chromatica", "Lady Gaga", "1", 12)
        a._enviar(forcar=True)
        events.desassinar(a.ao_evento)
    finally:
        atalho.publicar_andamento = original
    titulo, nome = publicados[-1]
    assert nome == "Lady Gaga — Chromatica"
    assert titulo.startswith("[ ÁLBUM ] 1")


def test_finalizar_apaga_o_andamento() -> None:
    chamadas: list[str] = []
    original = atalho.encerrar_andamento
    atalho.encerrar_andamento = lambda: chamadas.append("limpou")
    try:
        a = _novo(executar=_Gravador())
        events.lancamento_iniciado("/m/A", "Album", "D", "A", "1", 1)
        events.faixa("/m/A", 1, "a1", "F", "A", "concluido")
        a.finalizar(True)
        events.desassinar(a.ao_evento)
    finally:
        atalho.encerrar_andamento = original
    assert chamadas == ["limpou"]
