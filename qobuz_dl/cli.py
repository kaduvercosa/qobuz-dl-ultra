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
import dataclasses
import glob
import io
import json
import logging
import os
import signal
import string
import sys
import tempfile
import threading
import time
from datetime import datetime
from typing import Optional

import httpx
import keyring
import send2trash

from qobuz_dl import fuzzy, ui, update_check

# Módulos internos do ecossistema QOBUZ-DL
from qobuz_dl.bundle import Bundle
from qobuz_dl.color import (
    ACCENT_PRESETS,
    ANSI_BOLD,
    ANSI_CLEAR_LINE,
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
            # Limpeza best-effort: o arquivo pode já ter sumido.
            pass
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            # Limpeza best-effort: o arquivo pode já ter sumido.
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
    rgb: str | None = None
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
            # Entrada inválida: tratada como ausente.
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
                # Entrada inválida: tratada como ausente.
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
    """Próxima ação para uma falha de login, de acordo com o tipo do erro.

    Rede fora do ar, token vencido/inválido, assinatura inativa e cofre de
    senhas (keyring) pedem coisas diferentes; uma frase só serviria mal a todos.
    """
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


def _choose_tags_mode(arguments) -> None:
    """Pergunta o que o `tags` deve atualizar quando o usuário não disse (só em terminal).

    Sem terminal interativo (script, cron, pipe) ou com --tags-only/--covers-only/
    --both, não pergunta nada e o padrão (tags e capas) continua valendo.
    """
    if (
        getattr(arguments, "tags_only", False)
        or getattr(arguments, "covers_only", False)
        or getattr(arguments, "both", False)
    ):
        return
    try:
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        interactive = False
    if not interactive:
        return
    ui.emit_always("")
    ui.emit_always("O que atualizar?")
    ui.emit_always("  1) tags e capas   (padrão)")
    ui.emit_always("  2) só tags        (a capa não é pesquisada nem alterada)")
    ui.emit_always("  3) só capas       (nenhuma tag é regravada)")
    try:
        answer = input("Escolha [1-3, Enter = 1]: ").strip()
    except (EOFError, KeyboardInterrupt):
        answer = ""
        ui.emit_always("")
    if answer == "2":
        arguments.tags_only = True
    elif answer == "3":
        arguments.covers_only = True
    elif answer not in ("", "1"):
        ui.warn(f"Opção '{answer}' não reconhecida: usando tags e capas.")


def _handle_tags_report(target_dir: str, destination: str) -> int:
    """`tags --report [ARQUIVO]`: só leitura; grava um HTML/CSV com os problemas."""
    from qobuz_dl import library_report

    if not os.path.isdir(target_dir):
        ui.error(f"A pasta não existe no dispositivo: '{target_dir}'")
        return 1
    ui.step(f"Analisando a biblioteca em {target_dir} (só leitura)...")

    def progress(done: int, total: int) -> None:
        ui.emit(f"  {done}/{total} arquivos lidos")

    records = library_report.scan_directory(target_dir, progress=progress)
    if not records:
        ui.warn("Nenhum arquivo .flac/.mp3 encontrado na pasta.")
        return 1
    try:
        written = library_report.write_report(records, destination, target_dir)
    except OSError as exc:
        ui.error(
            f"Não foi possível gravar o relatório em '{destination}': {exc}. "
            "Escolha outro local, por exemplo: --report ~/relatorio.html"
        )
        return 1
    summary = library_report.summarize(records)
    ui.ok(
        f"{summary['total']} faixas analisadas, {summary['with_issues']} com problemas."
    )
    for code, count in summary["per_issue"].most_common(5):
        ui.emit(f"  • {library_report.ISSUES[code][0]}: {count}")
    ui.ok(f"Relatório salvo em: {written}")
    return 0


def _handle_tags_undo(arguments, base_dir: str) -> int:
    """`tags --undo [ID]` e `tags --undo-list`. Não consulta o Qobuz. Devolve o exit code."""
    from qobuz_dl import tags_journal

    runs = tags_journal.list_runs(base_dir)

    if getattr(arguments, "undo_list", False):
        if not runs:
            ui.warn("Nenhuma execução do tags guardada para desfazer.")
            return 0
        ui.step("Execuções do tags que podem ser desfeitas (da mais recente):")
        for run in runs:
            note = " (interrompida no meio da gravação)" if run["pending"] else ""
            ui.emit(
                f"  {run['id']}  modo={run['mode']}  "
                f"{run['files']} arquivo(s)  pasta={run['root']}{note}"
            )
        ui.emit(
            "  Para desfazer: qobuz-dl tags --undo [ID]   (sem ID = a mais recente)"
        )
        return 0

    run_id = getattr(arguments, "undo", None)
    dry_run = bool(getattr(arguments, "dry_run", False))
    force = bool(getattr(arguments, "undo_force", False))

    def on_file(status: str, path: str) -> None:
        text, level = _UNDO_STATUS_TEXT.get(status, (status, None))
        line = f"{os.path.basename(path)}: {text}"
        if level == "warn":
            ui.warn(line)
        elif level == "error":
            ui.error(line)
        else:
            ui.emit(f"  {line}")

    if not runs:
        ui.error("Nenhuma execução do tags guardada: não há o que desfazer.")
        return 1
    ui.step(
        "Simulando o desfazer (nada será gravado)..."
        if dry_run
        else "Desfazendo a execução do tags..."
    )
    result = tags_journal.undo_run(
        base_dir, run_id=run_id, force=force, dry_run=dry_run, on_file=on_file
    )
    if result is None:
        ui.error(
            f"Execução '{run_id}' não encontrada. Veja as disponíveis com: "
            "qobuz-dl tags --undo-list"
        )
        return 1

    ui.emit("")
    ui.emit(f"Execução {result['run']}:")
    if dry_run:
        ui.emit(f"  seriam restaurados : {result['would_restore']}")
    else:
        ui.emit(f"  restaurados        : {result['restored']}")
    ui.emit(f"  alterados depois   : {result['changed_since']}")
    ui.emit(f"  não encontrados    : {result['missing']}")
    ui.emit(f"  falhas             : {result['errors'] + result['verify_failed']}")
    return 1 if (result["errors"] or result["verify_failed"]) else 0


def _purge_databases(config_path: str, downloads_db: str) -> list[str]:
    """Apaga TODOS os bancos SQLite do programa (``--purge``).

    Bancos: ``qobuz_dl.db`` (IDs já baixados) e ``library.db`` (catálogo de
    favoritos/estado de download), mais os arquivos auxiliares do SQLite
    (``-journal``, ``-wal``, ``-shm``) que podem ficar ao lado deles.
    Devolve os caminhos realmente removidos.
    """
    from qobuz_dl.library_cmd import library_db_path

    removed: list[str] = []
    for db_path in (downloads_db, library_db_path(config_path)):
        for suffix in ("", "-journal", "-wal", "-shm"):
            path = db_path + suffix
            try:
                os.remove(path)
            except FileNotFoundError:
                # Limpeza best-effort: o arquivo pode já ter sumido.
                continue
            removed.append(path)
    return removed


async def _reset_config(config_file: str):
    """Run the interactive configuration wizard and save the result to config_file."""
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

    ui.emit(
        f"\n{C_ACCENT}[!] ATENÇÃO: A API Qobuz bloqueou login direto por senha para apps de terceiros.{OFF}"
    )
    ui.emit(
        f"{C_ACCENT}[!] Obtenha seu Token no navegador (F12 > Application/Storage > Local Storage > token).{OFF}"
    )

    auth_token = input("Cole o token do seu navegador aqui:\n- ").strip()

    # CORREÇÃO: Salva em user_token E user_auth_token
    config["qobuz"]["password"] = ""
    config["qobuz"]["user_token"] = auth_token
    config["qobuz"]["user_auth_token"] = auth_token

    ui.emit(f"\n{C_ACCENT}[?] Armazenamento de Senhas (OS Keyring):{OFF}")
    ui.emit(
        " Por padrão, os tokens são guardados criptografados no cofre do sistema operacional."
    )
    ui.emit(
        " Em ambientes sem interface gráfica (Linux headless, Docker, NAS), isso pode falhar."
    )

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

    #### MUDANÇAS COMEÇAM AQUI
    # ------------------------------------------------------------------
    # Credenciais da API: precisam existir antes de criar o cliente.
    # ------------------------------------------------------------------
    logging.info(
        f"\n{C_ACCENT}Obtendo credenciais da API via [bundle.js]. "
        f"Por favor, aguarde...{OFF}"
    )

    bundle = await Bundle.create()
    fetched_app_id = str(bundle.get_app_id())
    fetched_secrets = [s for s in bundle.get_secrets().values() if s]

    config["qobuz"]["app_id"] = fetched_app_id
    config["qobuz"]["secrets"] = ",".join(fetched_secrets)

    logging.info(f"{GREEN}[+] app_id e secrets obtidos com sucesso.{OFF}")

    # ------------------------------------------------------------------
    # Login e idioma padrão da conta.
    # ------------------------------------------------------------------
    account_language = "pt"
    account_language_detected = False

    logging.info(
        f"\n{C_ACCENT}Testando login com o e-mail e o token informados...{OFF}"
    )

    from qobuz_dl.qopy import Client as _Client

    try:
        _test_client = await _Client.create(
            email,
            "",
            fetched_app_id,
            fetched_secrets,
            user_auth_token=auth_token,
        )

        try:
            # O login normalmente já consulta e armazena o perfil.
            account_info = getattr(_test_client, "user_info", {}) or {}

            if isinstance(account_info, dict):
                account_info = account_info.get("user", account_info)

            if not isinstance(account_info, dict):
                account_info = {}

            language_code = account_info.get("language_code")

            # Só repete a consulta se o perfil carregado não trouxe idioma.
            if not isinstance(language_code, str) or not language_code.strip():
                try:
                    raw_account_info = await _test_client.api_call(
                        "user/get",
                        _request_timeout=httpx.Timeout(8.0, connect=3.0),
                        _max_attempts=1,
                    )

                    if isinstance(raw_account_info, dict):
                        account_info = raw_account_info.get("user", raw_account_info)
                        if isinstance(account_info, dict):
                            language_code = account_info.get("language_code")
                except Exception:
                    # A detecção de idioma não deve impedir a configuração.
                    language_code = None

            if isinstance(language_code, str) and language_code.strip():
                account_language = language_code.strip().lower()
                account_language_detected = True

        finally:
            await _test_client.close()

        logging.info(
            f"{GREEN}[+] Login realizado com sucesso! "
            f"Suas credenciais estão corretas.{OFF}"
        )

    except Exception as exc:
        logging.info(
            f"{RED}[!] Falha ao logar com este e-mail/token: {exc}{OFF}\n"
            f"{YELLOW}[!] A configuração foi salva mesmo assim -- "
            f"confira seu e-mail/token e rode 'qdl -r' de novo "
            f"(ou 'qdl auth -l') se precisar corrigi-los.{OFF}"
        )

    if account_language_detected:
        ui.emit(f"\n{GREEN}[+] Idioma padrão da conta Qobuz: {account_language}{OFF}")
    else:
        ui.emit(
            f"\n{YELLOW}[!] Não foi possível detectar o idioma da conta. "
            f"Usando o padrão: {account_language}{OFF}"
        )

    # Guarda o idioma da conta separado de uma possível escolha manual.
    config["qobuz"]["account_language"] = account_language

    # ------------------------------------------------------------------
    # Letras nos arquivos de áudio.
    # ------------------------------------------------------------------
    fetch_lyrics = (
        input(
            "\nBaixar e embutir letras nos arquivos de áudio? (s/n) [Padrão: sim]\n- "
        )
        .strip()
        .lower()
    )
    want_lyrics = fetch_lyrics not in {"n", "nao", "não", "no", "false", "0"}

    config["qobuz"]["fetch_lyrics"] = "true" if want_lyrics else "false"
    config["qobuz"]["embed_lyrics"] = "true" if want_lyrics else "false"

    # Mantém a possibilidade de salvar arquivos externos separadamente.
    save_lrc = False

    if want_lyrics:
        lrc_choice = (
            input(
                "\nSalvar também arquivos externos de letras (.lrc/.txt)? "
                "(s/n) [Padrão: não]\n- "
            )
            .strip()
            .lower()
        )
        save_lrc = lrc_choice in {"s", "sim", "y", "yes", "true", "1"}

    config["qobuz"]["no_lrc_files"] = "false" if save_lrc else "true"

    # ------------------------------------------------------------------
    # Traduções: pergunta separada, usando o idioma da conta como padrão.
    # ------------------------------------------------------------------
    # Traduções usam automaticamente o idioma da conta Qobuz.
    want_translations = False

    if want_lyrics:
        translation_choice = (
            input(
                "\nDeseja também incluir traduções das letras "
                f"em {account_language}? "
                "(s/n) [Padrão: sim]\n- "
            )
            .strip()
            .lower()
        )

        want_translations = translation_choice not in {
            "n",
            "nao",
            "não",
            "no",
            "false",
            "0",
        }

    config["qobuz"]["fetch_translation"] = "true" if want_translations else "false"
    config["qobuz"]["lyrics_translation_lang"] = account_language

    # ------------------------------------------------------------------
    # Genius como fallback de letras.
    # ------------------------------------------------------------------
    genius_token = ""

    if want_lyrics:
        ui.emit(
            f"\n{C_ACCENT}[!] Para usar o Genius como fallback, "
            f"insira seu API Token "
            f"(Enter para pular):{OFF}"
        )
        genius_token = input("Genius API Token:\n- ").strip()

    if use_keyring and _keyring_save("genius_token", genius_token):
        config["qobuz"]["genius_token"] = ""
    else:
        config["qobuz"]["genius_token"] = genius_token

    # ------------------------------------------------------------------
    # Pasta, formato e qualidade.
    # ------------------------------------------------------------------
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
            "\nQualidade "
            "(5:MP3 320k, 6:FLAC 16-bit, "
            "7:Hi-Res 24b<=96kHz, 27:Hi-Res Max) "
            "[Padrão 27]\n- "
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

    # no_lrc_files e embed_lyrics já foram definidos pelas respostas acima.
    # Não os sobrescreva aqui com valores fixos.

    config["qobuz"]["multi_value_tags"] = "false"
    config["qobuz"]["continuous_track_numbers"] = "false"
    config["qobuz"]["legacy_charmap"] = "false"
    config["qobuz"]["blacklist"] = "blacklist.txt"
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
    config["qobuz"]["multiple_disc_track_format"] = (
        "{disc_number}.{track_number} - {track_title}"
    )
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
    ui.emit("\r" + ANSI_CLEAR_LINE, end="")
    _print_welcome_screen()


def _remove_leftovers(directory: str):
    """Remove temporary download files from the specified directory."""
    for pattern in [".*.tmp", "~tmp_*.tmp"]:
        search_dir = os.path.join(directory, "**", pattern)
        for i in glob.glob(search_dir, recursive=True):
            try:
                send2trash.send2trash(i)
            except Exception as e:
                logger.debug(
                    f"Falha ao mover arquivo temporário '{i}' para a lixeira: {e}"
                )


def _format_timestamp(ts: Optional[int]) -> str:
    """Convert Unix timestamp to Brazilian date format (DD/MM/YYYY HH:MM:SS)."""
    if not ts:
        return "N/A"
    try:
        return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(ts)


def _formatar_valor(valor):
    """Normaliza um valor bruto da API para exibição legível."""
    if valor in (None, ""):
        return "N/A"
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    return valor


def _imprimir_campos_extras(
    dados: dict, titulo: str, ja_mostrados: set, indent: str = " "
):
    """Imprime campos extras do JSON da API."""
    extras = {k: v for k, v in dados.items() if k not in ja_mostrados}
    if not extras:
        return
    ui.emit(f"\n {CYAN}[{titulo}]{OFF}")
    for chave, valor in extras.items():
        if isinstance(valor, dict):
            if not valor:
                ui.emit(f"{indent}• {chave}: (vazio)")
                continue
            ui.emit(f"{indent}• {chave}:")
            for sub_chave, sub_valor in valor.items():
                ui.emit(f"{indent} - {sub_chave}: {_formatar_valor(sub_valor)}")
        elif isinstance(valor, list):
            if not valor:
                ui.emit(f"{indent}• {chave}: (vazio)")
                continue
            ui.emit(f"{indent}• {chave}:")
            for i, item in enumerate(valor):
                if isinstance(item, dict):
                    resumo = ", ".join(
                        f"{k}={_formatar_valor(v)}" for k, v in item.items()
                    )
                    ui.emit(f"{indent} [{i}] {resumo}")
                else:
                    ui.emit(f"{indent} - {_formatar_valor(item)}")
        else:
            ui.emit(f"{indent}• {chave}: {_formatar_valor(valor)}")


async def _auth_command(
    config_file: str, update_credentials: bool = False, show_json: bool = False
):
    """Subcomando unificado para gerenciar conta e assinatura."""
    from qobuz_dl.qopy import Client

    if not os.path.isfile(config_file):
        ui.error(
            "Arquivo de configuração não encontrado. Execute 'qobuz-dl -r' primeiro."
        )
        return False

    config = configparser.ConfigParser(interpolation=None)
    config.read(config_file, encoding="utf-8")
    section = "qobuz" if config.has_section("qobuz") else "DEFAULT"

    email = config.get(section, "email", fallback="")
    app_id = config.get(section, "app_id", fallback="")
    secrets = [s for s in config.get(section, "secrets", fallback="").split(",") if s]
    force_english = not config.getboolean(section, "native_lang", fallback=False)
    disable_keyring = config.getboolean(section, "disable_keyring", fallback=False)

    # CORREÇÃO: Lê token de todas as chaves possíveis
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
        ui.emit(
            f"\n\r{CYAN}[*] Consultando dados da conta e assinatura no Qobuz...{OFF}{ANSI_CLEAR_LINE}"
        )

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

    user_info: dict | None = None
    sub_info: dict | None = None

    try:
        try:
            user_info = await client.get_user_profile()
            sub_info = client.check_subscription()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                ui.error("Token de autenticação inválido, expirado ou revogado.")
                ui.detail("Informe um novo token: python3 -m qobuz_dl auth -l")
            else:
                ui.error(
                    "Falha ao consultar o perfil no Qobuz "
                    f"(HTTP {exc.response.status_code})."
                )
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

        if user_info is None or sub_info is None:
            ui.error(
                "A consulta não retornou os dados necessários da conta e da assinatura."
            )
            return False

        if update_credentials and token:
            _save_token_in_both_config_keys(config, token)
            config.set(section, "email", email)
            config.set(section, "password", "")
            saved_in_keyring = not disable_keyring and _keyring_save(
                "auth_token", token
            )
            if saved_in_keyring:
                config.set(section, "auth_token", "")
            else:
                config.set(section, "auth_token", token)
                if not disable_keyring:
                    config.set(section, "disable_keyring", "true")
                    ui.warn(
                        "Keyring indisponível: será desativado nesta configuração. "
                        "O token será salvo em texto puro no config.ini."
                    )
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
            return sub_info.get("is_active", False)

        sf = user_info.get("store_features") or {}
        cred = user_info.get("credential") or {}
        last_update = user_info.get("last_update") or {}
        status_color = GREEN if sub_info.get("is_active") else RED

        ui.emit("\n" + "=" * 68)
        ui.emit(f" {CYAN}{BG}🎵 QOBUZ // INFORMAÇÕES DA CONTA E ASSINATURA{OFF}")
        ui.emit("=" * 68)

        ui.emit(f"\n {CYAN}[👤 PERFIL DO USUÁRIO]{OFF}")
        nome_completo = (
            f"{user_info.get('firstname', '')} {user_info.get('lastname', '')}"
        ).strip() or "N/A"
        ui.emit(f" • Nome Completo: {nome_completo}")
        ui.emit(f" • Display Name: {user_info.get('display_name', 'N/A')}")
        ui.emit(f" • E-mail: {user_info.get('email', 'N/A')}")
        ui.emit(f" • Login: {user_info.get('login', 'N/A')}")
        ui.emit(
            f" • ID do Usuário: {user_info.get('id', 'N/A')} [Public ID: {user_info.get('publicId', 'N/A')}]"
        )
        ui.emit(
            f" • País / Zona: {user_info.get('country', 'N/A')} / {user_info.get('zone', 'N/A')}"
        )
        ui.emit(
            f" • Loja / Idioma: {user_info.get('store', 'N/A')} ({user_info.get('language_code', 'N/A')})"
        )

        def format_date_br(d_str):
            if not d_str:
                return "N/A"
            try:
                return datetime.strptime(str(d_str)[:10], "%Y-%m-%d").strftime(
                    "%d/%m/%Y"
                )
            except Exception:
                return str(d_str)

        ui.emit(
            f" • Nascimento / Idade: {format_date_br(user_info.get('birthdate'))} ({user_info.get('age', 'N/A')} anos, {user_info.get('genre', 'N/A')})"
        )
        ui.emit(f" • Conta Criada em: {format_date_br(user_info.get('creation_date'))}")

        ui.emit(f"\n {CYAN}[💳 STATUS DA SUBSCRIÇÃO (ASSINATURA)]{OFF}")
        ui.emit(
            f" • Status Atual: {status_color}● {str(sub_info.get('status')).upper()}{OFF}"
        )
        ui.emit(f" • Plano / Oferta: {sub_info.get('offer', 'N/A')}")
        ui.emit(
            f" • Periodicidade: {str(sub_info.get('periodicity', 'N/A')).capitalize()}"
        )
        ui.emit(f" • Data de Início: {sub_info.get('start_date') or 'N/A'}")
        ui.emit(f" • Data de Término: {sub_info.get('end_date') or 'N/A'}")
        ui.emit(
            f" • Cancelamento: {'Sim (Cancelada pelo usuário)' if sub_info.get('is_canceled') else 'Não'}"
        )
        ui.emit(f" • Vagas Família: {sub_info.get('household_size_max')} membro(s)")

        ui.emit(f"\n {CYAN}[🎛️ CREDENCIAL & RECURSOS DA CONTA]{OFF}")
        ui.emit(f" • Tipo de Membro: {cred.get('description', 'Membro Qobuz')}")
        ui.emit(
            f" • Streaming: {'Disponível' if sf.get('streaming') else 'Indisponível'}"
        )
        ui.emit(
            f" • Letras (Lyrics): {'Disponível' if sf.get('lyrics') else 'Indisponível'}"
        )
        ui.emit(
            f" • Importação de Músicas: {'Disponível' if sf.get('music_import') else 'Indisponível'}"
        )
        ui.emit(
            f" • Rádio / Club / Q: {'Disponível' if sf.get('radio') or sf.get('club') else 'Indisponível'}"
        )

        if last_update:
            ui.emit(f"\n {CYAN}[📊 ATIVIDADES & ÚLTIMAS ATUALIZAÇÕES]{OFF}")
            ui.emit(f" • Playlists: {_format_timestamp(last_update.get('playlist'))}")
            ui.emit(
                f" • Álbuns Favoritos: {_format_timestamp(last_update.get('favorite_album'))}"
            )
            ui.emit(
                f" • Faixas Favoritas: {_format_timestamp(last_update.get('favorite_track'))}"
            )
            ui.emit(
                f" • Artistas Favoritos: {_format_timestamp(last_update.get('favorite_artist'))}"
            )
            ui.emit(
                f" • Compras na Loja: {_format_timestamp(last_update.get('purchase'))}"
            )

        if not sub_info.get("is_active"):
            ui.warn("⚠️ AVISO DE ASSINATURA INATIVA:")
            ui.detail(
                f"Sua assinatura expirou em {sub_info.get('end_date')}. Para baixar álbuns e faixas completas em alta resolução, é necessário possuir uma conta ativa."
            )

            if not update_credentials:
                trocar = (
                    input(
                        f"\n{CYAN}[?] Deseja alterar o e-mail e o user_token agora? (s/N): {OFF}"
                    )
                    .strip()
                    .lower()
                )
                if trocar in ("s", "sim", "y", "yes"):
                    await client.close()
                    return await _auth_command(
                        config_file, update_credentials=True, show_json=show_json
                    )

        ui.emit("=" * 68 + "\n")

        return sub_info.get("is_active", False)
    finally:
        await client.close()


async def _garantir_assinatura_ativa(qobuz: QobuzDL) -> bool:
    """Bloqueia a execução até ter uma conta com assinatura ativa."""
    sub_info = qobuz.client.check_subscription()

    while not sub_info.get("is_active"):
        ui.error("CONTA SEM ASSINATURA ATIVA NO QOBUZ")
        ui.emit(f" {CYAN}•{OFF} Status Atual: {RED}{sub_info.get('status')}{OFF}")
        ui.emit(
            f" {CYAN}•{OFF} Plano: {sub_info.get('offer', 'N/A')} ({str(sub_info.get('periodicity', 'N/A')).capitalize()})"
        )
        ui.emit(
            f" {CYAN}•{OFF} Validade / Término: {sub_info.get('end_date') or 'N/A'}"
        )
        ui.emit(
            f" {CYAN}•{OFF} Cancelamento: {'Sim (Cancelada)' if sub_info.get('is_canceled') else 'Não'}"
        )
        ui.warn(
            "ℹ️ Sem uma assinatura ativa, a API da Qobuz não permite o download de faixas completas. É obrigatório informar o e-mail e o user_token de uma conta com assinatura ativa para continuar."
        )

        resp = (
            input(
                f"\n{CYAN}[?] Atualizar e-mail/user_token agora? (Enter = atualizar, 'cancelar' = sair): {OFF}"
            )
            .strip()
            .lower()
        )
        if resp in ("cancelar", "cancel", "sair", "n", "nao", "não"):
            ui.error(
                "Operação cancelada. Nenhum comando de download roda sem assinatura ativa."
            )
            ui.detail(f"Para ver os detalhes da conta, use: {GREEN}qobuz-dl auth{OFF}")
            return False

        await _auth_command(CONFIG_FILE, update_credentials=True)

        config = configparser.ConfigParser(interpolation=None)
        config.read(CONFIG_FILE, encoding="utf-8")
        section = "qobuz" if config.has_section("qobuz") else "DEFAULT"
        new_email = config.get(section, "email", fallback="")
        disable_kr = config.getboolean(section, "disable_keyring", fallback=False)
        if disable_kr:
            new_token = config.get(section, "auth_token", fallback="") or config.get(
                section, "password", fallback=""
            )
        else:
            new_token = _keyring_load("auth_token") or config.get(
                section, "auth_token", fallback=""
            )

        app_id = config.get(section, "app_id", fallback="")
        secrets = [
            s for s in config.get(section, "secrets", fallback="").split(",") if s
        ]

        try:
            await qobuz.initialize_client(new_email, new_token, app_id, secrets)
            sub_info = qobuz.client.check_subscription()
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


async def _handle_commands(qobuz: QobuzDL, arguments):
    """Roteia argumentos para o comando apropriado."""

    def sigint_handler(sig, frame):
        ui.error("Download interrompido manualmente pelo usuário.")
        ui.warn("Arquivos parciais foram enviados para a lixeira.")
        try:
            _remove_leftovers(qobuz.directory)
        except Exception as e:
            logger.debug(f"Falha ao limpar arquivos parciais no CTRL+C: {e}")
        sys.exit(1)

    signal.signal(signal.SIGINT, sigint_handler)

    DOWNLOAD_COMMANDS = {
        "dl",
        "lucky",
        "interactive",
        "i",
        "fun",
        "sync-playlist",
        "sp",
        "import-playlist",
        "ip",
        "sync-favorites",
        "sf",
    }

    if arguments.command in DOWNLOAD_COMMANDS or arguments.command is None:
        if not await _garantir_assinatura_ativa(qobuz):
            return

    try:
        if arguments.command == "dl":
            await qobuz.download_list_of_urls(arguments.SOURCE)
        elif arguments.command in ("sync-playlist", "sp"):
            from qobuz_dl.playlist_links import playlist_root
            from qobuz_dl.sync_playlist import sync_playlist

            await sync_playlist(
                qobuz,
                arguments.URL,
                playlist_root(qobuz.directory),
                auto_confirm=arguments.yes,
            )
        elif arguments.command in ("sync-favorites", "sf"):
            from qobuz_dl.library_cmd import cmd_sync_favorites

            rc = await cmd_sync_favorites(arguments, qobuz)
            if rc:
                sys.exit(rc)
        elif arguments.command in ("artists", "artist-review"):
            from qobuz_dl.artist_review import cmd_artist_review

            rc = await cmd_artist_review(arguments, qobuz)
            if rc:
                sys.exit(rc)
        elif arguments.command == "lucky":
            query = " ".join(arguments.QUERY)
            qobuz.lucky_type = arguments.type
            qobuz.lucky_limit = arguments.number
            await qobuz.lucky_mode(query)
        elif arguments.command in ("import-playlist", "ip"):
            await qobuz.import_playlist_from_url_or_file(
                source=arguments.SOURCE,
                name=str(getattr(arguments, "name", "") or ""),
                auto=getattr(arguments, "auto", False),
            )
        else:
            if hasattr(arguments, "limit"):
                qobuz.interactive_limit = arguments.limit
            await qobuz.interactive()
    except KeyboardInterrupt:
        # O downloader marca a pasta como [INCOMPLETE] antes de propagar a
        # interrupção. Repassar o sinal permite que main() devolva o código
        # 1 sem usar os._exit(), preservando o fechamento dos clientes HTTP.
        raise
    finally:
        _remove_leftovers(qobuz.directory)


_LOGO_FONT = {
    "A": ["01110", "10001", "11111", "10001", "10001"],
    "B": ["11110", "10001", "11110", "10001", "11110"],
    "D": ["11110", "10001", "10001", "10001", "11110"],
    "L": ["10000", "10000", "10000", "10000", "11111"],
    "O": ["01110", "10001", "10001", "10001", "01110"],
    "Q": ["01110", "10001", "10101", "10011", "01111"],
    "R": ["11110", "10001", "11110", "10100", "10010"],
    "T": ["11111", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "01110"],
    "Z": ["11111", "00010", "00100", "01000", "11111"],
    "-": ["00000", "00000", "11111", "00000", "00000"],
}
_LOGO_BLOCK = "\u2588"


def _render_logo_word(word: str):
    letters = list(word)
    rows = ["" for _ in range(5)]
    for i, ch in enumerate(letters):
        glyph = _LOGO_FONT[ch]
        for r in range(5):
            rows[r] += "".join(_LOGO_BLOCK if px == "1" else " " for px in glyph[r])
            if i != len(letters) - 1:
                rows[r] += " "
    return rows


def _print_logo(cols: int):
    line1 = _render_logo_word("QOBUZ-DL")
    line2 = _render_logo_word("ULTRA")
    art_width = len(line1[0])
    if cols >= art_width + 5:
        pad1 = " " * max((cols - art_width) // 2, 0)
        pad2 = " " * max((cols - len(line2[0])) // 2, 0)
        for row in line1:
            ui.emit_always(f"{CYAN}{pad1}{row}{OFF}")
        for row in line2:
            ui.emit_always(f"{CYAN}{pad2}{row}{OFF}")
    else:
        line_short = _render_logo_word("QDL")
        pad_short = " " * max((cols - len(line_short[0])) // 2, 0)
        for row in line_short:
            ui.emit_always(f"{CYAN}{pad_short}{row}{OFF}")


def _extract_subcommands(parser: argparse.ArgumentParser):
    subparsers_action = None
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subparsers_action = action
            break
    if subparsers_action is None:
        return []
    result = []
    for choice_action in subparsers_action._choices_actions:
        primary = choice_action.dest
        subparser = subparsers_action.choices[primary]
        aliases = [
            name
            for name, sp in subparsers_action.choices.items()
            if sp is subparser and name != primary
        ]
        alias_str = ", ".join(aliases) if aliases else None
        result.append((primary, alias_str, choice_action.help))
    return result


def _extract_global_flags(parser: argparse.ArgumentParser):
    result = []
    for action in parser._actions:
        if isinstance(action, (argparse._HelpAction, argparse._SubParsersAction)):
            continue
        flag_str = ", ".join(action.option_strings)
        if action.nargs == "?" and action.metavar:
            flag_str += f" [{action.metavar}]"
        result.append((flag_str, action.dest, action.help))
    return result


_COMMAND_DESCRIPTIONS_PT = {
    "dl": "Baixa por URL de álbum, faixa, artista, gravadora ou playlist do Qobuz, ou um arquivo de texto com uma lista dessas URLs.",
    "interactive": "Busca interativa: procura faixas/álbuns e escolhe o que baixar na hora.",
    "lucky": "Baixa os N primeiros resultados de uma busca no Qobuz, sem passar URL.",
    "lyrics": "Varre uma pasta já baixada e injeta letras/traduções que estejam faltando.",
    "tags": "Varre uma pasta já baixada, regrava/corrige as tags de metadados (com ou sem multi-tags) e troca a capa pela da Apple/iTunes quando validada. Escolha: --tags-only (só tags), --covers-only (só capas) ou o padrão (os dois). Use --dry-run para simular, --undo para desfazer, --resume/--incremental em bibliotecas grandes e --report para um relatório da pasta.",
    "sync-playlist": "Sincroniza uma pasta local com uma playlist do Qobuz (baixa o que falta, remove o que saiu).",
    "sync-favorites": "Sincroniza os favoritos da conta com o catálogo local (novos/removidos) e baixa o que falta.",
    "artists": "Lista artistas seguidos em ordem alfabética ou sorteia um para você conferir no Qobuz e marcar como revisado.",
    "scan": "Casa sua pasta de música com o catálogo (tag de ID, UPC, nome, fuzzy) e grava sentinelas.",
    "library": "Status e manutenção do catálogo local (missing, history, reconcile, reset-stuck).",
    "doctor": "Diagnostica ambiente, config, bancos e sentinelas (somente leitura).",
    "import-playlist": "Importa um arquivo de playlist (TXT, CSV, JSON) de qualquer plataforma para download.",
    "stats": "Mostra estatísticas detalhadas sobre sua biblioteca e downloads efetuados.",
    "inspect": "Mostra tudo de uma faixa local: dados técnicos, tags por assunto, capas, letras, diagnóstico e autenticidade (ou resume uma pasta).",
    "auth": "Exibe status da conta/assinatura ou atualiza credenciais de login e token.",
    "user": "Exibe informações da conta, status da assinatura e dados do perfil.",
}

_FLAG_DESCRIPTIONS_PT = {
    "reset": "cria/reseta o arquivo de configuração",
    "purge": "apaga o banco de downloads-já-feitos",
    "sync_db": "escaneia uma pasta local pra recuperar IDs do Qobuz perdidos no banco",
    "find_duplicates": "acha faixas duplicadas por fingerprint de áudio (Chromaprint), não só tag",
    "watch": "observa uma pasta e roda retro-tagging sozinho quando chegam arquivos novos",
    "show_config": "mostra a configuração atual",
}


def _start_update_probe(timeout: float = 1.2):
    """Começa a consultar o PyPI em segundo plano; devolve `esperar()`.

    A consulta roda enquanto a tela inicial é montada, então quase não soma
    tempo. `esperar()` devolve a versão nova (texto) ou None -- sem rede,
    timeout, checagem desligada ou já atualizado, a tela sai igual a antes.
    """
    if update_check.desativada():
        return lambda: None
    achado: list = []

    def _consultar():
        try:
            achado.append(update_check.nova_versao_disponivel(timeout=timeout))
        except Exception as e:
            logger.debug("Checagem de atualização falhou: %s: %s", type(e).__name__, e)

    worker = threading.Thread(target=_consultar, daemon=True)
    worker.start()

    def _esperar():
        worker.join(timeout + 0.3)
        return achado[0] if achado else None

    return _esperar


def _print_welcome_screen():
    from qobuz_dl import __version__
    from qobuz_dl.home_screen import render_aviso_update, render_home

    esperar_update = _start_update_probe()
    cols = ui.width()
    ui.emit_always("")
    _print_logo(cols)
    version_line = f"v{__version__}"
    pad_version = " " * max((cols - len(version_line)) // 2, 0)
    nova_versao = esperar_update()
    aviso = ""
    if nova_versao:
        negrito_aviso = ui.c(ANSI_BOLD)
        aviso = "\n" + render_aviso_update(
            __version__,
            nova_versao,
            largura=cols,
            cor=bool(negrito_aviso),
            destaque=ACCENT,
            mudo=MUTED,
            reset=RESET,
            negrito=negrito_aviso,
            comando=update_check.COMANDO_ATUALIZAR,
        )
    ui.emit_always(f"{RESET}{pad_version}{version_line}{aviso}\n")
    parser = qobuz_dl_args()

    comandos = []
    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    for name, aliases, help_text in _extract_subcommands(parser):
        usage = None
        if sub_action is not None:
            raw = getattr(sub_action.choices.get(name), "usage", None) or ""
            usage = raw.replace("qobuz-dl ", "", 1) or None
        comandos.append(
            {
                "nome": name,
                "aliases": aliases,
                "desc": _COMMAND_DESCRIPTIONS_PT.get(name, help_text or ""),
                "uso": usage,
            }
        )

    flags = []
    for flag_str, dest, help_text in _extract_global_flags(parser):
        flags.append((flag_str, _FLAG_DESCRIPTIONS_PT.get(dest, help_text or "")))

    negrito = ui.c(ANSI_BOLD)
    ui.emit_always(
        render_home(
            comandos,
            flags,
            largura=cols,
            cor=bool(negrito),
            destaque=ACCENT,
            mudo=MUTED,
            reset=RESET,
            negrito=negrito,
        )
    )
    ui.emit_always("")


def _print_command_help(argv: list) -> bool:
    """`qobuz-dl <comando> -h` em painéis. False se não achou o comando (usa o argparse).

    A tela é montada por `ColoredArgumentParser.print_help` a partir do parser
    real, então um comando ou flag novo aparece sozinho.
    """
    parser = qobuz_dl_args()
    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    command = next((a for a in argv if not a.startswith("-")), None)
    if sub_action is None or command not in sub_action.choices:
        return False
    ui.emit_always("")
    sub_action.choices[command].print_help()
    ui.emit_always("")
    return True


async def _initial_checks():
    if not os.path.isdir(CONFIG_PATH) or not os.path.isfile(CONFIG_FILE):
        os.makedirs(CONFIG_PATH, exist_ok=True)
        if "-r" not in sys.argv and "--reset" not in sys.argv:
            await _reset_config(CONFIG_FILE)
    # Ajuda em painéis, onde quer que -h/--help apareça na linha de comando:
    # sem comando (`qobuz-dl -h`, `qobuz-dl -v --help`, `--log-level debug -h`)
    # mostra a tela geral; com comando (`qobuz-dl tags -h`) mostra a do comando.
    if len(sys.argv) < 2:
        _print_welcome_screen()
        sys.exit(0)
    if any(arg in ("-h", "--help") for arg in sys.argv[1:]):
        command = _find_command(sys.argv[1:])
        if command is None:
            _print_welcome_screen()
            sys.exit(0)
        if _print_command_help([command]):
            sys.exit(0)


def _find_command(argv: list):
    """Primeiro argumento que é o nome (ou apelido) de um subcomando; None se não há."""
    parser = qobuz_dl_args()
    sub_action = next(
        (a for a in parser._actions if isinstance(a, argparse._SubParsersAction)),
        None,
    )
    names = set(sub_action.choices) if sub_action is not None else set()
    return next((arg for arg in argv if arg in names), None)


def _config_directory_if_exists() -> Optional[str]:
    """A pasta `directory` do config.ini, se estiver configurada e existir.

    Para os comandos locais que rodam antes do login (como o `inspect`): sem
    pasta no comando, usam a pasta padrão do config.ini, igual aos demais.
    None -> o chamador usa o próprio padrão (ex.: detecção pelo dispositivo).
    """
    from qobuz_dl import doctor

    configured = doctor.resolve_directory(CONFIG_FILE)
    if not configured:
        return None
    path = os.path.expanduser(configured)
    if IOS_HOME and not os.path.isabs(path):
        path = os.path.join(IOS_HOME, path)
    return path if os.path.isdir(path) else None


def check_for_updates():
    try:
        from qobuz_dl import __version__

        if update_check.desativada():
            return
        latest_version_str = update_check.nova_versao_disponivel(__version__)
        if latest_version_str:
            ui.warn(
                f"ATUALIZAÇÃO DISPONÍVEL: Ultra Edition v{latest_version_str} está disponível!"
            )
            ui.detail(f"- PyPI: rode '{update_check.COMANDO_ATUALIZAR}'")
            ui.detail("- Docker: puxe a imagem mais recente")
    except Exception as e:
        logger.debug("Checagem de atualização falhou: %s: %s", type(e).__name__, e)


@dataclasses.dataclass
class CliConfig:
    """Valores lidos do config.ini (já com os padrões usados quando faltam)."""

    section: str = "DEFAULT"
    email: str = ""
    password: str = ""
    token: str = ""
    genius_token: str = ""
    app_id: str = ""
    secrets: list[str] = dataclasses.field(default_factory=list)
    force_english: bool = True
    default_folder: str = "Qobuz Downloads"
    default_quality: str = "6"
    default_limit: str = "20"
    fetch_lyrics: bool = False
    legacy_charmap: bool = False
    no_credits_config: bool = False
    no_credits_flag: bool = False
    embed_art: bool = True
    albums_only: bool = False
    no_m3u: bool = False
    no_fallback: bool = False
    og_cover: bool = True
    no_cover: bool = False
    no_database: bool = False
    smart_discography: bool = False
    blacklist_config: str = "blacklist.txt"
    playlist_as_albums_config: bool = False
    folder_format: str = DEFAULT_FOLDER
    track_format: str = DEFAULT_TRACK


def _load_cli_config(config: configparser.ConfigParser, started: float) -> CliConfig:
    """Lê o config.ini já carregado em ``config`` (e migra tokens para o keyring).

    Pode levantar ``configparser.Error``/``KeyError`` quando faltam chaves
    obrigatórias; quem chama decide o que fazer (ver ``async_main``).
    """
    section = "qobuz" if config.has_section("qobuz") else "DEFAULT"
    cfg = CliConfig(section=section)
    cfg.email = config.get(section, "email")
    ini_token = config.get(section, "auth_token", fallback="")
    ini_genius = config.get(section, "genius_token", fallback="")
    disable_keyring = str(
        config.get(section, "disable_keyring", fallback="false")
    ).strip().lower() in ["true", "yes", "y", "1"]

    if disable_keyring:
        ini_password = config.get(section, "password", fallback="")
        cfg.token = ini_token if ini_token else ini_password
        cfg.genius_token = ini_genius
        cfg.password = ini_password
    else:
        cfg.token = _keyring_load("auth_token") or ini_token
        cfg.password = (
            cfg.token if cfg.token else config.get(section, "password", fallback="")
        )
        cfg.genius_token = _keyring_load("genius_token") or ini_genius

    migrated = False
    for k, v in (("auth_token", ini_token), ("genius_token", ini_genius)):
        if v and _keyring_save(k, v):
            config.set(section, k, "")
            migrated = True
    if migrated:
        try:
            _write_config_secure(config, CONFIG_FILE)
        except OSError:
            # O token já está no keyring, mas o config.ini não pôde ser
            # regravado: o valor antigo continua em texto puro nele.
            logger.warning(
                "Não foi possível limpar o token do config.ini depois de "
                "migrá-lo para o keyring; apague-o do arquivo à mão.",
                exc_info=True,
            )

    _startup_timing("leitura de config/keyring/migração", started)
    cfg.fetch_lyrics = config.getboolean(section, "fetch_lyrics", fallback=False)

    directory_val = config.get(section, "directory", fallback=None)
    if directory_val is not None:
        cfg.default_folder = directory_val
    else:
        legacy_val = config.get(section, "default_folder", fallback=None)
        if legacy_val is not None:
            ui.warn(
                "Aviso: 'default_folder' está obsoleto. Renomeie para 'directory' no config.ini."
            )
            cfg.default_folder = legacy_val
        else:
            cfg.default_folder = "Qobuz Downloads"

    if IOS_HOME and not os.path.isabs(cfg.default_folder):
        cfg.default_folder = os.path.join(IOS_HOME, cfg.default_folder)

    cfg.default_limit = config.get(section, "default_limit")
    cfg.default_quality = config.get(section, "default_quality")
    cfg.no_m3u = config.getboolean(section, "no_m3u", fallback=False)
    cfg.albums_only = config.getboolean(section, "albums_only", fallback=False)
    cfg.no_fallback = config.getboolean(section, "no_fallback", fallback=False)
    cfg.og_cover = config.getboolean(section, "og_cover", fallback=True)
    cfg.embed_art = config.getboolean(section, "embed_art", fallback=True)
    cfg.no_cover = config.getboolean(section, "no_cover", fallback=False)
    cfg.no_database = config.getboolean(section, "no_database", fallback=False)
    cfg.legacy_charmap = config.getboolean(section, "legacy_charmap", fallback=False)
    cfg.no_credits_config = config.getboolean(section, "no_credits", fallback=False)
    cfg.blacklist_config = config.get(section, "blacklist", fallback="blacklist.txt")
    cfg.playlist_as_albums_config = config.getboolean(
        section, "playlist_as_albums", fallback=False
    )

    cfg.app_id = config.get(section, "app_id")
    cfg.secrets = [s for s in config.get(section, "secrets").split(",") if s]
    cfg.smart_discography = config.getboolean(
        section, "smart_discography", fallback=False
    )

    cfg.folder_format = config.get(section, "folder_format", fallback=DEFAULT_FOLDER)
    cfg.track_format = config.get(section, "track_format", fallback=DEFAULT_TRACK)
    return cfg


async def async_main():
    main_started = time.perf_counter()
    await _initial_checks()
    _startup_timing("checagens iniciais concluídas", main_started)

    async def _async_check_updates():
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, check_for_updates)
        except Exception as e:
            logger.debug(f"Checagem de atualização falhou: {e}")

    update_task = asyncio.create_task(_async_check_updates())

    offline_args, _unknown = qobuz_dl_args().parse_known_args()
    offline_command = getattr(offline_args, "command", None)

    if offline_command == "stats":
        from qobuz_dl.stats_view import render_stats

        sys.exit(
            render_stats(
                QOBUZ_DB, show_all_artists=getattr(offline_args, "artistas", False)
            )
        )

    # `inspect --comparar` precisa de login (compara com o Qobuz): segue para
    # `_handle_commands`. Sem ele, o inspect é todo local e não autentica.
    if offline_command == "inspect" and not getattr(offline_args, "comparar", False):
        from qobuz_dl.inspector import run_inspector

        sys.exit(
            await run_inspector(
                getattr(offline_args, "caminho", None) or _config_directory_if_exists(),
                json_saida=bool(getattr(offline_args, "json", False)),
                resumo=bool(getattr(offline_args, "resumo", False)),
                completo=bool(getattr(offline_args, "completo", False)),
                espectro=not getattr(offline_args, "sem_espectro", False),
            )
        )

    # Comandos que NÃO precisam de login: doctor, scan e library. Rodam mesmo
    # com token expirado ou config.ini quebrado (justamente quando se usa o doctor).
    if offline_command in ("doctor", "check", "scan", "library-scan", "library", "lib"):
        from qobuz_dl import doctor
        from qobuz_dl.library_cmd import cmd_library, cmd_scan, library_db_path

        cfg_dir = os.path.expanduser(
            doctor.resolve_directory(CONFIG_FILE) or "Qobuz Downloads"
        )
        if IOS_HOME and not os.path.isabs(cfg_dir):
            cfg_dir = os.path.join(IOS_HOME, cfg_dir)

        if offline_command in ("doctor", "check"):
            results = doctor.run_checks(
                config_file=CONFIG_FILE,
                downloads_db=QOBUZ_DB,
                library_db=library_db_path(),
                directory=cfg_dir,
            )
            if getattr(offline_args, "json", False):
                print(json.dumps(doctor.to_json(results), ensure_ascii=False, indent=2))
                sys.exit(1 if any(r.level == doctor.FAIL for r in results) else 0)
            sys.exit(doctor.render(results))

        _cfg = configparser.ConfigParser(interpolation=None)
        _cfg.read(CONFIG_FILE, encoding="utf-8")
        _sec = "qobuz" if _cfg.has_section("qobuz") else "DEFAULT"
        if not _cfg.getboolean(_sec, "write_sentinel", fallback=True):
            offline_args.no_sentinel = True
        try:
            _quality = int(_cfg.get(_sec, "default_quality", fallback="27"))
        except ValueError:
            _quality = 27

        if offline_command in ("scan", "library-scan"):
            sys.exit(
                await cmd_scan(
                    offline_args,
                    directory=cfg_dir,
                    quality=_quality,
                    downloads_db=None
                    if _cfg.getboolean(_sec, "no_database", fallback=False)
                    else QOBUZ_DB,
                )
            )
        sys.exit(await cmd_library(offline_args, directory=cfg_dir))

    config = configparser.ConfigParser(interpolation=None)
    config_read_started = time.perf_counter()
    config.read(CONFIG_FILE, encoding="utf-8")

    # Valores-padrão: cobrem o único caminho que pode continuar após um erro de
    # configuração (o comando de reset, que termina logo abaixo).
    cfg = CliConfig(section="qobuz" if config.has_section("qobuz") else "DEFAULT")

    try:
        cfg = _load_cli_config(config, config_read_started)

        arguments = qobuz_dl_args(
            cfg.default_quality, cfg.default_limit, cfg.default_folder
        ).parse_args()

        if getattr(arguments, "no_lyrics", False):
            cfg.fetch_lyrics = False

        cfg.force_english = not getattr(arguments, "native_lang", False)
        with_credits_flag = getattr(arguments, "with_credits", False)
        cfg.no_credits_flag = (
            False
            if with_credits_flag
            else (getattr(arguments, "no_credits", False) or cfg.no_credits_config)
        )

    except (configparser.Error, KeyError) as error:
        arguments = qobuz_dl_args().parse_args()
        if not arguments.reset:
            sys.exit(
                f"{RED}Configuração inválida ou corrompida ({error}).\n{RESET}{YELLOW}Execute 'python -m qobuz_dl -r' para consertar isto.{RESET}"
            )

    # Nomes usados no resto da função (mantidos para não mexer no fluxo abaixo).
    section = cfg.section
    email = cfg.email
    password = cfg.password
    token = cfg.token
    genius_token = cfg.genius_token
    app_id = cfg.app_id
    secrets = cfg.secrets
    force_english = cfg.force_english
    default_folder = cfg.default_folder
    default_quality = cfg.default_quality
    default_limit = cfg.default_limit
    fetch_lyrics = cfg.fetch_lyrics
    legacy_charmap = cfg.legacy_charmap
    no_credits_flag = cfg.no_credits_flag
    embed_art = cfg.embed_art
    albums_only = cfg.albums_only
    no_m3u = cfg.no_m3u
    no_fallback = cfg.no_fallback
    og_cover = cfg.og_cover
    no_cover = cfg.no_cover
    no_database = cfg.no_database
    smart_discography = cfg.smart_discography
    blacklist_config = cfg.blacklist_config
    playlist_as_albums_config = cfg.playlist_as_albums_config
    folder_format = cfg.folder_format
    track_format = cfg.track_format

    ui.configure(
        quiet=getattr(arguments, "quiet", False),
        verbose=getattr(arguments, "verbose", False),
        color=False if getattr(arguments, "no_color", False) else None,
        ui_mode=getattr(arguments, "ui", "auto"),
    )
    _log_level_arg = getattr(arguments, "log_level", None)
    ui.install_logging(
        level=getattr(logging, _log_level_arg) if _log_level_arg else None
    )

    if arguments.reset:
        await _reset_config(CONFIG_FILE)
        sys.exit(0)

    if arguments.show_config:
        ui.emit_always(f"Configuração: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        ui.emit_always(_redacted_config(config))
        sys.exit(0)

    if arguments.purge:
        removed = _purge_databases(CONFIG_PATH, QOBUZ_DB)
        if removed:
            ui.ok(
                "Bancos de dados deletados: "
                + ", ".join(os.path.basename(path) for path in removed)
            )
        else:
            ui.ok("Nenhum banco de dados encontrado para deletar.")
        sys.exit(0)

    if arguments.command in (
        "auth",
        "login",
        "user",
        "account",
        "profile",
        "me",
        "info",
    ):
        is_login = getattr(arguments, "login", False) or (
            "--login" in sys.argv or "-l" in sys.argv or arguments.command in ("login",)
        )
        await _auth_command(
            CONFIG_FILE,
            update_credentials=is_login,
            show_json=getattr(arguments, "json", False),
        )
        sys.exit(0)

    binaries_started = time.perf_counter()
    checar_binarios_externos(
        precisa_fpcalc=bool(getattr(arguments, "find_duplicates", None))
    )
    _startup_timing("verificação de binários externos", binaries_started)

    if getattr(arguments, "sync_db", None):
        from qobuz_dl.db import create_db
        from qobuz_dl.qopy import Client
        from qobuz_dl.sync import sync_database

        create_db(QOBUZ_DB)
        sync_client = await Client.create(
            email,
            password,
            app_id,
            secrets,
            user_auth_token=token,
            force_english=force_english,
        )
        sync_dir = (
            default_folder if arguments.sync_db == "DEFAULT" else arguments.sync_db
        )
        if os.name == "nt":
            sync_dir = os.path.abspath(sync_dir)
            if not sync_dir.startswith("\\\\?\\"):
                sync_dir = "\\\\?\\" + sync_dir
        try:
            await sync_database(sync_dir, QOBUZ_DB, sync_client)
        finally:
            await sync_client.close()
        sys.exit(
            f"\n{GREEN}Sincronização do banco de dados concluída com sucesso.{OFF}"
        )

    if getattr(arguments, "find_duplicates", None):
        try:
            from qobuz_dl.sync import find_duplicate_tracks
        except ImportError as e:
            sys.exit(
                f"{RED}[!] --find-duplicates precisa do pacote extra 'duplicates'.{RESET}\n Instale com: pip install 'qobuz-dl-ultra[duplicates]'\n E instale o Chromaprint: apt install libchromaprint-tools\n (Detalhe técnico: {e})"
            )
        dup_dir = (
            default_folder
            if arguments.find_duplicates == "DEFAULT"
            else arguments.find_duplicates
        )
        if os.name == "nt":
            dup_dir = os.path.abspath(dup_dir)
            if not dup_dir.startswith("\\\\?\\"):
                dup_dir = "\\\\?\\" + dup_dir
        await find_duplicate_tracks(dup_dir)
        sys.exit(0)

    if getattr(arguments, "watch", None):
        try:
            from qobuz_dl.watcher import watch_directory
        except ImportError as e:
            sys.exit(
                f"{RED}[!] --watch precisa do pacote extra 'watch'.{RESET}\n Instale com: pip install 'qobuz-dl-ultra[watch]'\n (Detalhe técnico: {e})"
            )
        from qobuz_dl.qopy import Client

        watch_dir = default_folder if arguments.watch == "DEFAULT" else arguments.watch
        watch_dir = os.path.expanduser(watch_dir)
        if os.name == "nt":
            watch_dir = os.path.abspath(watch_dir)
            if not watch_dir.startswith("\\\\?\\"):
                watch_dir = "\\\\?\\" + watch_dir
        lrc_pref = not config.getboolean(section, "no_lrc_files", fallback=False)
        embed_pref = config.getboolean(section, "embed_lyrics", fallback=True)
        trans_lang = config.get(section, "lyrics_translation_lang", fallback="pt")
        watch_settings = QobuzDLSettings(lrc_files=lrc_pref, embed_lyrics=embed_pref)
        watch_settings.lyrics_translation_lang = trans_lang
        watch_settings.default_folder = watch_dir
        watch_client = None
        try:
            watch_client = await Client.create(
                email,
                password,
                app_id,
                secrets,
                user_auth_token=token,
                force_english=force_english,
            )
        except Exception as e:
            logging.debug(f"Aviso de autenticação para o cliente de letras: {e}")
        try:
            await watch_directory(
                watch_dir,
                client=watch_client,
                genius_token=genius_token,
                settings=watch_settings,
            )
        except KeyboardInterrupt:
            ui.error("Monitoramento interrompido pelo usuário (CTRL+C).")
        finally:
            if watch_client:
                await watch_client.close()
        sys.exit(0)

    if arguments.command == "lyrics":
        from qobuz_dl.qopy import Client
        from qobuz_dl.retro_tagger import inject_lyrics_retroactively

        target_dir = getattr(arguments, "DIR", None) or default_folder
        target_dir = os.path.expanduser(target_dir)
        home_dir = os.environ.get("HOME", "")
        if "Containers/Data/Application" in home_dir:
            docs_dir = os.path.join(home_dir, "Documents")
            if not target_dir.startswith(docs_dir):
                base_name = os.path.basename(target_dir.rstrip("/\\"))
                target_dir = os.path.join(
                    docs_dir, base_name if base_name else "Qobuz Downloads"
                )
        if os.name == "nt":
            target_dir = os.path.abspath(target_dir)
            if not target_dir.startswith("\\\\?\\"):
                target_dir = "\\\\?\\" + target_dir
        lrc_pref = not config.getboolean(section, "no_lrc_files", fallback=False)
        embed_pref = config.getboolean(section, "embed_lyrics", fallback=True)
        trans_lang = config.get(section, "lyrics_translation_lang", fallback="pt")
        lyrics_workers = getattr(arguments, "max_workers", None) or config.get(
            section, "max_workers", fallback="1"
        )
        local_settings = QobuzDLSettings(
            lrc_files=lrc_pref, embed_lyrics=embed_pref, max_workers=lyrics_workers
        )
        local_settings.lyrics_translation_lang = trans_lang
        local_settings.default_folder = target_dir
        lyrics_client = None
        try:
            lyrics_client = await Client.create(
                email,
                password,
                app_id,
                secrets,
                user_auth_token=token,
                force_english=force_english,
            )
        except Exception as e:
            logging.debug(f"Aviso de autenticação para o cliente de letras: {e}")
        try:
            await inject_lyrics_retroactively(
                target_dir,
                client=lyrics_client,
                genius_token=genius_token,
                settings=local_settings,
            )
        except KeyboardInterrupt:
            ui.error("Operação interrompida manualmente pelo usuário (CTRL+C).")
            ui.warn("Os arquivos já processados estão seguros. Saindo...")
        finally:
            if lyrics_client:
                await lyrics_client.close()
        sys.exit(0)

    if arguments.command == "inspect":
        # Só chega aqui com --comparar (o resto do inspect é local e roda antes).
        from qobuz_dl.inspector import comparar_com_qobuz
        from qobuz_dl.qopy import Client

        alvo = os.path.expanduser(getattr(arguments, "caminho", None) or "")
        if not os.path.isfile(alvo):
            ui.error(
                "--comparar precisa de um ARQUIVO: qobuz-dl inspect ARQUIVO --comparar"
            )
            sys.exit(1)
        try:
            inspect_client = await Client.create(
                email,
                password,
                app_id,
                secrets,
                user_auth_token=token,
                force_english=force_english,
            )
        except Exception as e:
            ui.error(f"Não foi possível autenticar no Qobuz para comparar: {e}")
            ui.detail(auth_failure_hint(e))
            sys.exit(1)
        inspect_settings = QobuzDLSettings.from_arguments_configparser(
            arguments, config
        )
        try:
            codigo = await comparar_com_qobuz(inspect_client, inspect_settings, alvo)
        finally:
            await inspect_client.close()
        sys.exit(codigo)

    if arguments.command == "tags":
        # Desfazer não precisa do Qobuz nem da pasta: resolve antes de autenticar.
        if getattr(arguments, "undo", None) or getattr(arguments, "undo_list", False):
            sys.exit(_handle_tags_undo(arguments, CONFIG_PATH))

        from qobuz_dl.qopy import Client
        from qobuz_dl.retro_tags import (
            resolve_library_dir,
            resolve_mode,
            retag_directory,
        )

        # Sem pasta no comando, usa a pasta padrão do config.ini (chave
        # "directory"), a mesma que os downloads já usam.
        informed_dir = getattr(arguments, "DIR", None)
        target_dir = resolve_library_dir(informed_dir or default_folder)
        if not informed_dir:
            ui.step(
                f"Nenhuma pasta informada: usando a pasta padrão do config.ini ({target_dir})"
            )
        tags_settings = QobuzDLSettings.from_arguments_configparser(arguments, config)
        tags_settings.default_folder = target_dir
        # Só leitura das tags locais: também não precisa autenticar no Qobuz.
        if getattr(arguments, "suggest_names", False):
            sys.exit(_handle_tags_suggest_names(target_dir, tags_settings))
        if getattr(arguments, "report", None):
            sys.exit(_handle_tags_report(target_dir, arguments.report))
        _choose_tags_mode(arguments)
        try:
            tags_client = await Client.create(
                email,
                password,
                app_id,
                secrets,
                user_auth_token=token,
                force_english=force_english,
            )
        except Exception as e:
            ui.error(f"Não foi possível autenticar no Qobuz para corrigir as tags: {e}")
            ui.detail(auth_failure_hint(e))
            sys.exit(1)
        if getattr(arguments, "force_covers", False):
            if getattr(arguments, "tags_only", False):
                ui.warn(
                    "--force-covers não tem efeito com --tags-only (a Apple não é consultada)."
                )
            else:
                ui.warn(
                    "Troca forçada de capas: a capa atual será substituída sempre que a "
                    "Apple devolver uma capa, mesmo se for pior. Use --undo para desfazer."
                )
        tags_dry_run = bool(getattr(arguments, "dry_run", False))
        tags_journal_run = None
        if not tags_dry_run and not getattr(arguments, "no_undo", False):
            from qobuz_dl.tags_journal import Journal

            try:
                tags_journal_run = Journal.create(
                    CONFIG_PATH,
                    resolve_mode(
                        bool(getattr(arguments, "covers_only", False)),
                        bool(getattr(arguments, "tags_only", False)),
                    ),
                    target_dir,
                )
            except OSError as e:
                await tags_client.close()
                ui.error(
                    f"Não foi possível criar o registro para desfazer ({e}). "
                    "Nada foi alterado. Use --no-undo para rodar sem backup."
                )
                sys.exit(1)
        from qobuz_dl.tags_progress import Progress

        tags_progress = Progress.open(
            CONFIG_PATH,
            target_dir,
            resolve_mode(
                bool(getattr(arguments, "covers_only", False)),
                bool(getattr(arguments, "tags_only", False)),
            ),
        )
        if getattr(arguments, "incremental", False):
            tags_progress.enable_skip()
        elif getattr(arguments, "resume", False):
            if tags_progress.interrupted:
                tags_progress.enable_skip()
                ui.step("Retomando a execução interrompida desta pasta...")
            else:
                ui.warn(
                    "Não há execução interrompida nesta pasta e modo: processando tudo."
                )
        elif tags_progress.interrupted and not tags_dry_run:
            ui.warn(
                "A última execução desta pasta foi interrompida. Para continuar "
                "de onde parou, rode de novo com --resume."
            )
        try:
            await retag_directory(
                target_dir,
                tags_client,
                tags_settings,
                covers_only=bool(getattr(arguments, "covers_only", False)),
                tags_only=bool(getattr(arguments, "tags_only", False)),
                dry_run=tags_dry_run,
                journal=tags_journal_run,
                progress=tags_progress,
                force_covers=bool(getattr(arguments, "force_covers", False)),
            )
        except KeyboardInterrupt:
            ui.error("Operação interrompida manualmente pelo usuário (CTRL+C).")
            ui.warn("Os arquivos já processados estão seguros. Saindo...")
            if not tags_dry_run:
                ui.emit(
                    "  Para continuar de onde parou, rode o mesmo comando com --resume."
                )
        finally:
            await tags_client.close()
        sys.exit(0)

    directory_to_use = (
        arguments.directory
        if hasattr(arguments, "directory") and arguments.directory
        else default_folder
    )
    directory_to_use = os.path.expanduser(directory_to_use)
    home_dir = os.environ.get("HOME", "")
    if "Containers/Data/Application" in home_dir:
        docs_dir = os.path.join(home_dir, "Documents")
        if not directory_to_use.startswith(docs_dir):
            base_name = os.path.basename(directory_to_use.rstrip("/\\"))
            directory_to_use = os.path.join(
                docs_dir, base_name if base_name else "Qobuz Downloads"
            )
    if os.name == "nt":
        directory_to_use = os.path.abspath(directory_to_use)
        if not directory_to_use.startswith("\\\\?\\"):
            directory_to_use = "\\\\?\\" + directory_to_use

    settings = QobuzDLSettings.from_arguments_configparser(arguments, config)
    settings.user_auth_token = token
    settings.legacy_charmap = legacy_charmap
    # [qobuz] playlist_dedupe = false desliga a limpeza de faixas duplicadas
    # entre playlist e álbum (padrão: ligada).
    settings.playlist_dedupe = config.getboolean(
        section, "playlist_dedupe", fallback=True
    )

    formats_to_validate = {
        "folder_format": getattr(arguments, "folder_format", None) or folder_format,
        "track_format": getattr(arguments, "track_format", None) or track_format,
        "fallback_folder_format": config.get(
            section, "fallback_folder_format", fallback="{artist} - {album}"
        ),
        "multiple_disc_track_format": config.get(
            section,
            "multiple_disc_track_format",
            fallback="{disc_number}.{track_number} - {track_title}",
        ),
    }
    validate_config_formats(formats_to_validate)

    qobuz = QobuzDL(
        directory_to_use,
        getattr(arguments, "quality", None) or default_quality,
        getattr(arguments, "embed_art", None) or embed_art,
        ignore_singles_eps=getattr(arguments, "albums_only", False) or albums_only,
        no_m3u_for_playlists=getattr(arguments, "no_m3u", False) or no_m3u,
        quality_fallback=not (getattr(arguments, "no_fallback", False) or no_fallback),
        cover_og_quality=getattr(arguments, "og_cover", None) or og_cover,
        no_cover=getattr(arguments, "no_cover", False) or no_cover,
        downloads_db=None
        if no_database or getattr(arguments, "no_db", False)
        else QOBUZ_DB,
        folder_format=getattr(arguments, "folder_format", None) or folder_format,
        track_format=getattr(arguments, "track_format", None) or track_format,
        smart_discography=getattr(arguments, "smart_discography", False)
        or smart_discography,
        fetch_lyrics=fetch_lyrics,
        no_lrc_files=not settings.lrc_files,
        genius_token=genius_token,
        force_english=force_english,
        no_credits=no_credits_flag,
        settings=settings,
        booklet_only=getattr(arguments, "booklet_only", False),
        blacklist=getattr(arguments, "blacklist", None) or blacklist_config,
        playlist_as_albums=getattr(arguments, "playlist_as_albums", False)
        or playlist_as_albums_config,
    )

    # A sincronização da checklist só mexe no banco local: sem login nem rede,
    # a menos que peça para atualizar os seguidos pela API.
    if not _is_local_checklist_sync(arguments) and arguments.command not in (
        "auth",
        "login",
        "user",
        "account",
        "profile",
        "me",
        "info",
    ):
        await qobuz.initialize_client(email, password, app_id, secrets)

    try:
        await _handle_commands(qobuz, arguments)
    finally:
        if hasattr(qobuz, "client") and qobuz.client:
            await qobuz.client.close()
    await update_task


def _is_local_checklist_sync(arguments) -> bool:
    """True quando ``artists --sincronizar-checklist`` só precisa do banco local."""
    return (
        arguments.command in ("artists", "artist-review")
        and bool(getattr(arguments, "sincronizar_checklist", False))
        and not getattr(arguments, "atualizar_seguidos", False)
    )


def _extract_checklist_json(captured: str) -> dict | None:
    """Recupera o objeto JSON emitido pelo modo checklist, ignorando logs de startup."""
    # ui.emit_always normalmente imprime uma linha isolada; o fallback procura
    # objetos em linhas que eventualmente tenham prefixos de terminal.
    for line in reversed(captured.splitlines()):
        candidates = [line.strip()]
        start = line.find("{")
        end = line.rfind("}")
        if start >= 0 and end > start:
            candidates.append(line[start : end + 1])
        for candidate in candidates:
            try:
                value = json.loads(candidate)
            except (json.JSONDecodeError, TypeError):
                # Linha que não é JSON: segue procurando.
                continue
            if isinstance(value, dict) and ("ok" in value or "parsed" in value):
                return value
    return None


def main():
    # Atalhos do iOS precisa receber um único JSON válido, sem banners de login,
    # assinatura, qualidade ou mensagens do logger. O comportamento dos demais
    # comandos permanece inalterado.
    checklist_json_mode = any(
        arg == "--sincronizar-checklist" or arg.startswith("--sincronizar-checklist=")
        for arg in sys.argv[1:]
    )
    if checklist_json_mode:
        captured_out = io.StringIO()
        captured_err = io.StringIO()
        exit_code = 0
        try:
            with (
                contextlib.redirect_stdout(captured_out),
                contextlib.redirect_stderr(captured_err),
            ):
                asyncio.run(async_main())
        except KeyboardInterrupt:
            exit_code = 1
        except SystemExit as exc:
            exit_code = (
                exc.code
                if isinstance(exc.code, int)
                else (0 if exc.code is None else 1)
            )
        except Exception as exc:
            exit_code = 1
            captured_err.write(str(exc))

        payload = _extract_checklist_json(captured_out.getvalue())
        if payload is None:
            details = captured_err.getvalue().strip()
            payload = {
                "ok": False,
                "error": details
                or "A sincronização terminou sem retornar um JSON válido.",
            }
            exit_code = exit_code or 1
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        if exit_code:
            raise SystemExit(exit_code)
        return

    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
