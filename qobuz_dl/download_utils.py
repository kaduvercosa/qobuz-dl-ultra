# ============================================================================
# download_utils.py -- funções puras/isoladas extraídas de downloader.py.
#
# Passo 1 da quebra de downloader.py em módulos menores (downloader.py
# tinha 2526 linhas). Escopo desta extração: SÓ as funções que já tinham
# cobertura de teste real (tests/unit/test_downloader_pure.py) antes de
# mover -- é a fatia que dá pra mover com uma rede de segurança de
# verdade, não um chute. O resto de downloader.py (fetch/embed de capa,
# relatório de letras, pipeline de segmento) fica pra um passo futuro,
# ainda sem essa cobertura.
#
# `downloader.py` re-exporta tudo daqui (import direto no topo do
# arquivo) -- qualquer código que já fazia
# `from qobuz_dl.downloader import format_release_type` (ou qualquer um
# dos outros nomes abaixo) continua funcionando sem mudar nada, incluindo
# a suíte de testes já escrita pra essas funções.
# ============================================================================
import logging
import os
import threading

from pathvalidate import sanitize_filepath

from qobuz_dl import ui
from qobuz_dl.color import OFF, WARNING as YELLOW
from qobuz_dl.utils import classify_release_type, clean_filename, get_album_artist

logger = logging.getLogger(__name__)


def is_track_streamable(track: dict) -> tuple[bool, str]:
    """
    [OPÇÃO A] Checagem prévia se a faixa está liberada para streaming completo.
    """
    streamable = track.get("streamable", False)
    sampleable = track.get("sampleable", False)
    purchasable = track.get("purchasable", False)

    if not streamable:
        if sampleable:
            return False, "Apenas amostra/demo (30s)"
        elif purchasable:
            return False, "Disponível apenas para compra avulsa"
        return False, "Não disponível na região"

    return True, ""


def create_missing_placeholder(track: dict, folder_path: str, reason: str):
    """
    [OPÇÃO B] Cria o arquivo .missing.txt na pasta do álbum
    """
    try:
        track_num = str(track.get("track_number", 0)).zfill(2)
        title = track.get("title", "Faixa").replace("/", "-").replace("\\", "-")
        artist = track.get("performer", {}).get(
            "name", track.get("artist", {}).get("name", "Desconhecido")
        )

        filename = f"{track_num}. {title} [INDISPONÍVEL].missing.txt"
        file_path = os.path.join(folder_path, filename)

        with open(file_path, "w", encoding="utf-8") as f:
            f.write(f"Faixa: {track_num}. {title}\n")
            f.write(f"Artista: {artist}\n")
            f.write(f"Duração: {track.get('duration', 0)}s\n")
            f.write(f"Motivo: {reason}\n")
            f.write("Status: Faixa indisponível para streaming na conta/região.\n")
    except Exception as e:
        # Best-effort: o placeholder .missing.txt e' so' um marcador
        # informativo, nao deve derrubar o download por causa dele.
        logger.debug(
            f"Falha ao criar {file_path if 'file_path' in locals() else '.missing.txt'}: {e}"
        )


def _get_safe_ncols():
    """Delegado para ``ui.progress_ncols()`` (fonte unica de largura)."""
    return ui.progress_ncols()


def _desc_budget(ncols):
    """Calculate description budget for progress bar based on terminal width."""
    FIXED_OVERHEAD = 2 + 4 + 1 + 1 + 1 + 13
    MIN_BAR = 6
    return max(6, min(30, ncols - FIXED_OVERHEAD - MIN_BAR - 1))


class _PositionPool:
    def __init__(self, size):
        """Initialize position pool for managing parallel download progress bar positions."""
        self._lock = threading.Lock()
        self._free = list(range(max(size, 1)))
        self.ncols = _get_safe_ncols()
        self.desc_len = _desc_budget(self.ncols)

    def acquire(self):
        """Acquire a slot from the semaphore (async context manager)."""
        with self._lock:
            if self._free:
                return self._free.pop(0)
            return 0

    def release(self, pos):
        """Release a slot back to the semaphore."""
        with self._lock:
            if pos not in self._free:
                self._free.append(pos)
                self._free.sort()


def format_release_type(
    api_release_type: str,
    track_count=0,
    title=None,
    version=None,
    duration_seconds=0,
) -> str:
    """
    Decide o texto final de {release_type} usado no nome da pasta
    (DEFAULT_FOLDER). Usa a MESMA classificacao unificada usada na busca/TUI
    (`classify_release_type`, em utils.py) -- antes esta funcao so confiava
    cegamente na tag "release_type" da API da Qobuz, que vem errada com
    frequencia (ex.: um lancamento de 5 faixas marcado "Single" pela
    gravadora ia pra pasta "Single/" em vez de "EP/"). Agora a contagem real
    de faixas manda (regra: <=3 Single, 4-7 EP, >7 Album), com prioridade
    pra palavras-chave explicitas no titulo/versao (live, compilation etc.).
    """
    tipo = classify_release_type(
        title=title,
        version=version,
        track_count=track_count,
        duration_seconds=duration_seconds,
        api_release_type=api_release_type,
    )
    if not tipo or tipo == "unknown":
        return "Desconhecido"
    if tipo == "ep":
        return "EP"
    return tipo.title()


def process_folder_format_with_subdirs(
    folder_format, attr_dict, path=None, legacy_charmap=False
):
    """Process folder format string and handle subdirectories."""
    path_parts = folder_format.split("/")
    cleaned_parts = []
    for part in path_parts:
        if not part:
            continue
        try:
            formatted_part = part.format(**attr_dict)
            cleaned_part = sanitize_filepath(
                clean_filename(formatted_part, legacy_charmap=legacy_charmap),
                replacement_text="_",
            )
            if cleaned_part and len(cleaned_part) > 120:
                start_f = cleaned_part[:60].rstrip(" .\"-_'")
                end_f = cleaned_part[-50:].lstrip(" .\"-_'")
                cleaned_part = f"{start_f}...{end_f}"
            if cleaned_part:
                cleaned_parts.append(cleaned_part)
        except KeyError as e:
            logger.warning(
                f"{YELLOW}Erro de formato ({e}), usando texto original.{OFF}"
            )
            cleaned_part = sanitize_filepath(
                clean_filename(part, legacy_charmap=legacy_charmap),
                replacement_text="_",
            )
            if cleaned_part and len(cleaned_part) > 120:
                start_f = cleaned_part[:60].rstrip(" .\"-_'")
                end_f = cleaned_part[-50:].lstrip(" .\"-_'")
                cleaned_part = f"{start_f}...{end_f}"
            if cleaned_part:
                cleaned_parts.append(cleaned_part)

    final_path = os.path.join(*cleaned_parts) if cleaned_parts else ""
    if path is not None:
        return os.path.join(path, final_path)
    return final_path


def _clean_format_str(folder: str, track: str, file_format: str) -> tuple[str, str]:
    """Clean format string by removing invalid patterns."""
    final = []
    for _i, fs in enumerate((folder, track)):
        if fs.endswith(".mp3"):
            fs = fs[:-4]
        elif fs.endswith(".flac"):
            fs = fs[:-5]
        fs = fs.strip()
        final.append(fs)
    return tuple(final)


def _safe_get(d: dict, *keys, default=None):
    """Safely get a value from dictionary with fallback."""
    curr = d
    res = default
    for key in keys:
        res = curr.get(key, default)
        if res == default or not hasattr(res, "__getitem__"):
            return res
        else:
            curr = res
    return res


def _artist_label(item_dict: dict, fallback: str = "") -> str:
    """
    Nome(s) de artista "oficial(is)" de um item da Qobuz -- faixa OU album
    -- baseado nos artistas marcados como main-artist na propria metadata
    (`get_album_artist`, ja usado pra tag de arquivo). Quando ha mais de um
    main-artist creditado na MESMA faixa/release (ex.: uma colaboracao ou
    duo genuino), junta os nomes por virgula ("Artista A, Artista B") em
    vez de usar so o performer daquela faixa isolada, que pode capturar
    so um nome mesmo quando ha colaboracao.

    Usado apenas para popular o report.json (artista por faixa e do
    cabecalho/"artista_album") -- NAO substitui `artist_name`/`artist`
    usados em nomeacao de pasta/arquivo e tags, que continuam como estavam.

    Importante: isso e diferente de "Vários Artistas", que so aparece no
    CABECALHO da pasta quando FAIXAS DE RELEASES DIFERENTES acabam juntas
    numa playlist/lote (ver `_recalc_artista` em postprocess.py) -- aqui
    estamos sempre falando dos artistas de UM release/faixa so.
    """
    try:
        nomes = [n for n in (get_album_artist(item_dict or {}) or []) if n]
    except Exception:
        nomes = []
    if nomes:
        return ", ".join(nomes)
    return fallback
