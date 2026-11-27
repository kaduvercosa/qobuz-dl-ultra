"""Histórico de faixas baixadas (qobuz_dl/historico.py) e o aviso de ISRC repetido.

A tabela `track_history` mora no banco dos downloads e guarda UMA LINHA POR
DOWNLOAD. Só serve para consulta: nada aqui pode quebrar um download.
"""

import sqlite3
from datetime import datetime
from types import SimpleNamespace

import pytest

from qobuz_dl import historico as h

pytestmark = pytest.mark.unit


def _grava(db, track_id, isrc="USUM71703861", titulo="Hurt", quando=None, **kw):
    base = dict(
        artista="Nine Inch Nails",
        formato="FLAC",
        bit_depth="24",
        sampling_rate="96",
        pasta="/m/a",
    )
    base.update(kw)
    return h.registrar(
        db,
        track_id,
        isrc,
        titulo,
        base["artista"],
        base["formato"],
        base["bit_depth"],
        base["sampling_rate"],
        base["pasta"],
        quando=quando,
    )


def test_normaliza_isrc():
    assert h.normalizar_isrc("us-um7-17-03861") == "USUM71703861"
    assert h.normalizar_isrc(" usum71703861 ") == "USUM71703861"
    assert h.normalizar_isrc(None) == ""
    assert h.normalizar_isrc("") == ""


def test_registra_uma_linha_por_download_e_cria_a_tabela(tmp_path):
    db = str(tmp_path / "qobuz_dl.db")
    assert _grava(db, "100") is True
    assert _grava(db, "100") is True
    conn = sqlite3.connect(db)
    linhas = conn.execute(
        "SELECT track_id, isrc, formato FROM track_history"
    ).fetchall()
    conn.close()
    assert linhas == [("100", "USUM71703861", "FLAC")] * 2


def test_registrar_sem_banco_ou_sem_id_devolve_false_sem_levantar(tmp_path):
    assert h.registrar(None, "1", "", "t", "a", "FLAC", "", "", "") is False
    assert (
        h.registrar(str(tmp_path / "x.db"), None, "", "t", "a", "", "", "", "") is False
    )
    # pasta que não existe: sqlite não abre o arquivo -> False, não exceção
    assert _grava(str(tmp_path / "nao" / "existe" / "x.db"), "1") is False


def test_ler_nunca_cria_a_tabela_nem_o_arquivo(tmp_path):
    inexistente = str(tmp_path / "nada.db")
    assert h.ultima_com_isrc(inexistente, "USUM71703861", "1") is None
    assert h.repetidas(inexistente) == []
    assert h.total_de_repetidas(inexistente) == 0
    assert h.isrc_com_ids_diferentes(inexistente) == 0
    assert h.tem_historico(inexistente) is False
    assert not (tmp_path / "nada.db").exists()

    # banco dos downloads que ainda não tem histórico: continua sem tabela
    db = str(tmp_path / "qobuz_dl.db")
    sqlite3.connect(db).execute("CREATE TABLE downloads (id text)").connection.close()
    assert h.repetidas(db) == []
    assert h.tem_historico(db) is False
    conn = sqlite3.connect(db)
    nomes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master")]
    conn.close()
    assert "track_history" not in nomes


def test_ultima_com_isrc_acha_outro_id_e_ignora_o_proprio(tmp_path):
    db = str(tmp_path / "qobuz_dl.db")
    _grava(db, "5966783", quando=datetime(2026, 11, 12, 10, 0, 0))
    _grava(db, "5966783", quando=datetime(2026, 11, 20, 10, 0, 0))

    # o mesmo ID não conta como "outro ID"
    assert h.ultima_com_isrc(db, "USUM71703861", "5966783") is None

    achou = h.ultima_com_isrc(db, "us-um7-17-03861", "19290321")
    assert achou is not None
    assert (achou.track_id, achou.isrc, achou.dia) == (
        "5966783",
        "USUM71703861",
        "20/11",
    )
    assert achou.qualidade == "FLAC 24/96"


def test_sem_isrc_nunca_afirma_que_e_a_mesma_gravacao(tmp_path):
    db = str(tmp_path / "qobuz_dl.db")
    _grava(db, "100", isrc="")
    assert h.ultima_com_isrc(db, "", "200") is None
    assert h.ultima_com_isrc(db, None, "200") is None
    assert h.isrc_com_ids_diferentes(db) == 0  # ISRC vazio não agrupa nada


def test_registro_mp3_e_sem_dados_mostra_so_o_formato():
    mp3 = h.Registro("1", "X", "t", "a", "MP3", "", "", "/p", "2026-11-12 10:00:00")
    assert mp3.qualidade == "MP3"
    flac_sem_dados = mp3._replace(formato="FLAC")
    assert flac_sem_dados.qualidade == "FLAC"
    assert mp3.dia == "12/11"
    assert mp3._replace(baixado_em="").dia == ""


def test_repetidas_ordena_por_vezes_e_depois_pela_mais_recente(tmp_path):
    db = str(tmp_path / "qobuz_dl.db")
    for dia in (1, 5, 12):
        _grava(db, "1", titulo="Hurt", isrc="A", quando=datetime(2026, 11, dia))
    for dia in (3, 9):
        _grava(db, "2", titulo="Pyramid Song", isrc="B", quando=datetime(2026, 11, dia))
    for dia in (2, 20):
        _grava(db, "3", titulo="Teardrop", isrc="C", quando=datetime(2026, 11, dia))
    _grava(db, "4", titulo="Uma vez só", isrc="D")

    itens = h.repetidas(db)

    assert [(i.titulo, i.vezes) for i in itens] == [
        ("Hurt", 3),
        ("Teardrop", 2),  # empate em 2x: a mais recente (20/11) vem antes
        ("Pyramid Song", 2),
    ]
    assert itens[0].ultima.startswith("2026-11-12")
    assert h.total_de_repetidas(db) == 3
    assert [i.titulo for i in h.repetidas(db, limite=2)] == ["Hurt", "Teardrop"]


def test_isrc_com_ids_diferentes_conta_gravacoes_em_varias_edicoes(tmp_path):
    db = str(tmp_path / "qobuz_dl.db")
    _grava(db, "10", isrc="AAA")
    _grava(db, "11", isrc="AAA")  # mesma gravação, outra edição
    _grava(db, "12", isrc="BBB")
    _grava(db, "12", isrc="BBB")  # mesmo ID duas vezes não conta
    assert h.isrc_com_ids_diferentes(db) == 1


def test_mostrar_aviso_escreve_um_aviso_e_duas_linhas_de_detalhe(monkeypatch):
    from qobuz_dl import ui

    avisos, infos = [], []
    monkeypatch.setattr(ui, "warn", lambda m: avisos.append(str(m)))
    monkeypatch.setattr(ui, "info", lambda m: infos.append(str(m)))
    anterior = h.Registro(
        "5966783",
        "USUM71703861",
        "Hurt",
        "NIN",
        "FLAC",
        "24",
        "96",
        "/m",
        "2026-11-12 10:00:00",
    )

    h.mostrar_aviso(anterior, "19290321")

    assert len(avisos) == 1
    assert "Mesma gravação, outro ID" in avisos[0] and "USUM71703861" in avisos[0]
    assert len(infos) == 2
    assert "antes: 12/11 · FLAC 24/96 · ID 5966783" in infos[0]
    assert "agora: ID 19290321 · baixando" in infos[1]


# --- ligação com o downloader ------------------------------------------------


def _download(db):
    """`self` mínimo para os dois métodos do histórico de `Download`."""
    return SimpleNamespace(download_db=db, item_id="19290321")


async def test_downloader_registra_o_historico_da_faixa(tmp_path):
    from qobuz_dl.downloader import Download

    db = str(tmp_path / "qobuz_dl.db")
    meta = {
        "id": 19290321,
        "title": "Hurt",
        "version": "Remastered",
        "isrc": "usum71703861",
    }

    await Download._registrar_historico(
        _download(db), meta, "Nine Inch Nails", "FLAC", 24, "96", "/m/Hurt.flac"
    )

    conn = sqlite3.connect(db)
    linha = conn.execute(
        "SELECT track_id, isrc, titulo, artista, formato, bit_depth, "
        "sampling_rate, pasta FROM track_history"
    ).fetchone()
    conn.close()
    assert linha == (
        "19290321",
        "USUM71703861",
        "Hurt (Remastered)",
        "Nine Inch Nails",
        "FLAC",
        "24",
        "96",
        "/m/Hurt.flac",
    )


async def test_downloader_sem_banco_nao_faz_nada_nem_quebra():
    from qobuz_dl.downloader import Download

    meta = {"id": 1, "title": "x", "isrc": "A"}
    await Download._registrar_historico(
        _download(None), meta, "a", "FLAC", 24, "96", "/p"
    )
    await Download._avisar_isrc_repetido(_download(None), meta)


async def test_downloader_avisa_quando_a_gravacao_ja_veio_com_outro_id(
    tmp_path, monkeypatch
):
    from qobuz_dl.downloader import Download

    db = str(tmp_path / "qobuz_dl.db")
    _grava(db, "5966783", quando=datetime(2026, 11, 12, 10, 0, 0))
    avisos = []
    monkeypatch.setattr(
        h, "mostrar_aviso", lambda ant, atual: avisos.append((ant, atual))
    )

    meta = {"id": 19290321, "isrc": "USUM71703861"}
    await Download._avisar_isrc_repetido(_download(db), meta)

    assert len(avisos) == 1
    assert avisos[0][0].track_id == "5966783" and avisos[0][1] == "19290321"


async def test_downloader_nao_avisa_para_o_mesmo_id_nem_sem_historico(
    tmp_path, monkeypatch
):
    from qobuz_dl.downloader import Download

    avisos = []
    monkeypatch.setattr(h, "mostrar_aviso", lambda ant, atual: avisos.append(ant))
    db = str(tmp_path / "qobuz_dl.db")

    await Download._avisar_isrc_repetido(_download(db), {"isrc": "USUM71703861"})
    _grava(db, "19290321")  # o próprio ID: não é "outro ID"
    await Download._avisar_isrc_repetido(_download(db), {"isrc": "USUM71703861"})

    assert avisos == []


async def test_erro_no_aviso_nao_derruba_o_download(tmp_path, monkeypatch):
    from qobuz_dl.downloader import Download

    db = str(tmp_path / "qobuz_dl.db")
    _grava(db, "5966783")

    def quebrado(*_a):
        raise RuntimeError("terminal sem suporte")

    monkeypatch.setattr(h, "mostrar_aviso", quebrado)
    await Download._avisar_isrc_repetido(_download(db), {"isrc": "USUM71703861"})
