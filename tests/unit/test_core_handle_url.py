"""Testes unitários de QobuzDL.handle_url()."""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


async def _empty_async_generator(*args, **kwargs):
    if False:
        yield None


def _noop_client():
    async def placeholder(*args, **kwargs):
        return None

    return SimpleNamespace(
        get_plist_meta=_empty_async_generator,
        get_artist_meta=_empty_async_generator,
        get_label_meta=_empty_async_generator,
        get_album_meta=placeholder,
        search_albums=placeholder,
        search_artists=placeholder,
        search_tracks=placeholder,
        search_playlists=placeholder,
        get_favorites=placeholder,
    )


def _build_app(**overrides):
    app = SimpleNamespace(
        client=_noop_client(),
        directory="/tmp/qdl-teste",
        smart_discography=False,
        no_m3u_for_playlists=True,
        blacklist_patterns=None,
        allowed_release_types=None,
        playlist_as_albums=False,
        delay=0,
        folder_format="original",
        settings=SimpleNamespace(
            max_workers=1,
            pl_success=0,
            pl_skipped=0,
            pl_failed=0,
            multiple_disc_one_dir=False,
        ),
    )
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


def _sem_efeitos_colaterais(monkeypatch):
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)
    monkeypatch.setattr(
        core.downloader,
        "print_download_header",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        core.downloader,
        "safe_print",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(core, "make_m3u", lambda path: None)

    async def finalize_report(*args, **kwargs):
        return None

    monkeypatch.setattr(core.postprocess, "finalize_report", finalize_report)


async def test_tipo_reconhecido_mas_nao_mapeado_devolve_false(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("desconhecido", "1"),
    )
    app = _build_app()

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/x/1",
    )

    assert result is False


async def test_track_direto_delega_pro_download_from_id(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("track", "555"),
    )
    chamada = {}

    async def download_from_id(item_id, album):
        chamada["args"] = (item_id, album)
        return True

    app = _build_app()
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/track/555",
    )

    assert result is True
    assert chamada["args"] == ("555", False)


async def test_album_direto_delega_pro_download_from_id(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("album", "999"),
    )
    chamada = {}

    async def download_from_id(item_id, album):
        chamada["args"] = (item_id, album)
        return True

    app = _build_app()
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/album/999",
    )

    assert result is True
    assert chamada["args"] == ("999", True)


async def test_container_sem_conteudo_devolve_false(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("playlist", "1"),
    )
    app = _build_app()

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/playlist/1",
    )

    assert result is False


async def test_playlist_sequencial_soma_outcomes_mistos(monkeypatch):
    _sem_efeitos_colaterais(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("playlist", "1"),
    )

    async def get_plist_meta(item_id):
        yield {
            "name": "Minha Playlist",
            "owner": {"name": "Fulano"},
            "duration": 100,
            "tracks": {"items": [{"id": "t1"}, {"id": "t2"}]},
        }

    resultados = iter([True, False])
    chamadas = []

    async def download_from_id(item_id, album, new_path=None, **kwargs):
        chamadas.append(item_id)
        return next(resultados)

    app = _build_app(
        client=_noop_client(),
        no_m3u_for_playlists=False,
    )
    app.client.get_plist_meta = get_plist_meta
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/playlist/1",
    )

    assert chamadas == ["t1", "t2"]
    assert result is False


async def test_paralelo_baixa_todos_via_gather(monkeypatch):
    _sem_efeitos_colaterais(monkeypatch)
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("playlist", "1"),
    )

    async def fake_sleep(_segundos):
        return None

    monkeypatch.setattr(core.asyncio, "sleep", fake_sleep)

    async def get_plist_meta(item_id):
        yield {
            "name": "P",
            "owner": {},
            "duration": 0,
            "tracks": {"items": [{"id": "a"}, {"id": "b"}]},
        }

    chamadas = []

    async def download_from_id(item_id, album, new_path=None, **kwargs):
        chamadas.append(item_id)
        return True

    settings = SimpleNamespace(
        max_workers=2,
        pl_success=0,
        pl_skipped=0,
        pl_failed=0,
        multiple_disc_one_dir=False,
    )
    app = _build_app(
        client=_noop_client(),
        settings=settings,
    )
    app.client.get_plist_meta = get_plist_meta
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/playlist/1",
    )

    assert sorted(chamadas) == ["a", "b"]
    assert result is True


async def test_blacklist_pula_item_correspondente(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("artist", "1"),
    )
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_artist_meta(item_id):
        yield {
            "name": "Artista",
            "albums": {
                "items": [
                    {"id": "a1", "title": "Album Bom"},
                    {"id": "a2", "title": "Album Ruim (Bootleg)"},
                ],
            },
        }

    chamadas = []

    async def download_from_id(item_id, album, new_path=None, **kwargs):
        chamadas.append(item_id)
        return True

    app = _build_app(
        client=_noop_client(),
        blacklist_patterns=["bootleg"],
    )
    app.client.get_artist_meta = get_artist_meta
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/artist/1",
    )

    assert chamadas == ["a1"]
    assert result is True


async def test_filtro_interativo_de_artista_aplica_tipo_selecionado(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("artist", "1"),
    )
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_artist_meta(item_id):
        yield {
            "name": "Artista",
            "albums": {
                "items": [
                    {"id": "a1", "title": "X", "tracks_count": 10},
                ],
            },
        }

    async def fake_tui_select(
        title,
        options,
        is_multi=False,
        item_category="album",
    ):
        return [(options[0], 0)]

    monkeypatch.setattr(core, "_tui_select", fake_tui_select)

    chamadas = []

    async def download_from_id(item_id, album, new_path=None, **kwargs):
        chamadas.append(item_id)
        return True

    app = _build_app(client=_noop_client())
    app.client.get_artist_meta = get_artist_meta
    app._is_interactive_session = True
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/artist/1",
    )

    assert app.allowed_release_types == ["album"]
    assert chamadas == ["a1"]
    assert result is True


async def test_filtro_interativo_sem_selecao_esvazia_a_lista(monkeypatch):
    monkeypatch.setattr(
        core,
        "get_url_info",
        lambda url: ("artist", "1"),
    )
    monkeypatch.setattr(core, "create_and_return_dir", lambda path: path)

    async def get_artist_meta(item_id):
        yield {
            "name": "Artista",
            "albums": {"items": [{"id": "a1", "title": "X"}]},
        }

    async def fake_tui_select(
        title,
        options,
        is_multi=False,
        item_category="album",
    ):
        return []

    monkeypatch.setattr(core, "_tui_select", fake_tui_select)

    chamadas = []

    async def download_from_id(item_id, album, new_path=None, **kwargs):
        chamadas.append(item_id)
        return True

    app = _build_app(client=_noop_client())
    app.client.get_artist_meta = get_artist_meta
    app._is_interactive_session = True
    app.download_from_id = download_from_id

    result = await core.QobuzDL.handle_url(
        app,
        "https://play.qobuz.com/artist/1",
    )

    assert chamadas == []
    assert app.allowed_release_types == []
    assert result is False
