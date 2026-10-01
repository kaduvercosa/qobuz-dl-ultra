"""Qobuz-DL Studio: local browser interface for Qobuz-DL Ultra.

By default, the server binds only to loopback. Existing credentials are read
from the user's local config/keyring and are never returned to the browser.
"""

from __future__ import annotations

import asyncio
import re
import configparser
from contextlib import asynccontextmanager
import json
import logging
import mimetypes
import os
import secrets
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qobuz_dl.core import QobuzDL
from qobuz_dl.constants import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.downloader import _resolve_art_url
from qobuz_dl import gui_meta
from qobuz_dl.paths import ensure_directory_ready
import hashlib
import httpx
from starlette.responses import StreamingResponse
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.utils import get_config_paths

logger = logging.getLogger("qobuz_dl.webapp")
PACKAGE_DIR = Path(__file__).resolve().parent
ASSET_DIR = PACKAGE_DIR / "gui_assets"
WEB_DIR = PACKAGE_DIR / "web_ui"
AUDIO_SUFFIXES = {".flac", ".mp3", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".aiff"}
QUALITY_LABELS = {
    5: "MP3",
    6: "FLAC · CD",
    7: "Hi-Res · até 96 kHz",
    27: "Hi-Res · acima de 96 kHz",
}


class DownloadRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(track|album)$")
    title: str = Field(default="", max_length=300)
    artist: str = Field(default="", max_length=300)
    cover: str = Field(default="", max_length=600)


class SettingsRequest(BaseModel):
    directory: str = Field(min_length=1, max_length=2000)
    quality: int = Field(ge=5, le=27)
    embed_art: bool = True
    fetch_lyrics: bool = True
    lrc_files: bool = True
    credits: bool = True
    m3u: bool = False
    quality_fallback: bool = True
    playlist_as_albums: bool = False
    verify_after_download: bool = False
    no_cover: bool = False
    smart_discography: bool = False
    multi_value_tags: bool = False
    max_workers: int = Field(default=1, ge=1, le=16)
    segment_workers: int = Field(default=4, ge=2, le=16)
    embedded_art_size: str = Field(
        default="org", pattern="^(50|100|150|300|600|max|org)$"
    )
    saved_art_size: str = Field(default="org", pattern="^(50|100|150|300|600|max|org)$")
    folder_format: str = Field(default=DEFAULT_FOLDER, max_length=500)
    track_format: str = Field(default=DEFAULT_TRACK, max_length=500)


class ToolRequest(BaseModel):
    action: str = Field(min_length=1, max_length=40)
    target: str = Field(default="", max_length=2000)
    subaction: str = Field(default="", max_length=40)
    dry_run: bool = True
    limit: int = Field(default=20, ge=1, le=500)
    max_depth: int = Field(default=4, ge=1, le=20)
    every: int = Field(default=0, ge=0, le=10080)
    download_new: bool = False
    download_missing: bool = False
    confirm_downloads: bool = False
    confirm_file_changes: bool = False
    confirm_delete: bool = False
    fix: bool = False
    json_output: bool = False
    artists: bool = False


class AccountSetupRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    token: str = Field(min_length=8, max_length=2048)
    store_in_keyring: bool = True


TOOL_LABELS = {
    "doctor": "Diagnóstico do sistema",
    "stats": "Estatísticas da biblioteca",
    "library": "Catálogo local",
    "scan": "Examinar pasta de música",
    "sync-favorites": "Sincronizar favoritos",
    "sync-db": "Reconstruir banco de downloads",
    "find-duplicates": "Encontrar faixas duplicadas",
    "lyrics": "Preencher letras nos arquivos",
    "inspect": "Inspecionar áudio e tags",
    "import-playlist": "Importar playlist externa",
    "sync-playlist": "Sincronizar pasta com playlist",
    "dl": "Baixar por URL do Qobuz",
    "lucky": "Buscar e baixar automaticamente",
    "user": "Perfil e assinatura",
    "show-config": "Ver configuração",
    "purge": "Apagar banco de downloads",
    "watch": "Monitorar pasta para novas faixas",
}
TOOL_ACTIONS = set(TOOL_LABELS)


def build_tool_argv(payload: ToolRequest, settings: dict[str, Any]) -> list[str]:
    """Build a bounded allowlisted CLI argument vector; never invokes a shell."""
    action, target = payload.action, payload.target.strip()
    if action not in TOOL_ACTIONS:
        raise ValueError("Ferramenta desconhecida")
    if action == "purge":
        if not payload.confirm_delete:
            raise ValueError("Confirme explicitamente a remoção do banco de downloads.")
        return ["-p"]
    if action == "show-config":
        return ["--show-config"]
    if action == "watch":
        if not target:
            raise ValueError("Informe a pasta que será monitorada.")
        if not payload.confirm_file_changes:
            raise ValueError(
                "Confirme que o monitoramento poderá editar arquivos de áudio."
            )
        return ["--watch", os.path.expanduser(target)]
    if action == "sync-db":
        if not payload.confirm_file_changes:
            raise ValueError("Confirme a alteração do banco local antes de continuar.")
        return ["--sync-db", os.path.expanduser(target or settings["directory"])]
    if action == "find-duplicates":
        if not target:
            raise ValueError("Informe a pasta que será examinada.")
        return ["--find-duplicates", os.path.expanduser(target)]
    if action == "doctor":
        return ["doctor", "--json"]
    if action == "stats":
        return ["stats"] + (["--artistas"] if payload.artists else [])
    if action == "user":
        return ["user", "--json"]
    if action == "library":
        sub = payload.subaction or "status"
        if sub not in {
            "status",
            "missing",
            "list",
            "history",
            "reconcile",
            "reset-stuck",
            "unmark",
        }:
            raise ValueError("Ação de catálogo inválida")
        argv = ["library", sub]
        if sub in {"missing", "list", "history"}:
            argv += ["--limit", str(min(200, payload.limit))]
        if sub == "unmark":
            if not target:
                raise ValueError("Informe o ID do álbum que será desmarcado.")
            if not payload.confirm_file_changes:
                raise ValueError("Confirme a alteração do catálogo antes de continuar.")
            argv.append(target)
        if sub == "reset-stuck" and not payload.confirm_file_changes:
            raise ValueError("Confirme a alteração do catálogo antes de continuar.")
        if sub == "reconcile":
            if not payload.confirm_file_changes and not payload.dry_run:
                raise ValueError(
                    "Confirme as alterações no catálogo antes de reconciliar."
                )
            if target:
                argv.append(os.path.expanduser(target))
            if payload.dry_run:
                argv.append("--dry-run")
            if payload.fix:
                argv.append("--fix")
        return argv
    if (
        action in {"dl", "lucky"}
        and not payload.dry_run
        and not payload.confirm_downloads
    ):
        raise ValueError("Confirme que deseja iniciar downloads antes de continuar.")
    if (
        action == "sync-favorites"
        and not payload.dry_run
        and (payload.download_new or payload.download_missing)
        and not payload.confirm_downloads
    ):
        raise ValueError("Confirme os downloads de favoritos antes de continuar.")
    if action == "import-playlist" and not payload.confirm_downloads:
        raise ValueError(
            "A importação pode pesquisar e baixar faixas; confirme essa ação."
        )
    if action == "sync-playlist" and (
        not payload.confirm_file_changes or not payload.confirm_downloads
    ):
        raise ValueError(
            "Confirme os downloads e alterações de arquivos antes de sincronizar."
        )
    if action == "lyrics" and not payload.confirm_file_changes:
        raise ValueError(
            "Confirme as alterações nos arquivos locais antes de continuar."
        )
    argv: list[str] = [action]
    if action in {"dl", "sync-favorites", "lucky"}:
        argv += [
            "--directory",
            os.path.expanduser(str(settings["directory"])),
            "--quality",
            str(settings["quality"]),
            "--max-workers",
            str(settings["max_workers"]),
            "--segment-workers",
            str(settings["segment_workers"]),
        ]
    if action in {"dl", "sync-favorites"} and payload.dry_run:
        argv.append("--dry-run")
    if action == "dl":
        if not target:
            raise ValueError("Informe uma URL do Qobuz ou um arquivo com URLs.")
        argv.append(target)
        if settings.get("playlist_as_albums"):
            argv.append("--playlist-as-albums")
    elif action == "import-playlist":
        if not target:
            raise ValueError("Informe uma URL ou o caminho do arquivo da playlist.")
        argv += [target]
    elif action == "sync-playlist":
        if not target:
            raise ValueError("Informe a URL da playlist Qobuz.")
        argv += ["--yes", target]
    elif action == "lucky":
        if not target:
            raise ValueError("Informe o termo de busca.")
        argv += ["--type", "album", "--number", str(min(20, payload.limit)), target]
    elif action == "sync-favorites":
        argv += ["--limit", str(min(500, payload.limit))]
        if payload.download_new:
            argv.append("--download-new")
        if payload.download_missing:
            argv.append("--download-missing")
        if payload.confirm_downloads:
            argv.append("--yes")
        if payload.every:
            argv += ["--every", str(payload.every)]
    elif action == "scan":
        target = target or str(settings["directory"])
        if not payload.dry_run and not payload.confirm_file_changes:
            raise ValueError(
                "Confirme que deseja atualizar o catálogo com o resultado do scan."
            )
        argv += [target, "--max-depth", str(payload.max_depth), "--no-review"]
        if payload.dry_run:
            argv.append("--dry-run")
    elif action == "lyrics":
        argv.append(os.path.expanduser(target or str(settings["directory"])))
    elif action == "inspect":
        if not target:
            raise ValueError(
                "Informe o caminho de um arquivo de áudio para inspecionar."
            )
        argv.append(os.path.expanduser(target))
    return argv


class FavoriteRequest(BaseModel):
    id: str = Field(min_length=1, max_length=80)
    kind: str = Field(pattern="^(track|album)$")


class GuiService:
    def __init__(self, *, demo: bool = False):
        self.demo = demo
        self.engine: QobuzDL | None = None
        self.client = None
        self.auth_lock = asyncio.Lock()
        self.worker_task: asyncio.Task | None = None
        self.queue_lock = asyncio.Lock()
        self.queue: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.current: dict[str, Any] | None = None
        self.local_files: dict[str, Path] = {}
        self._lib_cache: dict[str, Any] = {}
        self._engine_dirty = False
        self.tool_jobs: dict[str, dict[str, Any]] = {}
        self.tool_processes: dict[str, asyncio.subprocess.Process] = {}
        self.tool_tasks: set[asyncio.Task] = set()
        self.settings_path = Path(get_config_paths()["config_path"]) / "gui.json"
        self.local_settings = self._load_local_settings()

    def _load_local_settings(self) -> dict[str, Any]:
        defaults = {
            "directory": "",
            "quality": None,
            "embed_art": True,
            "fetch_lyrics": True,
            "lrc_files": True,
            "credits": True,
            "m3u": False,
            "quality_fallback": True,
            "playlist_as_albums": False,
            "verify_after_download": False,
            "no_cover": False,
            "smart_discography": False,
            "multi_value_tags": False,
            "max_workers": 1,
            "segment_workers": 4,
            "embedded_art_size": "org",
            "saved_art_size": "org",
            "folder_format": DEFAULT_FOLDER,
            "track_format": DEFAULT_TRACK,
        }
        # config.ini é a ÚNICA fonte das configurações de download: o que a CLI
        # lê é exatamente o que a GUI mostra e usa. (O antigo gui.json paralelo
        # divergia do config.ini e fazia a GUI baixar com regras que a pessoa
        # nunca escolheu no terminal.)
        return self._settings_from_ini(defaults)

    # chave da GUI -> (chave no config.ini, invertida?)
    _INI_BOOLS = {
        "embed_art": ("embed_art", False),
        "fetch_lyrics": ("fetch_lyrics", False),
        "lrc_files": ("no_lrc_files", True),
        "credits": ("no_credits", True),
        "m3u": ("no_m3u", True),
        "quality_fallback": ("no_fallback", True),
        "playlist_as_albums": ("playlist_as_albums", False),
        "verify_after_download": ("verify_after_download", False),
        "no_cover": ("no_cover", False),
        "smart_discography": ("smart_discography", False),
        "multi_value_tags": ("multi_value_tags", False),
    }
    _INI_STRINGS = ("embedded_art_size", "saved_art_size", "folder_format", "track_format")
    _INI_INTS = ("max_workers", "segment_workers")

    def _settings_from_ini(self, defaults: dict[str, Any]) -> dict[str, Any]:
        result = dict(defaults)
        config_file = get_config_paths()["config_file"]
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(config_file, encoding="utf-8")
        except (OSError, configparser.Error):
            return result
        section = "qobuz" if parser.has_section("qobuz") else "DEFAULT"

        def raw(key):
            return parser.get(section, key, fallback=None)

        for gui_key, (ini_key, inverted) in self._INI_BOOLS.items():
            value = raw(ini_key)
            if value is None or value.strip() == "":
                continue
            flag = value.strip().lower() in ("1", "true", "yes", "on")
            result[gui_key] = (not flag) if inverted else flag
        # fetch_lyrics da GUI liga busca E incorporação (ver connect()).
        embed = raw("embed_lyrics")
        if raw("fetch_lyrics") is None and embed:
            result["fetch_lyrics"] = embed.strip().lower() in ("1", "true", "yes", "on")
        for key in self._INI_STRINGS:
            value = raw(key)
            if value and value.strip():
                result[key] = value.strip()
        for key in self._INI_INTS:
            value = raw(key)
            try:
                if value:
                    result[key] = int(value)
            except ValueError:
                pass
        result["max_workers"] = max(1, min(16, int(result["max_workers"])))
        result["segment_workers"] = max(2, min(16, int(result["segment_workers"])))
        directory = raw("directory") or raw("default_folder")
        if directory:
            result["directory"] = directory
        quality = raw("default_quality")
        try:
            if quality and int(quality) in QUALITY_LABELS:
                result["quality"] = int(quality)
        except ValueError:
            pass
        return result

    def _write_settings_to_ini(self, data: dict[str, Any]) -> None:
        """Grava as preferências da GUI no config.ini da CLI, preservando
        todas as outras chaves (contas, tags, etc.)."""
        config_file = get_config_paths()["config_file"]
        parser = configparser.ConfigParser(interpolation=None)
        parser.read(config_file, encoding="utf-8")
        if not parser.has_section("qobuz"):
            parser.add_section("qobuz")
        section = "qobuz"

        def put(key, value):
            parser.set(section, key, str(value))

        for gui_key, (ini_key, inverted) in self._INI_BOOLS.items():
            flag = bool(data[gui_key])
            put(ini_key, str((not flag) if inverted else flag).lower())
        put("embed_lyrics", str(bool(data["fetch_lyrics"])).lower())
        for key in self._INI_STRINGS:
            put(key, data[key])
        for key in self._INI_INTS:
            put(key, int(data[key]))
        put("directory", data["directory"])
        put("default_quality", int(data["quality"]))
        parent = os.path.dirname(config_file)
        os.makedirs(parent, mode=0o700, exist_ok=True)
        temporary = config_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            parser.write(handle)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, config_file)

    def _read_config(self) -> tuple[configparser.ConfigParser, str, dict[str, str]]:
        config_file = get_config_paths()["config_file"]
        parser = configparser.ConfigParser(interpolation=None)
        if not os.path.isfile(config_file):
            raise RuntimeError(
                "Ainda não há uma conta Qobuz configurada. Abra Preferências → Conectar ou configurar conta."
            )
        parser.read(config_file, encoding="utf-8")
        section = "qobuz" if parser.has_section("qobuz") else "DEFAULT"
        names = (
            "email",
            "password",
            "auth_token",
            "user_auth_token",
            "user_token",
            "app_id",
            "secrets",
            "disable_keyring",
            "directory",
            "default_folder",
            "default_quality",
        )
        return (
            parser,
            section,
            {key: parser.get(section, key, fallback="") for key in names},
        )

    def config_status(self) -> dict[str, Any]:
        try:
            _, _, values = self._read_config()
            token_present = bool(
                values["password"]
                or values["auth_token"]
                or values["user_auth_token"]
                or values["user_token"]
            )
            if not token_present and values.get(
                "disable_keyring", ""
            ).strip().lower() not in {"1", "true", "yes", "on"}:
                try:
                    import keyring

                    token_present = bool(keyring.get_password("qobuz-dl", "auth_token"))
                except Exception:
                    pass
            configured = bool(values["email"] and token_present)
        except (RuntimeError, configparser.Error, OSError):
            configured, values = False, {}
        directory = (
            self.local_settings["directory"]
            or values.get("directory")
            or values.get("default_folder")
            or str(Path.home() / "Music")
        )
        try:
            configured_quality = int(values.get("default_quality", 6))
        except (TypeError, ValueError):
            configured_quality = 6
        quality = self.local_settings["quality"] or (
            configured_quality if configured_quality in QUALITY_LABELS else 6
        )
        return {
            "demo": self.demo,
            "configured": configured and not self.demo,
            "connected": bool(self.client),
            "directory": directory,
            "quality": quality,
            "qualityLabel": QUALITY_LABELS[quality],
            "configDir": get_config_paths()["config_path"],
            "accountLabel": "Conta Qobuz" if self.client else "Desconectado",
        }

    async def connect(self) -> dict[str, Any]:
        if self.demo:
            return {
                "connected": False,
                "demo": True,
                "message": "Modo de demonstração: nenhuma conta foi acessada.",
            }
        async with self.auth_lock:
            if self.client:
                return {"connected": True, "account": "Qobuz"}
            parser, section, values = self._read_config()
            token = ""
            if values["disable_keyring"].strip().lower() not in {
                "1",
                "true",
                "yes",
                "on",
            }:
                try:
                    import keyring

                    token = keyring.get_password("qobuz-dl", "auth_token") or ""
                except Exception:
                    pass
            token = (
                token
                or values["auth_token"]
                or values["user_auth_token"]
                or values["user_token"]
            ).strip()
            settings = QobuzDLSettings.from_arguments_configparser(
                SimpleNamespace(), parser
            )
            settings.user_auth_token = token
            quality = self.local_settings["quality"] or int(settings.default_quality)
            if quality not in QUALITY_LABELS:
                quality = 6
            directory = (
                self.local_settings["directory"]
                or values["directory"]
                or values["default_folder"]
                or str(Path.home() / "Music")
            )
            settings.default_folder, settings.default_quality = directory, quality
            settings.embed_art = self.local_settings["embed_art"]
            settings.fetch_translation = self.local_settings["fetch_lyrics"]
            settings.lrc_files = self.local_settings["lrc_files"]
            settings.embed_lyrics = self.local_settings["fetch_lyrics"]
            settings.verify_after_download = self.local_settings[
                "verify_after_download"
            ]
            settings.max_workers = self.local_settings["max_workers"]
            settings.segment_workers = self.local_settings["segment_workers"]
            settings.multi_value_tags = self.local_settings["multi_value_tags"]
            settings.smart_discography = self.local_settings["smart_discography"]
            settings.embedded_art_size = self.local_settings["embedded_art_size"]
            settings.saved_art_size = self.local_settings["saved_art_size"]
            settings.folder_format = self.local_settings["folder_format"]
            settings.track_format = self.local_settings["track_format"]
            no_db = parser.getboolean(section, "no_database", fallback=False)
            self.engine = QobuzDL(
                directory=directory,
                quality=quality,
                embed_art=settings.embed_art,
                no_m3u_for_playlists=not self.local_settings["m3u"],
                quality_fallback=self.local_settings["quality_fallback"],
                cover_og_quality=settings.cover_og_quality,
                no_cover=self.local_settings["no_cover"],
                downloads_db=None if no_db else get_config_paths()["qobuz_db"],
                folder_format=settings.folder_format,
                track_format=settings.track_format,
                smart_discography=settings.smart_discography,
                fetch_lyrics=self.local_settings["fetch_lyrics"],
                no_lrc_files=not self.local_settings["lrc_files"],
                force_english=True,
                no_credits=not self.local_settings["credits"],
                playlist_as_albums=self.local_settings["playlist_as_albums"],
                settings=settings,
            )
            secret_list = [
                part.strip() for part in values["secrets"].split(",") if part.strip()
            ]
            try:
                await self.engine.initialize_client(
                    values["email"], values["password"], values["app_id"], secret_list
                )
            except Exception as exc:
                self.engine = None
                logger.warning("Qobuz connection failed (%s)", type(exc).__name__)
                raise RuntimeError(
                    "Não foi possível conectar. Confira o login e a assinatura na configuração local do Qobuz-DL."
                ) from None
            self.client = self.engine.client
            return {"connected": True, "account": "Qobuz"}

    async def configure_account(self, request: AccountSetupRequest) -> dict[str, Any]:
        if self.demo:
            raise HTTPException(
                status_code=409,
                detail="Configuração de conta desativada na prévia demonstrativa.",
            )
        email, token = request.email.strip(), request.token.strip()
        if "@" not in email or any(ch.isspace() for ch in email):
            raise HTTPException(
                status_code=400, detail="Informe um endereço de e-mail válido."
            )
        from qobuz_dl.bundle import Bundle
        from qobuz_dl.qopy import Client

        try:
            bundle = await Bundle.create()
            app_id = str(bundle.get_app_id())
            secrets_list = [
                str(value) for value in bundle.get_secrets().values() if value
            ]
            if not app_id or not secrets_list:
                raise RuntimeError("As chaves de API não puderam ser obtidas.")
            verifier = await Client.create(
                email,
                "",
                app_id,
                secrets_list,
                user_auth_token=token,
                force_english=True,
            )
            try:
                await verifier.get_user_profile()
            finally:
                await verifier.close()
        except Exception as exc:
            logger.warning("Qobuz account setup failed (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=400,
                detail="Não foi possível validar e-mail/token com o Qobuz. As credenciais não foram salvas.",
            ) from None

        config_file = get_config_paths()["config_file"]
        parser = configparser.ConfigParser(interpolation=None)
        if os.path.isfile(config_file):
            parser.read(config_file, encoding="utf-8")
        if not parser.has_section("qobuz"):
            parser.add_section("qobuz")
        section = "qobuz"
        parser.set(section, "email", email)
        parser.set(section, "password", "")
        parser.set(section, "app_id", app_id)
        parser.set(section, "secrets", ",".join(secrets_list))
        parser.set(
            section,
            "directory",
            self.local_settings["directory"] or str(Path.home() / "Music"),
        )
        parser.set(section, "default_quality", str(self.local_settings["quality"] or 6))
        stored = False
        if request.store_in_keyring:
            try:
                import keyring

                keyring.set_password("qobuz-dl", "auth_token", token)
                stored = True
            except Exception:
                raise HTTPException(
                    status_code=400,
                    detail="O cofre de senhas do sistema não está disponível. Ative a opção de salvar o token protegido ou desmarque-a para usar config.ini.",
                ) from None
        parser.set(section, "disable_keyring", "false" if stored else "true")
        parser.set(section, "auth_token", "" if stored else token)
        parser.set(section, "user_auth_token", "" if stored else token)
        parser.set(section, "user_token", "" if stored else token)
        parent = os.path.dirname(os.path.abspath(config_file))
        os.makedirs(parent, mode=0o700, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=".config.", dir=parent, text=True)
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                parser.write(handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, config_file)
            if os.name == "posix":
                os.chmod(config_file, 0o600)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
        return {
            "configured": True,
            "connected": False,
            "email": email,
            "storedInKeyring": stored,
        }

    async def ensure_connected(self):
        if self.demo:
            return None
        if not self.client:
            # Fallback preguiçoso: se o autoconnect do startup (ver
            # lifespan() em create_app) não rolou por algum motivo
            # transitório, tenta de novo aqui usando config.ini/keyring
            # antes de desistir e pedir pra pessoa conectar manualmente.
            try:
                await self.connect()
            except (RuntimeError, HTTPException):
                pass
        if not self.client:
            raise HTTPException(
                status_code=409,
                detail="Conecte sua conta Qobuz para acessar o catálogo.",
            )
        return self.client

    def save_settings(self, request: SettingsRequest, write: bool = True) -> dict[str, Any]:
        directory, quality = request.directory, request.quality
        if quality not in QUALITY_LABELS:
            raise ValueError("Qualidade inválida")
        # Testa (e cria, se preciso) a pasta de verdade antes de gravar
        # qualquer coisa -- ver qobuz_dl/paths.py.
        expanded = ensure_directory_ready(directory)
        self.local_settings = request.model_dump()
        self.local_settings["directory"] = str(expanded)
        if write:
            self._write_settings_to_ini(self.local_settings)
        self._apply_to_engine()
        self.invalidate_library()
        return self.settings_payload()

    def _apply_to_engine(self) -> None:
        if not self.engine:
            return
        ls = self.local_settings
        self.engine.directory, self.engine.quality = ls["directory"], ls["quality"]
        (
            self.engine.settings.default_folder,
            self.engine.settings.default_quality,
        ) = ls["directory"], ls["quality"]
        self.engine.embed_art = ls["embed_art"]
        self.engine.fetch_lyrics = ls["fetch_lyrics"]
        self.engine.no_lrc_files = not ls["lrc_files"]
        self.engine.no_credits = not ls["credits"]
        self.engine.no_m3u_for_playlists = not ls["m3u"]
        self.engine.quality_fallback = ls["quality_fallback"]
        self.engine.playlist_as_albums = ls["playlist_as_albums"]
        self.engine.settings.verify_after_download = ls["verify_after_download"]
        self.engine.settings.max_workers = ls["max_workers"]
        self.engine.settings.segment_workers = ls["segment_workers"]
        self.engine.settings.multi_value_tags = ls["multi_value_tags"]
        self.engine.settings.smart_discography = ls["smart_discography"]
        self.engine.settings.embedded_art_size = ls["embedded_art_size"]
        self.engine.settings.saved_art_size = ls["saved_art_size"]
        self.engine.no_cover = ls["no_cover"]
        self.engine.folder_format = ls["folder_format"]
        self.engine.track_format = ls["track_format"]

    def settings_payload(self) -> dict[str, Any]:
        result = dict(self.local_settings)
        status = self.config_status()
        result["directory"] = result["directory"] or status["directory"]
        result["quality"] = result["quality"] or status["quality"]
        result["configDir"] = status["configDir"]
        result["configFile"] = get_config_paths()["config_file"]
        return result

    def reload_settings(self) -> None:
        """Relê o config.ini (alterações feitas no terminal aparecem na GUI)."""
        if self.current:
            return
        self.local_settings = self._settings_from_ini(self.local_settings)
        self._apply_to_engine()

    def _ini_parser(self) -> configparser.ConfigParser:
        parser = configparser.ConfigParser(interpolation=None)
        try:
            parser.read(get_config_paths()["config_file"], encoding="utf-8")
        except (OSError, configparser.Error):
            pass
        return parser

    def advanced_payload(self) -> dict[str, Any]:
        parser = self._ini_parser()
        section = "qobuz" if parser.has_section("qobuz") else "DEFAULT"
        return {
            "schema": gui_meta.ADVANCED_SCHEMA,
            "values": gui_meta.read_advanced(parser, section),
            "configFile": get_config_paths()["config_file"],
        }

    async def save_advanced(self, values: dict[str, Any]) -> dict[str, Any]:
        parser = self._ini_parser()
        gui_meta.write_advanced(parser, "qobuz", values)
        config_file = get_config_paths()["config_file"]
        os.makedirs(os.path.dirname(config_file), mode=0o700, exist_ok=True)
        temporary = config_file + ".tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            parser.write(handle)
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, config_file)
        await self.refresh_engine()
        return self.advanced_payload()

    async def refresh_engine(self) -> None:
        """Recria o motor de download a partir do config.ini atual (opções
        avançadas só entram em vigor assim). Adia se houver download ativo."""
        if self.demo or not self.client:
            return
        if self.current:
            self._engine_dirty = True
            return
        self._engine_dirty = False
        self.engine = None
        try:
            await self.connect()
        except Exception:
            logger.exception("Could not refresh engine")

    async def enqueue(self, request: DownloadRequest) -> dict[str, Any]:
        if self.demo:
            raise HTTPException(
                status_code=409,
                detail="Downloads ficam desativados no modo de demonstração.",
            )
        await self.ensure_connected()
        entry = {
            "id": secrets.token_urlsafe(12),
            "itemId": request.id,
            "kind": request.kind,
            "title": request.title or "Item sem título",
            "artist": request.artist or "",
            "status": "aguardando",
            "createdAt": time.time(),
            "message": "Na fila",
            "cover": request.cover if request.cover.startswith("https://") else None,
        }
        async with self.queue_lock:
            self.queue.append(entry)
            if not self.worker_task or self.worker_task.done():
                self.worker_task = asyncio.create_task(self._run_queue())
        return dict(entry)

    async def _run_queue(self):
        while True:
            async with self.queue_lock:
                if not self.queue:
                    self.current = None
                    return
                item = self.queue.pop(0)
                self.current = item
                item["status"], item["message"] = (
                    "baixando",
                    "O downloader está processando este item",
                )
            started_at = time.time()
            try:
                if self._engine_dirty:
                    self.current = None
                    await self.refresh_engine()
                    self.current = item
                self.reload_settings_unlocked()
                self.engine.directory = (
                    self.local_settings["directory"] or self.engine.directory
                )
                self.engine.quality = (
                    self.local_settings["quality"]
                    or self.engine.settings.default_quality
                )
                (
                    self.engine.settings.default_folder,
                    self.engine.settings.default_quality,
                ) = self.engine.directory, self.engine.quality
                success = await self.engine.download_from_id(
                    item["itemId"], album=item["kind"] == "album"
                )
                item["status"] = "concluído" if success else "falhou"
                item["message"] = (
                    "Download concluído"
                    if success
                    else "O downloader não concluiu este item; consulte o log local."
                )
                await self._verify_download(item, started_at, bool(success))
            except asyncio.CancelledError:
                item["status"], item["message"] = (
                    "interrompido",
                    "Download interrompido",
                )
                raise
            except Exception as exc:
                logger.exception("Queue download failed")
                item["status"], item["message"] = (
                    "falhou",
                    f"Falha no download ({type(exc).__name__})",
                )
            finally:
                item["finishedAt"] = time.time()
                async with self.queue_lock:
                    if self.current and self.current["id"] == item["id"]:
                        self.current = None
                    self.history.append(item)
                    self.history = self.history[-80:]

    def reload_settings_unlocked(self) -> None:
        self.local_settings = self._settings_from_ini(self.local_settings)
        self._apply_to_engine()

    def library(self, force: bool = False) -> list[dict[str, Any]]:
        root = (
            Path(self.local_settings["directory"] or self.config_status()["directory"])
            .expanduser()
            .resolve()
        )
        cache = self._lib_cache
        if (
            not force
            and cache.get("root") == str(root)
            and time.time() - cache.get("at", 0) < 45
        ):
            return cache["rows"]
        if not root.is_dir():
            self.local_files = {}
            self._lib_cache = {}
            return []
        try:
            files = [
                p
                for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
            ]
            files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        rows, mapping = [], {}
        for path in files[:3000]:
            row = self._audio_row(path, root)
            if row:
                rows.append(row)
                mapping[row["key"]] = path.resolve()
        self.local_files = mapping
        self._lib_cache = {"root": str(root), "at": time.time(), "rows": rows}
        return rows

    def invalidate_library(self) -> None:
        self._lib_cache = {}

    async def local_path(self, key: str) -> Path | None:
        path = self.local_files.get(key)
        if path is None:
            await asyncio.to_thread(self.library, True)
            path = self.local_files.get(key)
        return path if path and path.is_file() else None

    def _audio_row(self, path: Path, root: Path, check_cover: bool = False) -> dict[str, Any] | None:
        try:
            from mutagen import File as AudioFile
        except ImportError:
            return None
        try:
            stat = path.stat()
            audio = AudioFile(path, easy=True)
        except Exception:
            audio, stat = None, None
        if stat is None:
            try:
                stat = path.stat()
            except OSError:
                return None
        tags = getattr(audio, "tags", None) or {}
        info = getattr(audio, "info", None)

        def tag(*names: str) -> str:
            for name in names:
                value = tags.get(name) if hasattr(tags, "get") else None
                if value:
                    return ", ".join(str(v) for v in value) if isinstance(value, (list, tuple)) else str(value)
            return ""

        def number(text: str) -> int | None:
            match = re.match(r"\s*(\d+)", text or "")
            return int(match.group(1)) if match else None

        resolved = path.resolve()
        try:
            relative = str(resolved.relative_to(root))
        except ValueError:
            relative = path.name
        artist = tag("artist") or tag("albumartist") or "Artista desconhecido"
        rate = getattr(info, "sample_rate", 0) or 0
        row = {
            "key": hashlib.sha1(str(resolved).encode()).hexdigest()[:20],
            "title": tag("title") or path.stem,
            "artist": artist,
            "artists": [{"name": n.strip()} for n in re.split(r"\s*[,;]\s*", artist) if n.strip()],
            "album": tag("album"),
            "albumArtist": tag("albumartist"),
            "year": (tag("date", "originaldate") or "")[:4],
            "genre": tag("genre"),
            "trackNumber": number(tag("tracknumber")),
            "discNumber": number(tag("discnumber")),
            "composer": tag("composer"),
            "label": tag("organization", "label"),
            "isrc": tag("isrc"),
            "copyright": tag("copyright"),
            "duration": int(getattr(info, "length", 0) or 0),
            "format": path.suffix.lower().lstrip("."),
            "bitDepth": getattr(info, "bits_per_sample", None) or None,
            "sampleRate": round(rate / 1000, 1) if rate else None,
            "bitrate": int((getattr(info, "bitrate", 0) or 0) / 1000) or None,
            "channels": getattr(info, "channels", None),
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "path": relative,
            "folder": str(Path(relative).parent) if Path(relative).parent != Path(".") else "",
            "pathLabel": path.parent.name,
            "hasLrc": path.with_suffix(".lrc").is_file(),
        }
        row["cover"] = f"/api/library/cover/{row['key']}"
        row["id"] = row["key"]
        if check_cover:
            row["hasCover"] = self._has_cover(path)
        return row

    @staticmethod
    def _has_cover(path: Path) -> bool:
        try:
            from mutagen import File as AudioFile

            audio = AudioFile(path)
            if getattr(audio, "pictures", None):
                return True
            tags = getattr(audio, "tags", None)
            if tags is None:
                return False
            if hasattr(tags, "getall") and tags.getall("APIC"):
                return True
            return bool(tags.get("covr")) if hasattr(tags, "get") else False
        except Exception:
            return False

    def _scan_new_files(self, root: Path, since: float) -> list[dict[str, Any]]:
        found = []
        for dirpath, _dirs, names in os.walk(root):
            for name in names:
                if Path(name).suffix.lower() not in AUDIO_SUFFIXES:
                    continue
                candidate = Path(dirpath) / name
                try:
                    if candidate.stat().st_mtime >= since - 2:
                        found.append(candidate)
                except OSError:
                    continue
            if len(found) > 300:
                break
        rows = []
        for path in found[:120]:
            row = self._audio_row(path, root, check_cover=True)
            if row:
                row["tagsOk"] = bool(row["title"] and row["artist"] != "Artista desconhecido" and row["album"])
                row["ok"] = bool(row["size"] > 1000 and row["duration"] > 1)
                rows.append(row)
        return rows

    async def _verify_download(self, item: dict[str, Any], since: float, success: bool) -> None:
        """Confere no disco o que o downloader realmente gravou."""
        root = Path(self.local_settings["directory"]).expanduser().resolve()
        try:
            rows = await asyncio.to_thread(self._scan_new_files, root, since)
        except Exception:
            logger.exception("Could not verify download")
            return
        self.invalidate_library()
        warnings = []
        for row in rows:
            if not row["ok"]:
                warnings.append(f"{row['path']}: arquivo vazio ou ilegível")
            elif not row["tagsOk"]:
                warnings.append(f"{row['path']}: tags incompletas")
            elif not row["hasCover"] and not self.local_settings["no_cover"]:
                warnings.append(f"{row['path']}: sem capa embutida")
        item["savedTo"] = str(root)
        if rows:
            parents = {str((root / r["path"]).parent) for r in rows}
            item["savedTo"] = parents.pop() if len(parents) == 1 else str(root)
        item["files"] = [
            {k: r.get(k) for k in ("name", "key", "path", "format", "size", "duration", "bitDepth", "sampleRate", "bitrate", "hasCover", "hasLrc", "tagsOk", "ok", "title")}
            | {"name": Path(r["path"]).name}
            for r in rows
        ]
        item["warnings"] = warnings[:20]
        item["verified"] = bool(rows) and all(r["ok"] and r["tagsOk"] for r in rows)
        if success and not rows:
            item["status"] = "ignorado"
            item["message"] = "Nenhum arquivo novo foi gravado: já existia na pasta ou foi pulado pelo downloader."
        elif rows:
            item["message"] = f"{len(rows)} arquivo(s) gravado(s) e conferido(s) no disco."

    async def start_tool(self, payload: ToolRequest) -> dict[str, Any]:
        if self.demo:
            raise HTTPException(
                status_code=409,
                detail="Ferramentas do programa real são desativadas na prévia demonstrativa.",
            )
        if (
            sum(1 for proc in self.tool_processes.values() if proc.returncode is None)
            >= 3
        ):
            raise HTTPException(
                status_code=429, detail="Limite de três processos simultâneos atingido."
            )
        try:
            argv = build_tool_argv(
                payload,
                self.local_settings
                | {
                    "directory": self.config_status()["directory"],
                    "quality": self.config_status()["quality"],
                },
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        account_actions = {
            "sync-favorites",
            "lyrics",
            "import-playlist",
            "sync-playlist",
            "dl",
            "lucky",
            "user",
            "watch",
        }
        if payload.action in account_actions:
            await self.connect()
        job_id = secrets.token_urlsafe(12)
        job = {
            "id": job_id,
            "action": payload.action,
            "label": TOOL_LABELS[payload.action],
            "status": "running",
            "output": "",
            "startedAt": time.time(),
            "returncode": None,
        }
        self.tool_jobs[job_id] = job
        argv = ["--no-color", *argv]
        try:
            process = await asyncio.create_subprocess_exec(
                os.sys.executable,
                "-m",
                "qobuz_dl",
                *argv,
                cwd=str(Path.home()),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
        except OSError as exc:
            self.tool_jobs.pop(job_id, None)
            raise HTTPException(
                status_code=500,
                detail=f"Não foi possível iniciar o comando ({type(exc).__name__}).",
            ) from None
        self.tool_processes[job_id] = process
        task = asyncio.create_task(self._collect_tool_output(job_id, process))
        self.tool_tasks.add(task)
        task.add_done_callback(self.tool_tasks.discard)
        self.tool_jobs = dict(list(self.tool_jobs.items())[-40:])
        return {
            key: job[key] for key in ("id", "action", "label", "status", "startedAt")
        }

    async def _collect_tool_output(
        self, job_id: str, process: asyncio.subprocess.Process
    ) -> None:
        job = self.tool_jobs[job_id]
        try:
            if process.stdout:
                while line := await process.stdout.readline():
                    job["output"] = (
                        job["output"] + line.decode("utf-8", errors="replace")
                    )[-40000:]
            job["returncode"] = await asyncio.wait_for(process.wait(), timeout=86400)
            if job.pop("stopRequested", False) or job["returncode"] < 0:
                job["status"] = "interrompido"
            else:
                job["status"] = "concluído" if job["returncode"] == 0 else "falhou"
        except asyncio.TimeoutError:
            process.terminate()
            job["status"] = "tempo esgotado"
            job["output"] = (job["output"] + "\nProcesso encerrado após 24 horas.\n")[
                -40000:
            ]
        except asyncio.CancelledError:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=4)
                except asyncio.TimeoutError:
                    process.kill()
            job["status"] = "interrompido"
            raise
        finally:
            self.tool_processes.pop(job_id, None)
            job["finishedAt"] = time.time()

    async def stop_tool(self, job_id: str) -> bool:
        process = self.tool_processes.get(job_id)
        if not process or process.returncode is not None:
            return False
        self.tool_jobs[job_id]["stopRequested"] = True
        self.tool_jobs[job_id]["status"] = "parando"
        if os.name != "nt":
            try:
                os.killpg(process.pid, 15)
            except (OSError, ProcessLookupError):
                process.terminate()
        else:
            process.terminate()
        return True


DEMO_ALBUMS = [
    {
        "id": "demo-album-1",
        "title": "Blue Hour",
        "artist": "Mira Sol",
        "year": "2025",
        "genre": "Alternative",
        "tracks_count": 11,
        "quality": "24b/96kHz",
        "cover": "/assets/cover-nebula.jpg",
        "duration": "42 min",
    },
    {
        "id": "demo-album-2",
        "title": "Quiet Geometry",
        "artist": "Aster Vale",
        "year": "2024",
        "genre": "Modern Classical",
        "tracks_count": 9,
        "quality": "24b/88.2kHz",
        "cover": "/assets/cover-lilac.jpg",
        "duration": "37 min",
    },
    {
        "id": "demo-album-3",
        "title": "Afterglow Studies",
        "artist": "The North Lines",
        "year": "2025",
        "genre": "Indie",
        "tracks_count": 10,
        "quality": "24b/96kHz",
        "cover": "/assets/cover-amber.jpg",
        "duration": "39 min",
    },
    {
        "id": "demo-album-4",
        "title": "Fern & Velvet",
        "artist": "Lena Mor",
        "year": "2023",
        "genre": "Jazz",
        "tracks_count": 12,
        "quality": "16b/44.1kHz",
        "cover": "/assets/cover-forest.jpg",
        "duration": "48 min",
    },
]
DEMO_TRACKS = [
    {
        "id": "demo-track-1",
        "title": "The Blue Between",
        "artist": "Mira Sol",
        "album": "Blue Hour",
        "duration": 224,
        "cover": "/assets/cover-nebula.jpg",
        "quality": "24b/96kHz",
        "isrc": "",
    },
    {
        "id": "demo-track-2",
        "title": "A Map of Quiet",
        "artist": "Aster Vale",
        "album": "Quiet Geometry",
        "duration": 198,
        "cover": "/assets/cover-lilac.jpg",
        "quality": "24b/88.2kHz",
        "isrc": "",
    },
    {
        "id": "demo-track-3",
        "title": "Copper Sun",
        "artist": "The North Lines",
        "album": "Afterglow Studies",
        "duration": 241,
        "cover": "/assets/cover-amber.jpg",
        "quality": "24b/96kHz",
        "isrc": "",
    },
    {
        "id": "demo-track-4",
        "title": "Mosslight",
        "artist": "Lena Mor",
        "album": "Fern & Velvet",
        "duration": 213,
        "cover": "/assets/cover-forest.jpg",
        "quality": "16b/44.1kHz",
        "isrc": "",
    },
    {
        "id": "demo-track-5",
        "title": "Last Train Home",
        "artist": "Mira Sol",
        "album": "Blue Hour",
        "duration": 255,
        "cover": "/assets/cover-nebula.jpg",
        "quality": "24b/96kHz",
        "isrc": "",
    },
]


def _cover(item: dict[str, Any]) -> str | None:
    return gui_meta.cover_pair(item)[0]


def _track_result(item: dict[str, Any]) -> dict[str, Any]:
    return gui_meta.track_view(item)


def _album_result(item: dict[str, Any]) -> dict[str, Any]:
    return gui_meta.album_view(item)


_AUDIO_TYPES = {
    ".flac": "audio/flac", ".mp3": "audio/mpeg", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
    ".aac": "audio/aac", ".ogg": "audio/ogg", ".opus": "audio/ogg", ".wav": "audio/wav",
    ".aiff": "audio/aiff", ".aif": "audio/aiff", ".wv": "audio/x-wavpack",
}


def _ranged_file(path: Path, request: Request):
    """Serve o arquivo com suporte completo a Range (206). Safari e o iOS só
    tocam áudio se o servidor aceitar pedidos por faixa de bytes -- sem isso
    o player trata como transmissão: sem duração, sem avanço livre e só com
    os botões de +-10 s."""
    size = path.stat().st_size
    start, end, status = 0, size - 1, 200
    header = request.headers.get("range", "")
    if header.startswith("bytes="):
        try:
            first, _, last = header[6:].split(",")[0].partition("-")
            if first == "":
                start = max(0, size - int(last))
            else:
                start = int(first)
                end = int(last) if last else size - 1
            end = min(end, size - 1)
            if start > end or start >= size:
                return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
            status = 206
        except ValueError:
            start, end, status = 0, size - 1, 200
    length = end - start + 1

    def body():
        with open(path, "rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining > 0:
                chunk = handle.read(min(262144, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {"Accept-Ranges": "bytes", "Content-Length": str(length), "Cache-Control": "private, max-age=3600"}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(
        body(),
        status_code=status,
        media_type=_AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        headers=headers,
    )


def create_app(*, demo: bool = False, allow_network: bool = False) -> FastAPI:
    service = GuiService(demo=demo)

    @asynccontextmanager
    async def lifespan(_app):
        if not service.demo:
            # Tenta autenticar de cara usando o que já estiver no
            # config.ini/keyring (a mesma fonte que a CLI usa) -- assim
            # quem já rodou `qobuz-dl -r` antes não precisa "logar de
            # novo" clicando em Conectar na GUI. Falha aqui é normal
            # (config ainda não existe, token expirou etc.); a pessoa
            # configura pela tela de Preferências nesse caso, e
            # ensure_connected() tenta de novo puxo a puxo mais abaixo.
            try:
                await service.connect()
            except Exception:
                pass
        yield
        for job_id in list(service.tool_processes):
            await service.stop_tool(job_id)
        if service.tool_tasks:
            await asyncio.gather(*list(service.tool_tasks), return_exceptions=True)
        if service.worker_task and not service.worker_task.done():
            service.worker_task.cancel()
            try:
                await service.worker_task
            except asyncio.CancelledError:
                pass
        if service.client:
            await service.client.close()

    app = FastAPI(
        title="Qobuz-DL Studio · Local",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.service = service
    app.mount("/assets", StaticFiles(directory=str(ASSET_DIR)), name="assets")

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        loopback = {"localhost", "127.0.0.1", "::1"}
        host = (urlparse("//" + request.headers.get("host", "")).hostname or "").lower()
        # Com allow_network (servidor ligado em 0.0.0.0/IP da rede) qualquer
        # Host é aceito; sem ele, só loopback (proteção contra DNS rebinding).
        if not demo and not allow_network and host not in loopback:
            return Response(
                "A interface aceita apenas conexões locais.", status_code=403
            )
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        # Proteção CSRF: o Origin precisa ser o mesmo host que foi acessado
        # (em rede) ou loopback (local). Outro site nunca passa.
        allowed_origins = {host} if allow_network else loopback
        if not demo and (
            (origin and urlparse(origin).hostname not in allowed_origins)
            or site in {"cross-site", "same-site"}
        ):
            return Response("Origem não permitida.", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: data:; media-src 'self' https:; img-src 'self' https: data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    @app.get("/")
    async def index():
        return FileResponse(WEB_DIR / "index.html")

    @app.get("/app.js")
    async def javascript():
        return FileResponse(WEB_DIR / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    async def stylesheet():
        return FileResponse(WEB_DIR / "app.css", media_type="text/css")

    @app.get("/manifest.webmanifest")
    async def web_manifest():
        return FileResponse(
            WEB_DIR / "manifest.webmanifest", media_type="application/manifest+json"
        )

    @app.get("/sw.js")
    async def service_worker():
        # Na raiz para o service worker controlar o app inteiro.
        return FileResponse(
            WEB_DIR / "sw.js",
            media_type="text/javascript",
            headers={"Service-Worker-Allowed": "/"},
        )

    @app.get("/api/status")
    async def status():
        return service.config_status()

    @app.get("/api/settings")
    async def get_settings():
        service.reload_settings()
        return service.settings_payload()

    @app.get("/api/settings/advanced")
    async def get_advanced():
        return service.advanced_payload()

    @app.post("/api/settings/advanced")
    async def post_advanced(payload: dict[str, Any]):
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            raise HTTPException(status_code=400, detail="Formato inválido")
        try:
            return await service.save_advanced(values)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/connect")
    async def connect():
        try:
            return await service.connect()
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.post("/api/account/configure")
    async def configure_account(payload: AccountSetupRequest):
        return await service.configure_account(payload)

    @app.get("/api/search")
    async def search(q: str, kind: str = "all", limit: int = 24):
        empty = {"tracks": [], "albums": [], "playlists": [], "artists": []}
        if len(q.strip()) < 2:
            return empty
        limit = max(1, min(limit, 50))
        if service.demo:
            query = q.casefold()
            return {
                **empty,
                "tracks": [t for t in DEMO_TRACKS if query in f"{t['title']} {t['artist']} {t['album']}".casefold()][:limit] if kind in {"tracks", "all"} else [],
                "albums": [a for a in DEMO_ALBUMS if query in f"{a['title']} {a['artist']} {a['genre']}".casefold()][:limit] if kind in {"albums", "all"} else [],
            }
        client = await service.ensure_connected()
        data: dict[str, list[dict[str, Any]]] = dict(empty)

        async def grab(method: str, key: str, view):
            try:
                raw = await getattr(client, method)(q.strip(), limit=limit)
                items = raw.get(key, {}).get("items", []) if isinstance(raw, dict) else []
                return [view(x) for x in items if isinstance(x, dict)]
            except Exception:
                logger.exception("Catalog search failed (%s)", method)
                return []

        plan = []
        if kind in {"tracks", "all"}:
            plan.append(("tracks", "search_tracks", "tracks", gui_meta.track_view))
        if kind in {"albums", "all"}:
            plan.append(("albums", "search_albums", "albums", gui_meta.album_view))
        if kind in {"playlists", "all"}:
            plan.append(("playlists", "search_playlists", "playlists", gui_meta.playlist_view))
        if kind in {"artists", "all"}:
            plan.append(("artists", "search_artists", "artists", gui_meta.artist_view))
        results = await asyncio.gather(*(grab(m, k, v) for _, m, k, v in plan))
        for (name, *_), rows in zip(plan, results):
            data[name] = rows
        return data

    @app.get("/api/album/{album_id}")
    async def album(album_id: str):
        if service.demo:
            match = next((a for a in DEMO_ALBUMS if a["id"] == album_id), None)
            if not match:
                raise HTTPException(status_code=404, detail="Álbum não encontrado")
            return {"album": match, "tracks": [t for t in DEMO_TRACKS if t["album"] == match["title"]] or DEMO_TRACKS[:5], "credits": [], "descriptionHtml": ""}
        client = await service.ensure_connected()
        try:
            raw = await client.get_album_meta(album_id)
        except Exception:
            logger.exception("Album lookup failed")
            raise HTTPException(status_code=502, detail="Não foi possível carregar este álbum.") from None
        if not isinstance(raw, dict):
            raise HTTPException(status_code=404, detail="Álbum não encontrado")
        items = [x for x in (raw.get("tracks", {}) or {}).get("items", []) if isinstance(x, dict)]
        tracks = [gui_meta.track_view(x, raw) for x in items]
        return {
            "album": gui_meta.album_view(raw),
            "tracks": tracks,
            "credits": gui_meta.merge_credits([t["credits"] for t in tracks]),
            "descriptionHtml": gui_meta.clean_html(str(raw.get("description") or "")),
        }

    @app.get("/api/track/{track_id}")
    async def track(track_id: str):
        client = await service.ensure_connected()
        if client is None:
            raise HTTPException(status_code=404, detail="Faixa indisponível na demonstração")
        try:
            raw = await client.get_track_meta(track_id)
        except Exception:
            raise HTTPException(status_code=502, detail="Não foi possível carregar esta faixa.") from None
        if not isinstance(raw, dict):
            raise HTTPException(status_code=404, detail="Faixa não encontrada")
        album_raw = raw.get("album") if isinstance(raw.get("album"), dict) else {}
        return {"track": gui_meta.track_view(raw, album_raw), "album": gui_meta.album_view(album_raw) if album_raw else None}

    @app.get("/api/artist/{artist_id}")
    async def artist(artist_id: str):
        client = await service.ensure_connected()
        if client is None:
            raise HTTPException(status_code=404, detail="Artista indisponível na demonstração")
        try:
            raw = await client.api_call("artist/get", id=artist_id, offset=0, limit=50, type=None)
        except Exception:
            raise HTTPException(status_code=502, detail="Não foi possível carregar este artista.") from None
        if not isinstance(raw, dict):
            raise HTTPException(status_code=404, detail="Artista não encontrado")
        albums = [gui_meta.album_view(x) for x in (raw.get("albums", {}) or {}).get("items", []) if isinstance(x, dict)]
        info = gui_meta.artist_view(raw)
        info["biographyHtml"] = gui_meta.clean_html(info.pop("biography", ""))
        return {"artist": info, "albums": albums}

    @app.get("/api/playlists")
    async def playlists():
        client = await service.ensure_connected()
        if client is None:
            return {"items": []}
        try:
            raw = await client.get_user_playlists(limit=100)
        except Exception:
            logger.exception("Playlist listing failed")
            raise HTTPException(status_code=502, detail="Não foi possível carregar suas playlists.") from None
        if isinstance(raw, dict):
            raw = (raw.get("playlists", {}) or {}).get("items", []) or raw.get("items", [])
        return {"items": [gui_meta.playlist_view(x) for x in (raw or []) if isinstance(x, dict)]}

    @app.get("/api/playlist/{playlist_id}")
    async def playlist(playlist_id: str):
        client = await service.ensure_connected()
        if client is None:
            raise HTTPException(status_code=404, detail="Playlist indisponível na demonstração")
        items, head, offset = [], None, 0
        try:
            while offset < 500:
                raw = await client.api_call("playlist/get", id=playlist_id, offset=offset, limit=100, type=None)
                if not isinstance(raw, dict):
                    break
                head = head or raw
                page = [x for x in (raw.get("tracks", {}) or {}).get("items", []) if isinstance(x, dict)]
                if not page:
                    break
                items += page
                offset += len(page)
                if offset >= int(raw.get("tracks_count") or 0):
                    break
        except Exception:
            logger.exception("Playlist lookup failed")
            if not items:
                raise HTTPException(status_code=502, detail="Não foi possível carregar esta playlist.") from None
        if not head:
            raise HTTPException(status_code=404, detail="Playlist não encontrada")
        return {"playlist": gui_meta.playlist_view(head), "tracks": [gui_meta.track_view(x) for x in items], "credits": gui_meta.merge_credits([gui_meta.credits_of(x) for x in items])}

    @app.get("/api/favorites")
    async def favorites(kind: str = "albums", limit: int = 40):
        if kind not in {"albums", "tracks"}:
            raise HTTPException(status_code=400, detail="Tipo de favoritos inválido")
        limit = max(1, min(limit, 100))
        if service.demo:
            return {
                "items": DEMO_ALBUMS[:limit]
                if kind == "albums"
                else DEMO_TRACKS[:limit]
            }
        client = await service.ensure_connected()
        try:
            result = await client.get_favorites(fav_type=kind, limit=limit, offset=0)
            items = (
                result.get(kind, {}).get("items", [])
                if isinstance(result, dict)
                else []
            )
            return {
                "items": [
                    _album_result(x) if kind == "albums" else _track_result(x)
                    for x in items
                    if isinstance(x, dict)
                ]
            }
        except Exception:
            raise HTTPException(
                status_code=502, detail="Não foi possível carregar os favoritos."
            ) from None

    @app.post("/api/favorites")
    async def add_favorite(payload: FavoriteRequest):
        if service.demo:
            raise HTTPException(
                status_code=409, detail="Favoritos ficam desativados na demonstração."
            )
        client = await service.ensure_connected()
        try:
            return {
                "success": True,
                "result": await client.add_favorite(payload.id, payload.kind),
            }
        except Exception:
            logger.exception("Adding Qobuz favorite failed")
            raise HTTPException(
                status_code=502, detail="Não foi possível atualizar os favoritos."
            ) from None

    @app.get("/api/library")
    async def library(refresh: bool = False):
        rows = await asyncio.to_thread(service.library, refresh)
        return {
            "directory": str(
                Path(service.local_settings["directory"] or service.config_status()["directory"]).expanduser()
            ),
            "items": rows,
            "total": len(rows),
        }

    @app.get("/api/library/play/{file_key}")
    async def local_audio(file_key: str, request: Request):
        path = await service.local_path(file_key)
        if not path:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado (a biblioteca mudou; recarregue a página)")
        return _ranged_file(path, request)

    @app.get("/api/library/cover/{file_key}")
    async def local_cover(file_key: str):
        """Capa embutida no arquivo de áudio (APIC/PICTURE/covr) ou cover.jpg ao lado."""
        path = await service.local_path(file_key)
        if not path:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        data, mime = b"", "image/jpeg"
        try:
            from mutagen import File as AudioFile

            audio = AudioFile(path)
            tags = getattr(audio, "tags", None)
            if hasattr(audio, "pictures") and audio.pictures:
                pic = audio.pictures[0]
                data, mime = pic.data, pic.mime or mime
            elif tags is not None and hasattr(tags, "getall") and tags.getall("APIC"):
                pic = tags.getall("APIC")[0]
                data, mime = pic.data, pic.mime or mime
            elif tags is not None and "covr" in tags and tags["covr"]:
                pic = tags["covr"][0]
                data = bytes(pic)
                mime = "image/png" if getattr(pic, "imageformat", 13) == 14 else "image/jpeg"
        except Exception:
            data = b""
        if not data:
            for name in ("cover.jpg", "folder.jpg", "cover.png", "folder.png", "front.jpg"):
                candidate = path.parent / name
                if candidate.is_file() and candidate.stat().st_size < 12_000_000:
                    data = candidate.read_bytes()
                    mime = "image/png" if name.endswith(".png") else "image/jpeg"
                    break
        if not data:
            raise HTTPException(status_code=404, detail="Sem capa")
        return Response(data, media_type=mime, headers={"Cache-Control": "private, max-age=86400"})

    @app.get("/api/lyrics/{track_id}")
    async def stream_lyrics(track_id: str):
        """Letra (sincronizada quando existir) de uma faixa do catálogo Qobuz."""
        import re

        import httpx

        from qobuz_dl.downloader import fetch_qobuz_lyrics_json
        from qobuz_dl.lyrics_engine import LyricsEngine

        client = await service.ensure_connected()
        if client is None:
            return {"kind": "none", "text": ""}
        async with httpx.AsyncClient(follow_redirects=True) as http:
            payload = await fetch_qobuz_lyrics_json(client, http, track_id)
        engine = LyricsEngine.__new__(LyricsEngine)
        parsed = engine.extract_qobuz_lyrics(payload)
        if not parsed:
            return {"kind": "none", "text": ""}
        if parsed.get("synced"):
            return {"kind": "synced", "text": parsed["synced"], "lang": parsed.get("lang")}
        if parsed.get("plain"):
            return {"kind": "plain", "text": parsed["plain"], "lang": parsed.get("lang")}
        return {"kind": "none", "text": ""}

    @app.get("/api/library/lyrics/{file_key}")
    async def local_lyrics(file_key: str):
        """Letras de um arquivo local: .lrc ao lado do áudio ou tag embutida."""
        import re

        path = await service.local_path(file_key)
        if not path:
            raise HTTPException(status_code=404, detail="Arquivo não encontrado")
        root = (
            Path(
                service.local_settings["directory"]
                or service.config_status()["directory"]
            )
            .expanduser()
            .resolve()
        )
        if root not in path.resolve().parents:
            raise HTTPException(status_code=403, detail="Caminho fora da biblioteca")
        text = ""
        sidecar = path.with_suffix(".lrc")
        try:
            if sidecar.is_file() and sidecar.stat().st_size < 512_000:
                text = sidecar.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if not text:
            try:
                from mutagen import File as AudioFile

                audio = AudioFile(path)
                tags = getattr(audio, "tags", None)
                if tags is not None:
                    if hasattr(tags, "getall"):
                        frames = tags.getall("USLT")
                        text = str(frames[0].text) if frames else ""
                    else:
                        for name in ("LYRICS", "lyrics", "UNSYNCEDLYRICS", "unsyncedlyrics"):
                            value = tags.get(name)
                            if value:
                                text = str(value[0])
                                break
            except Exception:
                text = ""
        text = text[:512_000]
        if not text.strip():
            return {"kind": "none", "text": ""}
        synced = bool(re.search(r"\[\d{1,3}:\d{2}(?:[.:]\d{1,3})?\]", text))
        return {"kind": "synced" if synced else "plain", "text": text}

    @app.post("/api/settings")
    async def save_settings(payload: SettingsRequest):
        try:
            return service.save_settings(payload)
        except (ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/tools")
    async def tools():
        return {
            "items": [
                {"action": key, "label": label} for key, label in TOOL_LABELS.items()
            ]
        }

    @app.post("/api/tools/run")
    async def run_tool(payload: ToolRequest):
        try:
            return await service.start_tool(payload)
        except RuntimeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    @app.get("/api/tools/jobs")
    async def tool_jobs():
        return {"items": [dict(job) for job in list(service.tool_jobs.values())[-20:]]}

    @app.post("/api/tools/jobs/{job_id}/stop")
    async def stop_tool(job_id: str):
        if job_id not in service.tool_jobs:
            raise HTTPException(status_code=404, detail="Atividade não encontrada.")
        stopped = await service.stop_tool(job_id)
        return {"success": stopped, "status": service.tool_jobs[job_id]["status"]}

    @app.post("/api/download")
    async def download(payload: DownloadRequest):
        return await service.enqueue(payload)

    @app.get("/api/queue")
    async def queue():
        async with service.queue_lock:
            rows = [dict(item) for item in service.history] + [
                dict(item) for item in service.queue
            ]
            if service.current:
                rows.append(dict(service.current))
            return {"items": rows[-80:], "busy": bool(service.current)}

    @app.delete("/api/queue/{queue_id}")
    async def remove_queue_item(queue_id: str):
        async with service.queue_lock:
            original = len(service.queue)
            service.queue = [item for item in service.queue if item["id"] != queue_id]
            if len(service.queue) == original:
                raise HTTPException(
                    status_code=404, detail="Item pendente não encontrado"
                )
        return {"success": True}

    @app.get("/api/stream/{track_id}")
    async def stream(track_id: str, request: Request, quality: int = 6):
        if quality not in {5, 6}:
            quality = 6
        if service.demo:
            raise HTTPException(status_code=409, detail="A reprodução fica desativada na demonstração.")
        client = await service.ensure_connected()
        try:
            result = await client.get_track_url(track_id, quality)
        except Exception:
            logger.exception("Could not obtain authorized audio stream")
            raise HTTPException(
                status_code=403,
                detail="A faixa não está disponível para reprodução nesta conta ou formato.",
            ) from None
        if result.get("raw_key") or result.get("key") or not result.get("url"):
            raise HTTPException(
                status_code=415,
                detail="Esta faixa vem criptografada/segmentada e ainda não pode ser tocada no navegador. Baixe-a para ouvir.",
            )
        parsed = urlparse(result["url"])
        host = (parsed.hostname or "").lower()
        allowed = (
            host == "qobuz.com" or host.endswith(".qobuz.com")
            or host.endswith(".akamaized.net") or host.endswith(".akamaihd.net")
        )
        if parsed.scheme != "https" or not allowed:
            raise HTTPException(status_code=502, detail="A origem do fluxo retornado não foi reconhecida como CDN Qobuz.")
        # O servidor busca o áudio e entrega como ARQUIVO (200/206, Content-Length,
        # Accept-Ranges): o navegador enxerga duração e permite avançar/voltar livremente.
        upstream_headers = {}
        if request.headers.get("range"):
            upstream_headers["Range"] = request.headers["range"]
        http = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(20.0, read=60.0))
        try:
            upstream = await http.send(http.build_request("GET", result["url"], headers=upstream_headers), stream=True)
        except Exception:
            await http.aclose()
            raise HTTPException(status_code=502, detail="Não foi possível abrir o áudio no CDN do Qobuz.") from None
        if upstream.status_code >= 400:
            code = upstream.status_code
            await upstream.aclose()
            await http.aclose()
            raise HTTPException(status_code=502, detail=f"O CDN do Qobuz recusou o áudio (HTTP {code}).")
        headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=600"}
        for name in ("content-length", "content-range"):
            if name in upstream.headers:
                headers[name.title()] = upstream.headers[name]
        ctype = upstream.headers.get("content-type", "")
        if not ctype.startswith("audio/"):
            ctype = "audio/flac" if quality == 6 else "audio/mpeg"

        async def body():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()
                await http.aclose()

        return StreamingResponse(body(), status_code=upstream.status_code, media_type=ctype, headers=headers)

    return app


def run_gui(
    host: str = "0.0.0.0",
    port: int = 8060,
    demo: bool = False,
    open_browser: bool = True,
) -> None:
    """Sobe o servidor da GUI (bloqueante — roda em primeiro plano até Ctrl+C).

    Usado por `qobuz-dl gui run` e pelo processo filho que
    `qobuz_dl.gui_daemon.start()` cria em segundo plano. `host` aceita
    loopback, `0.0.0.0` (padrão), `lan` (IP desta máquina na rede local)
    ou um IP válido.
    """
    import asyncio
    import threading
    import webbrowser

    import uvicorn

    from qobuz_dl.gui_daemon import resolve_host, validate_host

    if not 1 <= port <= 65535:
        raise ValueError("a porta precisa estar entre 1 e 65535")
    resolved_host = validate_host(resolve_host(host))
    loopback = {"127.0.0.1", "localhost", "::1"}
    if open_browser and not demo and (
        resolved_host in loopback or resolved_host == "0.0.0.0"
    ):
        local = "127.0.0.1" if resolved_host == "0.0.0.0" else resolved_host
        webbrowser.open(f"http://{local}:{port}/")

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(demo=demo, allow_network=resolved_host not in loopback),
            host=resolved_host,
            port=port,
            log_level="info",
        )
    )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        server.run()
        return

    # Já existe um event loop ativo (caso de `qobuz-dl gui run`, que passa por
    # async_main em cli.py): server.run() chamaria asyncio.run() de novo e
    # estouraria. Roda o servidor numa thread com loop próprio.
    thread = threading.Thread(target=server.run, name="qobuz-gui-server", daemon=True)
    thread.start()
    try:
        while thread.is_alive():
            thread.join(0.5)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(5)
        return

    if not server.started:
        raise ValueError(
            f"o servidor não conseguiu subir em {resolved_host}:{port} "
            "(a porta pode já estar em uso)"
        )


def main() -> None:
    """Ponto de entrada para `python -m qobuz_dl.webapp` (uso direto, fora
    do comando principal). O caminho documentado e recomendado é
    `qobuz-dl gui` (ou `qobuz-dl gui run` para primeiro plano) — ver
    qobuz_dl/cli.py e o README."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Qobuz-DL Studio — interface web local"
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Interface de rede (padrão: 0.0.0.0, todas as interfaces)",
    )
    parser.add_argument(
        "--port", type=int, default=8060, help="Porta local (padrão: 8060)"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Abrir demonstração sem acesso a conta ou downloads",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Não abrir o navegador automaticamente",
    )
    args = parser.parse_args()
    try:
        run_gui(
            host=args.host,
            port=args.port,
            demo=args.demo,
            open_browser=not args.no_browser,
        )
    except ValueError as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
