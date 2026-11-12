# ==============================================================================
# MÓDULO: hooks.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Ações automáticas depois dos downloads: comandos, webhook, ntfy e
#            reescaneamento do servidor de música (Navidrome/Subsonic, Plex,
#            Jellyfin). Alimentado pelos eventos de `events.py`.
#
# Tudo é OPCIONAL e desligado por padrão. Nada aqui pode quebrar ou travar um
# download: toda falha vira aviso, todo comando e toda requisição tem prazo.
#
# Quando cada coisa roda
#   --on-complete CMD   a cada lançamento (álbum, faixa avulsa, playlist) concluído
#   --on-error CMD      a cada lançamento que terminou incompleto
#   --on-finish CMD     UMA vez, no fim da sessão (se algo foi processado)
#   --rescan SERVIDOR   UMA vez, no fim, e só se baixou arquivo novo
#   --webhook URL       POST em JSON (padrão: fim de cada lançamento e da sessão)
#   --ntfy TOPICO       notificação (padrão: só no fim da sessão)
#
# Segurança: o comando roda no shell do usuário, mas os dados do download (título,
# artista, pasta) vão em VARIÁVEIS DE AMBIENTE (QOBUZ_DL_*) e no stdin, nunca
# coladas no texto do comando. Um título com aspas, `;` ou `$(...)` não executa
# nada. Os segredos (token, senha) podem vir de variável de ambiente.
# ==============================================================================

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlparse

from qobuz_dl import events

logger = logging.getLogger(__name__)

# Comandos que baixam. Os outros (config, stats, tags...) não disparam hooks.
COMANDOS_DE_DOWNLOAD = frozenset(
    {
        "dl",
        "lucky",
        "interactive",
        "i",
        "fun",
        "sync-playlist",
        "sp",
        "import-playlist",
        "ip",
        "sync-favorites",
        "sf",
        "retry",
        None,
    }
)

# navidrome/gonic falam o protocolo Subsonic, então são apelidos de "subsonic".
SERVIDORES = ("subsonic", "plex", "jellyfin")
_APELIDOS = {"navidrome": "subsonic", "gonic": "subsonic"}

_EVENTOS_WEBHOOK_PADRAO = ("lancamento_concluido", "sessao_concluida")
_EVENTOS_NTFY_PADRAO = ("sessao_concluida",)
_NTFY_PADRAO = "https://ntfy.sh"
_AGENTE = "qobuz-dl-ultra"


# ----------------------------------------------------------------------------
# Configuração (flags > variáveis de ambiente > config.ini)
# ----------------------------------------------------------------------------
@dataclass
class ConfigHooks:
    """Tudo que os hooks precisam. Vazio = desligado."""

    eventos_arquivo: str = ""
    on_complete: str = ""
    on_error: str = ""
    on_finish: str = ""
    timeout_comando: float = 60.0
    timeout_http: float = 10.0
    webhook_url: str = ""
    webhook_segredo: str = ""
    webhook_eventos: tuple[str, ...] = _EVENTOS_WEBHOOK_PADRAO
    ntfy: str = ""
    ntfy_token: str = ""
    ntfy_eventos: tuple[str, ...] = _EVENTOS_NTFY_PADRAO
    rescan: tuple[str, ...] = ()
    subsonic_url: str = ""
    subsonic_user: str = ""
    subsonic_password: str = ""
    plex_url: str = ""
    plex_token: str = ""
    plex_section: str = ""
    jellyfin_url: str = ""
    jellyfin_api_key: str = ""

    def tem_hooks(self) -> bool:
        return bool(
            self.on_complete
            or self.on_error
            or self.on_finish
            or self.webhook_url
            or self.ntfy
            or self.rescan
        )

    def algo_ligado(self) -> bool:
        return bool(self.eventos_arquivo) or self.tem_hooks()


def _avisar(mensagem: str) -> None:
    """Aviso que o usuário precisa ver; cai no log se a tela não estiver disponível."""
    try:
        from qobuz_dl import ui

        ui.warn(mensagem)
    except Exception as exc:
        logger.warning("%s (sem tela: %s)", mensagem, exc)


def _informar(mensagem: str) -> None:
    try:
        from qobuz_dl import ui

        ui.info(mensagem)
    except Exception as exc:
        logger.info("%s (sem tela: %s)", mensagem, exc)


def _lista(valor: str) -> tuple[str, ...]:
    partes = [p.strip().lower() for p in str(valor or "").replace(";", ",").split(",")]
    return tuple(p for p in partes if p)


def _ler(
    arguments: Any,
    config: Any,
    section: str,
    ambiente: Mapping[str, str],
    chave: str,
    argumento: str | None = None,
) -> str:
    """Valor de ``chave``: flag da linha de comando, depois QOBUZ_DL_<CHAVE>,
    depois o config.ini (``raw``: URLs e comandos podem ter ``%``)."""
    if argumento:
        valor = getattr(arguments, argumento, None)
        if valor not in (None, ""):
            return str(valor).strip()
    valor = ambiente.get(f"QOBUZ_DL_{chave.upper()}", "")
    if valor.strip():
        return valor.strip()
    if config is not None:
        try:
            return str(config.get(section, chave, fallback="", raw=True) or "").strip()
        except Exception as exc:
            # Seção ou chave estranha no INI: trata como "não configurado".
            logger.debug("config.ini: não li %s: %s", chave, exc)
    return ""


def _numero(
    texto: str, padrao: float, minimo: float = 1.0, maximo: float = 600.0
) -> float:
    try:
        valor = float(texto)
    except (TypeError, ValueError):
        return padrao
    return min(max(valor, minimo), maximo)


def _eventos_validos(texto: str, padrao: tuple[str, ...], quem: str) -> tuple[str, ...]:
    escolhidos = _lista(texto)
    if not escolhidos:
        return padrao
    validos = tuple(e for e in escolhidos if e in events.EVENTOS_CONHECIDOS)
    for desconhecido in (e for e in escolhidos if e not in events.EVENTOS_CONHECIDOS):
        _avisar(
            f"{quem}: evento desconhecido '{desconhecido}' ignorado "
            f"(valores: {', '.join(events.EVENTOS_CONHECIDOS)})."
        )
    return validos or padrao


def carregar(
    arguments: Any,
    config: Any,
    section: str = "qobuz",
    ambiente: Mapping[str, str] | None = None,
) -> ConfigHooks:
    """Monta a configuração a partir da linha de comando, do ambiente e do config.ini."""
    amb = os.environ if ambiente is None else ambiente

    def ler(chave: str, argumento: str | None = None) -> str:
        return _ler(arguments, config, section, amb, chave, argumento)

    servidores = []
    for nome in _lista(ler("rescan", "rescan")):
        nome = _APELIDOS.get(nome, nome)
        if nome in SERVIDORES:
            if nome not in servidores:
                servidores.append(nome)
        else:
            _avisar(
                f"--rescan: servidor desconhecido '{nome}' "
                f"(use: navidrome, subsonic, plex, jellyfin)."
            )
    return ConfigHooks(
        eventos_arquivo=ler("events_file", "events_file"),
        on_complete=ler("on_complete", "on_complete"),
        on_error=ler("on_error", "on_error"),
        on_finish=ler("on_finish", "on_finish"),
        timeout_comando=_numero(ler("hook_timeout"), 60.0),
        timeout_http=_numero(ler("hook_http_timeout"), 10.0),
        webhook_url=ler("webhook_url", "webhook"),
        webhook_segredo=ler("webhook_secret"),
        webhook_eventos=_eventos_validos(
            ler("webhook_events"), _EVENTOS_WEBHOOK_PADRAO, "webhook_events"
        ),
        ntfy=ler("ntfy", "ntfy"),
        ntfy_token=ler("ntfy_token"),
        ntfy_eventos=_eventos_validos(
            ler("ntfy_events"), _EVENTOS_NTFY_PADRAO, "ntfy_events"
        ),
        rescan=tuple(servidores),
        subsonic_url=ler("subsonic_url"),
        subsonic_user=ler("subsonic_user"),
        subsonic_password=ler("subsonic_password"),
        plex_url=ler("plex_url"),
        plex_token=ler("plex_token"),
        plex_section=ler("plex_section"),
        jellyfin_url=ler("jellyfin_url"),
        jellyfin_api_key=ler("jellyfin_api_key"),
    )


def adicionar_args(parser: Any) -> None:
    """Acrescenta as flags de eventos e hooks a um subcomando de download."""
    grupo = parser.add_argument_group("eventos e hooks")
    grupo.add_argument(
        "--events",
        dest="events_file",
        metavar="ARQUIVO",
        default=None,
        help=(
            "grava um evento JSON por linha (progresso de faixas e álbuns) em "
            "ARQUIVO; use - para o stderr"
        ),
    )
    grupo.add_argument(
        "--on-complete",
        dest="on_complete",
        metavar="COMANDO",
        default=None,
        help="roda COMANDO ao concluir cada álbum/faixa/playlist (dados em QOBUZ_DL_*)",
    )
    grupo.add_argument(
        "--on-error",
        dest="on_error",
        metavar="COMANDO",
        default=None,
        help="roda COMANDO quando um álbum/faixa/playlist termina incompleto",
    )
    grupo.add_argument(
        "--on-finish",
        dest="on_finish",
        metavar="COMANDO",
        default=None,
        help="roda COMANDO uma vez, no fim da sessão de download",
    )
    grupo.add_argument(
        "--webhook",
        dest="webhook",
        metavar="URL",
        default=None,
        help="envia os eventos concluídos por POST (JSON) para esta URL",
    )
    grupo.add_argument(
        "--ntfy",
        dest="ntfy",
        metavar="TOPICO_OU_URL",
        default=None,
        help="notifica no ntfy (tópico, ou URL do seu servidor) ao fim da sessão",
    )
    grupo.add_argument(
        "--rescan",
        dest="rescan",
        metavar="SERVIDORES",
        default=None,
        help=(
            "pede ao servidor de música que reescaneie a biblioteca se baixou "
            "arquivo novo: navidrome, subsonic, plex, jellyfin (separe com vírgula)"
        ),
    )


# ----------------------------------------------------------------------------
# Textos e variáveis de ambiente
# ----------------------------------------------------------------------------
def _qualidade_texto(q: Mapping[str, Any] | None) -> str:
    q = q or {}
    formato = str(q.get("formato") or "").strip()
    bits, taxa = q.get("bit_depth"), q.get("sampling_rate")
    if bits and taxa:
        try:
            return f"{formato} {int(bits)}/{float(taxa):g}".strip()
        except (TypeError, ValueError):
            return formato
    return formato


def _duracao_texto(segundos: Any) -> str:
    try:
        total = int(float(segundos))
    except (TypeError, ValueError):
        return ""
    minutos, resto = divmod(total, 60)
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas} h {minutos:02d} min"
    if minutos:
        return f"{minutos} min {resto:02d} s"
    return f"{resto} s"


def variaveis_de_ambiente(ev: Mapping[str, Any]) -> dict[str, str]:
    """As variáveis QOBUZ_DL_* que o comando do usuário recebe."""
    resumo = ev.get("resumo") or {}

    def texto(valor: Any) -> str:
        return "" if valor is None else str(valor)

    dados = {
        "EVENTO": ev.get("evento"),
        "TITULO": ev.get("titulo"),
        "ARTISTA": ev.get("artista"),
        "PASTA": ev.get("pasta"),
        "ID": ev.get("id"),
        "TIPO": ev.get("tipo_lancamento") or ev.get("tipo"),
        "URL": ev.get("url"),
        "COMPLETO": "1" if ev.get("completo") else "0",
        "OK": "1" if ev.get("ok") else "0",
        "QUALIDADE": _qualidade_texto(ev.get("qualidade")),
        "FAIXAS_TOTAL": resumo.get("total"),
        "FAIXAS_OK": resumo.get("concluidas"),
        "FAIXAS_FALHAS": resumo.get("falhas", ev.get("faixas_falhas")),
        "FAIXAS_PULADAS": resumo.get("puladas", ev.get("faixas_puladas")),
        "FAIXAS_NOVAS": ev.get("faixas_novas"),
        "LANCAMENTOS": ev.get("lancamentos"),
        "DURACAO_S": ev.get("duracao_s"),
        "JSON": json.dumps(ev, ensure_ascii=False, separators=(",", ":"), default=str),
    }
    return {f"QOBUZ_DL_{k}": texto(v) for k, v in dados.items()}


def texto_evento(ev: Mapping[str, Any]) -> tuple[str, str, bool]:
    """(título, mensagem, deu_problema) para notificações."""
    nome = ev.get("evento")
    if nome == "lancamento_concluido":
        resumo = ev.get("resumo") or {}
        total, ok = resumo.get("total", 0), resumo.get("concluidas", 0)
        completo = bool(ev.get("completo"))
        titulo = (
            f"{'Concluído' if completo else 'Incompleto'}: {ev.get('titulo') or '?'}"
        )
        partes = [str(ev.get("artista") or "").strip(), f"{ok}/{total} faixas"]
        qual = _qualidade_texto(ev.get("qualidade"))
        if qual:
            partes.append(qual)
        return titulo, " · ".join(p for p in partes if p), not completo
    if nome == "sessao_concluida":
        falhas = int(ev.get("faixas_falhas") or 0)
        inc = int(ev.get("lancamentos_incompletos") or 0)
        problema = falhas > 0 or inc > 0 or not ev.get("ok", True)
        titulo = "Qobuz-DL: sessão concluída" + (" com falhas" if problema else "")
        partes = [
            f"{ev.get('lancamentos', 0)} lançamento(s)",
            f"{ev.get('faixas_novas', 0)} faixa(s) nova(s)",
        ]
        if falhas:
            partes.append(f"{falhas} falha(s)")
        if inc:
            partes.append(f"{inc} incompleto(s)")
        duracao = _duracao_texto(ev.get("duracao_s"))
        if duracao:
            partes.append(duracao)
        return titulo, " · ".join(partes), problema
    if nome == "faixa":
        situacao = str(ev.get("situacao") or "")
        return (
            f"{ev.get('artista') or ''} - {ev.get('titulo') or ''}".strip(" -"),
            situacao,
            situacao == "falha",
        )
    return str(nome or "evento"), json.dumps(ev, ensure_ascii=False, default=str), False


def _host(url: str) -> str:
    """Só o host, para mensagens: a URL inteira pode carregar token."""
    return urlparse(url).netloc or url


# ----------------------------------------------------------------------------
# Comando do usuário
# ----------------------------------------------------------------------------
_AVISOU_SEM_SUBPROCESSO = False


def _sem_subprocesso() -> bool:
    """True onde abrir um processo não é permitido. No iOS/a-Shell isso pode
    ENCERRAR o app inteiro (não levanta erro), então nem se tenta."""
    return sys.platform in {"ios", "emscripten", "wasi"}


def _matar(processo: Any) -> None:
    try:
        processo.kill()
    except ProcessLookupError:
        # Já terminou sozinho entre o prazo estourar e o kill.
        pass


async def executar_comando(
    comando: str, ev: Mapping[str, Any], timeout: float = 60.0
) -> bool:
    """Roda ``comando`` no shell com os dados do evento em QOBUZ_DL_* e o JSON
    do evento no stdin. Devolve True se saiu com código 0. Nunca levanta erro."""
    global _AVISOU_SEM_SUBPROCESSO
    if not str(comando or "").strip():
        return False
    ambiente = dict(os.environ)
    ambiente.update(variaveis_de_ambiente(ev))
    pasta = str(ev.get("pasta") or "")
    cwd = pasta if pasta and os.path.isdir(pasta) else None
    entrada = (
        json.dumps(ev, ensure_ascii=False, separators=(",", ":"), default=str) + "\n"
    ).encode("utf-8")
    if _sem_subprocesso():
        if not _AVISOU_SEM_SUBPROCESSO:
            _AVISOU_SEM_SUBPROCESSO = True
            _avisar(
                "Não consegui rodar o comando do hook neste ambiente "
                "(o iOS não abre processos). Use --webhook ou --ntfy no lugar."
            )
        return False
    try:
        processo = await asyncio.create_subprocess_shell(
            comando,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=ambiente,
            cwd=cwd,
        )
    except (OSError, NotImplementedError, ValueError) as exc:
        # iOS/a-Shell não cria processos; loop sem suporte a subprocess (Windows).
        if not _AVISOU_SEM_SUBPROCESSO:
            _AVISOU_SEM_SUBPROCESSO = True
            _avisar(
                "Não consegui rodar o comando do hook neste ambiente "
                f"({type(exc).__name__}). Use --webhook ou --ntfy no lugar."
            )
        logger.debug("subprocess indisponível: %s", exc)
        return False
    try:
        saida, erro = await asyncio.wait_for(
            processo.communicate(entrada), timeout=timeout
        )
    except asyncio.TimeoutError:
        _matar(processo)
        await processo.wait()
        _avisar(f"Hook passou de {timeout:g}s e foi interrompido: {comando[:60]}")
        return False
    except asyncio.CancelledError:
        _matar(processo)
        raise
    codigo = processo.returncode
    if saida:
        logger.debug("hook stdout: %s", saida.decode("utf-8", "replace")[:500])
    if codigo != 0:
        detalhe = erro.decode("utf-8", "replace").strip().splitlines()
        _avisar(
            f"Hook terminou com código {codigo}: {comando[:60]}"
            + (f" ({detalhe[-1][:120]})" if detalhe else "")
        )
        return False
    return True


# ----------------------------------------------------------------------------
# HTTP (webhook, ntfy e servidores de música)
# ----------------------------------------------------------------------------
def criar_cliente_http(timeout: float = 10.0) -> Any:
    """Cliente HTTP assíncrono (httpx é dependência do projeto; import tardio)."""
    import httpx

    return httpx.AsyncClient(
        timeout=timeout, follow_redirects=True, headers={"User-Agent": _AGENTE}
    )


async def _requisitar(
    cliente: Any, metodo: str, url: str, **kwargs: Any
) -> tuple[bool, str, Any]:
    """(ok, detalhe, resposta). O detalhe nunca leva a URL (ela pode ter token)."""
    try:
        resposta = await getattr(cliente, metodo)(url, **kwargs)
    except Exception as exc:
        logger.debug("Falha HTTP em %s: %s", _host(url), exc, exc_info=True)
        return False, type(exc).__name__, None
    status = int(getattr(resposta, "status_code", 0) or 0)
    return status < 400, f"HTTP {status}", resposta


async def enviar_webhook(
    cliente: Any, url: str, ev: Mapping[str, Any], segredo: str = ""
) -> bool:
    """POST do evento em JSON. Com ``segredo``, assina o corpo (HMAC-SHA256) no
    cabeçalho ``X-Qobuz-DL-Signature: sha256=<hex>``."""
    corpo = json.dumps(
        ev, ensure_ascii=False, separators=(",", ":"), default=str
    ).encode("utf-8")
    cabecalhos = {
        "Content-Type": "application/json",
        "X-Qobuz-DL-Event": str(ev.get("evento")),
    }
    if segredo:
        assinatura = hmac.new(
            segredo.encode("utf-8"), corpo, hashlib.sha256
        ).hexdigest()
        cabecalhos["X-Qobuz-DL-Signature"] = f"sha256={assinatura}"
    ok, detalhe, _ = await _requisitar(
        cliente, "post", url, content=corpo, headers=cabecalhos
    )
    if not ok:
        _avisar(f"Webhook falhou ({_host(url)}): {detalhe}")
    return ok


def separar_ntfy(destino: str) -> tuple[str, str]:
    """(servidor, tópico). ``meutopico`` -> ntfy.sh; ``https://meu.servidor/topico``
    -> o servidor do usuário."""
    destino = str(destino or "").strip()
    if not destino:
        return _NTFY_PADRAO, ""
    if "://" not in destino:
        return _NTFY_PADRAO, destino.strip("/")
    partes = urlparse(destino)
    caminho = partes.path.rstrip("/")
    prefixo, _, topico = caminho.rpartition("/")
    return f"{partes.scheme}://{partes.netloc}{prefixo}", topico


async def enviar_ntfy(
    cliente: Any, destino: str, ev: Mapping[str, Any], token: str = ""
) -> bool:
    """Notificação pelo formato JSON do ntfy (aceita acentos no título)."""
    servidor, topico = separar_ntfy(destino)
    if not topico:
        _avisar("ntfy: informe o tópico (ex.: --ntfy meu-topico).")
        return False
    titulo, mensagem, problema = texto_evento(ev)
    carga = {
        "topic": topico,
        "title": titulo,
        "message": mensagem or titulo,
        "priority": 4 if problema else 3,
        "tags": ["warning"] if problema else ["white_check_mark"],
    }
    cabecalhos = {"Authorization": f"Bearer {token}"} if token else {}
    ok, detalhe, _ = await _requisitar(
        cliente, "post", servidor, json=carga, headers=cabecalhos
    )
    if not ok:
        _avisar(f"ntfy falhou ({_host(servidor)}): {detalhe}")
    return ok


# ----------------------------------------------------------------------------
# Reescaneamento do servidor de música
# ----------------------------------------------------------------------------
def _base(url: str) -> str:
    return str(url or "").strip().rstrip("/")


async def _rescan_subsonic(cliente: Any, cfg: ConfigHooks) -> tuple[bool, str]:
    """Navidrome, Gonic e outros servidores Subsonic: ``/rest/startScan``."""
    if not (cfg.subsonic_url and cfg.subsonic_user and cfg.subsonic_password):
        return False, "faltam subsonic_url, subsonic_user ou subsonic_password"
    sal = secrets.token_hex(4)
    # MD5 aqui é exigência do protocolo Subsonic (token = md5(senha + sal)).
    token = hashlib.md5(
        (cfg.subsonic_password + sal).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    params = {
        "u": cfg.subsonic_user,
        "t": token,
        "s": sal,
        "v": "1.16.1",
        "c": _AGENTE,
        "f": "json",
    }
    ok, detalhe, resposta = await _requisitar(
        cliente, "get", f"{_base(cfg.subsonic_url)}/rest/startScan", params=params
    )
    if not ok:
        return False, detalhe
    try:
        corpo = resposta.json().get("subsonic-response", {})
    except (ValueError, AttributeError):
        return True, detalhe  # HTTP ok e corpo ilegível: não dá para dizer mais
    if corpo.get("status") == "ok":
        return True, "varredura iniciada"
    erro = (corpo.get("error") or {}).get("message", "resposta de erro")
    return False, str(erro)


async def _rescan_plex(cliente: Any, cfg: ConfigHooks) -> tuple[bool, str]:
    """Plex: ``GET /library/sections/<id>/refresh`` (uma ou mais seções)."""
    secoes = [s.strip() for s in cfg.plex_section.split(",") if s.strip()]
    if not (cfg.plex_url and cfg.plex_token and secoes):
        return False, (
            "faltam plex_url, plex_token ou plex_section (o número da biblioteca: "
            "veja /library/sections no seu servidor)"
        )
    falhas = []
    for secao in secoes:
        ok, detalhe, _ = await _requisitar(
            cliente,
            "get",
            f"{_base(cfg.plex_url)}/library/sections/{secao}/refresh",
            headers={"X-Plex-Token": cfg.plex_token},
        )
        if not ok:
            falhas.append(f"seção {secao}: {detalhe}")
    return (not falhas), ("; ".join(falhas) or f"{len(secoes)} seção(ões) em varredura")


async def _rescan_jellyfin(cliente: Any, cfg: ConfigHooks) -> tuple[bool, str]:
    """Jellyfin: ``POST /Library/Refresh`` (responde 204)."""
    if not (cfg.jellyfin_url and cfg.jellyfin_api_key):
        return False, "faltam jellyfin_url ou jellyfin_api_key"
    chave = cfg.jellyfin_api_key
    ok, detalhe, _ = await _requisitar(
        cliente,
        "post",
        f"{_base(cfg.jellyfin_url)}/Library/Refresh",
        headers={
            "Authorization": f'MediaBrowser Token="{chave}"',
            "X-Emby-Token": chave,
        },
    )
    return ok, ("varredura iniciada" if ok else detalhe)


_RESCAN: dict[str, Callable[[Any, ConfigHooks], Awaitable[tuple[bool, str]]]] = {
    "subsonic": _rescan_subsonic,
    "plex": _rescan_plex,
    "jellyfin": _rescan_jellyfin,
}
_NOMES_AMIGAVEIS = {
    "subsonic": "Navidrome/Subsonic",
    "plex": "Plex",
    "jellyfin": "Jellyfin",
}


async def reescanear(cliente: Any, cfg: ConfigHooks) -> dict[str, tuple[bool, str]]:
    """Pede o reescaneamento a cada servidor de ``cfg.rescan``. Cada um falha sozinho."""
    resultados: dict[str, tuple[bool, str]] = {}
    for nome in cfg.rescan:
        try:
            resultados[nome] = await _RESCAN[nome](cliente, cfg)
        except Exception as exc:
            logger.debug("Rescan %s falhou: %s", nome, exc, exc_info=True)
            resultados[nome] = (False, type(exc).__name__)
    return resultados


# ----------------------------------------------------------------------------
# Cola com o programa
# ----------------------------------------------------------------------------
class Automacao:
    """Escuta os eventos e dispara os hooks configurados."""

    def __init__(
        self,
        cfg: ConfigHooks,
        cliente_factory: Callable[[float], Any] | None = None,
    ) -> None:
        self.cfg = cfg
        self._factory = cliente_factory or criar_cliente_http
        self._cliente: Any = None
        self._tarefas: set[asyncio.Task[Any]] = set()
        # Um de cada vez e na ordem: um script do usuário nunca roda em cima de outro.
        self._fila = asyncio.Semaphore(1)
        self._ev_sessao: dict[str, Any] | None = None

    # -- ciclo de vida ------------------------------------------------------
    def iniciar(self) -> None:
        events.assinar(self._ao_evento)

    async def _obter_cliente(self) -> Any:
        if self._cliente is None:
            self._cliente = self._factory(self.cfg.timeout_http)
        return self._cliente

    async def _fechar_cliente(self) -> None:
        cliente, self._cliente = self._cliente, None
        if cliente is not None and hasattr(cliente, "aclose"):
            try:
                await cliente.aclose()
            except Exception as exc:
                logger.debug("Falha ao fechar o cliente HTTP: %s", exc)

    # -- recepção dos eventos ----------------------------------------------
    def _ao_evento(self, ev: dict[str, Any]) -> None:
        nome = ev.get("evento")
        if nome == "sessao_concluida":
            self._ev_sessao = ev  # tratado em finalizar(), depois do rescan
            return
        if nome == "lancamento_concluido":
            comando = self.cfg.on_complete if ev.get("completo") else self.cfg.on_error
            if comando:
                self._agendar(self._rodar_comando, comando, ev)
        if self.cfg.webhook_url and nome in self.cfg.webhook_eventos:
            self._agendar(self._rodar_webhook, ev)
        if self.cfg.ntfy and nome in self.cfg.ntfy_eventos:
            self._agendar(self._rodar_ntfy, ev)

    def _agendar(self, funcao: Callable[..., Awaitable[Any]], *args: Any) -> None:
        """Agenda ``funcao(*args)``. Recebe a função (e não a corrotina pronta)
        para que uma tarefa cancelada antes de começar não deixe corrotina
        esquecida sem ``await``."""
        try:
            tarefa = asyncio.get_running_loop().create_task(
                self._proteger(funcao, *args)
            )
        except RuntimeError:
            # Sem event loop rodando (chamada de outra thread): não há como agendar.
            logger.debug("Hook ignorado: sem event loop ativo")
            return
        self._tarefas.add(tarefa)
        tarefa.add_done_callback(self._tarefas.discard)

    async def _proteger(
        self, funcao: Callable[..., Awaitable[Any]], *args: Any
    ) -> None:
        try:
            await funcao(*args)
        except Exception as exc:
            # Hook com defeito não derruba o download: vira aviso no log.
            logger.warning("Hook falhou: %s", exc, exc_info=True)

    async def _rodar_comando(self, comando: str, ev: dict[str, Any]) -> None:
        async with self._fila:
            await executar_comando(comando, ev, timeout=self.cfg.timeout_comando)

    async def _rodar_webhook(self, ev: dict[str, Any]) -> None:
        async with self._fila:
            cliente = await self._obter_cliente()
            await enviar_webhook(
                cliente, self.cfg.webhook_url, ev, self.cfg.webhook_segredo
            )

    async def _rodar_ntfy(self, ev: dict[str, Any]) -> None:
        async with self._fila:
            cliente = await self._obter_cliente()
            await enviar_ntfy(cliente, self.cfg.ntfy, ev, self.cfg.ntfy_token)

    async def _esperar(self) -> None:
        while self._tarefas:
            await asyncio.gather(*list(self._tarefas), return_exceptions=True)

    # -- fim da sessão ---------------------------------------------------------
    async def finalizar(self, ok: bool, interrompida: bool = False) -> None:
        """Fecha a sessão: emite o evento final e roda o que é "uma vez só".

        Interrompida (Ctrl+C): só registra o evento; sem rede nem comandos, para
        o programa sair na hora.
        """
        events.sessao_concluida(ok, interrompida)
        ev = self._ev_sessao
        try:
            if interrompida:
                for tarefa in list(self._tarefas):
                    tarefa.cancel()
                return
            await self._esperar()
            if ev is None or not ev.get("lancamentos"):
                return  # nada foi processado: sem rescan, sem aviso
            await self._rodar_fim(ev)
        finally:
            events.desassinar(self._ao_evento)
            await self._fechar_cliente()

    async def _rodar_fim(self, ev: dict[str, Any]) -> None:
        """O que roda uma vez só. Cada passo falha sozinho: um webhook fora do ar
        não pula o ntfy nem o --on-finish."""
        cfg = self.cfg
        if cfg.rescan:
            await self._proteger(self._passo_rescan, ev)
        if cfg.on_finish:
            await self._proteger(self._rodar_comando, cfg.on_finish, ev)
        if cfg.webhook_url and "sessao_concluida" in cfg.webhook_eventos:
            await self._proteger(self._rodar_webhook, ev)
        if cfg.ntfy and "sessao_concluida" in cfg.ntfy_eventos:
            await self._proteger(self._rodar_ntfy, ev)

    async def _passo_rescan(self, ev: dict[str, Any]) -> None:
        if int(ev.get("faixas_novas") or 0) <= 0:
            logger.debug("Rescan pulado: nenhum arquivo novo nesta sessão")
            return
        cliente = await self._obter_cliente()
        for nome, (ok, detalhe) in (await reescanear(cliente, self.cfg)).items():
            rotulo = _NOMES_AMIGAVEIS.get(nome, nome)
            if ok:
                _informar(f"{rotulo}: {detalhe}")
            else:
                _avisar(f"{rotulo}: reescaneamento falhou ({detalhe})")


# ----------------------------------------------------------------------------
# Pontos de entrada usados pelo cli.py
# ----------------------------------------------------------------------------
def preparar(
    arguments: Any,
    config: Any,
    section: str = "qobuz",
    versao: str = "",
    ambiente: Mapping[str, str] | None = None,
) -> Automacao | None:
    """Liga eventos e hooks conforme a configuração. Devolve None (e não custa
    nada) quando o comando não baixa ou nada foi configurado."""
    comando = getattr(arguments, "command", None)
    if comando not in COMANDOS_DE_DOWNLOAD:
        return None
    cfg = carregar(arguments, config, section, ambiente)
    if not cfg.algo_ligado():
        return None
    events.reiniciar()
    if cfg.eventos_arquivo:
        events.configurar(cfg.eventos_arquivo)
    automacao = Automacao(cfg)
    automacao.iniciar()
    events.sessao_iniciada(comando, versao)
    return automacao


async def encerrar(
    automacao: Automacao | None, ok: bool, interrompida: bool = False
) -> None:
    """Fecha a sessão (hooks "uma vez só") e o arquivo de eventos. Nunca levanta erro."""
    if automacao is None:
        return
    try:
        await automacao.finalizar(ok, interrompida)
    except Exception as exc:
        logger.warning("Falha ao encerrar os hooks: %s", exc, exc_info=True)
    finally:
        events.fechar()
