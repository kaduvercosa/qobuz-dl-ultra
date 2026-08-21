import os
import re
import httpx  # antes: requests -- ver downloader.py/qopy.py, mesmo motivo
from mutagen.id3 import ID3, USLT, TXXX, ID3NoHeaderError
from mutagen.flac import FLAC
from tqdm import tqdm
from qobuz_dl.color import SUCCESS as GREEN, WARNING as YELLOW, ERROR as RED, RESET

# Import lyricsgenius only if the user has configured the token
try:
    import lyricsgenius
except ImportError:
    lyricsgenius = None


class LyricsEngine:
    """
    Roon-Ready Synchronized Lyrics Engine (Bilingual Edition).

    Responsible for fetching synchronized (LRC) or plain text lyrics from Qobuz natively,
    LRCLIB, and falling back to Genius API if configured. It automatically merges official
    Qobuz translations into a single interleaved Bilingual LRC file (quando disponíveis).
    """

    def __init__(self, genius_token=None, session=None):
        """
        Initializes the Lyrics Engine and conditionally loads the Genius API client.

        Args:
            genius_token (str, optional): The user's Genius API token. Defaults to None.
            session (httpx.Client, optional): A shared HTTP session to reuse for
                LRCLIB requests (connection pooling / keep-alive). If not provided,
                a dedicated session is created and owned by this instance. Note: the
                Genius client (lyricsgenius) manages its own internal session
                regardless -- it's a third-party library and isn't easily made to
                share our pool.
        """
        self.genius_token = genius_token
        self.genius = None
        if self.genius_token and lyricsgenius:
            self.genius = lyricsgenius.Genius(
                self.genius_token, remove_section_headers=True
            )
            self.genius.verbose = False

        self._owns_session = session is None
        # follow_redirects=True: requests seguia redirect por padrao, o
        # httpx.Client nao segue a menos que a gente peca (ver mesmo
        # comentario em downloader.py).
        self.session = session or httpx.Client(follow_redirects=True)

    def close(self):
        """
        Closes the underlying HTTP session -- but ONLY if this instance created
        it itself. If a shared session was passed in (session=...), closing it
        is the caller's responsibility, since other components may still be
        using it.
        """
        if self._owns_session:
            try:
                self.session.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # HELPERS DE CONVERSAO DE TEMPO (formato real do Qobuz: ms inteiros)
    # ------------------------------------------------------------------
    @staticmethod
    def _ms_to_lrc_timestamp(ms):
        """
        Converte um timestamp em milissegundos (formato bruto devolvido pelo
        Qobuz em 'start'/'end') para o formato padrao LRC [mm:ss.mmm].

        Usamos 3 casas decimais (milissegundos) em vez das 2 centesimais
        tradicionais porque o Qobuz ja entrega precisao de milissegundo, e o
        parser de _build_bilingual_lrc (regex \\d{2,}:\\d{2}\\.\\d{2,3}) aceita
        tanto 2 quanto 3 digitos na fracao, entao nao perdemos compatibilidade.
        """
        minutes = ms // 60000
        seconds = (ms % 60000) / 1000.0
        return f"[{minutes:02d}:{seconds:06.3f}]"

    def _qobuz_lines_to_lrc(self, lines):
        """
        Converte a lista de linhas do Qobuz (cada uma com 'line', 'start', 'end')
        em um bloco de texto LRC sincronizado, na ordem certa e com o tempo certo.

        Linhas sem 'start' (os separadores estruturais tipo {"line": ""} que o
        Qobuz usa entre estrofes) nao carregam nenhuma informacao de tempo, entao
        sao simplesmente ignoradas -- inclui-las geraria uma linha "fantasma" sem
        timestamp valido no arquivo final.
        """
        lrc_rows = []
        for entry in lines:
            start = entry.get("start")
            if start is None:
                continue  # separador estrutural sem timing, pula

            text = (entry.get("line") or "").strip()
            timestamp = self._ms_to_lrc_timestamp(start)
            lrc_rows.append(f"{timestamp} {text}")

        return "\n".join(lrc_rows) if lrc_rows else None

    @staticmethod
    def _qobuz_lines_to_plain(lines):
        """
        Gera a versao em texto puro (sem timestamps) a partir das mesmas linhas,
        preservando as quebras de estrofe (linhas vazias) para leitura humana.
        """
        plain_rows = [(entry.get("line") or "").strip() for entry in lines]
        # Remove espacos redundantes nas pontas, mas mantem as linhas em branco internas
        text = "\n".join(plain_rows).strip("\n")
        return text if text else None

    # ------------------------------------------------------------------
    # EXTRACAO A PARTIR DA RESPOSTA REAL DE track/lyricsUrl
    # ------------------------------------------------------------------
    def extract_qobuz_lyrics(self, lyrics_response, translation_response=None):
        """
        Extrai letras (e, se fornecida, traducao) a partir da resposta CRUA do
        endpoint track/lyricsUrl.

        Formato real observado:
            {
              "album_id": "...",
              "track_id": ...,
              "translation_langs": ["pt", "de", ...],
              "publishers": [...],
              "writers": "...",
              "original": {
                  "type": "lsync",
                  "lang": "en",
                  "lines": [{"line": "...", "start": 7380, "end": 11500}, ...]
              }
            }

        Args:
            lyrics_response (dict): resposta bruta de client.api_call("track/lyricsUrl", ...).
            translation_response (dict, optional): resposta de uma futura chamada de
                traducao, no MESMO formato de 'original' (ex: {"lang": "pt", "lines": [...]})
                -- ainda nao temos confirmado o endpoint/parametro exato pra isso, entao
                esse argumento fica pronto pra ser plugado assim que descobrirmos.

        Returns:
            dict | None: {
                "synced": str | None,       # LRC sincronizado do original
                "plain": str | None,        # texto puro do original
                "source": "qobuz",
                "lang": str,                 # idioma original (ex: "en")
                "translation_langs": list,   # idiomas de traducao anunciados pela API
                "translations": [            # populado somente se translation_response vier preenchido
                    {"language": str, "plain": str | None, "synced": str | None}
                ],
            }
        """
        if not lyrics_response or not isinstance(lyrics_response, dict):
            return None

        original = lyrics_response.get("original")
        if not original or not isinstance(original, dict):
            return None

        lines = original.get("lines") or []
        if not lines:
            return None

        synced = self._qobuz_lines_to_lrc(lines)
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

        # Se uma resposta de traducao ja veio pronta (mesmo formato de 'original'),
        # convertemos ela tambem e anexamos a lista de traducoes.
        if translation_response and isinstance(translation_response, dict):
            t_lines = translation_response.get("lines") or []
            if t_lines:
                t_synced = self._qobuz_lines_to_lrc(t_lines)
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
        """
        Interleaves original and translated LRC files chronologically.
        If timestamps match exactly, the original line is placed first.
        """
        if not original_lrc or not translated_lrc:
            return original_lrc or translated_lrc

        def parse_lrc(lrc_text, is_translation):
            parsed = []
            for line in lrc_text.splitlines():
                tags = re.findall(r"\[\d{2,}:\d{2}\.\d{2,3}\]", line)
                text = re.sub(r"\[\d{2,}:\d{2}\.\d{2,3}\]", "", line).strip()

                if not text:
                    continue  # Ignora linhas vazias para nao sujar o player

                for tag in tags:
                    try:
                        m, s = tag.strip("[]").split(":")
                        s, ms = s.split(".")
                        # Normaliza os milissegundos para manter uma ordenacao perfeita
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

        # Ordena primariamente pelo timestamp (ms).
        # Em caso de empate, a original (False) vem antes da traducao (True).
        combined.sort(key=lambda x: (x[0], x[3]))

        final_lrc = []
        for item in combined:
            tag, text, is_trans = item[1], item[2], item[3]
            if is_trans:
                # Seta de recuo e espaco para diferenciar visualmente a traducao
                final_lrc.append(f"{tag}    ↳ {text}")
            else:
                final_lrc.append(f"{tag} {text}")

        return "\n".join(final_lrc)

    # ------------------------------------------------------------------
    # FLUXO PRINCIPAL
    # ------------------------------------------------------------------
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
        """
        Waterfall engine: first try Qobuz natively, then LRCLIB (for LRC format), then Genius.

        Args:
            qobuz_lyrics_response (dict, optional): resposta bruta de
                client.api_call("track/lyricsUrl", track_id=...). Substitui o antigo
                parametro 'track_dict', que esperava um formato que a API nunca
                devolveu de fato.
            qobuz_translation_response (dict, optional): resposta bruta de uma
                futura chamada de traducao (mesmo formato de 'original'), se/quando
                descobrirmos o endpoint/parametro certo.
        """
        if not save_lrc and not embed_lyrics:
            return

        try:
            tqdm.write(f"    🔍 procurando letras para: {track}...")

            # 1. Tenta as letras nativas do Qobuz (Original + Bilingue se houver
            # traducao)
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

                # Tenta achar a traducao em PT primeiro, senao usa a primeira disponivel
                best_trans = None
                for t in translations:
                    if "pt" in str(t.get("language", "")).lower():
                        best_trans = t
                        break
                if not best_trans and translations:
                    best_trans = translations[0]

                final_sync = original_sync
                final_plain = original_plain

                # Idioma real gravado no arquivo -- usado pelo retro_tagger em
                # execucoes futuras para confirmar (ou corrigir) o conteudo,
                # em vez de so' assumir que "ter texto" significa "estar certo".
                if best_trans and (best_trans.get("synced") or best_trans.get("plain")):
                    trans_lang = str(best_trans.get("language") or "unknown").lower()
                    lang_tag = f"{orig_lang}+{trans_lang}"
                else:
                    lang_tag = orig_lang

                # Mesclagem Bilingue
                if best_trans:
                    if original_sync and best_trans.get("synced"):
                        final_sync = self._build_bilingual_lrc(
                            original_sync, best_trans.get("synced")
                        )
                    if original_plain and best_trans.get("plain"):
                        final_plain = f"{original_plain}\n\n--- TRADUCAO ({
                            best_trans.get(
                                'language', 'pt').upper()}) ---\n\n{
                            best_trans.get('plain')}"

                # 'source' fica marcado em toda a cadeia (print + tags no arquivo)
                # para deixar claro, tanto no terminal quanto no arquivo, que a
                # letra embutida veio oficialmente do Qobuz.
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

            # 2. Fallback to LRCLIB
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
                            f"    ✅ Letras sincronizadas injetadas e salvas como .lrc (via LRCLIB)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            f"    ✅ Letras sincronizadas salvas como .lrc (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            f"    ✅ Letras sincronizadas injetadas no metadata (via LRCLIB)!"
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
                            f"    ✅ Letras padrão injetadas e salvas como .txt (via LRCLIB)!"
                        )
                    elif save_lrc:
                        tqdm.write(
                            f"    ✅ Letras padrão salvas como .txt (via LRCLIB)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            f"    ✅ Letras padrão salvas no metadata (via LRCLIB)!"
                        )
                    return

            # 3. Fallback to Genius
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
                        tqdm.write(
                            f"    ✅ Lyrics injected via Genius and saved!")
                    elif save_lrc:
                        tqdm.write(
                            f"    ✅ Lyrics saved via Genius (Embedding disabled)!"
                        )
                    elif embed_lyrics:
                        tqdm.write(
                            f"    ✅ Lyrics injected via Genius (Fallback)!")
                    return

            tqdm.write(
                f"    {YELLOW}⚠️ Nenhuma letra encontrada para esta faixa.{RESET}")

        except Exception as e:
            tqdm.write(f"    {RED}❌ Erro durante a pesquisa de letras: {e}{RESET}")

    def _save_lrc_file(
        self, audio_file_path, synced_lyrics, source=None, language=None
    ):
        """
        Creates the .lrc or .txt file next to the audio file.

        Se 'source' for informado, adiciona a tag padrao de LRC [by:<source>]
        no topo do arquivo, deixando visivel (em qualquer player que leia
        metadados de LRC) de onde a letra veio -- por exemplo [by:Qobuz].
        Se 'language' for informado, adiciona a tag padrao [la:<lang>]
        (ex: [la:es] ou [la:es+pt] para bilingue), permitindo que execucoes
        futuras do retro_tagger saibam com certeza qual idioma foi gravado.
        """
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

    def _inject_metadata(self, file_path, lyrics, source=None, language=None):
        """
        Injects lyrics directly into FLAC (LYRICS block) or MP3 (USLT frame) tags.

        Se 'source' for informado:
          - FLAC: grava tambem uma tag extra 'LYRICS_SOURCE' (ex: "Qobuz"),
            visivel em qualquer player/organizador que leia tags Vorbis Comment.
          - MP3: usa o campo 'desc' do frame USLT para guardar a origem, ja que
            USLT e identificado justamente pela combinacao (lang, desc) -- assim
            da pra saber a origem sem precisar abrir um leitor de tags externo.

        Se 'language' for informado, grava tambem o idioma real do texto que
        foi injetado (ex: "es", "pt", ou "es+pt" quando bilingue):
          - FLAC: tag Vorbis extra 'LYRICS_LANG'.
          - MP3: frame TXXX separado com desc='LYRICS_LANG'.
        Isso permite que uma execucao futura do retro_tagger saiba com certeza
        qual idioma esta gravado no arquivo, em vez de precisar adivinhar --
        e assim consiga detectar e corrigir uma letra no idioma errado.
        """
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
                audio.save()
            elif ext == ".mp3":
                try:
                    audio = ID3(file_path)
                except ID3NoHeaderError:
                    audio = ID3()
                desc = source if source else ""
                audio.add(USLT(encoding=3, lang="eng", desc=desc, text=lyrics))
                if language:
                    # Remove qualquer TXXX:LYRICS_LANG anterior antes de gravar o novo
                    # valor
                    audio.delall("TXXX:LYRICS_LANG")
                    audio.add(TXXX(encoding=3, desc="LYRICS_LANG", text=language))
                audio.save(file_path)
        except Exception:
            pass
