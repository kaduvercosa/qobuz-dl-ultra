"""Faixa já baixada: confere pelo ID do Qobuz e nunca baixa de novo.

MÓDULO: decide se uma faixa (avulsa ou de playlist) já está no disco.
ONDE PROCURAR quando precisar mexer em "baixou de novo uma faixa que eu já tinha".

Regra: a faixa NÃO é baixada de novo quando existe uma cópia dela, identificada
pelo ID do Qobuz, cuja qualidade é IGUAL OU MAIOR que a pedida agora (a máxima,
27, serve para qualquer pedido). Só uma cópia de qualidade MENOR que a pedida
deixa o download acontecer (é um upgrade).

Como a cópia é confirmada (nesta ordem):

  1. o banco local (`downloads`) tem a faixa (ID Qobuz, media_type "track") numa
     qualidade >= a pedida e o arquivo registrado existe e não está vazio;
  2. a TAG do arquivo (QDL_TRACK_ID / QOBUZTRACKID) traz esse mesmo ID. Tag de
     OUTRA faixa recusa a cópia; arquivo sem tag de ID vale pelo banco;
  3. a qualidade REAL do arquivo (bit depth e taxa) não é menor que a que o
     Qobuz entregou quando ele foi baixado (colunas bit_depth/sampling_rate do
     banco). Isso evita tratar como "máxima" uma cópia que caiu para CD no meio
     do download;
  4. arquivo movido/renomeado: o banco aponta para um caminho que não existe
     mais, então o ÍNDICE DA BIBLIOTECA (`indice_biblioteca.py`) procura a tag
     com o ID; achando, o caminho do banco é corrigido e a faixa não é baixada;
  5. faixa que está no disco mas NÃO está no banco (banco perdido, arquivo
     copiado de fora): o mesmo índice a acha pela tag, a qualidade é medida no
     próprio arquivo e o programa REGISTRA a faixa no banco sozinho. Não precisa
     rodar `--sync-db`.

O índice é incremental e guardado em arquivo: a primeira execução lê as tags da
biblioteca toda (demora, e avisa); depois só olha os arquivos novos ou alterados.
Só é consultado quando o banco não resolve, ou seja, uma vez por execução.

Não cobre: faixa que só existe no disco em qualidade MENOR que a pedida mas que é
a melhor que o Qobuz tem (ex.: faixa só em CD com pedido 24-bit). Isso o Qobuz é
quem diz; quem pergunta é o reaproveitamento do álbum (`reuso.py`), que também usa
este índice. `--no-db` continua sendo a forma de FORÇAR um novo download.

Nunca levanta exceção: qualquer problema devolve None e o fluxo normal segue
(o pior caso é baixar, nunca pular por engano).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from qobuz_dl import indice_biblioteca
from qobuz_dl.indice_biblioteca import ArquivoIndexado

logger = logging.getLogger(__name__)

# Ordem de qualidade do Qobuz (format_id): MP3 < CD < 24/96 < 24/192.
_RANK = {5: 0, 6: 1, 7: 2, 27: 3}
_NOMES = {5: "MP3 320", 6: "CD 16-bit", 7: "24-bit/96 kHz", 27: "24-bit/192 kHz"}

# Resultado de conferir UM arquivo.
OK_TAG = "tag"  # a tag do arquivo traz o ID
OK_BANCO = "banco"  # arquivo sem tag de ID: vale o registro do banco
AUSENTE = "ausente"  # o arquivo não existe (ou está vazio)
OUTRA_FAIXA = "outra_faixa"  # a tag traz o ID de outra faixa
ABAIXO = "abaixo"  # qualidade real menor que a entregue na época
ILEGIVEL = "ilegivel"  # não dá para ler o áudio


@dataclass(frozen=True)
class FaixaExistente:
    caminho: str
    qualidade: int  # qualidade com que foi registrada (chave do banco)
    certeza: str  # "tag" | "banco" | "biblioteca"

    @property
    def descricao(self) -> str:
        return _NOMES.get(self.qualidade, f"qualidade {self.qualidade}")


def _rank(qualidade: Any) -> int:
    try:
        return _RANK.get(int(qualidade), -1)
    except (TypeError, ValueError):
        return -1


# ---------------------------------------------------------------------------
# Leitura de arquivo (síncrona: chamar sempre em thread)
# ---------------------------------------------------------------------------
def _ler_id(caminho: str) -> Optional[str]:
    from qobuz_dl.playlist_links import read_track_id

    valor = read_track_id(caminho)
    return str(valor) if valor else None


def _ler_info(caminho: str) -> Optional[tuple[str, int, float]]:
    """(formato, bits, taxa_khz) reais do arquivo; None se não conseguir ler."""
    try:
        from mutagen import File

        audio = File(caminho)
        info: Any = getattr(audio, "info", None)
        if info is None:
            return None
        if caminho.lower().endswith(".flac"):
            return (
                "flac",
                int(getattr(info, "bits_per_sample", 0) or 0),
                float(getattr(info, "sample_rate", 0) or 0) / 1000,
            )
        return ("mp3", 0, float(getattr(info, "sample_rate", 0) or 0) / 1000)
    except Exception:
        return None


def _numero(valor: Any) -> Optional[float]:
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if numero > 0 else None


def _conferir_arquivo(caminho: str, track_id: str, linha: dict[str, Any]) -> str:
    """Confere UM arquivo candidato. Devolve OK_TAG, OK_BANCO ou o motivo da recusa."""
    try:
        if not caminho or not os.path.isfile(caminho) or os.path.getsize(caminho) <= 0:
            return AUSENTE
    except OSError:
        return AUSENTE

    id_tag = _ler_id(caminho)
    if id_tag and id_tag != track_id:
        return OUTRA_FAIXA

    info = _ler_info(caminho)
    if info is None:
        return ILEGIVEL
    formato, bits, taxa = info

    # Qualidade real x o que o Qobuz entregou quando foi baixada.
    if formato == "flac":
        bits_db = _numero(linha.get("bit_depth"))
        taxa_db = _numero(linha.get("sampling_rate"))
        if taxa_db is not None and taxa_db > 1000:
            taxa_db /= 1000
        if bits_db is not None and bits < bits_db:
            return ABAIXO
        if taxa_db is not None and taxa < taxa_db - 0.05:
            return ABAIXO
    elif str(linha.get("file_format") or "").upper() == "FLAC":
        return ABAIXO  # o banco diz FLAC e o arquivo é MP3

    return OK_TAG if id_tag else OK_BANCO


# ---------------------------------------------------------------------------
# Banco (síncrono, em thread; só abre se o arquivo já existe: nunca cria banco)
# ---------------------------------------------------------------------------
def linhas_da_faixa(db_path: str, track_id: str) -> list[dict[str, Any]]:
    if not db_path or not os.path.isfile(db_path):
        return []
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        cur = conn.execute(
            "SELECT quality, saved_path, status, bit_depth, sampling_rate, "
            "file_format FROM downloads WHERE id=? AND media_type='track'",
            (track_id,),
        )
        colunas = [c[0] for c in cur.description]
        return [dict(zip(colunas, linha)) for linha in cur.fetchall()]
    finally:
        conn.close()


def _corrigir_caminho(
    db_path: str, track_id: str, qualidade: Any, novo_caminho: str
) -> None:
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        with conn:
            conn.execute(
                "UPDATE downloads SET saved_path=? "
                "WHERE id=? AND quality=? AND media_type='track'",
                (novo_caminho, track_id, qualidade),
            )
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Índice da biblioteca (persistente: ver indice_biblioteca.py)
# ---------------------------------------------------------------------------
_atualizados: set[str] = set()
_trava: Optional[asyncio.Lock] = None


def _atualizar_indice(raiz: str, index_path: str) -> dict[str, int]:
    # lambdas: as funções de leitura são procuradas na hora da chamada (testes
    # as trocam por leitores falsos)
    return indice_biblioteca.atualizar(
        raiz, index_path, lambda c: _ler_id(c), lambda c: _ler_info(c)
    )


def _avisar(mensagem: str) -> None:
    try:
        from qobuz_dl import ui

        ui.info(mensagem)
    except Exception:
        logger.info(mensagem)


async def _indice_em_dia(db_path: Any, raiz: Any) -> str:
    """Atualiza o índice UMA vez por execução (downloads em paralelo esperam)."""
    global _trava
    index_path = indice_biblioteca.caminho_do_indice(db_path)
    chave = f"{index_path}|{os.path.abspath(os.path.expanduser(str(raiz)))}"
    if chave in _atualizados:
        return index_path
    if _trava is None:
        _trava = asyncio.Lock()
    async with _trava:
        if chave not in _atualizados:
            primeira = not os.path.exists(index_path)
            if primeira:
                _avisar(
                    "Indexando a biblioteca (só na primeira vez; depois só os "
                    "arquivos novos)..."
                )
            stats = await asyncio.to_thread(_atualizar_indice, str(raiz), index_path)
            if primeira or stats["lidos"] >= 200:
                _avisar(f"Biblioteca indexada: {stats['total']} arquivos.")
            _atualizados.add(chave)
    return index_path


def esquecer_indice() -> None:
    """Esquece que o índice já foi atualizado nesta execução (testes)."""
    _atualizados.clear()


async def arquivos_da_faixa(
    db_path: Any, track_id: Any, raiz_biblioteca: Any
) -> list[ArquivoIndexado]:
    """Arquivos da biblioteca com a tag deste ID, de qualquer qualidade, do melhor
    para o pior. Atualiza o índice se ainda não foi atualizado nesta execução.
    Nunca levanta exceção (devolve [])."""
    try:
        if not db_path or not raiz_biblioteca or track_id in (None, ""):
            return []
        if not os.path.isfile(str(db_path)) or not os.path.isdir(
            os.path.expanduser(str(raiz_biblioteca))
        ):
            return []
        index_path = await _indice_em_dia(db_path, raiz_biblioteca)
        achados = await asyncio.to_thread(
            indice_biblioteca.consultar, index_path, str(track_id)
        )
        return sorted(
            achados,
            key=lambda a: indice_biblioteca.nivel_de_qualidade(
                a.formato, a.bits, a.taxa_khz
            ),
            reverse=True,
        )
    except Exception:
        logger.debug("Índice da biblioteca indisponível", exc_info=True)
        return []


def _registrar_no_banco(db_path: str, track_id: str, arq: ArquivoIndexado, nivel: int):
    """Grava no banco uma faixa que estava só no disco (a qualidade é a REAL)."""
    from qobuz_dl.playlist_links import _db_register_tracks

    flac = arq.formato == "flac"
    reg = {
        "quality": nivel,
        "file_format": "FLAC" if flac else "MP3",
        "quality_met": 1,
        "bit_depth": str(arq.bits) if flac else None,
        "sampling_rate": f"{arq.taxa_khz:g}" if flac else None,
    }
    _db_register_tracks(db_path, {track_id: arq.caminho}, reg, lambda c: _ler_id(c))


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
# Pergunta ao Qobuz o MÁXIMO da faixa: (bit depth, taxa em kHz) ou None.
ConsultarMaximo = Callable[[str], Awaitable[Optional[tuple[int, float]]]]


async def localizar_faixa_ja_baixada(
    db_path: Any,
    track_id: Any,
    qualidade_pedida: Any,
    raiz_biblioteca: Any = None,
    consultar_maximo: Optional[ConsultarMaximo] = None,
) -> Optional[FaixaExistente]:
    """Cópia já baixada desta faixa (por ID) em qualidade >= a pedida, ou None.

    `consultar_maximo` (opcional) deixa reconhecer como "máxima" uma faixa do disco
    que fica abaixo do nível pedido porque o Qobuz não tem mais que aquilo (ex.:
    pedido 24-bit/192 kHz e a faixa só existe em 24-bit/96 kHz ou em CD). Só é
    chamada para faixa achada no disco SEM registro no banco, então custa pouco.
    """
    try:
        return await _localizar(
            db_path, track_id, qualidade_pedida, raiz_biblioteca, consultar_maximo
        )
    except Exception:
        logger.debug("Checagem de faixa já baixada falhou; baixando", exc_info=True)
        return None


async def _localizar(
    db_path: Any,
    track_id: Any,
    qualidade_pedida: Any,
    raiz_biblioteca: Any,
    consultar_maximo: Optional[ConsultarMaximo] = None,
) -> Optional[FaixaExistente]:
    if not db_path or track_id in (None, ""):
        return None
    track_id = str(track_id)
    minimo = _rank(qualidade_pedida)
    if minimo < 0 or not os.path.isfile(str(db_path)):
        return None

    linhas = await asyncio.to_thread(linhas_da_faixa, str(db_path), track_id)
    candidatas = [
        linha
        for linha in linhas
        if _rank(linha.get("quality")) >= minimo
        and (linha.get("status") in (None, "", "downloaded"))
    ]
    candidatas.sort(key=lambda linha: _rank(linha.get("quality")), reverse=True)

    sumidas: list[dict[str, Any]] = []
    for linha in candidatas:
        caminho = str(linha.get("saved_path") or "")
        estado = await asyncio.to_thread(_conferir_arquivo, caminho, track_id, linha)
        if estado in (OK_TAG, OK_BANCO):
            return FaixaExistente(caminho, int(linha["quality"]), estado)
        if estado == AUSENTE:
            sumidas.append(linha)

    if not raiz_biblioteca:
        return None
    arquivos = await arquivos_da_faixa(db_path, track_id, raiz_biblioteca)
    if not arquivos:
        return None

    # Registro cujo arquivo sumiu do caminho: ele foi movido/renomeado?
    for linha in sumidas:
        for arq in arquivos:
            estado = await asyncio.to_thread(
                _conferir_arquivo, arq.caminho, track_id, linha
            )
            if estado == OK_TAG:
                await asyncio.to_thread(
                    _corrigir_caminho,
                    str(db_path),
                    track_id,
                    linha["quality"],
                    arq.caminho,
                )
                return FaixaExistente(arq.caminho, int(linha["quality"]), "biblioteca")

    # Sem registro (ou registro que não serve): a faixa está no disco e o banco
    # não sabe. A qualidade é medida no arquivo; se serve, registra sozinho.
    maximo_do_qobuz: Optional[tuple[int, float]] = None
    consultou = False
    for arq in arquivos:
        nivel = indice_biblioteca.nivel_de_qualidade(
            arq.formato, arq.bits, arq.taxa_khz
        )
        qualidade_registrada = nivel
        if _rank(nivel) < minimo:
            # Abaixo do nível pedido. Se o Qobuz não tem mais que isto para esta
            # faixa, o arquivo JÁ É o máximo e não há o que baixar.
            if consultar_maximo is None or arq.formato != "flac":
                continue
            if not consultou:
                consultou = True
                maximo_do_qobuz = await _perguntar_maximo(consultar_maximo, track_id)
            if not _atinge_o_maximo(arq, maximo_do_qobuz):
                continue
            qualidade_registrada = int(qualidade_pedida)
        estado = await asyncio.to_thread(_conferir_arquivo, arq.caminho, track_id, {})
        if estado == OK_TAG:
            await asyncio.to_thread(
                _registrar_no_banco,
                str(db_path),
                track_id,
                arq,
                qualidade_registrada,
            )
            return FaixaExistente(arq.caminho, qualidade_registrada, "biblioteca")
    return None


async def _perguntar_maximo(
    consultar: ConsultarMaximo, track_id: str
) -> Optional[tuple[int, float]]:
    try:
        return await consultar(track_id)
    except Exception:
        logger.debug("Não consegui consultar o máximo da faixa", exc_info=True)
        return None


def _atinge_o_maximo(arq: ArquivoIndexado, maximo: Optional[tuple[int, float]]) -> bool:
    if not maximo:
        return False
    bits_max, taxa_max = maximo
    if bits_max <= 0 or taxa_max <= 0:
        return False  # resposta sem o dado: não dá para afirmar que é o máximo
    return arq.bits >= bits_max and arq.taxa_khz >= taxa_max - 0.05
