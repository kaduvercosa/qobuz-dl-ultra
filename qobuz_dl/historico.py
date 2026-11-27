"""Histórico de faixas baixadas: uma linha para CADA vez que a faixa foi baixada.

MÓDULO: guarda, no MESMO banco dos downloads (`qobuz_dl.db`), a tabela
`track_history`: ID, ISRC, título, artista, formato, qualidade real, pasta e
data. A tabela `downloads` só sabe o ESTADO ATUAL de cada faixa (uma linha por
ID e qualidade); aqui fica a LINHA DO TEMPO, que ela não guarda.

Para que serve:
  * `duplicates --historico` lista as faixas baixadas mais de uma vez;
  * o download avisa quando a MESMA GRAVAÇÃO (mesmo ISRC) já foi baixada com
    outro ID do Qobuz (edição deluxe, relançamento, remaster, coletânea). O
    aviso é só informativo: o download continua normalmente.

Só registra download REAL: faixa reaproveitada do disco (`faixa_existente`) ou
pulada não entra. A tabela é criada na primeira gravação (`CREATE TABLE IF NOT
EXISTS`), então um banco antigo não precisa de migração. Tudo é "melhor
esforço": qualquer erro de banco vira registro de log e NUNCA interrompe um
download. As funções são SÍNCRONAS (SQLite): chamar sempre em thread.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any, NamedTuple, Optional

logger = logging.getLogger(__name__)

TABELA = "track_history"
_TIMEOUT_S = 10


class Registro(NamedTuple):
    track_id: str
    isrc: str
    titulo: str
    artista: str
    formato: str  # "FLAC" | "MP3"
    bit_depth: str  # "" quando não se aplica (MP3) ou não se sabe
    sampling_rate: str
    pasta: str
    baixado_em: str  # "AAAA-MM-DD HH:MM:SS" (hora local)

    @property
    def qualidade(self) -> str:
        """`FLAC 24/96` (ou só `MP3`, ou `FLAC` se não se sabe mais nada)."""
        if self.bit_depth and self.sampling_rate:
            return f"{self.formato} {self.bit_depth}/{self.sampling_rate}"
        return self.formato

    @property
    def dia(self) -> str:
        """`12/11` (dia/mês); vazio se a data não for legível."""
        return dia_e_mes(self.baixado_em)


class Repetida(NamedTuple):
    track_id: str
    titulo: str
    artista: str
    vezes: int
    ultima: str  # "AAAA-MM-DD HH:MM:SS"


def normalizar_isrc(isrc: Any) -> str:
    """ISRC sem espaços, hífens nem caixa baixa (`us-um7-17-03861` -> `USUM71703861`)."""
    if not isrc:
        return ""
    return "".join(ch for ch in str(isrc) if ch.isalnum()).upper()


def dia_e_mes(quando: str) -> str:
    try:
        return datetime.strptime(str(quando)[:10], "%Y-%m-%d").strftime("%d/%m")
    except ValueError:
        return ""


def _texto(valor: Any) -> str:
    return "" if valor is None else str(valor)


def _abrir(db_path: Any) -> sqlite3.Connection:
    """Abre o banco e garante a tabela (para GRAVAR)."""
    conn = sqlite3.connect(str(db_path), timeout=_TIMEOUT_S)
    try:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {TABELA} (
                "n" integer PRIMARY KEY AUTOINCREMENT,
                "track_id" text NOT NULL,
                "isrc" text NOT NULL DEFAULT '',
                "titulo" text NOT NULL DEFAULT '',
                "artista" text NOT NULL DEFAULT '',
                "formato" text NOT NULL DEFAULT '',
                "bit_depth" text NOT NULL DEFAULT '',
                "sampling_rate" text NOT NULL DEFAULT '',
                "pasta" text NOT NULL DEFAULT '',
                "baixado_em" text NOT NULL DEFAULT ''
            )"""
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABELA}_id ON {TABELA}(track_id)"
        )
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{TABELA}_isrc ON {TABELA}(isrc)")
    except sqlite3.Error:
        conn.close()
        raise
    return conn


def _abrir_para_ler(db_path: Any) -> Optional[sqlite3.Connection]:
    """Abre o banco só para LER; None se não há banco ou ainda não há histórico."""
    if not db_path or not os.path.isfile(str(db_path)):
        return None
    conn = sqlite3.connect(str(db_path), timeout=_TIMEOUT_S)
    try:
        existe = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (TABELA,)
        ).fetchone()
    except sqlite3.Error:
        conn.close()
        return None
    if not existe:
        conn.close()
        return None
    return conn


def registrar(
    db_path: Any,
    track_id: Any,
    isrc: Any,
    titulo: Any,
    artista: Any,
    formato: Any,
    bit_depth: Any,
    sampling_rate: Any,
    pasta: Any,
    quando: Optional[datetime] = None,
) -> bool:
    """Grava um download concluído. Devolve False (sem levantar) se não deu."""
    if not db_path or track_id is None or str(track_id) == "":
        return False
    momento = (quando or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    try:
        with closing(_abrir(db_path)) as conn, conn:
            conn.execute(
                f"INSERT INTO {TABELA} (track_id, isrc, titulo, artista, formato, "
                "bit_depth, sampling_rate, pasta, baixado_em) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    str(track_id),
                    normalizar_isrc(isrc),
                    _texto(titulo),
                    _texto(artista),
                    _texto(formato),
                    _texto(bit_depth),
                    _texto(sampling_rate),
                    _texto(pasta),
                    momento,
                ),
            )
        return True
    except (sqlite3.Error, OSError) as exc:
        logger.debug(
            "Não foi possível gravar o histórico da faixa %s: %s", track_id, exc
        )
        return False


def _como_registro(linha: tuple[Any, ...]) -> Registro:
    return Registro(*(_texto(v) for v in linha))


_COLUNAS = (
    "track_id, isrc, titulo, artista, formato, bit_depth, sampling_rate, "
    "pasta, baixado_em"
)


def ultima_com_isrc(db_path: Any, isrc: Any, ignorar_id: Any) -> Optional[Registro]:
    """Download mais recente de OUTRO ID com o mesmo ISRC; None se não há.

    Sem ISRC (faixa sem o código) nunca acha nada: não dá para afirmar que é a
    mesma gravação.
    """
    codigo = normalizar_isrc(isrc)
    if not codigo:
        return None
    try:
        conn = _abrir_para_ler(db_path)
        if conn is None:
            return None
        with closing(conn):
            linha = conn.execute(
                f"SELECT {_COLUNAS} FROM {TABELA} WHERE isrc=? AND track_id<>? "
                "ORDER BY baixado_em DESC, n DESC LIMIT 1",
                (codigo, str(ignorar_id)),
            ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("Não foi possível consultar o histórico: %s", exc)
        return None
    return _como_registro(linha) if linha else None


def repetidas(db_path: Any, limite: int = 20) -> list[Repetida]:
    """Faixas (mesmo ID) baixadas mais de uma vez: mais vezes primeiro, depois a
    mais recente. `limite` <= 0 devolve todas."""
    try:
        conn = _abrir_para_ler(db_path)
        if conn is None:
            return []
        with closing(conn):
            sql = (
                "SELECT track_id, "
                "(SELECT titulo FROM {t} h2 WHERE h2.track_id=h.track_id "
                "ORDER BY h2.n DESC LIMIT 1), "
                "(SELECT artista FROM {t} h3 WHERE h3.track_id=h.track_id "
                "ORDER BY h3.n DESC LIMIT 1), "
                "COUNT(*), MAX(baixado_em) FROM {t} h "
                "GROUP BY track_id HAVING COUNT(*) > 1 "
                "ORDER BY COUNT(*) DESC, MAX(baixado_em) DESC, track_id"
            ).format(t=TABELA)
            params: tuple[int, ...] = ()
            if limite > 0:
                sql += " LIMIT ?"
                params = (int(limite),)
            linhas = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        logger.debug("Não foi possível listar as repetidas: %s", exc)
        return []
    return [
        Repetida(str(i), _texto(t), _texto(a), int(v), _texto(u))
        for i, t, a, v, u in linhas
    ]


def tem_historico(db_path: Any) -> bool:
    """Já existe ao menos um download registrado?"""
    try:
        conn = _abrir_para_ler(db_path)
        if conn is None:
            return False
        with closing(conn):
            achou = conn.execute(f"SELECT 1 FROM {TABELA} LIMIT 1").fetchone()
            return achou is not None
    except sqlite3.Error:
        return False


def total_de_repetidas(db_path: Any) -> int:
    """Quantas faixas (IDs) foram baixadas mais de uma vez."""
    return len(repetidas(db_path, limite=0))


def isrc_com_ids_diferentes(db_path: Any) -> int:
    """Quantos ISRC aparecem com mais de um ID do Qobuz (mesma gravação em
    edições diferentes)."""
    try:
        conn = _abrir_para_ler(db_path)
        if conn is None:
            return 0
        with closing(conn):
            (n,) = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT isrc FROM {TABELA} WHERE isrc<>'' "
                "GROUP BY isrc HAVING COUNT(DISTINCT track_id) > 1)"
            ).fetchone()
    except sqlite3.Error as exc:
        logger.debug("Não foi possível contar os ISRC repetidos: %s", exc)
        return 0
    return int(n)


def mostrar_aviso(anterior: Registro, id_atual: Any) -> None:
    """Aviso de "mesma gravação, outro ID" (a linha em amarelo e três em cinza)."""
    from qobuz_dl import ui

    ui.warn(ui.tail("Mesma gravação, outro ID", f"ISRC {anterior.isrc}"))
    quando = f"{anterior.dia} · " if anterior.dia else ""
    antes = f"    antes: {quando}{anterior.qualidade} · ID {anterior.track_id}"
    ui.info(ui.dim(antes))
    ui.info(ui.dim(f"    agora: ID {id_atual} · baixando"))
