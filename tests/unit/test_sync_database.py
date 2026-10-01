"""Testa sync_database e find_duplicate_tracks de qobuz_dl/sync.py sem
abrir áudio real: FLAC/ID3 e handle_download_id são trocados por dublês.
Os arquivos .flac/.mp3 criados em tmp_path são vazios; só servem para o
os.walk enxergá-los.
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from qobuz_dl import sync

pytestmark = pytest.mark.unit


class _FakeFLAC(dict):
    def __init__(self, path):
        super().__init__(_FAKE_TAGS.get(path, {}))
        self.info = SimpleNamespace(bits_per_sample=24, sample_rate=96000)


class _Frame:
    def __init__(self, value):
        self.text = [value]


class _FakeID3(dict):
    def __init__(self, path):
        super().__init__(_FAKE_TAGS.get(path, {}))
        self.info = SimpleNamespace(sample_rate=44100)


_FAKE_TAGS: dict = {}


@pytest.fixture(autouse=True)
def _ambiente(monkeypatch):
    _FAKE_TAGS.clear()
    monkeypatch.setattr(sync, "FLAC", _FakeFLAC)
    monkeypatch.setattr(sync, "ID3", _FakeID3)
    monkeypatch.setattr(sync.asyncio, "sleep", AsyncMock())
    registro = AsyncMock()
    monkeypatch.setattr(sync, "handle_download_id", registro)
    return registro


def _criar(tmp_path, nome):
    caminho = tmp_path / nome
    caminho.write_bytes(b"")
    return str(caminho)


def _chamadas(mock, media_type):
    return [
        c.kwargs for c in mock.call_args_list if c.kwargs["media_type"] == media_type
    ]


async def test_diretorio_sem_audio_retorna_sem_gravar(tmp_path, _ambiente, caplog):
    (tmp_path / "nota.txt").write_text("x")
    with caplog.at_level(logging.INFO):
        await sync.sync_database(str(tmp_path), "db", client=None)
    _ambiente.assert_not_called()
    assert "Nenhum arquivo de áudio" in caplog.text


async def test_flac_com_tags_qdl_grava_faixa_e_album(tmp_path, _ambiente):
    caminho = _criar(tmp_path, "a.flac")
    _FAKE_TAGS[caminho] = {
        "QDL_TRACK_ID": ["111"],
        "QDL_ALBUM_ID": ["AAA"],
        "ARTIST": ["Fulano"],
        "ALBUM": ["Disco"],
        "DATE": ["2020-01-02"],
    }
    await sync.sync_database(str(tmp_path), "db", client=None)

    faixa = _chamadas(_ambiente, "track")[0]
    assert faixa["item_id"] == "111"
    assert faixa["file_format"] == "FLAC"
    assert faixa["bit_depth"] == "24"
    assert faixa["sampling_rate"] == "96.0"
    assert faixa["artist"] == "Fulano"
    assert faixa["saved_path"] == caminho
    album = _chamadas(_ambiente, "album")[0]
    assert album["item_id"] == "AAA"
    assert album["saved_path"] == str(tmp_path)


async def test_flac_usa_tag_legada_e_prefere_albumartist(tmp_path, _ambiente):
    caminho = _criar(tmp_path, "b.flac")
    _FAKE_TAGS[caminho] = {
        "QOBUZTRACKID": ["222"],
        "QOBUZALBUMID": ["BBB"],
        "ALBUMARTIST": ["Banda"],
        "ARTIST": ["Solista"],
    }
    await sync.sync_database(str(tmp_path), "db", client=None)
    assert _chamadas(_ambiente, "track")[0]["item_id"] == "222"
    assert _chamadas(_ambiente, "track")[0]["artist"] == "Banda"
    assert _chamadas(_ambiente, "album")[0]["item_id"] == "BBB"


async def test_album_repetido_e_gravado_uma_unica_vez(tmp_path, _ambiente):
    for nome, tid in (("1.flac", "1"), ("2.flac", "2")):
        _FAKE_TAGS[_criar(tmp_path, nome)] = {
            "QDL_TRACK_ID": [tid],
            "QDL_ALBUM_ID": ["MESMO"],
        }
    await sync.sync_database(str(tmp_path), "db", client=None)
    assert len(_chamadas(_ambiente, "track")) == 2
    assert len(_chamadas(_ambiente, "album")) == 1


async def test_mp3_le_frames_txxx_e_tpe2(tmp_path, _ambiente):
    caminho = _criar(tmp_path, "c.mp3")
    _FAKE_TAGS[caminho] = {
        "TXXX:QDL_TRACK_ID": _Frame("333"),
        "TXXX:QOBUZALBUMID": _Frame("CCC"),
        "TPE2": _Frame("Artista Album"),
        "TPE1": _Frame("Outro"),
        "TALB": _Frame("Disco MP3"),
        "TDRC": _Frame("2019"),
    }
    await sync.sync_database(str(tmp_path), "db", client=None)

    faixa = _chamadas(_ambiente, "track")[0]
    assert faixa["item_id"] == "333"
    assert faixa["file_format"] == "MP3"
    assert faixa["bit_depth"] == "16"
    assert faixa["artist"] == "Artista Album"
    assert faixa["album"] == "Disco MP3"
    assert faixa["release_date"] == "2019"
    assert _chamadas(_ambiente, "album")[0]["item_id"] == "CCC"


async def test_mp3_sem_tpe2_cai_para_tpe1(tmp_path, _ambiente):
    caminho = _criar(tmp_path, "d.mp3")
    _FAKE_TAGS[caminho] = {
        "TXXX:qdl_track_id": _Frame("444"),
        "TPE1": _Frame("So Artista"),
    }
    await sync.sync_database(str(tmp_path), "db", client=None)
    assert _chamadas(_ambiente, "track")[0]["artist"] == "So Artista"


async def test_sem_id_usa_isrc_na_api_e_completa_metadados(tmp_path, _ambiente):
    caminho = _criar(tmp_path, "e.flac")
    _FAKE_TAGS[caminho] = {"isrc": ["BRXXX0000001"]}
    client = SimpleNamespace(
        search_tracks=AsyncMock(
            return_value={
                "tracks": {
                    "items": [
                        {
                            "id": 999,
                            "album": {
                                "id": "ALB9",
                                "title": "Do Qobuz",
                                "release_date_original": "2021-05-05",
                            },
                            "performer": {"name": "Via API"},
                            "maximum_bit_depth": 24,
                            "maximum_sampling_rate": 192.0,
                        }
                    ]
                }
            }
        )
    )
    await sync.sync_database(str(tmp_path), "db", client)

    client.search_tracks.assert_awaited_once_with("BRXXX0000001", limit=1)
    faixa = _chamadas(_ambiente, "track")[0]
    assert faixa["item_id"] == "999"
    assert faixa["artist"] == "Via API"
    assert faixa["album"] == "Do Qobuz"
    assert faixa["release_date"] == "2021-05-05"
    assert faixa["bit_depth"] == "24"
    assert faixa["sampling_rate"] == "192.0"
    assert _chamadas(_ambiente, "album")[0]["item_id"] == "ALB9"


async def test_isrc_sem_resultado_nao_grava_nada(tmp_path, _ambiente):
    caminho = _criar(tmp_path, "f.flac")
    _FAKE_TAGS[caminho] = {"isrc": ["BRXXX0000002"]}
    client = SimpleNamespace(
        search_tracks=AsyncMock(return_value={"tracks": {"items": []}})
    )
    await sync.sync_database(str(tmp_path), "db", client)
    _ambiente.assert_not_called()


async def test_erro_em_um_arquivo_nao_interrompe_os_demais(
    tmp_path, _ambiente, caplog, monkeypatch
):
    ruim = _criar(tmp_path, "a_ruim.flac")
    bom = _criar(tmp_path, "b_bom.flac")
    _FAKE_TAGS[bom] = {"QDL_TRACK_ID": ["777"]}

    class _Quebra(_FakeFLAC):
        def __init__(self, path):
            if path == ruim:
                raise RuntimeError("arquivo corrompido")
            super().__init__(path)

    monkeypatch.setattr(sync, "FLAC", _Quebra)
    with caplog.at_level(logging.ERROR):
        await sync.sync_database(str(tmp_path), "db", client=None)
    assert "arquivo corrompido" in caplog.text
    assert [c["item_id"] for c in _chamadas(_ambiente, "track")] == ["777"]


async def test_keyboard_interrupt_e_tratado_com_aviso(tmp_path, _ambiente, caplog):
    caminho = _criar(tmp_path, "g.flac")
    _FAKE_TAGS[caminho] = {"QDL_TRACK_ID": ["888"]}
    _ambiente.side_effect = KeyboardInterrupt
    with caplog.at_level(logging.WARNING):
        await sync.sync_database(str(tmp_path), "db", client=None)
    assert "interrompida" in caplog.text


async def test_duplicatas_diretorio_vazio_retorna_dict_vazio(tmp_path):
    assert await sync.find_duplicate_tracks(str(tmp_path)) == {}


async def test_duplicatas_agrupa_mesma_fingerprint(tmp_path, monkeypatch, caplog):
    a, b, c = (_criar(tmp_path, n) for n in ("a.flac", "b.flac", "c.mp3"))
    mapa = {a: (1, "X"), b: (1, "X"), c: (1, "Y")}
    monkeypatch.setattr(sync, "_compute_fingerprint", lambda p: mapa[p])
    with caplog.at_level(logging.INFO):
        resultado = await sync.find_duplicate_tracks(str(tmp_path))
    assert list(resultado) == ["X"]
    assert sorted(resultado["X"]) == sorted([a, b])
    assert "1 grupo(s)" in caplog.text


async def test_duplicatas_sem_repeticao_e_arquivos_ilegiveis(
    tmp_path, monkeypatch, caplog
):
    a, b = _criar(tmp_path, "a.flac"), _criar(tmp_path, "b.flac")
    mapa = {a: (1, "X"), b: (None, None)}
    monkeypatch.setattr(sync, "_compute_fingerprint", lambda p: mapa[p])
    with caplog.at_level(logging.INFO):
        resultado = await sync.find_duplicate_tracks(str(tmp_path))
    assert resultado == {}
    assert "1 arquivo(s) ignorado(s)" in caplog.text
    assert "Nenhuma faixa duplicada" in caplog.text
