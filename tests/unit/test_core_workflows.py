from types import SimpleNamespace

import httpx
import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


def _build_app(**overrides):
    """`self` fake para `QobuzDL.download_from_id`.

    Só os atributos que o método realmente lê (ou repassa direto pro
    `downloader.Download`, que é sempre trocado por um fake nos testes
    abaixo -- os valores concretos não importam, só precisam existir).
    """
    app = SimpleNamespace(
        downloads_db=None,
        quality=6,
        client=None,
        directory="/tmp/qdl-teste",
        embed_art=False,
        ignore_singles_eps=False,
        quality_fallback=False,
        cover_og_quality=False,
        no_cover=False,
        folder_format="original",
        track_format="original",
        fetch_lyrics=False,
        no_lrc_files=False,
        genius_token=None,
        no_credits=False,
        booklet_only=False,
        playlist_as_albums=False,
        delay=0,
        settings=SimpleNamespace(pl_skipped=0, pl_failed=0),
    )
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


async def test_playlist_precarregada_baixa_sem_variavel_indefinida(
    tmp_path, monkeypatch
):
    app = SimpleNamespace(
        directory=str(tmp_path),
        folder_format="original",
        settings=SimpleNamespace(
            multiple_disc_one_dir=False,
            max_workers=1,
            pl_success=0,
            pl_skipped=0,
            pl_failed=0,
        ),
        playlist_as_albums=False,
        delay=0,
    )
    downloaded = []

    async def download_from_id(track_id, **kwargs):
        downloaded.append((track_id, kwargs["playlist_index"]))
        app.settings.pl_success += 1
        return True

    async def finalize_report(*args, **kwargs):
        return None

    app.download_from_id = download_from_id
    monkeypatch.setattr(core.downloader, "print_download_header", lambda *a, **k: None)
    monkeypatch.setattr(core.downloader, "safe_print", lambda *a, **k: None)
    monkeypatch.setattr(core.postprocess, "finalize_report", finalize_report)

    result = await core.QobuzDL.download_from_playlist_file(
        app, name="Favoritas", _preloaded_track_ids=["10", "20"]
    )

    assert result is True
    assert downloaded == [("10", 1), ("20", 2)]
    assert app.folder_format == "original"
    assert app.settings.multiple_disc_one_dir is False


async def test_lista_so_marca_url_concluida(tmp_path, monkeypatch):
    source = tmp_path / "links.txt"
    success_url = "https://play.qobuz.com/track/10"
    failed_url = "https://play.qobuz.com/track/20"
    source.write_text(f"{success_url}\n{failed_url}\n", encoding="utf-8")

    app = SimpleNamespace(
        settings=SimpleNamespace(max_workers=1),
        delay=0,
    )

    async def handle_url(url):
        return url == success_url

    app.handle_url = handle_url
    app.download_from_txt_file = None
    app.mark_url_done_in_file = core.QobuzDL.mark_url_done_in_file.__get__(app)

    await core.QobuzDL.download_list_of_urls(
        app, [success_url, failed_url], txt_file=str(source)
    )

    assert source.read_text(encoding="utf-8").splitlines() == [
        f"{success_url} [DONE]",
        failed_url,
    ]


async def test_download_from_id_ja_baixado_pula(monkeypatch):
    """Item já baixado (segundo o banco local): não deve nem instanciar
    downloader.Download -- só marca pl_skipped (quando é playlist) e
    retorna True sem tocar em rede/disco."""
    app = _build_app()

    async def handle_download_id(db, item_id, add_id, quality):
        return True

    def Download(*args, **kwargs):
        raise AssertionError(
            "não deveria instanciar downloader.Download para item já baixado"
        )

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", Download)

    result = await core.QobuzDL.download_from_id(app, "123", is_playlist=True)

    assert result is True
    assert app.settings.pl_skipped == 1
    assert app.settings.pl_failed == 0


async def test_download_from_id_sucesso_repassa_album_vs_track(monkeypatch):
    """Caminho feliz: instancia downloader.Download, repassa `not album`
    pra download_id_by_type() (álbum vs. faixa) e devolve o resultado."""
    app = _build_app()
    chamada = {}

    async def handle_download_id(db, item_id, add_id, quality):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, is_track, **kwargs):
            chamada["is_track"] = is_track
            return True

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)

    result = await core.QobuzDL.download_from_id(app, "456", album=True)

    assert result is True
    assert chamada["is_track"] is False  # album=True -> not album == False
    assert app.settings.pl_skipped == 0
    assert app.settings.pl_failed == 0


async def test_download_from_id_404_nao_derruba_e_marca_falha(monkeypatch):
    """Erro 404 da API (item removido/URL errada): não pode propagar --
    tem que virar log + pl_failed, senão um item ruim numa playlist
    derruba o resto dela."""
    app = _build_app()

    async def handle_download_id(db, item_id, add_id, quality):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise httpx.HTTPStatusError(
                "404 Not Found",
                request=SimpleNamespace(),
                response=SimpleNamespace(status_code=404),
            )

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)

    result = await core.QobuzDL.download_from_id(app, "789", is_playlist=True)

    assert result is False
    assert app.settings.pl_failed == 1


async def test_download_from_id_non_streamable_marca_falha(monkeypatch):
    """NonStreamable (faixa sem liberação de stream): mesmo tratamento
    do HTTPStatusError -- loga, conta pl_failed, segue pro próximo item."""
    app = _build_app()

    async def handle_download_id(db, item_id, add_id, quality):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            raise core.NonStreamable("sem stream liberado")

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)

    result = await core.QobuzDL.download_from_id(app, "999", is_playlist=True)

    assert result is False
    assert app.settings.pl_failed == 1


async def test_download_from_id_aplica_delay_apos_download(monkeypatch):
    """--delay entre downloads: precisa dormir DEPOIS do download,
    mesmo em erro -- é isso que evita rate limiting em lote."""
    app = _build_app(delay=5)
    dormiu = {}

    async def fake_sleep(segundos):
        dormiu["segundos"] = segundos

    async def handle_download_id(db, item_id, add_id, quality):
        return False

    class FakeDownload:
        def __init__(self, *args, **kwargs):
            pass

        async def download_id_by_type(self, *args, **kwargs):
            return True

    monkeypatch.setattr(core, "handle_download_id", handle_download_id)
    monkeypatch.setattr(core.downloader, "Download", FakeDownload)
    monkeypatch.setattr(core.asyncio, "sleep", fake_sleep)

    await core.QobuzDL.download_from_id(app, "111")

    assert dormiu["segundos"] == 5


async def test_get_tokens_filtra_secrets_vazios(monkeypatch):
    """`get_tokens` busca app_id + secrets via Bundle.create() -- precisa
    descartar secrets vazios/None (`get_secrets()` pode trazer timezones
    sem segredo decodificado, ver docstring de Bundle.get_secrets)."""
    app = SimpleNamespace()

    class FakeBundle:
        @classmethod
        async def create(cls):
            return cls()

        def get_app_id(self):
            return "123456789"

        def get_secrets(self):
            return {"berlin": "segredo123", "london": "", "paris": None}

    monkeypatch.setattr(core, "Bundle", FakeBundle)

    await core.QobuzDL.get_tokens(app)

    assert app.app_id == "123456789"
    assert app.secrets == ["segredo123"]


async def test_initialize_client_primeira_vez_nao_fecha_nada(monkeypatch):
    """Primeira chamada (sem client antigo): não pode tentar fechar nada
    que não existe."""
    app = SimpleNamespace(
        settings=SimpleNamespace(user_auth_token=None), force_english=False, quality=6
    )
    novo_cliente = object()

    class FakeClient:
        @classmethod
        async def create(cls, *args, **kwargs):
            return novo_cliente

    monkeypatch.setattr(core.qopy, "Client", FakeClient)

    await core.QobuzDL.initialize_client(app, "a@b.com", "senha", "111", ["s1"])

    assert app.client is novo_cliente


async def test_initialize_client_troca_e_fecha_cliente_antigo(monkeypatch):
    """Reautenticação (ex.: comando `auth`): o client antigo tem que ser
    fechado -- senão vaza a sessão HTTP dele pro resto do programa (mesma
    classe de bug do lyrics_engine.py)."""
    fechado = {}

    class ClienteAntigo:
        async def close(self):
            fechado["chamado"] = True

    app = SimpleNamespace(
        settings=SimpleNamespace(user_auth_token=None),
        force_english=False,
        quality=6,
        client=ClienteAntigo(),
    )
    novo_cliente = object()

    class FakeClient:
        @classmethod
        async def create(cls, *args, **kwargs):
            return novo_cliente

    monkeypatch.setattr(core.qopy, "Client", FakeClient)

    await core.QobuzDL.initialize_client(app, "a@b.com", "senha", "111", ["s1"])

    assert app.client is novo_cliente
    assert fechado.get("chamado") is True


async def test_initialize_client_nao_fecha_se_create_devolver_o_mesmo_client(
    monkeypatch,
):
    """Se `qopy.Client.create()` devolver o MESMO objeto que já estava em
    `self.client` (client cacheado/reaproveitado), não pode chamar
    `close()` nele -- fecharia a sessão que acabou de ser devolvida como
    válida."""

    async def close_nao_deveria_ser_chamado():
        raise AssertionError(
            "não deveria fechar o client que create() devolveu como atual"
        )

    cliente_existente = SimpleNamespace(close=close_nao_deveria_ser_chamado)

    app = SimpleNamespace(
        settings=SimpleNamespace(user_auth_token=None),
        force_english=False,
        quality=6,
        client=cliente_existente,
    )

    class FakeClient:
        @classmethod
        async def create(cls, *args, **kwargs):
            return cliente_existente

    monkeypatch.setattr(core.qopy, "Client", FakeClient)

    await core.QobuzDL.initialize_client(app, "a@b.com", "senha", "111", ["s1"])

    assert app.client is cliente_existente
