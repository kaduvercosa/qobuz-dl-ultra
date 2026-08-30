# ============================================================================
# retro_tagger.py -- inspecao e atualizacao retroativa de letras na biblioteca.
# Fluxo: localizar IDs/metadados -> consultar Qobuz -> atualizar letras -> relatorio.
# ============================================================================
import os
import re
import time
import logging
from mutagen.flac import FLAC
import mutagen.id3 as id3

from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.lyrics_engine import LyricsEngine
from qobuz_dl import ui
import shutil as _shutil
from qobuz_dl.color import INFO as CYAN, GREEN, WARNING as YELLOW, RED, OFF, RESET, BG

logger = logging.getLogger(__name__)


def extract_track_id(file_path: str) -> str | None:
    """
    Extrai o ID Qobuz de tags FLAC/MP3 ou do comentario tecnico.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".flac":
        try:
            audio = FLAC(file_path)
            for tag in ["QOBUZTRACKID", "QOBUZ TRACK ID", "TRACK_ID", "QOBUZ_TRACK_ID"]:
                val = audio.get(tag)
                if val and str(val[0]).strip():
                    return str(val[0]).strip()
            for comment in audio.get("COMMENT", []):
                m = re.search(r"Trk ID:\s*([0-9a-zA-Z]+)", str(comment), re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception as e:
            logger.debug(
                f"Falha ao extrair Trk ID do COMMENT (FLAC), tag provavelmente ausente: {e}"
            )

    elif ext == ".mp3":
        try:
            audio = id3.ID3(file_path)
            for frame in audio.getall("TXXX"):
                desc_clean = frame.desc.upper().replace(" ", "").replace("_", "")
                if desc_clean in ["QOBUZTRACKID", "TRACKID", "QOBUZTRACK"]:
                    if frame.text and str(frame.text[0]).strip():
                        return str(frame.text[0]).strip()
            for frame in audio.getall("COMM"):
                text = str(frame.text[0]) if frame.text else ""
                m = re.search(r"Trk ID:\s*([0-9a-zA-Z]+)", text, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception as e:
            logger.debug(
                f"Falha ao extrair Trk ID do frame COMM (MP3), tag provavelmente ausente: {e}"
            )

    return None


def inspect_existing_lyrics(file_path: str) -> dict:
    """
    Inspeciona letras embutidas e arquivos .lrc/.txt sem modificar o audio.
    """
    ext = os.path.splitext(file_path)[1].lower()
    base_name = os.path.splitext(file_path)[0]
    lrc_path = f"{base_name}.lrc"
    txt_path = f"{base_name}.txt"

    embedded = ""
    embedded_lang = None
    if ext == ".flac":
        try:
            audio = FLAC(file_path)
            embedded = (
                audio.get("LYRICS", [""])[0] or audio.get("UNSYNCEDLYRICS", [""])[0]
            )
            lang_vals = audio.get("LYRICS_LANG")
            if lang_vals:
                embedded_lang = str(lang_vals[0]).strip().lower() or None
        except Exception as e:
            logger.debug(f"Falha ao ler letra/idioma embutidos no FLAC: {e}")
    elif ext == ".mp3":
        try:
            audio = id3.ID3(file_path)
            for uslt in audio.getall("USLT"):
                if uslt.text:
                    embedded = uslt.text
                    break
            for txxx in audio.getall("TXXX:LYRICS_LANG"):
                if txxx.text:
                    embedded_lang = str(txxx.text[0]).strip().lower() or None
                    break
        except Exception as e:
            logger.debug(f"Falha ao ler letra/idioma embutidos no MP3 (USLT/TXXX): {e}")

    file_lyrics = ""
    file_lang = None
    if os.path.exists(lrc_path):
        try:
            with open(lrc_path, "r", encoding="utf-8", errors="ignore") as f:
                file_lyrics = f.read()
            m = re.search(r"\[la:\s*([a-zA-Z+\-]+)\s*\]", file_lyrics)
            if m:
                file_lang = m.group(1).strip().lower()
        except Exception as e:
            logger.debug(f"Falha ao ler arquivo .lrc externo: {e}")
    elif os.path.exists(txt_path) and "Tracklist" not in txt_path:
        try:
            with open(txt_path, "r", encoding="utf-8", errors="ignore") as f:
                file_lyrics = f.read()
        except Exception as e:
            logger.debug(f"Falha ao ler arquivo .txt de letra externo: {e}")

    lyrics_content = (embedded or file_lyrics).strip()
    has_lyrics = bool(lyrics_content)
    language = embedded_lang or file_lang

    is_bilingual = False
    if has_lyrics:
        if " » " in lyrics_content or re.search(
            r"---\s*TRADU[CÇ§][AÃ§]O", lyrics_content, re.IGNORECASE
        ):
            is_bilingual = True
        elif language and "+" in language:
            is_bilingual = True

    return {
        "has_lyrics": has_lyrics,
        "is_bilingual": is_bilingual,
        "content": lyrics_content,
        "lrc_exists": os.path.exists(lrc_path),
        "language": language,
    }


async def fetch_qobuz_lyrics_raw(client, track_id, language=None):
    """
    Consulta diretamente o endpoint de letras do Qobuz usando assinatura do cliente.
    [FIX] Corrigido de r.status para r.status_code.
    """
    try:
        params = {"track_id": track_id}
        if language:
            params["language"] = language
        params["request_ts"] = int(time.time())
        params["request_sig"] = client._modern_sig(
            "track/lyricsUrl", params, client.sec
        )

        r = await client.session.request(
            "get", client.base + "track/lyricsUrl", params=params
        )

        if r.status_code != 200:
            return None
        meta = r.json()

        lyrics_url = meta.get("url") or meta.get("lyrics_url")
        if not lyrics_url:
            for k, v in meta.items():
                if "url" in k.lower() and isinstance(v, str):
                    lyrics_url = v
                    break

        if not lyrics_url:
            return None

        resp = await client.session.get(lyrics_url, timeout=12.0)

        if resp.status_code == 200:
            return resp.json()

        return None

    except Exception as e:
        logger.debug(f"Falha ao baixar/assinar JSON de letra: {e}")
        return None


async def process_retroactive_lyrics_async(
    directory_path, client, genius_token=None, settings=None
):
    """
    Varre a biblioteca e decide se cada faixa precisa de letra, traducao ou correcao.
    [FIX] Agora fecha a engine no finally e verifica retorno de fetch_and_inject().
    """
    if settings is None:
        settings = QobuzDLSettings()

    target_lang = getattr(settings, "lyrics_translation_lang", "pt")
    save_lrc = getattr(settings, "lrc_files", True)
    embed_lyrics = getattr(settings, "embed_lyrics", True)

    lang_display = (
        target_lang.upper() if target_lang else "ORIGINAL (Sem traducao forcada)"
    )
    ui.emit(
        f"\n{CYAN}[*] Iniciando verificacao e atualizacao de letras no Qobuz...{OFF}"
    )
    ui.emit(f"{CYAN} • Pasta raiz :{RESET} {directory_path}")
    ui.emit(f"{CYAN} • Idioma alvo:{RESET} {lang_display}\n")

    engine = LyricsEngine(genius_token=genius_token, settings=settings)

    try:
        # Reune primeiro todos os arquivos para ordenar o processamento e calcular o total.
        files_to_check = []
        for root, _, files in os.walk(directory_path):
            for f in files:
                if f.lower().endswith((".flac", ".mp3")):
                    files_to_check.append(os.path.join(root, f))

        files_to_check.sort()

        # Guarda uma linha detalhada por arquivo para auditoria das alteracoes.
        report_items = []

        # Contadores usados no relatorio final; cada caminho de decisao incrementa um deles.
        stats = {
            "total": len(files_to_check),
            "updated_new_original": 0,
            "updated_new_pt": 0,
            "updated_to_bilingual": 0,
            "updated_bilingual_direct": 0,
            "updated_fallback": 0,
            "unchanged_already_bilingual": 0,
            "unchanged_already_pt": 0,
            "unchanged_no_trans_yet": 0,
            "not_found": 0,
            "corrected_wrong_language": 0,
        }

        for file_path in files_to_check:
            file_name = os.path.basename(file_path)
            ext = os.path.splitext(file_path)[1].lower()

            title, artist, album = "", "", ""
            if ext == ".flac":
                try:
                    audio = FLAC(file_path)
                    title = audio.get("TITLE", [""])[0]
                    artist = (
                        audio.get("ARTIST", [""])[0]
                        or audio.get("ALBUMARTIST", [""])[0]
                    )
                    album = audio.get("ALBUM", [""])[0]
                except Exception as e:
                    logger.debug(f"Falha ao ler tags do FLAC: {e}")
            elif ext == ".mp3":
                try:
                    audio = id3.ID3(file_path)
                    title = audio.get("TIT2").text[0] if audio.get("TIT2") else ""
                    artist = audio.get("TPE1").text[0] if audio.get("TPE1") else ""
                    album = audio.get("TALB").text[0] if audio.get("TALB") else ""
                except Exception as e:
                    logger.debug(f"Falha ao ler tags do MP3: {e}")

            if not title:
                title = os.path.splitext(file_name)[0]

            # IDs gravados pelo downloader sao confiaveis; busca textual e usada apenas como fallback.
            track_id = extract_track_id(file_path)
            track_id_is_trusted = bool(track_id)

            # Match textual de seguranca: evita abortar quando arquivos antigos nao possuem Qobuz ID.
            # [FIX] Agora valida tambem o artista para evitar associar faixas erradas.
            if not track_id and client:
                try:
                    search_query = f"{artist} {title}".strip()
                    res = await client.search_tracks(search_query, limit=5)
                    items = res.get("tracks", {}).get("items", [])

                    def normalize_text(value):
                        return re.sub(r"[^a-z0-9]", "", str(value).lower())

                    target_title = normalize_text(title)
                    target_artist = normalize_text(artist)

                    for item in items:
                        item_title = normalize_text(str(item.get("title", "")))
                        item_artist = normalize_text(
                            str(item.get("performer", {}).get("name", ""))
                        )

                        title_matches = (
                            target_title in item_title or item_title in target_title
                        )
                        artist_matches = (
                            not target_artist
                            or target_artist in item_artist
                            or item_artist in target_artist
                        )

                        if title_matches and artist_matches:
                            track_id = str(item.get("id"))
                            break
                except Exception as e:
                    logger.debug(f"Falha ao casar faixa por busca textual: {e}")

            # Estado atual: presenca, idioma e se a letra ja e bilingue.
            lyrics_state = inspect_existing_lyrics(file_path)
            has_lyrics = lyrics_state["has_lyrics"]
            is_bilingual = lyrics_state["is_bilingual"]
            existing_lang = lyrics_state["language"]

            display_name = f"{artist} - {title}" if artist else title
            id_display = f"[Track ID: {track_id}]" if track_id else "[Sem Track ID]"
            ui.emit(f"{CYAN}› Analisando:{RESET} {display_name} {id_display}")

            # Mantem original e traducao separados para decidir com seguranca o tipo de atualizacao.
            qobuz_orig_json = None
            qobuz_trans_block = None

            if client and track_id:
                qobuz_orig_json = await fetch_qobuz_lyrics_raw(
                    client, track_id, language=None
                )

                if target_lang:
                    trans_full = await fetch_qobuz_lyrics_raw(
                        client, track_id, language=target_lang
                    )
                    if isinstance(trans_full, dict):
                        qobuz_trans_block = trans_full.get("translation")

            if qobuz_orig_json and isinstance(qobuz_orig_json, dict):
                orig_block = qobuz_orig_json.get("original", {})
                orig_lang = str(orig_block.get("lang", "")).lower()
                lines = orig_block.get("lines", [])
                qobuz_has_sync = any(line.get("start") is not None for line in lines)
                upgrade_to_sync = (
                    qobuz_has_sync and has_lyrics and not lyrics_state["lrc_exists"]
                )

                if target_lang and orig_lang == target_lang.lower():
                    expected_lang = target_lang.lower()
                    if not has_lyrics:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=None,
                        )
                        if result["success"]:
                            stats["updated_new_pt"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "ATUALIZADO",
                                    f"Letra original inserida em {target_lang.upper()} (traducao desnecessaria)",
                                )
                            )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao inserir letra (Qobuz)",
                                )
                            )

                    elif (
                        not existing_lang
                        or existing_lang == "unknown"
                        or existing_lang != expected_lang
                        or upgrade_to_sync
                    ) and track_id_is_trusted:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=None,
                        )
                        if result["success"]:
                            stats["updated_new_pt"] += 1
                            stats["corrected_wrong_language"] += 1
                            if upgrade_to_sync:
                                report_items.append(
                                    (
                                        display_name,
                                        "UPGRADE -> SYNC",
                                        "Letra em texto simples substituida por versao sincronizada do Qobuz",
                                    )
                                )
                            else:
                                report_items.append(
                                    (
                                        display_name,
                                        "CORRIGIDO",
                                        "Letra nativa do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)",
                                    )
                                )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao corrigir letra (Qobuz)",
                                )
                            )
                    else:
                        stats["unchanged_already_pt"] += 1
                        report_items.append(
                            (
                                display_name,
                                "SEM ALTERAÇÃO",
                                f"Letra ja presente e em {target_lang.upper()}",
                            )
                        )

                elif not qobuz_trans_block:
                    expected_lang = orig_lang
                    if not has_lyrics:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=None,
                        )
                        if result["success"]:
                            stats["updated_new_original"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "ATUALIZADO",
                                    f"Letra original ({orig_lang.upper()}) inserida (sem traducao no Qobuz)",
                                )
                            )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao inserir letra original (Qobuz)",
                                )
                            )

                    elif (
                        not existing_lang
                        or existing_lang == "unknown"
                        or existing_lang != expected_lang
                        or upgrade_to_sync
                    ) and track_id_is_trusted:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=None,
                        )
                        if result["success"]:
                            stats["updated_new_original"] += 1
                            stats["corrected_wrong_language"] += 1
                            if upgrade_to_sync:
                                report_items.append(
                                    (
                                        display_name,
                                        "UPGRADE -> SYNC",
                                        "Letra em texto simples substituida por versao sincronizada original do Qobuz",
                                    )
                                )
                            else:
                                report_items.append(
                                    (
                                        display_name,
                                        "CORRIGIDO",
                                        "Letra original do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)",
                                    )
                                )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao corrigir letra original (Qobuz)",
                                )
                            )
                    else:
                        stats["unchanged_no_trans_yet"] += 1
                        report_items.append(
                            (
                                display_name,
                                "SEM ALTERAÇÃO",
                                "Letra original ja presente; sem traducao no Qobuz no momento",
                            )
                        )

                else:
                    expected_lang = f"{orig_lang}+{target_lang.lower()}"
                    if not has_lyrics:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=qobuz_trans_block,
                        )
                        if result["success"]:
                            stats["updated_bilingual_direct"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "ATUALIZADO",
                                    f"Letra original e traducao {target_lang.upper()} inseridas diretamente (Bilingue)",
                                )
                            )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao inserir letra bilingue (Qobuz)",
                                )
                            )

                    elif (
                        not existing_lang
                        or existing_lang == "unknown"
                        or existing_lang != expected_lang
                        or upgrade_to_sync
                    ) and track_id_is_trusted:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=qobuz_trans_block,
                        )
                        if result["success"]:
                            stats["updated_to_bilingual"] += 1
                            stats["corrected_wrong_language"] += 1
                            if upgrade_to_sync:
                                report_items.append(
                                    (
                                        display_name,
                                        "UPGRADE -> SYNC BILINGUE",
                                        "Letra em texto substituida por versão sincronizada (Original + Traducao)",
                                    )
                                )
                            else:
                                report_items.append(
                                    (
                                        display_name,
                                        "CORRIGIDO -> BILINGUE",
                                        "Letra do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)",
                                    )
                                )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao corrigir para bilingue (Qobuz)",
                                )
                            )

                    elif not is_bilingual and track_id_is_trusted:
                        result = engine.fetch_and_inject(
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=qobuz_orig_json,
                            qobuz_translation_response=qobuz_trans_block,
                        )
                        if result["success"]:
                            stats["updated_to_bilingual"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "ATUALIZADO -> BILINGUE",
                                    f"Letra existente atualizada com a nova traducao {target_lang.upper()} do Qobuz",
                                )
                            )
                        else:
                            stats["not_found"] += 1
                            report_items.append(
                                (
                                    display_name,
                                    "FALHA",
                                    "Falha ao atualizar para bilingue (Qobuz)",
                                )
                            )

                    elif not is_bilingual and not track_id_is_trusted:
                        stats["unchanged_no_trans_yet"] += 1
                        report_items.append(
                            (
                                display_name,
                                "SEM ALTERAÇÃO",
                                "Ja possui letra; Track ID nao confiavel (match por busca) -- pulado por seguranca",
                            )
                        )

                    else:
                        stats["unchanged_already_bilingual"] += 1
                        report_items.append(
                            (
                                display_name,
                                "SEM ALTERAÇÃO",
                                "Arquivo ja possui letra bilingue completa",
                            )
                        )

            else:
                if not has_lyrics:
                    result = engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=None,
                        qobuz_translation_response=None,
                    )
                    check_again = inspect_existing_lyrics(file_path)
                    if check_again["has_lyrics"] and result["success"]:
                        stats["updated_fallback"] += 1
                        report_items.append(
                            (
                                display_name,
                                "ATUALIZADO (FALLBACK)",
                                "Letra inserida via Musicmatch/LRCLIB/Genius (não disponível no Qobuz)",
                            )
                        )
                    else:
                        stats["not_found"] += 1
                        report_items.append(
                            (
                                display_name,
                                "NÃO ENCONTRADO",
                                "Nenhuma letra ou traducao encontrada no Qobuz nem nos fallbacks",
                            )
                        )
                else:
                    stats["unchanged_no_trans_yet"] += 1
                    report_items.append(
                        (
                            display_name,
                            "SEM ALTERAÇÃO",
                            "Ja possui letra; Qobuz nao possui registros de traducao",
                        )
                    )

        _w = min(_shutil.get_terminal_size((80, 24)).columns, 100)
        _bar = "━" * _w
        ui.emit(f"\n{CYAN}{_bar}{RESET}")
        ui.emit(
            f"{BG}{CYAN} {'RELATORIO DE ATUALIZACAO DE LETRAS (QOBUZ)':^{_w}}{RESET}"
        )
        ui.emit(f"{CYAN}{_bar}{RESET}\n")

        for name, status, desc in report_items:
            if "ATUALIZADO" in status or "UPGRADE" in status or "CORRIGIDO" in status:
                color = GREEN
                prefix = "[✓]"
            elif "SEM ALTERACAO" in status:
                color = CYAN
                prefix = "[-]"
            else:
                color = YELLOW
                prefix = "[!]"
            ui.emit(f" {color}{prefix} {name}{OFF}")
            ui.emit(f" Status: {color}{status}{RESET} -> {desc}\n")

        # Soma somente operacoes que alteraram ou inseriram letras.
        total_updates = (
            stats["updated_new_original"]
            + stats["updated_new_pt"]
            + stats["updated_to_bilingual"]
            + stats["updated_bilingual_direct"]
            + stats["updated_fallback"]
        )

        ui.emit(f"{CYAN}{'─' * _w}{RESET}")
        ui.emit(f"{BG}{CYAN} RESUMO GERAL:{RESET}")
        ui.emit(f" • Total de arquivos analisados: {stats['total']}")
        ui.emit(f" • Total de arquivos {GREEN}atualizados{OFF}: {total_updates}")
        ui.emit(
            f" - Convertidos para Bilingue (adicao de traducao PT): {stats['updated_to_bilingual']}"
        )
        ui.emit(
            f" - Novas letras Bilingues completas inseridas: {stats['updated_bilingual_direct']}"
        )
        ui.emit(f" - Novas letras no idioma alvo inseridas: {stats['updated_new_pt']}")
        ui.emit(
            f" - Novas letras originais inseridas (sem traducao no Qobuz): {stats['updated_new_original']}"
        )
        ui.emit(
            f" - Inseridas via fallback (Musicmatch/LRCLIB/Genius): {stats['updated_fallback']}"
        )
        if stats["corrected_wrong_language"] > 0:
            ui.emit(
                f" • Total {YELLOW}corrigidas por idioma incorreto{OFF}: {stats['corrected_wrong_language']}"
            )
        ui.emit(
            f" • Total {CYAN}sem alteracoes necessarias{OFF}: {stats['unchanged_already_bilingual'] + stats['unchanged_already_pt'] + stats['unchanged_no_trans_yet']}"
        )
        ui.emit(
            f" • Total {YELLOW}sem letra/traducao encontrada{OFF}: {stats['not_found']}"
        )
        ui.emit(f"{'=' * 75}\n")

    finally:
        # [FIX] Fecha a engine no finally para liberar a sessao HTTP.
        engine.close()


async def inject_lyrics_retroactively(
    directory_path=None, client=None, genius_token=None, settings=None
):
    """
    Entrada publica: resolve a pasta configurada e inicia o processamento retroativo.
    [FIX] Arquivo agora esta completo, com fechamento correto e tratamento de erros.
    """
    if settings is None:
        settings = QobuzDLSettings()

    if not directory_path:
        directory_path = getattr(settings, "default_folder", None)
        if not directory_path:
            try:
                directory_path = settings.raw_settings.get(
                    "download", "directory", fallback="QobuzDownloads"
                )
            except Exception as e:
                logger.debug(
                    f"Não foi possível ler 'directory' do config.ini, usando padrao: {e}"
                )
            directory_path = "QobuzDownloads"

    directory_path = os.path.expanduser(directory_path)

    # Nao cria uma biblioteca vazia automaticamente: informa o usuario e encerra.
    if not os.path.isdir(directory_path):
        ui.emit(
            f"{RED}[!] Erro: O diretório de downloads configurado não existe: '{directory_path}'{OFF}"
        )
        ui.emit(
            f"{YELLOW}[*] Dica: Baixe algum álbum primeiro ou configure a pasta com 'qobuz-dl -r'.{OFF}"
        )
        return

    try:
        await process_retroactive_lyrics_async(
            directory_path=directory_path,
            client=client,
            genius_token=genius_token,
            settings=settings,
        )
    except Exception as e:
        logger.error(
            "Erro no processamento retroativo de letras: %s",
            e,
            exc_info=True,
        )
        ui.emit(f"{RED}[!] O processamento retroativo falhou: {e}{OFF}")
        raise
