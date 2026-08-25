import os
import re
import httpx
from mutagen.id3 import ID3, USLT, TXXX, ID3NoHeaderError
from mutagen.flac import FLAC
from tqdm import tqdm
from qobuz_dl.color import SUCCESS as GREEN, WARNING as YELLOW, ERROR as RED, RESET

try:
    import lyricsgenius
except ImportError:
    lyricsgenius = None


class LyricsEngine:
    def __init__(self, genius_token=None, session=None):
        self.genius_token = genius_token
        self.genius = None
        if self.genius_token and lyricsgenius:
            self.genius = lyricsgenius.Genius(
                self.genius_token, remove_section_headers=True
            )
            self.genius.verbose = False

        # fetch_and_inject() e' 100% sincrona (chamada via run_in_executor
        # em downloader.py) e faz chamadas .get() no estilo requests/httpx
        # sincrono. A `session` compartilhada que vem de downloader.py hoje
        # e' um httpx.AsyncClient (o app inteiro migrou pra httpx async) --
        # usar um AsyncClient de forma sincrona so' devolve uma coroutine
        # nao executada (era exatamente o "coroutine object has no
        # attribute 'status_code'" que estava acontecendo). Nao da' pra
        # reaproveitar um client async aqui sem rodar outro event loop
        # dentro da thread do executor -- mais complexo, e ainda assim nao
        # reaproveitaria o pool de conexoes de verdade.
        #
        # Entao o LyricsEngine sempre mantem seu PROPRIO httpx.Client
        # sincrono e dedicado. Se algum dia `session` vier como um
        # httpx.Client sincrono de verdade, ainda reaproveita normalmente
        # (retrocompatibilidade); qualquer outro tipo (None, AsyncClient,
        # etc.) -- cria o seu proprio.
        if isinstance(session, httpx.Client):
            self._owns_session = False
            self.session = session
        else:
            self._owns_session = True
            self.session = httpx.Client(follow_redirects=True)

    def close(self):
        if self._owns_session:
            try:
                self.session.close()
            except Exception:
                pass

    @staticmethod
    def _ms_to_lrc_timestamp(ms):
        minutes = ms // 60000
        seconds = (ms % 60000) / 1000.0
        return f"[{minutes:02d}:{seconds:06.3f}]"

    def _qobuz_lines_to_lrc(self, lines, inject_intro=False):
        """
        Converte as linhas do Qobuz para LRC.
        Se inject_intro for True, garante a marcação ~ ~ ~ no segundo 0.
        """
        lrc_rows = []
        intro_added = False

        for entry in lines:
            start = entry.get("start")
            if start is None:
                continue

            text = (entry.get("line") or "").strip()

            if inject_intro and not intro_added:
                if start > 0:
                    lrc_rows.append("[00:00.000]   » » » ")

                if text:
                    lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)} {text}")
                else:
                    if start == 0:
                        lrc_rows.append("[00:00.000]   » » » ")
                intro_added = True
            else:
                lrc_rows.append(f"{self._ms_to_lrc_timestamp(start)} {text}")

        return "\n".join(lrc_rows) if lrc_rows else None

    @staticmethod
    def _qobuz_lines_to_plain(lines):
        plain_rows = [(entry.get("line") or "").strip() for entry in lines]
        text = "\n".join(plain_rows).strip("\n")
        return text if text else None

    def extract_qobuz_lyrics(self, lyrics_response, translation_response=None):
        if not lyrics_response or not isinstance(lyrics_response, dict):
            return None

        original = lyrics_response.get("original")
        if not original or not isinstance(original, dict):
            return None

        lines = original.get("lines") or []
        if not lines:
            return None

        # AQUI: Injeta a introdução APENAS na letra original
        synced = self._qobuz_lines_to_lrc(lines, inject_intro=True)
        plain = self._qobuz_lines_to_plain(lines)

        if not synced and not plain:
            return None

        result = {
            "synced": synced,
            "plain": plain,
            "source": "qobuz",
            "lang": original.get("lang", "en"),
            "translation_langs": lyrics_response.get("translation_langs", []),
            "translations": [],
        }

        if translation_response and isinstance(translation_response, dict):
            t_lines = translation_response.get("lines") or []
            if t_lines:
                # AQUI: NÃO injeta a introdução na tradução
                t_synced = self._qobuz_lines_to_lrc(t_lines, inject_intro=False)
                t_plain = self._qobuz_lines_to_plain(t_lines)
                if t_synced or t_plain:
                    result["translations"].append(
                        {
                            "language": translation_response.get("lang", "translated"),
                            "plain": t_plain,
                            "synced": t_synced,
                        }
                    )

        return result

    def _build_bilingual_lrc(self, original_lrc, translated_lrc):
        if not original_lrc or not translated_lrc:
            return original_lrc or translated_lrc

        def parse_lrc(lrc_text, is_translation):
            parsed = []
            for line in lrc_text.splitlines():
                tags = re.findall(r"\[\d{2,}:\d{2}\.\d{2,3}\]", line)
                text = re.sub(r"\[\d{2,}:\d{2}\.\d{2,3}\]", "", line).strip()

                if not text:
                    continue

                for tag in tags:
                    try:
                        m, s = tag.strip("[]").split(":")
                        s, ms = s.split(".")
                        time_ms = (
                            int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0")[:3])
                        )
                        parsed.append((time_ms, tag, text, is_translation))
                    except ValueError:
                        continue
            return parsed

        orig_parsed = parse_lrc(original_lrc, False)
        trans_parsed = parse_lrc(translated_lrc, True)

        combined = orig_parsed + trans_parsed
        combined.sort(key=lambda x: (x[0], x[3]))

        final_lrc = []
        for item in combined:
            tag, text, is_trans = item[1], item[2], item[3]
            if is_trans:
                final_lrc.append(f"{tag}  » {text}")
            else:
                final_lrc.append(f"{tag} {text}")

        return "\n".join(final_lrc)

    def fetch_and_inject(
        self,
        file_path,
        artist,
        track,
        album,
        save_lrc=True,
        embed_lyrics=True,
        qobuz_lyrics_response=None,
        qobuz_translation_response=None,
    ):
        if not save_lrc and not embed_lyrics:
            return

        try:
            tqdm.write(f"    🔍 Procurando letras para: {track}...")

            qobuz_lyrics = self.extract_qobuz_lyrics(
                qobuz_lyrics_response, qobuz_translation_response
            )

            if qobuz_lyrics and (
                qobuz_lyrics.get("synced") or qobuz_lyrics.get("plain")
            ):
                original_sync = qobuz_lyrics.get("synced")
                original_plain = qobuz_lyrics.get("plain")
                translations = qobuz_lyrics.get("translations", [])
                orig_lang = str(qobuz_lyrics.get("lang") or "unknown").lower()

                best_trans = None
                for t in translations:
                    if "pt" in str(t.get("language", "")).lower():
                        best_trans = t
                        break
                if not best_trans and translations:
                    best_trans = translations[0]

                final_sync = original_sync
                final_plain = original_plain

                if best_trans and (best_trans.get("synced") or best_trans.get("plain")):
                    trans_lang = str(best_trans.get("language") or "unknown").lower()
                    lang_tag = f"{orig_lang}+{trans_lang}"
                else:
                    lang_tag = orig_lang

                if best_trans:
                    if original_sync and best_trans.get("synced"):
                        final_sync = self._build_bilingual_lrc(
                            original_sync, best_trans.get("synced")
                        )
                    if original_plain and best_trans.get("plain"):
                        final_plain = f"{original_plain}\n\n--- TRADUCAO ({best_trans.get('language', 'pt').upper()}) ---\n\n{best_trans.get('plain')}"

                source_label = "Qobuz"

                if final_sync:
                    is_bilingual = (
                        "BILINGUAL " if best_trans and best_trans.get("synced") else ""
                    )
                    if embed_lyrics:
                        self._inject_metadata(
                            file_path,
                            final_sync,
                            source=source_label,
                            language=lang_tag,
                            bilingual=bool(is_bilingual),
                        )
                    if save_lrc:
                        self._save_lrc_file(
                            file_path,
                            final_sync,
                            source=source_label,
                            language=lang_tag,
                        )

                    if embed_lyrics and save_lrc:
                        tqdm.write(
                            f"    ✅ Letras {GREEN}{is_bilingual}{RESET}sincronizadas injetadas e salvas em .lrc (via Qobuz)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            f"    ✅ Letras {GREEN}{is_bilingual}{RESET}sincronizadas salvas em .lrc (via Qobuz)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            f"    ✅ Letras {GREEN}{is_bilingual}{RESET}sincronizadas injetadas no metadata (via Qobuz)!"
                        )
                    return

                elif final_plain:
                    is_bilingual = (
                        "BILINGUAL " if best_trans and best_trans.get("plain") else ""
                    )
                    if embed_lyrics:
                        self._inject_metadata(
                            file_path,
                            final_plain,
                            source=source_label,
                            language=lang_tag,
                            bilingual=bool(is_bilingual),
                        )
                    if save_lrc:
                        self._save_lrc_file(
                            file_path,
                            final_plain,
                            source=source_label,
                            language=lang_tag,
                        )

                    if embed_lyrics and save_lrc:
                        tqdm.write(
                            f"    ✅ Letras {GREEN}{is_bilingual}{RESET}padrão injetadas e salvas em .txt (via Qobuz)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            f"    ✅ Letras {GREEN}{is_bilingual}{RESET}padrão salvas em .txt (via Qobuz)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            f"    ✅ Letras {GREEN}{is_bilingual}{RESET}padrão injetadas no metadata (via Qobuz)!"
                        )
                    return

            lrclib_url = "https://lrclib.net/api/get"
            headers = {
                "User-Agent": "qobuz-dl-ultra/1.0 (https://github.com/kaduvercosa/qobuz-dl-ultra)"
            }

            params = {"artist_name": artist, "track_name": track, "album_name": album}
            response = self.session.get(
                lrclib_url, params=params, headers=headers, timeout=12
            )

            if response.status_code != 200:
                params = {"artist_name": artist, "track_name": track}
                response = self.session.get(
                    lrclib_url, params=params, headers=headers, timeout=12
                )

            if response.status_code == 200:
                data = response.json()
                synced_lyrics = data.get("syncedLyrics")
                plain_lyrics = data.get("plainLyrics")

                if synced_lyrics:
                    if embed_lyrics:
                        self._inject_metadata(
                            file_path,
                            synced_lyrics,
                            source="LRCLIB",
                            language="unknown",
                        )
                    if save_lrc:
                        self._save_lrc_file(
                            file_path,
                            synced_lyrics,
                            source="LRCLIB",
                            language="unknown",
                        )

                    if embed_lyrics and save_lrc:
                        tqdm.write(
                            "    ✅ Letras sincronizadas injetadas e salvas como .lrc (via LRCLIB)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            "    ✅ Letras sincronizadas salvas como .lrc (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            "    ✅ Letras sincronizadas injetadas no metadata (via LRCLIB)!"
                        )
                    return

                elif plain_lyrics:
                    if embed_lyrics:
                        self._inject_metadata(
                            file_path, plain_lyrics, source="LRCLIB", language="unknown"
                        )
                    if save_lrc:
                        self._save_lrc_file(
                            file_path, plain_lyrics, source="LRCLIB", language="unknown"
                        )

                    if embed_lyrics and save_lrc:
                        tqdm.write(
                            "    ✅ Letras padrão injetadas e salvas como .txt (via LRCLIB)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            "    ✅ Letras padrão salvas como .txt (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            "    ✅ Letras padrão salvas no metadata (via LRCLIB)!"
                        )
                    return

            if self.genius:
                song = self.genius.search_song(track, artist)
                if song and song.lyrics:
                    if embed_lyrics:
                        self._inject_metadata(
                            file_path, song.lyrics, source="Genius", language="unknown"
                        )
                    if save_lrc:
                        self._save_lrc_file(
                            file_path, song.lyrics, source="Genius", language="unknown"
                        )

                    if embed_lyrics and save_lrc:
                        tqdm.write("    ✅ Letras injetadas e salvas via Genius!")
                    elif save_lrc:
                        tqdm.write(
                            "    ✅ Letras salvas via Genius (embed desativado)!"
                        )
                    elif embed_lyrics:
                        tqdm.write("    ✅ Letras injetadas via Genius (Fallback)!")
                    return

            tqdm.write(
                f"    {YELLOW}⚠️ Nenhuma letra encontrada para esta faixa.{RESET}"
            )

        except Exception as e:
            tqdm.write(f"    {RED}❌ Erro durante a pesquisa de letras: {e}{RESET}")

    def _save_lrc_file(
        self, audio_file_path, synced_lyrics, source=None, language=None
    ):
        base_name = os.path.splitext(audio_file_path)[0]
        lrc_path = f"{base_name}.lrc"

        header_lines = []
        if source:
            header_lines.append(f"[by:{source}]")
        if language:
            header_lines.append(f"[la:{language}]")

        content = (
            ("\n".join(header_lines) + "\n" + synced_lyrics)
            if header_lines
            else synced_lyrics
        )

        with open(lrc_path, "w", encoding="utf-8") as f:
            f.write(content)

    def _inject_metadata(self, file_path, lyrics, source=None, language=None, bilingual=False):
        if not lyrics:
            return

        ext = os.path.splitext(file_path)[1].lower()
        try:
            if ext == ".flac":
                audio = FLAC(file_path)
                audio["LYRICS"] = lyrics
                if source:
                    audio["LYRICS_SOURCE"] = source
                if language:
                    audio["LYRICS_LANG"] = language
                # Tag explicita indicando bilingue -- antes so' dava pra
                # saber isso indiretamente (palavra "BILINGUAL" no print do
                # terminal, ou parseando o "+" dentro de LYRICS_LANG tipo
                # "en+pt"). Agora tem uma tag propria, direto no arquivo.
                audio["LYRICS_BILINGUAL"] = "1" if bilingual else "0"
                audio.save()
            elif ext == ".mp3":
                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    audio = ID3()
                desc = source if source else ""
                audio.add(USLT(encoding=3, lang="eng", desc=desc, text=lyrics))
                if language:
                    audio.delall("TXXX:LYRICS_LANG")
                    audio.add(TXXX(encoding=3, desc="LYRICS_LANG", text=language))
                audio.delall("TXXX:LYRICS_BILINGUAL")
                audio.add(
                    TXXX(
                        encoding=3,
                        desc="LYRICS_BILINGUAL",
                        text="1" if bilingual else "0",
                    )
                )
                audio.save(file_path)
        except Exception:
            pass
