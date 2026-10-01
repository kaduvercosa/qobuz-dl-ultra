"""Testes de build_tool_argv: argumentos da CLI montados pela GUI web."""

from __future__ import annotations

import os

import pytest

from qobuz_dl.webapp import TOOL_ACTIONS, TOOL_LABELS, ToolRequest, build_tool_argv

SETTINGS = {
    "directory": "/music",
    "quality": 6,
    "max_workers": 2,
    "segment_workers": 4,
    "playlist_as_albums": False,
}
COMMON = [
    "--directory",
    "/music",
    "--quality",
    "6",
    "--max-workers",
    "2",
    "--segment-workers",
    "4",
]


def argv(action: str, settings: dict | None = None, **kwargs):
    payload = ToolRequest(action=action, **kwargs)
    return build_tool_argv(payload, settings or SETTINGS)


def test_acao_desconhecida_e_rejeitada():
    with pytest.raises(ValueError, match="desconhecida"):
        argv("rm-rf")


def test_todas_as_acoes_conhecidas_geram_lista_de_strings():
    for action in TOOL_ACTIONS:
        result = argv(
            action,
            target="alvo",
            confirm_downloads=True,
            confirm_file_changes=True,
            confirm_delete=True,
        )
        assert isinstance(result, list)
        assert all(isinstance(item, str) for item in result)


def test_tool_actions_corresponde_aos_rotulos():
    assert TOOL_ACTIONS == set(TOOL_LABELS)


def test_target_com_metacaracteres_de_shell_continua_um_unico_argumento():
    alvo = "/tmp/x; rm -rf / && echo pwned"
    result = argv("inspect", target=alvo)
    assert result == ["inspect", alvo]


# --- purge / show-config -----------------------------------------------------


def test_purge_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("purge")


def test_purge_confirmado():
    assert argv("purge", confirm_delete=True) == ["-p"]


def test_show_config():
    assert argv("show-config") == ["--show-config"]


# --- watch ---------------------------------------------------------------------


def test_watch_exige_pasta():
    with pytest.raises(ValueError, match="pasta"):
        argv("watch", confirm_file_changes=True)


def test_watch_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("watch", target="/music/new")


def test_watch_confirmado():
    result = argv("watch", target="/music/new", confirm_file_changes=True)
    assert result == ["--watch", "/music/new"]


@pytest.mark.skipif(os.name != "posix", reason="expanduser depende de HOME no POSIX")
def test_watch_expande_til(monkeypatch):
    monkeypatch.setenv("HOME", "/home/usuario")
    result = argv("watch", target="~/Musicas", confirm_file_changes=True)
    assert result == ["--watch", "/home/usuario/Musicas"]


# --- sync-db / find-duplicates -------------------------------------------------


def test_sync_db_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("sync-db")


def test_sync_db_usa_pasta_das_configuracoes_por_padrao():
    assert argv("sync-db", confirm_file_changes=True) == ["--sync-db", "/music"]


def test_sync_db_com_pasta_explicita():
    result = argv("sync-db", target="/outra", confirm_file_changes=True)
    assert result == ["--sync-db", "/outra"]


def test_find_duplicates_exige_pasta():
    with pytest.raises(ValueError, match="pasta"):
        argv("find-duplicates")


def test_find_duplicates_ok():
    assert argv("find-duplicates", target="/music/a") == [
        "--find-duplicates",
        "/music/a",
    ]


# --- comandos simples ----------------------------------------------------------


def test_doctor_stats_user():
    assert argv("doctor") == ["doctor", "--json"]
    assert argv("stats") == ["stats"]
    assert argv("stats", artists=True) == ["stats", "--artistas"]
    assert argv("user") == ["user", "--json"]


# --- library -------------------------------------------------------------------


def test_library_padrao_e_status():
    assert argv("library") == ["library", "status"]


def test_library_subacao_invalida():
    with pytest.raises(ValueError, match="inválida"):
        argv("library", subaction="drop-all")


def test_library_missing_limita_em_200():
    result = argv("library", subaction="missing", limit=500)
    assert result == ["library", "missing", "--limit", "200"]


def test_library_list_respeita_limite_menor():
    result = argv("library", subaction="list", limit=15)
    assert result == ["library", "list", "--limit", "15"]


def test_library_unmark_exige_id_e_confirmacao():
    with pytest.raises(ValueError, match="ID"):
        argv("library", subaction="unmark", confirm_file_changes=True)
    with pytest.raises(ValueError, match="Confirme"):
        argv("library", subaction="unmark", target="123")


def test_library_unmark_ok():
    result = argv(
        "library", subaction="unmark", target="123", confirm_file_changes=True
    )
    assert result == ["library", "unmark", "123"]


def test_library_reset_stuck_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("library", subaction="reset-stuck")
    assert argv("library", subaction="reset-stuck", confirm_file_changes=True) == [
        "library",
        "reset-stuck",
    ]


def test_library_reconcile_dry_run_por_padrao():
    assert argv("library", subaction="reconcile") == [
        "library",
        "reconcile",
        "--dry-run",
    ]


def test_library_reconcile_com_pasta_e_fix():
    result = argv("library", subaction="reconcile", target="/music", fix=True)
    assert result == ["library", "reconcile", "/music", "--dry-run", "--fix"]


def test_library_reconcile_real_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("library", subaction="reconcile", dry_run=False)


def test_library_reconcile_real_confirmado():
    result = argv(
        "library", subaction="reconcile", dry_run=False, confirm_file_changes=True
    )
    assert result == ["library", "reconcile"]


# --- dl ------------------------------------------------------------------------


def test_dl_dry_run_padrao():
    url = "https://www.qobuz.com/album/x"
    assert argv("dl", target=url) == ["dl", *COMMON, "--dry-run", url]


def test_dl_exige_alvo():
    with pytest.raises(ValueError, match="URL"):
        argv("dl")


def test_dl_real_exige_confirmacao():
    with pytest.raises(ValueError, match="downloads"):
        argv("dl", target="https://x", dry_run=False)


def test_dl_real_confirmado_sem_dry_run():
    result = argv("dl", target="https://x", dry_run=False, confirm_downloads=True)
    assert result == ["dl", *COMMON, "https://x"]


def test_dl_playlist_como_albuns():
    settings = {**SETTINGS, "playlist_as_albums": True}
    result = argv("dl", settings, target="https://x")
    assert result[-1] == "--playlist-as-albums"


# --- sync-favorites ------------------------------------------------------------


def test_sync_favorites_dry_run_padrao():
    assert argv("sync-favorites") == [
        "sync-favorites",
        *COMMON,
        "--dry-run",
        "--limit",
        "20",
    ]


def test_sync_favorites_baixar_exige_confirmacao():
    with pytest.raises(ValueError, match="favoritos"):
        argv("sync-favorites", dry_run=False, download_new=True)
    with pytest.raises(ValueError, match="favoritos"):
        argv("sync-favorites", dry_run=False, download_missing=True)


def test_sync_favorites_sem_download_nao_exige_confirmacao():
    result = argv("sync-favorites", dry_run=False)
    assert "--dry-run" not in result
    assert "--yes" not in result


def test_sync_favorites_completo():
    result = argv(
        "sync-favorites",
        dry_run=False,
        download_new=True,
        download_missing=True,
        confirm_downloads=True,
        every=30,
        limit=50,
    )
    assert result == [
        "sync-favorites",
        *COMMON,
        "--limit",
        "50",
        "--download-new",
        "--download-missing",
        "--yes",
        "--every",
        "30",
    ]


# --- lucky ---------------------------------------------------------------------


def test_lucky_busca_padrao():
    assert argv("lucky", target="blue") == [
        "lucky",
        *COMMON,
        "--type",
        "album",
        "--number",
        "20",
        "blue",
    ]


def test_lucky_exige_termo():
    with pytest.raises(ValueError, match="busca"):
        argv("lucky")


def test_lucky_real_exige_confirmacao():
    with pytest.raises(ValueError, match="downloads"):
        argv("lucky", target="blue", dry_run=False)


def test_lucky_limita_numero_em_20():
    result = argv("lucky", target="blue", limit=300)
    assert result[result.index("--number") + 1] == "20"
    result = argv("lucky", target="blue", limit=5)
    assert result[result.index("--number") + 1] == "5"


# --- playlists -----------------------------------------------------------------


def test_import_playlist_exige_confirmacao_antes_do_alvo():
    with pytest.raises(ValueError, match="confirme"):
        argv("import-playlist", target="lista.m3u")


def test_import_playlist_exige_alvo():
    with pytest.raises(ValueError, match="playlist"):
        argv("import-playlist", confirm_downloads=True)


def test_import_playlist_ok():
    result = argv("import-playlist", target="lista.m3u", confirm_downloads=True)
    assert result == ["import-playlist", "lista.m3u"]


def test_sync_playlist_exige_as_duas_confirmacoes():
    with pytest.raises(ValueError, match="Confirme"):
        argv("sync-playlist", target="u", confirm_downloads=True)
    with pytest.raises(ValueError, match="Confirme"):
        argv("sync-playlist", target="u", confirm_file_changes=True)


def test_sync_playlist_exige_alvo():
    with pytest.raises(ValueError, match="URL"):
        argv("sync-playlist", confirm_downloads=True, confirm_file_changes=True)


def test_sync_playlist_ok():
    result = argv(
        "sync-playlist",
        target="https://x",
        confirm_downloads=True,
        confirm_file_changes=True,
    )
    assert result == ["sync-playlist", "--yes", "https://x"]


# --- lyrics / scan / inspect ---------------------------------------------------


def test_lyrics_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("lyrics")


def test_lyrics_usa_pasta_das_configuracoes():
    assert argv("lyrics", confirm_file_changes=True) == ["lyrics", "/music"]
    assert argv("lyrics", target="/x", confirm_file_changes=True) == ["lyrics", "/x"]


def test_scan_dry_run_padrao():
    assert argv("scan") == [
        "scan",
        "/music",
        "--max-depth",
        "4",
        "--no-review",
        "--dry-run",
    ]


def test_scan_real_exige_confirmacao():
    with pytest.raises(ValueError, match="Confirme"):
        argv("scan", dry_run=False)


def test_scan_real_confirmado():
    result = argv(
        "scan", target="/a", dry_run=False, confirm_file_changes=True, max_depth=7
    )
    assert result == ["scan", "/a", "--max-depth", "7", "--no-review"]


def test_inspect_exige_arquivo():
    with pytest.raises(ValueError, match="arquivo"):
        argv("inspect")


def test_inspect_ok():
    assert argv("inspect", target="/a.flac") == ["inspect", "/a.flac"]
