import os
import re
import time
import asyncio
import logging
from mutagen.flac import FLAC
import mutagen.id3 as id3
from mutagen.id3 import ID3NoHeaderError

from qobuz_dl.settings import QobuzDLSettings
from qobuz_dl.lyrics_engine import LyricsEngine
from qobuz_dl.color import CYAN, GREEN, YELLOW, RED, OFF

logger = logging.getLogger(__name__)


def extract_track_id(file_path: str) -> str | None:
    """
    Extrai o track_id do Qobuz a partir das tags Vorbis (FLAC) ou ID3 (MP3).
    Verifica tags dedicadas (QOBUZTRACKID) e comentários formatados ('Trk ID: <id>').
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".flac":
        try:
            audio = FLAC(file_path)
            # 1. Tags Vorbis diretas
            for tag in ["QOBUZTRACKID", "QOBUZ TRACK ID", "TRACK_ID", "QOBUZ_TRACK_ID"]:
                val = audio.get(tag)
                if val and str(val[0]).strip():
                    return str(val[0]).strip()
            # 2. Tag COMMENT com 'Trk ID: 123456'
            for comment in audio.get("COMMENT", []):
                m = re.search(r"Trk ID:\s*([0-9a-zA-Z]+)", str(comment), re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception as e:
            logger.debug(f"Falha ao extrair Trk ID do COMMENT (FLAC), tag provavelmente ausente: {e}")

    elif ext == ".mp3":
        try:
            audio = id3.ID3(file_path)
            # 1. Frames TXXX customizados
            for frame in audio.getall("TXXX"):
                desc_clean = frame.desc.upper().replace(" ", "").replace("_", "")
                if desc_clean in ["QOBUZTRACKID", "TRACKID", "QOBUZTRACK"]:
                    if frame.text and str(frame.text[0]).strip():
                        return str(frame.text[0]).strip()
            # 2. Frame COMM
            for frame in audio.getall("COMM"):
                text = str(frame.text[0]) if frame.text else ""
                m = re.search(r"Trk ID:\s*([0-9a-zA-Z]+)", text, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception as e:
            logger.debug(f"Falha ao extrair Trk ID do frame COMM (MP3), tag provavelmente ausente: {e}")

    return None


def inspect_existing_lyrics(file_path: str) -> dict:
    """
    Verifica se o arquivo já possui letras embutidas ou arquivo .lrc/.txt,
    identifica se as letras já são bilíngues, e lê o idioma real gravado
    (tag 'LYRICS_LANG' no FLAC/MP3, ou [la:xx] no .lrc) quando disponível.

    O campo "language" retornado pode ser:
      - None: arquivo sem essa informação (letra antiga, gravada antes desta
        feature existir, ou tag ilegível) -- trate com cautela, não se sabe.
      - "unknown": a própria origem da letra (LRCLIB/Genius) não informa idioma.
      - "es", "pt", "es+pt", etc.: idioma real conhecido com confiança.
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
            embedded = audio.get("LYRICS", [""])[0] or audio.get("UNSYNCEDLYRICS", [""])[0]
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

    # Prioriza o idioma gravado na tag embutida; cai para o do .lrc se preciso
    language = embedded_lang or file_lang

    # Identifica se já contém tradução intercalada (seta ↳ ou bloco de tradução)
    is_bilingual = False
    if has_lyrics:
        if "↳" in lyrics_content or re.search(r"---\s*TRADU[CÇ][AÃ]O", lyrics_content, re.IGNORECASE):
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
    Chama track/lyricsUrl no Qobuz (com ou sem language) e baixa o JSON
    sincronizado hospedado no CloudFront.
    """
    try:
        params = {"track_id": track_id}
        if language:
            params["language"] = language
        params["request_ts"] = int(time.time())
        params["request_sig"] = client._modern_sig("track/lyricsUrl", params, client.sec)

        async with client.session.request("get", client.base + "track/lyricsUrl", params=params) as r:
            if r.status != 200:
                return None
            meta = await r.json()

        lyrics_url = meta.get("url") or meta.get("lyrics_url")
        if not lyrics_url:
            for k, v in meta.items():
                if "url" in k.lower() and isinstance(v, str):
                    lyrics_url = v
                    break

        if not lyrics_url:
            return None

        loop = asyncio.get_event_loop()

        def _download_signed_json():
            import requests
            session = requests.Session()
            resp = session.get(lyrics_url, timeout=12)
            if resp.status_code == 200:
                return resp.json()
            return None

        return await loop.run_in_executor(None, _download_signed_json)
    except Exception as e:
        logger.debug(f"Falha ao baixar/assinar JSON de letra: {e}")
        return None


async def process_retroactive_lyrics_async(directory_path, client, genius_token=None, settings=None):
    """
    Varre a pasta recursivamente, aplica a lógica de decisão para letras e traduções
    do Qobuz e emite um relatório detalhado de alterações.
    """
    if settings is None:
        settings = QobuzDLSettings()

    target_lang = getattr(settings, "lyrics_translation_lang", "pt")
    save_lrc = getattr(settings, "lrc_files", True)
    embed_lyrics = getattr(settings, "embed_lyrics", True)

    print(f"\n{CYAN}[*] Iniciando verificação e atualização de letras no Qobuz...{OFF}")
    print(f"{CYAN}  • Pasta raiz :{OFF} {directory_path}")
    print(f"{CYAN}  • Idioma alvo:{OFF} {target_lang.upper()}\n")

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

        # 1. Metadados básicos
        title, artist, album = "", "", ""
        if ext == ".flac":
            try:
                audio = FLAC(file_path)
                title = audio.get("TITLE", [""])[0]
                artist = audio.get("ARTIST", [""])[0] or audio.get("ALBUMARTIST", [""])[0]
                album = audio.get("ALBUM", [""])[0]
            except Exception as e:
                logger.debug(f"Falha ao ler tags TITLE/ARTIST/ALBUM do FLAC pra identificar a faixa: {e}")
        elif ext == ".mp3":
            try:
                audio = id3.ID3(file_path)
                title = audio.get("TIT2").text[0] if audio.get("TIT2") else ""
                artist = audio.get("TPE1").text[0] if audio.get("TPE1") else ""
                album = audio.get("TALB").text[0] if audio.get("TALB") else ""
            except Exception as e:
                logger.debug(f"Falha ao ler tags TIT2/TPE1/TALB do MP3 pra identificar a faixa: {e}")

        if not title:
            title = os.path.splitext(file_name)[0]

        # 2. Extrai track_id (fonte confiável: tag embutida no próprio arquivo)
        track_id = extract_track_id(file_path)
        track_id_is_trusted = bool(track_id)

        # Fallback de busca no Qobuz se o arquivo não tiver track_id embutido.
        # ATENÇÃO: isso é uma busca por texto (título/artista) e pode retornar
        # uma gravação diferente (outra versão, cover, live, etc.) com o mesmo
        # nome. Por isso marcamos como "não confiável" e comparamos o título
        # retornado com o título do arquivo antes de aceitar o resultado.
        if not track_id and client:
            try:
                search_query = f"{artist} {title}".strip()
                res = await client.search_tracks(search_query, limit=5)
                items = res.get("tracks", {}).get("items", [])
                norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
                target_title = norm(title)
                for item in items:
                    item_title = norm(str(item.get("title", "")))
                    if target_title and (target_title in item_title or item_title in target_title):
                        track_id = str(item.get("id"))
                        break
                # track_id_is_trusted permanece False: veio de busca por texto,
                # não da tag do arquivo. Usado abaixo para evitar sobrescrever
                # letras já existentes com base num match incerto.
            except Exception as e:
                logger.debug(f"Falha ao casar faixa por busca textual na API (track_id permanece nao confiavel): {e}")

        # 3. Inspeciona o estado atual das letras
        lyrics_state = inspect_existing_lyrics(file_path)
        has_lyrics = lyrics_state["has_lyrics"]
        is_bilingual = lyrics_state["is_bilingual"]
        # Idioma REALMENTE gravado no arquivo, se soubermos com confiança
        # (tag LYRICS_LANG/[la:] gravada por uma execução anterior desta
        # ferramenta). None = não sabemos (letra antiga ou de outra fonte).
        existing_lang = lyrics_state["language"]

        display_name = f"{artist} - {title}" if artist else title
        id_display = f"[Track ID: {track_id}]" if track_id else "[Sem Track ID]"
        print(f"{CYAN}› Analisando:{OFF} {display_name} {id_display}")

        # 4. Consulta o Qobuz para original e tradução
        qobuz_orig_json = None
        qobuz_trans_block = None

        if client and track_id:
            qobuz_orig_json = await fetch_qobuz_lyrics_raw(client, track_id, language=None)
            if target_lang:
                trans_full = await fetch_qobuz_lyrics_raw(client, track_id, language=target_lang)
                if isinstance(trans_full, dict):
                    qobuz_trans_block = trans_full.get("translation")

        # 5. Avaliação das regras de negócio
        if qobuz_orig_json and isinstance(qobuz_orig_json, dict):
            orig_block = qobuz_orig_json.get("original", {})
            orig_lang = str(orig_block.get("lang", "")).lower()

            # REGRA 1: Letra original já é em Português
            if orig_lang == target_lang.lower():
                expected_lang = target_lang.lower()
                if not has_lyrics:
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=None
                    )
                    stats["updated_new_pt"] += 1
                    report_items.append((display_name, "ATUALIZADO", "Letra original inserida em PT (tradução desnecessária)"))
                elif (not existing_lang or existing_lang == "unknown" or existing_lang != expected_lang) and track_id_is_trusted:
                    # Sabemos com certeza (tag própria) que o idioma gravado está
                    # errado, ausente ou veio de fallback (unknown), e o track_id é confiável -> corrige.
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=None
                    )
                    stats["updated_new_pt"] += 1
                    stats["corrected_wrong_language"] += 1
                    report_items.append((display_name, "CORRIGIDO", "Letra nativa do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)"))
                else:
                    stats["unchanged_already_pt"] += 1
                    report_items.append((display_name, "SEM ALTERAÇÃO", "Letra já presente e em PT"))

            # REGRA 2: Letra original existe em outro idioma, mas Qobuz NÃO tem tradução PT
            elif not qobuz_trans_block:
                expected_lang = orig_lang
                if not has_lyrics:
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=None
                    )
                    stats["updated_new_original"] += 1
                    report_items.append((display_name, "ATUALIZADO", f"Letra original ({orig_lang.upper()}) inserida (sem tradução PT no Qobuz)"))
                elif (not existing_lang or existing_lang == "unknown" or existing_lang != expected_lang) and track_id_is_trusted:
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=None
                    )
                    stats["updated_new_original"] += 1
                    stats["corrected_wrong_language"] += 1
                    report_items.append((display_name, "CORRIGIDO", "Letra original do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)"))
                else:
                    stats["unchanged_no_trans_yet"] += 1
                    report_items.append((display_name, "SEM ALTERAÇÃO", "Letra original já presente; sem tradução PT no Qobuz no momento"))

            # REGRA 3: Qobuz possui letra original E tradução em PT
            else:
                expected_lang = f"{orig_lang}+{target_lang.lower()}"
                if not has_lyrics:
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=qobuz_trans_block
                    )
                    stats["updated_bilingual_direct"] += 1
                    report_items.append((display_name, "ATUALIZADO", "Letra original e tradução PT inseridas diretamente (Bilíngue)"))

                elif (not existing_lang or existing_lang == "unknown" or existing_lang != expected_lang) and track_id_is_trusted:
                    # Cobre tanto "idioma original errado", fallback, ou "faltando a
                    # metade da tradução" (ex: tag diz só 'es', deveria ser 'es+pt')
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=qobuz_trans_block
                    )
                    stats["updated_to_bilingual"] += 1
                    stats["corrected_wrong_language"] += 1
                    report_items.append((display_name, "CORRIGIDO -> BILÍNGUE", "Letra do Qobuz sobrescreveu a existente (Fallback/Idioma diferente)"))

                elif not is_bilingual and track_id_is_trusted:
                    # Upgrade de monolíngue para bilíngue (só quando o track_id veio
                    # da tag do próprio arquivo, não de um match incerto por busca)
                    engine.fetch_and_inject(
                        file_path=file_path, artist=artist, track=title, album=album,
                        save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                        qobuz_lyrics_response=qobuz_orig_json, qobuz_translation_response=qobuz_trans_block
                    )
                    stats["updated_to_bilingual"] += 1
                    report_items.append((display_name, "ATUALIZADO -> BILÍNGUE", "Letra existente atualizada com a nova tradução PT do Qobuz"))

                elif not is_bilingual and not track_id_is_trusted:
                    stats["unchanged_no_trans_yet"] += 1
                    report_items.append((display_name, "SEM ALTERAÇÃO", "Já possui letra; Track ID não confiável (match por busca) -- pulado por segurança"))

                else:
                    stats["unchanged_already_bilingual"] += 1
                    report_items.append((display_name, "SEM ALTERAÇÃO", "Arquivo já possui letra bilíngue completa"))

        # REGRA 4: Qobuz não possui letra nem tradução
        else:
            if not has_lyrics:
                # Tenta fallback externo (LRCLIB / Genius)
                engine.fetch_and_inject(
                    file_path=file_path, artist=artist, track=title, album=album,
                    save_lrc=save_lrc, embed_lyrics=embed_lyrics,
                    qobuz_lyrics_response=None, qobuz_translation_response=None
                )
                check_again = inspect_existing_lyrics(file_path)
                if check_again["has_lyrics"]:
                    stats["updated_fallback"] += 1
                    report_items.append((display_name, "ATUALIZADO (FALLBACK)", "Letra inserida via LRCLIB/Genius (não disponível no Qobuz)"))
                else:
                    stats["not_found"] += 1
                    report_items.append((display_name, "NÃO ENCONTRADO", "Nenhuma letra ou tradução encontrada no Qobuz nem nos fallbacks"))
            else:
                stats["unchanged_no_trans_yet"] += 1
                report_items.append((display_name, "SEM ALTERAÇÃO", "Já possui letra; Qobuz não possui registros de tradução"))

    # =========================================================================
    # RELATÓRIO DETALHADO FINAL
    # =========================================================================
    print(f"\n{'='*75}")
    print(f"{GREEN}{'RELATÓRIO DETALHADO DE ATUALIZAÇÃO DE LETRAS (QOBUZ)':^75}{OFF}")
    print(f"{'='*75}\n")

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
        print(f"     Status: {color}{status}{OFF} ➔ {desc}\n")

    total_updates = (
        stats["updated_new_original"] +
        stats["updated_new_pt"] +
        stats["updated_to_bilingual"] +
        stats["updated_bilingual_direct"] +
        stats["updated_fallback"]
    )

    print(f"{'='*75}")
    print(f"{CYAN}RESUMO GERAL:{OFF}")
    print(f"  • Total de arquivos analisados: {stats['total']}")
    print(f"  • Total de arquivos {GREEN}atualizados{OFF}: {total_updates}")
    print(f"      - Convertidos para Bilíngue (adição de tradução PT): {stats['updated_to_bilingual']}")
    print(f"      - Novas letras Bilíngues completas inseridas: {stats['updated_bilingual_direct']}")
    print(f"      - Novas letras em Português nativo inseridas: {stats['updated_new_pt']}")
    print(f"      - Novas letras originais inseridas (sem tradução PT no Qobuz): {stats['updated_new_original']}")
    print(f"      - Inseridas via fallback (LRCLIB/Genius): {stats['updated_fallback']}")
    if stats['corrected_wrong_language'] > 0:
        print(f"  • Total {YELLOW}corrigidas por idioma incorreto{OFF}: {stats['corrected_wrong_language']}")
    print(f"  • Total {CYAN}sem alterações necessárias{OFF}: {stats['unchanged_already_bilingual'] + stats['unchanged_already_pt'] + stats['unchanged_no_trans_yet']}")
    print(f"  • Total {YELLOW}sem letra/tradução encontrada{OFF}: {stats['not_found']}")
    print(f"{'='*75}\n")


async def inject_lyrics_retroactively(directory_path=None, client=None, genius_token=None, settings=None):
    """
    Ponto de entrada assíncrono chamado pelo CLI (deve ser usado com 'await',
    pois já é executado dentro do event loop de async_main()).
    Se directory_path for None, busca automaticamente a pasta raiz do config.ini.
    """
    if settings is None:
        settings = QobuzDLSettings()

    # 1. Resolve a pasta raiz a partir do config.ini se não fornecida
    if not directory_path:
        directory_path = getattr(settings, "default_folder", None)
        if not directory_path:
            try:
                directory_path = settings.raw_settings.get("download", "directory", fallback="QobuzDownloads")
            except Exception as e:
                logger.debug(f"Nao foi possivel ler 'directory' do config.ini, usando padrao: {e}")
                directory_path = "QobuzDownloads"

    # 2. Expande caminhos com ~ (ex: ~/Documents no iOS a-Shell)
    directory_path = os.path.expanduser(directory_path)

    # 3. Valida a existência do diretório
    if not os.path.isdir(directory_path):
        print(f"{RED}[!] Erro: O diretório de downloads configurado não existe: '{directory_path}'{OFF}")
        print(f"{YELLOW}[*] Dica: Baixe algum álbum primeiro ou configure a pasta com 'qobuz-dl -r'.{OFF}")
        return

    # 4. Executa o processamento assíncrono (já estamos dentro de um loop em execução,
    #    então usamos 'await' em vez de asyncio.run() para evitar
    #    "asyncio.run() cannot be called from a running event loop")
    await process_retroactive_lyrics_async(
        directory_path=directory_path,
        client=client,
        genius_token=genius_token,
        settings=settings
    )