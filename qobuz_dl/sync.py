import os
import logging
import asyncio
import acoustid
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from qobuz_dl.db import handle_download_id
# CYAN/YELLOW importados como INFO/WARNING renomeados: mesma cor de
# YELLOW (mantida por convencao), mas CYAN agora e' LIGHTBLUE_EX --
# visivel em terminal claro E escuro (CYAN puro quase some em fundo
# branco). Ver comentario completo em qobuz_dl/color.py. Zero mudanca
# de codigo neste arquivo: toda f-string que ja usa {CYAN}/{YELLOW}
# continua funcionando, so' a cor de fato renderizada muda.
from qobuz_dl.color import GREEN, RED, WARNING as YELLOW, INFO as CYAN, OFF

logger = logging.getLogger(__name__)


async def sync_database(directory, db_path, client):
    """
    Executes the Smart Reverse Lookup operation.

    Recursively scans the provided directory for audio files, extracts native QOBUZTRACKID
    and QOBUZALBUMID tags, and reconstructs the SQLite database to prevent future duplicate downloads.
    If custom tags are missing, it falls back to querying the Qobuz API using the embedded ISRC code.

    Args:
        directory (str): The root directory containing the user's downloaded music library.
        db_path (str): The local path to the target SQLite database file.
        client (Client): The initialized Qobuz API client for fallback ISRC lookups.
    """
    logger.info(f"\n{YELLOW}[*] Starting Local Database Synchronization...{OFF}")
    logger.info(f"{YELLOW}[*] Scanning directory: {directory}{OFF}")

    # --- PATCH OS.WALK: Immune a parentesi quadre e case-insensitive ---
    all_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith((".flac", ".mp3")):
                all_files.append(os.path.join(root, file))
    # -------------------------------------------------------------------

    if not all_files:
        logger.info(f"{YELLOW}[!] No audio files found in {directory}.{OFF}")
        return

    logger.info(
        f"{YELLOW}[*] Found {len(all_files)} audio files. Processing tags...{OFF}"
    )

    added_tracks = 0
    added_albums = set()

    try:
        for file_path in all_files:
            track_id = None
            album_id = None
            isrc = None
            quality = 27
            file_format = "FLAC" if file_path.lower().endswith(".flac") else "MP3"

            try:
                if file_path.lower().endswith(".flac"):
                    audio = FLAC(file_path)

                    # --- RICERCA GERARCHICA FLAC (Stealth -> Legacy) ---
                    track_id_list = (
                        audio.get("QDL_TRACK_ID") or audio.get("QOBUZTRACKID") or [None]
                    )
                    track_id = track_id_list[0]

                    album_id_list = (
                        audio.get("QDL_ALBUM_ID") or audio.get("QOBUZALBUMID") or [None]
                    )
                    album_id = album_id_list[0]

                    isrc = audio.get("isrc", [None])[0]

                elif file_path.lower().endswith(".mp3"):
                    audio = ID3(file_path)

                    # --- RICERCA GERARCHICA MP3 (Stealth -> Legacy) ---
                    track_txxx = (
                        audio.get("TXXX:QDL_TRACK_ID") or
                        audio.get("TXXX:qdl_track_id") or
                        audio.get("TXXX:QOBUZTRACKID")
                    )
                    if track_txxx:
                        track_id = track_txxx.text[0]

                    album_txxx = (
                        audio.get("TXXX:QDL_ALBUM_ID") or
                        audio.get("TXXX:qdl_album_id") or
                        audio.get("TXXX:QOBUZALBUMID")
                    )
                    if album_txxx:
                        album_id = album_txxx.text[0]

                    tsrc = audio.get("TSRC")
                    if tsrc:
                        isrc = tsrc.text[0]

                # --- REVERSE LOOKUP VIA API FOR OLD FILES ---
                if not track_id and isrc:
                    logger.info(
                        f"{CYAN}[*] Missing local ID. Fetching via API (ISRC: {isrc})...{OFF}"
                    )
                    # search_tracks() e' async def em qopy.py -- antes era
                    # chamado sem 'await', entao 'res' era um objeto de
                    # corrotina nunca executado (o reverse lookup por ISRC
                    # nunca funcionava de verdade, so' falhava silenciosamente
                    # no except abaixo).
                    res = await client.search_tracks(isrc, limit=1)
                    if res and "tracks" in res and res["tracks"]["items"]:
                        q_track = res["tracks"]["items"][0]
                        track_id = str(q_track["id"])
                        album_id = str(q_track.get("album", {}).get("id", ""))

                    # Human behavior delay to prevent Qobuz API throttling and hanging
                    await asyncio.sleep(0.2)

                # Inject Track ID into DB
                if track_id:
                    await handle_download_id(
                        db_path=db_path,
                        item_id=track_id,
                        add_id=True,
                        media_type="track",
                        quality=quality,
                        file_format=file_format,
                        saved_path=file_path,
                    )
                    added_tracks += 1

                # Inject Album ID into DB
                if album_id and album_id not in added_albums:
                    await handle_download_id(
                        db_path=db_path,
                        item_id=album_id,
                        add_id=True,
                        media_type="album",
                        quality=quality,
                        file_format=file_format,
                        saved_path=os.path.dirname(file_path),
                    )
                    added_albums.add(album_id)

            except Exception as e:
                logger.error(f"{RED}[!] Error processing {file_path}: {e}{OFF}")

    except KeyboardInterrupt:
        logger.warning(
            f"\n{YELLOW}[!] Synchronization forcibly interrupted by user!{OFF}"
        )
        logger.warning(
            f"{YELLOW}[!] Don't worry, all progress up to this point has been safely saved.{OFF}"
        )

    logger.info(
        f"{GREEN}[+] Sync complete! Restored {added_tracks} tracks and {
            len(added_albums)} albums into the local database.{OFF}"
    )


def _compute_fingerprint(filepath, max_length=120):
    """
    Calcula a fingerprint acustica (Chromaprint) de um arquivo de audio.

    IMPORTANTE: requer o binario `fpcalc` do Chromaprint instalado no
    SISTEMA (nao vem junto do `pip install pyacoustid` -- e' uma
    dependencia externa, no mesmo espirito do ffmpeg que o projeto ja
    depende em outros lugares pra remux/verificacao de integridade). Sem
    o fpcalc no PATH, isso falha graciosamente (retorna None, None) em
    vez de quebrar o scan inteiro.

    Roda em thread separada (ver find_duplicate_tracks) porque e' uma
    chamada bloqueante de subprocess -- fingerprinting decodifica o
    audio inteiro, nao e' instantaneo.
    """
    try:
        duration, fingerprint = acoustid.fingerprint_file(
            filepath, maxlength=max_length, force_fpcalc=False)
        return duration, fingerprint
    except acoustid.FingerprintGenerationError as e:
        logger.debug(f"Falha ao gerar fingerprint de '{filepath}': {e}")
        return None, None
    except FileNotFoundError as e:
        logger.debug(f"fpcalc (Chromaprint) nao encontrado no sistema: {e}")
        return None, None
    except Exception as e:
        logger.debug(f"Erro inesperado ao gerar fingerprint de '{filepath}': {e}")
        return None, None


async def find_duplicate_tracks(directory):
    """
    Detecta faixas de audio DUPLICADAS DE VERDADE comparando fingerprint
    acustica (Chromaprint/AcoustID), em vez de so' tags/ISRC como
    sync_database() faz. Pega casos que o matching por tag deixaria
    passar -- por exemplo, a mesma faixa baixada duas vezes com tags
    diferentes (re-tag manual, fonte diferente) ou com ISRC ausente/
    incorreto, mas que sao acusticamente o MESMO audio.

    So compara fingerprints ENTRE os arquivos encontrados localmente
    (nao consulta a base de dados online do AcoustID) -- nao precisa de
    API key nem de rede, e' 100% local/privado.

    Nao apaga nada automaticamente. So reporta os grupos de arquivos com
    fingerprint identica encontrados, pra decisao manual do usuario.

    Args:
        directory (str): Pasta raiz a escanear recursivamente.

    Returns:
        dict: mapeia fingerprint -> lista de caminhos de arquivo
            (somente grupos com mais de 1 arquivo, ou seja, duplicatas
            de verdade). Dict vazio se nao achou nenhuma.
    """
    logger.info(
        f"\n{YELLOW}[*] Escaneando '{directory}' por faixas duplicadas "
        f"(fingerprint de audio via Chromaprint/AcoustID)...{OFF}"
    )

    all_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith((".flac", ".mp3")):
                all_files.append(os.path.join(root, file))

    if not all_files:
        logger.info(
            f"{YELLOW}[!] Nenhum arquivo de áudio encontrado em {directory}.{OFF}")
        return {}

    logger.info(
        f"{YELLOW}[*] Calculando fingerprint de {len(all_files)} arquivo(s)...{OFF}")

    loop = asyncio.get_running_loop()
    fingerprints = {}
    skipped = 0

    for idx, filepath in enumerate(all_files, start=1):
        # run_in_executor: fpcalc e' um subprocess bloqueante, nao pode
        # rodar direto na coroutine sem travar o event loop -- mesmo
        # padrao ja usado pro resto do I/O sincrono no projeto.
        duration, fp = await loop.run_in_executor(None, _compute_fingerprint, filepath)
        if fp is None:
            skipped += 1
            continue
        fingerprints.setdefault(fp, []).append(filepath)

        if idx % 25 == 0 or idx == len(all_files):
            logger.info(f"{CYAN}    [{idx}/{len(all_files)}] processados...{OFF}")

    duplicates = {fp: paths for fp, paths in fingerprints.items() if len(paths) > 1}

    if skipped:
        logger.info(
            f"{YELLOW}[!] {skipped} arquivo(s) pulado(s) (fpcalc ausente ou "
            f"falha na leitura -- ver log em modo debug).{OFF}"
        )

    if not duplicates:
        logger.info(f"{GREEN}[✓] Nenhuma faixa duplicada encontrada.{OFF}")
        return {}

    total_redundant = sum(len(paths) - 1 for paths in duplicates.values())
    logger.info(
        f"{RED}[!] {len(duplicates)} grupo(s) de duplicatas encontrados "
        f"({total_redundant} arquivo(s) redundante(s)):{OFF}"
    )
    for paths in duplicates.values():
        logger.info(
            f"{YELLOW}  Grupo idêntico (áudio igual, arquivos diferentes):{OFF}")
        for p in paths:
            logger.info(f"    - {p}")

    return duplicates
