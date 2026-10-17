"""Capa da Apple: a edição pedida (Deluxe x padrão) tem que ser a do candidato."""

import pytest

from qobuz_dl.utils import (
    album_titulo_completo,
    edicao_album,
    get_apple_hq_cover,
    titulo_album_comparavel,
)

pytestmark = pytest.mark.unit


class TestEdicaoAlbum:
    @pytest.mark.parametrize(
        "titulo,esperado",
        [
            ("Rare", frozenset()),
            ("Rare (Explicit)", frozenset()),
            ("Rare (Deluxe)", frozenset({"deluxe"})),
            ("Rare (Deluxe - Explicit) (Deluxe)", frozenset({"deluxe"})),
            ("Rare (Deluxe Edition)", frozenset({"deluxe"})),
            ("Album (Super Deluxe)", frozenset({"deluxe", "super"})),
            ("Album (10th Anniversary Edition)", frozenset({"anniversary"})),
            ("Album (Expanded Edition)", frozenset({"expanded"})),
        ],
    )
    def test_edicoes(self, titulo, esperado):
        assert edicao_album(titulo) == esperado


class TestTituloAlbumComparavel:
    @pytest.mark.parametrize(
        "titulo,esperado",
        [
            ("Rare (Explicit)", "rare"),
            ("Rare", "rare"),
            ("Rare (Deluxe - Explicit) (Deluxe)", "rare (deluxe)"),
            ("Rare (Deluxe)", "rare (deluxe)"),
        ],
    )
    def test_normaliza(self, titulo, esperado):
        assert titulo_album_comparavel(titulo) == esperado


class TestAlbumTituloCompleto:
    def test_junta_versao(self):
        assert album_titulo_completo({"title": "Rare", "version": "Deluxe"}) == (
            "Rare (Deluxe)"
        )

    def test_nao_duplica_versao_que_ja_esta_no_titulo(self):
        album = {"title": "Rare (Deluxe - Explicit)", "version": "Deluxe"}
        assert album_titulo_completo(album) == "Rare (Deluxe - Explicit)"

    def test_sem_versao(self):
        assert album_titulo_completo({"title": "Rare"}) == "Rare"
        assert album_titulo_completo(None) == ""


STD = {
    "wrapperType": "collection",
    "artistName": "Selena Gomez",
    "collectionName": "Rare",
    "collectionId": 1,
    "artworkUrl100": "https://x/STD/100x100bb.jpg",
}
DLX = {
    **STD,
    "collectionName": "Rare (Deluxe)",
    "collectionId": 2,
    "artworkUrl100": "https://x/DLX/100x100bb.jpg",
}
STD_SONG = {**STD, "wrapperType": "track", "trackName": "Dance Again"}


class _Resp:
    status_code = 200

    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _Client:
    """Apple falsa: o ISRC só devolve a edição padrão (o caso do bug)."""

    async def head(self, url, **kwargs):
        return _Resp({})

    async def get(self, url, params=None, headers=None, timeout=None):
        if "lookup?upc" in url:
            return _Resp({"results": []})
        if "lookup?isrc" in url:
            return _Resp({"results": [STD_SONG]})
        if params and params.get("entity") == "album":
            return _Resp({"results": [STD, DLX]})
        return _Resp({"results": []})


@pytest.mark.parametrize(
    "album,esperada",
    [
        ("Rare (Deluxe)", "DLX"),
        ("Rare (Deluxe - Explicit) (Deluxe)", "DLX"),
        ("Rare (Explicit)", "STD"),
        ("Rare", "STD"),
    ],
)
async def test_capa_da_edicao_certa(album, esperada):
    url, _fonte, motivo = await get_apple_hq_cover(
        session=_Client(),
        upc="U",
        isrc="I",
        artist="Selena Gomez",
        album=album,
        release_kind="full",
    )
    assert url is not None, motivo
    assert f"/{esperada}/" in url
