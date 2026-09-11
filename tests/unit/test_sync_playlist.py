from types import SimpleNamespace

import pytest

from qobuz_dl import sync_playlist as sync_module
from qobuz_dl import utils

pytestmark = pytest.mark.unit


def _app(download_result):
    app = SimpleNamespace(
        client=object(),
        folder_format="original",
        settings=SimpleNamespace(multiple_disc_one_dir=False),
        no_m3u_for_playlists=True,
    )

    async def download_from_id(*args, **kwargs):
        return download_result

    app.download_from_id = download_from_id
    return app


async def _run_sync(
    tmp_path, monkeypatch, download_result, trash_error=False, remove_error=False
):
    orphan = tmp_path / "orphan.flac"
    orphan.write_bytes(b"audio")
    orphan_lrc = tmp_path / "orphan.lrc"
    orphan_lrc.write_text("lyrics", encoding="utf-8")

    async def fetch_remote(*args):
        return "Playlist", [{"id": "new", "title": "Nova", "album": {}}]

    monkeypatch.setattr(sync_module, "_fetch_remote_tracks", fetch_remote)
    monkeypatch.setattr(
        sync_module,
        "_scan_local_tracks",
        lambda directory: ({"old": str(orphan)}, []),
    )
    monkeypatch.setattr(utils, "make_m3u", lambda *args, **kwargs: None)
    trashed = []

    def trash(path):
        if trash_error:
            raise OSError("lixeira indisponível")
        trashed.append(path)

    monkeypatch.setattr(sync_module, "send2trash", trash)
    if remove_error:
        monkeypatch.setattr(
            sync_module.os,
            "remove",
            lambda path: (_ for _ in ()).throw(OSError("remoção indisponível")),
        )
    app = _app(download_result)

    result = await sync_module.sync_playlist(
        app,
        "https://play.qobuz.com/playlist/123",
        str(tmp_path),
        auto_confirm=True,
    )
    return result, app, trashed, orphan, orphan_lrc


async def test_falha_no_download_preserva_arquivos_orfaos(tmp_path, monkeypatch):
    result, app, trashed, orphan, _ = await _run_sync(tmp_path, monkeypatch, False)

    assert result is False
    assert trashed == []
    assert orphan.exists()
    assert app.folder_format == "original"
    assert app.settings.multiple_disc_one_dir is False


async def test_exclusao_ocorre_depois_de_download_bem_sucedido(tmp_path, monkeypatch):
    result, _, trashed, orphan, orphan_lrc = await _run_sync(
        tmp_path, monkeypatch, True
    )

    assert result is True
    assert trashed == [str(orphan), str(orphan_lrc)]


async def test_lixeira_indisponivel_cai_para_remocao_direta(tmp_path, monkeypatch):
    result, _, trashed, orphan, orphan_lrc = await _run_sync(
        tmp_path, monkeypatch, True, trash_error=True
    )

    assert result is True
    assert trashed == []
    assert not orphan.exists()
    assert not orphan_lrc.exists()


async def test_sync_falha_quando_lixeira_e_remocao_direta_falham(tmp_path, monkeypatch):
    result, _, _, orphan, orphan_lrc = await _run_sync(
        tmp_path,
        monkeypatch,
        True,
        trash_error=True,
        remove_error=True,
    )

    assert result is False
    assert orphan.exists()
    assert orphan_lrc.exists()
