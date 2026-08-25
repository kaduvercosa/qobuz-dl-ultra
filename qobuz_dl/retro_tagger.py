import os
import re
import time
import logging
from mutagen.flac import FLAC
import mutagen.id3 as id3

from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.lyrics_engine import LyricsEngine
import shutil as _shutil
from qobuz_dl.color import INFO as CYAN, GREEN, WARNING as YELLOW, RED, OFF, RESET, BG

logger = logging.getLogger(__name__)


def extract_track_id(file_path: str) -> str | None:
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
        if "»" in lyrics_content or re.search(
            r"---\s*TRADU[CÇ][AÃ]O", lyrics_content, re.IGNORECASE
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
        if r.status != 200:
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
    if settings is None:
        settings = QobuzDLSettings()

    target_lang = getattr(settings, "lyrics_translation_lang", "pt")
    save_lrc = getattr(settings, "lrc_files", True)
    embed_lyrics = getattr(settings, "embed_lyrics", True)

    print(f"\n{CYAN}[*] Iniciando verificação e atualização de letras no Qobuz...{OFF}")
    print(f"{CYAN}  • Pasta raiz :{RESET} {directory_path}")
    print(f"{CYAN}  • Idioma alvo:{RESET} {target_lang.upper()}\n")

    engine = LyricsEngine(genius_token=genius_token)

    files_to_check = []
    for root, _, files in os.walk(directory_path):
        for f in files:
            if f.lower().endswith((".flac", ".mp3")):
                files_to_check.append(os.path.join(root, f))

    files_to_check.sort()

    report_items = []
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
                    audio.get("ARTIST", [""])[0] or audio.get("ALBUMARTIST", [""])[0]
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

        track_id = extract_track_id(file_path)
        track_id_is_trusted = bool(track_id)

        if not track_id and client:
            try:
                search_query = f"{artist} {title}".strip()
                res = await client.search_tracks(search_query, limit=5)
                items = res.get("tracks", {}).get("items", [])

                def norm(s):
                    return re.sub(r"[^a-z0-9]", "", s.lower())

                target_title = norm(title)
                for item in items:
                    item_title = norm(str(item.get("title", "")))
                    if target_title and (
                        target_title in item_title or item_title in target_title
                    ):
                        track_id = str(item.get("id"))
                        break
            except Exception as e:
                logger.debug(f"Falha ao casar faixa por busca textual: {e}")

        lyrics_state = inspect_existing_lyrics(file_path)
        has_lyrics = lyrics_state["has_lyrics"]
        is_bilingual = lyrics_state["is_bilingual"]
        existing_lang = lyrics_state["language"]

        display_name = f"{artist} - {title}" if artist else title
        id_display = f"[Track ID: {track_id}]" if track_id else "[Sem Track ID]"
        print(f"{CYAN}› Analisando:{RESET} {display_name} {id_display}")

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

            if orig_lang == target_lang.lower():
                expected_lang = target_lang.lower()
                if not has_lyrics:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=None,
                    )
                    stats["updated_new_pt"] += 1
                    report_items.append(
                        (
                            display_name,
                            "ATUALIZADO",
                            "Letra original inserida em PT (tradução desnecessária)",
                        )
                    )
                elif (
                    not existing_lang or
                    existing_lang == "unknown" or
                    existing_lang != expected_lang
                ) and track_id_is_trusted:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=None,
                    )
                    stats["updated_new_pt"] += 1
                    stats["corrected_wrong_language"] += 1
                    report_items.append(
                        (
                            display_name,
                            "CORRIGIDO",
                            "Letra nativa do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)",
                        )
                    )
                else:
                    stats["unchanged_already_pt"] += 1
                    report_items.append(
                        (display_name, "SEM ALTERAÇÃO", "Letra já presente e em PT")
                    )

            elif not qobuz_trans_block:
                expected_lang = orig_lang
                if not has_lyrics:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=None,
                    )
                    stats["updated_new_original"] += 1
                    report_items.append(
                        (
                            display_name,
                            "ATUALIZADO",
                            f"Letra original ({orig_lang.upper()}) inserida (sem tradução PT no Qobuz)",
                        )
                    )
                elif (
                    not existing_lang or
                    existing_lang == "unknown" or
                    existing_lang != expected_lang
                ) and track_id_is_trusted:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=None,
                    )
                    stats["updated_new_original"] += 1
                    stats["corrected_wrong_language"] += 1
                    report_items.append(
                        (
                            display_name,
                            "CORRIGIDO",
                            "Letra original do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)",
                        )
                    )
                else:
                    stats["unchanged_no_trans_yet"] += 1
                    report_items.append(
                        (
                            display_name,
                            "SEM ALTERAÇÃO",
                            "Letra original já presente; sem tradução PT no Qobuz no momento",
                        )
                    )

            else:
                expected_lang = f"{orig_lang}+{target_lang.lower()}"
                if not has_lyrics:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=qobuz_trans_block,
                    )
                    stats["updated_bilingual_direct"] += 1
                    report_items.append(
                        (
                            display_name,
                            "ATUALIZADO",
                            "Letra original e tradução PT inseridas diretamente (Bilíngue)",
                        )
                    )

                elif (
                    not existing_lang or
                    existing_lang == "unknown" or
                    existing_lang != expected_lang
                ) and track_id_is_trusted:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=qobuz_trans_block,
                    )
                    stats["updated_to_bilingual"] += 1
                    stats["corrected_wrong_language"] += 1
                    report_items.append(
                        (
                            display_name,
                            "CORRIGIDO -> BILÍNGUE",
                            "Letra do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)",
                        )
                    )

                elif not is_bilingual and track_id_is_trusted:
                    engine.fetch_and_inject(
                        file_path=file_path,
                        artist=artist,
                        track=title,
                        album=album,
                        save_lrc=save_lrc,
                        embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json,
                        qobuz_translation_response=qobuz_trans_block,
                    )
                    stats["updated_to_bilingual"] += 1
                    report_items.append(
                        (
                            display_name,
                            "ATUALIZADO -> BILÍNGUE",
                            "Letra existente atualizada com a nova tradução PT do Qobuz",
                        )
                    )

                elif not is_bilingual and not track_id_is_trusted:
                    stats["unchanged_no_trans_yet"] += 1
                    report_items.append(
                        (
                            display_name,
                            "SEM ALTERAÇÃO",
                            "Já possui letra; Track ID não confiável (match por busca) -- pulado por segurança",
                        )
                    )

                else:
                    stats["unchanged_already_bilingual"] += 1
                    report_items.append(
                        (
                            display_name,
                            "SEM ALTERAÇÃO",
                            "Arquivo já possui letra bilíngue completa",
                        )
                    )

        else:
            if not has_lyrics:
                engine.fetch_and_inject(
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
                if check_again["has_lyrics"]:
                    stats["updated_fallback"] += 1
                    report_items.append(
                        (
                            display_name,
                            "ATUALIZADO (FALLBACK)",
                            "Letra inserida via LRCLIB/Genius (não disponível no Qobuz)",
                        )
                    )
                else:
                    stats["not_found"] += 1
                    report_items.append(
                        (
                            display_name,
                            "NÃO ENCONTRADO",
                            "Nenhuma letra ou tradução encontrada no Qobuz nem nos fallbacks",
                        )
                    )
            else:
                stats["unchanged_no_trans_yet"] += 1
                report_items.append(
                    (
                        display_name,
                        "SEM ALTERAÇÃO",
                        "Já possui letra; Qobuz não possui registros de tradução",
                    )
                )

    _w = min(_shutil.get_terminal_size((80, 24)).columns, 100)
    _bar = "━" * _w
    print(f"\n{CYAN}{_bar}{RESET}")
    print(f"{BG}{CYAN}{'RELATÓRIO DE ATUALIZAÇÃO DE LETRAS (QOBUZ)':^{_w}}{RESET}")
    print(f"{CYAN}{_bar}{RESET}\n")

    for name, status, desc in report_items:
        if "ATUALIZADO" in status:
            color = GREEN
            prefix = "[✓]"
        elif "SEM ALTERAÇÃO" in status:
            color = CYAN
            prefix = "[-]"
        else:
            color = YELLOW
            prefix = "[!]"
        print(f" {color}{prefix} {name}{OFF}")
        print(f"     Status: {color}{status}{RESET} ➔ {desc}\n")

    total_updates = (
        stats["updated_new_original"] +
        stats["updated_new_pt"] +
        stats["updated_to_bilingual"] +
        stats["updated_bilingual_direct"] +
        stats["updated_fallback"]
    )

    print(f"{CYAN}{'─' * _w}{RESET}")
    print(f"{BG}{CYAN}RESUMO GERAL:{RESET}")
    print(f"  • Total de arquivos analisados: {stats['total']}")
    print(f"  • Total de arquivos {GREEN}atualizados{OFF}: {total_updates}")
    print(
        f"      - Convertidos para Bilíngue (adição de tradução PT): {stats['updated_to_bilingual']}"
    )
    print(
        f"      - Novas letras Bilíngues completas inseridas: {stats['updated_bilingual_direct']}"
    )
    print(
        f"      - Novas letras em Português nativo inseridas: {stats['updated_new_pt']}"
    )
    print(
        f"      - Novas letras originais inseridas (sem tradução PT no Qobuz): {stats['updated_new_original']}"
    )
    print(
        f"      - Inseridas via fallback (LRCLIB/Genius): {stats['updated_fallback']}"
    )
    if stats["corrected_wrong_language"] > 0:
        print(
            f"  • Total {YELLOW}corrigidas por idioma incorreto{OFF}: {stats['corrected_wrong_language']}"
        )
    print(
        f"  • Total {CYAN}sem alterações necessárias{OFF}: {stats['unchanged_already_bilingual'] + stats['unchanged_already_pt'] + stats['unchanged_no_trans_yet']}"
    )
    print(f"  • Total {YELLOW}sem letra/tradução encontrada{OFF}: {stats['not_found']}")
    print(f"{'=' * 75}\n")


async def inject_lyrics_retroactively(
    directory_path=None, client=None, genius_token=None, settings=None
):
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
                    f"Nao foi possivel ler 'directory' do config.ini, usando padrao: {e}"
                )
                directory_path = "QobuzDownloads"

    directory_path = os.path.expanduser(directory_path)

    if not os.path.isdir(directory_path):
        print(
            f"{RED}[!] Erro: O diretório de downloads configurado não existe: '{directory_path}'{OFF}"
        )
        print(
            f"{YELLOW}[*] Dica: Baixe algum álbum primeiro ou configure a pasta com 'qobuz-dl -r'.{OFF}"
        )
        return

    await process_retroactive_lyrics_async(
        directory_path=directory_path,
        client=client,
        genius_token=genius_token,
        settings=settings,
    )
