"""Testes direcionados para os últimos ramos relevantes de core.py."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def test_tui_renderer_opcoes_vazias_e_multiplas_larguras(monkeypatch):
    class Size:
        columns = 8
        rows = 10

    class Output:
        def get_size(self):
            return Size()

    class App:
        last = None

        def __init__(self, layout, key_bindings, **kwargs):
            self.output = Output()
            self.layout = layout
            self.key_bindings = key_bindings
            App.last = self

        async def run_async(self):
            return None

    monkeypatch.setattr(core, "Application", App)
    monkeypatch.setattr(core, "get_app", lambda: App.last)

    await core._tui_select("T", [], item_category="filter")
    assert App.last is not None


async def test_qobuzdl_init_com_blacklist_valida(tmp_path, monkeypatch):
    blacklist = tmp_path / "blacklist.txt"
    blacklist.write_text("# comentário\nbootleg\n\nremix\n", encoding="utf-8")

    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)
    monkeypatch.setattr(core, "create_db", lambda path: object())

    app = core.QobuzDL(
        directory=str(tmp_path),
        downloads_db="db.sqlite",
        blacklist=str(blacklist),
    )

    assert app.blacklist_patterns == ["bootleg", "remix"]
    assert app.downloads_db is not None


async def test_qobuzdl_init_blacklist_inexistente(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    app = core.QobuzDL(
        directory=str(tmp_path),
        blacklist=str(tmp_path / "missing.txt"),
    )

    assert app.blacklist_patterns == []


async def test_initialize_client_sem_close(monkeypatch):
    new_client = object()

    class Client:
        @classmethod
        async def create(cls, *args, **kwargs):
            return new_client

    monkeypatch.setattr(core.qopy, "Client", Client)
    app = SimpleNamespace(
        settings=SimpleNamespace(user_auth_token=None),
        force_english=True,
        quality=6,
    )

    await core.QobuzDL.initialize_client(
        app,
        "email",
        "password",
        "app",
        ["secret"],
    )

    assert app.client is new_client


async def test_download_list_url_arquivo(monkeypatch, tmp_path):
    calls = []
    source = tmp_path / "urls.txt"
    source.write_text("https://play.qobuz.com/track/1\n", encoding="utf-8")

    async def download_from_txt_file(path):
        calls.append(path)

    app = SimpleNamespace(
        settings=SimpleNamespace(max_workers=1),
        delay=0,
        download_from_txt_file=download_from_txt_file,
        mark_url_done_in_file=lambda *args: None,
    )

    monkeypatch.setattr(core, "get_url_info", lambda url: ("album", "1"))

    await core.QobuzDL.download_list_of_urls(app, [str(source)])

    assert calls == [str(source)]


async def test_download_from_txt_file_ignora_linhas_invalidas(tmp_path):
    source = tmp_path / "urls.txt"
    source.write_text(
        "# comentário\n\nhttps://play.qobuz.com/album/1 [DONE]\nnão é url\n",
        encoding="utf-8",
    )

    app = SimpleNamespace()
    await core.QobuzDL.download_from_txt_file(app, str(source))


async def test_lucky_mode_sem_resultados():
    async def search(*args, **kwargs):
        return []

    async def download_list(*args, **kwargs):
        return None

    app = SimpleNamespace(
        lucky_type="album",
        lucky_limit=1,
        search_by_type=search,
        download_list_of_urls=download_list,
    )

    result = await core.QobuzDL.lucky_mode(app, "query", download=True)
    assert result == []
