# ==============================================================================
# MÓDULO: cli.py (QOBUZ-DL-ULTRA) -- VERSÃO COMPLETA ATUALIZADA
# CORREÇOES APLICADAS:
#   1) user_token e user_auth_token sincronizados no config.ini
#   2) Token lido de múltiplas chaves (user_token, user_auth_token, auth_token, keyring)
#   3) Assinatura verificada corretamente via check_subscription()
# ==============================================================================

import argparse
import asyncio
import configparser
import contextlib
import glob
import io
import json
import logging
import os
import signal
import string
import sys
import tempfile
import time
from datetime import datetime
from typing import Optional

import httpx
import keyring
import send2trash
from packaging.version import Version

from qobuz_dl import fuzzy, ui

# Módulos internos do ecossistema QOBUZ-DL
from qobuz_dl.bundle import Bundle
from qobuz_dl.color import (
    ACCENT_PRESETS,
    BG,
    GREEN,
    MUTED,
    OFF,
    RED,
    RESET,
    accent_preview,
)
from qobuz_dl.color import HIGHLIGHT as ACCENT
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import WARNING as YELLOW
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.http_download import make_sync_client
from qobuz_dl.paths import DirectoryNotUsable, ensure_directory_ready
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.utils import checar_binarios_externos, get_config_paths

logger = logging.getLogger(__name__)


def _startup_timing(stage: str, started: float | None = None) -> None:
    """Emite métricas de inicialização somente quando solicitado pelo usuário.

    Ative com QOBUZ_DL_STARTUP_TIMING=1. Não registra credenciais nem dados
    das requisições; serve para localizar gargalos sem poluir o uso normal.
    """
    if os.environ.get("QOBUZ_DL_STARTUP_TIMING", "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return
    elapsed = time.perf_counter() - started if started is not None else None
    if elapsed is None:
        logger.warning("[startup-timing] %s", stage)
    else:
        logger.warning("[startup-timing] %s: %.3fs", stage, elapsed)


_config_paths = get_config_paths()
CONFIG_DIR = _config_paths["config_dir"]
CONFIG_PATH = _config_paths["config_path"]
CONFIG_FILE = _config_paths["config_file"]
QOBUZ_DB = _config_paths["qobuz_db"]
IOS_HOME = os.environ.get("QOBUZ_DL_IOS_HOME")

KEYRING_SERVICE = "qobuz-dl"
_SENSITIVE_CONFIG_KEYS = {
    "auth_token",
    "email",
    "genius_token",
    "password",
    "secrets",
    "user_auth_token",
    "user_token",
}


def _write_config_secure(config, config_file):
    """Grava o INI atomicamente e limita sua leitura ao usuário atual."""
    parent = os.path.dirname(os.path.abspath(config_file))
    os.makedirs(parent, mode=0o700, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".config.", dir=parent, text=True)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as config_handle:
            config.write(config_handle)
            config_handle.flush()
            os.fsync(config_handle.fileno())
        os.replace(temporary_path, config_file)
        if os.name == "posix":
            os.chmod(config_file, 0o600)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _redacted_config(config):
    """Serializa uma cópia do INI sem revelar credenciais no terminal."""
    safe = configparser.ConfigParser(interpolation=None)
    for key, value in config.defaults().items():
        safe["DEFAULT"][key] = (
            "<redacted>" if key.lower() in _SENSITIVE_CONFIG_KEYS and value else value
        )
    for section in config.sections():
        safe.add_section(section)
        for key, value in config.items(section):
            safe.set(
                section,
                key,
                "<redacted>"
                if key.lower() in _SENSITIVE_CONFIG_KEYS and value
                else value,
            )
    output = io.StringIO()
    safe.write(output)
    return output.getvalue()


def _bootstrap_ui():
    """Configure UI system early based on command-line flags before argparse runs."""
    argv = sys.argv[1:]
    ui.configure(
        quiet="--quiet" in argv,
        verbose=("--verbose" in argv or "-v" in argv),
        color=False if "--no-color" in argv else None,
    )
    ui.install_logging()


_bootstrap_ui()


def _keyring_save(key: str, value: str) -> bool:
    """Store a credential in the OS keyring. Returns True on success."""
    if not value:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception:
        return False


def _keyring_load(key: str):
    """Retrieve a credential from the OS keyring. Returns None if not found or on error."""
    try:
        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception:
        return None


def _save_token_in_both_config_keys(config, token: str) -> None:
    """Mantém user_token e user_auth_token sincronizados no config.ini."""
    token = (token or "").strip()
    if not token:
        return
    section = "qobuz" if config.has_section("qobuz") else "DEFAULT"
    if not config.has_section(section) and section != "DEFAULT":
        config.add_section(section)
    config.set(section, "user_token", token)
    config.set(section, "user_auth_token", token)


def _load_token_from_all_locations(config, section="qobuz"):
    """Lê o token usando todos os nomes legados/atuais."""
    disable_keyring = config.getboolean(section, "disable_keyring", fallback=False)
    keyring_token = "" if disable_keyring else (_keyring_load("auth_token") or "")
    for value in (
        keyring_token,
        config.get(section, "user_auth_token", fallback=""),
        config.get(section, "user_token", fallback=""),
        config.get(section, "auth_token", fallback=""),
        config.get(section, "password", fallback=""),
    ):
        value = (value or "").strip()
        if value:
            return value
    return ""


def validate_config_formats(formats_to_check: dict):
    """Validate folder_format and track_format patterns for typos and unknown placeholders."""
    VALID_KEYS = {
        "artist",
        "album",
        "album_id",
        "album_url",
        "album_title",
        "album_title_base",
        "album_artist",
        "album_genre",
        "album_composer",
        "label",
        "copyright",
        "upc",
        "barcode",
        "release_date",
        "year",
        "media_type",
        "format",
        "bit_depth",
        "sampling_rate",
        "album_version",
        "version_tag",
        "disc_count",
        "track_count",
        "ExplicitFlag",
        "explicit",
        "release_type",
        "tracktitle",
        "track_title",
        "track_title_base",
        "track_id",
        "track_artist",
        "track_composer",
        "track_number",
        "isrc",
        "version",
        "disc_number",
    }

    has_errors = False

    for config_name, format_string in formats_to_check.items():
        if not format_string:
            continue

        try:
            parsed_vars = [
                tup[1]
                for tup in string.Formatter().parse(str(format_string))
                if tup[1] is not None
            ]

            for var in parsed_vars:
                base_var = var.split(":")[0].split("!")[0]

                if base_var not in VALID_KEYS:
                    ui.warn(
                        f"Aviso de Configuração: Variável desconhecida "
                        f"'{{{base_var}}}' em '{config_name}'."
                    )
                    best = fuzzy.melhor_match(base_var, VALID_KEYS, corte=0.6)
                    if best:
                        ui.detail(f"-> Você quis dizer '{{{best}}}'?")
                    ui.detail(
                        "-> Isso fará com que o padrão seja descartado "
                        "durante o download."
                    )
                    has_errors = True

        except ValueError as e:
            ui.error(f"Erro de Sintaxe em '{config_name}': {e}")
            has_errors = True

    if has_errors:
        ui.blank()
        ui.error(
            "Dica: Revise seu arquivo config.ini ou parâmetros de linha "
            "de comando antes de iniciar."
        )
        sys.exit(1)


def _pick_accent_color() -> str:
    """Interactive accent color picker for the config wizard. Returns RGB string."""
    ui.emit(f"\n{BG}[?] Cor de destaque do programa:{OFF}")
    ui.wrapped("Aparece em nomes de faixas, cabeçalhos, barras e progresso.", indent=4)
    ui.blank()

    for idx, (name, _rgb, escape) in enumerate(ACCENT_PRESETS, 1):
        if escape:
            preview = accent_preview(escape, "━━ [FAIXA] ARTISTA The Weeknd")
            ui.emit(f" {idx:2}. {name:<22} {preview}")
        else:
            ui.emit(f" {idx:2}. {name}")

    ui.blank()
    while True:
        _n = len(ACCENT_PRESETS)
        prompt = f"Escolha (1-{_n}) [Enter = 1 padrão]: "
        if len(prompt) > ui.width():
            prompt = f"Escolha 1-{_n}: "
        choice = input(prompt).strip()
        if not choice:
            choice = "1"
        try:
            idx = int(choice)
            if 1 <= idx <= len(ACCENT_PRESETS):
                name, rgb, escape = ACCENT_PRESETS[idx - 1]
                break
        except ValueError:
            pass
        ui.emit(f" Por favor escolha entre 1 e {len(ACCENT_PRESETS)}.")

    if rgb is None:
        ui.emit("\n Digite os valores RGB separados por ponto e vírgula.")
        ui.emit(" Exemplo: 255;100;50 (vermelho), 0;200;150 (teal)\n")
        while True:
            raw = input(" Código RGB (R;G;B): ").strip()
            raw = raw.replace(",", ";").replace(" ", ";")
            parts_str = [x.strip() for x in raw.split(";") if x.strip()]
            try:
                parts = [int(x) for x in parts_str]
                if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                    rgb = ";".join(str(p) for p in parts)
                    escape = ui.c(f"\033[38;2;{parts[0]};{parts[1]};{parts[2]}m")
                    break
            except ValueError:
                pass
            ui.emit(" Formato inválido. Use três números de 0 a 255, ex: 150;80;220")

    ui.emit("\n Preview da sua cor:")
    ui.emit(accent_preview(escape or "", "━━ [FAIXA] ARTISTA The Weeknd\n"))
    confirm = (
        input(" Confirmar esta cor? (Enter = sim, n = escolher outra): ")
        .strip()
        .lower()
    )
    if confirm in ("n", "nao", "no"):
        return _pick_accent_color()

    ui.emit(f"\n {GREEN}Cor salva: {escape}━━ {name.strip()}{OFF}\n")
    return rgb


_UNDO_STATUS_TEXT = {
    "restored": ("restaurado", None),
    "would_restore": ("seria restaurado", None),
    "changed_since": (
        "pulado: foi alterado depois do tags (use --undo-force para restaurar)",
        "warn",
    ),
    "missing": ("pulado: o arquivo não existe mais", "warn"),
    "verify_failed": ("restaurado, mas a conferência final não bateu", "error"),
    "error": ("erro ao restaurar", "error"),
}


def _handle_tags_suggest_names(target_dir: str, settings) -> int:
    """`tags --suggest-names`: só lê as tags da pasta; não consulta o Qobuz nem grava."""
    from qobuz_dl import name_suggest
    from qobuz_dl.metadata import _nomes_protegidos

    if not os.path.isdir(target_dir):
        ui.error(f"A pasta não existe no dispositivo: '{target_dir}'")
        return 1
    ui.step(f"Procurando artistas com vírgula no nome em {target_dir} (só leitura)...")

    def progress(done: int, total: int) -> None:
        ui.emit(f"  {done}/{total} arquivos lidos")

    scanned = name_suggest.scan_directory(target_dir, progress=progress)
    suggestions = name_suggest.suggest_from_lists(
        scanned, protected=_nomes_protegidos(settings=settings)
    )
    if not suggestions:
        ui.ok("Nenhum artista com vírgula faltando: nada para adicionar ao config.ini.")
        return 0
    ui.step("Possíveis artistas com vírgula no nome (revise antes de usar):")
    for item in suggestions:
        ui.emit(f"  • {item['name']}  ({item['files']} faixas)")
    ui.emit(
        "  Eles aparecem sempre lado a lado, como se fossem um nome só. Se for o "
        "caso, adicione na seção [qobuz] do config.ini:"
    )
    ui.emit(
        "  "
        + name_suggest.config_line(
            getattr(settings, "artists_with_comma", ()) or (), suggestions
        )
    )
    return 0


def auth_failure_hint(exc: BaseException) -> str:
    """Próxima ação para uma falha de login, de acordo com o tipo do erro."""
    from qobuz_dl.exceptions import (
        AuthenticationError,
        InvalidAppCredentialsError,
        InvalidAppSecretError,
        NoActiveSubscriptionError,
    )

    name = type(exc).__name__.lower()
    text = str(exc).lower()
    if isinstance(exc, NoActiveSubscriptionError):
        return (
            "Sua conta não tem assinatura ativa de streaming/download: confira "
            "a assinatura no site da Qobuz."
        )
    if isinstance(exc, (InvalidAppCredentialsError, InvalidAppSecretError)):
        return (
            "O app_id/secret do Qobuz foi recusado. Atualize o programa "
            "(pip install -U qobuz-dl-ultra) ou rode: qobuz-dl -r"
        )
    if isinstance(exc, AuthenticationError) or any(
        word in text for word in ("401", "unauthorized", "invalid token", "token")
    ):
        return (
            "O token pode ter expirado ou sido revogado. Informe um novo token: "
            "python3 -m qobuz_dl auth -l"
        )
    if "keyring" in name or "keyring" in text:
        return (
            "Não foi possível ler o cofre de senhas (keyring). Rode: qobuz-dl "
            "doctor; ou use disable_keyring = true no config.ini."
        )
    if (
        isinstance(exc, (ConnectionError, TimeoutError))
        or any(word in name for word in ("connect", "timeout", "network", "dns"))
        or any(
            word in text
            for word in (
                "name or service",
                "temporary failure",
                "connection",
                "timed out",
                "network",
                "nodename",
            )
        )
    ):
        return (
            "Parece problema de rede. Verifique sua conexão com a internet (ou "
            "VPN/proxy) e tente de novo."
        )
    return "Rode: qobuz-dl doctor para diagnosticar o ambiente e a configuração."


async def _reset_config(config_file: str):
    if ui.width() >= 41:
        logging.info(f"\n{BG}[ QOBUZ-DL-ULTRA - CONFIGURAÇÃO INICIAL ]{OFF}")
    else:
        logging.info(f"\n{BG}[ CONFIGURAÇÃO INICIAL ]{OFF}")

    config = configparser.ConfigParser(interpolation=None)
    config["qobuz"] = {}

    accent_rgb = _pick_accent_color()
    config["qobuz"]["accent_color"] = accent_rgb
    C_ACCENT = ui.c(f"\033[38;2;{accent_rgb}m") if accent_rgb else ui.c(YELLOW)

    email = input("Digite seu e-mail do Qobuz:\n- ").strip()
    config["qobuz"]["email"] = email

    ui.emit(f"\n{C_ACCENT}[!] ATENÇÃO: A API Qobuz bloqueou login direto por senha para apps de terceiros.{OFF}")
    ui.emit(f"{C_ACCENT}[!] Obtenha seu Token no navegador (F12 > Application/Storage > Local Storage > token).{OFF}")

    auth_token = input("Cole o token do seu navegador aqui:\n- ").strip()
    config["qobuz"]["password"] = ""
    config["qobuz"]["user_token"] = auth_token
    config["qobuz"]["user_auth_token"] = auth_token

    disable_kr = (
        input(
            " Desativar Keyring e salvar tokens em texto puro no config.ini? (yes/no) [Padrão: no]\n- "
        )
        .strip()
        .lower()
    )
    use_keyring = False if disable_kr in ["yes", "y", "true"] else True
    config["qobuz"]["disable_keyring"] = "true" if not use_keyring else "false"

    if use_keyring and _keyring_save("auth_token", auth_token):
        config["qobuz"]["auth_token"] = ""
    else:
        config["qobuz"]["auth_token"] = auth_token

    fetch_lyrics = (
        input(
            "\nBaixar e embutir letras/traduções automaticamente? (yes/no) [Padrão: yes]\n- "
        )
        .strip()
        .lower()
    )
    config["qobuz"]["fetch_lyrics"] = (
        "false" if fetch_lyrics in ["no", "n", "false"] else "true"
    )

    genius_token = ""
    if config["qobuz"]["fetch_lyrics"] == "true":
        ui.emit(f"\n{C_ACCENT}[?] Idioma de Tradução de Letras:{OFF}")
        ui.emit(
            " Opções: pt (Português), en (Inglês), es (Espanhol), fr (Francês), original (Manter nativo)"
        )
        lang_choice = input(" Idioma [Padrão: pt]:\n- ").strip().lower()
        if lang_choice in ["original", "orig"]:
            config["qobuz"]["lyrics_translation_lang"] = ""
        elif lang_choice in ["pt", "en", "es", "fr", "de", "it"]:
            config["qobuz"]["lyrics_translation_lang"] = lang_choice
        else:
            config["qobuz"]["lyrics_translation_lang"] = "pt"

        ui.emit(f"\n{C_ACCENT}[!] Para usar o Genius como fallback, insira seu API Token (Enter para pular e usar apenas LRCLIB):{OFF}")
        genius_token = input("Genius API Token:\n- ").strip()
    else:
        config["qobuz"]["lyrics_translation_lang"] = "pt"

    if use_keyring and _keyring_save("genius_token", genius_token):
        config["qobuz"]["genius_token"] = ""
    else:
        config["qobuz"]["genius_token"] = genius_token

    while True:
        directory_input = (
            input("\nPasta de download (pressione Enter para 'Qobuz Downloads')\n- ")
            or "Qobuz Downloads"
        )
        try:
            ensure_directory_ready(directory_input)
        except DirectoryNotUsable as error:
            ui.warn(str(error))
            continue
        break
    config["qobuz"]["directory"] = directory_input
    config["qobuz"]["folder_format"] = (
        input(f"\nFormato da pasta (pressione Enter para '{DEFAULT_FOLDER}')\n- ")
        or DEFAULT_FOLDER
    )
    config["qobuz"]["default_quality"] = (
        input(
            "\nQualidade (5:MP3 320k, 6:FLAC 16-bit, 7:Hi-Res 24b<=96kHz, 27:Hi-Res Max) [Padrão 27]\n- "
        )
        or "27"
    )
    config["qobuz"]["default_limit"] = "500"
    config["qobuz"]["no_m3u"] = "false"
    config["qobuz"]["albums_only"] = "false"
    config["qobuz"]["no_fallback"] = "false"
    config["qobuz"]["og_cover"] = "true"
    config["qobuz"]["embed_art"] = "true"
    config["qobuz"]["no_cover"] = "false"
    config["qobuz"]["no_database"] = "false"
    config["qobuz"]["no_lrc_files"] = "true"
    config["qobuz"]["embed_lyrics"] = "true"
    config["qobuz"]["multi_value_tags"] = "false"
    config["qobuz"]["legacy_charmap"] = "false"
    config["qobuz"]["blacklist"] = "blacklist.txt"

    logging.info(f"\n{C_ACCENT}Obtendo credenciais da API via [bundle.js]. Por favor, aguarde...{OFF}")
    bundle = await Bundle.create()
    fetched_app_id = str(bundle.get_app_id())
    fetched_secrets = [s for s in bundle.get_secrets().values() if s]
    config["qobuz"]["app_id"] = fetched_app_id
    config["qobuz"]["secrets"] = ",".join(fetched_secrets)
    logging.info(f"{GREEN}[+] app_id e secrets obtidos com sucesso.{OFF}")

    logging.info(f"\n{C_ACCENT}Testando login com o e-mail e o token informados...{OFF}")
    from qobuz_dl.qopy import Client as _Client

    try:
        _test_client = await _Client.create(
            email, "", fetched_app_id, fetched_secrets, user_auth_token=auth_token
        )
        await _test_client.close()
        logging.info(f"{GREEN}[+] Login realizado com sucesso! Suas credenciais estão corretas.{OFF}")
    except Exception as e:
        logging.info(
            f"{RED}[!] Falha ao logar com este e-mail/token: {e}{OFF}\n"
            f"{YELLOW}[!] A configuração foi salva mesmo assim -- confira seu e-mail/token "
            f"e rode 'qdl -r' de novo (ou 'qdl auth -l') se precisar corrigi-los.{OFF}"
        )

    config["qobuz"]["track_format"] = "{track_number} - {track_title}"
    config["qobuz"]["fallback_folder_format"] = "{album_artist} - {album_title}"
    config["qobuz"]["smart_discography"] = "false"
    config["qobuz"]["no_album_artist_tag"] = "false"
    config["qobuz"]["no_album_title_tag"] = "false"
    config["qobuz"]["no_track_artist_tag"] = "false"
    config["qobuz"]["no_track_title_tag"] = "false"
    config["qobuz"]["no_release_date_tag"] = "false"
    config["qobuz"]["no_media_type_tag"] = "false"
    config["qobuz"]["no_genre_tag"] = "false"
    config["qobuz"]["no_track_number_tag"] = "false"
    config["qobuz"]["no_track_total_tag"] = "false"
    config["qobuz"]["no_disc_number_tag"] = "false"
    config["qobuz"]["no_disc_total_tag"] = "false"
    config["qobuz"]["no_composer_tag"] = "false"
    config["qobuz"]["no_replaygain_tag"] = "false"
    config["qobuz"]["no_album_url_tag"] = "false"
    config["qobuz"]["no_explicit_tag"] = "false"
    config["qobuz"]["no_copyright_tag"] = "false"
    config["qobuz"]["no_label_tag"] = "false"
    config["qobuz"]["no_credits"] = "false"
    config["qobuz"]["no_upc_tag"] = "false"
    config["qobuz"]["no_isrc_tag"] = "false"
    config["qobuz"]["embedded_art_size"] = "org"
    config["qobuz"]["saved_art_size"] = "org"
    config["qobuz"]["multiple_disc_prefix"] = "CD"
    config["qobuz"]["multiple_disc_one_dir"] = "false"
    config["qobuz"]["multiple_disc_track_format"] = "{disc_number}.{track_number} - {track_title}"
    config["qobuz"]["max_workers"] = "1"

    _write_config_secure(config, config_file)
    logging.info(f"\n{GREEN}[+] Configuração salva com sucesso em {config_file}!{OFF}")

    global ACCENT, CYAN
    if accent_rgb:
        nova_cor = f"\033[38;2;{accent_rgb}m"
        ACCENT = nova_cor
        CYAN = nova_cor

    ui.emit(f"{ACCENT}\n [*] Atualizando interface...{OFF}", end="")
    await asyncio.sleep(2.0)
    ui.emit("\r\033[K", end="")
    _print_welcome_screen()


def _remove_leftovers(directory: str):
    for pattern in [".*.tmp", "~tmp_*.tmp"]:
        search_dir = os.path.join(directory, "**", pattern)
        for i in glob.glob(search_dir, recursive=True):
            try:
                send2trash.send2trash(i)
            except Exception as e:
                logger.debug(f"Falha ao mover arquivo temporário '{i}' para a lixeira: {e}")


def _format_timestamp(ts: Optional[int]) -> str:
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(ts)


def _formatar_valor(valor):
    if valor in (None, ""):
        return "N/A"
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    return valor


def _safe_subscription_value(sub_info: dict | None, key: str, default: str = "N/A") -> str:
    if not isinstance(sub_info, dict):
        return default
    value = sub_info.get(key, default)
    if value is None or value == "":
        return default
    return str(value)


async def _auth_command(config_file: str, update_credentials: bool = False, show_json: bool = False):
    from qobuz_dl.qopy import Client

    if not os.path.isfile(config_file):
        ui.error("Arquivo de configuração não encontrado. Execute 'qobuz-dl -r' primeiro.")
        return False

    config = configparser.ConfigParser(interpolation=None)
    config.read(config_file, encoding="utf-8")
    section = "qobuz" if config.has_section("qobuz") else "DEFAULT"

    email = config.get(section, "email", fallback="")
    app_id = config.get(section, "app_id", fallback="")
    secrets = [s for s in config.get(section, "secrets", fallback="").split(",") if s]
    force_english = not config.getboolean(section, "native_lang", fallback=False)
    disable_keyring = config.getboolean(section, "disable_keyring", fallback=False)
    token = _load_token_from_all_locations(config, section)

    if update_credentials or not token:
        ui.emit(f"\n{CYAN}{BG}[ QOBUZ-DL - ATUALIZAÇÃO DE CREDENCIAIS ]{OFF}\n")
        new_email = input(f"E-mail atual [{email}]:\n- ").strip()
        if new_email:
            email = new_email
            config.set(section, "email", email)

        ui.emit(f"\n{CYAN}[!] Cole o novo Token de autenticação do seu navegador:{OFF}")
        new_token = input("- ").strip()
        if not new_token:
            ui.warn("Token vazio. Operação cancelada.")
            return False
        token = new_token
        update_credentials = True
    else:
        ui.emit(f"\n\r{CYAN}[*] Consultando dados da conta e assinatura no Qobuz...{OFF}\033[K")

    try:
        client = await Client.create(
            email=email,
            pwd="",
            app_id=app_id,
            secrets=secrets,
            user_auth_token=token,
            force_english=force_english,
        )
    except Exception as e:
        ui.error(f"Falha ao autenticar com a API ({type(e).__name__}).")
        ui.detail(auth_failure_hint(e))
        if update_credentials:
            ui.error("As alterações NÃO foram salvas.")
        return False

    try:
        try:
            user_info = await client.get_user_profile()
            sub_info = client.check_subscription() or {}
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                ui.error("Token de autenticação inválido, expirado ou revogado.")
                ui.detail("Informe um novo token: python3 -m qobuz_dl auth -l")
            else:
                ui.error(f"Falha ao consultar o perfil no Qobuz (HTTP {exc.response.status_code}).")
                ui.detail(auth_failure_hint(exc))
            if update_credentials:
                ui.error("As alterações NÃO foram salvas.")
            return False
        except Exception as exc:
            ui.error(f"Falha ao consultar o perfil no Qobuz ({type(exc).__name__}).")
            ui.detail(auth_failure_hint(exc))
            if update_credentials:
                ui.error("As alterações NÃO foram salvas.")
            return False

        if update_credentials and token:
            _save_token_in_both_config_keys(config, token)
            config.set(section, "email", email)
            config.set(section, "password", "")
            saved_in_keyring = not disable_keyring and _keyring_save("auth_token", token)
            if saved_in_keyring:
                config.set(section, "auth_token", "")
            else:
                config.set(section, "auth_token", token)
                if not disable_keyring:
                    config.set(section, "disable_keyring", "true")
                    ui.warn("Keyring indisponível: será desativado nesta configuração. O token será salvo em texto puro no config.ini.")
            try:
                _write_config_secure(config, config_file)
            except OSError:
                ui.error("Não foi possível gravar o arquivo de configuração.")
                if saved_in_keyring:
                    ui.warn("O Keyring foi atualizado, mas o config.ini não foi salvo.")
                return False
            ui.ok("Credenciais atualizadas e configuração salva!")

        if show_json:
            ui.emit_always(json.dumps(user_info, indent=2, ensure_ascii=False))
            return bool(sub_info.get("is_active", False))

        sf = user_info.get("store_features") or {}
        cred = user_info.get("credential") or {}
        last_update = user_info.get("last_update") or {}
        is_active = bool(sub_info.get("is_active", False))
        status_color = GREEN if is_active else RED
        status_text = str(sub_info.get("status") or "unknown").upper()
        offer_text = _safe_subscription_value(sub_info, "offer", "N/A")
        periodicity_text = str(_safe_subscription_value(sub_info, "periodicity", "N/A")).capitalize()
        start_date = sub_info.get("start_date") or "N/A"
        end_date = sub_info.get("end_date") or "N/A"

        ui.emit("\n" + "=" * 68)
        ui.emit(f" {CYAN}{BG}🎵 QOBUZ // INFORMAÇÕES DA CONTA E ASSINATURA{OFF}")
        ui.emit("=" * 68)

        ui.emit(f"\n {CYAN}[👤 PERFIL DO USUÁRIO]{OFF}")
        nome_completo = (f"{user_info.get('firstname', '')} {user_info.get('lastname', '')}").strip() or "N/A"
        ui.emit(f" • Nome Completo: {nome_completo}")
        ui.emit(f" • Display Name: {user_info.get('display_name', 'N/A')}")
        ui.emit(f" • E-mail: {user_info.get('email', 'N/A')}")
        ui.emit(f" • Login: {user_info.get('login', 'N/A')}")
        ui.emit(f" • ID do Usuário: {user_info.get('id', 'N/A')} [Public ID: {user_info.get('publicId', 'N/A')}]")
        ui.emit(f" • País / Zona: {user_info.get('country', 'N/A')} / {user_info.get('zone', 'N/A')}")
        ui.emit(f" • Loja / Idioma: {user_info.get('store', 'N/A')} ({user_info.get('language_code', 'N/A')})")

        def format_date_br(d_str):
            if not d_str:
                return "N/A"
            try:
                return datetime.strptime(str(d_str)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
            except Exception:
                return str(d_str)

        ui.emit(f" • Nascimento / Idade: {format_date_br(user_info.get('birthdate'))} ({user_info.get('age', 'N/A')} anos, {user_info.get('genre', 'N/A')})")
        ui.emit(f" • Conta Criada em: {format_date_br(user_info.get('creation_date'))}")

        ui.emit(f"\n {CYAN}[💳 STATUS DA SUBSCRIÇÃO (ASSINATURA)]{OFF}")
        ui.emit(f" • Status Atual: {status_color}● {status_text}{OFF}")
        ui.emit(f" • Plano / Oferta: {offer_text}")
        ui.emit(f" • Periodicidade: {periodicity_text}")
        ui.emit(f" • Data de Início: {start_date}")
        ui.emit(f" • Data de Término: {end_date}")
        ui.emit(f" • Cancelamento: {'Sim (Cancelada pelo usuário)' if sub_info.get('is_canceled') else 'Não'}")
        ui.emit(f" • Vagas Família: {sub_info.get('household_size_max', 'N/A')} membro(s)")

        ui.emit(f"\n {CYAN}[🎛️ CREDENCIAL & RECURSOS DA CONTA]{OFF}")
        ui.emit(f" • Tipo de Membro: {cred.get('description', 'Membro Qobuz')}")
        ui.emit(f" • Streaming: {'Disponível' if sf.get('streaming') else 'Indisponível'}")
        ui.emit(f" • Letras (Lyrics): {'Disponível' if sf.get('lyrics') else 'Indisponível'}")
        ui.emit(f" • Importação de Músicas: {'Disponível' if sf.get('music_import') else 'Indisponível'}")
        ui.emit(f" • Rádio / Club / Q: {'Disponível' if sf.get('radio') or sf.get('club') else 'Indisponível'}")

        if last_update:
            ui.emit(f"\n {CYAN}[📊 ATIVIDADES & ÚLTIMAS ATUALIZAÇÕES]{OFF}")
            ui.emit(f" • Playlists: {_format_timestamp(last_update.get('playlist'))}")
            ui.emit(f" • Álbuns Favoritos: {_format_timestamp(last_update.get('favorite_album'))}")
            ui.emit(f" • Faixas Favoritas: {_format_timestamp(last_update.get('favorite_track'))}")
            ui.emit(f" • Artistas Favoritos: {_format_timestamp(last_update.get('favorite_artist'))}")
            ui.emit(f" • Compras na Loja: {_format_timestamp(last_update.get('purchase'))}")

        if not is_active:
            ui.warn("⚠️ AVISO DE ASSINATURA INATIVA:")
            ui.detail(f"Sua assinatura expirou em {end_date}. Para baixar álbuns e faixas completas em alta resolução, é necessário possuir uma conta ativa.")

            if not update_credentials:
                trocar = (
                    input(f"\n{CYAN}[?] Deseja alterar o e-mail e o user_token agora? (s/N): {OFF}")
                    .strip()
                    .lower()
                )
                if trocar in ("s", "sim", "y", "yes"):
                    await client.close()
                    return await _auth_command(config_file, update_credentials=True, show_json=show_json)

        ui.emit("=" * 68 + "\n")
        return is_active
    finally:
        await client.close()


async def _garantir_assinatura_ativa(qobuz: QobuzDL) -> bool:
    sub_info = qobuz.client.check_subscription() or {}

    while not bool(sub_info.get("is_active", False)):
        offer = _safe_subscription_value(sub_info, "offer", "N/A")
        periodicity = str(_safe_subscription_value(sub_info, "periodicity", "N/A")).capitalize()
        ui.error("CONTA SEM ASSINATURA ATIVA NO QOBUZ")
        ui.emit(f" {CYAN}•{OFF} Status Atual: {RED}{sub_info.get('status') or 'N/A'}{OFF}")
        ui.emit(f" {CYAN}•{OFF} Plano: {offer} ({periodicity})")
        ui.emit(f" {CYAN}•{OFF} Validade / Término: {sub_info.get('end_date') or 'N/A'}")
        ui.emit(f" {CYAN}•{OFF} Cancelamento: {'Sim (Cancelada)' if sub_info.get('is_canceled') else 'Não'}")
        ui.warn("ℹ️ Sem uma assinatura ativa, a API da Qobuz não permite o download de faixas completas. É obrigatório informar o e-mail e o user_token de uma conta com assinatura ativa para prosseguir.")

        resp = (
            input(f"\n{CYAN}[?] Atualizar e-mail/user_token agora? (Enter = atualizar, 'cancelar' = sair): {OFF}")
            .strip()
            .lower()
        )
        if resp in ("cancelar", "cancel", "sair", "n", "nao", "não"):
            ui.error("Operação cancelada. Nenhum comando de download roda sem assinatura ativa.")
            ui.detail(f"Para ver os detalhes da conta, use: {GREEN}qobuz-dl auth{OFF}")
            return False

        await _auth_command(CONFIG_FILE, update_credentials=True)

        config = configparser.ConfigParser(interpolation=None)
        config.read(CONFIG_FILE, encoding="utf-8")
        section = "qobuz" if config.has_section("qobuz") else "DEFAULT"
        new_email = config.get(section, "email", fallback="")
        disable_kr = config.getboolean(section, "disable_keyring", fallback=False)
        if disable_kr:
            new_token = config.get(section, "auth_token", fallback="") or config.get(section, "password", fallback="")
        else:
            new_token = _keyring_load("auth_token") or config.get(section, "auth_token", fallback="")

        app_id = config.get(section, "app_id", fallback="")
        secrets = [s for s in config.get(section, "secrets", fallback="").split(",") if s]

        try:
            await qobuz.initialize_client(new_email, new_token, app_id, secrets)
            sub_info = qobuz.client.check_subscription() or {}
        except Exception as e:
            ui.error(f"Falha ao validar a nova conta: {e}")
            sub_info = {
                "is_active": False,
                "status": "erro de autenticação",
                "offer": "N/A",
                "periodicity": "N/A",
                "end_date": None,
                "is_canceled": False,
            }

    return True


if __name__ == "__main__":
    main()