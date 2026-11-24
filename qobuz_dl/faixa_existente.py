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
     mais, então a biblioteca é varrida UMA vez por execução procurando a tag
     com o ID; achando, o caminho do banco é corrigido e a faixa não é baixada.

Não cobre: faixa que não tem registro nenhum no banco (banco perdido, arquivo
copiado de fora). Para isso, `qobuz-dl --sync-db` reconstrói o banco lendo as
tags da biblioteca. `--no-db` continua sendo a forma de FORÇAR um novo download.

Nunca levanta exceção: qualquer problema devolve None e o fluxo normal segue
(o pior caso é baixar, nunca pular por engano).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

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
def _linhas_da_faixa(db_path: str, track_id: str) -> list[dict[str, Any]]:
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
# Índice da biblioteca (só para arquivo movido; uma varredura por execução)
# ---------------------------------------------------------------------------
_indices: dict[str, dict[str, list[str]]] = {}
_trava: Optional[asyncio.Lock] = None


def _varrer(raiz: str) -> dict[str, list[str]]:
    from qobuz_dl.playlist_links import scan_track_paths

    return dict(scan_track_paths(raiz, _ler_id))


async def _indice_da_biblioteca(raiz: str) -> dict[str, list[str]]:
    global _trava
    chave = os.path.abspath(os.path.expanduser(str(raiz)))
    if chave in _indices:
        return _indices[chave]
    if _trava is None:
        _trava = asyncio.Lock()
    async with _trava:  # downloads em paralelo não varrem duas vezes
        if chave not in _indices:
            logger.info("Procurando faixa movida: varrendo a biblioteca (uma vez).")
            _indices[chave] = await asyncio.to_thread(_varrer, chave)
    return _indices[chave]


def esquecer_indice() -> None:
    """Descarta o índice em memória (testes; ou depois de reorganizar a pasta)."""
    _indices.clear()


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
async def localizar_faixa_ja_baixada(
    db_path: Any,
    track_id: Any,
    qualidade_pedida: Any,
    raiz_biblioteca: Any = None,
) -> Optional[FaixaExistente]:
    """Cópia já baixada desta faixa (por ID) em qualidade >= a pedida, ou None."""
    try:
        return await _localizar(db_path, track_id, qualidade_pedida, raiz_biblioteca)
    except Exception:
        logger.debug("Checagem de faixa já baixada falhou; baixando", exc_info=True)
        return None


async def _localizar(
    db_path: Any, track_id: Any, qualidade_pedida: Any, raiz_biblioteca: Any
) -> Optional[FaixaExistente]:
    if not db_path or track_id in (None, ""):
        return None
    track_id = str(track_id)
    minimo = _rank(qualidade_pedida)
    if minimo < 0:
        return None

    linhas = await asyncio.to_thread(_linhas_da_faixa, str(db_path), track_id)
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

    # Registro cujo arquivo sumiu do caminho: ele foi movido/renomeado?
    if sumidas and raiz_biblioteca:
        indice = await _indice_da_biblioteca(str(raiz_biblioteca))
        for linha in sumidas:
            for novo in indice.get(track_id, []):
                estado = await asyncio.to_thread(
                    _conferir_arquivo, novo, track_id, linha
                )
                if estado == OK_TAG:
                    await asyncio.to_thread(
                        _corrigir_caminho,
                        str(db_path),
                        track_id,
                        linha["quality"],
                        novo,
                    )
                    return FaixaExistente(novo, int(linha["quality"]), "biblioteca")
    return None
