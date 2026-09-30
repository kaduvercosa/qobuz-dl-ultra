from fastapi.testclient import TestClient

from qobuz_dl.webapp import ToolRequest, build_tool_argv, create_app


def test_demo_home_and_assets_are_served():
    client = TestClient(create_app(demo=True))
    assert client.get("/", headers={"host": "localhost"}).status_code == 200
    assert "qobuz-dl-ultra" in client.get("/", headers={"host": "localhost"}).text
    assert client.get("/app.css", headers={"host": "localhost"}).status_code == 200
    assert client.get("/app.js", headers={"host": "localhost"}).status_code == 200
    assert (
        client.get(
            "/assets/cover-nebula.jpg", headers={"host": "localhost"}
        ).status_code
        == 200
    )


def test_demo_catalog_and_album_are_searchable():
    client = TestClient(create_app(demo=True))
    status = client.get("/api/status", headers={"host": "localhost"})
    assert status.status_code == 200
    assert status.json()["demo"] is True
    assert status.json()["configured"] is False

    result = client.get("/api/search?q=blue&kind=all", headers={"host": "localhost"})
    assert result.status_code == 200
    assert result.json()["albums"][0]["title"] == "Blue Hour"
    assert result.json()["tracks"][0]["album"] == "Blue Hour"

    album = client.get("/api/album/demo-album-1", headers={"host": "localhost"})
    assert album.status_code == 200
    assert album.json()["album"]["artist"] == "Mira Sol"
    assert len(album.json()["tracks"]) == 2


def test_demo_blocks_actions_that_would_access_account_or_download():
    client = TestClient(create_app(demo=True))
    headers = {"host": "localhost"}
    assert client.post("/api/connect", json={}, headers=headers).json()["demo"] is True
    assert client.get("/api/stream/demo-track-1", headers=headers).status_code == 409
    assert (
        client.post(
            "/api/download", json={"id": "123", "kind": "track"}, headers=headers
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/favorites", json={"id": "123", "kind": "track"}, headers=headers
        ).status_code
        == 409
    )


def test_normal_app_is_loopback_only_and_rejects_cross_site_posts():
    client = TestClient(create_app())
    assert client.get("/api/status", headers={"host": "example.com"}).status_code == 403
    blocked = client.post(
        "/api/connect",
        json={},
        headers={
            "host": "localhost",
            "origin": "https://example.com",
            "sec-fetch-site": "cross-site",
        },
    )
    assert blocked.status_code == 403
    assert client.get("/api/status", headers={"host": "localhost"}).status_code == 200


def test_demo_search_limits_and_missing_album():
    client = TestClient(create_app(demo=True))
    headers = {"host": "localhost"}
    result = client.get("/api/search?q=a&kind=all&limit=500", headers=headers)
    assert result.json() == {"tracks": [], "albums": []}
    assert client.get("/api/album/not-here", headers=headers).status_code == 404
    assert client.get("/api/favorites?kind=invalid", headers=headers).status_code == 400


def test_gui_settings_returns_defaults_and_demo_disables_tool_runner():
    client = TestClient(create_app(demo=True))
    headers = {"host": "localhost"}
    settings = client.get("/api/settings", headers=headers)
    assert settings.status_code == 200
    assert settings.json()["quality"] == 6
    assert settings.json()["max_workers"] == 1
    assert settings.json()["fetch_lyrics"] is True
    assert client.get("/api/tools", headers=headers).status_code == 200
    response = client.post("/api/tools/run", json={"action": "doctor"}, headers=headers)
    assert response.status_code == 409
    account = client.post(
        "/api/account/configure",
        json={"email": "music@example.com", "token": "private-token"},
        headers=headers,
    )
    assert account.status_code == 409


def test_tool_argv_uses_allowlisted_arguments_without_shell_expansion():
    settings = {
        "directory": "/music",
        "quality": 6,
        "max_workers": 2,
        "segment_workers": 4,
        "playlist_as_albums": False,
    }
    payload = ToolRequest(
        action="dl", target="https://qobuz.com/album/a; touch /tmp/bad", dry_run=True
    )
    argv = build_tool_argv(payload, settings)
    assert "--dry-run" in argv
    assert argv[-1] == "https://qobuz.com/album/a; touch /tmp/bad"
    assert "touch" not in argv[:-1]


def test_destructive_and_file_changing_tools_require_explicit_confirmation():
    settings = {
        "directory": "/music",
        "quality": 6,
        "max_workers": 1,
        "segment_workers": 4,
        "playlist_as_albums": False,
    }
    try:
        build_tool_argv(ToolRequest(action="purge"), settings)
    except ValueError as exc:
        assert "Confirme" in str(exc)
    else:
        raise AssertionError("Purge must require explicit confirmation")

    try:
        build_tool_argv(ToolRequest(action="lyrics", target="/music"), settings)
    except ValueError as exc:
        assert "Confirme" in str(exc)
    else:
        raise AssertionError("Tag-writing tools must require explicit confirmation")


def test_tool_argv_rejects_unknown_action_and_sync_download_needs_confirmation():
    settings = {
        "directory": "/music",
        "quality": 6,
        "max_workers": 1,
        "segment_workers": 4,
        "playlist_as_albums": False,
    }
    try:
        build_tool_argv(ToolRequest(action="anything"), settings)
    except ValueError as exc:
        assert "desconhecida" in str(exc)
    else:
        raise AssertionError("Unknown actions must not be passed to a shell")
    try:
        build_tool_argv(
            ToolRequest(action="sync-playlist", target="https://qobuz.com/playlist/1"),
            settings,
        )
    except ValueError as exc:
        assert "Confirme" in str(exc)
    else:
        raise AssertionError("Playlist sync must require confirmations")


def test_settings_are_atomically_persisted_in_cli_config(tmp_path, monkeypatch):
    from qobuz_dl import webapp

    config_dir = tmp_path / "config"
    config_file = config_dir / "config.ini"

    monkeypatch.setattr(
        webapp,
        "get_config_paths",
        lambda: {
            "config_path": str(config_dir),
            "config_file": str(config_file),
            "qobuz_db": str(config_dir / "downloads.db"),
        },
    )

    app = webapp.create_app()
    service = app.state.service
    payload = webapp.SettingsRequest(
        directory=str(tmp_path / "Music"),
        quality=7,
        embed_art=False,
        fetch_lyrics=False,
        lrc_files=False,
        credits=False,
        m3u=True,
        quality_fallback=False,
        playlist_as_albums=True,
        verify_after_download=True,
        max_workers=6,
        segment_workers=10,
        embedded_art_size="600",
        saved_art_size="300",
    )

    service.save_settings(payload)

    saved = config_file.read_text(encoding="utf-8")
    assert "default_quality = 7" in saved
    assert "max_workers = 6" in saved
    assert "playlist_as_albums = true" in saved
    if __import__("os").name == "posix":
        assert config_file.stat().st_mode & 0o777 == 0o600
