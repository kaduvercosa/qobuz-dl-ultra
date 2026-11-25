"""Índice persistente e incremental da biblioteca: ID do Qobuz -> arquivos.

MÓDULO: lembra quais arquivos da biblioteca têm qual ID do Qobuz (tag), para que
`faixa_existente.py` reconheça sozinho uma faixa que está no disco mas não está
no banco (banco perdido, arquivo copiado de fora, registro apagado). Substitui
a necessidade de rodar `--sync-db` à mão.
ONDE PROCURAR quando a faixa "existe mas o programa não a viu".

Por que um índice em arquivo e não uma varredura a cada execução: ler a tag de
milhares de arquivos custa minutos no a-Shell. Aqui a tag só é lida na PRIMEIRA
vez e para arquivo NOVO ou ALTERADO (muda o tamanho ou a data de modificação);
nas outras execuções basta olhar tamanho e data de cada arquivo.

O índice fica ao lado do banco de downloads (`track_index.db`), é só cache (pode
ser apagado sem perder nada: é refeito) e nunca é a fonte da verdade: quem
confirma a faixa é `faixa_existente._conferir_arquivo` (tag, qualidade, arquivo).

As funções são SÍNCRONAS (disco e SQLite): chamar sempre em thread.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any, Callable, NamedTuple, Optional

logger = logging.getLogger(__name__)

NOME_DO_INDICE = "track_index.db"
EXTENSOES_DE_AUDIO = (".flac", ".mp3")

LeitorDeId = Callable[[str], Optional[str]]
LeitorDeInfo = Callable[[str], Optional[tuple[str, int, float]]]


class ArquivoIndexado(NamedTuple):
    caminho: str
    formato: str  # "flac" | "mp3" ("" se não deu para ler)
    bits: int
    taxa_khz: float


def caminho_do_indice(db_path: Any) -> str:
    """O índice mora na mesma pasta do banco de downloads."""
    return os.path.join(os.path.dirname(os.path.abspath(str(db_path))), NOME_DO_INDICE)


def _estatisticas(caminho: str) -> Optional[os.stat_result]:
    """`os.stat` que devolve None se o arquivo sumiu ou não pode ser lido."""
    try:
        return os.stat(caminho)
    except OSError:
        return None


def _abrir(index_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(index_path, timeout=10)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS arquivos ("
        "caminho TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, "
        "tamanho INTEGER NOT NULL, track_id TEXT, formato TEXT, "
        "bits INTEGER, taxa_khz REAL)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS ix_arquivos_id ON arquivos(track_id)")
    return conn


def atualizar(
    raiz: Any,
    index_path: str,
    ler_id: LeitorDeId,
    ler_info: LeitorDeInfo,
) -> dict[str, int]:
    """Põe o índice em dia com a pasta `raiz`; devolve {"total","lidos","removidos"}.

    `lidos` = arquivos cuja tag foi lida agora (novos ou alterados).
    """
    stats = {"total": 0, "lidos": 0, "removidos": 0}
    raiz = os.path.abspath(os.path.expanduser(str(raiz)))
    if not os.path.isdir(raiz):
        return stats

    conn = _abrir(index_path)
    try:
        conhecidos = {
            caminho: (mtime, tamanho)
            for caminho, mtime, tamanho in conn.execute(
                "SELECT caminho, mtime_ns, tamanho FROM arquivos"
            )
        }
        vistos: set[str] = set()
        with conn:
            for pasta, _dirs, nomes in os.walk(raiz):
                for nome in nomes:
                    if not nome.lower().endswith(EXTENSOES_DE_AUDIO):
                        continue
                    caminho = os.path.join(pasta, nome)
                    st = _estatisticas(caminho)
                    if st is None:
                        continue  # sumiu no meio da varredura: o próximo ciclo acerta
                    vistos.add(caminho)
                    stats["total"] += 1
                    if conhecidos.get(caminho) == (st.st_mtime_ns, st.st_size):
                        continue
                    track_id = ler_id(caminho)
                    info = ler_info(caminho) if track_id else None
                    formato, bits, taxa = info if info else ("", 0, 0.0)
                    conn.execute(
                        "INSERT OR REPLACE INTO arquivos (caminho, mtime_ns, tamanho, "
                        "track_id, formato, bits, taxa_khz) VALUES (?,?,?,?,?,?,?)",
                        (
                            caminho,
                            st.st_mtime_ns,
                            st.st_size,
                            str(track_id) if track_id else None,
                            formato,
                            bits,
                            taxa,
                        ),
                    )
                    stats["lidos"] += 1
            sumidos = [c for c in conhecidos if c not in vistos]
            for i in range(0, len(sumidos), 400):
                parte = sumidos[i : i + 400]
                conn.execute(
                    "DELETE FROM arquivos WHERE caminho IN (%s)"
                    % ",".join("?" * len(parte)),
                    parte,
                )
            stats["removidos"] = len(sumidos)
    finally:
        conn.close()
    return stats


def consultar(index_path: str, track_id: Any) -> list[ArquivoIndexado]:
    """Arquivos do índice com esta tag de ID (vazio se não há índice)."""
    if not os.path.isfile(index_path):
        return []
    conn = sqlite3.connect(index_path, timeout=10)
    try:
        linhas = conn.execute(
            "SELECT caminho, formato, bits, taxa_khz FROM arquivos WHERE track_id=?",
            (str(track_id),),
        ).fetchall()
    finally:
        conn.close()
    return [
        ArquivoIndexado(c, f or "", int(b or 0), float(t or 0)) for c, f, b, t in linhas
    ]


def nivel_de_qualidade(formato: str, bits: int, taxa_khz: float) -> int:
    """Nível do Qobuz (format_id) que o arquivo REAL atinge: 5, 6, 7 ou 27.

    MP3 -> 5; FLAC até 16 bits -> 6 (CD); 24 bits até 96 kHz -> 7; acima -> 27.
    0 se não deu para ler o arquivo.
    """
    if formato == "mp3":
        return 5
    if formato != "flac":
        return 0
    if bits <= 16:
        return 6
    return 7 if taxa_khz <= 96.05 else 27
