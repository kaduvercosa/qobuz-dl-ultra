# ==============================================================================
# MÓDULO: cli.py (QOBUZ-DL-ULTRA)
# DESCRIÇÃO: Ponto de entrada principal (CLI) da aplicação QOBUZ-DL-ULTRA.
#            Responsável pelo fluxo de inicialização, leitura e validação de
#            configurações (.ini), gerenciamento de credenciais (Keyring/OS),
#            interface de linha de comando (argparse), rotas de execução e
#            despacho de downloads e ferramentas offline.
# ==============================================================================

import argparse
import asyncio
import configparser
import glob
import json
import logging
import os
import signal
import string
import sys
import time
from datetime import datetime

import httpx
import keyring
import send2trash
from packaging.version import Version

from qobuz_dl import fuzzy, ui

# Módulos internos do ecossistema QOBUZ-DL
from qobuz_dl.bundle import Bundle
from qobuz_dl.color import ACCENT_PRESETS, BG, GREEN
from qobuz_dl.color import HIGHLIGHT as ACCENT
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import MUTED, OFF, RED, RESET
from qobuz_dl.color import WARNING as YELLOW
from qobuz_dl.color import accent_preview
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.core import QobuzDL
from qobuz_dl.downloader import DEFAULT_FOLDER, DEFAULT_TRACK
from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.utils import checar_binarios_externos, get_config_paths

logger = logging.getLogger(__name__)

_config_paths = get_config_paths()
CONFIG_DIR = _config_paths["config_dir"]
CONFIG_PATH = _config_paths["config_path"]
CONFIG_FILE = _config_paths["config_file"]
QOBUZ_DB = _config_paths["qobuz_db"]
IOS_HOME = os.environ.get("QOBUZ_DL_IOS_HOME")

KEYRING_SERVICE = "qobuz-dl"


def _bootstrap_ui():
    argv = sys.argv[1:]
    ui.configure(
        quiet="--quiet" in argv,
        verbose=("--verbose" in argv or "-v" in argv),
        color=False if "--no-color" in argv else None,
    )
    ui.install_logging()


_bootstrap_ui()


def _keyring_save(key: str, value: str) -> bool:
    if not value:
        return False
    try:
        keyring.set_password(KEYRING_SERVICE, key, value)
        return True
    except Exception:
        return False


def _keyring_load(key: str):
    try:
        return keyring.get_password(KEYRING_SERVICE, key)
    except Exception:
        return None


def validate_config_formats(formats_to_check: dict):
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
                    # ui.warn (não print cru): garante que --quiet não
                    # engula um aviso que vai causar sys.exit(1) logo abaixo.
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
    ui.emit(f"\n{BG}[?] Cor de destaque do programa:{OFF}")
    ui.wrapped("Aparece em nomes de faixas, cabeçalhos, barras e progresso.", indent=4)
    ui.blank()

    for idx, (name, _rgb, escape) in enumerate(ACCENT_PRESETS, 1):
        if escape:
            preview = accent_preview(escape, "━━ [FAIXA]  ARTISTA  The Weeknd")
            ui.emit(f"  {idx:2}. {name:<22} {preview}")
        else:
            ui.emit(f"  {idx:2}. {name}")

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
        ui.emit(f"  Por favor escolha entre 1 e {len(ACCENT_PRESETS)}.")

    if rgb is None:
        ui.emit("\n  Digite os valores RGB separados por ponto e vírgula.")
        ui.emit("  Exemplo: 255;100;50 (vermelho), 0;200;150 (teal)\n")
        while True:
            raw = input("  Código RGB (R;G;B): ").strip()
            raw = raw.replace(",", ";").replace(" ", ";")
            parts_str = [x.strip() for x in raw.split(";") if x.strip()]
            try:
                parts = [int(x) for x in parts_str]
                if len(parts) == 3 and all(0 <= p <= 255 for p in parts):
                    rgb = ";".join(str(p) for p in parts)
                    # ui.c() aqui: sem isto, --no-color/NO_COLOR ficava sem
                    # efeito neste preview específico, porque o escape é
                    # montado na hora a partir do RGB digitado (não vem de
                    # ACCENT_PRESETS, que já nasce zerado com a cor desligada).
                    escape = ui.c(f"\033[38;2;{parts[0]};{parts[1]};{parts[2]}m")
                    break
            except ValueError:
                pass
            ui.emit("  Formato inválido. Use três números de 0 a 255, ex: 150;80;220")

        ui.emit("\n  Preview da sua cor:")
        ui.emit(accent_preview(escape, "━━ [FAIXA]  ARTISTA  The Weeknd\n"))
        confirm = (
            input("  Confirmar esta cor? (Enter = sim, n = escolher outra): ")
            .strip()
            .lower()
        )
        if confirm in ("n", "nao", "no"):
            return _pick_accent_color()

    ui.emit(f"\n  {GREEN}Cor salva: {escape}━━ {name.strip()}{OFF}\n")
    return rgb


def _reset_config(config_file: str):
    if ui.width() >= 41:
        logging.info(f"\n{BG}[ QOBUZ-DL-ULTRA - CONFIGURAÇÃO INICIAL ]{OFF}")
    else:
        logging.info(f"\n{BG}[ CONFIGURAÇÃO INICIAL ]{OFF}")

    config = configparser.ConfigParser(interpolation=None)
    config["qobuz"] = {}

    accent_rgb = _pick_accent_color()
    config["qobuz"]["accent_color"] = accent_rgb
    # BUGFIX: antes montava o escape direto do RGB sem passar por ui.c(),
    # entao --no-color/NO_COLOR nao tinha efeito nestes prompts do wizard
    # (mesmo problema do preview de RGB customizado em _pick_accent_color).
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
    config["qobuz"]["password"] = ""

    ui.emit(f"\n{C_ACCENT}[?] Armazenamento de Senhas (OS Keyring):{OFF}")
    ui.emit(
        "    Por padrão, os tokens são guardados criptografados no cofre do sistema operacional."
    )
    ui.emit(
        "    Em ambientes sem interface gráfica (Linux headless, Docker, NAS), isso pode falhar."
    )
    disable_kr = (
        input(
            "    Desativar Keyring e salvar tokens em texto puro no config.ini? (yes/no) [Padrão: no]\n- "
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
        ui.emit(
            f"\n{C_ACCENT}[!] Para usar o Genius como fallback, insira seu API Token (Enter para pular e usar apenas LRCLIB):{OFF}"
        )
        genius_token = input("Genius API Token:\n- ").strip()

    if use_keyring and _keyring_save("genius_token", genius_token):
        config["qobuz"]["genius_token"] = ""
    else:
        config["qobuz"]["genius_token"] = genius_token

    config["qobuz"]["directory"] = (
        input("\nPasta de download (pressione Enter para 'Qobuz Downloads')\n- ")
        or "Qobuz Downloads"
    )
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

    ui.emit(f"\n{C_ACCENT}[?] Idioma de Tradução de Letras:{OFF}")
    ui.emit(
        "    Opções: pt (Português), en (Inglês), es (Espanhol), fr (Francês), original (Manter nativo)"
    )
    lang_choice = input("    Idioma [Padrão: pt]:\n- ").strip().lower()
    if lang_choice in ["original", "orig"]:
        config["qobuz"]["lyrics_translation_lang"] = ""
    elif lang_choice in ["pt", "en", "es", "fr", "de", "it"]:
        config["qobuz"]["lyrics_translation_lang"] = lang_choice
    else:
        config["qobuz"]["lyrics_translation_lang"] = "pt"

    logging.info(
        f"\n{C_ACCENT}Obtendo credenciais da API via bundle.js... Por favor, aguarde.{OFF}"
    )
    bundle = Bundle()
    config["qobuz"]["app_id"] = str(bundle.get_app_id())
    config["qobuz"]["secrets"] = ",".join(bundle.get_secrets().values())

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
    config["qobuz"][
        "multiple_disc_track_format"
    ] = "{disc_number}.{track_number} - {track_title}"
    config["qobuz"]["max_workers"] = "1"
    config["qobuz"]["user_auth_token"] = ""

    with open(config_file, "w", encoding="utf-8") as configfile:
        config.write(configfile)

    logging.info(f"\n{GREEN}[+] Configuração salva com sucesso em {config_file}!{OFF}")

    global ACCENT, CYAN
    if accent_rgb:
        nova_cor = f"\033[38;2;{accent_rgb}m"
        ACCENT = nova_cor
        CYAN = nova_cor

    ui.emit(f"{ACCENT}\n [*] Atualizando interface...{OFF}", end="")
    time.sleep(2.0)
    ui.emit("\r\033[K", end="")
    _print_welcome_screen()


def _remove_leftovers(directory: str):
    for pattern in [".*.tmp", "~tmp_*.tmp"]:
        search_dir = os.path.join(directory, "**", pattern)
        for i in glob.glob(search_dir, recursive=True):
            try:
                send2trash.send2trash(i)
            except Exception as e:
                logger.debug(
                    f"Falha ao mover arquivo temporário '{i}' para a lixeira: {e}"
                )


def _format_timestamp(ts: int) -> str:
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
    dados: dict, titulo: str, ja_mostrados: set, indent: str = "   "
):
    """
    Imprime, em formato de árvore, TODO campo de `dados` que ainda não
    apareceu no relatório curado (ou seja, tudo que não está em
    `ja_mostrados`).

    Existe para que o relatório de conta nunca fique incompleto: mesmo que
    a API do Qobuz adicione campos novos no futuro, ou que algum campo
    pouco comum não tenha sido previsto na seção "bonita" acima, ele ainda
    aparece aqui automaticamente -- sem precisar editar este arquivo.
    """
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
                ui.emit(f"{indent}    - {sub_chave}: {_formatar_valor(sub_valor)}")
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
                    ui.emit(f"{indent}    [{i}] {resumo}")
                else:
                    ui.emit(f"{indent}    - {_formatar_valor(item)}")
        else:
            ui.emit(f"{indent}• {chave}: {_formatar_valor(valor)}")


# ==============================================================================
# SUBCOMANDO UNIFICADO: AUTH / USER / ME / PROFILE (GERENCIAMENTO DE CONTA)
# ==============================================================================
async def _auth_command(
    config_file: str, update_credentials: bool = False, show_json: bool = False
):
    """
    Subcomando unificado:
    - Se update_credentials=True: solicita novos dados, valida na API e salva.
    - Se update_credentials=False: exibe o perfil completo e a assinatura.
    - Se a assinatura estiver inativa/cancelada, oferece a troca de credenciais interativamente.
    """
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

    if disable_keyring:
        token = config.get(section, "auth_token", fallback="") or config.get(
            section, "password", fallback=""
        )
    else:
        token = _keyring_load("auth_token") or config.get(
            section, "auth_token", fallback=""
        )

    # 1. Se foi chamado explicitamente para atualizar credenciais (ou não tem token)
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
            if not token:
                return False
        else:
            token = new_token
            ui.emit(
                f"\n\r{CYAN}[*] Validando nova sessão com a API do Qobuz...{OFF}\033[K"
            )
    else:
        ui.emit(
            f"\n\r{CYAN}[*] Consultando dados da conta e assinatura no Qobuz...{OFF}\033[K"
        )

    # 2. Conecta na API
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
        ui.error(f"Falha ao autenticar com a API: {e}")
        if update_credentials:
            ui.error("As alterações NÃO foram salvas.")
        return False

    try:
        user_info = await client.get_user_profile()
        sub_info = client.check_subscription()

        # Salva o token validado se veio de uma atualização
        if update_credentials and token:
            if not disable_keyring and _keyring_save("auth_token", token):
                config.set(section, "auth_token", "")
                ui.ok("Token salvo com segurança no Keyring do sistema!")
            else:
                config.set(section, "auth_token", token)
                config.set(section, "password", "")
                ui.ok("Token salvo no config.ini!")

            with open(config_file, "w", encoding="utf-8") as f:
                config.write(f)

        if show_json:
            # ui.emit_always: saída de --json é o entregável do comando (pensada
            # para ser lida por script/pipe), então precisa sobreviver a
            # --quiet -- ao contrário das mensagens de progresso acima.
            ui.emit_always(json.dumps(user_info, indent=2, ensure_ascii=False))
            return sub_info.get("is_active", False)

        sf = user_info.get("store_features") or {}
        cred = user_info.get("credential") or {}
        last_update = user_info.get("last_update") or {}
        status_color = GREEN if sub_info.get("is_active") else RED

        ui.emit("\n" + "=" * 68)
        ui.emit(f"  {CYAN}{BG}🎵 QOBUZ // INFORMAÇÕES DA CONTA E ASSINATURA{OFF}")
        ui.emit("=" * 68)

        ui.emit(f"\n {CYAN}[👤 PERFIL DO USUÁRIO]{OFF}")
        nome_completo = (
            f"{user_info.get('firstname', '')} {user_info.get('lastname', '')}".strip()
            or "N/A"
        )
        ui.emit(f"   • Nome Completo:     {nome_completo}")
        ui.emit(f"   • Display Name:      {user_info.get('display_name', 'N/A')}")
        ui.emit(f"   • E-mail:            {user_info.get('email', 'N/A')}")
        ui.emit(f"   • Login:             {user_info.get('login', 'N/A')}")
        ui.emit(
            f"   • ID do Usuário:     {user_info.get('id', 'N/A')} [Public ID: {user_info.get('publicId', 'N/A')}]"
        )
        ui.emit(
            f"   • País / Zona:       {user_info.get('country', 'N/A')} / {user_info.get('zone', 'N/A')}"
        )
        ui.emit(
            f"   • Loja / Idioma:     {user_info.get('store', 'N/A')} ({user_info.get('language_code', 'N/A')})"
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
            f"   • Nascimento / Idade:{format_date_br(user_info.get('birthdate'))} ({user_info.get('age', 'N/A')} anos, {user_info.get('genre', 'N/A')})"
        )
        ui.emit(
            f"   • Conta Criada em:   {format_date_br(user_info.get('creation_date'))}"
        )

        ui.emit(f"\n {CYAN}[💳 STATUS DA SUBSCRIÇÃO (ASSINATURA)]{OFF}")
        ui.emit(
            f"   • Status Atual:      {status_color}● {str(sub_info.get('status')).upper()}{OFF}"
        )
        ui.emit(f"   • Plano / Oferta:    {sub_info.get('offer', 'N/A')}")
        ui.emit(
            f"   • Periodicidade:     {str(sub_info.get('periodicity', 'N/A')).capitalize()}"
        )
        ui.emit(f"   • Data de Início:    {sub_info.get('start_date') or 'N/A'}")
        ui.emit(f"   • Data de Término:   {sub_info.get('end_date') or 'N/A'}")
        ui.emit(
            f"   • Cancelamento:      {'Sim (Cancelada pelo usuário)' if sub_info.get('is_canceled') else 'Não'}"
        )
        ui.emit(
            f"   • Vagas Família:     {sub_info.get('household_size_max')} membro(s)"
        )

        ui.emit(f"\n {CYAN}[🎛️ CREDENCIAL & RECURSOS DA CONTA]{OFF}")
        ui.emit(f"   • Tipo de Membro:    {cred.get('description', 'Membro Qobuz')}")
        ui.emit(
            f"   • Streaming:         {'Disponível' if sf.get('streaming') else 'Indisponível'}"
        )
        ui.emit(
            f"   • Letras (Lyrics):   {'Disponível' if sf.get('lyrics') else 'Indisponível'}"
        )
        ui.emit(
            f"   • Importação Músicas:{'Disponível' if sf.get('music_import') else 'Indisponível'}"
        )
        ui.emit(
            f"   • Rádio / Club / Q:  {'Disponível' if sf.get('radio') or sf.get('club') else 'Indisponível'}"
        )

        if last_update:
            ui.emit(f"\n {CYAN}[📊 ATIVIDADES & ÚLTIMAS ATUALIZAÇÕES]{OFF}")
            ui.emit(
                f"   • Playlists:         {_format_timestamp(last_update.get('playlist'))}"
            )
            ui.emit(
                f"   • Álbuns Favoritos:  {_format_timestamp(last_update.get('favorite_album'))}"
            )
            ui.emit(
                f"   • Faixas Favoritas:  {_format_timestamp(last_update.get('favorite_track'))}"
            )
            ui.emit(
                f"   • Artistas Favoritos:{_format_timestamp(last_update.get('favorite_artist'))}"
            )
            ui.emit(
                f"   • Compras na Loja:   {_format_timestamp(last_update.get('purchase'))}"
            )

        # Se a assinatura estiver inativa e não acabamos de atualizar:
        if not sub_info.get("is_active"):
            ui.warn("⚠️  AVISO DE ASSINATURA INATIVA:")
            ui.detail(
                f"Sua assinatura expirou em {sub_info.get('end_date')}. Para baixar álbuns e faixas "
                "completas em alta resolução, é necessário possuir uma conta ativa."
            )
            ui.emit("=" * 68)

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
        else:
            ui.emit("=" * 68 + "\n")

        return sub_info.get("is_active", False)
    finally:
        await client.close()


# ==============================================================================
# GARANTIA DE ASSINATURA ATIVA (bloqueia até ter uma conta válida)
# ==============================================================================
async def _garantir_assinatura_ativa(qobuz: QobuzDL) -> bool:
    """
    Bloqueia a execução de comandos de download até que o `qobuz.client`
    tenha uma assinatura Qobuz ativa.

    Diferente do comportamento anterior (uma única tentativa de trocar
    e-mail/token, com desistência automática se a nova conta também
    estivesse inativa), esta função mantém o usuário em laço: se a conta
    não tiver assinatura ativa, é OBRIGATÓRIO atualizar o e-mail e o
    user_token para tentar de novo. O laço só termina de duas formas:
        1) uma conta com assinatura ativa é encontrada -> retorna True;
        2) o usuário cancela explicitamente digitando "cancelar" -> False.

    Returns:
        bool: True se `qobuz.client` está pronto com assinatura ativa.
            False se o usuário cancelou a operação.
    """
    sub_info = qobuz.client.check_subscription()

    while not sub_info.get("is_active"):
        ui.error("CONTA SEM ASSINATURA ATIVA NO QOBUZ")
        ui.emit(f" {CYAN}•{OFF} Status Atual:       {RED}{sub_info.get('status')}{OFF}")
        ui.emit(
            f" {CYAN}•{OFF} Plano:              {sub_info.get('offer', 'N/A')} ({str(sub_info.get('periodicity', 'N/A')).capitalize()})"
        )
        ui.emit(
            f" {CYAN}•{OFF} Validade / Término: {sub_info.get('end_date') or 'N/A'}"
        )
        ui.emit(
            f" {CYAN}•{OFF} Cancelamento:       {'Sim (Cancelada)' if sub_info.get('is_canceled') else 'Não'}"
        )
        ui.warn(
            "ℹ️  Sem uma assinatura ativa, a API da Qobuz não permite o download "
            "de faixas completas. É obrigatório informar o e-mail e o user_token "
            "de uma conta com assinatura ativa para continuar."
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

        # _auth_command já pede o novo e-mail/token, valida na API e salva (no Keyring ou no config.ini, conforme a configuração do usuário).
        await _auth_command(CONFIG_FILE, update_credentials=True)

        # Recarrega as credenciais recém-salvas do disco e reinicializa o cliente principal (`qobuz.client`) com elas -- não importa se a conta ficou ativa ou não: o laço reavalia a assinatura logo abaixo e, se ainda estiver inativa, volta ao topo e pede a atualização de novo, mostrando o status da conta que acabou de ser testada.
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
            # Credenciais inválidas ou falha de rede: trata como "ainda sem
            # assinatura ativa" e deixa o laço pedir a atualização de novo,
            # em vez de deixar a exceção derrubar o programa.
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


# ==============================================================================
# ROTEADOR DE SUBCOMANDOS E BLOQUEIO/RECUPERAÇÃO DE ASSINATURA
# ==============================================================================
async def _handle_commands(qobuz: QobuzDL, arguments):
    def sigint_handler(sig, frame):
        ui.error("Download interrompido manualmente pelo usuário.")
        ui.warn("Arquivos parciais foram enviados para a lixeira.")
        try:
            _remove_leftovers(qobuz.directory)
        except Exception as e:
            # Best-effort: mesmo se a limpeza falhar, o programa tem que
            # terminar (sys.exit abaixo). So' registra pra facilitar debug.
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
    }

    if arguments.command in DOWNLOAD_COMMANDS or arguments.command is None:
        if not await _garantir_assinatura_ativa(qobuz):
            return

    try:
        if arguments.command == "dl":
            await qobuz.download_list_of_urls(arguments.SOURCE)

        elif arguments.command in ("sync-playlist", "sp"):
            from qobuz_dl.sync_playlist import sync_playlist

            await sync_playlist(
                qobuz,
                arguments.URL,
                qobuz.directory,
                auto_confirm=arguments.yes,
            )

        elif arguments.command == "lucky":
            query = " ".join(arguments.QUERY)
            qobuz.lucky_type = arguments.type
            qobuz.lucky_limit = arguments.number
            await qobuz.lucky_mode(query)

        elif arguments.command in ("import-playlist", "ip"):
            await qobuz.import_playlist_from_url_or_file(
                source=arguments.SOURCE,
                name=getattr(arguments, "name", None),
                auto=getattr(arguments, "auto", False),
            )

        else:
            if hasattr(arguments, "limit"):
                qobuz.interactive_limit = arguments.limit
            await qobuz.interactive()

    except KeyboardInterrupt:
        pass
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
            ui.emit(f"{CYAN}{pad1}{row}{OFF}")
        for row in line2:
            ui.emit(f"{CYAN}{pad2}{row}{OFF}")
    else:
        line_short = _render_logo_word("QDL")
        pad_short = " " * max((cols - len(line_short[0])) // 2, 0)
        for row in line_short:
            ui.emit(f"{CYAN}{pad_short}{row}{OFF}")


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
    "sync-playlist": "Sincroniza uma pasta local com uma playlist do Qobuz (baixa o que falta, remove o que saiu).",
    "import-playlist": "Importa um arquivo de playlist (TXT, CSV, JSON) de qualquer plataforma para download.",
    "stats": "Mostra estatísticas detalhadas sobre sua biblioteca e downloads efetuados.",
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


def _print_welcome_screen():
    from qobuz_dl import __version__

    cols = ui.width()
    ui.blank()
    _print_logo(cols)
    version_line = f"v{__version__}"
    pad_version = " " * max((cols - len(version_line)) // 2, 0)
    ui.emit(f"{RESET}{pad_version}{version_line}\n")

    ui.rule("=")
    ui.emit(f"{ACCENT}{BG}Uso: qobuz-dl ou qdl + <comando>{OFF}")
    ui.wrapped(
        f"{ACCENT}Help:{RESET} qobuz-dl <comando> --help "
        f"{MUTED}(lista todas as opções do comando){OFF} ",
        indent=4,
    )
    ui.blank()

    parser = qobuz_dl_args()

    ui.emit(f"{ACCENT}{BG}COMANDOS:{OFF}\n")
    for name, aliases, help_text in _extract_subcommands(parser):
        label = name if not aliases else f"{name} ({aliases})"
        desc = _COMMAND_DESCRIPTIONS_PT.get(name, help_text or "")
        ui.emit(f"  {ACCENT}{label}{OFF}")
        ui.wrapped(desc, indent=4)
    ui.blank()

    if cols >= 62:
        ui.emit(
            f"{ACCENT}{BG}FLAGS GLOBAIS:{RESET} {MUTED}(não pertencem a nenhum comando específico){OFF}\n"
        )
    else:
        ui.emit(f"{BG}FLAGS GLOBAIS:{RESET}\n")

    for flag_str, dest, help_text in _extract_global_flags(parser):
        desc = _FLAG_DESCRIPTIONS_PT.get(dest, help_text or "")
        ui.emit(f"  {ACCENT}{flag_str}{OFF}")
        ui.wrapped(desc, indent=4)
    ui.blank()

    ui.rule("=")


def _initial_checks():
    if not os.path.isdir(CONFIG_PATH) or not os.path.isfile(CONFIG_FILE):
        os.makedirs(CONFIG_PATH, exist_ok=True)
        if "-r" not in sys.argv and "--reset" not in sys.argv:
            _reset_config(CONFIG_FILE)

    if len(sys.argv) < 2:
        _print_welcome_screen()
        sys.exit(0)


def check_for_updates():
    try:
        from qobuz_dl import __version__

        url = "https://api.github.com/repos/kaduvercosa/qobuz-dl-ultra/releases/latest"
        response = httpx.get(url, timeout=2)
        response.raise_for_status()

        latest_version_str = response.json().get("tag_name", "").lstrip("vV")
        current_version_str = __version__

        versao_remota = Version(latest_version_str)
        versao_local = Version(current_version_str)

        if versao_remota > versao_local:
            ui.warn(
                f"ATUALIZAÇÃO DISPONÍVEL: Ultra Edition v{latest_version_str} está disponível!"
            )
            ui.detail("- PyPI: rode 'pip install --upgrade qobuz-dl-ultra'")
            ui.detail("- Docker: puxe a imagem mais recente")

    except Exception as e:
        logger.debug("Checagem de atualização falhou: %s: %s", type(e).__name__, e)


# ==============================================================================
# MOTOR PRINCIPAL ASSÍNCRONO (ASYNC_MAIN)
# ==============================================================================
async def async_main():
    _initial_checks()

    async def _async_check_updates():
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, check_for_updates)
        except Exception as e:
            # Checagem de atualização é best-effort e roda em background;
            # uma falha aqui (rede fora, PyPI indisponível) nunca deve
            # impactar o download em andamento -- so' fica registrado.
            logger.debug(f"Checagem de atualização falhou: {e}")

    asyncio.create_task(_async_check_updates())

    offline_args, _unknown = qobuz_dl_args().parse_known_args()
    offline_command = getattr(offline_args, "command", None)

    if offline_command == "stats":
        from qobuz_dl.stats_view import render_stats

        sys.exit(
            render_stats(
                QOBUZ_DB,
                show_all_artists=getattr(offline_args, "artistas", False),
            )
        )

    config = configparser.ConfigParser(interpolation=None)
    config.read(CONFIG_FILE, encoding="utf-8")

    try:
        section = "qobuz" if config.has_section("qobuz") else "DEFAULT"
        email = config.get(section, "email")

        ini_token = config.get(section, "auth_token", fallback="")
        ini_genius = config.get(section, "genius_token", fallback="")
        disable_keyring = str(
            config.get(section, "disable_keyring", fallback="false")
        ).strip().lower() in ["true", "yes", "y", "1"]

        if disable_keyring:
            ini_password = config.get(section, "password", fallback="")
            token = ini_token if ini_token else ini_password
            genius_token = ini_genius
            password = ini_password
        else:
            token = _keyring_load("auth_token") or ini_token
            password = token if token else config.get(section, "password", fallback="")
            genius_token = _keyring_load("genius_token") or ini_genius

            migrated = False
            for k, v in (("auth_token", ini_token), ("genius_token", ini_genius)):
                if v and _keyring_save(k, v):
                    config.set(section, k, "")
                    migrated = True
            if migrated:
                try:
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        config.write(f)
                except OSError:
                    pass

        fetch_lyrics = config.getboolean(section, "fetch_lyrics", fallback=False)

        directory_val = config.get(section, "directory", fallback=None)
        if directory_val is not None:
            default_folder = directory_val
        else:
            legacy_val = config.get(section, "default_folder", fallback=None)
            if legacy_val is not None:
                ui.warn(
                    "Aviso: 'default_folder' está obsoleto. Renomeie para 'directory' no config.ini."
                )
                default_folder = legacy_val
            else:
                default_folder = "Qobuz Downloads"

        if IOS_HOME and not os.path.isabs(default_folder):
            default_folder = os.path.join(IOS_HOME, default_folder)

        default_limit = config.get(section, "default_limit")
        default_quality = config.get(section, "default_quality")
        no_m3u = config.getboolean(section, "no_m3u", fallback=False)
        albums_only = config.getboolean(section, "albums_only", fallback=False)
        no_fallback = config.getboolean(section, "no_fallback", fallback=False)
        og_cover = config.getboolean(section, "og_cover", fallback=True)
        embed_art = config.getboolean(section, "embed_art", fallback=True)
        no_cover = config.getboolean(section, "no_cover", fallback=False)
        no_database = config.getboolean(section, "no_database", fallback=False)
        legacy_charmap = config.getboolean(section, "legacy_charmap", fallback=False)
        no_credits_config = config.getboolean(section, "no_credits", fallback=False)
        blacklist_config = config.get(section, "blacklist", fallback="blacklist.txt")
        playlist_as_albums_config = config.getboolean(
            section, "playlist_as_albums", fallback=False
        )

        app_id = config.get(section, "app_id")
        secrets = [s for s in config.get(section, "secrets").split(",") if s]
        smart_discography = config.getboolean(
            section, "smart_discography", fallback=False
        )
        folder_format = config.get(section, "folder_format", fallback=DEFAULT_FOLDER)
        track_format = config.get(section, "track_format", fallback=DEFAULT_TRACK)

        arguments = qobuz_dl_args(
            default_quality, default_limit, default_folder
        ).parse_args()

        if getattr(arguments, "no_lyrics", False):
            fetch_lyrics = False

        force_english = not getattr(arguments, "native_lang", False)
        with_credits_flag = getattr(arguments, "with_credits", False)
        no_credits_flag = (
            False
            if with_credits_flag
            else (getattr(arguments, "no_credits", False) or no_credits_config)
        )

    except (configparser.Error, KeyError) as error:
        arguments = qobuz_dl_args().parse_args()
        if not arguments.reset:
            sys.exit(
                f"{RED}Configuração inválida ou corrompida ({error}).\n{RESET}"
                f"{YELLOW}Execute 'python -m qobuz_dl -r' para consertar isto.{RESET}"
            )

    # BUGFIX (--quiet ignorado): _bootstrap_ui() configura a ui ANTES do
    # argparse rodar, lendo sys.argv na unha só pra ter algo utilizável nos
    # prints que acontecem durante o parse (ex.: o aviso de config obsoleta
    # acima). Isso cobre o caso comum, mas diverge de casos reais do
    # argparse: abreviação de flag (--qui), configuração vinda só de
    # subparser, etc. Agora que `arguments` é o resultado final e
    # autoritativo do parse, re-sincronizamos a ui com ele -- daqui pra
    # frente (inclusive dentro das threads de download) o --quiet golpeia
    # de verdade, e não só nos casos que a heurística acertou por sorte.
    ui.configure(
        quiet=getattr(arguments, "quiet", False),
        verbose=getattr(arguments, "verbose", False),
        color=False if getattr(arguments, "no_color", False) else None,
    )
    # --log-level e' opcional e sobrepoe o nivel implicito de -v/--quiet
    # (WARNING/INFO/DEBUG). Serve pra separar duas coisas que hoje viviam
    # coladas: "quero ver menos coisa na TELA" (--quiet, afeta ui.warn/ok/
    # step/skip/emit) de "quero mais detalhe no LOG" (--log-level DEBUG,
    # afeta so' o que passa por logger.debug/info/... de verdade).
    _log_level_arg = getattr(arguments, "log_level", None)
    ui.install_logging(
        level=getattr(logging, _log_level_arg) if _log_level_arg else None
    )

    if arguments.reset:
        _reset_config(CONFIG_FILE)
        sys.exit(0)

    if arguments.show_config:
        ui.emit_always(f"Configuração: {CONFIG_FILE}\nDatabase: {QOBUZ_DB}\n---")
        with open(CONFIG_FILE, encoding="utf-8") as f:
            ui.emit_always(f.read())
        sys.exit(0)

    if arguments.purge:
        try:
            os.remove(QOBUZ_DB)
        except FileNotFoundError:
            pass
        sys.exit(f"{GREEN}O banco de dados foi deletado com sucesso.{OFF}")

    # Subcomando Unificado: AUTH / LOGIN / USER / ACCOUNT / ME / PROFILE
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

    checar_binarios_externos(
        precisa_fpcalc=bool(getattr(arguments, "find_duplicates", None))
    )

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

        await sync_database(sync_dir, QOBUZ_DB, sync_client)
        sys.exit(
            f"\n{GREEN}Sincronização do banco de dados concluída com sucesso.{OFF}"
        )

    if getattr(arguments, "find_duplicates", None):
        try:
            from qobuz_dl.sync import find_duplicate_tracks
        except ImportError as e:
            sys.exit(
                f"{RED}[!] --find-duplicates precisa do pacote extra 'duplicates'.{RESET}\n"
                f"    Instale com: pip install 'qobuz-dl-ultra[duplicates]'\n"
                f"    E instale o Chromaprint: apt install libchromaprint-tools\n"
                f"    (Detalhe técnico: {e})"
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
                f"{RED}[!] --watch precisa do pacote extra 'watch'.{RESET}\n"
                f"    Instale com: pip install 'qobuz-dl-ultra[watch]'\n"
                f"    (Detalhe técnico: {e})"
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

        local_settings = QobuzDLSettings(lrc_files=lrc_pref, embed_lyrics=embed_pref)
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
    settings.legacy_charmap = legacy_charmap

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
        downloads_db=(
            None if no_database or getattr(arguments, "no_db", False) else QOBUZ_DB
        ),
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

    if arguments.command not in (
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


def main():
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        sys.exit(1)


if __name__ == "__main__":
    main()
