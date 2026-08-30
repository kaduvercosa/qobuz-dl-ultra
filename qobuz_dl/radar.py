import configparser
import urllib.request
import xml.etree.ElementTree as ET
import asyncio
import questionary
from qobuz_dl.qopy import Client

# CYAN/YELLOW importados como INFO/WARNING renomeados: mesma cor de
# YELLOW (mantida por convencao), mas CYAN agora e' LIGHTBLUE_EX --
# visivel em terminal claro E escuro (CYAN puro quase some em fundo
# branco). Ver comentario completo em qobuz_dl/color.py. Zero mudanca
# de codigo neste arquivo: toda f-string que ja usa {CYAN}/{YELLOW}
# continua funcionando, so' a cor de fato renderizada muda.
from qobuz_dl.color import GREEN, WARNING as YELLOW, RED, INFO as CYAN, OFF
from qobuz_dl.utils import get_config_paths
from qobuz_dl import ui


async def setup_client(config, section):
    """
    Initializes the Qobuz client reading from the config file.

    Antes construia Client(...) direto, pulando a inicializacao assincrona
    (Client.create()) que de fato autentica e valida os secrets -- o client
    resultante nunca funcionava corretamente. Agora usa o mesmo caminho
    assincrono que o resto do app usa (ver cli.py/core.py).
    """
    app_id = config.get(section, "app_id")
    # secrets no config.ini e' uma string separada por virgula -- precisa
    # virar lista, igual ao resto do app faz (ver cli.py).
    secrets = [s for s in config.get(section, "secrets", fallback="").split(",") if s]
    auth_token = config.get(section, "auth_token", fallback="")
    email = config.get(section, "email", fallback="") or None
    pwd = config.get(section, "password", fallback="") or None

    api = await Client.create(email, pwd, app_id, secrets, user_auth_token=auth_token)
    return api


async def get_or_save_rss_link(config_path, config, section):
    """Retrieves the RSS link or asks the user for it on first run."""
    if config.has_option(section, "musicbutler_rss"):
        rss_link = config.get(section, "musicbutler_rss").strip()
        if rss_link:
            return rss_link

    ui.emit(f"{YELLOW}[!] No RSS feed found.{OFF}")
    # ask_async() em vez de ask() -- nao bloqueia o event loop enquanto
    # espera o usuario digitar (relevante se, no futuro, algo mais estiver
    # rodando concorrentemente).
    rss_link = await questionary.text(
        "Paste your private MusicButler RSS link here:"
    ).ask_async()

    if rss_link:
        config.set(section, "musicbutler_rss", rss_link)
        with open(config_path, "w") as configfile:
            config.write(configfile)
        ui.emit(f"{GREEN}[+] Link permanently saved to config!{OFF}\n")

    return rss_link


def _fetch_rss_releases_sync(rss_url):
    """Downloads and parses the RSS/Atom feed ignoring XML namespaces (blocking)."""
    ui.emit(f"{CYAN}[*] Syncing with MusicButler...{OFF}")
    try:
        req = urllib.request.Request(
            rss_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        )
        with urllib.request.urlopen(req) as response:
            xml_data = response.read()

        root = ET.fromstring(xml_data)
        releases = []

        for elem in root.iter():
            tag = elem.tag.split("}")[-1]

            if tag in ["item", "entry"]:
                for child in elem.iter():
                    child_tag = child.tag.split("}")[-1]
                    if child_tag == "title" and child.text:
                        releases.append(child.text.strip())
                        break

        return releases
    except Exception as e:
        ui.emit(f"{RED}[!] Error reading RSS feed: {e}{OFF}")
        return []


async def fetch_rss_releases(rss_url):
    """
    Async wrapper around the blocking RSS fetch/parse -- offloaded via
    run_in_executor pra nao travar o event loop durante o request de rede.
    """
    # BUGFIX: get_event_loop() dentro de corrotina esta' deprecado (3.10+).
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _fetch_rss_releases_sync, rss_url)


async def run_radar():
    """Main execution function for the radar command."""
    # Antes usava exclusivamente os.getenv('APPDATA'), que so' existe no
    # Windows -- em qualquer outro SO (Linux, macOS, iOS/A-Shell) isso
    # retornava None e quebrava na hora seguinte (os.path.join(None, ...)).
    # Agora reaproveita a mesma deteccao cross-platform usada pelo resto do
    # app (cli.py), incluindo o suporte a A-Shell via QOBUZ_DL_IOS_HOME.
    config_file = get_config_paths()["config_file"]

    config = configparser.ConfigParser()
    config.read(config_file)
    if not config.sections():
        ui.emit(f"{RED}[!] config.ini file not found at {config_file}{OFF}")
        return

    section = config.sections()[0]

    # 1. RSS Link Management
    rss_url = await get_or_save_rss_link(config_file, config, section)
    if not rss_url:
        ui.emit(f"{RED}[!] Operation cancelled. No link provided.{OFF}")
        return

    # 2. Connect to Qobuz API
    api = None
    try:
        api = await setup_client(config, section)
    except Exception as e:
        ui.emit(f"{RED}[!] Connection error to Qobuz: {e}{OFF}")
        return

    try:
        # 3. Download and parse RSS
        releases = await fetch_rss_releases(rss_url)

        if not releases:
            ui.emit(f"{YELLOW}[!] No new releases found in the feed.{OFF}")
            return

        ui.emit(
            f"{GREEN}[+] Found {len(releases)} new releases! Searching on Qobuz...{OFF}\n"
        )

        # 4. Search on Qobuz and prepare the UI menu
        # Sequencial de proposito (nao paralelizado): evita disparar muitas
        # buscas simultaneas contra a API do Qobuz de uma vez (mesma logica
        # de "human behavior" usada no resto do app, ex: delay entre
        # downloads em core.py).
        choices = []
        for release_title in releases:
            search_result = await api.search_albums(release_title, limit=1)

            if (
                search_result
                and "albums" in search_result
                and search_result["albums"]["items"]
            ):
                album_data = search_result["albums"]["items"][0]
                album_id = album_data["id"]

                artist = album_data.get("artist", {}).get("name", "Unknown")
                title = album_data.get("title", "Unknown")
                display_name = f"{artist} - {title}"

                choices.append(questionary.Choice(title=display_name, value=album_id))
            else:
                ui.emit(f"{YELLOW}[!] Not found on Qobuz: {release_title}{OFF}")

        if not choices:
            ui.emit(
                f"{YELLOW}\n[!] None of the releases in the feed are currently available on Qobuz.{OFF}"
            )
            return

        # 5. Interactive UI Menu
        ui.emit("\n")
        selected_album_ids = await questionary.checkbox(
            "🎧 Select releases to add to Favorites (Space to select, Enter to confirm):",
            choices=choices,
        ).ask_async()

        if not selected_album_ids:
            ui.emit(f"{YELLOW}[*] No albums selected. Exiting.{OFF}")
            return

        # 6. Add to Favorites
        ui.emit(
            f"\n{CYAN}[*] Adding {len(selected_album_ids)} albums to favorites...{OFF}"
        )
        for album_id in selected_album_ids:
            try:
                await api.add_favorite_album(album_id)
                ui.emit(f"{GREEN}  [+] Added: ID {album_id}{OFF}")
            except Exception as e:
                ui.emit(f"{RED}  [-] Error with ID {album_id}: {e}{OFF}")

        ui.emit(
            f"\n{GREEN}✅ Operation complete! You can now run qobuz-dl to download them.{OFF}"
        )
    finally:
        if api is not None:
            try:
                await api.close()
            except Exception:
                pass
