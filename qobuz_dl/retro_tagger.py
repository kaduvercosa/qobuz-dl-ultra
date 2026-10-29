# ============================================================================
# retro_tagger.py -- inspecao e atualizacao retroativa de letras na biblioteca.
# Fluxo: localizar IDs/metadados -> consultar Qobuz -> atualizar letras -> relatorio.
# ============================================================================
import asyncio
import collections
import contextvars
import logging
import os
import re
import textwrap
import time

import mutagen.id3 as id3
from mutagen.flac import FLAC
from mutagen.mp3 import MP3

from qobuz_dl import lyrics_comment, ui
from qobuz_dl.color import GREEN, MUTED, OFF, RED, RESET
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import WARNING as YELLOW
from qobuz_dl.concurrency import run_bounded
from qobuz_dl.executors import MAX_LYRICS_WORKERS
from qobuz_dl.lyrics_engine import (
    RANK_BINILYRICS,
    RANK_PLAIN,
    RANK_QOBUZ_BILINGUAL,
    RANK_QOBUZ_SYNCED,
    LyricsEngine,
    has_lrc_timestamps,
    lyrics_source_rank,
)
from qobuz_dl.resilient_http import request_async
from qobuz_dl.settings import MAX_DOWNLOAD_WORKERS, QobuzDLSettings

logger = logging.getLogger(__name__)


def extract_track_id(file_path: str) -> str | None:
    """
    Extrai o ID Qobuz de tags FLAC/MP3 ou do comentario tecnico.
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".flac":
        try:
            audio_flac = FLAC(file_path)
            for tag in ["QOBUZTRACKID", "QOBUZ TRACK ID", "TRACK_ID", "QOBUZ_TRACK_ID"]:
                val = audio_flac.get(tag)
                if val and str(val[0]).strip():
                    return str(val[0]).strip()
            for comment in audio_flac.get("COMMENT", []):
                m = re.search(r"Trk ID:\s*([0-9a-zA-Z]+)", str(comment), re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception as e:
            logger.debug(
                f"Falha ao extrair Trk ID do COMMENT (FLAC), tag provavelmente ausente: {e}"
            )

    elif ext == ".mp3":
        try:
            audio_id3 = id3.ID3(file_path)
            for frame in audio_id3.getall("TXXX"):
                desc_clean = frame.desc.upper().replace(" ", "").replace("_", "")
                if desc_clean in ["QOBUZTRACKID", "TRACKID", "QOBUZTRACK"]:
                    if frame.text and str(frame.text[0]).strip():
                        return str(frame.text[0]).strip()
            for frame in audio_id3.getall("COMM"):
                text = str(frame.text[0]) if frame.text else ""
                m = re.search(r"Trk ID:\s*([0-9a-zA-Z]+)", text, re.IGNORECASE)
                if m:
                    return m.group(1).strip()
        except Exception as e:
            logger.debug(
                f"Falha ao extrair Trk ID do frame COMM (MP3), tag provavelmente ausente: {e}"
            )

    return None


def _lyrics_lookup_hints(file_path):
    """ISRC e duração (s) do arquivo: ajudam fontes que identificam a gravação
    exata (BiniLyrics). Qualquer falha de leitura devolve só o que deu."""
    hints: dict[str, str | int] = {}
    try:
        if file_path.lower().endswith(".flac"):
            audio_flac = FLAC(file_path)
            isrc = (audio_flac.get("ISRC") or [""])[0]
            length = getattr(audio_flac.info, "length", 0)
        else:
            audio_mp3 = MP3(file_path)
            audio_tags = getattr(audio_mp3, "tags", None)
            frame = audio_tags.get("TSRC") if audio_tags is not None else None
            isrc = frame.text[0] if frame is not None and frame.text else ""
            length = getattr(audio_mp3.info, "length", 0)
        if isrc:
            hints["isrc"] = str(isrc).strip()
        if length:
            hints["duration"] = int(round(length))
    except Exception as e:
        logger.debug(f"Falha ao ler ISRC/duração de {file_path}: {e}")
    return hints


_TIMED_LINE_RE = re.compile(r"^\s*\[(\d{2,}):(\d{2})\.(\d{2,3})\](.*)$")


def _has_repeated_timestamp(lrc_text):
    """True se dois versos DIFERENTES compartilham o mesmo timestamp -- é
    assim que o formato atual guarda original + tradução."""
    seen: dict[tuple[int, int, str], str] = {}
    for line in (lrc_text or "").splitlines():
        m = _TIMED_LINE_RE.match(line)
        if not m:
            continue
        text = m.group(4).strip()
        if not text:
            continue
        key = (int(m.group(1)), int(m.group(2)), m.group(3).ljust(3, "0")[:3])
        if key in seen and seen[key] != text:
            return True
        seen.setdefault(key, text)
    return False


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
    embedded_bilingual = False
    embedded_source = None
    comment_source = None
    if ext == ".flac":
        try:
            audio_flac = FLAC(file_path)
            embedded = (
                audio_flac.get("LYRICS", [""])[0]
                or audio_flac.get("UNSYNCEDLYRICS", [""])[0]
            )
            source_vals = audio_flac.get("LYRICS_SOURCE")
            if source_vals:
                embedded_source = str(source_vals[0]).strip() or None
            comment_source = lyrics_comment.source_from_comment(
                audio_flac.get("COMMENT")
            )
            lang_vals = audio_flac.get("LYRICS_LANG")
            if lang_vals:
                embedded_lang = str(lang_vals[0]).strip().lower() or None
            bil_vals = audio_flac.get("LYRICS_BILINGUAL")
            if bil_vals:
                embedded_bilingual = str(bil_vals[0]).strip() == "1"
        except Exception as e:
            logger.debug(f"Falha ao ler letra/idioma embutidos no FLAC: {e}")
    elif ext == ".mp3":
        try:
            audio_id3 = id3.ID3(file_path)
            for uslt in audio_id3.getall("USLT"):
                if uslt.text:
                    embedded = uslt.text
                    # A fonte da letra fica na descricao do frame USLT.
                    embedded_source = (
                        str(getattr(uslt, "desc", "") or "").strip() or None
                    )
                    break
            for comm in audio_id3.getall("COMM"):
                comment_source = lyrics_comment.source_from_comment(
                    list(getattr(comm, "text", None) or [])
                )
                if comment_source:
                    break
            for txxx in audio_id3.getall("TXXX:LYRICS_LANG"):
                if txxx.text:
                    embedded_lang = str(txxx.text[0]).strip().lower() or None
                    break
            for txxx in audio_id3.getall("TXXX:LYRICS_BILINGUAL"):
                if txxx.text:
                    embedded_bilingual = str(txxx.text[0]).strip() == "1"
                    break
        except Exception as e:
            logger.debug(f"Falha ao ler letra/idioma embutidos no MP3 (USLT/TXXX): {e}")

    file_lyrics = ""
    file_lang = None
    file_source = None
    if os.path.exists(lrc_path):
        try:
            with open(lrc_path, encoding="utf-8", errors="ignore") as f:
                file_lyrics = f.read()
            m = re.search(r"\[la:\s*([a-zA-Z+\-]+)\s*\]", file_lyrics)
            if m:
                file_lang = m.group(1).strip().lower()
            m = re.search(r"\[by:\s*([^\]\n]+?)\s*\]", file_lyrics)
            if m:
                file_source = m.group(1).strip() or None
        except Exception as e:
            logger.debug(f"Falha ao ler arquivo .lrc externo: {e}")
    elif os.path.exists(txt_path) and "Tracklist" not in txt_path:
        try:
            with open(txt_path, encoding="utf-8", errors="ignore") as f:
                file_lyrics = f.read()
        except Exception as e:
            logger.debug(f"Falha ao ler arquivo .txt de letra externo: {e}")

    lyrics_content = (embedded or file_lyrics).strip()
    has_lyrics = bool(lyrics_content)
    language = embedded_lang or file_lang
    # Origem da letra ATUAL: campo proprio da tag, depois a linha "Letra:" do
    # COMMENT; para letra que so existe no .lrc, o cabecalho [by:] dele.
    if not has_lyrics:
        lyrics_source = None
    elif embedded:
        lyrics_source = embedded_source or comment_source
    else:
        lyrics_source = file_source

    is_bilingual = False
    if has_lyrics:
        # A tradução não tem mais marcador: ela é reconhecida pela flag
        # LYRICS_BILINGUAL gravada junto da letra, pelo idioma composto
        # ("en+pt"), por dois versos diferentes no mesmo timestamp (formato
        # atual) ou pelo antigo marcador "»" / cabeçalho "--- TRADUÇÃO".
        if (
            embedded_bilingual
            or _has_repeated_timestamp(lyrics_content)
            or " » " in lyrics_content
            or re.search(r"\]\s*»\s", lyrics_content)
            or re.search(r"---\s*TRADU[CÇ§][AÃ§]O", lyrics_content, re.IGNORECASE)
        ):
            is_bilingual = True
        elif language and "+" in language:
            is_bilingual = True

    return {
        "has_lyrics": has_lyrics,
        "is_bilingual": is_bilingual,
        # True quando a letra ATUAL (embutida ou do .lrc/.txt) tem marcas de
        # tempo [mm:ss.xx], venha ela do Qobuz ou de qualquer outro provedor.
        # E isso, e nao a existencia de um .lrc ao lado do audio, que diz se
        # a letra ja e sincronizada.
        "is_synced": has_lrc_timestamps(lyrics_content),
        # De onde veio a letra atual (Qobuz, BiniLyrics, Musixmatch, LRCLIB);
        # None em arquivos antigos que nao registraram a origem.
        "source": lyrics_source,
        "content": lyrics_content,
        "lrc_exists": os.path.exists(lrc_path),
        "language": language,
    }


# Falhas do Qobuz da faixa em processamento: "limite" (429) e "indisponivel"
# (408/425/5xx ou rede) que SOBRARAM depois dos retries de `request_async`. Fica
# num ContextVar para não mudar a assinatura de `fetch_qobuz_lyrics_raw`.
_QOBUZ_FALHAS: contextvars.ContextVar = contextvars.ContextVar(
    "qobuz_lyrics_falhas", default=None
)


def _registrar_qobuz(chave: str) -> None:
    contador = _QOBUZ_FALHAS.get()
    if contador is not None:
        contador[chave] += 1


def _registrar_falha_http(status) -> None:
    """Anota uma resposta que ficou 429/408/425/5xx mesmo após os retries."""
    if status == 429:
        _registrar_qobuz("limite")
    elif isinstance(status, int) and (status in {408, 425} or status >= 500):
        _registrar_qobuz("indisponivel")


def _erro_de_rede(exc: BaseException) -> bool:
    """Erro de conexão/tempo esgotado (sem importar o httpx aqui)."""
    if isinstance(exc, (asyncio.TimeoutError, OSError)):
        return True
    return any(
        cls.__name__ in {"TransportError", "TimeoutException", "NetworkError"}
        for cls in type(exc).__mro__
    )


async def fetch_qobuz_lyrics_raw(client, track_id, language=None):
    """
    Consulta diretamente o endpoint de letras do Qobuz usando assinatura do cliente.
    [FIX] Corrigido de r.status para r.status_code.

    Devolve None quando não há letra OU quando o Qobuz continuou recusando /
    indisponível depois dos retries; só no segundo caso a falha é registrada
    (`_QOBUZ_FALHAS`) e o `lyrics` adia a faixa em vez de decidir sem o Qobuz.
    """
    try:
        params = {"track_id": track_id}
        if language:
            params["language"] = language
        params["request_ts"] = int(time.time())
        params["request_sig"] = client._modern_sig(
            "track/lyricsUrl", params, client.sec
        )

        r = await request_async(
            client.session, "GET", client.base + "track/lyricsUrl", params=params
        )

        if r.status_code != 200:
            _registrar_falha_http(r.status_code)
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

        resp = await request_async(client.session, "GET", lyrics_url, timeout=12.0)

        if resp.status_code == 200:
            return resp.json()

        _registrar_falha_http(resp.status_code)
        return None

    except Exception as e:
        if _erro_de_rede(e):
            _registrar_qobuz("indisponivel")
        logger.debug(f"Falha ao baixar/assinar JSON de letra: {e}")
        return None


def _lyrics_workers(settings):
    """Faixas de letra em paralelo: `max_workers`, no máximo MAX_LYRICS_WORKERS (3).

    Acima disso o Qobuz responde 429 e as faixas ficam ADIADAS, sem baixar o
    tempo total. É o mesmo teto do pool de letras do download.
    """
    try:
        workers = int(getattr(settings, "max_workers", 1))
    except (TypeError, ValueError):
        workers = 1
    return min(MAX_LYRICS_WORKERS, MAX_DOWNLOAD_WORKERS, max(1, workers))


def _collect_audio_files(directory_path):
    """Lista (ordenada) os .flac/.mp3 de uma pasta, recursivamente."""
    found = []
    for root, _, files in os.walk(directory_path):
        for f in files:
            if f.lower().endswith((".flac", ".mp3")):
                found.append(os.path.join(root, f))
    found.sort()
    return found


def _read_basic_tags(file_path, ext):
    """Lê (título, artista, álbum) do arquivo; vazio quando a tag não existe."""
    title, artist, album = "", "", ""
    if ext == ".flac":
        try:
            audio_flac = FLAC(file_path)
            title = audio_flac.get("TITLE", [""])[0]
            artist = (
                audio_flac.get("ARTIST", [""])[0]
                or audio_flac.get("ALBUMARTIST", [""])[0]
            )
            album = audio_flac.get("ALBUM", [""])[0]
        except Exception as e:
            logger.debug(f"Falha ao ler tags do FLAC: {e}")
    elif ext == ".mp3":
        try:
            audio_id3 = id3.ID3(file_path)
            title = audio_id3.get("TIT2").text[0] if audio_id3.get("TIT2") else ""
            artist = audio_id3.get("TPE1").text[0] if audio_id3.get("TPE1") else ""
            album = audio_id3.get("TALB").text[0] if audio_id3.get("TALB") else ""
        except Exception as e:
            logger.debug(f"Falha ao ler tags do MP3: {e}")
    return title, artist, album


# ---------------------------------------------------------------------------
# Saída do comando `lyrics` -- mesmo padrão do `tags`: legenda de cores,
# cabeçalho por álbum, um bloco por faixa com o índice geral [NN/total] e um
# resumo no fim. Cores com significado fixo: verde = letra inserida/alterada,
# amarelo = atenção, cinza = nada a alterar.
# ---------------------------------------------------------------------------
_STATUS_ALTERADOS = ("ATUALIZADO", "UPGRADE", "CORRIGIDO", "PRIORIDADE")

# Texto humano de cada status (a linha "• ..." do bloco da faixa).
_STATUS_TEXTO = {
    "ATUALIZADO": "letra inserida",
    "ATUALIZADO (FALLBACK)": "letra inserida por outro provedor",
    "ATUALIZADO -> BILINGUE": "tradução adicionada (agora bilíngue)",
    "UPGRADE -> SYNC": "texto simples → sincronizada",
    "UPGRADE -> SYNC BILINGUE": "texto simples → sincronizada bilíngue",
    "CORRIGIDO": "letra trocada pela nativa do Qobuz",
    "CORRIGIDO -> BILINGUE": "letra trocada pela versão bilíngue do Qobuz",
    "PRIORIDADE": "origem trocada por uma de maior prioridade",
    "ADIADO": "Qobuz indisponível; faixa adiada",
    "FALHA": "não foi possível gravar a letra",
    "NÃO ENCONTRADO": "nenhuma letra encontrada",
}

# Linhas do "O que houve em cada faixa" do resumo: (rótulo, status somados, cor).
_RESUMO_STATUS = (
    ("texto simples → sincronizada", ("UPGRADE -> SYNC", "UPGRADE -> SYNC BILINGUE")),
    ("ganharam a tradução (bilíngue)", ("ATUALIZADO -> BILINGUE",)),
    ("letras novas inseridas", ("ATUALIZADO",)),
    ("letras novas por outros provedores", ("ATUALIZADO (FALLBACK)",)),
    ("trocadas pela nativa do Qobuz", ("CORRIGIDO", "CORRIGIDO -> BILINGUE")),
    ("origem trocada por prioridade", ("PRIORIDADE",)),
)


def _largura_terminal() -> int:
    return max(60, min(ui.raw_width(), 120))


def _curto(texto: str, limite: int) -> str:
    texto = str(texto)
    if len(texto) <= limite:
        return texto
    return texto[: max(1, limite - 1)].rstrip() + "…"


def _linhas_rotuladas(rotulo: str, texto: str, largura: int) -> list:
    """Linha "   Rótulo  texto" com continuação alinhada sob o texto."""
    return textwrap.wrap(
        texto,
        width=largura - 1,
        initial_indent=f"   {rotulo:<6} ",
        subsequent_indent=" " * 10,
        break_long_words=True,
        break_on_hyphens=False,
    ) or [f"   {rotulo}"]


def _legenda_lyrics() -> list:
    return [
        f"{CYAN} Legenda das cores:{RESET}",
        f"   {GREEN}•{OFF} verde     letra inserida ou alterada",
        f"   {YELLOW}•{OFF} amarelo   atenção: letra não encontrada ou falha ao gravar",
        f"   {MUTED}•{OFF} cinza     nada a alterar",
        f"   {RED}•{OFF} vermelho  erro",
    ]


def _descrever_letra(estado: dict) -> str:
    """Resume a letra que o arquivo tem: 'sincronizada · EN+PT · bilíngue'."""
    if not estado.get("has_lyrics"):
        return "sem letra"
    partes = ["sincronizada" if estado.get("is_synced") else "texto simples"]
    idioma = str(estado.get("language") or "").strip()
    if idioma and idioma != "unknown":
        partes.append(idioma.upper())
    else:
        partes.append("idioma não identificado")
    if estado.get("is_bilingual"):
        partes.append("bilíngue")
    origem = str(estado.get("source") or "").strip()
    partes.append(f"via {origem}" if origem else "origem não registrada")
    return " · ".join(partes)


def _descrever_resultado(resultado) -> str:
    """Resume a letra gravada, a partir do retorno de `fetch_and_inject`."""
    if not isinstance(resultado, dict):
        return ""
    partes = []
    if resultado.get("synchronized"):
        partes.append("sincronizada")
    idioma = str(resultado.get("language") or "").strip()
    if idioma and idioma != "unknown":
        partes.append(idioma.upper())
    if resultado.get("bilingual"):
        partes.append("bilíngue")
    if resultado.get("source"):
        partes.append(f"via {resultado['source']}")
    return " · ".join(partes)


def _motivo_troca(estado: dict, esperado: str) -> str:
    """Por que a letra atual foi trocada pela do Qobuz (quando NÃO é upgrade)."""
    idioma = str(estado.get("language") or "").strip()
    if not idioma or idioma == "unknown":
        return (
            "Idioma da letra atual não identificado (comum em letras de outros "
            "provedores); trocada pela nativa do Qobuz"
        )
    return (
        f"Idioma da letra atual ({idioma.upper()}) difere do esperado "
        f"({str(esperado).upper()}); trocada pela nativa do Qobuz"
    )


def _rank_letra_atual(estado: dict):
    """Prioridade da letra que o arquivo tem hoje.

    None = sem letra ou origem não registrada (arquivo antigo); RANK_PLAIN =
    texto simples (sem marcas de tempo: pior que qualquer sincronizada);
    1 a 5 = Qobuz bilíngue, Qobuz, BiniLyrics, Musixmatch, LRCLIB.
    """
    if not estado.get("has_lyrics"):
        return None
    if not estado.get("is_synced"):
        return RANK_PLAIN
    return lyrics_source_rank(
        estado.get("source"), bilingual=bool(estado.get("is_bilingual"))
    )


def _decidir_troca(estado: dict, melhor_rank: int, idioma_esperado: str):
    """(precisa_trocar, motivo) para uma letra existente.

    `melhor_rank`: a melhor letra que o Qobuz pode dar agora (1 = bilíngue,
    2 = só a original). Motivos: "upgrade" (texto simples -> sincronizada),
    "prioridade" (a atual é de fonte inferior) ou "idioma" (idioma da letra
    atual errado/desconhecido). Letra atual melhor que a disponível nunca é
    trocada.
    """
    rank = _rank_letra_atual(estado)
    if rank == RANK_PLAIN:
        return True, "upgrade"
    if rank is not None:
        if melhor_rank < rank:
            return True, "prioridade"
        if rank < melhor_rank:
            return False, ""
    idioma = estado.get("language")
    if not idioma or idioma == "unknown" or idioma != idioma_esperado:
        return True, "idioma"
    return False, ""


def _status_da_troca(
    motivo, modo, estado, esperado, melhor, orig_lang, alvo_up
) -> tuple:
    """(status, descrição) de uma letra existente trocada pela do Qobuz."""
    bilingue = modo == "bilingue"
    if motivo == "upgrade":
        if bilingue:
            return (
                "UPGRADE -> SYNC BILINGUE",
                "Letra em texto simples substituída por versão sincronizada (Original + Tradução)",
            )
        return (
            "UPGRADE -> SYNC",
            "Letra em texto simples substituída por versão sincronizada do Qobuz",
        )
    origem = str(estado.get("source") or "").strip()
    if motivo == "prioridade":
        if "qobuz" in origem.lower():
            return (
                "ATUALIZADO -> BILINGUE",
                f"Letra sincronizada do Qobuz ganhou a tradução {alvo_up}",
            )
        return (
            "PRIORIDADE",
            f"Letra atual era do {origem} (prioridade {_rank_letra_atual(estado)}); "
            f"o Qobuz{' bilíngue' if bilingue else ''} tem prioridade {melhor}",
        )
    if (
        bilingue
        and not estado.get("is_bilingual")
        and estado.get("language") == orig_lang
    ):
        # Mesma letra no idioma original; só faltava a tradução.
        return (
            "ATUALIZADO -> BILINGUE",
            f"Letra existente ({orig_lang.upper()}) ganhou a tradução {alvo_up} do Qobuz",
        )
    return (
        "CORRIGIDO -> BILINGUE" if bilingue else "CORRIGIDO",
        _motivo_troca(estado, esperado),
    )


def _bloco_faixa(
    rotulo: str,
    nome: str,
    status: str,
    desc: str,
    antes: str,
    depois: str,
    nota_id: str | None,
    largura: int,
    simulado: bool = False,
) -> list:
    """Linhas do bloco de uma faixa (cor: verde / cinza / amarelo)."""
    if status.startswith(_STATUS_ALTERADOS):
        tipo, cor = "alterada", GREEN
        prefixo = "Seria alterada" if simulado else "Alterada"
        estado = f"{prefixo} -- {_STATUS_TEXTO.get(status, status.lower())}"
    elif status == "SEM ALTERAÇÃO":
        tipo, cor = "igual", MUTED
        estado = "Sem alterações"
    else:
        tipo, cor = "atencao", YELLOW
        estado = f"Atenção -- {_STATUS_TEXTO.get(status, status.lower())}"

    linhas = [f"{cor}{rotulo} {nome}{OFF}", f"   {cor}• {estado}{OFF}"]
    if tipo == "alterada":
        for linha in _linhas_rotuladas("Antes", antes, largura):
            linhas.append(f"{MUTED}{linha}{OFF}")
        if depois:
            for linha in _linhas_rotuladas("Agora", depois, largura):
                linhas.append(f"{GREEN}{linha}{OFF}")
    else:
        for linha in _linhas_rotuladas("Atual", antes, largura):
            linhas.append(f"{MUTED}{linha}{OFF}")
    if nota_id:
        for linha in _linhas_rotuladas("ID", nota_id, largura):
            linhas.append(f"{YELLOW}{linha}{OFF}")
    for linha in _linhas_rotuladas("Motivo", desc, largura):
        linhas.append(f"{MUTED}{linha}{OFF}")
    return linhas


def _ler_album_e_artista(file_path: str):
    """(álbum, artista do álbum) das tags; vazio quando não existem."""
    ext = os.path.splitext(file_path)[1].lower()
    album, artista = "", ""
    try:
        if ext == ".flac":
            audio = FLAC(file_path)
            album = audio.get("ALBUM", [""])[0]
            artista = audio.get("ALBUMARTIST", [""])[0]
        elif ext == ".mp3":
            tags = id3.ID3(file_path)
            album = tags.get("TALB").text[0] if tags.get("TALB") else ""
            artista = tags.get("TPE2").text[0] if tags.get("TPE2") else ""
    except Exception as e:
        logger.debug(f"Falha ao ler álbum/artista do álbum: {e}")
    return str(album or ""), str(artista or "")


def _cabecalho_album(file_path: str, quantidade: int, largura: int) -> list:
    """Cabeçalho do álbum (uma vez por pasta): '== Álbum -- Artista | N faixas ==='."""
    album, artista = _ler_album_e_artista(file_path)
    titulo = album.strip() or os.path.basename(os.path.dirname(file_path)).strip()
    if not titulo:
        return []
    cabecalho = f"== {titulo}" + (f" -- {artista.strip()}" if artista.strip() else "")
    cabecalho += f"  |  {quantidade} faixa{'s' if quantidade != 1 else ''}"
    cabecalho = _curto(cabecalho, largura - 6) + " "
    return [f"{CYAN}{cabecalho}{'=' * max(3, largura - len(cabecalho) - 1)}{OFF}"]


async def process_retroactive_lyrics_async(
    directory_path,
    client,
    genius_token=None,
    settings=None,
    *,
    dry_run=False,
    journal=None,
    progress=None,
):
    """
    Varre a biblioteca e decide se cada faixa precisa de letra, traducao ou correcao.

    A saida segue o padrao do comando `tags`: cada faixa ganha um bloco com o
    indice geral [NN/total] (da pra saber quanto falta), os blocos saem sempre
    na ordem dos arquivos (mesmo em modo paralelo) e o resumo vem no final.

    Uma letra so e tratada como "texto simples" quando ela realmente nao tem
    marcas de tempo -- venha de onde vier. Letra ja sincronizada (de qualquer
    provedor) nao e mais confundida com texto simples nem sobrescrita so por
    isso.
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
    if embed_lyrics and save_lrc:
        destino = "metadata + arquivo .lrc"
    elif embed_lyrics:
        destino = "só no metadata"
    elif save_lrc:
        destino = "só em arquivo .lrc"
    else:
        destino = "nada (metadata e .lrc desligados)"
    n_workers = _lyrics_workers(settings)

    ui.emit(
        f"\n{CYAN}[*] Iniciando verificação e atualização de letras no Qobuz...{OFF}"
    )
    ui.emit(f"{CYAN} • Pasta raiz  :{RESET} {directory_path}")
    ui.emit(f"{CYAN} • Idioma alvo :{RESET} {lang_display}")
    ui.emit(f"{CYAN} • Gravação    :{RESET} {destino}")
    ui.emit(
        f"{CYAN} • Prioridade  :{RESET} Qobuz bilíngue > Qobuz > BiniLyrics > "
        "Musixmatch > LRCLIB (só letra sincronizada)"
    )
    if n_workers > 1:
        ui.emit(f"{CYAN} • Em paralelo :{RESET} {n_workers} faixas por vez")
    if dry_run:
        ui.emit(
            f"{YELLOW} • SIMULAÇÃO   :{RESET} nada será gravado (nem letra, nem "
            ".lrc, nem progresso); as fontes de letra são consultadas normalmente"
        )
    ui.emit("")
    for linha_legenda in _legenda_lyrics():
        ui.emit(linha_legenda)

    engine = LyricsEngine(genius_token=genius_token, settings=settings)

    try:
        # Reune primeiro todos os arquivos para ordenar o processamento e calcular
        # o total.
        # Varredura e leitura de tags rodam em thread: com o loop parado, o
        # cliente HTTP do Qobuz não conseguia nem manter a conexão viva.
        files_to_check = await asyncio.to_thread(_collect_audio_files, directory_path)
        pulados = 0
        if progress is not None and progress.skipping:
            files_to_check, pulados = await asyncio.to_thread(
                progress.filter_pending, files_to_check
            )
        total = len(files_to_check)

        # Contadores usados no resumo final; cada caminho de decisao incrementa um deles.
        stats = {
            "total": total,
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
            "upgraded_to_sync": 0,
            "priority_swaps": 0,
            "deferred": 0,
        }
        # Quantas faixas terminaram em cada status (alimenta o resumo final).
        status_counts: collections.Counter = collections.Counter()

        if not files_to_check:
            if pulados:
                ui.emit(
                    f"\n{GREEN}[+] Nada a fazer: os {pulados} arquivo(s) já estavam "
                    f"em dia. Rode sem --resume/--incremental para refazer tudo.{OFF}"
                )
                if progress is not None and not dry_run:
                    await asyncio.to_thread(progress.finish)
            else:
                ui.emit(
                    f"\n{YELLOW}[!] Nenhum arquivo .flac/.mp3 encontrado na pasta.{OFF}"
                )
            return stats

        nota_pulados = f" ({pulados} já em dia, pulados)" if pulados else ""
        ui.emit(f"\n{CYAN}[i] {total} arquivo(s) para verificar{nota_pulados}.{OFF}")

        largura = _largura_terminal()
        digitos = len(str(total))
        por_pasta = collections.Counter(os.path.dirname(f) for f in files_to_check)

        async def _registrar_progresso(file_path, status):
            """Anota o resultado da faixa (base do --resume/--incremental)."""
            if progress is not None and not dry_run:
                await asyncio.to_thread(progress.record, file_path, status)

        async def _processar_arquivo(index, file_path):
            """Decide e aplica a letra de UM arquivo; devolve as linhas do bloco."""
            rotulo = f"[{index:0{digitos}d}/{total}]"
            file_name = os.path.basename(file_path)
            ext = os.path.splitext(file_path)[1].lower()

            title, artist, album = await asyncio.to_thread(
                _read_basic_tags, file_path, ext
            )

            if not title:
                title = os.path.splitext(file_name)[0]

            lookup_hints = await asyncio.to_thread(_lyrics_lookup_hints, file_path)

            # IDs gravados pelo downloader sao confiaveis; busca textual e usada
            # apenas como fallback.
            track_id = await asyncio.to_thread(extract_track_id, file_path)
            track_id_is_trusted = bool(track_id)

            # Match textual de seguranca: evita abortar quando arquivos antigos nao possuem Qobuz ID.
            # [FIX] Agora valida tambem o artista para evitar associar faixas erradas.
            if not track_id and client:
                try:
                    search_query = f"{artist} {title}".strip()
                    res = await client.search_tracks(search_query, limit=5)
                    items = res.get("tracks", {}).get("items", [])

                    def normalize_text(value):
                        """Normalize text for comparison (remove accents, lowercase, etc.)."""
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

            # Estado atual: presenca, idioma, se ja e sincronizada e se ja e bilingue.
            lyrics_state = await asyncio.to_thread(inspect_existing_lyrics, file_path)
            has_lyrics = lyrics_state["has_lyrics"]
            is_bilingual = lyrics_state["is_bilingual"]
            existing_lang = lyrics_state["language"]
            antes = _descrever_letra(lyrics_state)

            display_name = f"{artist} - {title}" if artist else title

            # Aviso so quando o ID nao veio das tags (o caso normal fica quieto).
            if track_id_is_trusted:
                nota_id = None
            elif track_id:
                nota_id = (
                    f"Track ID: {track_id} (achado por busca textual; não é "
                    "confiável, então só insere letra nova)"
                )
            else:
                nota_id = "sem Track ID (nas tags nem na busca do Qobuz)"

            # Mantem original e traducao separados para decidir com seguranca o tipo
            # de atualizacao.
            qobuz_orig_json = None
            qobuz_trans_block = None

            # Falhas do Qobuz DESTA faixa (limite/indisponibilidade que sobraram
            # depois dos retries): sem a resposta dele não dá para decidir pela
            # prioridade.
            falhas_qobuz: collections.Counter = collections.Counter()
            token_falhas = _QOBUZ_FALHAS.set(falhas_qobuz)
            try:
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
            finally:
                _QOBUZ_FALHAS.reset(token_falhas)

            if falhas_qobuz["limite"] or falhas_qobuz["indisponivel"]:
                # Não mexe no arquivo: gravar agora uma letra de fonte inferior
                # (ou trocar uma letra boa) só porque o Qobuz não respondeu.
                stats["deferred"] += 1
                status_counts["ADIADO"] += 1
                await _registrar_progresso(file_path, "ADIADO")
                motivo_falha = (
                    "recusou a consulta por limite de requisições"
                    if falhas_qobuz["limite"]
                    else "ficou indisponível"
                )
                return _bloco_faixa(
                    rotulo,
                    display_name,
                    "ADIADO",
                    f"O Qobuz {motivo_falha} mesmo após as novas tentativas; a "
                    "faixa não foi alterada. Rode o lyrics de novo mais tarde "
                    "(ou use menos --max-workers).",
                    antes,
                    "",
                    nota_id,
                    largura,
                )

            resultado: dict = {}

            async def _injetar(orig, trans, melhor_que=None):
                """Grava a letra (em thread) e guarda o retorno para o bloco.

                `melhor_que`: so aceita fontes de prioridade melhor que esta.
                """
                nonlocal resultado
                # Simulação: o motor só procura; nada é gravado nem registrado.
                extra = {"dry_run": True} if dry_run else {}
                diario = None if dry_run else journal

                def _gravar():
                    snap = diario.snapshot(file_path) if diario else None
                    try:
                        return engine.fetch_and_inject(
                            **lookup_hints,
                            file_path=file_path,
                            artist=artist,
                            track=title,
                            album=album,
                            save_lrc=save_lrc,
                            embed_lyrics=embed_lyrics,
                            qobuz_lyrics_response=orig,
                            qobuz_translation_response=trans,
                            track_number=index,
                            quiet=True,
                            better_than=melhor_que,
                            **extra,
                        )
                    finally:
                        if diario and snap is not None:
                            diario.record(file_path, snap)

                resultado = await asyncio.to_thread(_gravar)
                return resultado

            status, desc = "SEM ALTERAÇÃO", ""

            # So letra SINCRONIZADA serve (a do Qobuz sem marcas de tempo e'
            # ignorada, como em qualquer outra fonte).
            orig_lang = ""
            qobuz_usavel = False
            if qobuz_orig_json and isinstance(qobuz_orig_json, dict):
                orig_block = qobuz_orig_json.get("original", {}) or {}
                orig_lang = str(orig_block.get("lang", "")).lower()
                qobuz_usavel = any(
                    line.get("start") is not None
                    for line in (orig_block.get("lines") or [])
                )
            trans_sync = isinstance(qobuz_trans_block, dict) and any(
                line.get("start") is not None
                for line in (qobuz_trans_block.get("lines") or [])
            )
            alvo_up = (target_lang or "").upper()

            if qobuz_usavel:
                # Melhor letra que o Qobuz pode dar: bilingue (prioridade 1) se
                # houver traducao sincronizada; senao a original (prioridade 2).
                if target_lang and orig_lang == target_lang.lower():
                    modo, esperado, trans = "alvo", target_lang.lower(), None
                    melhor = RANK_QOBUZ_SYNCED
                    chave_nova = chave_troca = "updated_new_pt"
                    chave_igual = "unchanged_already_pt"
                    msg_inserida = (
                        f"Letra original inserida em {alvo_up} (tradução desnecessária)"
                    )
                    msg_falha_inserir = "Falha ao inserir letra (Qobuz)"
                    msg_falha_trocar = "Falha ao corrigir letra (Qobuz)"
                    msg_igual = f"Letra já presente e em {alvo_up}"
                elif not trans_sync:
                    modo, esperado, trans = "original", orig_lang, None
                    melhor = RANK_QOBUZ_SYNCED
                    chave_nova = chave_troca = "updated_new_original"
                    chave_igual = "unchanged_no_trans_yet"
                    msg_inserida = f"Letra original ({orig_lang.upper()}) inserida (sem tradução no Qobuz)"
                    msg_falha_inserir = "Falha ao inserir letra original (Qobuz)"
                    msg_falha_trocar = "Falha ao corrigir letra original (Qobuz)"
                    msg_igual = (
                        "Letra original já presente; sem tradução no Qobuz no momento"
                    )
                else:
                    modo, esperado, trans = (
                        "bilingue",
                        f"{orig_lang}+{target_lang.lower()}",
                        qobuz_trans_block,
                    )
                    melhor = RANK_QOBUZ_BILINGUAL
                    chave_nova = "updated_bilingual_direct"
                    chave_troca = "updated_to_bilingual"
                    chave_igual = (
                        "unchanged_already_bilingual"
                        if is_bilingual
                        else "unchanged_no_trans_yet"
                    )
                    msg_inserida = f"Letra original e tradução {alvo_up} inseridas diretamente (bilíngue)"
                    msg_falha_inserir = "Falha ao inserir letra bilíngue (Qobuz)"
                    msg_falha_trocar = "Falha ao corrigir para bilíngue (Qobuz)"
                    msg_igual = "Arquivo já possui letra bilíngue completa"

                precisa_trocar, motivo = _decidir_troca(lyrics_state, melhor, esperado)

                if not has_lyrics:
                    res = await _injetar(qobuz_orig_json, trans)
                    if res.get("success"):
                        stats[chave_nova] += 1
                        status, desc = "ATUALIZADO", msg_inserida
                    else:
                        stats["not_found"] += 1
                        status, desc = "FALHA", msg_falha_inserir

                elif precisa_trocar and track_id_is_trusted:
                    res = await _injetar(qobuz_orig_json, trans)
                    if res.get("success"):
                        stats[chave_troca] += 1
                        status, desc = _status_da_troca(
                            motivo,
                            modo,
                            lyrics_state,
                            esperado,
                            melhor,
                            orig_lang,
                            alvo_up,
                        )
                    else:
                        stats["not_found"] += 1
                        status, desc = "FALHA", msg_falha_trocar

                elif precisa_trocar:
                    stats[chave_igual] += 1
                    desc = "A letra atual poderia ser trocada pela do Qobuz, mas o Track ID veio de busca (não confiável) -- pulado por segurança"

                elif modo == "bilingue" and not is_bilingual and track_id_is_trusted:
                    # Idioma ja composto (ex.: "en+pt") mas sem a marca de bilingue.
                    res = await _injetar(qobuz_orig_json, trans)
                    if res.get("success"):
                        stats["updated_to_bilingual"] += 1
                        status = "ATUALIZADO -> BILINGUE"
                        desc = f"Letra existente atualizada com a nova tradução {alvo_up} do Qobuz"
                    else:
                        stats["not_found"] += 1
                        status = "FALHA"
                        desc = "Falha ao atualizar para bilíngue (Qobuz)"

                elif modo == "bilingue" and not is_bilingual:
                    stats["unchanged_no_trans_yet"] += 1
                    desc = "Já possui letra; Track ID não confiável (match por busca) -- pulado por segurança"

                else:
                    stats[chave_igual] += 1
                    desc = msg_igual

            else:
                # Sem letra sincronizada no Qobuz: valem as outras fontes, na
                # ordem BiniLyrics > Musixmatch > LRCLIB.
                rank_atual = _rank_letra_atual(lyrics_state)
                if not has_lyrics:
                    res = await _injetar(None, None)
                    check_again = await asyncio.to_thread(
                        inspect_existing_lyrics, file_path
                    )
                    if res.get("success") and (dry_run or check_again["has_lyrics"]):
                        stats["updated_fallback"] += 1
                        status = "ATUALIZADO (FALLBACK)"
                        desc = "Letra inserida via BiniLyrics/Musixmatch/LRCLIB (não disponível no Qobuz)"
                    else:
                        stats["not_found"] += 1
                        status = "NÃO ENCONTRADO"
                        desc = "Nenhuma letra sincronizada encontrada no Qobuz nem nos fallbacks"
                elif track_id_is_trusted and (
                    rank_atual == RANK_PLAIN
                    or (rank_atual is not None and rank_atual > RANK_BINILYRICS)
                ):
                    # Texto simples (qualquer fonte vence) ou fonte inferior
                    # (Musixmatch/LRCLIB): so troca por uma fonte MELHOR.
                    plano = rank_atual == RANK_PLAIN
                    res = await _injetar(None, None, None if plano else rank_atual)
                    if res.get("success"):
                        stats["updated_fallback"] += 1
                        if plano:
                            status = "UPGRADE -> SYNC"
                            desc = "Letra em texto simples substituída por versão sincronizada de outra fonte (o Qobuz não tem)"
                        else:
                            status = "PRIORIDADE"
                            desc = f"Letra atual era do {lyrics_state.get('source')} (prioridade {rank_atual}); encontrada fonte de maior prioridade"
                    else:
                        stats["unchanged_no_trans_yet"] += 1
                        desc = (
                            "A letra atual é texto simples e nenhuma fonte tem versão sincronizada"
                            if plano
                            else f"Nenhuma fonte de maior prioridade que {lyrics_state.get('source')} tem letra sincronizada"
                        )
                else:
                    stats["unchanged_no_trans_yet"] += 1
                    desc = "Já possui letra; o Qobuz não tem letra sincronizada para esta faixa"

            await _registrar_progresso(file_path, status)
            status_counts[status] += 1
            stats["upgraded_to_sync"] += status.startswith("UPGRADE")
            stats["priority_swaps"] += status == "PRIORIDADE"
            stats["corrected_wrong_language"] += status.startswith("CORRIGIDO")
            depois = (
                _descrever_resultado(resultado)
                if status.startswith(_STATUS_ALTERADOS)
                else ""
            )
            return _bloco_faixa(
                rotulo,
                display_name,
                status,
                desc,
                antes,
                depois,
                nota_id,
                largura,
                simulado=dry_run,
            )

        # Os blocos saem SEMPRE na ordem dos arquivos, mesmo que, em modo
        # paralelo, terminem fora de ordem: cada faixa pronta espera sua vez.
        prontos: dict[int, tuple[str, list]] = {}
        proximo: int = 1
        pasta_atual: str | None = None
        trava_saida = asyncio.Lock()

        async def _liberar():
            nonlocal proximo, pasta_atual

            async with trava_saida:
                while proximo in prontos:
                    caminho, linhas = prontos.pop(proximo)
                    pasta = os.path.dirname(caminho)
                    if pasta != pasta_atual:
                        cabecalho = await asyncio.to_thread(
                            _cabecalho_album, caminho, por_pasta[pasta], largura
                        )
                        if cabecalho:
                            ui.emit("")
                            for linha in cabecalho:
                                ui.emit(linha)
                        pasta_atual = pasta
                    ui.emit("")
                    for linha in linhas:
                        ui.emit(linha)
                    proximo += 1

        async def _uma(index, file_path):
            prontos[index] = (file_path, await _processar_arquivo(index, file_path))
            await _liberar()

        async def _worker(_worker_index, entry):
            index, file_path = entry
            await _uma(index, file_path)

        # Compartilha o semáforo global com downloads/playlist que usem
        # run_bounded no mesmo event loop; não cria um limite paralelo isolado.
        resultados = await run_bounded(
            list(enumerate(files_to_check, 1)),
            _worker,
            limit=n_workers,
        )
        for resultado in resultados:
            if isinstance(resultado, BaseException):
                raise resultado
        if progress is not None and not dry_run:
            await asyncio.to_thread(progress.finish)

        # Soma somente operacoes que alteraram ou inseriram letras.
        total_updates = (
            stats["updated_new_original"]
            + stats["updated_new_pt"]
            + stats["updated_to_bilingual"]
            + stats["updated_bilingual_direct"]
            + stats["updated_fallback"]
        )
        sem_alteracao = (
            stats["unchanged_already_bilingual"]
            + stats["unchanged_already_pt"]
            + stats["unchanged_no_trans_yet"]
        )

        ui.emit("")
        if dry_run:
            ui.emit(f"{GREEN}[+] Simulação concluída: nada foi gravado.{OFF}")
        else:
            ui.emit(f"{GREEN}[+] Verificação de letras concluída.{OFF}")
        ui.emit("")
        ui.emit(f"{CYAN} Resumo:{RESET}")
        for texto, valor, cor_ativa, explicacao in (
            (
                "Arquivos analisados",
                stats["total"],
                None,
                "faixas .flac/.mp3 encontradas na pasta",
            ),
            (
                "Atualizados",
                total_updates,
                GREEN,
                "faixas com letra inserida ou alterada",
            ),
            (
                "Sem alterações",
                sem_alteracao,
                None,
                "faixas cuja letra já estava como deveria",
            ),
            (
                "Sem letra / com falha",
                stats["not_found"],
                YELLOW,
                "nenhuma fonte tinha letra ou a gravação falhou",
            ),
            (
                "Adiadas (Qobuz)",
                stats["deferred"],
                YELLOW,
                "o Qobuz recusou ou não respondeu; rode de novo",
            ),
        ):
            cor_valor = cor_ativa if (cor_ativa and valor) else MUTED
            ui.emit(
                f"{CYAN} • {texto.ljust(26)}:{RESET} "
                f"{cor_valor}{valor}{OFF}  {MUTED}{explicacao}{OFF}"
            )

        detalhes = [
            (rotulo_status, sum(status_counts[s] for s in nomes))
            for rotulo_status, nomes in _RESUMO_STATUS
        ]
        if any(qtd for _, qtd in detalhes):
            ui.emit("")
            ui.emit(f"{CYAN} Letras (o que houve em cada faixa):{RESET}")
            for rotulo_status, qtd in detalhes:
                if qtd:
                    ui.emit(
                        f"{CYAN} • {rotulo_status.ljust(36)}:{RESET} {GREEN}{qtd}{OFF}"
                    )

        if stats["deferred"]:
            ui.emit("")
            ui.emit(
                f"{YELLOW}[!] {stats['deferred']} faixa(s) adiada(s): o Qobuz recusou "
                "as consultas. Rode o lyrics de novo mais tarde ou reduza "
                f"--max-workers.{OFF}"
            )

        return stats

    finally:
        # [FIX] Fecha a engine no finally para liberar a sessao HTTP.
        engine.close()
        if progress is not None and not dry_run:
            await asyncio.to_thread(progress.save)


async def inject_lyrics_retroactively(
    directory_path=None,
    client=None,
    genius_token=None,
    settings=None,
    *,
    dry_run=False,
    journal=None,
    progress=None,
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
            if not directory_path:
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
        extra = {}
        if dry_run:
            extra["dry_run"] = True
        if journal is not None:
            extra["journal"] = journal
        if progress is not None:
            extra["progress"] = progress
        await process_retroactive_lyrics_async(
            directory_path=directory_path,
            client=client,
            genius_token=genius_token,
            settings=settings,
            **extra,
        )
    except Exception as e:
        logger.error(
            "Erro no processamento retroativo de letras: %s",
            e,
            exc_info=True,
        )
        ui.emit(f"{RED}[!] O processamento retroativo falhou: {e}{OFF}")
        raise
