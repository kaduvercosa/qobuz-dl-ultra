# ==============================================================================
# MÓDULO: fila.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Fila de links para o modo Atalho do iOS.
#
# Por que existe
#   O iOS só roda UM "Execute Long Command" por vez; uma segunda chamada falha
#   na hora. Para acrescentar links com um download em andamento, um Atalho
#   rápido ("Execute Command") grava o link na fila e o Long Command em
#   andamento o encontra sozinho.
#
#     adicionar (rápido):  python3 -m qobuz_dl.fila adicionar "LINK"
#     baixar (Long):       qobuz-dl dl --atalho @fila
#
#   `@fila` pode vir junto de links normais: `dl --atalho LINK1 @fila`.
#
# Como a fila é guardada (sem trava, sem banco)
#   Uma pasta `fila/` ao lado do config.ini, um arquivo por link:
#     <instante>-<id>.link        pendente
#     <instante>-<id>.andamento   sendo baixado agora
#     <instante>-<id>.falhou      não baixou (volta com `repetir`)
#   Criar e renomear arquivo é atômico: dois Atalhos nunca se atrapalham, e a
#   ordem é a do nome do arquivo (instante em nanossegundos).
#
# "Está rodando?"
#   O consumidor atualiza o arquivo `ativo` a cada poucos segundos. Quem
#   adiciona lê a idade dele (até 30 s = rodando). Se o iPhone matar o comando,
#   a marca expira sozinha e a fila volta a ser considerada parada.
#
# O consumidor termina quando a fila esvazia. Esperar parado por novos links
# não vale a pena: sem progresso, o iOS pode cancelar o Long Command.
#
# Este módulo não importa nada pesado (rodar `adicionar` tem que ser rápido).
# ==============================================================================

from __future__ import annotations

import argparse
import asyncio
import configparser
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger(__name__)

TOKEN = "@fila"  # no lugar de um link: "esvazie a fila"

SUFIXO_PENDENTE = ".link"
SUFIXO_ANDAMENTO = ".andamento"
SUFIXO_FALHOU = ".falhou"
ARQUIVO_ATIVO = "ativo"

ATIVO_VALIDADE_S = 30.0  # idade máxima da marca para "está rodando"
ARQUIVO_ANDAMENTO = "andamento.json"
ANDAMENTO_VALIDADE_S = 30.0  # o andamento é regravado a cada ~2 s; mais velho = parado
BATIMENTO_S = 5.0  # de quanto em quanto tempo o consumidor renova a marca

# Mesma regra de `utils.get_url_info`, mas exigindo http(s): arquivo local ou
# texto solto não entra na fila.
_LINK = re.compile(
    r"^https?://(?:w{3}|open|play)\.qobuz\.com"
    r"(?:/[a-z]{2}-[a-z]{2})?/(album|artist|track|playlist|label)"
    r"(?:/[-\w]+)?/(\w+)",
    re.IGNORECASE,
)
_TIPOS = {
    "album": "Álbum",
    "artist": "Artista",
    "track": "Faixa",
    "playlist": "Playlist",
    "label": "Gravadora",
}


class FilaOcupada(RuntimeError):
    """Já existe um consumidor da fila rodando (marca `ativo` recente)."""


def chave_do_link(link: str) -> tuple[str, str] | None:
    """(tipo, id) do link do Qobuz, ou None se não for um link aceito."""
    achado = _LINK.match(str(link).strip())
    if achado is None:
        return None
    return achado.group(1).lower(), achado.group(2)


def link_curto(link: str, limite: int = 40) -> str:
    """`play.qobuz.com/album/123` (sem esquema, cortado)."""
    texto = re.sub(r"^https?://", "", str(link).strip())
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def rotulo_do_link(link: str) -> str:
    """`Álbum 123`: o que dá para dizer sem consultar a rede."""
    chave = chave_do_link(link)
    if chave is None:
        return link_curto(link)
    return f"{_TIPOS[chave[0]]} {chave[1]}"


# -- nomes legíveis ----------------------------------------------------------
# O link só traz o id (`Álbum 0886446218915`). O nome e o artista vêm da API
# pública do Qobuz (só com o app_id, sem login) e ficam guardados em
# `nomes.json`, na pasta da fila, para o `listar` não repetir a consulta.
API_BASE = "https://www.qobuz.com/api.json/0.2"
APP_ID_PADRAO = "798273057"  # o mesmo de `qopy.DEFAULT_APP_ID` (sem importar o qopy)
ARQUIVO_NOMES = "nomes.json"
BUSCA_TIMEOUT_S = 4.0  # por consulta
LISTAR_ORCAMENTO_S = 8.0  # tempo total de rede do `listar` (o resto vem do cache)
_PARAMETRO_ID = {
    "album": "album_id",
    "artist": "artist_id",
    "track": "track_id",
    "playlist": "playlist_id",
    "label": "label_id",
}
_SLUG = re.compile(
    r"/(?:album|artist|track|playlist|label)/([-\w]+)/\w+", re.IGNORECASE
)


def etiqueta_do_tipo(tipo: str) -> str:
    """`[ ÁLBUM ]`, `[ PLAYLIST ]`... (o tipo entre colchetes, como no resumo)."""
    return f"[ {_TIPOS.get(tipo, tipo).upper()} ]"


def rotulo_com_nome(link: str, nome: str = "") -> str:
    """`[ ÁLBUM ] Artista — Título`; sem nome, `[ ÁLBUM ] <id>`."""
    chave = chave_do_link(link)
    if chave is None:
        return link_curto(link)
    return f"{etiqueta_do_tipo(chave[0])} {nome or chave[1]}"


def _rede_ligada() -> bool:
    return os.environ.get("QOBUZ_DL_FILA_REDE", "1").strip().lower() not in {
        "0",
        "false",
        "nao",
        "não",
    }


def _ler_config() -> tuple[str, str]:
    """(app_id, token) do config.ini; ``QOBUZ_DL_APP_ID`` manda no app_id.

    O programa chama a API sempre com o token da conta; sem ele o Qobuz pode
    recusar a consulta do nome, então ele segue junto quando existe no config
    (no keyring não dá para olhar daqui, sem importar o programa inteiro).
    """
    achado_id, token = "", ""
    try:
        from qobuz_dl.utils import get_config_paths

        config = configparser.ConfigParser()
        config.read(get_config_paths()["config_file"], encoding="utf-8")
        secao = "qobuz" if config.has_section("qobuz") else "DEFAULT"
        achado_id = config.get(secao, "app_id", fallback="").strip()
        for chave in ("user_auth_token", "user_token", "auth_token"):
            token = config.get(secao, chave, fallback="").strip()
            if token:
                break
    except Exception:  # config ilegível ou programa sem as dependências
        pass
    manual = os.environ.get("QOBUZ_DL_APP_ID", "").strip()
    return manual or achado_id or APP_ID_PADRAO, token


def app_id() -> str:
    """O app_id do config.ini (ou `QOBUZ_DL_APP_ID`); cai no padrão do programa."""
    return _ler_config()[0]


# Por que o último nome não veio (para o `adicionar`/`listar` dizerem na tela).
_ultimo_erro: list[str] = []


def ultimo_erro() -> str:
    return _ultimo_erro[-1] if _ultimo_erro else ""


def _registrar_erro(texto: str) -> None:
    _ultimo_erro.append(texto[:140])


def _nome_da_resposta(tipo: str, dados: Any) -> str:
    """`Artista — Título` (álbum/faixa) ou só o nome (playlist/artista/gravadora)."""
    if not isinstance(dados, dict):
        return ""

    def _nome(valor: Any) -> str:
        return str(valor.get("name") or "").strip() if isinstance(valor, dict) else ""

    if tipo in ("album", "track"):
        titulo = str(dados.get("title") or "").strip()
        versao = str(dados.get("version") or "").strip()
        if titulo and versao:
            titulo = f"{titulo} ({versao})"
        if tipo == "album":
            artista = _nome(dados.get("artist"))
        else:
            artista = _nome(dados.get("performer")) or _nome(
                (dados.get("album") or {}).get("artist")
                if isinstance(dados.get("album"), dict)
                else None
            )
        return f"{artista} — {titulo}" if artista and titulo else titulo or artista
    return str(dados.get("name") or "").strip()


def buscar_nome(
    link: str, timeout: float = BUSCA_TIMEOUT_S, abrir: Any = None
) -> str | None:
    """Pergunta o nome à API do Qobuz. None se não deu (rede, id, resposta).

    Tenta com o token da conta (como o programa faz) e, se o Qobuz o recusar,
    uma vez sem ele. O motivo da falha fica em `ultimo_erro()`.
    """
    chave = chave_do_link(link)
    if chave is None or not _rede_ligada() or timeout <= 0:
        return None
    tipo, ident = chave
    chave_api, token = _ler_config()
    consulta = urllib.parse.urlencode({_PARAMETRO_ID[tipo]: ident, "app_id": chave_api})
    url = f"{API_BASE}/{tipo}/get?{consulta}"
    variantes = [token, ""] if token else [""]
    for usar_token in variantes:
        cabecalhos = {"X-App-Id": chave_api, "User-Agent": "Mozilla/5.0"}
        if usar_token:
            cabecalhos["X-User-Auth-Token"] = usar_token
        pedido = urllib.request.Request(url, headers=cabecalhos)
        try:
            with (abrir or urllib.request.urlopen)(pedido, timeout=timeout) as resposta:
                dados = json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            mensagem = ""
            try:
                mensagem = str(
                    json.loads(exc.read().decode("utf-8")).get("message", "")
                )
            except Exception:
                pass  # corpo do erro ilegível: fica só o código HTTP
            _registrar_erro(f"HTTP {exc.code}" + (f": {mensagem}" if mensagem else ""))
            continue  # com token recusado, tenta sem ele
        except Exception as exc:  # sem rede, tempo esgotado, JSON ruim
            _registrar_erro(f"{type(exc).__name__}: {exc}")
            return None
        nome = _nome_da_resposta(tipo, dados)
        if nome:
            return nome
        _registrar_erro("resposta sem nome")
        return None
    return None


def nome_do_slug(link: str) -> str:
    """Plano B sem rede: o trecho do link (`esu-baixo-eru` → `Esu Baixo Eru`)."""
    achado = _SLUG.search(str(link))
    if achado is None:
        return ""
    return " ".join(p.capitalize() for p in achado.group(1).split("-") if p)


_PUBLICADO: list[Any] = ["", 0.0, None]  # último texto, quando, Fila usada


def publicar_andamento(
    titulo: str, nome: str = "", fila: "Fila | None" = None, agora: float | None = None
) -> None:
    """Grava o andamento (no máx. 1x a cada 2 s se o texto não mudou). Nunca falha."""
    try:
        instante = time.monotonic() if agora is None else agora
        texto = f"{nome}\n{titulo}"
        if fila is None:
            if _PUBLICADO[2] is None:
                _PUBLICADO[2] = Fila()
            fila = _PUBLICADO[2]
        if texto == _PUBLICADO[0] and instante - float(_PUBLICADO[1]) < 2.0:
            return
        fila.gravar_andamento(titulo, nome)
        _PUBLICADO[0], _PUBLICADO[1] = texto, instante
    except Exception as exc:  # informação de cortesia: nunca atrapalha o download
        logger.debug("fila: não consegui publicar o andamento: %s", exc)


def encerrar_andamento(fila: "Fila | None" = None) -> None:
    try:
        (fila or _PUBLICADO[2] or Fila()).limpar_andamento()
        _PUBLICADO[0], _PUBLICADO[1] = "", 0.0
    except Exception as exc:  # idem
        logger.debug("fila: não consegui limpar o andamento: %s", exc)


def diretorio_padrao() -> str:
    """`<config>/qobuz-dl/fila`. QOBUZ_DL_FILA_DIR muda o lugar (testes)."""
    manual = os.environ.get("QOBUZ_DL_FILA_DIR", "").strip()
    if manual:
        return manual
    try:
        from qobuz_dl.utils import get_config_paths

        base = str(get_config_paths()["config_path"])
    except Exception:  # sem as dependências do programa: cai num lugar seguro
        base = os.path.join(os.path.expanduser("~"), "Documents", "qobuz-dl")
    return os.path.join(base, "fila")


@dataclass
class Item:
    nome: str  # nome do arquivo
    caminho: str
    link: str
    estado: str  # "pendente" | "andamento" | "falhou"
    motivo: str = ""


@dataclass
class Adicao:
    link: str
    resultado: str  # "novo" | "repetido" | "andamento" | "invalido"
    posicao: int = 0
    rodando: bool = False


@dataclass
class Resultado:
    """O que a fila fez numa execução do consumidor."""

    links: int = 0
    falharam: list[str] = field(default_factory=list)


class Fila:
    def __init__(
        self,
        diretorio: str | None = None,
        relogio: Callable[[], float] = time.time,
    ) -> None:
        self.dir = diretorio or diretorio_padrao()
        self._relogio = relogio

    # -- arquivos ----------------------------------------------------------
    def _garantir(self) -> None:
        os.makedirs(self.dir, exist_ok=True)

    def _ler(self, nome: str, estado: str) -> Item | None:
        caminho = os.path.join(self.dir, nome)
        try:
            with open(caminho, encoding="utf-8") as arquivo:
                linhas = arquivo.read().splitlines()
        except OSError:
            return None  # sumiu entre o `listdir` e a leitura
        if not linhas or not linhas[0].strip():
            return None
        motivo = linhas[1].strip() if len(linhas) > 1 else ""
        return Item(nome, caminho, linhas[0].strip(), estado, motivo)

    def _listar(self, sufixo: str, estado: str) -> list[Item]:
        try:
            nomes = sorted(n for n in os.listdir(self.dir) if n.endswith(sufixo))
        except OSError:
            return []
        itens = (self._ler(nome, estado) for nome in nomes)
        return [item for item in itens if item is not None]

    def pendentes(self) -> list[Item]:
        return self._listar(SUFIXO_PENDENTE, "pendente")

    def em_andamento(self) -> list[Item]:
        return self._listar(SUFIXO_ANDAMENTO, "andamento")

    def falhos(self) -> list[Item]:
        return self._listar(SUFIXO_FALHOU, "falhou")

    # -- andamento ao vivo ------------------------------------------------------
    # No iPad o banner do Atalho só mostra o botão de parar e o anel (o título
    # não aparece). Quem baixa grava aqui o que o banner diria, e o `listar`
    # (um Execute Command, que roda ao lado do Long Command) mostra na hora.
    def gravar_andamento(self, titulo: str, nome: str = "") -> None:
        try:
            self._garantir()
            temporario = os.path.join(
                self.dir, f".tmp-andamento-{uuid.uuid4().hex[:6]}"
            )
            with open(temporario, "w", encoding="utf-8") as arquivo:
                json.dump(
                    {"titulo": titulo, "nome": nome, "ts": time.time()},
                    arquivo,
                    ensure_ascii=False,
                )
            os.replace(temporario, os.path.join(self.dir, ARQUIVO_ANDAMENTO))
        except OSError:
            pass  # é só informação: sem ela o download segue igual

    def ler_andamento(self) -> dict[str, str] | None:
        """O andamento gravado há pouco, ou None (nada rodando ou parado)."""
        try:
            with open(
                os.path.join(self.dir, ARQUIVO_ANDAMENTO), encoding="utf-8"
            ) as arquivo:
                dados = json.load(arquivo)
            if time.time() - float(dados.get("ts", 0)) > ANDAMENTO_VALIDADE_S:
                return None
            return {
                "titulo": str(dados.get("titulo", "")),
                "nome": str(dados.get("nome", "")),
            }
        except (OSError, ValueError, AttributeError):
            return None  # sem arquivo ou ilegível: não há andamento a mostrar

    def limpar_andamento(self) -> None:
        try:
            os.remove(os.path.join(self.dir, ARQUIVO_ANDAMENTO))
        except OSError:
            pass  # já não existia

    # -- nomes em cache ----------------------------------------------------
    def nomes(self) -> dict[str, str]:
        try:
            with open(
                os.path.join(self.dir, ARQUIVO_NOMES), encoding="utf-8"
            ) as arquivo:
                dados = json.load(arquivo)
        except (OSError, ValueError):
            return {}
        return (
            {str(k): str(v) for k, v in dados.items()}
            if isinstance(dados, dict)
            else {}
        )

    def guardar_nome(self, chave: str, nome: str) -> None:
        try:
            self._garantir()
            dados = self.nomes()
            dados[chave] = nome
            temporario = os.path.join(self.dir, f".tmp-nomes-{uuid.uuid4().hex[:6]}")
            with open(temporario, "w", encoding="utf-8") as arquivo:
                json.dump(dados, arquivo, ensure_ascii=False)
            os.replace(temporario, os.path.join(self.dir, ARQUIVO_NOMES))
        except OSError:
            pass  # o nome é só enfeite: sem cache, a próxima consulta refaz

    def nome_do_link(
        self,
        link: str,
        *,
        rede: bool = True,
        timeout: float = BUSCA_TIMEOUT_S,
        buscar: Callable[[str, float], str | None] | None = None,
    ) -> str:
        """Nome legível do link: cache → API → trecho do link → ``""``."""
        chave = chave_do_link(link)
        if chave is None:
            return ""
        indice = f"{chave[0]}:{chave[1]}"
        guardado = self.nomes().get(indice, "")
        if guardado:
            return guardado
        if rede:
            achado = (buscar or buscar_nome)(link, timeout)
            if achado:
                self.guardar_nome(indice, achado)
                return achado
        return nome_do_slug(link)

    def contar_pendentes(self) -> int:
        try:
            return sum(1 for n in os.listdir(self.dir) if n.endswith(SUFIXO_PENDENTE))
        except OSError:
            return 0

    # -- quem adiciona -----------------------------------------------------
    def adicionar(self, link: str) -> Adicao:
        link = str(link).strip()
        chave = chave_do_link(link)
        if chave is None:
            return Adicao(link, "invalido")
        rodando = self.esta_ativo()
        pendentes = self.pendentes()
        for posicao, item in enumerate(pendentes, start=1):
            if chave_do_link(item.link) == chave:
                return Adicao(link, "repetido", posicao, rodando)
        for item in self.em_andamento():
            if chave_do_link(item.link) == chave:
                return Adicao(link, "andamento", 0, rodando)
        self._garantir()
        base = f"{time.time_ns():020d}-{uuid.uuid4().hex[:6]}"
        temporario = os.path.join(self.dir, f".tmp-{base}")
        with open(temporario, "w", encoding="utf-8") as arquivo:
            arquivo.write(link + "\n")
        os.replace(temporario, os.path.join(self.dir, base + SUFIXO_PENDENTE))
        return Adicao(link, "novo", len(pendentes) + 1, rodando)

    # -- quem consome ------------------------------------------------------
    def reivindicar(self) -> Item | None:
        """Pega o próximo pendente (renomeia para `.andamento`) ou None."""
        for item in self.pendentes():
            destino = item.caminho[: -len(SUFIXO_PENDENTE)] + SUFIXO_ANDAMENTO
            try:
                os.rename(item.caminho, destino)
            except OSError:
                continue  # outro consumidor foi mais rápido
            item.caminho, item.estado = destino, "andamento"
            item.nome = os.path.basename(destino)
            return item
        return None

    def _mover(self, item: Item, sufixo_novo: str, estado: str) -> None:
        raiz, _ = os.path.splitext(item.caminho)
        destino = raiz + sufixo_novo
        os.replace(item.caminho, destino)
        item.caminho, item.estado = destino, estado
        item.nome = os.path.basename(destino)

    def concluir(self, item: Item) -> None:
        try:
            os.remove(item.caminho)
        except OSError:
            pass  # o arquivo já sumiu (outro Atalho limpou): nada a concluir

    def devolver(self, item: Item) -> None:
        """Volta à fila (comando interrompido no meio do link)."""
        try:
            self._mover(item, SUFIXO_PENDENTE, "pendente")
        except OSError:
            pass  # o arquivo já foi movido ou apagado por outro comando: a fila já está certa

    def falhar(self, item: Item, motivo: str = "") -> None:
        try:
            if motivo:
                with open(item.caminho, "a", encoding="utf-8") as arquivo:
                    arquivo.write(" ".join(motivo.split())[:200] + "\n")
            self._mover(item, SUFIXO_FALHOU, "falhou")
        except OSError:
            pass  # o arquivo sumiu: o link já não está na fila, não há o que marcar

    def registrar_falho(self, link: str, motivo: str = "") -> None:
        """Link dado na linha de comando que falhou: `repetir` também o traz."""
        try:
            self._garantir()
            base = f"{time.time_ns():020d}-{uuid.uuid4().hex[:6]}"
            temporario = os.path.join(self.dir, f".tmp-{base}")
            with open(temporario, "w", encoding="utf-8") as arquivo:
                arquivo.write(str(link).strip() + "\n")
                if motivo:
                    arquivo.write(" ".join(motivo.split())[:200] + "\n")
            os.replace(temporario, os.path.join(self.dir, base + SUFIXO_FALHOU))
        except OSError as exc:
            logger.debug("fila: não consegui guardar o link que falhou (%s)", exc)

    def recuperar_andamento(self) -> int:
        """Links que ficaram `.andamento` de um comando morto voltam à fila."""
        total = 0
        for item in self.em_andamento():
            self.devolver(item)
            total += 1
        return total

    def repetir(self) -> int:
        """`.falhou` volta a `.link` (mantém a posição original)."""
        total = 0
        for item in self.falhos():
            try:
                self._mover(item, SUFIXO_PENDENTE, "pendente")
                total += 1
            except OSError:
                pass  # o item sumiu ou já voltou por outro Atalho: segue para o próximo
        return total

    def limpar(self, pendentes: bool = True, falhos: bool = True) -> int:
        total = 0
        grupos = []
        if pendentes:
            grupos.append(self.pendentes())
        if falhos:
            grupos.append(self.falhos())
        for itens in grupos:
            for item in itens:
                try:
                    os.remove(item.caminho)
                    total += 1
                except OSError:
                    pass  # já apagado por outro Atalho: segue para o próximo
        return total

    # -- marca "está rodando" ----------------------------------------------
    @property
    def _caminho_ativo(self) -> str:
        return os.path.join(self.dir, ARQUIVO_ATIVO)

    def marcar_ativo(self) -> None:
        try:
            self._garantir()
            with open(self._caminho_ativo, "w", encoding="utf-8") as arquivo:
                arquivo.write(f"{self._relogio():.0f}\n")
        except OSError as exc:
            logger.debug("fila: não consegui renovar a marca (%s)", exc)

    def limpar_ativo(self) -> None:
        try:
            os.remove(self._caminho_ativo)
        except OSError:
            pass  # a marca já não existe: nada a limpar

    def esta_ativo(self) -> bool:
        try:
            idade = self._relogio() - os.path.getmtime(self._caminho_ativo)
        except OSError:
            return False
        return idade <= ATIVO_VALIDADE_S


# ----------------------------------------------------------------------------
# Consumidor: roda dentro do `dl`
# ----------------------------------------------------------------------------
async def consumir(
    baixar: Callable[[list[str]], Awaitable[Any]],
    iniciais: Iterable[str] = (),
    fila: Fila | None = None,
    ao_link: Callable[[int, int, int], None] | None = None,
    problemas: Callable[[], int] | None = None,
    ao_falhar: Callable[[str], None] | None = None,
    batimento: float = BATIMENTO_S,
) -> Resultado:
    """Baixa os links dados e depois esvazia a fila, um link por vez.

    * `baixar([link])` faz o download de um link (`download_list_of_urls`).
    * `ao_link(indice, total, esperando)` avisa o banner antes de cada link.
    * `problemas()` devolve um contador que SÓ SOBE quando algo deu errado
      (faixa que falhou, álbum incompleto); se subir durante um link, o link
      conta como "não baixou".
    * Um link que levanta erro vai para `.falhou` e a fila segue.
    * Interrompido no meio (Ctrl+C, cancelamento), o link volta à fila.

    Levanta `FilaOcupada` se outro consumidor já está rodando.
    """
    f = fila or Fila()
    if f.esta_ativo():
        raise FilaOcupada(
            "Outro download da fila já está rodando "
            f"(se ele foi encerrado, aguarde {ATIVO_VALIDADE_S:.0f} s)."
        )
    voltaram = f.recuperar_andamento()
    if voltaram:
        logger.debug("fila: %d link(s) de uma execução anterior voltaram", voltaram)
    f.marcar_ativo()

    async def _bater() -> None:
        while True:
            await asyncio.sleep(batimento)
            f.marcar_ativo()

    coracao = asyncio.get_running_loop().create_task(_bater())
    resultado = Resultado()
    restantes = [str(link) for link in iniciais]
    try:
        while True:
            item: Item | None = None
            if restantes:
                link = restantes.pop(0)
            else:
                item = f.reivindicar()
                if item is None:
                    break
                link = item.link
            resultado.links += 1
            esperando = len(restantes) + f.contar_pendentes()
            if ao_link is not None:
                ao_link(resultado.links, resultado.links + esperando, esperando)
            antes = problemas() if problemas is not None else 0
            motivo = ""
            try:
                await baixar([link])
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                if item is not None:
                    f.devolver(item)  # não foi baixado: continua na fila
                raise
            except Exception as exc:
                motivo = f"{type(exc).__name__}: {exc}"
                logger.warning("Falha ao baixar %s: %s", link_curto(link), exc)
            if not motivo and problemas is not None and problemas() > antes:
                motivo = "faixas com falha ou álbum incompleto"
            if motivo:
                resultado.falharam.append(link)
                if ao_falhar is not None:
                    ao_falhar(link)
                if item is not None:
                    f.falhar(item, motivo)
                else:
                    f.registrar_falho(link, motivo)
            elif item is not None:
                f.concluir(item)
    finally:
        coracao.cancel()
        f.limpar_ativo()
    return resultado


# ----------------------------------------------------------------------------
# Linha de comando:  python3 -m qobuz_dl.fila adicionar|listar|repetir|limpar
# ----------------------------------------------------------------------------
def _texto_adicao(adicao: Adicao, nome: str = "") -> str:
    rotulo = rotulo_com_nome(adicao.link, nome)
    if adicao.resultado == "invalido":
        return (
            "Link não reconhecido: só álbum, faixa, playlist, artista ou "
            f"gravadora do Qobuz.\n{link_curto(adicao.link)}"
        )
    if adicao.resultado == "repetido":
        return f"Já está na fila (posição {adicao.posicao})\n{rotulo}"
    if adicao.resultado == "andamento":
        return f"Já está sendo baixado agora\n{rotulo}"
    if adicao.rodando:
        aviso = "Há um download em andamento: este link entra sozinho."
    else:
        aviso = "Nenhum download rodando: inicie o Long Command."
    return f"Na fila: posição {adicao.posicao}\n{rotulo}\n{aviso}"


def _aviso_sem_nome() -> str:
    """Linha final dizendo por que uma consulta de nome falhou (se falhou)."""
    motivo = ultimo_erro()
    return f"\n\nSem o nome: {motivo}" if motivo else ""


def _comando_adicionar(fila: Fila, links: list[str]) -> int:
    _ultimo_erro.clear()
    adicoes = [fila.adicionar(link) for link in links]
    textos = []
    for a in adicoes:
        nome = "" if a.resultado == "invalido" else fila.nome_do_link(a.link)
        textos.append(_texto_adicao(a, nome))
    print("\n\n".join(textos) + _aviso_sem_nome())
    return 2 if any(a.resultado == "invalido" for a in adicoes) else 0


def _comando_listar(fila: Fila) -> int:
    _ultimo_erro.clear()
    pendentes, andamento, falhos = fila.pendentes(), fila.em_andamento(), fila.falhos()
    partes = [f"{len(pendentes)} esperando"]
    if andamento:
        partes.append(f"{len(andamento)} baixando")
    if falhos:
        verbo = "falhou" if len(falhos) == 1 else "falharam"
        partes.append(f"{len(falhos)} {verbo}")
    ao_vivo = fila.ler_andamento()
    if fila.esta_ativo() or ao_vivo:
        estado = "Download em andamento."
    else:
        estado = "Nenhum download rodando."
    linhas = [f"Fila: {' · '.join(partes)}", estado]
    if ao_vivo:
        linhas.append(f"Agora: {ao_vivo['nome'] or ao_vivo['titulo']}")
        if ao_vivo["nome"] and ao_vivo["titulo"]:
            linhas.append(f"       {ao_vivo['titulo']}")
    fim_da_rede = time.monotonic() + LISTAR_ORCAMENTO_S
    for titulo, itens in (
        ("Baixando", andamento),
        ("Esperando", pendentes),
        ("Falharam", falhos),
    ):
        if not itens:
            continue
        linhas.append("")
        linhas.append(titulo)
        for n, item in enumerate(itens, start=1):
            extra = f" ({item.motivo})" if item.motivo else ""
            restante = fim_da_rede - time.monotonic()
            nome = fila.nome_do_link(
                item.link,
                rede=restante > 0,
                timeout=min(BUSCA_TIMEOUT_S, max(restante, 0.0)),
            )
            linhas.append(f"{n}. {rotulo_com_nome(item.link, nome)}{extra}")
    print("\n".join(linhas) + _aviso_sem_nome())
    return 0


def _comando_nome(fila: Fila, links: list[str]) -> int:
    """Mostra `[ ÁLBUM ] Artista — Título` de cada link (para o Atalho avisar antes)."""
    _ultimo_erro.clear()
    linhas = []
    for link in links:
        if chave_do_link(link) is None:
            linhas.append(f"Link não reconhecido: {link_curto(link)}")
        else:
            linhas.append(rotulo_com_nome(link, fila.nome_do_link(link)))
    print("\n".join(linhas) + _aviso_sem_nome())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m qobuz_dl.fila",
        description=(
            "Fila de links do modo Atalho do iOS. Para baixar a fila: "
            "qobuz-dl dl --atalho @fila"
        ),
    )
    parser.add_argument(
        "--dir", metavar="PASTA", default=None, help="pasta da fila (avançado)"
    )
    sub = parser.add_subparsers(dest="acao", required=True)
    add = sub.add_parser("adicionar", help="coloca um ou mais links na fila")
    add.add_argument("LINK", nargs="+")
    sub.add_parser("listar", help="mostra o que está na fila e o andamento")
    nm = sub.add_parser("nome", help="mostra o tipo, o artista e o título de um link")
    nm.add_argument("LINK", nargs="+")
    sub.add_parser("repetir", help="devolve à fila os links que falharam")
    lim = sub.add_parser("limpar", help="apaga links da fila")
    lim.add_argument(
        "o_que",
        nargs="?",
        choices=("tudo", "falhos", "pendentes"),
        default="tudo",
        help="o que apagar (padrão: pendentes e falhos)",
    )
    args = parser.parse_args(argv)
    fila = Fila(args.dir)
    if args.acao == "adicionar":
        return _comando_adicionar(fila, args.LINK)
    if args.acao == "listar":
        return _comando_listar(fila)
    if args.acao == "nome":
        return _comando_nome(fila, args.LINK)
    if args.acao == "repetir":
        total = fila.repetir()
        print(f"{total} link(s) voltaram para a fila.")
        return 0
    total = fila.limpar(
        pendentes=args.o_que in ("tudo", "pendentes"),
        falhos=args.o_que in ("tudo", "falhos"),
    )
    print(f"{total} link(s) apagados da fila.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
