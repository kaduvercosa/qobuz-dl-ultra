"""Fila de links do modo Atalho (fila.py) e o token `@fila` do `dl`."""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import tempfile
import time
from typing import Any

from qobuz_dl import atalho, events, fila, ui

ALBUM = "https://play.qobuz.com/album/abc123"
ALBUM_OPEN = "https://open.qobuz.com/album/abc123"
FAIXA = "https://open.qobuz.com/track/777"
PLAYLIST = "https://play.qobuz.com/playlist/55"


def _tmp():
    return tempfile.TemporaryDirectory()


# --- links ------------------------------------------------------------------
def test_chave_do_link_aceita_so_links_do_qobuz() -> None:
    assert fila.chave_do_link(ALBUM) == ("album", "abc123")
    assert fila.chave_do_link(ALBUM_OPEN) == ("album", "abc123")
    assert fila.chave_do_link("https://www.qobuz.com/br-pt/album/x-y/abc123") == (
        "album",
        "abc123",
    )
    assert fila.chave_do_link(FAIXA) == ("track", "777")
    assert fila.chave_do_link(PLAYLIST) == ("playlist", "55")
    assert fila.chave_do_link("/album/abc") is None  # sem https
    assert fila.chave_do_link("lista.txt") is None  # arquivo não entra
    assert fila.chave_do_link("https://exemplo.com/album/abc") is None
    assert fila.chave_do_link("") is None


def test_rotulo_e_link_curto() -> None:
    assert fila.rotulo_do_link(ALBUM) == "Álbum abc123"
    assert fila.rotulo_do_link(FAIXA) == "Faixa 777"
    assert fila.link_curto(ALBUM) == "play.qobuz.com/album/abc123"
    assert len(fila.link_curto("https://x/" + "a" * 100, 20)) == 20


# --- adicionar --------------------------------------------------------------
def test_adicionar_mantem_a_ordem_e_informa_a_posicao() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        a1, a2, a3 = f.adicionar(ALBUM), f.adicionar(FAIXA), f.adicionar(PLAYLIST)
        assert [a.resultado for a in (a1, a2, a3)] == ["novo"] * 3
        assert [a.posicao for a in (a1, a2, a3)] == [1, 2, 3]
        assert [i.link for i in f.pendentes()] == [ALBUM, FAIXA, PLAYLIST]


def test_adicionar_recusa_repetido_mesmo_com_outro_formato_de_link() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(FAIXA)
        f.adicionar(ALBUM)
        repetido = f.adicionar(ALBUM_OPEN)
        assert repetido.resultado == "repetido"
        assert repetido.posicao == 2
        assert f.contar_pendentes() == 2


def test_adicionar_recusa_link_invalido_sem_criar_arquivo() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        assert f.adicionar("qualquer coisa").resultado == "invalido"
        assert f.contar_pendentes() == 0
        assert not os.path.exists(os.path.join(d, ".tmp-x"))


def test_link_em_andamento_nao_entra_de_novo() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        item = f.reivindicar()
        assert item is not None
        assert f.adicionar(ALBUM).resultado == "andamento"


def test_link_que_falhou_pode_ser_adicionado_de_novo() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        item = f.reivindicar()
        assert item is not None
        f.falhar(item, "erro")
        assert f.adicionar(ALBUM).resultado == "novo"


def test_adicionar_nao_deixa_arquivo_temporario() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        assert not [n for n in os.listdir(d) if n.startswith(".tmp")]


# --- consumir arquivos ------------------------------------------------------
def test_reivindicar_segue_a_ordem_e_marca_andamento() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        f.adicionar(FAIXA)
        primeiro = f.reivindicar()
        assert primeiro is not None and primeiro.link == ALBUM
        assert primeiro.estado == "andamento"
        assert primeiro.nome.endswith(fila.SUFIXO_ANDAMENTO)
        assert [i.link for i in f.pendentes()] == [FAIXA]
        assert [i.link for i in f.em_andamento()] == [ALBUM]
        f.concluir(primeiro)
        assert f.em_andamento() == []
        segundo = f.reivindicar()
        assert segundo is not None and segundo.link == FAIXA
        assert f.reivindicar() is None


def test_falhar_guarda_o_motivo_e_repetir_devolve() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        item = f.reivindicar()
        assert item is not None
        f.falhar(item, "ValueError:  algo\ndeu errado")
        falhos = f.falhos()
        assert len(falhos) == 1
        assert falhos[0].link == ALBUM
        assert falhos[0].motivo == "ValueError: algo deu errado"
        assert f.pendentes() == []
        assert f.repetir() == 1
        assert [i.link for i in f.pendentes()] == [ALBUM]
        assert f.falhos() == []


def test_devolver_e_recuperar_andamento() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        f.adicionar(FAIXA)
        f.reivindicar()
        f.reivindicar()
        assert f.recuperar_andamento() == 2
        assert [i.link for i in f.pendentes()] == [ALBUM, FAIXA]


def test_limpar_pode_ser_parcial() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        f.adicionar(FAIXA)
        item = f.reivindicar()
        assert item is not None
        f.falhar(item, "x")
        assert f.limpar(pendentes=False, falhos=True) == 1
        assert f.contar_pendentes() == 1
        assert f.limpar() == 1
        assert f.contar_pendentes() == 0


def test_dois_consumidores_nao_pegam_o_mesmo_link() -> None:
    with _tmp() as d:
        a, b = fila.Fila(d), fila.Fila(d)
        a.adicionar(ALBUM)
        pegos = [x for x in (a.reivindicar(), b.reivindicar()) if x is not None]
        assert len(pegos) == 1


# --- está rodando? ----------------------------------------------------------
def test_marca_de_ativo_expira() -> None:
    agora = [time.time()]
    with _tmp() as d:
        f = fila.Fila(d, relogio=lambda: agora[0])
        assert not f.esta_ativo()
        f.marcar_ativo()
        assert f.esta_ativo()
        agora[0] += fila.ATIVO_VALIDADE_S + 5
        assert not f.esta_ativo()
        f.marcar_ativo()  # renovar
        agora[0] = time.time()
        assert f.esta_ativo()
        f.limpar_ativo()
        assert not f.esta_ativo()


def test_adicionar_diz_se_ha_download_rodando() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        assert f.adicionar(ALBUM).rodando is False
        f.marcar_ativo()
        assert f.adicionar(FAIXA).rodando is True


# --- consumir (o laço do `dl @fila`) ---------------------------------------
def _roda(coro: Any) -> Any:
    return asyncio.run(coro)


def test_consumir_baixa_os_links_dados_e_depois_a_fila() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(FAIXA)
        f.adicionar(PLAYLIST)
        baixados: list[str] = []
        avisos: list[tuple[int, int, int]] = []

        async def baixar(links: list[str]) -> None:
            baixados.extend(links)

        res = _roda(
            fila.consumir(
                baixar,
                [ALBUM],
                fila=f,
                ao_link=lambda i, t, e: avisos.append((i, t, e)),
            )
        )
        assert baixados == [ALBUM, FAIXA, PLAYLIST]
        assert avisos == [(1, 3, 2), (2, 3, 1), (3, 3, 0)]
        assert res.links == 3 and res.falharam == []
        assert f.pendentes() == [] and f.em_andamento() == []
        assert not f.esta_ativo()  # a marca some no fim


def test_link_adicionado_durante_o_download_entra_na_mesma_execucao() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        baixados: list[str] = []
        avisos: list[tuple[int, int, int]] = []

        async def baixar(links: list[str]) -> None:
            baixados.extend(links)
            if links == [ALBUM]:
                fila.Fila(d).adicionar(PLAYLIST)  # outro Atalho, durante o download

        res = _roda(
            fila.consumir(
                baixar, fila=f, ao_link=lambda i, t, e: avisos.append((i, t, e))
            )
        )
        assert baixados == [ALBUM, PLAYLIST]
        assert avisos == [(1, 1, 0), (2, 2, 0)]
        assert res.links == 2


def test_marca_de_ativo_fica_de_pe_durante_o_download() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        vistos: list[bool] = []

        async def baixar(_links: list[str]) -> None:
            vistos.append(fila.Fila(d).esta_ativo())

        _roda(fila.consumir(baixar, fila=f))
        assert vistos == [True]


def test_batimento_renova_a_marca() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        renovacoes: list[float] = []
        original = f.marcar_ativo

        def marcar() -> None:
            renovacoes.append(time.time())
            original()

        f.marcar_ativo = marcar  # type: ignore[method-assign]

        async def baixar(_links: list[str]) -> None:
            await asyncio.sleep(0.12)

        _roda(fila.consumir(baixar, fila=f, batimento=0.02))
        assert len(renovacoes) >= 3  # a inicial + batimentos


def test_erro_num_link_vai_para_falhou_e_a_fila_segue() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        f.adicionar(FAIXA)
        baixados: list[str] = []
        falhos: list[str] = []

        async def baixar(links: list[str]) -> None:
            baixados.extend(links)
            if links == [ALBUM]:
                raise RuntimeError("sem rede")

        res = _roda(fila.consumir(baixar, fila=f, ao_falhar=falhos.append))
        assert baixados == [ALBUM, FAIXA]
        assert res.falharam == [ALBUM] == falhos
        assert [i.link for i in f.falhos()] == [ALBUM]
        assert "sem rede" in f.falhos()[0].motivo
        assert f.pendentes() == []


def test_contador_de_problemas_marca_o_link_como_nao_baixou() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        f.adicionar(FAIXA)
        contador = [0]

        async def baixar(links: list[str]) -> None:
            if links == [ALBUM]:
                contador[0] += 1  # uma faixa falhou durante este link

        res = _roda(fila.consumir(baixar, fila=f, problemas=lambda: contador[0]))
        assert res.falharam == [ALBUM]
        assert [i.link for i in f.falhos()] == [ALBUM]
        assert f.pendentes() == []  # o segundo link não herdou o problema


def test_link_da_linha_de_comando_que_falha_tambem_pode_ser_repetido() -> None:
    with _tmp() as d:
        f = fila.Fila(d)

        async def baixar(_links: list[str]) -> None:
            raise RuntimeError("boom")

        res = _roda(fila.consumir(baixar, [ALBUM], fila=f))
        assert res.falharam == [ALBUM]
        assert [i.link for i in f.falhos()] == [ALBUM]
        assert f.repetir() == 1
        assert [i.link for i in f.pendentes()] == [ALBUM]


def test_interrupcao_devolve_o_link_a_fila() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)

        async def baixar(_links: list[str]) -> None:
            raise KeyboardInterrupt

        try:
            _roda(fila.consumir(baixar, fila=f))
        except KeyboardInterrupt:
            pass
        else:
            raise AssertionError("era para propagar a interrupção")
        assert [i.link for i in f.pendentes()] == [ALBUM]
        assert f.em_andamento() == [] and f.falhos() == []
        assert not f.esta_ativo()


def test_consumidor_ocupado_recusa_e_nao_apaga_a_marca() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.marcar_ativo()
        chamado = []

        async def baixar(links: list[str]) -> None:
            chamado.append(links)

        try:
            _roda(fila.consumir(baixar, [ALBUM], fila=f))
        except fila.FilaOcupada:
            pass
        else:
            raise AssertionError("era para recusar")
        assert chamado == []
        assert f.esta_ativo()  # a marca é do outro consumidor


def test_comando_morto_deixa_link_em_andamento_que_volta() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        assert f.reivindicar() is not None  # o comando anterior morreu aqui
        baixados: list[str] = []

        async def baixar(links: list[str]) -> None:
            baixados.extend(links)

        _roda(fila.consumir(baixar, fila=f))
        assert baixados == [ALBUM]


# --- linha de comando -------------------------------------------------------
def _cli(*args: str) -> tuple[int, str]:
    saida = io.StringIO()
    with contextlib.redirect_stdout(saida):
        codigo = fila.main(list(args))
    return codigo, saida.getvalue()


def test_cli_adicionar_sem_download_rodando() -> None:
    with _tmp() as d:
        codigo, texto = _cli("--dir", d, "adicionar", ALBUM)
        assert codigo == 0
        linhas = texto.splitlines()
        assert linhas[0] == "Na fila: posição 1"
        assert linhas[1] == "[ ÁLBUM ] abc123"
        assert linhas[2] == "Nenhum download rodando: inicie o Long Command."


def test_cli_adicionar_com_download_rodando() -> None:
    with _tmp() as d:
        fila.Fila(d).marcar_ativo()
        _, texto = _cli("--dir", d, "adicionar", FAIXA)
        assert "Há um download em andamento: este link entra sozinho." in texto
        assert "Nenhum download rodando" not in texto


def test_cli_repetido_e_invalido() -> None:
    with _tmp() as d:
        _cli("--dir", d, "adicionar", ALBUM)
        codigo, texto = _cli("--dir", d, "adicionar", ALBUM_OPEN)
        assert codigo == 0
        assert texto.startswith("Já está na fila (posição 1)")
        codigo, texto = _cli("--dir", d, "adicionar", "lixo")
        assert codigo == 2
        assert texto.startswith("Link não reconhecido")


def test_cli_varios_links_de_uma_vez() -> None:
    with _tmp() as d:
        codigo, texto = _cli("--dir", d, "adicionar", ALBUM, FAIXA)
        assert codigo == 0
        assert "posição 1" in texto and "posição 2" in texto


def test_cli_listar_repetir_e_limpar() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.adicionar(ALBUM)
        f.adicionar(FAIXA)
        item = f.reivindicar()
        assert item is not None
        f.falhar(item, "erro")
        _, texto = _cli("--dir", d, "listar")
        assert texto.splitlines()[0] == "Fila: 1 esperando · 1 falhou"
        assert "Esperando" in texto and "Falharam" in texto
        assert "[ FAIXA ] 777" in texto and "[ ÁLBUM ] abc123 (erro)" in texto
        _, texto = _cli("--dir", d, "repetir")
        assert texto.startswith("1 link(s) voltaram")
        _, texto = _cli("--dir", d, "limpar")
        assert texto.startswith("2 link(s) apagados")
        assert f.contar_pendentes() == 0


def test_listar_com_a_fila_vazia() -> None:
    with _tmp() as d:
        _, texto = _cli("--dir", d, "listar")
        assert texto.splitlines()[0] == "Fila: 0 esperando"
        assert "Nenhum download rodando." in texto


def test_diretorio_pode_vir_do_ambiente() -> None:
    antigo = os.environ.get("QOBUZ_DL_FILA_DIR")
    os.environ["QOBUZ_DL_FILA_DIR"] = "/tmp/xyz-fila"
    try:
        assert fila.diretorio_padrao() == "/tmp/xyz-fila"
    finally:
        if antigo is None:
            os.environ.pop("QOBUZ_DL_FILA_DIR", None)
        else:
            os.environ["QOBUZ_DL_FILA_DIR"] = antigo


# --- banner e resumo do modo atalho com a fila ------------------------------
def _atalho_novo(**kw: Any) -> atalho.Atalho:
    events.reiniciar()
    a = atalho.Atalho(executar=lambda _c: 0, **kw)
    events.assinar(a.ao_evento)
    return a


def test_banner_mostra_a_tela_da_fila() -> None:
    agora = [100.0]
    a = _atalho_novo(relogio=lambda: agora[0])
    a.definir_link(3, 5, 2)
    assert a.titulo() == "Fila: 2 esperando · link 3/5"  # ainda sem lançamento
    events.lancamento_iniciado("/m/A", "Album", "Disco", "Artista", "1", 12)
    telas = []
    for _ in range(4):
        telas.append(a.titulo())
        agora[0] += atalho.ROTACAO_S
    assert "Fila: 2 esperando · link 3/5" in telas
    assert telas[0] == "[ ÁLBUM ] 1 · faixa 1/12"  # sem "/N": a fila mistura tipos
    events.desassinar(a.ao_evento)


def test_tela_da_fila_sem_ninguem_esperando() -> None:
    a = _atalho_novo()
    a.definir_link(5, 5, 0)
    assert a.titulo() == "Fila · link 5/5"
    events.desassinar(a.ao_evento)


def test_esperados_deixa_de_valer_com_a_fila() -> None:
    a = _atalho_novo(esperados=3)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 4)
    assert a.titulo().startswith("[ ÁLBUM ] 1/3")
    a.definir_link(1, 3, 2)
    assert a.titulo().startswith("[ ÁLBUM ] 1 ·")
    events.desassinar(a.ao_evento)


def test_contador_de_problemas_so_sobe() -> None:
    a = _atalho_novo()
    assert a.problemas() == 0
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 3)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    assert a.problemas() == 0
    events.faixa("/m/A", 2, "t2", "Dois", "X", "falha", "erro")
    assert a.problemas() == 1
    events.lancamento_concluido("/m/A", False, {"progresso": {"resumo": {"total": 3}}})
    assert a.problemas() == 1  # incompleto com falha: não conta duas vezes
    events.lancamento_iniciado("/m/B", "Album", "B", "X", "2", 1)
    events.lancamento_concluido("/m/B", False, {})
    assert a.problemas() == 2  # incompleto sem falha conta 1
    events.desassinar(a.ao_evento)


def test_resumo_com_fila_conta_links_e_lista_os_que_falharam() -> None:
    a = _atalho_novo(com_json=True)
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    events.lancamento_concluido("/m/A", True, {"progresso": {"resumo": {"total": 1}}})
    a.definir_link(5, 5, 0)
    a.registrar_link_falho(ALBUM)
    a.registrar_link_falho(ALBUM)  # repetido não conta duas vezes
    texto, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    linhas = texto.splitlines()
    assert "Fila: 5 links · 1 não baixou" in linhas
    assert linhas.index("Fila: 5 links · 1 não baixou") == 2  # logo após os totais
    assert "[ LINKS QUE FALHARAM ]" in linhas
    assert "⚠ play.qobuz.com/album/abc123" in linhas
    assert '  volta à fila com "repetir"' in linhas
    assert dados["ok"] is False
    assert dados["fila"] == {"links": 5, "falharam": 1}


def test_resumo_da_fila_sem_falhas() -> None:
    a = _atalho_novo()
    events.lancamento_iniciado("/m/A", "Album", "A", "X", "1", 1)
    events.faixa("/m/A", 1, "t1", "Um", "X", "concluido")
    events.lancamento_concluido("/m/A", True, {"progresso": {"resumo": {"total": 1}}})
    a.definir_link(1, 1, 0)
    texto, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert "Fila: 1 link" in texto.splitlines()
    assert "LINKS QUE FALHARAM" not in texto
    assert dados["ok"] is True


def test_sem_fila_o_resumo_e_o_json_nao_mudam() -> None:
    a = _atalho_novo(com_json=True)
    _, dados = a.resumo(ok=True)
    events.desassinar(a.ao_evento)
    assert "fila" not in dados


def test_ativo_devolve_o_modo_em_andamento() -> None:
    assert atalho.ativo() is None
    a = atalho.Atalho(executar=lambda _c: 0)
    a.iniciar()
    try:
        assert atalho.ativo() is a
    finally:
        a.parar()
    assert atalho.ativo() is None
    assert ui.estado.espelho is None


# --- cola com o `dl` (cli.py) -----------------------------------------------
class _QobuzFalso:
    def __init__(self) -> None:
        self.chamadas: list[list[str]] = []

    async def download_list_of_urls(self, urls: list[str]) -> None:
        self.chamadas.append(list(urls))


def _com_fila_em(diretorio: str):
    class _Ambiente:
        def __enter__(self) -> None:
            self.antigo = os.environ.get("QOBUZ_DL_FILA_DIR")
            os.environ["QOBUZ_DL_FILA_DIR"] = diretorio

        def __exit__(self, *_a: object) -> None:
            if self.antigo is None:
                os.environ.pop("QOBUZ_DL_FILA_DIR", None)
            else:
                os.environ["QOBUZ_DL_FILA_DIR"] = self.antigo

    return _Ambiente()


def test_dl_sem_token_baixa_so_os_links_dados() -> None:
    from qobuz_dl import cli

    with _tmp() as d, _com_fila_em(d):
        fila.Fila(d).adicionar(FAIXA)  # na fila, mas ninguém pediu @fila
        qobuz = _QobuzFalso()
        _roda(cli._baixar_fontes(qobuz, [ALBUM, PLAYLIST]))
        # a lista chega inteira ao download, como antes
        assert qobuz.chamadas == [[ALBUM, PLAYLIST]]
        assert fila.Fila(d).contar_pendentes() == 1


def test_dl_com_token_baixa_os_links_e_depois_a_fila() -> None:
    from qobuz_dl import cli

    with _tmp() as d, _com_fila_em(d):
        fila.Fila(d).adicionar(FAIXA)
        qobuz = _QobuzFalso()
        _roda(cli._baixar_fontes(qobuz, [ALBUM, fila.TOKEN]))
        assert qobuz.chamadas == [[ALBUM], [FAIXA]]
        assert fila.Fila(d).contar_pendentes() == 0


def test_dl_so_com_token_esvazia_a_fila() -> None:
    from qobuz_dl import cli

    with _tmp() as d, _com_fila_em(d):
        fila.Fila(d).adicionar(FAIXA)
        qobuz = _QobuzFalso()
        _roda(cli._baixar_fontes(qobuz, [fila.TOKEN]))
        assert qobuz.chamadas == [[FAIXA]]


def test_dl_com_outro_consumidor_ativo_avisa_e_nao_baixa() -> None:
    from qobuz_dl import cli

    with _tmp() as d, _com_fila_em(d):
        fila.Fila(d).marcar_ativo()
        qobuz = _QobuzFalso()
        with ui.estado_temporario(quiet=True):
            _roda(cli._baixar_fontes(qobuz, [ALBUM, fila.TOKEN]))
        assert qobuz.chamadas == []


def test_dl_com_fila_alimenta_o_banner_do_modo_atalho() -> None:
    from qobuz_dl import cli

    with _tmp() as d, _com_fila_em(d):
        fila.Fila(d).adicionar(FAIXA)
        qobuz = _QobuzFalso()

        async def roda() -> atalho.Atalho:
            modo = atalho.Atalho(executar=lambda _c: 0)
            modo.iniciar()
            await cli._baixar_fontes(qobuz, [ALBUM, fila.TOKEN])
            modo.parar()
            return modo

        modo = _roda(roda())
        assert modo._fila_indice == 2 and modo._fila_total == 2
        assert modo.fila_ativa is True


# --- nomes legíveis (nome e artista no lugar do id) --------------------------
class _Resposta:
    def __init__(self, corpo: dict) -> None:
        import json

        self._corpo = json.dumps(corpo).encode("utf-8")

    def __enter__(self) -> "_Resposta":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self) -> bytes:
        return self._corpo


def _com_rede(corpo: Any = None, erro: bool = False):
    """Liga a consulta e devolve (abrir falso, pedidos feitos)."""
    pedidos: list[Any] = []

    def abrir(pedido: Any, timeout: float = 0) -> _Resposta:
        pedidos.append((pedido.full_url, dict(pedido.header_items()), timeout))
        if erro:
            raise OSError("sem rede")
        return _Resposta(corpo)

    return abrir, pedidos


def test_nome_da_resposta_de_album_faixa_e_playlist() -> None:
    album = {
        "title": "Esú",
        "version": "Deluxe",
        "artist": {"name": "Baco Exu do Blues"},
    }
    assert fila._nome_da_resposta("album", album) == "Baco Exu do Blues — Esú (Deluxe)"
    faixa = {"title": "Te Amo Disgraça", "performer": {"name": "Baco"}}
    assert fila._nome_da_resposta("track", faixa) == "Baco — Te Amo Disgraça"
    so_album = {"title": "X", "album": {"artist": {"name": "Y"}}}
    assert fila._nome_da_resposta("track", so_album) == "Y — X"
    assert fila._nome_da_resposta("playlist", {"name": "Treino"}) == "Treino"
    assert fila._nome_da_resposta("artist", {"name": "Daft Punk"}) == "Daft Punk"
    assert fila._nome_da_resposta("album", {"title": "Só título"}) == "Só título"
    assert fila._nome_da_resposta("album", None) == ""


def test_buscar_nome_consulta_a_api_com_o_app_id() -> None:
    os.environ["QOBUZ_DL_FILA_REDE"] = "1"
    os.environ["QOBUZ_DL_APP_ID"] = "123456789"
    try:
        abrir, pedidos = _com_rede({"title": "Esú", "artist": {"name": "Baco"}})
        assert fila.buscar_nome(ALBUM, 3.0, abrir=abrir) == "Baco — Esú"
        url, cabecalhos, timeout = pedidos[0]
        assert url.startswith("https://www.qobuz.com/api.json/0.2/album/get?")
        assert "album_id=abc123" in url and "app_id=123456789" in url
        assert cabecalhos.get("X-app-id") == "123456789"
        assert timeout == 3.0
    finally:
        os.environ["QOBUZ_DL_FILA_REDE"] = "0"
        os.environ.pop("QOBUZ_DL_APP_ID", None)


def test_buscar_nome_devolve_none_sem_rede_ou_com_a_rede_desligada() -> None:
    os.environ["QOBUZ_DL_FILA_REDE"] = "1"
    try:
        abrir, _ = _com_rede(erro=True)
        assert fila.buscar_nome(ALBUM, 3.0, abrir=abrir) is None
        assert fila.buscar_nome("lixo", 3.0, abrir=abrir) is None
    finally:
        os.environ["QOBUZ_DL_FILA_REDE"] = "0"
    abrir, pedidos = _com_rede({"title": "X"})
    assert fila.buscar_nome(ALBUM, 3.0, abrir=abrir) is None  # rede desligada
    assert pedidos == []


def test_nome_do_slug_e_rotulo_com_nome() -> None:
    link = "https://www.qobuz.com/br-pt/album/esu-baixo-eru-de-blues/0886446218915"
    assert fila.nome_do_slug(link) == "Esu Baixo Eru De Blues"
    assert fila.nome_do_slug(ALBUM) == ""  # sem trecho no link
    assert fila.rotulo_com_nome(ALBUM, "Baco — Esú") == "[ ÁLBUM ] Baco — Esú"
    assert fila.rotulo_com_nome(FAIXA) == "[ FAIXA ] 777"
    assert fila.rotulo_com_nome("lixo") == "lixo"


def test_nome_do_link_usa_o_cache_e_so_consulta_uma_vez() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        chamadas: list[str] = []

        def buscar(link: str, timeout: float) -> str | None:
            chamadas.append(link)
            return "Baco — Esú"

        assert f.nome_do_link(ALBUM, buscar=buscar) == "Baco — Esú"
        assert f.nome_do_link(ALBUM_OPEN, buscar=buscar) == "Baco — Esú"  # mesmo id
        assert len(chamadas) == 1
        assert f.nomes() == {"album:abc123": "Baco — Esú"}


def test_nome_do_link_sem_rede_cai_no_slug_e_nao_guarda() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        link = "https://www.qobuz.com/br-pt/album/esu-baixo/0886"
        assert f.nome_do_link(link, buscar=lambda *_: None) == "Esu Baixo"
        assert f.nomes() == {}  # só o nome de verdade vai para o cache
        assert f.nome_do_link(ALBUM, rede=False) == ""


def test_cli_adicionar_e_listar_mostram_nome_e_artista() -> None:
    original = fila.buscar_nome
    fila.buscar_nome = lambda link, timeout=0, abrir=None: {
        "abc123": "Baco — Esú",
        "777": "Daft Punk — Get Lucky",
    }[fila.chave_do_link(link)[1]]
    try:
        with _tmp() as d:
            _, texto = _cli("--dir", d, "adicionar", ALBUM)
            assert texto.splitlines()[1] == "[ ÁLBUM ] Baco — Esú"
            fila.Fila(d).adicionar(FAIXA)
            _, texto = _cli("--dir", d, "listar")
            assert "1. [ ÁLBUM ] Baco — Esú" in texto
            assert "2. [ FAIXA ] Daft Punk — Get Lucky" in texto
    finally:
        fila.buscar_nome = original


class _ErroHttp:
    """`abrir` falso: responde 400 com a mensagem do Qobuz (e conta as chamadas)."""

    def __init__(self, ok_sem_token: bool = False) -> None:
        self.cabecalhos: list[dict] = []
        self.ok_sem_token = ok_sem_token

    def __call__(self, pedido: Any, timeout: float = 0) -> Any:
        import io
        import urllib.error

        cab = dict(pedido.header_items())
        self.cabecalhos.append(cab)
        if self.ok_sem_token and "X-user-auth-token" not in cab:
            return _Resposta({"title": "Esú", "artist": {"name": "Baco"}})
        corpo = io.BytesIO(b'{"message": "Invalid or missing user_auth_token"}')
        raise urllib.error.HTTPError(pedido.full_url, 400, "Bad Request", {}, corpo)


def _com_config(app: str, token: str):
    original = fila._ler_config
    fila._ler_config = lambda: (app, token)
    return original


def test_buscar_nome_manda_o_token_da_conta_quando_ha() -> None:
    os.environ["QOBUZ_DL_FILA_REDE"] = "1"
    original = _com_config("111", "tok-da-conta")
    try:
        abrir, pedidos = _com_rede({"title": "Esú", "artist": {"name": "Baco"}})
        assert fila.buscar_nome(ALBUM, 3.0, abrir=abrir) == "Baco — Esú"
        assert pedidos[0][1].get("X-user-auth-token") == "tok-da-conta"
    finally:
        fila._ler_config = original
        os.environ["QOBUZ_DL_FILA_REDE"] = "0"


def test_buscar_nome_tenta_sem_token_quando_o_qobuz_recusa_o_token() -> None:
    os.environ["QOBUZ_DL_FILA_REDE"] = "1"
    original = _com_config("111", "token-velho")
    try:
        abrir = _ErroHttp(ok_sem_token=True)
        assert fila.buscar_nome(ALBUM, 3.0, abrir=abrir) == "Baco — Esú"
        assert len(abrir.cabecalhos) == 2  # com token (recusado) e sem token
    finally:
        fila._ler_config = original
        os.environ["QOBUZ_DL_FILA_REDE"] = "0"


def test_buscar_nome_guarda_o_motivo_quando_o_qobuz_recusa() -> None:
    os.environ["QOBUZ_DL_FILA_REDE"] = "1"
    original = _com_config("111", "")
    fila._ultimo_erro.clear()
    try:
        assert fila.buscar_nome(ALBUM, 3.0, abrir=_ErroHttp()) is None
        assert fila.ultimo_erro() == "HTTP 400: Invalid or missing user_auth_token"
        fila._ultimo_erro.clear()
        abrir, _ = _com_rede(erro=True)
        assert fila.buscar_nome(ALBUM, 3.0, abrir=abrir) is None
        assert fila.ultimo_erro() == "OSError: sem rede"
    finally:
        fila._ler_config = original
        os.environ["QOBUZ_DL_FILA_REDE"] = "0"
        fila._ultimo_erro.clear()


def test_cli_diz_por_que_o_nome_nao_veio() -> None:
    original = fila.buscar_nome

    def falha(link: str, timeout: float = 0, abrir: Any = None) -> None:
        fila._registrar_erro("HTTP 400: Invalid app_id")
        return None

    fila.buscar_nome = falha
    try:
        with _tmp() as d:
            _, texto = _cli("--dir", d, "adicionar", ALBUM)
            assert texto.splitlines()[1] == "[ ÁLBUM ] abc123"
            assert texto.rstrip().endswith("Sem o nome: HTTP 400: Invalid app_id")
            _, texto = _cli("--dir", d, "listar")
            assert "1. [ ÁLBUM ] abc123" in texto
            assert texto.rstrip().endswith("Sem o nome: HTTP 400: Invalid app_id")
    finally:
        fila.buscar_nome = original


# --- andamento ao vivo (o iPad só mostra o anel no banner) --------------------
def test_andamento_grava_le_e_limpa() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        assert f.ler_andamento() is None
        f.gravar_andamento("[ ÁLBUM ] 1 · faixa 4/12", "Lady Gaga — Chromatica")
        assert f.ler_andamento() == {
            "titulo": "[ ÁLBUM ] 1 · faixa 4/12",
            "nome": "Lady Gaga — Chromatica",
        }
        f.limpar_andamento()
        assert f.ler_andamento() is None
        f.limpar_andamento()  # já não existe: não é erro


def test_andamento_velho_demais_conta_como_parado() -> None:
    import json

    with _tmp() as d:
        f = fila.Fila(d)
        f.gravar_andamento("x", "y")
        caminho = os.path.join(d, fila.ARQUIVO_ANDAMENTO)
        dados = json.load(open(caminho, encoding="utf-8"))
        dados["ts"] -= fila.ANDAMENTO_VALIDADE_S + 5
        json.dump(dados, open(caminho, "w", encoding="utf-8"))
        assert f.ler_andamento() is None


def test_publicar_andamento_nao_regrava_o_mesmo_texto_a_cada_instante() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        fila._PUBLICADO[0], fila._PUBLICADO[1] = "", 0.0
        fila.publicar_andamento("t1", "n", fila=f, agora=100.0)
        antes = os.stat(os.path.join(d, fila.ARQUIVO_ANDAMENTO)).st_mtime_ns
        fila.publicar_andamento("t1", "n", fila=f, agora=100.5)  # igual e cedo: pula
        assert os.stat(os.path.join(d, fila.ARQUIVO_ANDAMENTO)).st_mtime_ns == antes
        fila.publicar_andamento("t2", "n", fila=f, agora=100.6)  # mudou: grava
        assert f.ler_andamento()["titulo"] == "t2"
        fila.publicar_andamento("t2", "n", fila=f, agora=103.0)  # passou de 2 s: renova
        fila.encerrar_andamento(f)
        assert f.ler_andamento() is None
        fila._PUBLICADO[0], fila._PUBLICADO[1] = "", 0.0


def test_listar_mostra_o_que_esta_baixando_agora_mesmo_sem_fila() -> None:
    with _tmp() as d:
        f = fila.Fila(d)
        f.gravar_andamento("[ ÁLBUM ] 1 · faixa 4/12", "Lady Gaga — Chromatica")
        _, texto = _cli("--dir", d, "listar")
        linhas = texto.splitlines()
        assert "Download em andamento." in linhas  # download direto, sem a fila
        assert "Agora: Lady Gaga — Chromatica" in linhas
        assert "       [ ÁLBUM ] 1 · faixa 4/12" in linhas
        f.limpar_andamento()
        _, texto = _cli("--dir", d, "listar")
        assert "Agora:" not in texto and "Nenhum download rodando." in texto


def test_listar_na_partida_mostra_so_a_etapa() -> None:
    with _tmp() as d:
        fila.Fila(d).gravar_andamento("Qobuz · entrando na conta · 10 s")
        _, texto = _cli("--dir", d, "listar")
        assert "Agora: Qobuz · entrando na conta · 10 s" in texto.splitlines()


def test_cli_nome_mostra_tipo_artista_e_titulo() -> None:
    original = fila.buscar_nome
    fila.buscar_nome = lambda link, timeout=0, abrir=None: "Lady Gaga — Chromatica"
    try:
        with _tmp() as d:
            codigo, texto = _cli("--dir", d, "nome", ALBUM, "lixo")
            linhas = texto.splitlines()
            assert codigo == 0
            assert linhas[0] == "[ ÁLBUM ] Lady Gaga — Chromatica"
            assert linhas[1].startswith("Link não reconhecido")
    finally:
        fila.buscar_nome = original
