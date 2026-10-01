"""Testes extras para qobuz_dl/webapp.py.

Cobre cenários de borda que o test_webapp.py não alcança:
- segment_workers clampado
- verify_after_download / embed_art na INI
- save_settings sem engine atribuído (não deve explodir)
- GuiService em modo demo não lê config.ini
- build_tool_argv com playlist_as_albums=True adicionando flag
- ToolRequest com subaction None em library levanta
- INI com seção faltando (sem seção [qobuz])
"""

from __future__ import annotations

import configparser
from pathlib import Path
from unittest.mock import patch

import pytest

from qobuz_dl import webapp
from qobuz_dl.webapp import GuiService, SettingsRequest, ToolRequest, build_tool_argv


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, mode=0o700)
    config_file = config_dir / "config.ini"
    db_file = config_dir / "downloads.db"
    paths = {
        "config_path": str(config_dir),
        "config_file": str(config_file),
        "qobuz_db": str(db_file),
    }
    monkeypatch.setattr(webapp, "get_config_paths", lambda: paths)
    return paths


@pytest.fixture()
def service(tmp_config):
    return GuiService()


@pytest.fixture()
def music_dir(tmp_path):
    d = tmp_path / "Music"
    d.mkdir()
    return d


@pytest.fixture()
def base_payload(music_dir):
    return SettingsRequest(
        directory=str(music_dir),
        quality=6,
        embed_art=False,
        fetch_lyrics=False,
        lrc_files=True,
        credits=True,
        m3u=True,
        quality_fallback=False,
        playlist_as_albums=False,
        verify_after_download=True,
        max_workers=2,
        segment_workers=4,
        embedded_art_size="org",
        saved_art_size="org",
    )


# ---------------------------------------------------------------------------
# segment_workers clampado
# ---------------------------------------------------------------------------


class TestSegmentWorkersClamp:
    def test_clampado_acima_de_32(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "segment_workers", "999")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        s = GuiService().local_settings
        assert s["segment_workers"] <= 32

    def test_minimo_1(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "segment_workers", "0")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        s = GuiService().local_settings
        assert s["segment_workers"] >= 1


# ---------------------------------------------------------------------------
# verify_after_download / embed_art lidos corretamente
# ---------------------------------------------------------------------------


class TestBoolFlags:
    def test_verify_after_download_true(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "verify_after_download", "true")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        assert GuiService().local_settings["verify_after_download"] is True

    def test_embed_art_false(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "embed_art", "false")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        assert GuiService().local_settings["embed_art"] is False

    def test_no_credits_true_inverte_para_credits_false(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "no_credits", "true")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        assert GuiService().local_settings["credits"] is False

    def test_no_fallback_true_inverte_para_quality_fallback_false(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "no_fallback", "true")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        assert GuiService().local_settings["quality_fallback"] is False


# ---------------------------------------------------------------------------
# save_settings sem engine não deve levantar
# ---------------------------------------------------------------------------


class TestSaveWithoutEngine:
    def test_sem_engine_nao_explode(self, service, base_payload, music_dir):
        assert not hasattr(service, "engine") or service.engine is None
        with patch.object(webapp, "ensure_directory_ready", return_value=music_dir):
            service.save_settings(base_payload)  # não deve levantar
        assert service.local_settings["quality"] == 6


# ---------------------------------------------------------------------------
# GuiService em modo demo
# ---------------------------------------------------------------------------


class TestDemoMode:
    def test_demo_nao_le_config_ini(self, tmp_config):
        """No modo demo, mesmo que exista um config.ini com quality=27,
        config_status deve retornar configured=False."""
        config_file = Path(tmp_config["config_file"])
        p = configparser.ConfigParser(interpolation=None)
        p.add_section("qobuz")
        p.set("qobuz", "default_quality", "27")
        p.set("qobuz", "directory", "/tmp/x")
        with open(config_file, "w", encoding="utf-8") as fh:
            p.write(fh)
        status = GuiService(demo=True).config_status()
        assert status["configured"] is False
        assert status["demo"] is True


# ---------------------------------------------------------------------------
# build_tool_argv — playlist_as_albums
# ---------------------------------------------------------------------------


class TestBuildToolArgvExtra:
    BASE = {
        "directory": "/tmp/Music",
        "quality": 7,
        "max_workers": 2,
        "segment_workers": 4,
        "playlist_as_albums": True,
    }

    def test_dl_com_playlist_as_albums_inclui_flag(self):
        argv = build_tool_argv(
            ToolRequest(
                action="dl", dry_run=True, target="https://open.qobuz.com/album/abc"
            ),
            self.BASE,
        )
        assert "--playlist-as-albums" in argv

    def test_sync_favorites_sem_download_nao_levanta(self):
        """dry_run=False e download_new=False não deve precisar de confirmação."""
        argv = build_tool_argv(
            ToolRequest(action="sync-favorites", dry_run=False, download_new=False),
            self.BASE,
        )
        assert "sync-favorites" in argv

    def test_library_sem_subaction_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(ToolRequest(action="library", subaction=None), self.BASE)

    def test_library_unmark_com_target_e_confirmacao(self):
        argv = build_tool_argv(
            ToolRequest(
                action="library",
                subaction="unmark",
                target="abc123",
                confirm_file_changes=True,
            ),
            self.BASE,
        )
        assert "unmark" in argv
        assert "abc123" in argv


# ---------------------------------------------------------------------------
# INI sem seção [qobuz]
# ---------------------------------------------------------------------------


class TestIniSemSecao:
    def test_config_sem_secao_qobuz_usa_defaults(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        config_file.write_text("[outrasecao]\nchave = valor\n", encoding="utf-8")
        s = GuiService().local_settings
        assert s["quality"] is None
        assert s["max_workers"] == 1
