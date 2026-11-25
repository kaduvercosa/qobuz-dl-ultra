"""Faixa já baixada é reconhecida pelo ID do Qobuz (qobuz_dl/faixa_existente.py).

Regra: não baixa de novo se existe cópia (conferida por ID) em qualidade IGUAL OU
MAIOR que a pedida; cópia de qualidade MENOR deixa baixar (upgrade).

Os arquivos são falsos: o nome é '<id>.flac' e as leituras de tag/áudio são
trocadas por funções simples, então o teste não precisa de mutagen.
"""

import inspect
import os
import sqlite3

import pytest

from qobuz_dl import faixa_existente as fe
from qobuz_dl import playlist_links as pl

pytestmark = pytest.mark.unit

SCHEMA = """CREATE TABLE downloads (
  "id" text NOT NULL, "media_type" text NOT NULL DEFAULT 'album',
  "quality" integer NOT NULL DEFAULT 27, "file_format" text NOT NULL DEFAULT 'FLAC',
  "quality_met" integer NOT NULL DEFAULT 0, "bit_depth" text, "sampling_rate" text,
  "saved_path" text NOT NULL DEFAULT '', "status" text NOT NULL DEFAULT 'downloaded',
  "url" text NOT NULL DEFAULT '', "release_date" text NOT NULL DEFAULT '',
  "artist" text NOT NULL DEFAULT '', "album" text NOT NULL DEFAULT '',
  PRIMARY KEY ("id", "quality"));"""


@pytest.fixture(autouse=True)
def _leitores_falsos(monkeypatch):
    """ID = nome do arquivo ('<id>.flac'; '<id>_semtag.flac' não tem tag);
    áudio = 24-bit/96 kHz, ou CD se o nome tiver '_cd'."""

    def ler_id(caminho):
        nome = os.path.basename(caminho).split(".")[0]
        return None if nome.endswith("_semtag") else nome.split("_")[0]

    def ler_info(caminho):
        if "_ilegivel" in caminho:
            return None
        if "_cd" in caminho:
            return ("flac", 16, 44.1)
        if caminho.endswith(".mp3"):
            return ("mp3", 0, 44.1)
        return ("flac", 24, 96.0)

    monkeypatch.setattr(fe, "_ler_id", ler_id)
    monkeypatch.setattr(fe, "_ler_info", ler_info)
    monkeypatch.setattr(pl, "read_track_id", ler_id)
    fe.esquecer_indice()
    yield
    fe.esquecer_indice()


def _arquivo(pasta, nome):
    caminho = pasta / nome
    caminho.parent.mkdir(parents=True, exist_ok=True)
    caminho.write_bytes(b"audio")
    return str(caminho)


def _banco(tmp_path, linhas):
    """linhas: (id, qualidade, caminho[, bit_depth, taxa[, status[, formato]]])"""
    db = str(tmp_path / "downloads.db")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    for tid, qualidade, caminho, *resto in linhas:
        bits, taxa, status, formato = (list(resto) + [None] * 4)[:4]
        conn.execute(
            "INSERT INTO downloads (id, media_type, quality, saved_path, bit_depth, "
            "sampling_rate, status, file_format) VALUES (?, 'track', ?, ?, ?, ?, ?, ?)",
            (
                tid,
                qualidade,
                caminho,
                bits,
                taxa,
                status or "downloaded",
                formato or "FLAC",
            ),
        )
    conn.commit()
    conn.close()
    return db


def _caminho_no_banco(db, tid, qualidade):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(
            "SELECT saved_path FROM downloads WHERE id=? AND quality=?",
            (tid, qualidade),
        ).fetchone()[0]
    finally:
        conn.close()


async def test_mesma_qualidade_com_tag_certa_nao_baixa_de_novo(tmp_path):
    arq = _arquivo(tmp_path, "Album/100.flac")
    db = _banco(tmp_path, [("100", 27, arq, "24", "96")])

    achou = await fe.localizar_faixa_ja_baixada(db, 100, 27, tmp_path)

    assert achou == fe.FaixaExistente(arq, 27, "tag")


async def test_qualidade_maxima_serve_para_qualquer_pedido(tmp_path):
    arq = _arquivo(tmp_path, "Album/100.flac")
    db = _banco(tmp_path, [("100", 27, arq, "24", "96")])

    for pedida in (27, 7, 6, 5):
        achou = await fe.localizar_faixa_ja_baixada(db, "100", pedida, tmp_path)
        assert achou is not None and achou.caminho == arq


async def test_copia_de_qualidade_menor_deixa_baixar_o_upgrade(tmp_path):
    arq = _arquivo(tmp_path, "Album/100_cd.flac")
    db = _banco(tmp_path, [("100", 6, arq.replace("_cd", ""), "16", "44.1")])
    os.rename(arq, arq.replace("_cd", ""))  # o arquivo real é CD

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None
    # pedindo CD, a mesma cópia basta
    assert await fe.localizar_faixa_ja_baixada(db, "100", 6, tmp_path) is not None


async def test_tag_de_outra_faixa_recusa(tmp_path):
    arq = _arquivo(tmp_path, "Album/999.flac")  # a tag diz 999
    db = _banco(tmp_path, [("100", 27, arq, "24", "96")])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None


async def test_arquivo_sem_tag_de_id_vale_pelo_banco(tmp_path):
    arq = _arquivo(tmp_path, "Album/100_semtag.flac")
    db = _banco(tmp_path, [("100", 27, arq, "24", "96")])

    achou = await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path)

    assert achou is not None and achou.certeza == "banco"


async def test_copia_que_caiu_para_cd_no_download_nao_conta_como_maxima(tmp_path):
    # o Qobuz entregou 24/96 (registrado), mas o arquivo em disco é 16/44.1
    arq = _arquivo(tmp_path, "Album/100_cd.flac")
    db = _banco(tmp_path, [("100", 27, arq, "24", "96")])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None


async def test_faixa_que_so_existe_em_cd_no_qobuz_nao_e_rebaixada(tmp_path):
    # pedido 27, mas o Qobuz só tem CD: o banco guarda 16/44.1 e o arquivo confere
    arq = _arquivo(tmp_path, "Album/100_cd.flac")
    db = _banco(tmp_path, [("100", 27, arq, "16", "44.1")])

    achou = await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path)

    assert achou is not None and achou.qualidade == 27


async def test_mp3_registrado_como_flac_nao_e_aceito(tmp_path):
    arq = _arquivo(tmp_path, "Album/100.mp3")
    db = _banco(tmp_path, [("100", 27, arq, None, None, None, "FLAC")])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None


@pytest.mark.parametrize("nome", ["100_ilegivel.flac"])
async def test_arquivo_ilegivel_nao_conta(tmp_path, nome):
    arq = _arquivo(tmp_path, f"Album/{nome}")
    db = _banco(tmp_path, [("100", 27, arq)])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None


async def test_registro_que_nao_esta_baixado_ou_arquivo_vazio_nao_conta(tmp_path):
    arq = _arquivo(tmp_path, "Album/100.flac")
    vazio = tmp_path / "Album" / "200.flac"
    vazio.write_bytes(b"")
    db = _banco(
        tmp_path,
        [("100", 27, arq, None, None, "failed"), ("200", 27, str(vazio))],
    )

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None
    assert await fe.localizar_faixa_ja_baixada(db, "200", 27, tmp_path) is None


async def test_arquivo_movido_e_achado_pela_tag_e_o_banco_e_corrigido(tmp_path):
    novo = _arquivo(tmp_path, "Outra pasta/100.flac")
    db = _banco(tmp_path, [("100", 27, str(tmp_path / "Album/100.flac"), "24", "96")])

    achou = await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path)

    assert achou == fe.FaixaExistente(novo, 27, "biblioteca")
    assert _caminho_no_banco(db, "100", 27) == novo


async def test_arquivo_movido_sem_pasta_de_biblioteca_nao_varre_nada(tmp_path):
    _arquivo(tmp_path, "Outra pasta/100.flac")
    db = _banco(tmp_path, [("100", 27, str(tmp_path / "Album/100.flac"))])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, None) is None


async def test_indice_e_atualizado_uma_vez_so_por_execucao(tmp_path, monkeypatch):
    _arquivo(tmp_path, "Outra pasta/100.flac")
    _arquivo(tmp_path, "Outra pasta/101.flac")
    db = _banco(
        tmp_path,
        [
            ("100", 27, str(tmp_path / "x/100.flac")),
            ("101", 27, str(tmp_path / "x/101.flac")),
        ],
    )
    chamadas = []
    original = fe._atualizar_indice
    monkeypatch.setattr(
        fe, "_atualizar_indice", lambda r, i: chamadas.append(r) or original(r, i)
    )

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path)
    assert await fe.localizar_faixa_ja_baixada(db, "101", 27, tmp_path)
    assert len(chamadas) == 1


async def test_faixa_so_no_disco_e_reconhecida_e_registrada_sozinha(tmp_path):
    """Sem registro no banco (banco perdido, arquivo de fora): o índice acha pela
    tag e o banco é preenchido, sem `--sync-db`."""
    arq = _arquivo(tmp_path, "Album/100.flac")  # áudio falso: 24-bit/96 kHz
    db = _banco(tmp_path, [])

    achou = await fe.localizar_faixa_ja_baixada(db, "100", 7, tmp_path)

    assert achou == fe.FaixaExistente(arq, 7, "biblioteca")  # 24/96 = nível 7
    conn = sqlite3.connect(db)
    linha = conn.execute(
        "SELECT quality, saved_path, bit_depth, sampling_rate, status, file_format "
        "FROM downloads WHERE id='100' AND media_type='track'"
    ).fetchone()
    conn.close()
    assert linha == (7, os.path.abspath(arq), "24", "96", "downloaded", "FLAC")
    # na vez seguinte o banco resolve sozinho (certeza pela tag, sem índice)
    fe.esquecer_indice()
    de_novo = await fe.localizar_faixa_ja_baixada(db, "100", 6, tmp_path)
    assert de_novo is not None and de_novo.certeza == "tag"


def _maximo(valor, chamadas=None):
    async def consultar(track_id):
        if chamadas is not None:
            chamadas.append(track_id)
        if isinstance(valor, Exception):
            raise valor
        return valor

    return consultar


async def test_faixa_no_disco_que_ja_e_o_maximo_do_qobuz_nao_e_baixada(tmp_path):
    """Pedido 24-bit/192 kHz, mas o Qobuz só tem a faixa em 24/96: o arquivo que
    está no disco já é o máximo. Registra como qualidade pedida (como um download)."""
    arq = _arquivo(tmp_path, "Album/100.flac")  # 24/96
    db = _banco(tmp_path, [])
    chamadas = []

    achou = await fe.localizar_faixa_ja_baixada(
        db, "100", 27, tmp_path, consultar_maximo=_maximo((24, 96.0), chamadas)
    )

    assert achou == fe.FaixaExistente(arq, 27, "biblioteca")
    assert chamadas == ["100"]
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT quality FROM downloads WHERE id='100'").fetchall() == [
        (27,)
    ]
    conn.close()
    # próxima execução: o banco já resolve, sem perguntar nada ao Qobuz
    fe.esquecer_indice()
    de_novo = await fe.localizar_faixa_ja_baixada(
        db, "100", 27, tmp_path, consultar_maximo=_maximo(RuntimeError("sem rede"))
    )
    assert de_novo is not None and de_novo.certeza == "tag"


async def test_faixa_so_em_cd_no_qobuz_com_pedido_maximo_nao_e_baixada(tmp_path):
    arq = _arquivo(tmp_path, "Album/100_cd.flac")
    db = _banco(tmp_path, [])

    achou = await fe.localizar_faixa_ja_baixada(
        db, "100", 27, tmp_path, consultar_maximo=_maximo((16, 44.1))
    )

    assert achou == fe.FaixaExistente(arq, 27, "biblioteca")


async def test_arquivo_abaixo_do_maximo_do_qobuz_deixa_baixar_o_upgrade(tmp_path):
    _arquivo(tmp_path, "Album/100.flac")  # 24/96, mas o Qobuz tem 24/192
    db = _banco(tmp_path, [])

    achou = await fe.localizar_faixa_ja_baixada(
        db, "100", 27, tmp_path, consultar_maximo=_maximo((24, 192.0))
    )

    assert achou is None
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM downloads").fetchone()[0] == 0
    conn.close()


@pytest.mark.parametrize(
    "consultar",
    [_maximo(RuntimeError("sem rede")), _maximo(None), _maximo((0, 0.0))],
)
async def test_sem_resposta_do_qobuz_nao_assume_que_e_o_maximo(tmp_path, consultar):
    _arquivo(tmp_path, "Album/100_cd.flac")
    db = _banco(tmp_path, [])

    assert (
        await fe.localizar_faixa_ja_baixada(
            db, "100", 27, tmp_path, consultar_maximo=consultar
        )
        is None
    )


async def test_nao_pergunta_ao_qobuz_quando_o_arquivo_ja_basta(tmp_path):
    _arquivo(tmp_path, "Album/100.flac")  # 24/96 = nível 7; pedido 7
    db = _banco(tmp_path, [])
    chamadas = []

    achou = await fe.localizar_faixa_ja_baixada(
        db, "100", 7, tmp_path, consultar_maximo=_maximo((24, 192.0), chamadas)
    )

    assert achou is not None and chamadas == []


async def test_faixa_so_no_disco_em_qualidade_menor_que_a_pedida_nao_serve(tmp_path):
    _arquivo(tmp_path, "Album/100_cd.flac")
    db = _banco(tmp_path, [])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT count(*) FROM downloads").fetchone()[0] == 0
    conn.close()


async def test_faixa_so_no_disco_com_tag_de_outra_faixa_nao_e_confundida(tmp_path):
    _arquivo(tmp_path, "Album/999.flac")
    db = _banco(tmp_path, [])

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None


async def test_avisa_so_na_primeira_indexacao(tmp_path, monkeypatch):
    avisos = []
    monkeypatch.setattr(fe, "_avisar", avisos.append)
    _arquivo(tmp_path, "Album/100.flac")
    db = _banco(tmp_path, [])

    await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path)
    assert len(avisos) == 2 and "Indexando" in avisos[0] and "1 arquivos" in avisos[1]

    fe.esquecer_indice()  # nova execução: o índice já existe, não avisa
    await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path)
    assert len(avisos) == 2


async def test_arquivos_da_faixa_devolve_o_melhor_primeiro(tmp_path):
    _arquivo(tmp_path, "A/100_cd.flac")
    _arquivo(tmp_path, "B/100.flac")
    db = _banco(tmp_path, [])

    achados = await fe.arquivos_da_faixa(db, "100", tmp_path)

    assert [os.path.basename(a.caminho) for a in achados] == ["100.flac", "100_cd.flac"]
    assert await fe.arquivos_da_faixa(None, "100", tmp_path) == []
    assert await fe.arquivos_da_faixa(db, "100", tmp_path / "nao_existe") == []


async def test_sem_banco_nao_cria_arquivo_e_devolve_none(tmp_path):
    db = str(tmp_path / "nao_existe.db")

    assert await fe.localizar_faixa_ja_baixada(db, "100", 27, tmp_path) is None
    assert not os.path.exists(db)
    assert not os.path.exists(tmp_path / "track_index.db")  # nada é criado
    assert await fe.localizar_faixa_ja_baixada(None, "100", 27, tmp_path) is None
    assert await fe.localizar_faixa_ja_baixada(db, None, 27, tmp_path) is None


async def test_qualidade_pedida_invalida_ou_banco_quebrado_nao_levanta(tmp_path):
    arq = _arquivo(tmp_path, "Album/100.flac")
    db = _banco(tmp_path, [("100", 27, arq)])
    lixo = tmp_path / "lixo.db"
    lixo.write_text("isto não é um banco sqlite", encoding="utf-8")

    assert await fe.localizar_faixa_ja_baixada(db, "100", "abc", tmp_path) is None
    assert await fe.localizar_faixa_ja_baixada(str(lixo), "100", 27, tmp_path) is None
    assert (
        await fe.localizar_faixa_ja_baixada(str(tmp_path), "100", 27, tmp_path) is None
    )


def test_download_from_id_consulta_a_checagem_por_id_antes_do_banco_antigo():
    """Trava de regressão: a checagem nova tem que vir ANTES da consulta antiga
    (que apaga o registro cujo arquivo sumiu) e valer só para faixa."""
    from qobuz_dl import core

    fonte = inspect.getsource(core.QobuzDL.download_from_id)
    assert "localizar_faixa_ja_baixada(" in fonte
    assert fonte.index("localizar_faixa_ja_baixada(") < fonte.index(
        "handle_download_id("
    )
    assert "if not album:" in fonte
    # a faixa do disco que já é o máximo do Qobuz também é reconhecida
    assert "consultar_maximo=self._maximo_da_faixa" in fonte


class _ClienteFalso:
    def __init__(self, meta):
        self.meta = meta

    async def get_track_meta(self, track_id):
        if isinstance(self.meta, Exception):
            raise self.meta
        return self.meta


@pytest.mark.parametrize(
    ("meta", "esperado"),
    [
        ({"maximum_bit_depth": 24, "maximum_sampling_rate": 96.0}, (24, 96.0)),
        ({"maximum_bit_depth": 16, "maximum_sampling_rate": 44.1}, (16, 44.1)),
        ({"maximum_bit_depth": 24}, None),
        ({}, None),
        (None, None),
    ],
)
async def test_maximo_da_faixa_le_os_metadados_do_qobuz(meta, esperado):
    from types import SimpleNamespace

    from qobuz_dl import core

    eu = SimpleNamespace(client=_ClienteFalso(meta))

    assert await core.QobuzDL._maximo_da_faixa(eu, "1") == esperado


def test_registro_das_faixas_do_album_nao_depende_de_playlist_dedupe(tmp_path):
    arq = _arquivo(tmp_path, "Album/100.flac")
    db = str(tmp_path / "downloads.db")
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

    st = pl.absorb_playlist_copies_sync(
        str(tmp_path),
        db,
        {"100": arq},
        reg={"quality": 27, "bit_depth": "24", "sampling_rate": "96"},
        reader=fe._ler_id,
        dedupe=False,
    )

    assert st == {"absorbed": [], "kept": [], "registered": 1}
    assert _caminho_no_banco(db, "100", 27) == os.path.abspath(arq)


def test_downloader_registra_faixas_mesmo_com_dedupe_desligado():
    from qobuz_dl import downloader

    fonte = inspect.getsource(downloader.Download._absorb_playlist_copies)
    assert "dedupe=dedupe" in fonte
    assert 'getattr(cfg, "playlist_dedupe", True)' in fonte
