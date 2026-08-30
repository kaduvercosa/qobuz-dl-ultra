# # ============================================================================
# # sync_playlist.py -- sincronização bidirecional entre playlist Qobuz e pasta local.
# # Compara IDs, baixa faixas ausentes, remove órfãs e atualiza o arquivo .m3u.
# # ============================================================================
import os
import logging
from mutagen.flac import FLAC
from mutagen.id3 import ID3

from qobuz_dl.color import INFO as CYAN, GREEN, RED, WARNING as YELLOW, OFF

logger = logging.getLogger(__name__)


# # Varre FLAC/MP3 e cria mapa Qobuz track ID → caminho local; separa arquivos sem tag.
def _scan_local_tracks(directory):
    """
    Varre um diretório local recursivamente para mapear os arquivos de áudio existentes
    usando seus IDs de faixa Qobuz embutidos nas tags.

    Args:
        directory (str): O caminho do diretório local a ser varrido.

    Returns:
        tuple: Uma tupla contendo:
            - dict: Um mapeamento de IDs de faixa Qobuz extraídos para seus caminhos locais.
            - list: Uma lista de caminhos de arquivos que não possuem uma tag de ID de faixa Qobuz válida.
    """
    local_tracks = {}
    untagged_files = []

    for root, _, files in os.walk(directory):
        for fname in files:
            if not fname.lower().endswith((".flac", ".mp3")):
                continue

            fpath = os.path.join(root, fname)
            track_id = None

            try:
                if fpath.lower().endswith(".flac"):
                    audio = FLAC(fpath)
                    track_id_list = (
                        audio.get("QDL_TRACK_ID") or audio.get("QOBUZTRACKID") or [None]
                    )
                    track_id = track_id_list[0]
                else:
                    audio = ID3(fpath)
                    track_txxx = (
                        audio.get("TXXX:QDL_TRACK_ID")
                        or audio.get("TXXX:qdl_track_id")
                        or audio.get("TXXX:QOBUZTRACKID")
                    )
                    if track_txxx:
                        track_id = track_txxx.text[0]
            except Exception as e:
                logger.debug(f"Falha ao ler tags de {fpath}: {e}")

            if track_id:
                local_tracks[str(track_id)] = fpath
            else:
                untagged_files.append(fpath)

    return local_tracks, untagged_files


# # Consome o gerador paginado do cliente e junta todas as faixas da playlist.
async def _fetch_remote_tracks(client, playlist_id):
    """
    Recupera a lista completa de faixas e os metadados de uma playlist a partir da API Qobuz.

    Args:
        client (Client): A instância inicializada do cliente da API Qobuz.
        playlist_id (str): O identificador único da playlist alvo.

    Returns:
        tuple: Uma tupla contendo:
            - str: O nome resolvido da playlist.
            - list: Uma lista de dicionários contendo os metadados de cada faixa.
    """
    all_items = []
    playlist_name = "Unknown Playlist"
    # # get_plist_meta() é async generator; async for é obrigatório para paginação.
    async for chunk in client.get_plist_meta(playlist_id):
        if "name" in chunk and playlist_name == "Unknown Playlist":
            playlist_name = chunk.get("name")
        items = chunk.get("tracks", {}).get("items", [])
        all_items.extend(items)
    return playlist_name, all_items


# # Substitui caracteres inválidos para manter o nome da pasta portátil entre sistemas.
def _sanitize_dirname(name):
    """
    Remove caracteres ilegais para criar nomes de diretório seguros entre diferentes
    sistemas operacionais.

    Args:
        name (str): A string original do diretório.

    Returns:
        str: A string do diretório sanitizada.
    """
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "_")
    return name.strip()


# # Remove diretórios vazios após exclusões, preservando _Playlists.
def _clean_empty_dirs(base_directory, exclude_dirs=None):
    """
    Exclui recursivamente subdiretórios vazios deixados para trás após a sincronização
    das faixas.

    Args:
        base_directory (str): O diretório raiz a ser avaliado.
        exclude_dirs (set, optional): Um conjunto de nomes de diretório a proteger contra
            exclusão. O padrão é None.
    """
    exclude = set(exclude_dirs or [])
    exclude.add("_Playlists")

    for root, dirs, _files in os.walk(base_directory, topdown=False):
        for d in dirs:
            dir_path = os.path.join(root, d)
            try:
                if d in exclude:
                    continue
                if not os.listdir(dir_path):
                    os.rmdir(dir_path)
                    rel = os.path.relpath(dir_path, base_directory)
                    logger.info(f"  {RED}[-] Diretório vazio removido: {rel}{OFF}")
            except OSError:
                pass


# # Fluxo principal: valida URL → busca remoto → compara local → confirma → sincroniza.
async def sync_playlist(qobuz_dl, url, folder, auto_confirm=False):
    """
    O motor principal de Sincronização Bidirecional de Playlist.

    Espelha uma playlist do Qobuz localmente, identificando faixas ausentes (a serem
    baixadas) e faixas órfãs (a serem excluídas fisicamente, junto com os arquivos .lrc
    associados). Mantém uma arquitetura de "Pasta Plana" e atualiza automaticamente o
    arquivo de playlist .m3u.

    Args:
        qobuz_dl (QobuzDL): A instância principal da aplicação.
        url (str): A URL válida da playlist Qobuz.
        folder (str): O diretório alvo base no sistema local.
        auto_confirm (bool, optional): Se True, ignora o prompt de confirmação interativo.
            O padrão é False.
    """
    from qobuz_dl.utils import get_url_info, make_m3u

    try:
        url_type, playlist_id = get_url_info(url)
    except (AttributeError, IndexError):
        logger.error(f"{RED}URL inválida: {url}{OFF}")
        return

    if url_type != "playlist":
        logger.error(
            f"{RED}A URL não é uma playlist (tipo detectado: '{url_type}'). "
            f"Use uma URL de playlist como https://play.qobuz.com/playlist/12345{OFF}"
        )
        return

    logger.info(f"\n{YELLOW}━━━ SINCRONIZAÇÃO DE PLAYLIST ━━━{OFF}")
    logger.info(f"{YELLOW}URL : {url}{OFF}")

    logger.info(f"{CYAN}[1/4] Buscando playlist no Qobuz...{OFF}")
    playlist_name, remote_items = await _fetch_remote_tracks(
        qobuz_dl.client, playlist_id
    )
    # # IDs remotos tornam a diferença entre playlist e pasta local O(n).
    remote_ids = {str(item["id"]): item for item in remote_items}
    logger.info(
        f"{CYAN}      Encontradas {len(remote_ids)} faixas na playlist do Qobuz.{OFF}"
    )

    if not remote_ids:
        logger.info(
            f"{YELLOW}A playlist do Qobuz está vazia. Nada para sincronizar.{OFF}"
        )
        return

    safe_playlist_name = _sanitize_dirname(playlist_name)
    base_name = os.path.basename(os.path.normpath(folder))

    if base_name == safe_playlist_name:
        target_folder = folder
    else:
        target_folder = os.path.join(folder, safe_playlist_name)

    logger.info(f"{YELLOW}DIR : {target_folder}{OFF}\n")

    os.makedirs(target_folder, exist_ok=True)
    logger.info(f"{CYAN}[2/4] Escaneando pasta local...{OFF}")
    local_tracks, untagged = _scan_local_tracks(target_folder)
    logger.info(
        f"{CYAN}      Encontradas {len(local_tracks)} faixas taggeadas localmente.{OFF}"
    )
    if untagged:
        logger.info(
            f"{YELLOW}      {len(untagged)} arquivos não possuem tag QOBUZTRACKID "
            f"e serão ignorados.{OFF}"
        )

    local_id_set = set(local_tracks.keys())
    remote_id_set = set(remote_ids.keys())

    # # Faixas remotas ausentes precisam ser baixadas; IDs locais ausentes do remoto são órfãos.
    to_download_ids = remote_id_set - local_id_set
    to_delete_ids = local_id_set - remote_id_set
    already_synced = local_id_set & remote_id_set

    logger.info(f"\n{CYAN}[3/4] Resumo da sincronização:{OFF}")
    logger.info(f"  {GREEN}↓ A baixar   : {len(to_download_ids)} faixas{OFF}")
    logger.info(f"  {RED}✕ A excluir  : {len(to_delete_ids)} arquivos{OFF}")
    logger.info(f"    Já sincronizadas: {len(already_synced)} faixas")

    if not to_download_ids and not to_delete_ids:
        logger.info(f"\n{GREEN}✓ A pasta já está sincronizada com a playlist!{OFF}")

        if not getattr(qobuz_dl, "no_m3u_for_playlists", False):
            make_m3u(target_folder, remote_items)
            logger.info(
                f"{CYAN}✓ Arquivo .m3u da playlist atualizado com a ordem mais recente das faixas.{OFF}"
            )
        return

    if to_delete_ids:
        logger.info(f"\n{RED}Arquivos a EXCLUIR:{OFF}")
        for tid in sorted(to_delete_ids):
            logger.info(f"  {RED}✕ {os.path.basename(local_tracks[tid])}{OFF}")

    if to_download_ids:
        logger.info(f"\n{GREEN}Faixas a BAIXAR:{OFF}")
        for tid in sorted(to_download_ids):
            item = remote_ids[tid]
            album_artist = item.get("album", {}).get("artist", {}).get("name")
            performer_name = item.get("performer", {}).get("name", "Unknown")
            artist = (
                performer_name
                if album_artist in [None, "Various Artists"]
                else album_artist
            )
            title = item.get("title", "Unknown")
            logger.info(f"  {GREEN}↓ {artist} -- {title}{OFF}")

    # # Exclusões são destrutivas; sem auto_confirm, exige confirmação explícita do usuário.
    if not auto_confirm:
        try:
            answer = (
                input(f"\n{YELLOW}Prosseguir com a sincronização? [y/N]: {OFF}")
                .strip()
                .lower()
            )
            if answer != "y":
                logger.info(f"{YELLOW}Sincronização cancelada pelo usuário.{OFF}")
                return
        except (KeyboardInterrupt, EOFError):
            logger.info(f"\n{YELLOW}Sincronização cancelada.{OFF}")
            return

    logger.info(f"\n{CYAN}[4/4] Executando sincronização...{OFF}")

    deleted_count = 0
    # # Remove áudio órfão e seu .lrc associado, depois limpa diretórios vazios.
    for tid in to_delete_ids:
        fpath = local_tracks[tid]
        try:
            os.remove(fpath)
            deleted_count += 1
            logger.info(f"  {RED}[-] Excluído: {os.path.basename(fpath)}{OFF}")

            lrc_path = os.path.splitext(fpath)[0] + ".lrc"
            if os.path.isfile(lrc_path):
                os.remove(lrc_path)
                logger.info(f"  {RED}[-] Excluído: {os.path.basename(lrc_path)}{OFF}")
        except OSError as e:
            logger.error(f"  {RED}[!] Falha ao excluir {fpath}: {e}{OFF}")

    _clean_empty_dirs(target_folder, exclude_dirs={"_Playlists"})

    # # Temporariamente força pasta plana para que faixas de playlist não sejam organizadas como álbuns.
    original_folder_format = qobuz_dl.folder_format
    original_multi_disc = qobuz_dl.settings.multiple_disc_one_dir
    qobuz_dl.folder_format = "."
    qobuz_dl.settings.multiple_disc_one_dir = True

    # # Preserva a ordem remota para numerar faixas no download e no .m3u.
    position_map = {}
    for idx, item in enumerate(remote_items, start=1):
        position_map[str(item["id"])] = idx

    downloaded_count = 0
    for tid in to_download_ids:
        playlist_idx = position_map.get(tid, 0)
        try:
            # # A chamada é assíncrona: await garante que a faixa terminou antes de contar sucesso.
            await qobuz_dl.download_from_id(
                tid,
                album=False,
                alt_path=target_folder,
                is_playlist=True,
                playlist_index=playlist_idx,
            )
            downloaded_count += 1
        except Exception as e:
            logger.error(f"  {RED}[!] Falha ao baixar faixa {tid}: {e}{OFF}")

    # # Restaura as configurações originais mesmo após o bloco de downloads.
    qobuz_dl.folder_format = original_folder_format
    qobuz_dl.settings.multiple_disc_one_dir = original_multi_disc

    if not getattr(qobuz_dl, "no_m3u_for_playlists", False):
        make_m3u(target_folder, remote_items)

    logger.info(f"\n{GREEN}━━━ SINCRONIZAÇÃO CONCLUÍDA ━━━{OFF}")
    logger.info(f"  {GREEN}↓ Baixadas  : {downloaded_count} faixas{OFF}")
    logger.info(f"  {RED}✕ Excluídas : {deleted_count} arquivos{OFF}")
    logger.info(f"  {GREEN}✓ Total agora: {len(remote_ids)} faixas{OFF}\n")
