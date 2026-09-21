"""Testes da camada de terminal (scan / library / sync-favorites)."""

import asyncio
from types import SimpleNamespace

import pytest

from qobuz_dl import library_cmd as lc
from qobuz_dl import library_scan as ls
from qobuz_dl import sentinel as sn
from qobuz_dl.library_db import LibraryDB


@pytest.fixture(autouse=True)
def sem_mutagen(monkeypatch):
    monkeypatch.setattr(ls, "_read_tags", lambda p: {})


@pytest.fixture
def lib(tmp_path):
    return LibraryDB(tmp_path / "l.db")


def _args(**kw):
    base = dict(
        DIR=None,
        dry_run=False,
        no_sentinel=False,
        no_adopt=False,
        no_review=True,
        rescan=False,
        max_depth=4,
        fuzzy_threshold=0.85,
        json=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _pasta(root, rel, n=1):
    p = root / rel
    p.mkdir(parents=True)
    for i in range(n):
        (p / f"{i}.flac").write_bytes(b"x")
    return p


def test_scan_escreve_json_e_retorna_zero(tmp_path, lib):
    lib.upsert_album("qobuz", "1", "B", "A")
    _pasta(tmp_path / "m", "A - B")
    _pasta(tmp_path / "m", "Sem - Match")
    out = tmp_path / "r.json"
    rc = asyncio.run(
        lc.cmd_scan(
            _args(DIR=str(tmp_path / "m"), json=str(out)),
            directory="x",
            downloads_db=None,
            lib=lib,
        )
    )
    assert rc == 0 and out.exists()
    assert lib.get_album_by_source_id("qobuz", "1")["download_status"] == "complete"


def test_scan_pasta_inexistente(tmp_path, lib):
    rc = asyncio.run(
        lc.cmd_scan(_args(DIR=str(tmp_path / "nada")), directory="x", lib=lib)
    )
    assert rc == 1


def test_scan_dedup_writer_chamado(tmp_path, lib, monkeypatch):
    chamadas = []

    async def fake_writer(album, folder, meta):
        chamadas.append(album["source_album_id"])

    monkeypatch.setattr(lc, "_make_dedup_writer", lambda db, q: fake_writer)
    lib.upsert_album("qobuz", "1", "B", "A")
    _pasta(tmp_path / "m", "A - B")
    asyncio.run(
        lc.cmd_scan(
            _args(DIR=str(tmp_path / "m")), directory="x", downloads_db="db", lib=lib
        )
    )
    assert chamadas == ["1"]


def test_library_acoes(tmp_path, lib, capsys):
    a = lib.upsert_album("qobuz", "1", "B", "A")
    lib.update_status(a, "downloading")
    run = lambda **kw: asyncio.run(
        lc.cmd_library(
            SimpleNamespace(TARGET=None, limit=None, fix=False, dry_run=False, **kw),
            directory=str(tmp_path),
            lib=lib,
        )
    )
    assert run(action="status") == 0
    assert run(action="missing") == 0
    assert run(action="history") == 0
    assert run(action="reset-stuck") == 0
    assert lib.get_album(a)["download_status"] == "not_downloaded"
    assert "A - B" in capsys.readouterr().out


def test_library_unmark_e_reconcile(tmp_path, lib):
    a = lib.upsert_album("qobuz", "1", "B", "A")
    pasta = _pasta(tmp_path, "A - B")
    ls.mark_album_downloaded(lib, a, folder=pasta)
    ns = lambda **kw: SimpleNamespace(limit=None, fix=False, dry_run=False, **kw)
    assert (
        asyncio.run(
            lc.cmd_library(ns(action="unmark", TARGET="1"), directory="x", lib=lib)
        )
        == 0
    )
    assert not sn.has_sentinel(pasta)
    assert (
        asyncio.run(
            lc.cmd_library(ns(action="unmark", TARGET="999"), directory="x", lib=lib)
        )
        == 1
    )
    assert (
        asyncio.run(
            lc.cmd_library(
                ns(action="reconcile", TARGET=str(tmp_path)), directory="x", lib=lib
            )
        )
        == 0
    )


class FakeQobuz:
    def __init__(self, items, ok=True):
        self.downloads_db = None
        self.settings = SimpleNamespace(write_sentinel=True)
        self.baixados = []
        self.ok = ok
        outer = self

        class C:
            async def api_call(self, endpoint, **kw):
                return {
                    "albums": {
                        "items": items[kw["offset"] : kw["offset"] + kw["limit"]],
                        "total": len(items),
                    }
                }

        self.client = C()

    async def download_from_id(self, album_id, album=True):
        self.baixados.append(album_id)
        return self.ok


def _sf(**kw):
    base = dict(
        download_new=False,
        download_missing=False,
        dry_run=False,
        yes=True,
        every=None,
        limit=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_sync_favorites_diff_e_download(lib):
    q = FakeQobuz([{"id": "1", "title": "T", "artist": {"name": "A"}}])
    assert asyncio.run(lc.cmd_sync_favorites(_sf(download_new=True), q, lib=lib)) == 0
    assert q.baixados == ["1"]


def test_sync_favorites_falha_de_download_retorna_1(lib):
    q = FakeQobuz([{"id": "1", "title": "T", "artist": {"name": "A"}}], ok=False)
    assert asyncio.run(lc.cmd_sync_favorites(_sf(download_new=True), q, lib=lib)) == 1


def test_sync_favorites_dry_run(lib):
    q = FakeQobuz([{"id": "1", "title": "T", "artist": {"name": "A"}}])
    assert (
        asyncio.run(
            lc.cmd_sync_favorites(_sf(dry_run=True, download_new=True), q, lib=lib)
        )
        == 0
    )
    assert q.baixados == [] and lib.get_albums() == []


def test_sync_favorites_every_repete(lib):
    q = FakeQobuz([{"id": "1", "title": "T", "artist": {"name": "A"}}])
    esperas = []

    async def sleep(s):
        esperas.append(s)
        if len(esperas) == 2:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        asyncio.run(lc.cmd_sync_favorites(_sf(every=5), q, lib=lib, sleep=sleep))
    assert esperas == [300, 300] and len(lib.get_sync_history("qobuz")) == 2


def test_sync_favorites_interactive_prompt_opcao_1(lib, monkeypatch):
    """Option 1 downloads only favorites added by the current sync."""
    q = FakeQobuz([{"id": "1", "title": "T", "artist": {"name": "A"}}])
    monkeypatch.setattr(lc, "_interactive", lambda: True)

    responses = iter(["1", "s"])

    async def fake_ask(prompt):
        """Return the next scripted response to an interactive prompt."""
        return next(responses)

    monkeypatch.setattr(lc, "_ask", fake_ask)
    assert asyncio.run(lc.cmd_sync_favorites(_sf(), q, lib=lib)) == 0
    assert q.baixados == ["1"]
    assert lib.get_sync_history("qobuz")[0]["albums_downloaded"] == 1


def test_sync_favorites_interactive_prompt_opcao_2(lib, monkeypatch):
    """Option 2 downloads every favorite missing from local storage."""
    lib.upsert_album("qobuz", "2", "Title2", "Artist2")
    q = FakeQobuz(
        [
            {"id": "1", "title": "T", "artist": {"name": "A"}},
            {"id": "2", "title": "Title2", "artist": {"name": "Artist2"}},
        ]
    )
    monkeypatch.setattr(lc, "_interactive", lambda: True)

    responses = iter(["2", "s"])

    async def fake_ask(prompt):
        """Return the next scripted response to an interactive prompt."""
        return next(responses)

    monkeypatch.setattr(lc, "_ask", fake_ask)
    assert asyncio.run(lc.cmd_sync_favorites(_sf(), q, lib=lib)) == 0
    assert "2" in q.baixados and "1" in q.baixados
