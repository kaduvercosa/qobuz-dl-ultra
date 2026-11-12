"""Hooks pós-download: configuração, comando, webhook, ntfy e rescan.

O QUE ESTE ARQUIVO TRAVA
------------------------
- Precedência flag > ambiente > config.ini, e nome de servidor inválido avisa.
- O comando do usuário recebe os dados por ENVIRONMENT/stdin, nunca colados no
  texto: um título com `$(...)` ou aspas não executa nada.
- Hook com defeito, lento ou ausente (iOS) nunca derruba nem trava o download.
- Rescan só no fim e só se houve arquivo novo; Ctrl+C não usa rede.
- Segredos não vazam em mensagem (a URL com token nunca é impressa).

Nenhum teste usa rede: o cliente HTTP é um falso injetado.
"""

import argparse
import configparser
import hashlib
import hmac
import json
import os
import sys
import types

import pytest

from qobuz_dl import events, hooks

pytestmark = pytest.mark.unit

# Roda comandos de verdade: no Windows não há `sh` e no iOS/a-Shell abrir um
# processo pode encerrar o app (mesmo motivo dos testes do ffmpeg real).
SO_UNIX = pytest.mark.skipif(
    os.name == "nt" or sys.platform == "ios",
    reason="roda comandos no shell; no iOS abrir processo encerra o app",
)


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    events.reiniciar()
    avisos = []
    monkeypatch.setattr(hooks, "_avisar", avisos.append)
    monkeypatch.setattr(hooks, "_informar", lambda _m: None)
    yield avisos
    events.reiniciar()


class Resp:
    def __init__(self, status=200, corpo=None):
        self.status_code = status
        self._corpo = corpo

    def json(self):
        if self._corpo is None:
            raise ValueError("sem corpo")
        return self._corpo


class ClienteFalso:
    def __init__(self, status=200, corpo=None, erro=None):
        self.chamadas = []
        self.status, self.corpo, self.erro = status, corpo, erro
        self.fechado = False

    async def _responder(self, metodo, url, kw):
        self.chamadas.append((metodo, url, kw))
        if self.erro:
            raise self.erro
        return Resp(self.status, self.corpo)

    async def get(self, url, **kw):
        return await self._responder("get", url, kw)

    async def post(self, url, **kw):
        return await self._responder("post", url, kw)

    async def aclose(self):
        self.fechado = True


def _args(**kw):
    base = {"command": "dl"}
    base.update(kw)
    return types.SimpleNamespace(**base)


def _config(**chaves):
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.add_section("qobuz")
    for k, v in chaves.items():
        cfg.set("qobuz", k, v)
    return cfg


def _relatorio(total=2, ok=2, falhas=0, titulo="Album"):
    return {
        "tipo": "album",
        "identificacao": {"titulo": titulo, "artista": "Art", "id": "1"},
        "progresso": {"resumo": {"total": total, "concluidas": ok, "falhas": falhas}},
        "qualidade": {"formato": "FLAC", "bit_depth": 24, "sampling_rate": 96},
    }


# ----------------------------------------------------------------------------
# Configuração
# ----------------------------------------------------------------------------
def test_precedencia_flag_ambiente_config():
    cfg = _config(on_finish="do-config", webhook_url="http://config")
    amb = {"QOBUZ_DL_ON_FINISH": "do-ambiente", "QOBUZ_DL_WEBHOOK_URL": "http://amb"}
    c = hooks.carregar(_args(on_finish="da-flag"), cfg, "qobuz", amb)
    assert c.on_finish == "da-flag"
    assert c.webhook_url == "http://amb"  # ambiente vence o config.ini
    c = hooks.carregar(_args(), cfg, "qobuz", {})
    assert (c.on_finish, c.webhook_url) == ("do-config", "http://config")


def test_nada_configurado_fica_desligado():
    c = hooks.carregar(_args(), _config(), "qobuz", {})
    assert not c.algo_ligado()
    assert hooks.preparar(_args(), _config(), "qobuz", ambiente={}) is None


def test_comando_que_nao_baixa_nao_liga(_limpo):
    ns = _args(command="stats", on_finish="x")
    assert hooks.preparar(ns, None, "qobuz", ambiente={}) is None


def test_rescan_apelidos_e_desconhecido(_limpo):
    c = hooks.carregar(_args(rescan="Navidrome, plex,gonic,foo"), None, "qobuz", {})
    assert c.rescan == ("subsonic", "plex")
    assert any("foo" in a for a in _limpo)


def test_eventos_desconhecidos_avisam_e_caem_no_padrao(_limpo):
    c = hooks.carregar(_args(), _config(ntfy_events="inventado"), "qobuz", {})
    assert c.ntfy_eventos == ("sessao_concluida",)
    assert any("inventado" in a for a in _limpo)


def test_prazo_fora_da_faixa_e_limitado():
    assert hooks._numero("9999", 60.0) == 600.0
    assert hooks._numero("0", 60.0) == 1.0
    assert hooks._numero("abc", 60.0) == 60.0


def test_flags_existem_no_parser():
    p = argparse.ArgumentParser()
    hooks.adicionar_args(p)
    ns = p.parse_args(
        ["--events", "e.ndjson", "--on-complete", "a", "--on-error", "b",
         "--on-finish", "c", "--webhook", "http://x", "--ntfy", "t", "--rescan", "plex"]
    )  # fmt: skip
    assert (ns.events_file, ns.on_complete, ns.on_error) == ("e.ndjson", "a", "b")
    assert (ns.on_finish, ns.webhook, ns.ntfy, ns.rescan) == (
        "c",
        "http://x",
        "t",
        "plex",
    )
    assert p.parse_args([]).events_file is None


# ----------------------------------------------------------------------------
# Variáveis e textos
# ----------------------------------------------------------------------------
def test_variaveis_de_ambiente():
    ev = {
        "evento": "lancamento_concluido", "titulo": "T", "artista": "A", "pasta": "/p",
        "completo": True, "faixas_novas": 3, "tipo_lancamento": "Album",
        "resumo": {"total": 4, "concluidas": 3, "falhas": 1, "puladas": 0},
        "qualidade": {"formato": "FLAC", "bit_depth": 24, "sampling_rate": 96},
    }  # fmt: skip
    v = hooks.variaveis_de_ambiente(ev)
    assert v["QOBUZ_DL_TITULO"] == "T" and v["QOBUZ_DL_COMPLETO"] == "1"
    assert v["QOBUZ_DL_FAIXAS_OK"] == "3" and v["QOBUZ_DL_FAIXAS_FALHAS"] == "1"
    assert v["QOBUZ_DL_FAIXAS_NOVAS"] == "3" and v["QOBUZ_DL_QUALIDADE"] == "FLAC 24/96"
    assert v["QOBUZ_DL_TIPO"] == "Album"
    assert json.loads(v["QOBUZ_DL_JSON"])["titulo"] == "T"
    assert all(isinstance(x, str) for x in v.values())  # None vira texto vazio


def test_texto_de_notificacao():
    t, m, problema = hooks.texto_evento(
        {"evento": "sessao_concluida", "ok": True, "lancamentos": 2, "faixas_novas": 9,
         "faixas_falhas": 1, "lancamentos_incompletos": 1, "duracao_s": 125}
    )  # fmt: skip
    assert problema and "com falhas" in t
    assert "9 faixa(s) nova(s)" in m and "2 min 05 s" in m
    t, m, problema = hooks.texto_evento(
        {"evento": "lancamento_concluido", "titulo": "X", "artista": "Y", "completo": True,
         "resumo": {"total": 3, "concluidas": 3}}
    )  # fmt: skip
    assert not problema and t == "Concluído: X" and m.startswith("Y · 3/3 faixas")


# ----------------------------------------------------------------------------
# Comando
# ----------------------------------------------------------------------------
@SO_UNIX
async def test_titulo_malicioso_nao_executa_e_chega_inteiro(tmp_path):
    saida = tmp_path / "saida.txt"
    perigoso = 'x"; touch ' + str(tmp_path / "invadido") + '; echo "$(id)'
    ev = {"evento": "lancamento_concluido", "titulo": perigoso, "completo": True}
    cmd = f'printf "%s" "$QOBUZ_DL_TITULO" > "{saida}"'
    assert await hooks.executar_comando(cmd, ev, timeout=10) is True
    assert saida.read_text(encoding="utf-8") == perigoso
    assert not (tmp_path / "invadido").exists()


@SO_UNIX
async def test_json_no_stdin(tmp_path):
    saida = tmp_path / "stdin.json"
    ev = {"evento": "faixa", "titulo": "açaí"}
    assert await hooks.executar_comando(f'cat > "{saida}"', ev, timeout=10) is True
    assert json.loads(saida.read_text(encoding="utf-8"))["titulo"] == "açaí"


@SO_UNIX
async def test_comando_roda_na_pasta_do_lancamento(tmp_path):
    saida = tmp_path / "pwd.txt"
    ev = {"evento": "lancamento_concluido", "pasta": str(tmp_path)}
    assert await hooks.executar_comando(f'pwd > "{saida}"', ev, timeout=10)
    assert os.path.realpath(saida.read_text().strip()) == os.path.realpath(tmp_path)


@SO_UNIX
async def test_codigo_de_saida_ruim_vira_aviso(_limpo):
    assert (
        await hooks.executar_comando("echo oops >&2; exit 3", {"evento": "x"}, 10)
        is False
    )
    assert any("código 3" in a and "oops" in a for a in _limpo)


@SO_UNIX
async def test_prazo_estourado_mata_o_comando(_limpo):
    assert (
        await hooks.executar_comando("sleep 5", {"evento": "x"}, timeout=0.3) is False
    )
    assert any("interrompido" in a for a in _limpo)


async def test_comando_vazio_nao_faz_nada():
    assert await hooks.executar_comando("   ", {"evento": "x"}) is False


def test_sem_subprocesso_so_em_plataformas_restritas(monkeypatch):
    for plataforma, esperado in (
        ("ios", True),
        ("emscripten", True),
        ("linux", False),
        ("darwin", False),
        ("win32", False),
    ):
        monkeypatch.setattr(hooks.sys, "platform", plataforma)
        assert hooks._sem_subprocesso() is esperado


async def test_ios_nunca_tenta_abrir_processo(monkeypatch, _limpo):
    """No a-Shell abrir processo pode fechar o app: o hook nem tenta."""

    def proibido(*a, **k):
        raise AssertionError("tentou abrir um processo")

    monkeypatch.setattr(hooks.asyncio, "create_subprocess_shell", proibido)
    monkeypatch.setattr(hooks, "_sem_subprocesso", lambda: True)
    monkeypatch.setattr(hooks, "_AVISOU_SEM_SUBPROCESSO", False)
    assert await hooks.executar_comando("echo oi", {"evento": "x"}) is False
    assert await hooks.executar_comando("echo oi", {"evento": "x"}) is False
    assert len([a for a in _limpo if "neste ambiente" in a]) == 1


async def test_sem_subprocesso_avisa_uma_vez(monkeypatch, _limpo):
    async def nao_pode(*a, **k):
        raise NotImplementedError

    monkeypatch.setattr(hooks.asyncio, "create_subprocess_shell", nao_pode)
    monkeypatch.setattr(hooks, "_AVISOU_SEM_SUBPROCESSO", False)
    assert await hooks.executar_comando("echo", {"evento": "x"}) is False
    assert await hooks.executar_comando("echo", {"evento": "x"}) is False
    assert len([a for a in _limpo if "neste ambiente" in a]) == 1


# ----------------------------------------------------------------------------
# Webhook e ntfy
# ----------------------------------------------------------------------------
async def test_webhook_assina_o_corpo():
    cliente = ClienteFalso()
    ev = {"evento": "lancamento_concluido", "titulo": "açaí"}
    assert await hooks.enviar_webhook(cliente, "http://h/x", ev, "segredo") is True
    _m, url, kw = cliente.chamadas[0]
    esperado = hmac.new(b"segredo", kw["content"], hashlib.sha256).hexdigest()
    assert kw["headers"]["X-Qobuz-DL-Signature"] == f"sha256={esperado}"
    assert json.loads(kw["content"].decode("utf-8"))["titulo"] == "açaí"
    assert kw["headers"]["X-Qobuz-DL-Event"] == "lancamento_concluido"


async def test_webhook_sem_segredo_nao_assina():
    cliente = ClienteFalso()
    await hooks.enviar_webhook(cliente, "http://h/x", {"evento": "faixa"})
    assert "X-Qobuz-DL-Signature" not in cliente.chamadas[0][2]["headers"]


async def test_webhook_falha_avisa_sem_vazar_a_url(_limpo):
    cliente = ClienteFalso(status=500)
    assert (
        await hooks.enviar_webhook(cliente, "http://h/token-secreto", {"evento": "x"})
        is False
    )
    assert any("HTTP 500" in a for a in _limpo)
    assert not any("token-secreto" in a for a in _limpo)
    cliente = ClienteFalso(erro=ConnectionError("boom http://h/token-secreto"))
    assert (
        await hooks.enviar_webhook(cliente, "http://h/token-secreto", {"evento": "x"})
        is False
    )
    assert not any("token-secreto" in a for a in _limpo)


def test_separar_ntfy():
    assert hooks.separar_ntfy("meu-topico") == ("https://ntfy.sh", "meu-topico")
    assert hooks.separar_ntfy("https://n.exemplo.com/meu") == (
        "https://n.exemplo.com",
        "meu",
    )
    assert hooks.separar_ntfy("https://x.com/ntfy/meu/") == (
        "https://x.com/ntfy",
        "meu",
    )
    assert hooks.separar_ntfy("https://n.exemplo.com") == ("https://n.exemplo.com", "")


async def test_ntfy_payload_e_token():
    cliente = ClienteFalso()
    ev = {"evento": "sessao_concluida", "ok": True, "lancamentos": 1, "faixas_novas": 2}
    assert await hooks.enviar_ntfy(cliente, "topico", ev, token="tk") is True
    _m, url, kw = cliente.chamadas[0]
    assert url == "https://ntfy.sh"
    assert kw["json"]["topic"] == "topico" and kw["json"]["priority"] == 3
    assert kw["headers"] == {"Authorization": "Bearer tk"}
    ev["faixas_falhas"] = 2
    await hooks.enviar_ntfy(cliente, "topico", ev)
    assert cliente.chamadas[1][2]["json"]["priority"] == 4
    assert cliente.chamadas[1][2]["json"]["tags"] == ["warning"]


async def test_ntfy_sem_topico_avisa(_limpo):
    assert (
        await hooks.enviar_ntfy(ClienteFalso(), "https://n.com", {"evento": "x"})
        is False
    )
    assert any("tópico" in a for a in _limpo)


# ----------------------------------------------------------------------------
# Rescan
# ----------------------------------------------------------------------------
async def test_rescan_subsonic_token_e_resposta():
    cfg = hooks.ConfigHooks(
        rescan=("subsonic",), subsonic_url="http://nd:4533/",
        subsonic_user="u", subsonic_password="senha",
    )  # fmt: skip
    cliente = ClienteFalso(corpo={"subsonic-response": {"status": "ok"}})
    ok, _ = (await hooks.reescanear(cliente, cfg))["subsonic"]
    assert ok
    _m, url, kw = cliente.chamadas[0]
    assert url == "http://nd:4533/rest/startScan"
    p = kw["params"]
    assert p["t"] == hashlib.md5((b"senha" + p["s"].encode())).hexdigest()
    assert p["u"] == "u" and p["f"] == "json" and "senha" not in json.dumps(p)


async def test_rescan_subsonic_erro_do_servidor():
    cfg = hooks.ConfigHooks(
        rescan=("subsonic",), subsonic_url="http://nd", subsonic_user="u", subsonic_password="x"
    )  # fmt: skip
    corpo = {
        "subsonic-response": {
            "status": "failed",
            "error": {"message": "Wrong password"},
        }
    }
    ok, detalhe = (await hooks.reescanear(ClienteFalso(corpo=corpo), cfg))["subsonic"]
    assert not ok and detalhe == "Wrong password"


async def test_rescan_plex_varias_secoes():
    cfg = hooks.ConfigHooks(
        rescan=("plex",), plex_url="http://plex:32400", plex_token="tk", plex_section="3, 5"
    )  # fmt: skip
    cliente = ClienteFalso()
    ok, _ = (await hooks.reescanear(cliente, cfg))["plex"]
    assert ok
    urls = [c[1] for c in cliente.chamadas]
    assert urls == [
        "http://plex:32400/library/sections/3/refresh",
        "http://plex:32400/library/sections/5/refresh",
    ]
    assert all(c[2]["headers"] == {"X-Plex-Token": "tk"} for c in cliente.chamadas)


async def test_rescan_jellyfin():
    cfg = hooks.ConfigHooks(
        rescan=("jellyfin",), jellyfin_url="http://jf:8096", jellyfin_api_key="k"
    )
    cliente = ClienteFalso(status=204)
    ok, _ = (await hooks.reescanear(cliente, cfg))["jellyfin"]
    m, url, kw = cliente.chamadas[0]
    assert ok and m == "post" and url == "http://jf:8096/Library/Refresh"
    assert kw["headers"]["Authorization"] == 'MediaBrowser Token="k"'


async def test_rescan_com_config_faltando_explica():
    cfg = hooks.ConfigHooks(rescan=("plex", "subsonic", "jellyfin"))
    res = await hooks.reescanear(ClienteFalso(), cfg)
    assert all(not ok for ok, _ in res.values())
    assert "plex_section" in res["plex"][1]


async def test_rescan_um_servidor_caindo_nao_derruba_os_outros():
    cfg = hooks.ConfigHooks(
        rescan=("plex", "jellyfin"), plex_url="http://p", plex_token="t", plex_section="1",
        jellyfin_url="http://j", jellyfin_api_key="k",
    )  # fmt: skip

    class Metade(ClienteFalso):
        async def get(self, url, **kw):
            raise ConnectionError("fora do ar")

    res = await hooks.reescanear(Metade(status=204), cfg)
    assert res["plex"][0] is False and res["jellyfin"][0] is True


# ----------------------------------------------------------------------------
# Sessão completa (Automacao)
# ----------------------------------------------------------------------------
def _montar(tmp_path, **flags):
    cliente = ClienteFalso(status=204, corpo={"subsonic-response": {"status": "ok"}})
    cfg = hooks.ConfigHooks(
        webhook_url="http://h/x", ntfy="topico", rescan=("plex",),
        plex_url="http://p", plex_token="t", plex_section="1", **flags,
    )  # fmt: skip
    auto = hooks.Automacao(cfg, cliente_factory=lambda _t: cliente)
    auto.iniciar()
    return auto, cliente


def _baixar(tmp_path, nome="Album", novas=1, completo=True):
    trabalho = str(tmp_path / f"[IN PROGRESS] {nome}")
    for i in range(novas):
        events.faixa(trabalho, f"{i:02d}", i, f"f{i}", "Art", "ok")
    if novas == 0:
        events.faixa(trabalho, "01", 1, "f", "Art", "ok", events.MOTIVO_EXISTENTE)
    events.lancamento_concluido(str(tmp_path / nome), completo, _relatorio(titulo=nome))


async def test_sessao_com_arquivo_novo_faz_rescan_webhook_e_ntfy(tmp_path):
    auto, cliente = _montar(tmp_path)
    _baixar(tmp_path)
    await auto.finalizar(ok=True)
    chamadas = [(m, u) for m, u, _ in cliente.chamadas]
    assert ("get", "http://p/library/sections/1/refresh") in chamadas
    assert chamadas.count(("post", "http://h/x")) == 2  # lançamento + sessão
    assert ("post", "https://ntfy.sh") in chamadas
    assert cliente.fechado is True


async def test_sessao_sem_arquivo_novo_nao_faz_rescan(tmp_path):
    auto, cliente = _montar(tmp_path)
    _baixar(tmp_path, novas=0)
    await auto.finalizar(ok=True)
    assert not any("refresh" in u for _m, u, _k in cliente.chamadas)
    assert any(u == "https://ntfy.sh" for _m, u, _k in cliente.chamadas)  # o aviso sai


async def test_sessao_vazia_nao_dispara_nada(tmp_path):
    auto, cliente = _montar(tmp_path)
    await auto.finalizar(ok=True)
    assert cliente.chamadas == []


async def test_ctrl_c_nao_usa_rede(tmp_path):
    auto, cliente = _montar(tmp_path)
    _baixar(tmp_path)
    vistos = []
    events.assinar(vistos.append)
    await auto.finalizar(ok=False, interrompida=True)
    assert [
        c for c in cliente.chamadas if "refresh" in c[1] or c[1] == "https://ntfy.sh"
    ] == []
    assert (
        vistos[-1]["evento"] == "sessao_concluida"
        and vistos[-1]["interrompida"] is True
    )


@SO_UNIX
async def test_on_complete_e_on_error_por_resultado(tmp_path):
    ok_log, erro_log = tmp_path / "ok.txt", tmp_path / "erro.txt"
    cfg = hooks.ConfigHooks(
        on_complete=f'echo "$QOBUZ_DL_TITULO" >> "{ok_log}"',
        on_error=f'echo "$QOBUZ_DL_TITULO" >> "{erro_log}"',
    )
    auto = hooks.Automacao(cfg)
    auto.iniciar()
    _baixar(tmp_path, "Bom", completo=True)
    _baixar(tmp_path, "Ruim", completo=False)
    await auto.finalizar(ok=True)
    assert ok_log.read_text().strip() == "Bom"
    assert erro_log.read_text().strip() == "Ruim"


@SO_UNIX
async def test_on_finish_roda_uma_vez_com_totais(tmp_path):
    log = tmp_path / "fim.txt"
    cfg = hooks.ConfigHooks(
        on_finish=f'echo "$QOBUZ_DL_LANCAMENTOS/$QOBUZ_DL_FAIXAS_NOVAS" >> "{log}"'
    )
    auto = hooks.Automacao(cfg)
    auto.iniciar()
    _baixar(tmp_path, "A", novas=2)
    _baixar(tmp_path, "B", novas=1)
    await auto.finalizar(ok=True)
    assert log.read_text().splitlines() == ["2/3"]


async def test_hook_quebrado_nao_derruba(tmp_path, monkeypatch):
    async def explode(*a, **k):
        raise RuntimeError("defeito no hook")

    monkeypatch.setattr(hooks, "enviar_webhook", explode)
    auto, _cliente = _montar(tmp_path)
    _baixar(tmp_path)
    await auto.finalizar(ok=True)  # não levanta


async def test_encerrar_none_e_inofensivo():
    await hooks.encerrar(None, ok=True)


async def test_preparar_liga_arquivo_de_eventos_e_encerrar_fecha(tmp_path):
    destino = tmp_path / "ev.ndjson"
    auto = hooks.preparar(_args(events_file=str(destino)), None, "qobuz", "1.0", {})
    assert auto is not None
    events.faixa(str(tmp_path / "A"), "01", 1, "a", "Art", "ok")
    await hooks.encerrar(auto, ok=True)
    nomes = [
        json.loads(x)["evento"]
        for x in destino.read_text(encoding="utf-8").splitlines()
    ]
    assert nomes == ["sessao_iniciada", "faixa", "sessao_concluida"]
    assert events.gravando() is False
