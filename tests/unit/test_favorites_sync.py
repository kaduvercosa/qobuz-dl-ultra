"""Testes do sync de favoritos (paginação segura, diff, download)."""

import asyncio

import pytest

from qobuz_dl import favorites_sync as fs
from qobuz_dl.library_db import LibraryDB


class FakeClient:
    def __init__(self, albums, total=None, fail_on_page=None):
        self.albums, self.total, self.fail_on_page = albums, total, fail_on_page
        self.calls = 0

    async def api_call(self, endpoint, **kw):
        self.calls += 1
        if self.fail_on_page == self.calls:
            raise RuntimeError("rede caiu")
        off, lim = kw["offset"], kw["limit"]
        total = len(self.albums) if self.total is None else self.total
        return {"albums": {"items": self.albums[off:off + lim], "total": total}}


def _item(i, **kw):
    d = {"id": str(i), "title": f"T{i}", "artist": {"name": "A"}, "tracks_count": 10,
         "maximum_bit_depth": 24, "maximum_sampling_rate": 96}
    d.update(kw)
    return d


@pytest.fixture
def lib(tmp_path):
    return LibraryDB(tmp_path / "l.db")


def test_extract_versao_e_campos():
    a = fs.extract_album_data(_item(1, title="X", version="Deluxe", upc="123",
                                    label={"name": "L"}, image={"large": "u"}))
    assert a["title"] == "X (Deluxe)" and a["label"] == "L" and a["cover_url"] == "u"
    assert fs.extract_album_data({"title": "sem id"}) is None


def test_paginacao_completa(monkeypatch):
    monkeypatch.setattr(fs, "PAGE_SIZE", 2)
    c = FakeClient([_item(i) for i in range(5)])
    items, total = asyncio.run(fs.fetch_all_favorite_albums(c, page_size=2))
    assert len(items) == 5 and total == 5 and c.calls == 3


def test_erro_de_rede_propaga():
    c = FakeClient([_item(i) for i in range(4)], fail_on_page=2)
    with pytest.raises(fs.FavoritesFetchError):
        asyncio.run(fs.fetch_all_favorite_albums(c, page_size=2))


def test_resposta_sem_bloco_albums_e_erro():
    class Vazio:
        async def api_call(self, *a, **k):
            return {}

    with pytest.raises(fs.FavoritesFetchError):
        asyncio.run(fs.fetch_all_favorite_albums(Vazio()))


def test_diff_novos_e_removidos(lib):
    lib.upsert_album("qobuz", "old", "Velho", "A")
    r = asyncio.run(fs.refresh_library(lib, FakeClient([_item(1), _item(2)])))
    assert r["new"] == 2 and r["removed"] == 1 and r["complete"]
    assert lib.get_album_by_source_id("qobuz", "old")["removed_from_service"] == 1
    r2 = asyncio.run(fs.refresh_library(lib, FakeClient([_item(1), _item(2)])))
    assert r2["new"] == 0 and r2["removed"] == 0


def test_paginacao_incompleta_nao_marca_remocao(lib):
    lib.upsert_album("qobuz", "old", "Velho", "A")
    c = FakeClient([_item(1)], total=50)  # API diz 50, entrega 1
    r = asyncio.run(fs.refresh_library(lib, c))
    assert not r["complete"] and r["removed"] == 0
    assert lib.get_album_by_source_id("qobuz", "old")["removed_from_service"] == 0


def test_dry_run_nao_grava(lib):
    r = asyncio.run(fs.refresh_library(lib, FakeClient([_item(1)]), dry_run=True))
    assert r["new"] == 1 and lib.get_albums() == []


def test_run_sync_baixa_novos_e_registra_historico(tmp_path, lib):
    pasta = tmp_path / "Disco"
    pasta.mkdir()
    (pasta / "1.flac").write_bytes(b"x")
    baixados = []

    async def dl(album_id):
        baixados.append(album_id)
        return album_id == "1"

    async def _go():
        return await fs.run_sync(lib, FakeClient([_item(1), _item(2)]), download_new=True,
                                 download_fn=dl, downloads_db=None)

    res = asyncio.run(_go())
    assert sorted(baixados) == ["1", "2"]
    assert res["download"]["downloaded"] == 1 and res["download"]["failed"] == 1
    assert lib.get_album_by_source_id("qobuz", "1")["download_status"] == "complete"
    assert lib.get_album_by_source_id("qobuz", "2")["download_status"] == "failed"
    h = lib.get_sync_history("qobuz")
    assert h[0]["status"] == "complete" and h[0]["albums_new"] == 2


def test_run_sync_falha_de_rede_marca_run_failed(lib):
    with pytest.raises(fs.FavoritesFetchError):
        asyncio.run(fs.run_sync(lib, FakeClient([_item(1)], fail_on_page=1)))
    assert lib.get_sync_history("qobuz")[0]["status"] == "failed"


def test_confirmacao_negada_nao_baixa(lib):
    async def dl(_):
        raise AssertionError("não devia baixar")

    async def no(_):
        return False

    res = asyncio.run(fs.run_sync(lib, FakeClient([_item(1)]), download_new=True, download_fn=dl, confirm=no))
    assert res["targets"] == 0


def test_limit_e_missing(lib):
    for i in range(3):
        lib.upsert_album("qobuz", str(i), f"T{i}", "A")
    assert len(fs.pick_download_targets(lib, missing=True, limit=2)) == 2
    assert fs.pick_download_targets(lib) == []
