"""Testes unitários para qobuz_dl/webapp.py.

Cobre:
- GuiService._settings_from_ini / _write_settings_to_ini (round-trip)
- GuiService.save_settings (valida qualidade, grava config.ini)
- GuiService.config_status (retorna defaults quando config ausente)
- build_tool_argv (geração de argumentos para cada ferramenta)
- SettingsRequest (validação de campos pelo Pydantic)
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from qobuz_dl import webapp
from qobuz_dl.webapp import (
    GuiService,
    SettingsRequest,
    ToolRequest,
    build_tool_argv,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """Redireciona get_config_paths() para um diretório temporário isolado."""
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
    """GuiService isolado com config vazia."""
    return GuiService()


@pytest.fixture()
def service_with_ini(tmp_config):
    """GuiService com um config.ini pré-populado."""
    config_file = Path(tmp_config["config_file"])
    parser = configparser.ConfigParser(interpolation=None)
    parser.add_section("qobuz")
    parser.set("qobuz", "directory", "/tmp/Música")
    parser.set("qobuz", "default_quality", "7")
    parser.set("qobuz", "max_workers", "4")
    parser.set("qobuz", "segment_workers", "8")
    parser.set("qobuz", "embed_art", "true")
    parser.set("qobuz", "fetch_lyrics", "true")
    parser.set("qobuz", "no_lrc_files", "false")
    parser.set("qobuz", "no_credits", "false")
    parser.set("qobuz", "no_m3u", "false")
    parser.set("qobuz", "no_fallback", "false")
    parser.set("qobuz", "playlist_as_albums", "true")
    parser.set("qobuz", "verify_after_download", "false")
    parser.set("qobuz", "embedded_art_size", "600")
    parser.set("qobuz", "saved_art_size", "300")
    config_file.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with open(config_file, "w", encoding="utf-8") as fh:
        parser.write(fh)
    return GuiService()


@pytest.fixture()
def settings_payload(tmp_path):
    """SettingsRequest válido para usar nos testes."""
    music_dir = tmp_path / "Music"
    music_dir.mkdir()
    return SettingsRequest(
        directory=str(music_dir),
        quality=7,
        embed_art=True,
        fetch_lyrics=True,
        lrc_files=True,
        credits=True,
        m3u=False,
        quality_fallback=True,
        playlist_as_albums=False,
        verify_after_download=False,
        max_workers=4,
        segment_workers=8,
        embedded_art_size="600",
        saved_art_size="300",
    )


# ---------------------------------------------------------------------------
# _settings_from_ini
# ---------------------------------------------------------------------------


class TestSettingsFromIni:
    def test_le_quality_e_diretorio(self, service_with_ini):
        s = service_with_ini.local_settings
        assert s["quality"] == 7
        assert s["directory"] == "/tmp/Música"

    def test_le_max_workers(self, service_with_ini):
        assert service_with_ini.local_settings["max_workers"] == 4

    def test_le_segment_workers(self, service_with_ini):
        assert service_with_ini.local_settings["segment_workers"] == 8

    def test_le_playlist_as_albums(self, service_with_ini):
        assert service_with_ini.local_settings["playlist_as_albums"] is True

    def test_le_embedded_art_size(self, service_with_ini):
        assert service_with_ini.local_settings["embedded_art_size"] == "600"

    def test_le_saved_art_size(self, service_with_ini):
        assert service_with_ini.local_settings["saved_art_size"] == "300"

    def test_config_ausente_retorna_defaults(self, service):
        s = service.local_settings
        assert s["quality"] is None
        assert s["max_workers"] == 1

    def test_max_workers_clampado_a_16(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        parser = configparser.ConfigParser(interpolation=None)
        parser.add_section("qobuz")
        parser.set("qobuz", "max_workers", "99")
        with open(config_file, "w", encoding="utf-8") as fh:
            parser.write(fh)
        s = GuiService().local_settings
        assert s["max_workers"] == 16

    def test_max_workers_minimo_1(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        parser = configparser.ConfigParser(interpolation=None)
        parser.add_section("qobuz")
        parser.set("qobuz", "max_workers", "0")
        with open(config_file, "w", encoding="utf-8") as fh:
            parser.write(fh)
        s = GuiService().local_settings
        assert s["max_workers"] == 1

    def test_qualidade_invalida_ignorada(self, tmp_config):
        config_file = Path(tmp_config["config_file"])
        parser = configparser.ConfigParser(interpolation=None)
        parser.add_section("qobuz")
        parser.set("qobuz", "default_quality", "99")
        with open(config_file, "w", encoding="utf-8") as fh:
            parser.write(fh)
        assert GuiService().local_settings["quality"] is None

    def test_bool_invertido_lrc(self, tmp_config):
        """no_lrc_files=true na INI → lrc_files=False na GUI."""
        config_file = Path(tmp_config["config_file"])
        parser = configparser.ConfigParser(interpolation=None)
        parser.add_section("qobuz")
        parser.set("qobuz", "no_lrc_files", "true")
        with open(config_file, "w", encoding="utf-8") as fh:
            parser.write(fh)
        assert GuiService().local_settings["lrc_files"] is False

    def test_bool_invertido_m3u(self, tmp_config):
        """no_m3u=true na INI → m3u=False na GUI."""
        config_file = Path(tmp_config["config_file"])
        parser = configparser.ConfigParser(interpolation=None)
        parser.add_section("qobuz")
        parser.set("qobuz", "no_m3u", "true")
        with open(config_file, "w", encoding="utf-8") as fh:
            parser.write(fh)
        assert GuiService().local_settings["m3u"] is False


# ---------------------------------------------------------------------------
# _write_settings_to_ini / round-trip
# ---------------------------------------------------------------------------


class TestWriteSettingsToIni:
    def test_round_trip_qualidade_e_diretorio(
        self, service, settings_payload, tmp_config
    ):
        with patch.object(
            webapp,
            "ensure_directory_ready",
            return_value=Path(settings_payload.directory),
        ):
            service.save_settings(settings_payload)
        text = Path(tmp_config["config_file"]).read_text(encoding="utf-8")
        assert "default_quality = 7" in text
        assert "directory = " in text

    def test_round_trip_max_workers(self, service, settings_payload, tmp_config):
        with patch.object(
            webapp,
            "ensure_directory_ready",
            return_value=Path(settings_payload.directory),
        ):
            service.save_settings(settings_payload)
        text = Path(tmp_config["config_file"]).read_text(encoding="utf-8")
        assert "max_workers = 4" in text

    def test_round_trip_playlist_as_albums(self, service, settings_payload, tmp_config):
        payload = settings_payload.model_copy(update={"playlist_as_albums": True})
        with patch.object(
            webapp, "ensure_directory_ready", return_value=Path(payload.directory)
        ):
            service.save_settings(payload)
        text = Path(tmp_config["config_file"]).read_text(encoding="utf-8")
        assert "playlist_as_albums = true" in text

    def test_bool_invertido_no_lrc_files_gravado(
        self, service, settings_payload, tmp_config
    ):
        """lrc_files=False na GUI → no_lrc_files = true no INI."""
        payload = settings_payload.model_copy(update={"lrc_files": False})
        with patch.object(
            webapp, "ensure_directory_ready", return_value=Path(payload.directory)
        ):
            service.save_settings(payload)
        text = Path(tmp_config["config_file"]).read_text(encoding="utf-8")
        assert "no_lrc_files = true" in text

    def test_bool_invertido_no_m3u_gravado(self, service, settings_payload, tmp_config):
        """m3u=True na GUI → no_m3u = false no INI."""
        payload = settings_payload.model_copy(update={"m3u": True})
        with patch.object(
            webapp, "ensure_directory_ready", return_value=Path(payload.directory)
        ):
            service.save_settings(payload)
        text = Path(tmp_config["config_file"]).read_text(encoding="utf-8")
        assert "no_m3u = false" in text

    def test_arquivo_tem_permissao_600_no_posix(
        self, service, settings_payload, tmp_config
    ):
        with patch.object(
            webapp,
            "ensure_directory_ready",
            return_value=Path(settings_payload.directory),
        ):
            service.save_settings(settings_payload)
        if os.name == "posix":
            mode = Path(tmp_config["config_file"]).stat().st_mode & 0o777
            assert mode == 0o600

    def test_preserva_chaves_existentes(
        self, service_with_ini, settings_payload, tmp_config
    ):
        """Gravar settings não apaga chaves como email/app_id."""
        config_file = Path(tmp_config["config_file"])
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(config_file, encoding="utf-8")
        if not parser.has_section("qobuz"):
            parser.add_section("qobuz")
        parser.set("qobuz", "email", "usuario@example.com")
        parser.set("qobuz", "app_id", "12345")
        with open(config_file, "w", encoding="utf-8") as fh:
            parser.write(fh)

        with patch.object(
            webapp,
            "ensure_directory_ready",
            return_value=Path(settings_payload.directory),
        ):
            service_with_ini.save_settings(settings_payload)

        text = config_file.read_text(encoding="utf-8")
        assert "usuario@example.com" in text
        assert "12345" in text


# ---------------------------------------------------------------------------
# save_settings
# ---------------------------------------------------------------------------


class TestSaveSettings:
    def test_qualidade_invalida_levanta_value_error(self, service):
        """Força um objeto fora do range de qualidade válido via __new__."""
        obj = object.__new__(SettingsRequest)
        for field, val in [
            ("quality", 99),
            ("directory", "/tmp/Music"),
            ("embed_art", True),
            ("fetch_lyrics", True),
            ("lrc_files", True),
            ("credits", True),
            ("m3u", False),
            ("quality_fallback", True),
            ("playlist_as_albums", False),
            ("verify_after_download", False),
            ("no_cover", False),
            ("smart_discography", False),
            ("multi_value_tags", False),
            ("max_workers", 1),
            ("segment_workers", 4),
            ("embedded_art_size", "org"),
            ("saved_art_size", "org"),
            ("folder_format", "{artist}/{album}"),
            ("track_format", "{tracknumber}. {title}"),
        ]:
            object.__setattr__(obj, field, val)
        with pytest.raises(ValueError, match="(?i)qualidade"):
            service.save_settings(obj)

    def test_atualiza_local_settings(self, service, settings_payload):
        with patch.object(
            webapp,
            "ensure_directory_ready",
            return_value=Path(settings_payload.directory),
        ):
            service.save_settings(settings_payload)
        assert service.local_settings["quality"] == 7
        assert service.local_settings["max_workers"] == 4

    def test_atualiza_engine_quando_presente(self, service, settings_payload):
        engine = MagicMock()
        engine.settings = MagicMock()
        service.engine = engine
        with patch.object(
            webapp,
            "ensure_directory_ready",
            return_value=Path(settings_payload.directory),
        ):
            service.save_settings(settings_payload)
        assert engine.quality == 7
        assert engine.settings.max_workers == 4


# ---------------------------------------------------------------------------
# config_status
# ---------------------------------------------------------------------------


class TestConfigStatus:
    def test_sem_config_retorna_defaults(self, service):
        status = service.config_status()
        assert status["configured"] is False
        assert status["quality"] in (5, 6, 7, 27)

    def test_demo_mode(self, tmp_config):
        svc = GuiService(demo=True)
        status = svc.config_status()
        assert status["demo"] is True
        assert status["configured"] is False

    def test_retorna_directory_da_ini(self, service_with_ini):
        status = service_with_ini.config_status()
        assert status["directory"] == "/tmp/Música"

    def test_retorna_quality_da_ini(self, service_with_ini):
        status = service_with_ini.config_status()
        assert status["quality"] == 7
        assert "Hi-Res" in status["qualityLabel"]


# ---------------------------------------------------------------------------
# build_tool_argv
# ---------------------------------------------------------------------------


class TestBuildToolArgv:
    BASE_SETTINGS = {
        "directory": "/tmp/Music",
        "quality": 6,
        "max_workers": 2,
        "segment_workers": 4,
        "playlist_as_albums": False,
    }

    def _s(self, **kwargs):
        return {**self.BASE_SETTINGS, **kwargs}

    def test_doctor(self):
        argv = build_tool_argv(ToolRequest(action="doctor"), self._s())
        assert argv == ["doctor", "--json"]

    def test_stats_sem_artistas(self):
        argv = build_tool_argv(ToolRequest(action="stats"), self._s())
        assert argv == ["stats"]

    def test_stats_com_artistas(self):
        argv = build_tool_argv(ToolRequest(action="stats", artists=True), self._s())
        assert "--artistas" in argv

    def test_user(self):
        argv = build_tool_argv(ToolRequest(action="user"), self._s())
        assert argv == ["user", "--json"]

    def test_show_config(self):
        argv = build_tool_argv(ToolRequest(action="show-config"), self._s())
        assert argv == ["--show-config"]

    def test_purge_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(action="purge", confirm_delete=False), self._s()
            )

    def test_purge_com_confirmacao(self):
        argv = build_tool_argv(
            ToolRequest(action="purge", confirm_delete=True), self._s()
        )
        assert argv == ["-p"]

    def test_dl_sem_url_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="dl", dry_run=False, confirm_downloads=True, target=""
                ),
                self._s(),
            )

    def test_dl_com_url(self):
        argv = build_tool_argv(
            ToolRequest(
                action="dl", dry_run=True, target="https://open.qobuz.com/album/abc"
            ),
            self._s(),
        )
        assert "dl" in argv
        assert "https://open.qobuz.com/album/abc" in argv

    def test_dl_sem_confirmacao_e_sem_dry_run_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="dl",
                    dry_run=False,
                    confirm_downloads=False,
                    target="https://x",
                ),
                self._s(),
            )

    def test_lucky_sem_target_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="lucky", dry_run=False, confirm_downloads=True, target=""
                ),
                self._s(),
            )

    def test_lucky_com_target(self):
        argv = build_tool_argv(
            ToolRequest(
                action="lucky",
                dry_run=False,
                confirm_downloads=True,
                target="pink floyd",
            ),
            self._s(),
        )
        assert "lucky" in argv
        assert "pink floyd" in argv

    def test_sync_favorites_dry_run(self):
        argv = build_tool_argv(
            ToolRequest(action="sync-favorites", dry_run=True),
            self._s(),
        )
        assert "sync-favorites" in argv
        assert "--dry-run" in argv

    def test_sync_favorites_com_download_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="sync-favorites",
                    dry_run=False,
                    download_new=True,
                    confirm_downloads=False,
                ),
                self._s(),
            )

    def test_watch_sem_target_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(action="watch", target="", confirm_file_changes=True),
                self._s(),
            )

    def test_watch_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="watch", target="/tmp/Music", confirm_file_changes=False
                ),
                self._s(),
            )

    def test_watch_com_target_e_confirmacao(self):
        argv = build_tool_argv(
            ToolRequest(action="watch", target="/tmp/Music", confirm_file_changes=True),
            self._s(),
        )
        assert "--watch" in argv
        assert "/tmp/Music" in argv

    def test_find_duplicates_sem_target_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(ToolRequest(action="find-duplicates", target=""), self._s())

    def test_find_duplicates_com_target(self):
        argv = build_tool_argv(
            ToolRequest(action="find-duplicates", target="/tmp/Music"),
            self._s(),
        )
        assert "--find-duplicates" in argv

    def test_scan_dry_run(self):
        argv = build_tool_argv(
            ToolRequest(action="scan", target="/tmp/Music", dry_run=True),
            self._s(),
        )
        assert "scan" in argv
        assert "--dry-run" in argv

    def test_scan_sem_confirmacao_e_sem_dry_run_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="scan",
                    target="/tmp/Music",
                    dry_run=False,
                    confirm_file_changes=False,
                ),
                self._s(),
            )

    def test_inspect_sem_target_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(ToolRequest(action="inspect", target=""), self._s())

    def test_inspect_com_target(self):
        argv = build_tool_argv(
            ToolRequest(action="inspect", target="/tmp/music/track.flac"),
            self._s(),
        )
        assert "/tmp/music/track.flac" in argv

    def test_lyrics_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(action="lyrics", confirm_file_changes=False),
                self._s(),
            )

    def test_lyrics_com_confirmacao(self):
        argv = build_tool_argv(
            ToolRequest(action="lyrics", confirm_file_changes=True),
            self._s(),
        )
        assert "lyrics" in argv

    def test_import_playlist_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="import-playlist",
                    target="https://x",
                    confirm_downloads=False,
                ),
                self._s(),
            )

    def test_sync_playlist_sem_confirmacoes_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="sync-playlist",
                    target="https://x",
                    confirm_file_changes=False,
                    confirm_downloads=False,
                ),
                self._s(),
            )

    def test_acao_desconhecida_levanta(self):
        with pytest.raises(ValueError, match="(?i)ferramenta"):
            build_tool_argv(ToolRequest(action="rm-rf"), self._s())

    def test_library_status(self):
        argv = build_tool_argv(
            ToolRequest(action="library", subaction="status"), self._s()
        )
        assert argv == ["library", "status"]

    def test_library_missing_com_limit(self):
        argv = build_tool_argv(
            ToolRequest(action="library", subaction="missing", limit=50),
            self._s(),
        )
        assert "--limit" in argv
        assert "50" in argv

    def test_library_unmark_sem_target_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="library",
                    subaction="unmark",
                    target="",
                    confirm_file_changes=True,
                ),
                self._s(),
            )

    def test_library_unmark_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(
                    action="library",
                    subaction="unmark",
                    target="abc123",
                    confirm_file_changes=False,
                ),
                self._s(),
            )

    def test_sync_db_sem_confirmacao_levanta(self):
        with pytest.raises(ValueError):
            build_tool_argv(
                ToolRequest(action="sync-db", confirm_file_changes=False),
                self._s(),
            )

    def test_sync_db_com_confirmacao(self):
        argv = build_tool_argv(
            ToolRequest(action="sync-db", confirm_file_changes=True),
            self._s(),
        )
        assert "--sync-db" in argv


# ---------------------------------------------------------------------------
# SettingsRequest — validação Pydantic
# ---------------------------------------------------------------------------


class TestSettingsRequestValidation:
    def test_quality_invalido_levanta(self, tmp_path):
        music = tmp_path / "Music"
        music.mkdir()
        with pytest.raises(ValueError):
            SettingsRequest(directory=str(music), quality=99)

    def test_quality_minimo(self, tmp_path):
        music = tmp_path / "Music"
        music.mkdir()
        sr = SettingsRequest(directory=str(music), quality=5)
        assert sr.quality == 5

    def test_embedded_art_size_invalido_levanta(self, tmp_path):
        music = tmp_path / "Music"
        music.mkdir()
        with pytest.raises(ValueError):
            SettingsRequest(directory=str(music), quality=6, embedded_art_size="9999")

    def test_embedded_art_size_validos(self, tmp_path):
        music = tmp_path / "Music"
        music.mkdir()
        for size in ("50", "100", "150", "300", "600", "max", "org"):
            sr = SettingsRequest(
                directory=str(music), quality=6, embedded_art_size=size
            )
            assert sr.embedded_art_size == size

    def test_directory_vazio_levanta(self):
        with pytest.raises(ValueError):
            SettingsRequest(directory="", quality=6)

    def test_max_workers_fora_do_range_levanta(self, tmp_path):
        music = tmp_path / "Music"
        music.mkdir()
        with pytest.raises(ValueError):
            SettingsRequest(directory=str(music), quality=6, max_workers=99)
