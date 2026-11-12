# ==============================================================================
# MÓDULO: events.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Eventos de progresso em JSON, uma linha por evento (NDJSON).
#
# Serve para ligar o programa a um painel, a um Atalho do iOS, a um script ou a
# um `tail -f`, sem precisar de interface web. Ligado por `--events ARQUIVO`
# (ou `events_file` no config.ini / QOBUZ_DL_EVENTS_FILE). Desligado por padrão.
#
# Este módulo só usa a biblioteca padrão e NUNCA levanta erro: um destino de
# eventos quebrado (disco cheio, pasta sem permissão) vira aviso no log e o
# download segue. Quem emite os eventos é o `postprocess.py`, porque as funções
# `init_report`, `update_track_status` e `finalize_report` já são o funil por
# onde passa toda faixa e todo lançamento (álbum, faixa avulsa e playlist).
#
# Contrato (também em docs/EVENTOS_E_HOOKS.md): cada linha é um objeto JSON com
# `v` (versão do formato), `ts` (ISO 8601 em UTC) e `evento`. Eventos:
#   sessao_iniciada     comando, versao
#   lancamento_iniciado pasta, tipo, titulo, artista, id, faixas
#   faixa               pasta, numero, id, titulo, artista, situacao, motivo, nova
#   lancamento_concluido pasta, tipo, titulo, artista, id, completo, resumo, ...
#   sessao_concluida    ok, duracao_s, lancamentos, faixas_novas, faixas_falhas...
# ==============================================================================

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, TextIO

logger = logging.getLogger(__name__)

# Sobe quando um campo existente muda de significado (campo novo não conta).
VERSAO_ESQUEMA = 1

# Mesmo texto que o downloader grava como `motivo` quando a faixa já estava no
# disco (downloader.py, `outcome == "existing"`). É o que separa "baixou agora"
# de "já tinha": só a primeira conta como arquivo novo (reescanear a biblioteca
# do servidor de música sem ter arquivo novo seria barulho à toa).
# tests/unit/test_events.py confere que o texto ainda existe no downloader.
MOTIVO_EXISTENTE = "Arquivo já existente"

EVENTOS_CONHECIDOS = (
    "sessao_iniciada",
    "lancamento_iniciado",
    "faixa",
    "lancamento_concluido",
    "sessao_concluida",
)

_SITUACAO_CONCLUIDA = {"ok", "complete", "completo", "concluido"}


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


# O downloader renomeia a pasta do álbum durante o trabalho
# ("[IN PROGRESS] X" -> "X", ou "[INCOMPLETE] X" se falhou). Para somar as
# faixas e anunciar o álbum uma vez só, a chave ignora esses prefixos.
_PREFIXOS_DE_PASTA = (
    "[IN PROGRESS] ",
    "[IN PROGRESS]",
    "[INCOMPLETE] ",
    "[INCOMPLETE]",
)


def _chave_pasta(pasta: str) -> str:
    caminho = os.path.abspath(str(pasta))
    topo, nome = os.path.split(caminho)
    for prefixo in _PREFIXOS_DE_PASTA:
        if nome.startswith(prefixo):
            nome = nome[len(prefixo) :]
            break
    return os.path.join(topo, nome)


def _situacao(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    return "concluido" if texto in _SITUACAO_CONCLUIDA else (texto or "pendente")


class _Estado:
    """Tudo o que muda durante a sessão. Um objeto só, para os testes poderem
    zerar (``reiniciar``) sem mexer em vários globais."""

    def __init__(self) -> None:
        self.trava = threading.RLock()
        self.arquivo: TextIO | None = None
        self.fecha_arquivo = False
        self.destino = ""
        self.assinantes: list[Callable[[dict[str, Any]], None]] = []
        self.anunciados: set[str] = set()
        self.ultimo_fim: dict[str, tuple] = {}
        # (pasta, id da faixa) -> (situacao, nova): o estado FINAL de cada faixa.
        self.faixas: dict[tuple[str, str], tuple[str, bool]] = {}
        self.lancamentos: list[dict[str, Any]] = []
        self.inicio = time.monotonic()


_estado = _Estado()


def reiniciar() -> None:
    """Fecha o destino e zera contadores, assinantes e anúncios (início de sessão e testes)."""
    fechar()
    with _estado.trava:
        _estado.assinantes = []
        _estado.anunciados = set()
        _estado.ultimo_fim = {}
        _estado.faixas = {}
        _estado.lancamentos = []
        _estado.inicio = time.monotonic()
        _estado.destino = ""


def configurar(destino: str | None) -> bool:
    """Liga a gravação em ``destino``: caminho de arquivo (modo append) ou ``-``
    para o stderr. ``None``/vazio desliga. Devolve True se ficou gravando.

    Vai para o stderr (e não para o stdout) porque o stdout é da tela do
    programa; misturar os dois estragaria quem lê o JSON.
    """
    fechar()
    destino = (destino or "").strip()
    if not destino or destino.lower() in {"off", "no", "false", "0"}:
        return False
    with _estado.trava:
        _estado.destino = destino
        if destino == "-":
            _estado.arquivo = sys.stderr
            _estado.fecha_arquivo = False
            return True
        try:
            caminho = os.path.abspath(os.path.expanduser(destino))
            pasta = os.path.dirname(caminho)
            if pasta:
                os.makedirs(pasta, exist_ok=True)
            # Linha a linha: `tail -f` e leitores de stream veem cada evento na hora.
            _estado.arquivo = open(caminho, "a", encoding="utf-8", buffering=1)
            _estado.fecha_arquivo = True
            return True
        except OSError as exc:
            logger.warning(
                "Eventos desligados: não consegui abrir %s (%s)", destino, exc
            )
            _estado.arquivo = None
            return False


def fechar() -> None:
    """Fecha o arquivo de eventos (se for um arquivo nosso). Pode chamar à vontade."""
    with _estado.trava:
        arquivo, fechar_ao_fim = _estado.arquivo, _estado.fecha_arquivo
        _estado.arquivo = None
        _estado.fecha_arquivo = False
    if arquivo is not None and fechar_ao_fim:
        try:
            arquivo.close()
        except OSError as exc:
            logger.debug("Falha ao fechar o arquivo de eventos: %s", exc)


def gravando() -> bool:
    return _estado.arquivo is not None


def assinar(funcao: Callable[[dict[str, Any]], None]) -> None:
    """Registra uma função chamada (de forma síncrona) a cada evento. Os hooks usam isto."""
    with _estado.trava:
        if funcao not in _estado.assinantes:
            _estado.assinantes.append(funcao)


def desassinar(funcao: Callable[[dict[str, Any]], None]) -> None:
    with _estado.trava:
        if funcao in _estado.assinantes:
            _estado.assinantes.remove(funcao)


def emitir(evento: str, **campos: Any) -> dict[str, Any]:
    """Monta o evento, grava uma linha e avisa os assinantes. Nunca levanta erro."""
    carga: dict[str, Any] = {"v": VERSAO_ESQUEMA, "ts": _agora(), "evento": evento}
    carga.update(campos)
    try:
        with _estado.trava:
            arquivo = _estado.arquivo
            assinantes = list(_estado.assinantes)
            if arquivo is not None:
                linha = json.dumps(
                    carga, ensure_ascii=False, separators=(",", ":"), default=str
                )
                try:
                    arquivo.write(linha + "\n")
                    arquivo.flush()
                except (OSError, ValueError) as exc:
                    logger.warning("Eventos desligados: falha ao gravar (%s)", exc)
                    _estado.arquivo = None
        for funcao in assinantes:
            try:
                funcao(carga)
            except Exception as exc:
                # Um assinante com defeito não pode derrubar o download.
                logger.debug("Assinante de eventos falhou: %s", exc, exc_info=True)
    except Exception as exc:
        logger.debug("Falha ao emitir evento %s: %s", evento, exc, exc_info=True)
    return carga


# ----------------------------------------------------------------------------
# Eventos prontos (é o que o resto do programa chama)
# ----------------------------------------------------------------------------
def sessao_iniciada(comando: str | None, versao: str = "") -> None:
    with _estado.trava:
        _estado.inicio = time.monotonic()
    emitir("sessao_iniciada", comando=comando or "interactive", versao=versao)


def lancamento_iniciado(
    pasta: str,
    tipo: str = "",
    titulo: str = "",
    artista: str = "",
    item_id: str = "",
    faixas: int | None = None,
) -> None:
    """Anuncia o lançamento UMA vez por pasta e por execução (retomar um álbum
    interrompido, ou chamar de novo por faixa de playlist, não repete)."""
    chave = _chave_pasta(pasta)
    with _estado.trava:
        if chave in _estado.anunciados:
            return
        _estado.anunciados.add(chave)
    emitir(
        "lancamento_iniciado",
        pasta=pasta,
        tipo=tipo,
        titulo=titulo,
        artista=artista,
        id=item_id,
        faixas=faixas,
    )


def faixa(
    pasta: str,
    numero: Any,
    item_id: Any,
    titulo: str,
    artista: Any = "",
    situacao: str = "",
    motivo: str = "",
) -> None:
    estado = _situacao(situacao)
    ja_existia = MOTIVO_EXISTENTE.lower() in (motivo or "").lower()
    nova = estado == "concluido" and not ja_existia
    with _estado.trava:
        _estado.faixas[(_chave_pasta(pasta), str(item_id))] = (estado, nova)
    emitir(
        "faixa",
        pasta=pasta,
        numero=numero,
        id=item_id,
        titulo=titulo,
        artista=artista if isinstance(artista, str) else str(artista or ""),
        situacao=estado,
        motivo=motivo or "",
        nova=nova,
    )


def lancamento_concluido(pasta: str, completo: bool, relatorio: dict[str, Any]) -> None:
    """Fim de um álbum, faixa avulsa ou playlist. ``relatorio`` é o dict do
    `.report.json` (de onde saem título, artista e contagens)."""
    ident = relatorio.get("identificacao") or {}
    resumo = (relatorio.get("progresso") or {}).get("resumo") or {}
    qualidade = relatorio.get("qualidade") or {}
    chave = _chave_pasta(pasta)
    assinatura = (bool(completo), tuple(sorted(resumo.items())))
    with _estado.trava:
        # finalize_report pode ser chamado de novo para a mesma pasta, com o
        # mesmo resultado (faixa avulsa dentro de lote): não repete o evento.
        if _estado.ultimo_fim.get(chave) == assinatura:
            return
        _estado.ultimo_fim[chave] = assinatura
        novas = sum(
            1 for (p, _i), (_s, nova) in _estado.faixas.items() if p == chave and nova
        )
        item = {
            "pasta": pasta,
            "titulo": ident.get("titulo", ""),
            "artista": ident.get("artista", ""),
            "completo": bool(completo),
            "faixas_novas": novas,
        }
        _estado.lancamentos.append(item)
    emitir(
        "lancamento_concluido",
        pasta=pasta,
        tipo=relatorio.get("tipo", ""),
        tipo_lancamento=ident.get("tipo_lancamento", ""),
        titulo=ident.get("titulo", ""),
        artista=ident.get("artista", ""),
        id=ident.get("id", ""),
        url=ident.get("url", ""),
        completo=bool(completo),
        resumo=resumo,
        faixas_novas=novas,
        qualidade={
            "formato": qualidade.get("formato", ""),
            "bit_depth": qualidade.get("bit_depth"),
            "sampling_rate": qualidade.get("sampling_rate"),
        },
    )


def resumo_sessao() -> dict[str, Any]:
    """Totais da sessão até agora (para o evento final e para os hooks)."""
    with _estado.trava:
        estados = list(_estado.faixas.values())
        lancamentos = list(_estado.lancamentos)
        duracao = time.monotonic() - _estado.inicio
    return {
        "duracao_s": round(duracao, 1),
        "lancamentos": len(lancamentos),
        "lancamentos_incompletos": sum(1 for x in lancamentos if not x["completo"]),
        "faixas_novas": sum(1 for _s, nova in estados if nova),
        "faixas_falhas": sum(1 for s, _n in estados if s == "falha"),
        "faixas_puladas": sum(1 for s, _n in estados if s == "pulada"),
    }


def sessao_concluida(ok: bool, interrompida: bool = False) -> dict[str, Any]:
    resumo = resumo_sessao()
    emitir("sessao_concluida", ok=bool(ok), interrompida=bool(interrompida), **resumo)
    return resumo
