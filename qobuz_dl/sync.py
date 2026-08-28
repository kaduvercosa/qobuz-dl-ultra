# # ============================================================================
# # sync.py -- reconstrução do banco local e detecção de faixas duplicadas.
# # Fluxo: varrer biblioteca → extrair IDs/ISRC → gravar no banco ou comparar fingerprints.
# # ============================================================================
import os
import logging
import asyncio
import acoustid
import unicodedata
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen import File
from qobuz_dl.db import handle_download_id
from qobuz_dl.color import GREEN, RED, WARNING as YELLOW, INFO as CYAN, OFF

logger = logging.getLogger(__name__)


# # Reconstrói o banco a partir dos arquivos já baixados, sem repetir chamadas de download.
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
    logger.info(
        f"\n{YELLOW}[*] Iniciando Sincronização do Banco de Dados Local...{OFF}")
    logger.info(f"{YELLOW}[*] Escaneando diretório: {directory}{OFF}")

    # # Caminho absoluto evita problemas com nomes contendo colchetes ou variação de maiúsculas.
    all_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith((".flac", ".mp3")):
                all_files.append(os.path.join(root, file))

    if not all_files:
        logger.info(
            f"{YELLOW}[!] Nenhum arquivo de áudio encontrado em {directory}.{OFF}")
        return

    logger.info(
        f"{YELLOW}[*] {len(all_files)} arquivos de áudio encontrados. Processando tags e metadados...{OFF}"
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

            artist_name = ""
            album_name = ""
            release_date = ""
            bit_depth = None
            sampling_rate = None

            try:
                if file_path.lower().endswith(".flac"):
                    audio = FLAC(file_path)

            # # Tenta primeiro a tag nova (QDL_*); cai para a tag legada (QOBUZ*) se ausente.
                    track_id_list = (
                        audio.get("QDL_TRACK_ID") or audio.get("QOBUZTRACKID") or [None]
                    )
                    track_id = track_id_list[0]

                    album_id_list = (
                        audio.get("QDL_ALBUM_ID") or audio.get("QOBUZALBUMID") or [None]
                    )
                    album_id = album_id_list[0]

                    isrc = audio.get("isrc", [None])[0]

                    artist_name = (audio.get("ALBUMARTIST") or
                                   audio.get("ARTIST") or [""])[0]
                    album_name = audio.get("ALBUM", [""])[0]
                    release_date = audio.get("DATE", [""])[0]
                    bit_depth = getattr(audio.info, "bits_per_sample", 16)
                    sampling_rate = getattr(audio.info, "sample_rate", 44100) / \
                        1000.0 if getattr(audio.info, "sample_rate", None) else None

                elif file_path.lower().endswith(".mp3"):
                    audio = ID3(file_path)

            # # Mesma hierarquia de busca do FLAC, adaptada aos frames TXXX do MP3.
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

                    tpe2 = audio.get("TPE2")
                    tpe1 = audio.get("TPE1")
                    talb = audio.get("TALB")
                    tdrc = audio.get("TDRC") or audio.get("TYER")

                    artist_name = tpe2.text[0] if tpe2 else (
                        tpe1.text[0] if tpe1 else "")
                    album_name = talb.text[0] if talb else ""
                    release_date = str(tdrc.text[0]) if tdrc else ""
                    bit_depth = 16
                    sampling_rate = getattr(audio.info, "sample_rate", 44100) / \
                        1000.0 if getattr(audio.info, "sample_rate", None) else None

        # # Arquivos antigos sem ID embutido são recuperados via busca por ISRC na API.
                if not track_id and isrc:
                    logger.info(
                        f"{CYAN}[*] ID local ausente. Buscando via API (ISRC: {isrc})...{OFF}"
                    )
                    res = await client.search_tracks(isrc, limit=1)
                    if res and "tracks" in res and res["tracks"]["items"]:
                        q_track = res["tracks"]["items"][0]
                        track_id = str(q_track["id"])
                        album_id = str(q_track.get("album", {}).get("id", ""))

                        if not artist_name:
                            artist_name = q_track.get("performer", {}).get("name", "")
                        if not album_name:
                            album_name = q_track.get("album", {}).get("title", "")
                        if not release_date:
                            release_date = q_track.get("album", {}).get(
                                "release_date_original", "")
                        bit_depth = q_track.get("maximum_bit_depth", bit_depth)
                        sampling_rate = q_track.get(
                            "maximum_sampling_rate", sampling_rate)

        # # Pequeno intervalo entre chamadas para não sobrecarregar a API durante a varredura.
                    await asyncio.sleep(0.2)

        # # Registra a faixa como já obtida, evitando que o downloader tente buscá-la de novo.
                if track_id:
                    await handle_download_id(
                        db_path=db_path,
                        item_id=track_id,
                        add_id=True,
                        media_type="track",
                        quality=quality,
                        file_format=file_format,
                        quality_met=1,
                        bit_depth=str(bit_depth) if bit_depth else None,
                        sampling_rate=str(sampling_rate) if sampling_rate else None,
                        release_date=str(release_date),
                        artist=str(artist_name),
                        album=str(album_name),
                        saved_path=file_path,
                    )
                    added_tracks += 1

        # # Evita gravar o mesmo álbum mais de uma vez ao processar várias faixas dele.
                if album_id and album_id not in added_albums:
                    await handle_download_id(
                        db_path=db_path,
                        item_id=album_id,
                        add_id=True,
                        media_type="album",
                        quality=quality,
                        file_format=file_format,
                        quality_met=1,
                        bit_depth=str(bit_depth) if bit_depth else None,
                        sampling_rate=str(sampling_rate) if sampling_rate else None,
                        release_date=str(release_date),
                        artist=str(artist_name),
                        album=str(album_name),
                        saved_path=os.path.dirname(file_path),
                    )
                    added_albums.add(album_id)

            except Exception as e:
                logger.error(f"{RED}[!] Erro ao processar {file_path}: {e}{OFF}")

    except KeyboardInterrupt:
        logger.warning(
            f"\n{YELLOW}[!] Sincronização interrompida forçadamente pelo usuário!{OFF}"
        )
        logger.warning(
            f"{YELLOW}[!] Não se preocupe, todo o progresso até aqui foi salvo com segurança.{OFF}"
        )

    logger.info(
        f"{GREEN}[+] Sincronização concluída! Restauradas {added_tracks} faixas e {len(added_albums)} álbuns no banco de dados local com metadados completos.{OFF}")


# # Prioriza Chromaprint; sem ele, cai em MD5 de áudio, depois ID/ISRC, depois metadados.
def _compute_fingerprint(filepath, max_length=120):
    """
    Tenta calcular a fingerprint via Chromaprint primeiro. Se falhar, usa um
    sistema de 3 níveis de precisão nativo do Python:
    1. FLAC Native Audio MD5 (Perfeito para FLACs, ignora tags e analisa o áudio real)
    2. IDs Universais (QOBUZTRACKID ou ISRC)
    3. Metadados Expandidos (Artista + Álbum + Título + Duração)
    """
    try:
        # # Caminho preferido quando o Chromaprint/fpcalc está disponível no sistema.
        duration, fingerprint = acoustid.fingerprint_file(
            filepath, maxlength=max_length, force_fpcalc=False
        )
        return duration, fingerprint
    except Exception:
        try:
            duration = 0

            if filepath.lower().endswith(".flac"):
                audio_flac = FLAC(filepath)
                duration = int(audio_flac.info.length)

            # # MD5 nativo do FLAC identifica áudio idêntico mesmo com tags diferentes.
                if getattr(audio_flac.info, "md5_signature", 0) != 0:
                    return duration, f"flac_audio_md5:{audio_flac.info.md5_signature}"

                track_id = audio_flac.get("QOBUZTRACKID", [None])[0]
                isrc = audio_flac.get("isrc", [None])[0]

                if track_id:
                    return duration, f"qobuz_id:{track_id}"
                if isrc:
                    return duration, f"isrc:{isrc}"

            elif filepath.lower().endswith(".mp3"):
                audio_id3 = ID3(filepath)

                track_txxx = audio_id3.get("TXXX:QOBUZTRACKID")
                if track_txxx:
                    return duration, f"qobuz_id:{track_txxx.text[0]}"

                tsrc = audio_id3.get("TSRC")
                if tsrc:
                    return duration, f"isrc:{tsrc.text[0]}"

            audio = File(filepath, easy=True)
            if audio is None:
                return None, None

            duration = int(audio.info.length) if hasattr(audio, "info") else duration
            title = audio.get("title", [""])[0] if audio.get("title") else ""
            artist = audio.get("artist", [""])[0] if audio.get("artist") else ""
            album = audio.get("album", [""])[0] if audio.get("album") else ""

            def norm(s):
                return (
                    unicodedata.normalize("NFKD", str(s))
                    .encode("ASCII", "ignore")
                    .decode("utf-8")
                    .lower()
                    .strip()
                )

            # # Último recurso: assinatura baseada em artista, álbum, título e duração normalizados.
            meta_fp = f"meta_hash:{norm(artist)}|{norm(album)}|{norm(title)}|{duration}"
            return duration, meta_fp

        except Exception as meta_err:
            logger.debug(
                f"Falha total ao ler arquivo para fallback '{filepath}': {meta_err}"
            )
            return None, None


# # Agrupa arquivos pela mesma fingerprint para revelar duplicatas reais.
async def find_duplicate_tracks(directory):
    """
    Detecta faixas de audio DUPLICADAS usando um sistema híbrido.
    No desktop, usa a fingerprint acustica (Chromaprint/AcoustID).
    No celular/iPad, usa o Fallback de Metadados (Artista + Título + Duração)
    para comparar os arquivos.
    """
    logger.info(
        f"\n{YELLOW}[*] Escaneando '{directory}' por faixas duplicadas "
        f"(híbrido: AcoustID / Metadados)...{OFF}"
    )

    all_files = []
    for root, _, files in os.walk(directory):
        for file in files:
            if file.lower().endswith((".flac", ".mp3")):
                all_files.append(os.path.join(root, file))

    if not all_files:
        logger.info(
            f"{YELLOW}[!] Nenhum arquivo de áudio encontrado em {directory}.{OFF}"
        )
        return {}

    logger.info(
        f"{YELLOW}[*] Processando as assinaturas de {len(all_files)} arquivo(s)...{OFF}"
    )

    loop = asyncio.get_running_loop()
    fingerprints = {}
    skipped = 0

    for idx, filepath in enumerate(all_files, start=1):
        duration, fp = await loop.run_in_executor(None, _compute_fingerprint, filepath)
        if fp is None:
            skipped += 1
            continue
        fingerprints.setdefault(fp, []).append(filepath)

        if idx % 25 == 0 or idx == len(all_files):
            logger.info(f"{CYAN}    [{idx}/{len(all_files)}] processados...{OFF}")

    # # Só sobra no resultado quem tiver mais de um arquivo com a mesma fingerprint.
    duplicates = {fp: paths for fp, paths in fingerprints.items() if len(paths) > 1}

    if skipped:
        logger.info(
            f"{YELLOW}[!] {skipped} arquivo(s) ignorado(s) (falha total de leitura).{OFF}"
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
            f"{YELLOW}  Grupo idêntico detectado (mesma assinatura de áudio/metadado):{OFF}"
        )
        for p in paths:
            logger.info(f"    - {p}")

    return duplicates
