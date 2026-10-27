# Funções utilitárias diversas do qobuz-dl: geração de playlist .m3u,
# filtragem de discografia, formatação de duração, checagem de binários
# externos (ffmpeg/fpcalc), verificação de integridade de áudio, limpeza de
# nomes de arquivo e resolução de caminhos de configuração multiplataforma.

import logging
import os
import re
import shutil
import string
import subprocess
import time
import unicodedata
import urllib.parse
from typing import Any, Optional

import platformdirs

from qobuz_dl.apple_cover import (  # noqa: F401  (reexporta o que foi movido)
    _APPLE_FALLBACK_COUNTRIES,
    _APPLE_SINGLE,
    _EDICOES_ALBUM,
    _MARCADORES_VERSAO,
    _PALAVRAS_NEUTRAS_ALBUM,
    _PAREN_MARCADOR_VERSAO,
    _SEPARADORES_ARTISTAS,
    _SUFIXO_EDICAO_INOFENSIVO,
    _partes_artista,
    album_titulo_completo,
    apple_release_kind,
    contar_faixas_em_comum,
    edicao_album,
    extrair_essencia,
    extrair_titulo_comparavel,
    extrair_titulo_completo,
    faixas_equivalentes,
    faixas_mesma_lista,
    get_apple_hq_cover,
    marcadores_versao,
    score_artista,
    termo_de_busca,
    titulo_album_comparavel,
)
from qobuz_dl.color import INFO as CYAN
from qobuz_dl.color import OFF, RED
from qobuz_dl.color import WARNING as YELLOW

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EXTENSIONS = (".mp3", ".flac")


class PartialFormatter(string.Formatter):
    # Formatter customizado que trata variáveis ausentes sem lançar
    # KeyError. Usado para montar nomes de arquivo/pasta a partir de tags
    # de metadados que podem não vir preenchidas na resposta da API.

    def __init__(self, missing="n/a", bad_fmt="n/a"):
        """Initialize formatter with fallback values for missing or invalid fields."""
        self.missing, self.bad_fmt = missing, bad_fmt

    def get_field(self, field_name, args, kwargs):
        """Get a field value, returning None if missing instead of raising KeyError."""
        # Campo ausente: em vez de lançar KeyError/AttributeError, devolve
        # None para que format_field() substitua pelo valor `missing`.
        try:
            val = super().get_field(field_name, args, kwargs)
        except (KeyError, AttributeError):
            val = None, field_name
        return val

    def format_field(self, value, spec):
        """Format a field value, using fallbacks for empty/None values or invalid format specs."""
        # Valor vazio/None vira `self.missing`. Spec de formatação inválido
        # (ex.: aplicar formatação numérica a uma string) vira `self.bad_fmt`
        # em vez de lançar ValueError.
        if not value:
            return self.missing
        try:
            return super().format_field(value, spec)
        except ValueError:
            if self.bad_fmt:
                return self.bad_fmt
            raise


def make_m3u(pl_directory, remote_items=None):
    # Gera um arquivo de playlist .m3u8 (UTF-8) a partir dos arquivos de
    # áudio presentes em `pl_directory`.
    #
    # Quando `remote_items` (ordem da playlist vinda da API do Qobuz) é
    # fornecido, usa um algoritmo de 4 passes para casar cada item remoto
    # com o arquivo local correspondente e preservar a ordem exata da
    # playlist online -- ignorando completamente o nome físico do arquivo.
    """Create an M3U playlist file from a list of audio file paths."""
    import logging
    import os
    import re

    from mutagen import File
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3

    logger = logging.getLogger(__name__)
    EXTENSIONS = (".mp3", ".flac")

    track_list = ["#EXTM3U"]
    rel_folder = os.path.basename(os.path.normpath(pl_directory))
    pl_name = rel_folder + ".m3u8"
    pl_full_path = os.path.join(pl_directory, pl_name)

    # 1. Varre a pasta local e extrai as tags de cada arquivo de áudio.
    def _read_info(f, audio_full_path):
        # Lê as tags de UM arquivo (antes inline no loop do os.walk).
        info = {
            "path": audio_full_path,
            "title": "",
            "artist": "",
            "isrc": "",
            "qobuz_id": "",
            "duration": 0,
        }
        try:
            # Duração genérica via mutagen.File (funciona para
            # qualquer formato suportado).
            audio_gen = File(audio_full_path)
            if audio_gen and audio_gen.info:
                info["duration"] = int(audio_gen.info.length)

            # Leitura das tags específicas de cada formato.
            if audio_full_path.lower().endswith(".flac"):
                audio = FLAC(audio_full_path)
                info["qobuz_id"] = audio.get("QOBUZTRACKID", [None])[0]
                info["isrc"] = audio.get("ISRC", [None])[0]
                info["title"] = audio.get("TITLE", [""])[0]
                info["artist"] = audio.get("ARTIST", [""])[0]
            else:
                audio_id3 = ID3(audio_full_path)
                # Frames TXXX customizados precisam ser varridos
                # manualmente (não têm chave direta como TIT2/TPE1).
                for frame in audio_id3.getall("TXXX"):
                    if frame.desc.upper() == "QOBUZTRACKID":
                        info["qobuz_id"] = frame.text[0]
                        break
                isrc_frame = audio_id3.get("TSRC")
                info["isrc"] = isrc_frame.text[0] if isrc_frame else None
                tit2 = audio_id3.get("TIT2")
                info["title"] = tit2.text[0] if tit2 else ""
                tpe1 = audio_id3.get("TPE1")
                info["artist"] = tpe1.text[0] if tpe1 else ""
        except Exception as e:
            logger.debug(f"Erro ao ler tags de {f}: {e}")
            info["title"] = os.path.splitext(f)[0]  # título de fallback

        return info

    audio_paths = []
    for local, dirs, files in os.walk(pl_directory):
        dirs.sort()
        for f in files:
            if os.path.splitext(f)[-1].lower() in EXTENSIONS:
                audio_paths.append((f, os.path.abspath(os.path.join(local, f))))

    # Faixas que moram na pasta do álbum mas pertencem à playlist (ver
    # playlist_links.py): entram no .m3u como se estivessem aqui.
    from qobuz_dl.playlist_links import valid_links

    _seen = {p for _f, p in audio_paths}
    for _tid, _p in valid_links(pl_directory).items():
        if _p not in _seen:
            _seen.add(_p)
            audio_paths.append((os.path.basename(_p), os.path.abspath(_p)))

    local_files_info = [_read_info(f, p) for f, p in audio_paths]

    ordered_files = []

    # 2. Casa os arquivos locais com a ordem da API do Qobuz (4 passes).
    if remote_items:
        # Pré-indexa os arquivos locais em dicionários para buscas O(1).
        by_tid = {str(f["qobuz_id"]): f for f in local_files_info if f.get("qobuz_id")}
        # ISRC e título NÃO identificam uma faixa de forma única (a mesma
        # gravação aparece em single e álbum; o mesmo título, em álbuns
        # diferentes). Por isso guardamos listas e só aceitamos um arquivo
        # cujo ID Qobuz gravado na tag seja o do item remoto -- ou que não
        # tenha ID nenhum (arquivos antigos).
        by_isrc: dict[str, list[dict[str, Any]]] = {}
        by_title: dict[str, list[dict[str, Any]]] = {}
        for _f in local_files_info:
            if _f.get("isrc"):
                by_isrc.setdefault(str(_f["isrc"]), []).append(_f)
            if _f.get("title"):
                by_title.setdefault(str(_f["title"]).strip().lower(), []).append(_f)

        def _first_compatible(files, tid):
            """Primeiro arquivo que não pertença a OUTRA faixa Qobuz."""
            for cand in files or []:
                cand_id = cand.get("qobuz_id")
                if not tid or not cand_id or str(cand_id) == tid:
                    return cand
            return None

        missing_count = 0
        table_header = (
            f"\n{RED}{'━' * 80}\n"
            f"{YELLOW}{'MISSING LOCAL TRACKS':^80}\n"
            f"{RED}{'━' * 80}{OFF}\n"
            f"{CYAN}{'TITLE':<35} │ {'ARTIST':<25} │ {'ID':<12}{OFF}\n"
            f"{'─' * 80}"
        )

        for item in remote_items:
            tid = str(item.get("id", ""))
            isrc = str(item.get("isrc", ""))
            track_title = item.get("title", "Unknown Title")
            album_artist = item.get("album", {}).get("artist", {}).get("name")
            performer_name = item.get("performer", {}).get("name", "Unknown Artist")
            final_artist = (
                performer_name
                if album_artist in [None, "Various Artists"]
                else album_artist
            )

            # Passes 1-3: buscas rápidas em dicionário, em ordem de
            # confiabilidade (ID Qobuz > ISRC > título exato).
            best_match = (
                by_tid.get(tid)
                or _first_compatible(by_isrc.get(isrc), tid)
                or _first_compatible(by_title.get(track_title.strip().lower()), tid)
            )

            # Passe 4: fallback por substring no nome do arquivo, quando
            # nenhum dos anteriores encontrou correspondência.
            if not best_match and track_title != "Unknown Title":
                for f_info in local_files_info:
                    if track_title.lower() in os.path.basename(
                        f_info["path"]
                    ).lower() and _first_compatible([f_info], tid):
                        best_match = f_info
                        break

            if best_match:
                ordered_files.append(best_match)
                # Nota: não removemos best_match de local_files_info aqui de
                # propósito, para permitir faixas duplicadas dentro da
                # mesma playlist.
            else:
                if missing_count == 0:
                    logger.warning(table_header)
                row = f"{track_title[:35]:<35} │ {final_artist[:25]:<25} │ {tid:<12}"
                logger.warning(f"{YELLOW}{row}{OFF}")
                missing_count += 1

        if missing_count > 0:
            logger.warning(f"{RED}{'━' * 80}{OFF}\n")

    # 3. Fallback (álbuns, ou quando o casamento com a API falhou):
    #    ordenação natural pelo nome do arquivo (ex.: "Faixa 2" antes de
    #    "Faixa 10").
    if not remote_items or len(ordered_files) == 0:

        def natural_sort_key(s):
            """Generate a sort key for natural/human sorting of strings with numbers."""
            return [
                int(text) if text.isdigit() else text.lower()
                for text in re.split(r"(\d+)", s)
            ]

        ordered_files = sorted(
            local_files_info,
            key=lambda x: natural_sort_key(os.path.basename(x["path"])),
        )

    # 4. Gera as linhas do M3U e grava o arquivo (só se houver ao menos
    #    uma faixa, além do cabeçalho #EXTM3U).
    for f_info in ordered_files:
        audio_rel_path = os.path.relpath(f_info["path"], pl_directory)

        disp_title = f_info["title"] or "Unknown Title"
        disp_artist = f_info["artist"] or "Unknown Artist"
        length = f_info["duration"]

        index = f"#EXTINF:{length}, {disp_artist} - {disp_title}\n{audio_rel_path}"
        track_list.append(index)

    if len(track_list) > 1:
        with open(pl_full_path, "w", encoding="utf-8") as pl:
            pl.write("\n".join(track_list))


def smart_discography_filter(
    contents: list, save_space: bool = False, skip_extras: bool = False
) -> list:
    # Filtro heurístico de discografia: ao baixar todos os álbuns de um
    # artista, a API costuma trazer relançamentos, coletâneas e álbuns onde
    # o artista aparece só como feature. Esta função remove:
    #   - álbuns de outros artistas onde o artista pedido é apenas feature;
    #   - álbuns duplicados em qualidades diferentes (mantém a melhor);
    #   - (opcionalmente) edições de colecionador, deluxe e ao vivo.

    """Filter out likely tribute/cover albums from artist discographies."""

    def print_album(album: dict) -> None:
        # Auxiliar só para depuração (logger.debug).
        """Print album information in a formatted way."""
        logger.debug(
            f"{album['title']} - {album.get('version', '~~')} "
            "({album['maximum_bit_depth']}/{album['maximum_sampling_rate']}"
            " by {album['artist']['name']}) {album['id']}"
        )

    TYPE_REGEXES = {
        "remaster": r"(?i)(re)?master(ed)?",
        "extra": r"(?i)(anniversary|deluxe|live|collector|demo|expanded)",
    }

    def is_type(album_t: str, album: dict) -> bool:
        # Verifica se o título/versão do álbum casa com o regex do tipo
        # pedido (ex.: "remaster" ou "extra").
        """Check if an item matches a specific type string."""
        version = album.get("version", "")
        title = album.get("title", "")
        regex = TYPE_REGEXES[album_t]
        return re.search(regex, f"{title} {version}") is not None

    def essence(album: str) -> str:
        # Reduz o título do álbum à sua "essência": ignora texto entre
        # parênteses/colchetes e deixa tudo minúsculo. Usado para agrupar
        # álbuns com nomes parecidos mas não idênticos (ex.: "Album" e
        # "Album (Deluxe Edition)" caem no mesmo grupo).
        """Extract the core essence of a title (lowercase, no punctuation)."""
        r = re.match(r"([^\(]+)(?:\s*[\(\[][^\)][\)\]])*", album)
        return (
            r.group(1).strip().lower() if r is not None else str(album).strip().lower()
        )

    requested_artist = contents[0]["name"]
    items = [item["albums"]["items"] for item in contents][0]

    # Agrupa os álbuns duplicados pelo título "essencial".
    title_grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        title_ = essence(item["title"])
        if title_ not in title_grouped:
            title_grouped[title_] = []
        title_grouped[title_].append(item)

    items = []
    for albums in title_grouped.values():
        # Dentro de cada grupo, decide qual é a "melhor" versão: maior bit
        # depth e, a partir dele, a taxa de amostragem mais alta (ou mais
        # baixa, se save_space estiver ativo, para economizar espaço).
        best_bit_depth = max(a["maximum_bit_depth"] for a in albums)
        get_best = min if save_space else max
        best_sampling_rate = get_best(
            a["maximum_sampling_rate"]
            for a in albums
            if a["maximum_bit_depth"] == best_bit_depth
        )
        remaster_exists = any(is_type("remaster", a) for a in albums)

        # IMPORTANTE (late binding / B023): esta closure lê best_bit_depth,
        # best_sampling_rate e remaster_exists via ARGUMENTOS DEFAULT (não
        # direto das variáveis do loop acima). Isso é proposital: se lesse
        # das variáveis do loop, qualquer uso futuro que guardasse esta
        # função para chamar depois (lista de callbacks, generator lazy,
        # thread pool) passaria a usar os valores da ÚLTIMA iteração do
        # loop para todos os grupos, filtrando a discografia errada
        # silenciosamente. Os argumentos default capturam o valor no
        # momento em que a função é definida, não no momento em que é
        # chamada.
        def is_valid(
            album: dict,
            _bit_depth=best_bit_depth,
            _sampling_rate=best_sampling_rate,
            _remaster_exists=remaster_exists,
        ) -> bool:
            """Check if an album release type is valid (Album/EP/Single)."""
            return (
                album["maximum_bit_depth"] == _bit_depth
                and album["maximum_sampling_rate"] == _sampling_rate
                and album["artist"]["name"] == requested_artist
                and not (  # estados não permitidos:
                    (_remaster_exists and not is_type("remaster", album))
                    or (skip_extras and is_type("extra", album))
                )
            )

        filtered = tuple(filter(is_valid, albums))
        # Na maioria dos casos len(filtered) é 0 ou 1. Se for maior, é uma
        # duplicata completa -- não importa qual dos dois é escolhido.
        if len(filtered) >= 1:
            items.append(filtered[0])

    return items


def format_duration(duration):
    # Formata uma duração em segundos como string HH:MM:SS.
    """Format seconds into HH:MM:SS or MM:SS string."""
    return time.strftime("%H:%M:%S", time.gmtime(duration))


# Cache do resultado da checagem de binários externos. Chave = nome do
# binário, valor = caminho encontrado ou None. Existe para que o aviso saia
# UMA vez por execução, não uma vez por arquivo processado.
_BINARIOS_CHECADOS: dict[str, Optional[str]] = {}

# Onde procurar além do PATH. O a-Shell (iOS/iPadOS) traz ffmpeg nativo em
# $APPDIR/bin, que nem sempre está no PATH do processo Python.
_DIRS_EXTRA = [
    os.path.join(os.environ.get("APPDIR", ""), "bin"),
]


def encontrar_binario(nome):
    # Procura um executável externo (ex.: ffmpeg, fpcalc) no PATH e nos
    # diretórios extras conhecidos, memorizando o resultado em cache.
    #
    # Antes desta função existir, a ausência do ffmpeg só era descoberta
    # via FileNotFoundError na hora de rodar o subprocess, dentro de
    # verify_audio_integrity(). Como aquela função roda por arquivo, um
    # álbum de 14 faixas produzia 14 mensagens de erro que pareciam 14
    # arquivos corrompidos, quando o problema real era um só (falta de
    # instalação).
    """Find a binary executable in PATH."""
    if nome in _BINARIOS_CHECADOS:
        return _BINARIOS_CHECADOS[nome]

    caminho = shutil.which(nome)
    if not caminho:
        for d in _DIRS_EXTRA:
            if not d or not os.path.isdir(d):
                continue
            tentativa = shutil.which(nome, path=d)
            if tentativa:
                caminho = tentativa
                break

    _BINARIOS_CHECADOS[nome] = caminho
    return caminho


def _avisar(titulo, detalhe):
    # Emite um aviso de duas partes (título curto + detalhe) já quebrado na
    # largura do terminal, via qobuz_dl.ui quando disponível.
    #
    # O try/except ImportError existe para que esta função continue
    # utilizável fora da CLI (ex.: importada por um script avulso), onde o
    # módulo ui pode não estar configurado -- nesse caso cai para logging
    # simples de uma linha só.
    """Print a warning about missing external binaries."""
    try:
        from qobuz_dl import ui

        ui.warn(titulo)
        ui.wrapped(detalhe, indent=4)
    except ImportError:  # pragma: no cover - só fora da CLI
        logger.warning("%s %s", titulo, detalhe)


def checar_binarios_externos(precisa_fpcalc=False):
    # Verifica na inicialização os executáveis externos que o pip NÃO
    # instala: ffmpeg (checagem de integridade) e fpcalc/Chromaprint (usado
    # por --find-duplicates via pyacoustid). Avisa UMA vez, com instrução
    # de instalação, em vez de deixar cada funcionalidade falhar do seu
    # jeito mais adiante na execução.
    #
    # `precisa_fpcalc` controla se o aviso sobre fpcalc é exibido: só faz
    # sentido cobrar Chromaprint de quem realmente vai usar fingerprint de
    # áudio, não de quem só quer baixar um álbum.
    """Check for required external binaries (ffmpeg, ffprobe, optionally fpcalc)."""
    resultado = {"ffmpeg": encontrar_binario("ffmpeg"), "fpcalc": None}

    if not resultado["ffmpeg"]:
        _avisar(
            # Título curto de propósito: ui.warn() não quebra linha -- só
            # ui.wrapped() (usado no detalhe) quebra. Com a tag "[!] "
            # ocupando 4 colunas, o título precisa caber em ~28 caracteres
            # para não estourar num terminal de 32 colunas (a-Shell no
            # iPad em tela dividida).
            "ffmpeg nao encontrado",
            "A integridade dos arquivos baixados nao sera verificada. O "
            "download em si funciona normalmente. Instale com `apt install "
            "ffmpeg` ou `brew install ffmpeg`. No a-Shell (iOS/iPadOS) o "
            "ffmpeg ja vem embutido em $APPDIR/bin.",
        )

    if precisa_fpcalc:
        resultado["fpcalc"] = encontrar_binario("fpcalc")
        if not resultado["fpcalc"]:
            _avisar(
                "fpcalc nao encontrado",
                "O --find-duplicates nao vai funcionar: o fingerprint de audio "
                "depende deste executavel do Chromaprint, que NAO vem via pip. "
                "Instale com `apt install libchromaprint-tools` ou `brew "
                "install chromaprint`.",
            )

    return resultado


def verify_audio_integrity(filepath, timeout=180) -> tuple[bool | None, str]:
    # Verifica se um arquivo de áudio está corrompido, decodificando-o por
    # INTEIRO com o ffmpeg -- não só lendo metadados/tags (que é o que
    # ffprobe -show_format/-show_streams fazia antes).
    #
    # Um FLAC/MP3 pode ter tags perfeitas e ainda assim ter o stream de
    # áudio truncado/corrompido no meio (ex.: download que caiu na metade e
    # o arquivo parcial passou despercebido). Decodificar o arquivo inteiro
    # (descartando a saída com "-f null -") é a única forma confiável de
    # pegar isso, usando os mesmos flags do remux que já existe em
    # downloader.py (-nostdin, -v error).
    """Verify audio file integrity by attempting full decode with ffmpeg."""
    if not os.path.isfile(filepath):
        return False, "Arquivo nao encontrado."

    # Consulta o cache em vez de descobrir a ausência do ffmpeg via
    # FileNotFoundError a cada arquivo. O aviso completo, com instrução de
    # instalação, já saiu uma única vez em checar_binarios_externos().
    ffmpeg = encontrar_binario("ffmpeg")
    if not ffmpeg:
        return None, "ffmpeg nao disponivel -- integridade nao verificada."

    try:
        result = subprocess.run(
            [
                ffmpeg,
                "-nostdin",
                "-v",
                "error",
                "-i",
                filepath,
                "-f",
                "null",
                "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError:
        return (
            None,
            "ffmpeg nao encontrado no sistema (necessario para verificar integridade).",
        )
    except subprocess.TimeoutExpired:
        return False, f"Verificacao excedeu o tempo limite de {timeout}s."

    if result.returncode != 0 or result.stderr.strip():
        # Qualquer linha em stderr com "-v error" indica problema real de
        # decodificação (não apenas avisos), então tratamos como corrompido.
        return (
            False,
            result.stderr.strip() or f"ffmpeg saiu com codigo {result.returncode}.",
        )

    return True, ""


def create_and_return_dir(directory):
    # Cria (se necessário) e devolve o caminho absoluto de um diretório,
    # expandindo "~" quando presente.
    """Create directory if it doesn't exist and return the path."""
    fix = os.path.abspath(os.path.expanduser(directory))
    os.makedirs(fix, exist_ok=True)
    return fix


def get_url_info(url):
    # Extrai o tipo de mídia (album/artist/track/playlist/label) e o ID de
    # uma URL do Qobuz via regex. Compatível com os formatos:
    #   https://www.qobuz.com/us-en/{type}/{name}/{id}
    #   https://open.qobuz.com/{type}/{id}
    #   https://play.qobuz.com/{type}/{id}
    #   /us-en/{type}/-/{id}
    r = re.search(
        r"(?:https:\/\/(?:w{3}|open|play)\.qobuz\.com)?(?:\/[a-z]{2}-[a-z]{2})"
        r"?\/(album|artist|track|playlist|label)(?:\/[-\w\d]+)?\/([\w\d]+)",
        url,
    )
    if r is None:
        raise AttributeError("Formato de URL do Qobuz inválido.")
    return r.groups()


def label_matches(label, filter_label) -> bool:
    """Diz se a gravadora (objeto "label" da API do Qobuz) casa com o filtro.

    Filtro numérico: precisa ser igual ao ID da gravadora. Qualquer filtro
    também casa se estiver contido no nome (sem diferenciar maiúsculas).
    Gravadora ausente nunca casa.
    """
    filter_label = str(filter_label or "").strip()
    if not filter_label:
        return True
    label = label or {}
    name = str(label.get("name") or "").strip()
    label_id = str(label.get("id") or "").strip()
    if filter_label.isdigit() and filter_label == label_id:
        return True
    return bool(name) and filter_label.lower() in name.lower()


def normalize_label_filter(value):
    """Aceita nome, ID ou URL de gravadora do Qobuz e devolve o texto do filtro
    (só o caminho da URL conta: '?ref=...' é ignorado). Vazio vira None."""
    text = str(value or "").strip()
    if not text:
        return None
    if "label/" in text:
        text = urllib.parse.urlparse(text).path.rstrip("/").split("/")[-1]
    return text or None


def filter_chunks_by_label(content, iterable_key, filter_label):
    """Remove (no próprio `content`) os lançamentos de outras gravadoras da
    listagem de um artista. Itens sem o campo "label" ficam: serão conferidos de
    novo no download. Devolve (total, mantidos)."""
    total = kept = 0
    for chunk in content:
        section = chunk.get(iterable_key) or {}
        batch = section.get("items", [])
        section["items"] = [
            item
            for item in batch
            if "label" not in item or label_matches(item.get("label"), filter_label)
        ]
        total += len(batch)
        kept += len(section["items"])
    return total, kept


def release_date_key(item) -> str:
    """Data de lançamento (AAAA-MM-DD) de um resultado da busca; '' se não tem."""
    if not isinstance(item, dict):
        return ""
    album_raw = item.get("album")
    album: dict = album_raw if isinstance(album_raw, dict) else {}
    for value in (
        item.get("release_date_original"),
        item.get("release_date"),
        album.get("release_date_original"),
        album.get("release_date"),
    ):
        if value:
            return str(value)
    return ""


def sort_by_release_date(items) -> list:
    """Do lançamento mais novo para o mais antigo (estável; sem data vai ao fim)."""
    return sorted(items, key=release_date_key, reverse=True)


def get_album_artist(qobuz_album: dict) -> list:
    # Extrai os artistas principais de um álbum a partir da resposta da API
    # do Qobuz, devolvendo uma LISTA de strings (não uma string única) para
    # permitir Multi-Artist Tagging nativo -- Vorbis Comments discretos por
    # artista em arquivos FLAC.
    """Determine the album artist from track metadata, handling multi-disc albums."""
    try:
        # Se a chave "artists" não existir, cai para o artista único do
        # campo "artist".
        if not qobuz_album.get("artists"):
            single_artist = qobuz_album.get("artist", {}).get("name", "")
            return [single_artist] if single_artist else []

        # Filtra o array mantendo só quem tem o papel "main-artist"
        # (exclui produtores, remixers, featurings, etc.).
        main_artists = list(
            filter(
                lambda a: "main-artist" in a.get("roles", []),
                qobuz_album.get("artists", []),
            )
        )

        # Extrai só os nomes e devolve como lista.
        if main_artists:
            return [a["name"] for a in main_artists]
        else:
            single_artist = qobuz_album.get("artist", {}).get("name", "")
            return [single_artist] if single_artist else []

    except Exception as e:
        # Qualquer erro inesperado na estrutura do JSON cai para o
        # artista único, em vez de propagar a exceção.
        logger.error(f"Error getting album artist: {str(e)}")
        single_artist = qobuz_album.get("artist", {}).get("name", "")
        return [single_artist] if single_artist else []


# ------------------------------------------------------------------------
# Classificação unificada de tipo de release (Single/EP/Album, alem de
# Live/Compilation). Mora aqui (utils.py) -- e nao em core.py ou
# downloader.py -- justamente pra poder ser importada pelos dois sem criar
# import circular (core.py importa downloader.py; se isso morasse num dos
# dois, o outro nao conseguiria importar).
#
# Usada em TODOS os lugares que decidem "que tipo de release e esse":
#   - a busca/TUI (pra exibir Album/EP/Single/Live/Compilation)
#   - o filtro de tipo ao explorar um artista por URL
#   - o nome da pasta de download (downloader.py, placeholder
#     {release_type} em DEFAULT_FOLDER) -- ANTES esse caminho usava uma
#     logica separada, mais simples, que so confiava cegamente na tag
#     "release_type" da API da Qobuz. Isso causava divergencia: a busca
#     podia classificar certo (ex.: "EP" pra um release de 5 faixas
#     marcado erroneamente como "Single" pela gravadora/distribuidora),
#     mas o download ia pra pasta "Single/" mesmo assim, porque usava
#     outra funcao. Unificar aqui garante que os dois caminhos SEMPRE
#     concordam.
#
# Regra oficial do projeto pra contagem de faixas (usada quando nao ha
# sinal mais forte -- ver prioridade abaixo):
#   <=3 faixas  -> Single
#   4-7 faixas  -> EP
#   >7 faixas   -> Album
# ------------------------------------------------------------------------
def classify_release_type(
    title=None,
    version=None,
    track_count=0,
    duration_seconds=0,
    api_release_type=None,
    item_type="unknown",
) -> str:
    """
    Classifica o tipo de release. Prioridade (do mais confiavel pro menos):

      1) Palavras-chave explicitas no titulo/versao ("live", "best of",
         "greatest hits", "... EP" etc.) -- sinal de intencao humana
         (artista/gravadora rotulou explicitamente), tem prioridade sobre
         qualquer contagem ou tag da API.
      2) Contagem real de faixas, pela regra oficial do projeto (<=3
         single, 4-7 EP, >7 album) -- vale MESMO que a API tenha marcado
         como outra coisa dentro do proprio trio single/ep/album, porque a
         tag da API vem errada com frequencia.
      3) Se a contagem de faixas for desconhecida/zero: cai pra duracao
         total (album se for longo) ou, na falta disso, pra tag da propria
         API / tipo do item.
    """
    base_title = (title or "").lower()
    version_tag = (version or "").lower()
    r_type = (api_release_type or "unknown").lower()
    track_count = int(track_count or 0)
    duration_seconds = int(duration_seconds or 0)

    if "live" in version_tag or "(live" in base_title or "- live" in base_title:
        return "live"

    if any(
        kw in base_title or kw in version_tag
        for kw in ["best of", "greatest hits", "anthology", "collection", "compilation"]
    ):
        return "compilation"

    if " ep" in base_title or version_tag == "ep":
        return "ep"

    if track_count > 0:
        if track_count <= 3:
            return "single"
        if track_count <= 7:
            return "ep"
        return "album"

    # Contagem de faixas desconhecida: ultimo recurso.
    if duration_seconds >= 1740:
        return "album"
    if r_type != "unknown":
        return r_type
    return item_type


def apply_legacy_charmap(filename: str) -> str:
    # Aplica regras de substituição de caracteres "legado" para
    # compatibilidade com caminhos do Windows, usando ASCII simples em vez
    # dos caracteres unicode full-width (para quem prefere ASCII puro).
    # Regras específicas pedidas pela comunidade (JosiahDanger):
    """Apply legacy character mapping for compatibility."""
    filename = filename.replace(":", "-")
    filename = filename.replace("?", "")

    # Substituições padrão para os demais caracteres inválidos no Windows.
    filename = filename.replace("/", "-")
    filename = filename.replace("\\", "-")
    filename = filename.replace("*", "-")
    filename = filename.replace('"', "'")
    filename = filename.replace("<", "[")
    filename = filename.replace(">", "]")
    filename = filename.replace("|", "-")

    # Remove traços duplos que as substituições acima podem gerar
    # (ex.: "A / B" -> "A - B").
    filename = re.sub(r"\s*-\s*-+", " -", filename)

    return filename


def clean_filename(filename: str, legacy_charmap: bool = False) -> str:
    # Limpa caracteres especiais, espaços e separadores redundantes em
    # nomes de arquivo, normalizando unicode para a forma NFC (garante
    # compatibilidade entre sistemas operacionais diferentes).

    # Normaliza a string unicode para a forma NFC primeiro.
    """Clean a filename by removing/replacing invalid characters."""
    filename = unicodedata.normalize("NFC", filename)

    # Funde múltiplos separadores consecutivos (espaços, vírgulas, pontos,
    # vírgula chinesa, dois-pontos, ponto-e-vírgula, barra vertical,
    # barras, underscore -- não inclui o símbolo "-") em um único.
    filename = re.sub(r"(?:\s*([,\.\:\;\|/\\_])\s*){2,}", r"\1 ", filename)

    # Padrões de pares de colchetes/parênteses a limpar.
    patterns = [
        # Remove pares de colchetes/parênteses que contêm só caracteres
        # especiais (sem texto útil dentro).
        (r"\(\s*\W*\s*\)", ""),  # (...)
        (r"\[\s*\W*\s*\]", ""),  # [...]
        (r"\{\s*\W*\s*\}", ""),  # {...}
        (r"<\s*\W*\s*>", ""),  # <...>
        (r"《\s*\W*\s*》", ""),  # 《...》
        (r"〈\s*\W*\s*〉", ""),  # 〈...〉
        (r"「\s*\W*\s*」", ""),  # 「...」
        (r"『\s*\W*\s*』", ""),  # 『...』
        (r"（\s*\W*\s*）", ""),  # （...）
        (r"［\s*\W*\s*］", ""),  # ［...］
        (r"【\s*\W*\s*】", ""),  # 【...】
        # Casos de borda: remove separador logo após um colchete de
        # abertura, ou logo antes de um colchete de fechamento.
        (r"(?<=[\(\[\{<《〈「『（［【])(\s*[,\.\:\;\|/\\_]\s*)\b", ""),
        (r"\b(\s*[,\.\:\;\|/\\_]\s*)(?=[】］）』」〉》>\}\]\)])", ""),
    ]

    # Aplica cada padrão em sequência.
    for pattern, replacement in patterns:
        filename = re.sub(pattern, replacement, filename)

    # Funde múltiplos espaços em um só.
    filename = re.sub(r"\s+", " ", filename)

    # Remove pontos e espaços nas extremidades.
    filename = filename.strip().strip(".").strip()

    # Escolhe entre o charmap ASCII legado e os caracteres unicode
    # full-width, conforme o parâmetro legacy_charmap.
    if legacy_charmap:
        return apply_legacy_charmap(filename)
    else:
        return invalid_chars_to_fullwidth(filename)


def invalid_chars_to_fullwidth(filename):
    # Converte caracteres ilegais em nomes de arquivo do Windows para os
    # equivalentes unicode "full-width" visualmente parecidos, em vez de
    # simplesmente removê-los ou trocar por "-".
    """Convert invalid filename characters to fullwidth equivalents."""
    invalid_to_fullwidth = {
        "/": "／",
        "\\": "＼",
        ":": "：",
        "*": "＊",
        "?": "？",
        '"': "＂",
        "<": "＜",
        ">": "＞",
        "|": "｜",
    }

    for invalid_char, fullwidth_char in invalid_to_fullwidth.items():
        filename = filename.replace(invalid_char, fullwidth_char)
    return filename


def qobuz_release_kind(album: dict) -> Optional[str]:
    """'single' ou 'full' (EP/álbum/ao vivo/coletânea) do álbum Qobuz; None se
    não der para saber. Single só casa com "X - Single" da Apple e álbum/EP
    nunca recebe a capa de um single."""
    if not album:
        return None
    try:
        tracks = (album.get("tracks") or {}).get("items") or []
        kind = classify_release_type(
            title=album.get("title"),
            version=album.get("version"),
            track_count=album.get("tracks_count") or len(tracks),
            duration_seconds=album.get("duration"),
            api_release_type=album.get("release_type") or album.get("product_type"),
        )
    except Exception:
        return None
    if not kind:
        return None
    return "single" if kind == "single" else "full"


def qobuz_track_title(item: dict) -> str:
    """Título da faixa juntando "title" e "version" (a Apple junta os dois)."""
    title = (item.get("title") or "").strip()
    version = (item.get("version") or "").strip()
    if version and version.casefold() not in title.casefold():
        title = f"{title} ({version})"
    return title


async def find_apple_cover(
    session,
    album: dict,
    item: Optional[dict] = None,
    artist: Optional[str] = None,
    trace: Optional[list] = None,
) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """ÚNICO ponto de busca de capa Apple, usado pelo download e pelo `tags`.

    Monta os mesmos critérios nos dois comandos para que o resultado seja o
    mesmo: título + versão do álbum, UPC, tipo (single x álbum) e edição
    estrita (Deluxe só com Deluxe, padrão só com padrão; nada é presumido).

    - Sem `item`: busca do ÁLBUM, confirmada pela lista de faixas.
    - Com `item`: busca da FAIXA (ISRC/título), com o álbum da faixa.
    Devolve (url, fonte, motivo), como get_apple_hq_cover.
    """
    album = album or {}
    kwargs: dict = {}
    if trace is not None:
        kwargs["trace"] = trace

    if item is None:
        tracks = (album.get("tracks") or {}).get("items") or []
        return await get_apple_hq_cover(
            session=session,
            upc=album.get("upc"),
            artist=artist or (album.get("artist") or {}).get("name", ""),
            album=album_titulo_completo(album),
            release_kind=qobuz_release_kind(album),
            deep_album_search=True,
            album_tracks=[qobuz_track_title(t) for t in tracks] or None,
            fallback_countries=True,
            strict_edition=True,
            **kwargs,
        )

    album_data = item.get("album") or album
    artist_data = (
        item.get("performer") or album_data.get("artist") or album.get("artist") or {}
    )
    return await get_apple_hq_cover(
        session=session,
        upc=album_data.get("upc") or album.get("upc"),
        isrc=item.get("isrc"),
        artist=artist or artist_data.get("name", ""),
        album=album_titulo_completo(album_data) or album_titulo_completo(album),
        track_title=qobuz_track_title(item),
        release_kind=qobuz_release_kind(album) or qobuz_release_kind(album_data),
        strict_edition=True,
        **kwargs,
    )


def get_config_paths():
    # Resolve o diretório de configuração multiplataforma (Windows,
    # Linux/macOS e iOS/a-Shell) e devolve os caminhos padrão de
    # config.ini e do banco de dados dentro dele.
    #
    # Centraliza aqui a lógica de detecção que antes só existia em cli.py
    # -- outros pontos de entrada precisam da mesma resolução exata, e
    # manter uma única fonte de verdade significa que uma mudança futura
    # nessa lógica (ex.: suportar uma nova plataforma) só precisa
    # acontecer em um lugar.
    """Return dictionary of config file paths for the platform."""
    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    config_dir = os.environ.get("CONFIG_DIR")

    if not config_dir:
        if ios_home:
            config_dir = ios_home
        else:
            # Detecção automática de iOS / a-Shell.
            home_dir = os.environ.get("HOME", "")
            if "Containers/Data/Application" in home_dir:
                config_dir = os.path.join(home_dir, "Documents")
            else:
                # Windows, macOS, Linux e Android assumem o padrão nativo
                # do sistema operacional.
                config_dir = platformdirs.user_config_dir()

    config_path = os.path.join(config_dir, "qobuz-dl")
    config_file = os.path.join(config_path, "config.ini")
    qobuz_db = os.path.join(config_path, "qobuz_dl.db")

    return {
        "config_dir": config_dir,
        "config_path": config_path,
        "config_file": config_file,
        "qobuz_db": qobuz_db,
    }
