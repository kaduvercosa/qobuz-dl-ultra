# Funções utilitárias diversas do qobuz-dl: geração de playlist .m3u,
# filtragem de discografia, formatação de duração, checagem de binários
# externos (ffmpeg/fpcalc), verificação de integridade de áudio, limpeza de
# nomes de arquivo e resolução de caminhos de configuração multiplataforma.

import re
import string
import os
import logging
import shutil
import subprocess
import time
from qobuz_dl.color import RED, WARNING as YELLOW, INFO as CYAN, OFF
import unicodedata
import platformdirs

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

EXTENSIONS = (".mp3", ".flac")


class PartialFormatter(string.Formatter):
    # Formatter customizado que trata variáveis ausentes sem lançar
    # KeyError. Usado para montar nomes de arquivo/pasta a partir de tags
    # de metadados que podem não vir preenchidas na resposta da API.

    def __init__(self, missing="n/a", bad_fmt="n/a"):
        self.missing, self.bad_fmt = missing, bad_fmt

    def get_field(self, field_name, args, kwargs):
        # Campo ausente: em vez de lançar KeyError/AttributeError, devolve
        # None para que format_field() substitua pelo valor `missing`.
        try:
            val = super(PartialFormatter, self).get_field(field_name, args, kwargs)
        except (KeyError, AttributeError):
            val = None, field_name
        return val

    def format_field(self, value, spec):
        # Valor vazio/None vira `self.missing`. Spec de formatação inválido
        # (ex.: aplicar formatação numérica a uma string) vira `self.bad_fmt`
        # em vez de lançar ValueError.
        if not value:
            return self.missing
        try:
            return super(PartialFormatter, self).format_field(value, spec)
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
    import os
    import re
    import logging
    from mutagen.id3 import ID3
    from mutagen.flac import FLAC
    from mutagen import File

    logger = logging.getLogger(__name__)
    EXTENSIONS = (".mp3", ".flac")

    track_list = ["#EXTM3U"]
    rel_folder = os.path.basename(os.path.normpath(pl_directory))
    pl_name = rel_folder + ".m3u8"
    pl_full_path = os.path.join(pl_directory, pl_name)

    # 1. Varre a pasta local e extrai as tags de cada arquivo de áudio.
    local_files_info = []
    for local, dirs, files in os.walk(pl_directory):
        dirs.sort()
        for f in files:
            if os.path.splitext(f)[-1].lower() in EXTENSIONS:
                audio_full_path = os.path.abspath(os.path.join(local, f))
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
                        audio = ID3(audio_full_path)
                        # Frames TXXX customizados precisam ser varridos
                        # manualmente (não têm chave direta como TIT2/TPE1).
                        for frame in audio.getall("TXXX"):
                            if frame.desc.upper() == "QOBUZTRACKID":
                                info["qobuz_id"] = frame.text[0]
                                break
                        isrc_frame = audio.get("TSRC")
                        info["isrc"] = isrc_frame.text[0] if isrc_frame else None
                        tit2 = audio.get("TIT2")
                        info["title"] = tit2.text[0] if tit2 else ""
                        tpe1 = audio.get("TPE1")
                        info["artist"] = tpe1.text[0] if tpe1 else ""
                except Exception as e:
                    logger.debug(f"Erro ao ler tags de {f}: {e}")
                    info["title"] = os.path.splitext(f)[0]  # título de fallback

                local_files_info.append(info)

    ordered_files = []

    # 2. Casa os arquivos locais com a ordem da API do Qobuz (4 passes).
    if remote_items:
        # Pré-indexa os arquivos locais em dicionários para buscas O(1).
        by_tid = {str(f["qobuz_id"]): f for f in local_files_info if f.get("qobuz_id")}
        by_isrc = {str(f["isrc"]): f for f in local_files_info if f.get("isrc")}
        by_title = {
            str(f["title"]).strip().lower(): f
            for f in local_files_info
            if f.get("title")
        }

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
                or by_isrc.get(isrc)
                or by_title.get(track_title.strip().lower())
            )

            # Passe 4: fallback por substring no nome do arquivo, quando
            # nenhum dos anteriores encontrou correspondência.
            if not best_match and track_title != "Unknown Title":
                for f_info in local_files_info:
                    if track_title.lower() in os.path.basename(f_info["path"]).lower():
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

    def print_album(album: dict) -> None:
        # Auxiliar só para depuração (logger.debug).
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
        version = album.get("version", "")
        title = album.get("title", "")
        regex = TYPE_REGEXES[album_t]
        return re.search(regex, f"{title} {version}") is not None

    def essence(album: dict) -> str:
        # Reduz o título do álbum à sua "essência": ignora texto entre
        # parênteses/colchetes e deixa tudo minúsculo. Usado para agrupar
        # álbuns com nomes parecidos mas não idênticos (ex.: "Album" e
        # "Album (Deluxe Edition)" caem no mesmo grupo).
        r = re.match(r"([^\(]+)(?:\s*[\(\[][^\)][\)\]])*", album)
        return r.group(1).strip().lower()

    requested_artist = contents[0]["name"]
    items = [item["albums"]["items"] for item in contents][0]

    # Agrupa os álbuns duplicados pelo título "essencial".
    title_grouped = dict()
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
    return time.strftime("%H:%M:%S", time.gmtime(duration))


# Cache do resultado da checagem de binários externos. Chave = nome do
# binário, valor = caminho encontrado ou None. Existe para que o aviso saia
# UMA vez por execução, não uma vez por arquivo processado.
_BINARIOS_CHECADOS = {}

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


def verify_audio_integrity(filepath, timeout=180):
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
    if not os.path.isfile(filepath):
        return False, "Arquivo nao encontrado."

    # Consulta o cache em vez de descobrir a ausência do ffmpeg via
    # FileNotFoundError a cada arquivo. O aviso completo, com instrução de
    # instalação, já saiu uma única vez em checar_binarios_externos().
    ffmpeg = encontrar_binario("ffmpeg")
    if not ffmpeg:
        return False, "ffmpeg nao disponivel -- integridade nao verificada."

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
            False,
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
    return r.groups()


def get_album_artist(qobuz_album: dict) -> list:
    # Extrai os artistas principais de um álbum a partir da resposta da API
    # do Qobuz, devolvendo uma LISTA de strings (não uma string única) para
    # permitir Multi-Artist Tagging nativo -- Vorbis Comments discretos por
    # artista em arquivos FLAC.
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
    item_type="album",
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


def get_config_paths():
    # Resolve o diretório de configuração multiplataforma (Windows,
    # Linux/macOS e iOS/a-Shell) e devolve os caminhos padrão de
    # config.ini e do banco de dados dentro dele.
    #
    # Centraliza aqui a lógica de detecção que antes só existia em cli.py
    # -- outros pontos de entrada (ex.: radar.py) precisam da mesma
    # resolução exata, e manter uma única fonte de verdade significa que
    # uma mudança futura nessa lógica (ex.: suportar uma nova plataforma)
    # só precisa acontecer em um lugar.
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
