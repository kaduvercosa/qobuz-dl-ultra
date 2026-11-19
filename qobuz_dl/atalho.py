# ==============================================================================
# MÓDULO: atalho.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Modo "Atalho do iOS": faz o download rodar dentro da ação
#            "Execute Long Command" do a-Shell e avisa cada etapa no banner do
#            iOS (barra de progresso e Dynamic Island).
#
# Como funciona
#   O "Execute Long Command" só devolve o texto da saída no FIM. Durante a
#   execução, o único canal ao vivo é o comando nativo `progress` do a-Shell
#   (`progress 4/36`, `progress --title "..."`, `progress --done`). Este módulo
#   assina os eventos de `events.py` (faixa, álbum, sessão), junta tudo num
#   título e numa fração e chama `progress`.
#
#   O banner mostra ~30 caracteres e o iOS já escreve "N of M" embaixo; por isso
#   o título gira a cada 3 s entre: andamento, etapa atual e nome do item. Uma
#   falha fica parada ~8 s antes de o rodízio voltar.
#
#   Ligado por `--atalho` (ou QOBUZ_DL_ATALHO=1). Desligado por padrão.
#
# Anel de progresso (downloads grandes)
#   O iOS pode cancelar o Long Command que fica muito tempo sem reportar
#   progresso (no Atalho aparece como erro ou `timeout`, e o download segue vivo
#   no a-Shell). Por isso cada faixa vale ESCALA_PADRAO (1000) unidades:
#     * os bytes baixados andam de 0 a 85 % da faixa (`progresso_bytes.py`);
#     * os 15 % finais são RESERVA para o pós-processamento (conversão, tags,
#       capa, letras), que não baixa nada mas pode demorar;
#     * se nada mexer por BATIDA_PADRAO_S (8 s), o anel avança 1 unidade, SEMPRE
#       (mesmo com o progresso real parado ou atrás do anel); se a reserva
#       acabar, o total cresce (o `progress` permite) para o anel nunca parar;
#     * o anel nunca volta atrás e só fecha quando todas as faixas ficam prontas.
#   QOBUZ_DL_ATALHO_ESCALA=1 volta a contar só faixas; QOBUZ_DL_ATALHO_BATIDA_S=0
#   desliga o avanço de 8 s.
#
# Regras de segurança do download
#   * Só o iOS chama `progress` (sys.platform == "ios"); em outros sistemas o
#     modo só muda o texto final, o que ajuda a testar fora do iPhone.
#   * `progress` só é chamado pela thread do laço de eventos (nunca por thread
#     de download): o ios_system guarda diretório e pid do processo inteiro.
#   * No máximo uma atualização a cada 0,5 s e só quando algo mudou.
#   * Se `progress` não existir, o aviso some sozinho e o download segue.
#   * Nada aqui levanta erro para o download: toda falha vira log de depuração.
#
# Saída: no modo atalho o terminal fica silencioso (como --quiet, sem cores);
# avisos e erros continuam saindo. No fim sai UM resumo em texto puro: status e
# totais na primeira linha (é o que a notificação mostra), depois blocos por
# tipo entre colchetes ([ ÁLBUM ], [ PLAYLIST ], [ FAIXAS AVULSAS ]). A linha
# JSON só sai com `--atalho-json`.
#
# Teste sem baixar nada:  python3 -m qobuz_dl.atalho
# ==============================================================================

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import shlex
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping

from qobuz_dl import _partida, events, progresso_bytes, ui, vigia
from qobuz_dl.fila import encerrar_andamento, link_curto, publicar_andamento

logger = logging.getLogger(__name__)

# Comandos que baixam (mesma lista dos hooks).
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

_VERDADEIRO = frozenset({"1", "true", "yes", "on", "sim", "s"})

INTERVALO_S = 0.5  # no máximo 2 atualizações do banner por segundo
ROTACAO_S = 3.0  # tempo de cada "tela" do título
AVISO_S = 8.0  # quanto tempo uma falha fica parada no banner
TITULO_MAX = 32  # o banner do iOS corta em ~30 caracteres
ETAPA_MAX = 32
NOME_MAX = 40  # nomes no resumo final
FALHAS_POR_ALBUM = 5  # quantas faixas com falha listar no resumo de cada bloco
ENV_ESCALA = "QOBUZ_DL_ATALHO_ESCALA"
ESCALA_PADRAO = 1000  # unidades do anel por faixa (1 = só conta faixas prontas)
FRACAO_BYTES_ANEL = 0.85  # parte da faixa que os bytes ocupam; o resto é reserva
ESCALA_MAX = 1000
ENV_BATIDA = "QOBUZ_DL_ATALHO_BATIDA_S"
BATIDA_PADRAO_S = 8.0  # anel parado por tanto tempo anda 1 unidade (0 desliga)

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_PREFIXO_VISUAL = re.compile(r"^[\s│├└┌┐┘┤┬┴┼─━═║╭╮╯╰▸▶►•|`+\-]+")
_PREFIXO_TAG = re.compile(r"^\[[^\]]{1,3}\]\s*")
_LINK_CONHECIDO = re.compile(r"/(album|track|playlist)/", re.IGNORECASE)

_SITUACOES_FINAIS = frozenset({"concluido", "pulada", "falha"})
_ROTULOS = {"playlist": "Playlist", "faixa": "Faixa"}

# Marcas do resumo final (texto puro; o banner já usa o mesmo ⚠).
MARCA_OK = "✓"
MARCA_ATENCAO = "⚠"
RECUO = "  "  # detalhes ficam alinhados sob o nome, depois da marca
_LINK_FAIXA = re.compile(r"/track/", re.IGNORECASE)


# ----------------------------------------------------------------------------
# Ligar / desligar
# ----------------------------------------------------------------------------
def _verdadeiro(valor: Any) -> bool:
    return str(valor or "").strip().lower() in _VERDADEIRO


def ligado(arguments: Any, ambiente: Mapping[str, str] | None = None) -> bool:
    """True se o modo atalho foi pedido: flag `--atalho` ou QOBUZ_DL_ATALHO."""
    if bool(getattr(arguments, "atalho", False)):
        return True
    amb = os.environ if ambiente is None else ambiente
    return _verdadeiro(amb.get("QOBUZ_DL_ATALHO", ""))


def aplicavel(arguments: Any, ambiente: Mapping[str, str] | None = None) -> bool:
    """Ligado E num comando que baixa (um `stats` nunca fica mudo por causa do
    ambiente)."""
    comando = getattr(arguments, "command", None)
    return comando in COMANDOS_DE_DOWNLOAD and ligado(arguments, ambiente)


def escala_do_ambiente(ambiente: Mapping[str, str] | None = None) -> int:
    """Unidades do anel por faixa: `QOBUZ_DL_ATALHO_ESCALA` (1 = só faixas prontas)."""
    env = os.environ if ambiente is None else ambiente
    bruto = str(env.get(ENV_ESCALA, "")).strip()
    if not bruto:
        return ESCALA_PADRAO
    try:
        valor = int(float(bruto.replace(",", ".")))
    except (ValueError, OverflowError):
        return ESCALA_PADRAO
    return min(max(valor, 1), ESCALA_MAX)


def batida_do_ambiente(ambiente: Mapping[str, str] | None = None) -> float:
    """Segundos de anel parado até ele andar 1 unidade: `QOBUZ_DL_ATALHO_BATIDA_S`
    (0 desliga)."""
    env = os.environ if ambiente is None else ambiente
    bruto = str(env.get(ENV_BATIDA, "")).strip()
    if not bruto:
        return BATIDA_PADRAO_S
    try:
        valor = float(bruto.replace(",", "."))
    except ValueError:
        return BATIDA_PADRAO_S
    if not math.isfinite(valor):
        return BATIDA_PADRAO_S
    return max(valor, 0.0)


def adicionar_args(parser: Any) -> None:
    """Acrescenta `--atalho` a um subcomando de download."""
    grupo = parser.add_argument_group("atalho do iOS")
    grupo.add_argument(
        "--atalho",
        dest="atalho",
        action="store_true",
        default=False,
        help=(
            "modo Atalho do iOS (a-Shell 'Execute Long Command'): avisa cada etapa "
            "no banner do iOS, deixa o terminal silencioso e devolve um resumo "
            "em texto puro no fim"
        ),
    )
    grupo.add_argument(
        "--atalho-json",
        dest="atalho_json",
        action="store_true",
        default=False,
        help=(
            "com --atalho: acrescenta ao resumo final uma linha JSON "
            '({"ok":true,"albuns":3,"novas":35,"falhas":0}) para o Atalho ler'
        ),
    )


def esperados_de_fontes(fontes: Iterable[Any] | None) -> int:
    """Quantos lançamentos os links prometem, ou 0 se não dá para saber.

    Só conta quando TODOS os itens são links de álbum, faixa ou playlist: uma
    página de artista, gravadora ou um arquivo de lista rende um número que o
    programa só descobre durante o download.
    """
    if not fontes:
        return 0
    itens = [str(f).strip() for f in fontes if str(f).strip()]
    if not itens or not all(_LINK_CONHECIDO.search(i) for i in itens):
        return 0
    return len(itens)


def faixas_esperadas_de_fontes(fontes: Iterable[Any] | None) -> int:
    """Quantas faixas os links prometem, se TODOS forem links de faixa.

    Com 4 links de faixa o total é 4 desde o início; com álbuns ou playlists
    misturados o programa só descobre o total durante o download.
    """
    if not fontes:
        return 0
    itens = [str(f).strip() for f in fontes if str(f).strip()]
    if not itens or not all(_LINK_FAIXA.search(i) for i in itens):
        return 0
    return len(itens)


# ----------------------------------------------------------------------------
# Texto
# ----------------------------------------------------------------------------
def limpar_linha(texto: Any, limite: int = ETAPA_MAX) -> str:
    """Última linha com conteúdo de ``texto``, sem cores, sem moldura e curta.

    É o que o terminal mostraria (``  ├─ Baixando: Faixa``) reduzido ao que o
    banner do iOS consegue exibir.
    """
    for linha in reversed(_ANSI.sub("", str(texto or "")).splitlines()):
        linha = _PREFIXO_VISUAL.sub("", linha.strip())
        linha = _PREFIXO_TAG.sub("", linha)
        linha = _CONTROLE.sub(" ", linha)
        linha = re.sub(r"\s+", " ", linha).strip()
        if sum(1 for c in linha if c.isalnum()) >= 3:
            if len(linha) > limite:
                linha = linha[: limite - 1].rstrip() + "…"
            return linha
    return ""


def _curto(texto: str, limite: int) -> str:
    texto = " ".join(str(texto).split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def _duracao_texto(segundos: float | None) -> str:
    if segundos is None or segundos < 0:
        return ""
    total = int(segundos)
    minutos, resto = divmod(total, 60)
    horas, minutos = divmod(minutos, 60)
    if horas:
        return f"{horas} h {minutos:02d} min"
    if minutos:
        return f"{minutos} min {resto:02d} s"
    return f"{resto} s"


def _plural(n: int, singular: str, plural: str) -> str:
    return f"{n} {singular if n == 1 else plural}"


def etiqueta(tipo: str) -> str:
    """O tipo entre colchetes, em maiúsculas: ``[ ÁLBUM ]``, ``[ PLAYLIST ]``."""
    return f"[ {' '.join(str(tipo).split()).upper()} ]"


def _executar_progress(comando: str) -> int:
    """Roda um comando nativo do a-Shell (o iOS não abre processos)."""
    return int(os.system(f"{comando} >/dev/null 2>&1") or 0)


# ----------------------------------------------------------------------------
# Estado
# ----------------------------------------------------------------------------
@dataclass
class _Lancamento:
    chave: str
    pasta: str
    indice: int
    tipo: str = ""
    titulo: str = ""
    artista: str = ""
    faixas: int = 0
    inicio: float = 0.0
    fim: float | None = None
    completo: bool | None = None
    novas: int = 0
    resumo: dict[str, Any] = field(default_factory=dict)
    terminais: dict[str, str] = field(default_factory=dict)  # id da faixa -> situação
    falhas: dict[str, str] = field(default_factory=dict)  # id da faixa -> "03 Título"

    @property
    def rotulo(self) -> str:
        return _ROTULOS.get(self.tipo.strip().lower(), "Álbum")

    @property
    def nome(self) -> str:
        partes = [p for p in (self.artista, self.titulo) if p]
        return " — ".join(partes) if partes else os.path.basename(self.pasta)


class Atalho:
    """Assina os eventos e mantém o banner do iOS e o resumo final."""

    def __init__(
        self,
        executar: Callable[[str], int] | None = None,
        esperados: int = 0,
        intervalo: float = INTERVALO_S,
        relogio: Callable[[], float] = time.monotonic,
        faixas_esperadas: int = 0,
        com_json: bool = False,
        rotacao: float = ROTACAO_S,
        inicio_processo: float | None = None,
        escala: int | None = None,
        batida: float | None = None,
    ) -> None:
        self.esperados = max(0, int(esperados))  # lançamentos prometidos pelos links
        self.faixas_esperadas = max(0, int(faixas_esperadas))
        self.com_json = com_json
        self.intervalo = intervalo
        self.rotacao = rotacao
        self._relogio = relogio
        self._inicio = relogio()
        # Os segundos da partida contam desde o início do processo (não deste
        # objeto): o banner continua de onde `_partida.py` parou.
        self._t0 = self._inicio if inicio_processo is None else inicio_processo
        self.fase = "lendo o link"  # etapa mostrada até o 1º lançamento
        # Fora do iOS não há `progress`: o modo só muda o texto.
        if executar is None and sys.platform == "ios":
            executar = _executar_progress
        self._executar = executar
        self._trava = threading.Lock()
        self._lancs: dict[str, _Lancamento] = {}
        self._atual: str = ""
        self._etapa: str = ""
        self._aviso: str = ""
        self._aviso_ate: float = 0.0
        self._feitas_max = 0
        self._total_max = 0
        self._ultimo_titulo: str | None = None
        self._ultima_fracao: tuple[int, int] | None = None
        # Anel do iOS em unidades finas (ver `_anel_locked`).
        self.escala = (
            escala_do_ambiente()
            if escala is None
            else max(1, min(int(escala), ESCALA_MAX))
        )
        self.batida = batida_do_ambiente() if batida is None else max(0.0, batida)
        self._anel_n = 0  # maiores valores REAIS vistos: nunca voltam atrás
        self._anel_m = 0
        self._anel_parado_desde = self._inicio
        self._batidas = 0  # unidades que a batida já somou à parada atual
        self._anel_enviado: tuple[int, int] = (0, 0)
        self._progress_ok = False  # algum `progress` já funcionou
        self.travamento = ""  # preenchido pelo vigia se o download parou
        self.vigia: vigia.Vigia | None = None
        self._sessao: dict[str, Any] = {}
        self._tarefa: asyncio.Task[None] | None = None
        # Fila de links (`dl --atalho @fila`): só preenchido nesse modo.
        self.fila_ativa = False
        self._fila_indice = 0
        self._fila_total = 0
        self._fila_espera = 0
        self._fila_falharam: list[str] = []
        self.emite_sessao = False  # True quando nenhum hook abriu a sessão de eventos

    # -- eventos -----------------------------------------------------------
    def ao_evento(self, ev: Mapping[str, Any]) -> None:
        """Assinante de `events.assinar`. Roda na thread de quem emitiu."""
        try:
            nome = ev.get("evento")
            with self._trava:
                if nome == "lancamento_iniciado":
                    self._lancamento_iniciado(ev)
                elif nome == "faixa":
                    self._faixa(ev)
                elif nome == "lancamento_concluido":
                    self._lancamento_concluido(ev)
                elif nome == "sessao_concluida":
                    self._sessao = dict(ev)
        except Exception as exc:
            logger.debug("atalho: evento ignorado (%s)", exc, exc_info=True)

    def _obter(self, pasta: str) -> _Lancamento:
        chave = events._chave_pasta(pasta)
        lanc = self._lancs.get(chave)
        if lanc is None:
            lanc = _Lancamento(
                chave=chave,
                pasta=chave,
                indice=len(self._lancs) + 1,
                inicio=self._relogio(),
            )
            self._lancs[chave] = lanc
        self._atual = chave
        return lanc

    def _lancamento_iniciado(self, ev: Mapping[str, Any]) -> None:
        lanc = self._obter(str(ev.get("pasta") or ""))
        lanc.tipo = str(ev.get("tipo") or lanc.tipo)
        lanc.titulo = str(ev.get("titulo") or lanc.titulo)
        lanc.artista = str(ev.get("artista") or lanc.artista)
        lanc.faixas = max(lanc.faixas, int(ev.get("faixas") or 0))
        self._etapa = ""

    def _avisar(self, texto: str) -> None:
        self._aviso = texto
        self._aviso_ate = self._relogio() + AVISO_S

    def _faixa(self, ev: Mapping[str, Any]) -> None:
        lanc = self._obter(str(ev.get("pasta") or ""))
        situacao = str(ev.get("situacao") or "")
        if situacao not in _SITUACOES_FINAIS:
            return
        faixa_id = str(ev.get("id") or ev.get("numero") or "")
        numero = str(ev.get("numero") or "").strip()
        lanc.terminais[faixa_id] = situacao
        if situacao == "falha":
            titulo = str(ev.get("titulo") or "").strip()
            lanc.falhas[faixa_id] = f"{numero.zfill(2)} {titulo}".strip()
            self._avisar(
                f"⚠ Faixa {numero} falhou · segue" if numero else "⚠ Faixa falhou"
            )
        else:
            lanc.falhas.pop(faixa_id, None)
            if situacao == "pulada":
                self._etapa = f"Faixa {numero} pulada" if numero else "Faixa pulada"
            else:
                self._etapa = f"Faixa {numero} pronta" if numero else "Faixa pronta"

    def _lancamento_concluido(self, ev: Mapping[str, Any]) -> None:
        lanc = self._obter(str(ev.get("pasta") or ""))
        lanc.titulo = str(ev.get("titulo") or lanc.titulo)
        lanc.artista = str(ev.get("artista") or lanc.artista)
        lanc.tipo = str(ev.get("tipo") or lanc.tipo)
        lanc.completo = bool(ev.get("completo"))
        lanc.novas = int(ev.get("faixas_novas") or 0)
        lanc.resumo = dict(ev.get("resumo") or {})
        lanc.fim = self._relogio()
        if lanc.completo:
            self._etapa = "Concluído"
        else:
            incompleto = "incompleta" if lanc.rotulo != "Álbum" else "incompleto"
            self._avisar(f"⚠ {lanc.rotulo} {incompleto}")

    # -- fila de links -----------------------------------------------------
    def definir_link(self, indice: int, total: int, espera: int) -> None:
        """O consumidor da fila vai começar o link `indice` de `total`."""
        with self._trava:
            self.fila_ativa = True
            self._fila_indice = indice
            self._fila_total = total
            self._fila_espera = espera
            self._etapa = ""

    def registrar_link_falho(self, link: str) -> None:
        with self._trava:
            self.fila_ativa = True
            if link not in self._fila_falharam:
                self._fila_falharam.append(link)

    def problemas(self) -> int:
        """Contador que só sobe: faixas com falha e álbuns incompletos."""
        with self._trava:
            total = 0
            for lanc in self._lancs.values():
                total += len(lanc.falhas)
                if lanc.completo is False and not lanc.falhas:
                    total += 1
            return total

    def _tela_fila(self) -> str:
        if self._fila_espera:
            return (
                f"Fila: {self._fila_espera} esperando · "
                f"link {self._fila_indice}/{self._fila_total}"
            )
        return f"Fila · link {self._fila_indice}/{self._fila_total}"

    def espelhar(self, texto: Any) -> None:
        """Recebe cada mensagem que o terminal mostraria (de qualquer thread)."""
        vigia.pulso()  # qualquer mensagem do programa é sinal de vida
        limpo = limpar_linha(texto)
        if limpo:
            with self._trava:
                self._etapa = limpo

    # -- banner ------------------------------------------------------------
    @staticmethod
    def _feitas_lanc(lanc: _Lancamento) -> int:
        # Álbum encerrado conta inteiro: a barra fecha mesmo se alguma faixa
        # nunca gerou evento (por exemplo, já existia).
        if lanc.completo is None:
            return len(lanc.terminais)
        return max(len(lanc.terminais), lanc.faixas)

    def _totais(self) -> tuple[int, int]:
        lancs = self._lancs.values()
        feitas = sum(self._feitas_lanc(lanc) for lanc in lancs)
        total = sum(max(lanc.faixas, len(lanc.terminais)) for lanc in lancs)
        total = max(total, self.faixas_esperadas)
        # O total pode crescer, o feito nunca volta (é a regra do `progress`).
        self._feitas_max = max(self._feitas_max, feitas)
        self._total_max = max(self._total_max, total, self._feitas_max)
        return self._feitas_max, self._total_max

    def _anel_locked(self) -> tuple[int, int]:
        """(feito, total) do ANEL do iOS, em unidades finas. Chamar com a trava.

        O iOS pode cancelar o Long Command cujo anel não anda. Cada faixa vale
        `escala` unidades: os bytes baixados ocupam os primeiros 85 % dela e o
        resto é reserva para o pós-processamento. Se nada mudar por `batida`
        segundos (conversão, letras, rede parada, leitura de uma playlist
        enorme), o anel avança 1 unidade, mesmo que o progresso real esteja
        parado ou atrás do anel; se a reserva acabar, o total cresce. O anel só
        fecha com todas as faixas prontas e o que já foi enviado nunca volta.
        Com `escala` 1 é o modo antigo: uma unidade por faixa pronta.
        """
        feitas, total = self._totais()
        esc = self.escala
        if esc <= 1:
            return feitas, total
        agora = self._relogio()
        parado = self._anel_parado_desde
        if total <= 0:
            # Antes do 1º lançamento: só a batida, continuando de onde a partida parou.
            if self.batida > 0 and agora - parado >= self.batida:
                self._batidas += 1
                parado = agora
            self._anel_parado_desde = parado
            if self._batidas:
                n = len(_partida.ETAPAS) + 1 + self._batidas
                m = n + esc
                n = max(n, self._anel_enviado[0])
                m = max(m, self._anel_enviado[1], n)
                self._anel_n, self._anel_m = n, m
                self._anel_enviado = (n, m)
            return self._anel_enviado
        parcial = int(progresso_bytes.parcial() * esc * FRACAO_BYTES_ANEL)
        real_m = total * esc
        real_n = min(feitas * esc + parcial, real_m)
        pendente = feitas < total
        n, m = max(self._anel_n, self._anel_enviado[0]), self._anel_m
        if real_n > n:  # o progresso real manda
            n = real_n
            parado = agora
        elif pendente and self.batida > 0 and agora - parado >= self.batida:
            if n + 1 >= max(m, real_m):  # reserva esgotada: o total pode crescer
                m = max(m, real_m) + esc
            n += 1  # a batida: o anel SEMPRE anda
            parado = agora
        m = max(m, real_m, self._anel_enviado[1], n + (1 if pendente else 0))
        if not pendente:  # tudo pronto: anel cheio
            n = m = max(n, m, real_m)
        self._anel_n, self._anel_m = n, m
        self._anel_parado_desde = parado
        self._anel_enviado = (n, m)
        return n, m

    def titulo(self) -> str:
        """Título atual do banner (uma linha só: o `progress` não tem subtítulo)."""
        with self._trava:
            return self._titulo_locked()

    def _tela_andamento(self, lanc: _Lancamento) -> str:
        tipo = etiqueta(lanc.rotulo)
        if lanc.rotulo == "Faixa":
            if self.faixas_esperadas:
                feitas = sum(len(item.terminais) for item in self._lancs.values())
                atual = min(feitas + 1, self.faixas_esperadas)
                return f"{tipo} {atual}/{self.faixas_esperadas}"
            return f"{tipo} {lanc.indice}"
        cabecalho = f"{tipo} {lanc.indice}"
        if self.esperados >= lanc.indice and not self.fila_ativa:
            cabecalho += f"/{self.esperados}"
        if not lanc.faixas:
            return cabecalho
        atual = min(len(lanc.terminais) + 1, lanc.faixas)
        return f"{cabecalho} · faixa {atual}/{lanc.faixas}"

    def _titulo_locked(self) -> str:
        lanc = self._lancs.get(self._atual)
        if lanc is None:
            agora = self._relogio()
            decorridos = max(int(agora - self._t0), 0)
            lendo = f"Qobuz · {self.fase} · {decorridos - decorridos % 2} s"
            if self.fila_ativa and self._fila_total:
                # Antes do 1º lançamento (a API ainda responde) o título alterna
                # entre a fila e a etapa com os segundos, para não ficar parado.
                passo = self.rotacao if self.rotacao > 0 else 0
                vez = int((agora - self._inicio) // passo) % 2 if passo else 0
                return self._tela_fila() if vez == 0 else lendo
            return lendo
        agora = self._relogio()
        if self._aviso and agora < self._aviso_ate:
            return _curto(self._aviso, TITULO_MAX)  # falha: fica parada
        telas = [self._tela_andamento(lanc)]
        if self._etapa:
            telas.append(self._etapa[:1].upper() + self._etapa[1:])
        if lanc.nome:
            telas.append(lanc.nome)
        if self.fila_ativa and self._fila_total:
            telas.append(self._tela_fila())
        passo = self.rotacao if self.rotacao > 0 else 0
        indice = int((agora - self._inicio) // passo) % len(telas) if passo else 0
        return _curto(telas[indice], TITULO_MAX)

    def _enviar(self, forcar: bool = False) -> None:
        """Manda ao `progress` só o que mudou. Chamar só da thread do laço."""
        with self._trava:
            titulo = self._titulo_locked()
            fracao = self._anel_locked()
            atual = self._lancs.get(self._atual)
            nome = atual.nome if atual is not None else ""
        # No iPad o banner só mostra o anel: o texto vai também para o `listar`.
        publicar_andamento(_CONTROLE.sub(" ", titulo), nome)
        if self._executar is None:
            return
        comandos: list[str] = []
        if forcar or titulo != self._ultimo_titulo:
            seguro = shlex.quote(_CONTROLE.sub(" ", titulo))
            comandos.append(f"progress --title {seguro}")
        if fracao[1] <= 0:
            if self._ultima_fracao is None:
                comandos.append("progress --indeterminate")
        elif fracao != self._ultima_fracao:
            comandos.append(f"progress {fracao[0]}/{fracao[1]}")
        passageira = False  # falha de um `progress` que já funcionou antes
        for comando in comandos:
            if self._executar is None:
                break
            try:
                if self._executar(comando) != 0:
                    if self._progress_ok:
                        logger.debug("atalho: `progress` falhou; tenta de novo")
                        passageira = True
                        break
                    logger.debug("atalho: `progress` indisponível; banner desligado")
                    self._executar = None
                else:
                    self._progress_ok = True
            except Exception as exc:
                logger.debug("atalho: falha ao chamar progress: %s", exc)
                if self._progress_ok:
                    passageira = True
                else:
                    self._executar = None
                break
        if passageira:
            return  # não marca como enviado: o próximo tique repete
        self._ultimo_titulo = titulo
        self._ultima_fracao = fracao

    async def _laco(self) -> None:
        while True:
            await asyncio.sleep(self.intervalo)
            try:
                self._enviar()
            except Exception as exc:
                logger.debug("atalho: tique ignorado (%s)", exc, exc_info=True)

    # -- ciclo de vida -----------------------------------------------------
    def iniciar(self) -> None:
        global _ATIVO
        _ATIVO = self
        ui.estado.espelho = _espelhar
        try:
            self._tarefa = asyncio.get_running_loop().create_task(self._laco())
        except RuntimeError:
            self._tarefa = None  # sem laço: só o envio final
        self._enviar(forcar=True)

    def parar(self) -> None:
        global _ATIVO
        if self._tarefa is not None:
            self._tarefa.cancel()
            self._tarefa = None
        if ui.estado.espelho is _espelhar:
            ui.estado.espelho = None
        if _ATIVO is self:
            _ATIVO = None

    def finalizar(self, ok: bool, interrompida: bool = False) -> None:
        """Último envio ao banner, resumo em texto e `progress --done`."""
        self.parar()
        try:
            self._enviar()
        except Exception as exc:
            logger.debug("atalho: último envio falhou (%s)", exc, exc_info=True)
        encerrar_andamento()
        texto, _ = self.resumo(ok and not interrompida, interrompida)
        ui.emit_always(texto)
        if self._executar is not None:
            try:
                self._executar("progress --done")
            except Exception as exc:
                logger.debug("atalho: progress --done falhou: %s", exc)

    # -- resumo final ------------------------------------------------------
    @staticmethod
    def _estatisticas(lanc: _Lancamento) -> dict[str, Any]:
        """Contagens de um lançamento (do relatório, ou das faixas vistas)."""
        terminais = list(lanc.terminais.values())
        resumo = lanc.resumo
        falhas = max(len(lanc.falhas), int(resumo.get("falhas") or 0))
        puladas = int(resumo.get("puladas") or 0) or terminais.count("pulada")
        if lanc.completo is None:
            novas = terminais.count("concluido")
        else:
            novas = lanc.novas
        total = int(resumo.get("total") or 0) or max(lanc.faixas, len(lanc.terminais))
        avulsa_ok = lanc.rotulo == "Faixa" and lanc.completo is None and bool(terminais)
        bom = falhas == 0 and (lanc.completo is True or avulsa_ok)
        return {
            "total": total,
            "novas": novas,
            "puladas": puladas,
            "falhas": falhas,
            "bom": bom,
        }

    @staticmethod
    def _detalhe(lanc: _Lancamento, est: Mapping[str, Any]) -> str:
        partes: list[str]
        if lanc.completo is None and lanc.rotulo != "Faixa":
            partes = ["não terminou"]
            if est["falhas"]:
                partes.append(_plural(est["falhas"], "falha", "falhas"))
        elif est["falhas"] or est["puladas"]:
            partes = [_plural(est["novas"], "nova", "novas")]
            if est["puladas"]:
                partes.append(_plural(est["puladas"], "pulada", "puladas"))
            if est["falhas"]:
                partes.append(_plural(est["falhas"], "falha", "falhas"))
        else:
            partes = [_plural(est["total"], "faixa", "faixas")]
        if lanc.fim is not None:
            partes.append(_duracao_texto(lanc.fim - lanc.inicio))
        return " · ".join(p for p in partes if p)

    @staticmethod
    def _linhas_falhas(falhas: list[str]) -> list[str]:
        recuo = RECUO * 2
        linhas = [
            f"{recuo}falhou: {_curto(n, NOME_MAX)}" for n in falhas[:FALHAS_POR_ALBUM]
        ]
        if len(falhas) > FALHAS_POR_ALBUM:
            linhas.append(f"{recuo}… e mais {len(falhas) - FALHAS_POR_ALBUM}")
        return linhas

    def resumo(
        self, ok: bool, interrompida: bool = False
    ) -> tuple[str, dict[str, Any]]:
        """(texto puro, dados) do fim da sessão.

        Primeira linha: status e tempo (é o que a notificação mostra). Depois,
        os totais e um bloco por tipo: álbuns, playlists e faixas avulsas.
        """
        interrompida = interrompida or bool(self.travamento)
        with self._trava:
            lancs = list(self._lancs.values())
            sessao = dict(self._sessao)
        grupos: dict[str, list[_Lancamento]] = {
            "Álbum": [],
            "Playlist": [],
            "Faixa": [],
        }
        for lanc in lancs:
            grupos.setdefault(lanc.rotulo, []).append(lanc)
        estat = {id(lanc): self._estatisticas(lanc) for lanc in lancs}

        faixas = sum(e["total"] for e in estat.values())
        novas = sum(e["novas"] for e in estat.values())
        puladas = sum(e["puladas"] for e in estat.values())
        falhas = sum(e["falhas"] for e in estat.values())
        falharam = list(self._fila_falharam)
        todos_ok = all(e["bom"] for e in estat.values()) and not falharam

        blocos: list[str] = []
        for rotulo, plural in (
            ("Álbum", "Álbuns"),
            ("Playlist", "Playlists"),
            ("Faixa", "Faixas avulsas"),
        ):
            itens = grupos.get(rotulo) or []
            if not itens:
                continue
            linhas = [etiqueta(plural if len(itens) > 1 else rotulo)]
            if rotulo == "Faixa":
                est_g = [estat[id(i)] for i in itens]
                nomes = [n for i in itens for n in i.falhas.values()]
                total_g = sum(e["total"] for e in est_g)
                novas_g = sum(e["novas"] for e in est_g)
                puladas_g = sum(e["puladas"] for e in est_g)
                falhas_g = sum(e["falhas"] for e in est_g)
                partes = [_plural(total_g, "faixa", "faixas")]
                if falhas_g or puladas_g:
                    partes = [_plural(novas_g, "nova", "novas")]
                    if puladas_g:
                        partes.append(_plural(puladas_g, "pulada", "puladas"))
                    if falhas_g:
                        partes.append(_plural(falhas_g, "falha", "falhas"))
                marca = MARCA_OK if all(e["bom"] for e in est_g) else MARCA_ATENCAO
                linhas.append(f"{marca} " + " · ".join(partes))
                linhas.extend(self._linhas_falhas(nomes))
            else:
                for lanc in itens:
                    est = estat[id(lanc)]
                    marca = MARCA_OK if est["bom"] else MARCA_ATENCAO
                    linhas.append(f"{marca} {_curto(lanc.nome, NOME_MAX)}")
                    linhas.append(RECUO + self._detalhe(lanc, est))
                    linhas.extend(self._linhas_falhas(list(lanc.falhas.values())))
            blocos.append("\n".join(linhas))

        duracao = sessao.get("duracao_s")
        if not isinstance(duracao, (int, float)):
            duracao = self._relogio() - self._inicio
        tempo = _duracao_texto(float(duracao))
        dados: dict[str, Any] = {
            "ok": bool(ok and lancs and todos_ok and not interrompida),
            "albuns": len(lancs),
            "novas": novas,
            "falhas": falhas,
        }
        if self.fila_ativa:
            dados["fila"] = {"links": self._fila_indice, "falharam": len(falharam)}

        if not lancs:
            cabeca = ["Interrompido antes de terminar."] if interrompida else []
            cabeca.append("Nenhum álbum foi processado.")
        else:
            if interrompida:
                status = "Interrompido"
            elif falhas:
                status = f"Concluído com {_plural(falhas, 'falha', 'falhas')}"
            elif not todos_ok:
                status = "Concluído com pendências"
            else:
                status = f"Concluído · {_plural(faixas, 'faixa', 'faixas')}"
            totais = [_plural(novas, "nova", "novas")]
            if puladas:
                totais.append(_plural(puladas, "pulada", "puladas"))
            if falhas:
                totais.append(_plural(falhas, "falha", "falhas"))
            cabeca = [" · ".join(p for p in (status, tempo) if p), " · ".join(totais)]
        if self.travamento:
            cabeca.append(f"{MARCA_ATENCAO} {self.travamento}")
        if self.fila_ativa:
            texto_fila = _plural(self._fila_indice, "link", "links")
            if falharam:
                verbo = "não baixou" if len(falharam) == 1 else "não baixaram"
                texto_fila += f" · {len(falharam)} {verbo}"
            cabeca.insert(2 if lancs else len(cabeca), f"Fila: {texto_fila}")
        if falharam:
            bloco = [etiqueta("Links que falharam")]
            for link in falharam[:FALHAS_POR_ALBUM]:
                bloco.append(f"{MARCA_ATENCAO} {link_curto(link, NOME_MAX)}")
            if len(falharam) > FALHAS_POR_ALBUM:
                bloco.append(f"{RECUO}… e mais {len(falharam) - FALHAS_POR_ALBUM}")
            bloco.append(f'{RECUO}volta à fila com "repetir"')
            blocos.append("\n".join(bloco))
        partes_texto = ["\n".join(cabeca), *blocos]
        if self.com_json:
            partes_texto.append(json.dumps(dados, separators=(",", ":")))
        return "\n\n".join(partes_texto), dados


# O `ui` guarda só uma função simples (o estado é copiado em testes com
# `dataclasses.asdict`, que não pode copiar um objeto com thread e trava).
_ATIVO: Atalho | None = None


def ativo() -> Atalho | None:
    """O modo atalho em andamento (ou None). Usado pela cola com a fila."""
    return _ATIVO


def _espelhar(texto: Any) -> None:
    ativo = _ATIVO
    if ativo is not None:
        ativo.espelhar(texto)


# ----------------------------------------------------------------------------
# Cola com o programa (cli.py)
# ----------------------------------------------------------------------------
def preparar(
    arguments: Any,
    versao: str = "",
    ambiente: Mapping[str, str] | None = None,
    automacao_ativa: bool = False,
) -> Atalho | None:
    """Liga o modo atalho. Devolve None (e não custa nada) quando desligado.

    ``automacao_ativa``: os hooks já abriram a sessão de eventos; senão este
    módulo abre (e fecha) a sessão sozinho.
    """
    if not aplicavel(arguments, ambiente):
        return None
    fontes = getattr(arguments, "SOURCE", None)
    atalho = Atalho(
        inicio_processo=_partida.T0,
        esperados=esperados_de_fontes(fontes),
        faixas_esperadas=faixas_esperadas_de_fontes(fontes),
        com_json=bool(getattr(arguments, "atalho_json", False)),
    )
    if not automacao_ativa:
        events.reiniciar()
    progresso_bytes.reiniciar()
    events.assinar(atalho.ao_evento)
    atalho.emite_sessao = not automacao_ativa
    if atalho.emite_sessao:
        events.sessao_iniciada(getattr(arguments, "command", None), versao)
    atalho.iniciar()
    _ligar_vigia(atalho, ambiente)
    return atalho


def _ligar_vigia(atalho: Atalho, ambiente: Mapping[str, str] | None = None) -> None:
    """Liga o vigia: download sem sinal de vida é cancelado e, se preciso, morto.

    Sem isto, um download travado deixava o Long Command rodando para sempre
    em segundo plano (e a fila marcada como "em andamento").
    """
    limite = vigia.limite_do_ambiente(ambiente)
    if limite <= 0:
        return
    try:
        laco = asyncio.get_running_loop()
        tarefa = asyncio.current_task()
    except RuntimeError:
        return  # sem laço asyncio não há tarefa para cancelar

    def ao_travar(parado: float) -> None:
        atalho.travamento = (
            f"Travou: sem sinal de vida há {_duracao_texto(parado)}; cancelado"
        )
        baixador = sys.modules.get("qobuz_dl.downloader")
        aborto = getattr(baixador, "abort_event", None)
        if aborto is not None:
            aborto.set()  # pede às threads de download que parem
        if tarefa is not None:
            laco.call_soon_threadsafe(tarefa.cancel)

    def ao_forcar(parado: float) -> None:
        vigia.forcar_saida(
            1,
            f"{MARCA_ATENCAO} Travou: sem sinal de vida há {_duracao_texto(parado)}. "
            "Encerrado à força; o que estava baixando volta à fila.",
        )

    vigia.pulso()  # o relógio do vigia começa agora
    atalho.vigia = vigia.Vigia(ao_travar, ao_forcar, limite=limite)
    atalho.vigia.iniciar()


async def encerrar(atalho: Atalho | None, ok: bool, interrompida: bool = False) -> None:
    """Fecha o modo atalho: evento final (se for nosso), resumo e `--done`."""
    if atalho is None:
        return
    try:
        if atalho.emite_sessao:
            events.sessao_concluida(ok, interrompida)
        atalho.finalizar(ok, interrompida)
    except Exception as exc:
        logger.warning("Falha ao encerrar o modo atalho: %s", exc, exc_info=True)
    finally:
        events.desassinar(atalho.ao_evento)
        if atalho.emite_sessao:
            events.fechar()
        if atalho.vigia is not None:
            atalho.vigia.parar()
            # Rede de segurança: acabou a sessão; se o Python não sair sozinho
            # (thread presa), sai à força em vez de ficar rodando no a-Shell.
            vigia.armar_saida(0 if ok and not interrompida else 1)


# ----------------------------------------------------------------------------
# Teste sem baixar nada:  python3 -m qobuz_dl.atalho
# ----------------------------------------------------------------------------
# (tipo, título, artista, faixas, número da faixa que falha ou None)
_ROTEIRO = (
    ("Album", "Primeiro álbum", "Artista A", 5, None),
    ("playlist", "Minha playlist", "", 6, 4),
    ("faixa", "Faixa solta 1", "Artista C", 1, None),
    ("faixa", "Faixa solta 2", "Artista D", 1, None),
)


async def simular(
    pausa: float = 1.0,
    executar: Callable[[str], int] | None = None,
    emitir_resumo: bool = True,
    com_json: bool = False,
) -> Atalho:
    """Percorre álbum, playlist e 2 faixas de mentira pelos MESMOS eventos de
    um download real (a playlist é anunciada antes, como faz o `core.py`)."""
    events.reiniciar()
    progresso_bytes.reiniciar()
    atalho = Atalho(
        executar=executar,
        intervalo=min(0.5, max(pausa / 2, 0.01)),
        com_json=com_json,
    )
    events.assinar(atalho.ao_evento)
    atalho.iniciar()
    ok = True
    try:
        for n, (tipo, titulo, artista, faixas, falha) in enumerate(_ROTEIRO, start=1):
            pasta = f"/teste/[IN PROGRESS] {titulo}"
            if tipo != "faixa":  # faixa avulsa só ganha evento depois de pronta
                events.lancamento_iniciado(
                    pasta, tipo, titulo, artista, f"teste{n}", faixas
                )
                await asyncio.sleep(pausa)
            for numero in range(1, faixas + 1):
                atalho.espelhar(f"  ├─ Baixando: {titulo} {numero}")
                chave_bytes = f"{pasta}/{numero}"
                for passo in range(1, 5):  # bytes chegando: o anel anda na faixa
                    progresso_bytes.registrar(chave_bytes, passo * 25, 100)
                    await asyncio.sleep(pausa / 4)
                progresso_bytes.concluir(chave_bytes)
                atalho.espelhar(f"  ├─ Gravando tags e capa: {numero}")
                await asyncio.sleep(pausa)
                if tipo == "faixa":
                    events.lancamento_iniciado(
                        pasta, tipo, titulo, artista, f"teste{n}", faixas
                    )
                situacao = "falha" if numero == falha else "concluido"
                ok = ok and situacao != "falha"
                events.faixa(
                    pasta,
                    numero,
                    f"teste{n}-{numero}",
                    f"{titulo} {numero}",
                    artista,
                    situacao,
                    "Falha simulada" if situacao == "falha" else "",
                )
                await asyncio.sleep(pausa)
            events.lancamento_concluido(
                pasta.replace("[IN PROGRESS] ", ""),
                falha is None,
                {
                    "tipo": tipo,
                    "identificacao": {"titulo": titulo, "artista": artista},
                    "progresso": {
                        "resumo": {
                            "total": faixas,
                            "concluidas": faixas - (1 if falha else 0),
                            "puladas": 0,
                            "falhas": 1 if falha else 0,
                            "pendentes": 0,
                        }
                    },
                },
            )
            await asyncio.sleep(pausa)
        events.sessao_concluida(ok)
    finally:
        events.desassinar(atalho.ao_evento)
        if emitir_resumo:
            atalho.finalizar(ok)
        else:
            atalho.parar()
    return atalho


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m qobuz_dl.atalho",
        description=(
            "Simula um download (álbum, playlist e 2 faixas, com 1 falha) para você "
            "conferir o banner do iOS no Atalho, sem baixar nada. Rode dentro de "
            "'Execute Long Command'."
        ),
    )
    parser.add_argument(
        "--pausa",
        type=float,
        default=1.0,
        metavar="SEG",
        help="segundos entre as etapas simuladas (padrão: 1)",
    )
    argumentos = parser.parse_args(argv)
    if sys.platform != "ios":
        print(
            "Fora do iOS não há banner (`progress`): só o texto final aparece.",
            file=sys.stderr,
        )
    asyncio.run(simular(pausa=max(0.0, argumentos.pausa)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
