"""Álbum com vários discos: numeração por disco (padrão) ou 1..N (opcional).

Padrão: TRACKNUMBER/TRCK e TRACKTOTAL por disco, com DISCNUMBER/DISCTOTAL/TPOS.
Com `continuous_track_numbers` (flag --continuous-track-numbers): TRACKNUMBER/
TRCK de 1 a N e TRACKTOTAL = N; as tags de disco continuam gravadas.
O nome do arquivo é sempre por disco (CD 02/01 - Temporal).
"""

import pytest

import qobuz_dl.metadata as metadata
from qobuz_dl.metadata import _numeracao_continua
from qobuz_dl.settings import QobuzDLSettings

from .test_metadata_tagging import FakeFLAC, FakeID3, _album, _item

pytestmark = pytest.mark.unit


def _album_dois_discos(completo=True):
    itens = [
        {"id": 100 + n, "media_number": 1, "track_number": n} for n in range(1, 6)
    ] + [{"id": 200 + n, "media_number": 2, "track_number": n} for n in range(1, 7)]
    if not completo:
        itens = itens[:-1]
    return _album(tracks_count=11, media_count=2, tracks={"items": itens})


def test_disco_2_continua_do_disco_1():
    album = _album_dois_discos()
    assert _numeracao_continua(album, {"id": 201}) == (6, 11)
    assert _numeracao_continua(album, {"id": 105}) == (5, 11)
    assert _numeracao_continua(album, {"id": 206}) == (11, 11)


def test_fallback_por_disco_e_faixa_quando_id_nao_bate():
    item = {"id": 999, "media_number": 2, "track_number": 2}
    assert _numeracao_continua(_album_dois_discos(), item) == (7, 11)


@pytest.mark.parametrize(
    "album",
    [
        _album(tracks_count=10, media_count=1),  # disco único
        _album_dois_discos(completo=False),  # lista parcial
        _album(tracks_count=11, media_count=2),  # faixa avulsa, sem lista
    ],
)
def test_sem_numeracao_continua_nesses_casos(album):
    assert _numeracao_continua(album, {"id": 201}) is None


def test_flac_padrao_numera_por_disco(monkeypatch, tmp_path):
    fake = FakeFLAC()
    monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
    origem = tmp_path / "tmp.flac"
    origem.write_bytes(b"dummy")

    metadata.tag_flac(
        str(origem),
        str(tmp_path),
        str(tmp_path / "final.flac"),
        _item(id=201, track_number=1, media_number=2),
        _album_dois_discos(),
        istrack=False,
        settings=QobuzDLSettings(),
    )

    assert fake["TRACKNUMBER"] == "1"
    assert fake["TRACKTOTAL"] == "6"
    assert fake["DISCNUMBER"] == "2"
    assert fake["DISCTOTAL"] == "2"


def test_flac_1_a_n_mantem_tags_de_disco(monkeypatch, tmp_path):
    fake = FakeFLAC()
    monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
    origem = tmp_path / "tmp.flac"
    origem.write_bytes(b"dummy")

    metadata.tag_flac(
        str(origem),
        str(tmp_path),
        str(tmp_path / "final.flac"),
        _item(id=201, track_number=1, media_number=2),
        _album_dois_discos(),
        istrack=False,
        settings=QobuzDLSettings(continuous_track_numbers=True),
    )

    assert fake["TRACKNUMBER"] == "6"
    assert fake["TRACKTOTAL"] == "11"
    assert fake["DISCNUMBER"] == "2"
    assert fake["DISCTOTAL"] == "2"


def test_flac_disco_unico_continua_como_antes(monkeypatch, tmp_path):
    fake = FakeFLAC()
    monkeypatch.setattr(metadata, "FLAC", lambda path: fake)
    origem = tmp_path / "tmp.flac"
    origem.write_bytes(b"dummy")

    metadata.tag_flac(
        str(origem),
        str(tmp_path),
        str(tmp_path / "final.flac"),
        _item(),
        _album(),
        istrack=False,
        settings=QobuzDLSettings(),
    )

    assert fake["TRACKNUMBER"] == "3"
    assert fake["DISCNUMBER"] == "1"
    assert fake["DISCTOTAL"] == "1"


def test_mp3_padrao_trck_por_disco(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
    audios = []
    original_init = FakeID3.__init__

    def _init(self, filename=None):
        original_init(self, filename)
        audios.append(self)

    monkeypatch.setattr(FakeID3, "__init__", _init)
    origem = tmp_path / "tmp.mp3"
    origem.write_bytes(b"dummy")

    metadata.tag_mp3(
        str(origem),
        str(tmp_path),
        str(tmp_path / "final.mp3"),
        _item(id=201, track_number=1, media_number=2),
        _album_dois_discos(),
        istrack=False,
        settings=QobuzDLSettings(),
    )

    assert audios[0]["TRCK"].text == ["1/6"]
    assert audios[0]["TPOS"].text == ["2/2"]


def test_mp3_trck_1_a_n_e_tpos_mantido(monkeypatch, tmp_path):
    monkeypatch.setattr(metadata.id3, "ID3", FakeID3)
    audios = []
    original_init = FakeID3.__init__

    def _init(self, filename=None):
        original_init(self, filename)
        audios.append(self)

    monkeypatch.setattr(FakeID3, "__init__", _init)
    origem = tmp_path / "tmp.mp3"
    origem.write_bytes(b"dummy")

    metadata.tag_mp3(
        str(origem),
        str(tmp_path),
        str(tmp_path / "final.mp3"),
        _item(id=201, track_number=1, media_number=2),
        _album_dois_discos(),
        istrack=False,
        settings=QobuzDLSettings(continuous_track_numbers=True),
    )

    assert audios[0]["TRCK"].text == ["6/11"]
    assert audios[0]["TPOS"].text == ["2/2"]


# --------------------------------------------------------------------
# Faixa avulsa / playlist: busca a lista de faixas do álbum
# --------------------------------------------------------------------
class _ClienteFalso:
    def __init__(self, album=None, erro=False):
        self.album = album
        self.erro = erro
        self.chamadas = 0

    async def get_album_meta(self, album_id):
        self.chamadas += 1
        if self.erro:
            raise RuntimeError("falha de rede")
        return self.album


def _faixa_avulsa(album_completo):
    album = {k: v for k, v in album_completo.items() if k != "tracks"}
    return {"id": 201, "track_number": 1, "media_number": 2, "album": album}


def _rodar(cliente, meta, continua=True):
    import asyncio
    from types import SimpleNamespace

    from qobuz_dl.downloader import Download

    eu = SimpleNamespace(
        client=cliente,
        settings=SimpleNamespace(continuous_track_numbers=continua),
    )
    return asyncio.run(Download._com_lista_completa_de_faixas(eu, meta)), eu


def test_faixa_avulsa_recebe_lista_do_album_e_numera_1_a_n():
    completo = _album_dois_discos()
    cliente = _ClienteFalso(album=completo)
    meta, _ = _rodar(cliente, _faixa_avulsa(completo))

    assert cliente.chamadas == 1
    assert _numeracao_continua(meta["album"], meta) == (6, 11)


def test_falha_na_busca_mantem_numeracao_por_disco():
    completo = _album_dois_discos()
    original = _faixa_avulsa(completo)
    meta, _ = _rodar(_ClienteFalso(erro=True), original)

    assert meta is original
    assert _numeracao_continua(meta["album"], meta) is None


def test_disco_unico_nao_busca_o_album():
    album = _album(tracks_count=10, media_count=1)
    cliente = _ClienteFalso(album=album)
    original = {"id": 5, "track_number": 3, "media_number": 1, "album": album}
    meta, _ = _rodar(cliente, original)

    assert meta is original
    assert cliente.chamadas == 0


def test_padrao_nao_busca_o_album():
    completo = _album_dois_discos()
    cliente = _ClienteFalso(album=completo)
    original = _faixa_avulsa(completo)
    meta, _ = _rodar(cliente, original, continua=False)

    assert meta is original
    assert cliente.chamadas == 0


def test_flag_e_config_ligam_a_numeracao_continua():
    import argparse
    import configparser

    config = configparser.ConfigParser()
    config["qobuz"] = {}
    desligado = QobuzDLSettings.from_arguments_configparser(
        argparse.Namespace(), config
    )
    assert desligado.continuous_track_numbers is False

    por_flag = QobuzDLSettings.from_arguments_configparser(
        argparse.Namespace(continuous_track_numbers=True), config
    )
    assert por_flag.continuous_track_numbers is True

    config["qobuz"]["continuous_track_numbers"] = "true"
    por_config = QobuzDLSettings.from_arguments_configparser(
        argparse.Namespace(), config
    )
    assert por_config.continuous_track_numbers is True


@pytest.mark.parametrize(
    "comando", [["dl", "https://play.qobuz.com/album/1"], ["tags", "/musicas"]]
)
def test_flag_continuous_track_numbers_nos_comandos(comando):
    from qobuz_dl.commands import qobuz_dl_args

    parser = qobuz_dl_args()
    sem_flag = parser.parse_args(comando)
    assert not hasattr(sem_flag, "continuous_track_numbers")  # cai no config.ini

    com_flag = parser.parse_args(
        [comando[0], "--continuous-track-numbers", *comando[1:]]
    )
    assert com_flag.continuous_track_numbers is True
