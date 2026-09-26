"""Qobuz-DL Studio: local browser interface for Qobuz-DL Ultra.

By default, the server binds only to loopback. Existing credentials are read
from the user's local config/keyring and are never returned to the browser.
"""

from __future__ import annotations

import asyncio
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
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return defaults
            quality = int(data.get("quality", 6))
            if quality not in QUALITY_LABELS:
                quality = 6
            parsed = {**defaults, **data, "quality": quality}
            for key in (
                "embed_art",
                "fetch_lyrics",
                "lrc_files",
                "credits",
                "m3u",
                "quality_fallback",
                "playlist_as_albums",
                "verify_after_download",
                "no_cover",
                "smart_discography",
                "multi_value_tags",
            ):
                parsed[key] = bool(parsed[key])
            parsed["max_workers"] = max(1, min(16, int(parsed.get("max_workers", 1))))
            parsed["segment_workers"] = max(
                2, min(16, int(parsed.get("segment_workers", 4)))
            )
            for key in ("folder_format", "track_format"):
                if not isinstance(parsed[key], str) or not parsed[key].strip():
                    parsed[key] = defaults[key]
            return parsed
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return defaults

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
            raise HTTPException(
                status_code=409,
                detail="Conecte sua conta Qobuz para acessar o catálogo.",
            )
        return self.client

    def save_settings(self, request: SettingsRequest) -> dict[str, Any]:
        directory, quality = request.directory, request.quality
        if quality not in QUALITY_LABELS:
            raise ValueError("Qualidade inválida")
        expanded = Path(os.path.expanduser(directory)).resolve()
        if expanded.exists() and not expanded.is_dir():
            raise ValueError("O destino informado existe e não é uma pasta")
        self.local_settings = request.model_dump()
        self.local_settings["directory"] = str(expanded)
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.settings_path.parent.chmod(0o700)
        except OSError:
            pass
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self.local_settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.settings_path)
        if self.engine:
            self.engine.directory, self.engine.quality = str(expanded), quality
            (
                self.engine.settings.default_folder,
                self.engine.settings.default_quality,
            ) = str(expanded), quality
            self.engine.embed_art = request.embed_art
            self.engine.fetch_lyrics = request.fetch_lyrics
            self.engine.no_lrc_files = not request.lrc_files
            self.engine.no_credits = not request.credits
            self.engine.no_m3u_for_playlists = not request.m3u
            self.engine.quality_fallback = request.quality_fallback
            self.engine.playlist_as_albums = request.playlist_as_albums
            self.engine.settings.verify_after_download = request.verify_after_download
            self.engine.settings.max_workers = request.max_workers
            self.engine.settings.segment_workers = request.segment_workers
            self.engine.settings.multi_value_tags = request.multi_value_tags
            self.engine.settings.smart_discography = request.smart_discography
            self.engine.settings.embedded_art_size = request.embedded_art_size
            self.engine.settings.saved_art_size = request.saved_art_size
            self.engine.no_cover = request.no_cover
            self.engine.folder_format = request.folder_format
            self.engine.track_format = request.track_format
        return self.config_status()

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
            "cover": None,
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
            try:
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

    def library(self) -> list[dict[str, Any]]:
        root = (
            Path(self.local_settings["directory"] or self.config_status()["directory"])
            .expanduser()
            .resolve()
        )
        if not root.is_dir():
            self.local_files.clear()
            return []
        try:
            from mutagen import File as AudioFile
        except ImportError:
            return []
        rows: list[dict[str, Any]] = []
        self.local_files.clear()
        try:
            files = (
                p
                for p in root.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
            )
            ordered = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return []
        for path in ordered[:500]:
            try:
                audio = AudioFile(path, easy=True)
                if audio is None:
                    continue
                tags = audio.tags or {}
                get = lambda name, default="", _tags=tags: str(
                    (_tags.get(name) or [default])[0]
                )
                info = getattr(audio, "info", None)
                key = secrets.token_urlsafe(16)
                self.local_files[key] = path.resolve()
                rows.append(
                    {
                        "key": key,
                        "title": get("title", path.stem),
                        "artist": get("artist", "Artista desconhecido"),
                        "album": get("album", ""),
                        "duration": int(getattr(info, "length", 0) or 0),
                        "format": path.suffix.lower().lstrip("."),
                        "size": path.stat().st_size,
                        "pathLabel": path.parent.name,
                        "cover": None,
                    }
                )
            except (OSError, ValueError, TypeError):
                continue
        return rows

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
    image = item.get("image") or item.get("cover")
    if isinstance(image, str) and image.startswith(("https://", "http://")):
        return image
    if isinstance(image, dict):
        for value in image.values():
            if isinstance(value, str) and value.startswith(("https://", "http://")):
                return value
    return None


def _track_result(item: dict[str, Any]) -> dict[str, Any]:
    album = item.get("album") or {}
    performer = item.get("performer") or item.get("artist") or {}
    return {
        "id": str(item.get("id", "")),
        "title": item.get("title") or "Faixa sem título",
        "artist": performer.get("name", "Artista desconhecido")
        if isinstance(performer, dict)
        else str(performer),
        "album": album.get("title", "") if isinstance(album, dict) else str(album),
        "duration": int(item.get("duration") or 0),
        "cover": _cover(album if isinstance(album, dict) else item) or _cover(item),
        "quality": f"{item.get('maximum_bit_depth', 16)}b/{item.get('maximum_sampling_rate', 44.1)}kHz"
        if item.get("hires_streamable")
        else "16b/44.1kHz",
        "isrc": item.get("isrc", ""),
    }


def _album_result(item: dict[str, Any]) -> dict[str, Any]:
    artist = item.get("artist") or item.get("artists") or item.get("performer") or {}
    if isinstance(artist, list):
        artist_name = ", ".join(
            a.get("name", "") for a in artist if isinstance(a, dict)
        )
    elif isinstance(artist, dict):
        artist_name = artist.get("name", "Artista desconhecido")
    else:
        artist_name = str(artist)
    genre = item.get("genre") or ""
    return {
        "id": str(item.get("id", "")),
        "title": item.get("title") or "Álbum sem título",
        "artist": artist_name or "Artista desconhecido",
        "year": str(
            item.get("release_date_original") or item.get("release_date") or ""
        )[:4],
        "genre": genre.get("name", "") if isinstance(genre, dict) else genre,
        "tracks_count": int(item.get("tracks_count") or 0),
        "quality": f"{item.get('maximum_bit_depth', 16)}b/{item.get('maximum_sampling_rate', 44.1)}kHz"
        if item.get("hires_streamable")
        else "16b/44.1kHz",
        "cover": _cover(item),
        "duration": "",
    }


def create_app(*, demo: bool = False) -> FastAPI:
    service = GuiService(demo=demo)

    @asynccontextmanager
    async def lifespan(_app):
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
        host = request.headers.get("host", "").split(":", 1)[0].strip("[]").lower()
        if host not in {"localhost", "127.0.0.1", "::1"} and not demo:
            return Response(
                "A interface aceita apenas conexões locais.", status_code=403
            )
        origin = request.headers.get("origin")
        site = request.headers.get("sec-fetch-site")
        if not demo and (
            (
                origin
                and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}
            )
            or site in {"cross-site", "same-site"}
        ):
            return Response("Origem não permitida.", status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self' https: data:; media-src 'self' https:; img-src 'self' https: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
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

    @app.get("/api/status")
    async def status():
        return service.config_status()

    @app.get("/api/settings")
    async def get_settings():
        result = dict(service.local_settings)
        status = service.config_status()
        result["directory"] = result["directory"] or status["directory"]
        result["quality"] = result["quality"] or status["quality"]
        result["configDir"] = status["configDir"]
        return result

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
    async def search(q: str, kind: str = "tracks", limit: int = 24):
        if len(q.strip()) < 2:
            return {"tracks": [], "albums": []}
        limit = max(1, min(limit, 50))
        if service.demo:
            query = q.casefold()
            tracks = [
                t
                for t in DEMO_TRACKS
                if query in f"{t['title']} {t['artist']} {t['album']}".casefold()
            ]
            albums = [
                a
                for a in DEMO_ALBUMS
                if query in f"{a['title']} {a['artist']} {a['genre']}".casefold()
            ]
            return {
                "tracks": tracks[:limit] if kind in {"tracks", "all"} else [],
                "albums": albums[:limit] if kind in {"albums", "all"} else [],
            }
        client = await service.ensure_connected()
        try:
            data: dict[str, list[dict[str, Any]]] = {"tracks": [], "albums": []}
            if kind in {"tracks", "all"}:
                raw = await client.search_tracks(q.strip(), limit=limit)
                items = (
                    raw.get("tracks", {}).get("items", [])
                    if isinstance(raw, dict)
                    else []
                )
                data["tracks"] = [
                    _track_result(x) for x in items if isinstance(x, dict)
                ]
            if kind in {"albums", "all"}:
                raw = await client.search_albums(q.strip(), limit=limit)
                items = (
                    raw.get("albums", {}).get("items", [])
                    if isinstance(raw, dict)
                    else []
                )
                data["albums"] = [
                    _album_result(x) for x in items if isinstance(x, dict)
                ]
            return data
        except Exception:
            logger.exception("Catalog search failed")
            raise HTTPException(
                status_code=502, detail="A busca no catálogo falhou. Tente novamente."
            ) from None

    @app.get("/api/album/{album_id}")
    async def album_details(album_id: str):
        if service.demo:
            album = next((x for x in DEMO_ALBUMS if x["id"] == album_id), None)
            if album is None:
                raise HTTPException(status_code=404, detail="Álbum não encontrado")
            return {
                "album": album,
                "tracks": [
                    dict(x) for x in DEMO_TRACKS if x["album"] == album["title"]
                ],
            }
        client = await service.ensure_connected()
        try:
            raw = await client.get_album_meta(album_id)
            block = raw.get("tracks", {}) if isinstance(raw, dict) else {}
            items = block.get("items", []) if isinstance(block, dict) else []
            return {
                "album": _album_result(raw),
                "tracks": [_track_result(x) for x in items if isinstance(x, dict)],
            }
        except Exception:
            logger.exception("Album metadata fetch failed")
            raise HTTPException(
                status_code=502, detail="Não foi possível carregar este álbum."
            ) from None

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
    async def library():
        return {
            "items": service.library(),
            "directory": service.local_settings["directory"]
            or service.config_status()["directory"],
        }

    @app.get("/api/library/play/{file_key}")
    async def local_audio(file_key: str):
        path = service.local_files.get(file_key)
        if not path or not path.is_file():
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
        return FileResponse(
            path,
            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream",
        )

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
    async def stream(track_id: str, quality: int = 6):
        if quality not in {5, 6}:
            raise HTTPException(
                status_code=400,
                detail="O player do navegador suporta MP3 ou FLAC de CD. Hi-Res segmentado não é reproduzido pelo navegador nesta versão.",
            )
        if service.demo:
            raise HTTPException(
                status_code=409, detail="A reprodução fica desativada na demonstração."
            )
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
                detail="O fluxo exige decodificação proprietária e não pode ser reproduzido pelo player do navegador.",
            )
        parsed = urlparse(result["url"])
        host = (parsed.hostname or "").lower()
        allowed = (
            host == "qobuz.com"
            or host.endswith(".qobuz.com")
            or host.endswith(".akamaized.net")
            or host.endswith(".akamaihd.net")
        )
        if parsed.scheme != "https" or not allowed:
            raise HTTPException(
                status_code=502,
                detail="A origem do fluxo retornado não foi reconhecida como CDN Qobuz.",
            )
        return RedirectResponse(result["url"], status_code=307)

    return app


def main() -> None:
    """Launch the browser UI; loopback is the default to keep it private."""
    import argparse
    import webbrowser
    import uvicorn

    parser = argparse.ArgumentParser(
        description="Qobuz-DL Studio — interface web local"
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Interface de rede (padrão: apenas este computador)",
    )
    parser.add_argument(
        "--port", type=int, default=8787, help="Porta local (padrão: 8787)"
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
    if not 1 <= args.port <= 65535:
        parser.error("a porta precisa estar entre 1 e 65535")
    if args.host not in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}:
        parser.error(
            "por segurança, o host deve ser loopback; 0.0.0.0 só é útil para um preview isolado"
        )
    if not args.no_browser and not args.demo:
        webbrowser.open(f"http://127.0.0.1:{args.port}/")
    uvicorn.run(
        create_app(demo=args.demo), host=args.host, port=args.port, log_level="info"
    )


if __name__ == "__main__":
    main()
