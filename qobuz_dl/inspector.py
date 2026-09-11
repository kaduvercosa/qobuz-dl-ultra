# ==============================================================================
# inspector.py -- "qobuz-dl inspect": inspeciona um arquivo de áudio local.
#
# Três partes:
#   1. Navegador de arquivos em tela cheia (prompt_toolkit) -- mesmo esquema
#      de teclas do seletor principal em core.py._tui_select() (setas/j-k,
#      PageUp/PageDown, g/G, dígitos, Enter, Esc/Ctrl-C), pra funcionar
#      identico com teclado físico (Bluetooth/Magic Keyboard) e o teclado
#      na tela do iOS/a-Shell.
#   2. Extração de TODAS as tags/metadados do arquivo escolhido -- sem
#      curadoria: cada campo que existe no arquivo aparece no relatório
#      (mesma filosofia do relatório de conta em cli.py).
#   3. Checagem heurística de "isso é genuinamente lossless/hi-res, ou é
#      upsample/transcode de uma fonte lossy disfarçado de FLAC?" via
#      análise espectral (extra opcional 'analyze': numpy).
# ==============================================================================
import os
import subprocess

from qobuz_dl import ui
from qobuz_dl.utils import encontrar_binario, format_duration

try:
    from prompt_toolkit import Application
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout.containers import HSplit, ScrollOffsets, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.layout.layout import Layout
except ImportError:
    Application = None

# Reaproveita o MESMO estilo visual do seletor principal (cores, "hovered",
# etc.) -- assim o navegador de arquivos parece parte do mesmo programa,
# não um widget à parte com visual diferente.
from qobuz_dl.core import pt_style

# O gráfico é gerado como HTML com SVG embutido (ver _gerar_grafico_html
# abaixo), não como PNG -- por isso não depende de Pillow. Motivo: um PNG
# é uma imagem estática, "congelada" no momento em que foi gerada; não
# tem como ela reagir ao modo claro/escuro do dispositivo de quem for
# ABRIR o arquivo depois (só saberia o modo de quem gerou, se soubesse
# disso). HTML + CSS resolve isso de verdade via `prefers-color-scheme`,
# que o navegador aplica automaticamente com base no modo do aparelho de
# quem está vendo -- e atualiza sozinho se a pessoa alternar o modo com o
# arquivo já aberto.

_AUDIO_EXTS = (
    ".flac",
    ".mp3",
    ".m4a",
    ".alac",
    ".ogg",
    ".opus",
    ".wav",
    ".aiff",
    ".ape",
    ".wv",
)


def _detectar_pasta_padrao():
    """
    Decide de onde o navegador de arquivos deve abrir quando o usuário NÃO
    digita uma pasta -- olhando pra que tipo de dispositivo/SO está
    rodando o programa, não só assumindo o diretório atual.

    Segue a MESMA convenção de detecção de iOS já usada em
    `utils.get_config_paths()` (variável de ambiente QOBUZ_DL_IOS_HOME
    como override manual, depois o padrão "Containers/Data/Application"
    no $HOME pra detectar a-Shell/iOS automaticamente) -- pra não ter duas
    lógicas diferentes de "isso aqui é iOS?" no mesmo projeto.

    Ordem de prioridade:
        1. QOBUZ_DL_IOS_HOME (override manual, igual ao resto do projeto)
        2. iOS / a-Shell detectado automaticamente -> ~/Documents
        3. Android / Termux detectado automaticamente -> pasta de Música
           do armazenamento compartilhado, se existir
        4. Windows / macOS / Linux (desktop) -> ~/Music (ou ~/Música em
           instalações localizadas em português), se existir
        5. Fallback final -> diretório atual (os.getcwd())

    Cada etapa SÓ é aceita se a pasta realmente existir -- senão cai pra
    próxima da lista, terminando sempre em algo que existe de verdade.
    """
    candidatos = []

    ios_home = os.environ.get("QOBUZ_DL_IOS_HOME")
    if ios_home:
        candidatos.append(ios_home)

    home_dir = os.environ.get("HOME", "")

    # iOS / a-Shell: o próprio a-Shell expõe esse padrão de caminho no
    # $HOME (sandbox de app do iOS); "Documents" é a única pasta que o
    # Arquivos.app do iOS deixa o usuário ver/importar música de fato.
    if "Containers/Data/Application" in home_dir:
        candidatos.append(os.path.join(home_dir, "Documents"))

    # Android / Termux: Termux roda com HOME tipo
    # /data/data/com.termux/files/home, e o armazenamento compartilhado
    # do Android (onde o usuário de fato vê "Música" no gerenciador de
    # arquivos) fica montado à parte, não dentro do HOME do Termux.
    if (
        "com.termux" in home_dir
        or os.environ.get("ANDROID_ROOT")
        or os.environ.get("ANDROID_DATA")
    ):
        candidatos.append("/storage/emulated/0/Music")
        candidatos.append("/sdcard/Music")
        candidatos.append(os.path.join(home_dir, "storage", "music"))

    # Windows / macOS / Linux "de mesa": ~/Music é o nome convencional em
    # instalações em inglês; ~/Música cobre o caso comum de SO
    # localizado em português (o nome real da pasta varia por idioma do
    # sistema, então tentamos as duas grafias mais prováveis).
    candidatos.append(os.path.join(home_dir or os.path.expanduser("~"), "Music"))
    candidatos.append(os.path.join(home_dir or os.path.expanduser("~"), "Música"))

    for candidato in candidatos:
        if candidato and os.path.isdir(candidato):
            return candidato

    # Nada acima existe -- cai pro HOME (se existir) e por fim pro
    # diretório atual, igual ao comportamento anterior.
    if home_dir and os.path.isdir(home_dir):
        return home_dir
    return os.getcwd()


def _listar_diretorio(caminho):
    """
    Lista um diretório pra navegação: subpastas primeiro (ordem alfabética,
    sem contar maiúsc/minúsc), depois arquivos de áudio reconhecidos --
    tudo mais (fotos, .txt, .lrc, arquivos ocultos) fica de fora da lista,
    já que o objetivo aqui é escolher um ARQUIVO DE ÁUDIO pra inspecionar.

    Devolve uma lista de dicts {"nome", "caminho", "is_dir"}. Diretórios
    sem permissão de leitura não derrubam a navegação -- aparecem vazios
    (o usuário só não consegue entrar neles).
    """
    entradas = []
    try:
        with os.scandir(caminho) as it:
            brutos = list(it)
    except (PermissionError, FileNotFoundError, NotADirectoryError):
        return entradas

    pastas = sorted(
        (
            e
            for e in brutos
            if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
        ),
        key=lambda e: e.name.lower(),
    )
    arquivos = sorted(
        (
            e
            for e in brutos
            if e.is_file(follow_symlinks=False)
            and not e.name.startswith(".")
            and e.name.lower().endswith(_AUDIO_EXTS)
        ),
        key=lambda e: e.name.lower(),
    )

    for e in pastas:
        entradas.append({"nome": f"📁 {e.name}/", "caminho": e.path, "is_dir": True})
    for e in arquivos:
        entradas.append({"nome": f"🎵 {e.name}", "caminho": e.path, "is_dir": False})

    return entradas


async def _navegar_arquivos(diretorio_inicial):
    """
    Navegador de arquivos em tela cheia. Retorna o caminho do arquivo de
    áudio escolhido, ou None se o usuário cancelou (Esc/Ctrl+C).

    Atalhos (mesmo esquema de core.py._tui_select, ver comentários lá):
        ↑/↓ ou j/k       -- move o cursor
        PageUp/PageDown  -- pula 10 itens
        g / G            -- vai pro primeiro / último item
        1-9              -- pula direto pro item daquela posição
        Enter ou → ou l  -- entra na pasta / escolhe o arquivo sob o cursor
        ← ou h ou Backspace -- volta pra pasta anterior
        Ctrl+C ou Esc    -- cancela (devolve None)
    """
    if Application is None:
        ui.error("prompt_toolkit não está instalado. Rode: pip install prompt_toolkit")
        return None

    estado = {
        "dir": os.path.abspath(os.path.expanduser(diretorio_inicial)),
        "entradas": [],
        "cursor": 0,
        "erro": None,
    }

    def _recarregar():
        """Reload the file list in the file browser."""
        estado["entradas"] = _listar_diretorio(estado["dir"])
        estado["cursor"] = min(estado["cursor"], max(0, len(estado["entradas"]) - 1))

    _recarregar()

    bindings = KeyBindings()

    def _mover(delta):
        """Move cursor in the file browser."""

        def _fn(event):
            if estado["entradas"]:
                estado["cursor"] = max(
                    0, min(len(estado["entradas"]) - 1, estado["cursor"] + delta)
                )

        return _fn

    # Setas + j/k -- as duas formas funcionam sempre, sem exigir troca de
    # layout de teclado (importante no teclado físico Bluetooth E no
    # teclado na tela do a-Shell/iSH, que às vezes esconde as setas numa
    # barra extra).
    bindings.add("up")(_mover(-1))
    bindings.add("k")(_mover(-1))
    bindings.add("down")(_mover(1))
    bindings.add("j")(_mover(1))

    @bindings.add("pageup")
    def _(event):
        if estado["entradas"]:
            estado["cursor"] = max(0, estado["cursor"] - 10)

    @bindings.add("pagedown")
    def _(event):
        if estado["entradas"]:
            estado["cursor"] = min(len(estado["entradas"]) - 1, estado["cursor"] + 10)

    @bindings.add("g")
    def _(event):
        estado["cursor"] = 0

    @bindings.add("G")
    def _(event):
        if estado["entradas"]:
            estado["cursor"] = len(estado["entradas"]) - 1

    def _make_digit_jump(n):
        def _fn(event):
            idx = n - 1
            if estado["entradas"] and idx < len(estado["entradas"]):
                estado["cursor"] = idx

        return _fn

    for _digito in range(1, 10):
        bindings.add(str(_digito))(_make_digit_jump(_digito))

    def _entrar_ou_escolher(event):
        """Enter directory or select file."""
        if not estado["entradas"]:
            return
        item = estado["entradas"][estado["cursor"]]
        if item["is_dir"]:
            estado["dir"] = item["caminho"]
            estado["cursor"] = 0
            estado["erro"] = None
            _recarregar()
        else:
            event.app.exit(result=item["caminho"])

    bindings.add("enter")(_entrar_ou_escolher)
    bindings.add("right")(_entrar_ou_escolher)
    bindings.add("l")(_entrar_ou_escolher)

    def _voltar(event):
        """Go back to parent directory."""
        pai = os.path.dirname(estado["dir"].rstrip(os.sep))
        # Já está na raiz do sistema de arquivos (dirname("/") == "/",
        # dirname("C:\\") == "C:\\") -- não tem pra onde voltar.
        if pai and pai != estado["dir"]:
            estado["dir"] = pai
            estado["cursor"] = 0
            estado["erro"] = None
            _recarregar()

    bindings.add("left")(_voltar)
    bindings.add("h")(_voltar)
    bindings.add("backspace")(_voltar)

    @bindings.add("r")
    def _(event):
        # Força redesenho -- útil se o terminal não reagir sozinho a girar
        # a tela do iPad/iPhone (mesmo atalho do seletor principal).
        _recarregar()
        event.app.invalidate()

    @bindings.add("c-c")
    def _(event):
        event.app.exit(result=None)

    @bindings.add("escape")
    def _(event):
        event.app.exit(result=None)

    def get_header_text():
        """Generate header text for file browser UI."""
        return [
            ("class:title", "\n === Escolha um arquivo de áudio ===\n\n"),
            ("class:footer", f" 📂 {estado['dir']}\n"),
            ("class:meta", ""),
        ]

    def get_list_text():
        """Generate list text for file browser UI."""
        res = []
        if not estado["entradas"]:
            res.append(
                ("class:footer", "\n   (pasta vazia ou sem arquivos de áudio)\n")
            )
            return res

        for i, item in enumerate(estado["entradas"]):
            hovered = i == estado["cursor"]
            estilo = (
                "class:hovered"
                if hovered
                else ("class:item_title" if item["is_dir"] else "")
            )
            prefixo = "▶ " if hovered else "  "
            res.append((estilo, f" {prefixo}{item['nome']}\n"))

        return res

    def get_footer_text():
        """Generate footer text for file browser UI."""
        msg = (
            " [↑↓/jk] Mover   [→/l/Enter] Entrar/Escolher   [←/h] Voltar   "
            "[1-9] Ir para   [Esc] Cancelar"
        )
        return [("class:footer", msg + "\n")]

    header_window = Window(
        content=FormattedTextControl(text=get_header_text), dont_extend_height=True
    )
    list_window = Window(
        content=FormattedTextControl(text=get_list_text, focusable=True),
        scroll_offsets=ScrollOffsets(top=2, bottom=2),
        wrap_lines=False,
    )
    footer_window = Window(
        content=FormattedTextControl(text=get_footer_text), dont_extend_height=True
    )

    layout = Layout(HSplit([header_window, list_window, footer_window]))
    app = Application(
        layout=layout,
        key_bindings=bindings,
        full_screen=True,
        style=pt_style,
        mouse_support=True,
    )

    return await app.run_async()


def _formatar_tamanho(num_bytes):
    """Format file size in human-readable format."""
    for unidade in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024:
            return f"{num_bytes:.1f} {unidade}" if unidade != "B" else f"{num_bytes} B"
        num_bytes /= 1024
    return f"{num_bytes:.1f} TB"


def _extrair_tudo(caminho):
    """
    Extrai TODOS os dados de um arquivo de áudio: informações técnicas do
    stream (codec, sample rate, profundidade de bits, canais, duração,
    bitrate) e CADA tag/campo de metadado presente -- sem selecionar um
    subconjunto: se o campo existe no arquivo, ele aparece aqui.

    Retorna um dict {"tecnico": {...}, "tags": {...}, "capas": [...]}.
    Lança a exceção original se o mutagen não reconhecer o arquivo --
    quem chama decide como reportar isso ao usuário.
    """
    ext = os.path.splitext(caminho)[1].lower()
    dados = {"tecnico": {}, "tags": {}, "capas": []}

    tamanho = os.path.getsize(caminho)
    dados["tecnico"]["Arquivo"] = os.path.basename(caminho)
    dados["tecnico"]["Tamanho"] = _formatar_tamanho(tamanho)
    dados["tecnico"]["Extensão"] = ext.lstrip(".").upper()

    if ext == ".flac":
        from mutagen.flac import FLAC

        audio = FLAC(caminho)
        info = audio.info
        dados["tecnico"].update(
            {
                "Codec": "FLAC (lossless)",
                "Sample rate": f"{info.sample_rate} Hz",
                "Profundidade de bits": f"{info.bits_per_sample} bits",
                "Canais": info.channels,
                "Duração": format_duration(info.length),
                "Bitrate médio": (
                    f"{info.bitrate // 1000} kbps" if info.bitrate else "N/A"
                ),
            }
        )
        dados["_sample_rate"] = info.sample_rate
        dados["_duracao_s"] = info.length

        if audio.tags:
            for chave, valores in audio.tags:
                atual = dados["tags"].get(chave)
                dados["tags"][chave] = f"{atual}, {valores}" if atual else valores

        for i, pic in enumerate(audio.pictures, 1):
            dados["capas"].append(
                f"[{i}] {pic.mime}, {pic.width}x{pic.height}, {_formatar_tamanho(len(pic.data))}"
            )

    elif ext == ".mp3":
        from mutagen.id3 import APIC
        from mutagen.mp3 import MP3

        audio = MP3(caminho)
        info = audio.info
        dados["tecnico"].update(
            {
                "Codec": "MP3",
                "Sample rate": f"{info.sample_rate} Hz",
                "Canais": info.channels,
                "Duração": format_duration(info.length),
                "Bitrate": f"{info.bitrate // 1000} kbps",
                "Modo": getattr(info, "mode", "N/A"),
            }
        )
        dados["_sample_rate"] = info.sample_rate
        dados["_duracao_s"] = info.length

        if audio.tags:
            for frame_id, frame in audio.tags.items():
                if isinstance(frame, APIC):
                    dados["capas"].append(
                        f"[{frame_id}] {frame.mime}, {_formatar_tamanho(len(frame.data))}"
                    )
                else:
                    dados["tags"][frame_id] = str(frame)

    else:
        # Formato genérico via mutagen.File (M4A/ALAC, OGG Vorbis, Opus,
        # WAV, AIFF, APE, WavPack...).
        from mutagen import File as MutagenFile

        audio = MutagenFile(caminho)
        if audio is None:
            raise ValueError("Formato de áudio não reconhecido pelo mutagen.")
        info = audio.info
        dados["tecnico"].update(
            {
                "Codec": type(audio).__name__,
                "Sample rate": f"{getattr(info, 'sample_rate', '?')} Hz",
                "Canais": getattr(info, "channels", "?"),
                "Duração": format_duration(info.length),
                "Bitrate": (
                    f"{info.bitrate // 1000} kbps"
                    if getattr(info, "bitrate", None)
                    else "N/A"
                ),
            }
        )
        dados["_sample_rate"] = getattr(info, "sample_rate", 44100)
        dados["_duracao_s"] = info.length

        if audio.tags:
            for chave, valor in audio.tags.items():
                if isinstance(valor, list):
                    valor = ", ".join(str(v) for v in valor)
                dados["tags"][str(chave)] = str(valor)

    return dados


def _checar_genuinidade(caminho, sample_rate, duracao_s):
    """
    Heurística de "isso é genuinamente lossless/hi-res, ou é upsample de
    uma fonte lossy disfarçado de FLAC/hi-res?": decodifica algumas
    janelas do áudio via ffmpeg, roda FFT em cada uma e mede até que
    frequência o espectro realmente tem conteúdo antes de cair no piso de
    ruído -- fontes lossy (MP3/AAC) têm um corte abrupto bem abaixo do
    Nyquist real, mesmo depois de reencodadas pra FLAC/hi-res.

    IMPORTANTE: isso é uma HEURÍSTICA, não uma prova matemática. Masters
    antigos ou gravações com corte natural podem dar falso positivo; um
    transcode de bitrate muito alto pode passar despercebido. Trate como
    um indício forte, não veredito definitivo. O relatório já gera um
    gráfico de resposta em frequência (ver _gerar_grafico_html logo
    logo abaixo); se quiser o espectrograma 2D clássico do ffmpeg por
    fora, pra comparar, o comando manual é:
        ffmpeg -i arquivo.flac -lavfi showspectrumpic=s=1024x512 spec.png
    """
    try:
        import numpy as np
    except ImportError:
        return {
            "disponivel": False,
            "motivo": (
                "Pacote opcional 'numpy' não instalado -- necessário pra "
                "análise espectral. Rode: pip install 'qobuz-dl-ultra[analyze]'"
            ),
        }

    ffmpeg = encontrar_binario("ffmpeg")
    if not ffmpeg:
        return {
            "disponivel": False,
            "motivo": "ffmpeg não encontrado -- necessário pra decodificar o áudio antes da análise.",
        }

    if not sample_rate or not duracao_s or duracao_s < 3:
        return {
            "disponivel": False,
            "motivo": "Faixa curta demais ou sample rate desconhecido pra uma análise confiável.",
        }

    nyquist = sample_rate / 2
    janela_s = min(6.0, duracao_s / 4)
    espectros = []

    # 4 janelas espalhadas nos primeiros 90% da faixa -- evita fade-in/
    # fade-out (silêncio ou quase-silêncio nas pontas distorceria a média).
    for proporcao_inicio in (0.10, 0.35, 0.60, 0.85):
        inicio = duracao_s * proporcao_inicio
        if inicio + janela_s > duracao_s:
            continue
        try:
            resultado = subprocess.run(
                [
                    ffmpeg,
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(inicio),
                    "-t",
                    str(janela_s),
                    "-i",
                    caminho,
                    "-ac",
                    "1",
                    "-f",
                    "f32le",
                    "-",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, OSError):
            continue
        if resultado.returncode != 0 or not resultado.stdout:
            continue

        amostras = np.frombuffer(resultado.stdout, dtype=np.float32)
        if len(amostras) < 1024:
            continue

        janela_hann = np.hanning(len(amostras))
        espectro = np.abs(np.fft.rfft(amostras * janela_hann))
        espectros.append(espectro)

    if not espectros:
        return {
            "disponivel": False,
            "motivo": "Não foi possível decodificar amostras suficientes pra análise.",
        }

    tamanho_min = min(len(e) for e in espectros)
    media = np.mean([e[:tamanho_min] for e in espectros], axis=0)
    media_db = 20 * np.log10(media + 1e-12)
    freqs = np.linspace(0, nyquist, tamanho_min)

    pico_db = float(np.max(media_db))
    # Piso de ruído estimado pelos últimos 5% da banda (região que, em
    # qualquer fonte real, já é só ruído residual).
    fatia_topo = max(1, tamanho_min // 20)

    # Converte para uma lista pura do Python para evitar o bug do numpy.ma na mediana
    fatia_lista = media_db[-fatia_topo:].tolist()
    if isinstance(fatia_lista, list):
        # Remove eventuais valores NaN ou infinitos para evitar falhas
        fatia_limpa = [x for x in fatia_lista if not (np.isnan(x) or np.isinf(x))]
        if fatia_limpa:
            fatia_limpa.sort()
            mid = len(fatia_limpa) // 2
            piso_db = float(
                fatia_limpa[mid]
                if len(fatia_limpa) % 2 != 0
                else (fatia_limpa[mid - 1] + fatia_limpa[mid]) / 2
            )
        else:
            piso_db = float(np.min(media_db))
    else:
        piso_db = float(np.median(media_db[-fatia_topo:]))

    limiar_db = max(piso_db + 10, pico_db - 60)

    acima = np.where(media_db >= limiar_db)[0]
    corte_hz = float(freqs[acima[-1]]) if len(acima) else 0.0
    proporcao = (corte_hz / nyquist) if nyquist else 0.0

    if proporcao >= 0.90:
        veredito, cor = "genuino", "ok"
        mensagem = "  Espectro ocupa quase toda a banda declarada -- consistente com áudio genuinamente lossless/hi-res."
    elif proporcao >= 0.60:
        veredito, cor = "inconclusivo", "warn"
        mensagem = (
            "  Corte um pouco abaixo do esperado -- pode ser masterização/"
            "gravação real, ou compressão leve. Não é conclusivo sozinho."
        )
    else:
        veredito, cor = "suspeito", "error"
        mensagem = (
            f"  Corte abrupto em ~{corte_hz / 1000:.1f} kHz, bem abaixo dos "
            f"~{nyquist / 1000:.1f} kHz esperados pra {sample_rate} Hz -- "
            "padrão típico de fonte lossy (MP3/AAC) upsampleada pra "
            "parecer lossless/hi-res."
        )

    return {
        "disponivel": True,
        "veredito": veredito,
        "cor": cor,
        "mensagem": mensagem,
        "corte_hz": corte_hz,
        "nyquist_hz": nyquist,
        "proporcao": proporcao,
        # Dados brutos do espectro médio, em listas Python puras (não
        # arrays numpy) -- pra quem for desenhar o gráfico não precisar
        # depender de numpy de novo lá na frente.
        "freqs_hz": freqs.tolist(),
        "media_db": media_db.tolist(),
        "piso_db": piso_db,
        "limiar_db": limiar_db,
        "pico_db": pico_db,
    }


# --------------------------------------------------------------------------
# Paleta inspirada na linguagem visual da Nothing (preto/branco + um único
# vermelho de destaque, grid de pontinhos lembrando os componentes expostos
# do design deles) -- NÃO usa o logo, a fonte "NType" ou qualquer asset
# registrado da marca; é só a mesma ideia de "poucas cores, grid de pontos,
# tudo em caixa alta monoespaçada" aplicada a um gráfico técnico.
#
# As cores abaixo são o modo ESCURO (usado como valor inicial/`:root` no
# CSS); o modo claro é definido só como um bloco `@media
# (prefers-color-scheme: light)` que sobrescreve essas mesmas variáveis --
# ver _gerar_grafico_html() logo abaixo.
# --------------------------------------------------------------------------
_CSS_VARS_ESCURO = {
    "--cor-fundo": "#0a0a0a",
    "--cor-texto": "#f5f5f5",
    "--cor-destaque": "#e4182b",
    "--cor-secundario": "#9a9a9a",
    "--cor-grade": "#333333",
    "--cor-hachura": "#3a1216",
    "--cor-painel-fundo": "#141414",
}
_CSS_VARS_CLARO = {
    "--cor-fundo": "#fafaf8",
    "--cor-texto": "#141414",
    "--cor-destaque": "#c81023",
    "--cor-secundario": "#6e6e6e",
    "--cor-grade": "#dedede",
    "--cor-hachura": "#ffd6d9",
    "--cor-painel-fundo": "#f1f1ef",
}


def _suavizar(valores, janela=11):
    # Média móvel simples -- a curva crua do FFT é serrilhada demais pra
    # quem não é engenheiro de áudio enxergar o que importa (o formato
    # geral e onde o corte acontece). Suavizar deixa só o essencial
    # visível, sem mudar a conclusão (o corte continua no mesmo lugar).
    """Smooth pixel array for cover art display."""
    n = len(valores)
    if janela <= 1 or n == 0:
        return list(valores)
    metade = janela // 2
    return [
        sum(valores[max(0, i - metade) : min(n, i + metade + 1)])
        / len(valores[max(0, i - metade) : min(n, i + metade + 1)])
        for i in range(n)
    ]


def _esc(txt):
    # Escapa texto antes de embutir em HTML/SVG -- o nome da faixa vem
    # do nome do arquivo do usuário, que pode ter "&", "<", ">" etc.
    """Escape HTML special characters."""
    import html

    return html.escape(str(txt), quote=True)


def _gerar_grafico_html(caminho_audio, genuinidade):
    """
    Gera um gráfico de resposta em frequência como um arquivo HTML
    autocontido (SVG embutido, CSS embutido, sem dependências externas
    nem JavaScript) -- no lugar de uma imagem PNG estática.

    POR QUÊ HTML E NÃO PNG: um PNG é "congelado" no instante em que foi
    gerado -- não existe forma de uma imagem estática reagir ao modo
    claro/escuro do dispositivo de quem for ABRIR o arquivo depois (na
    melhor das hipóteses, só saberia o modo de quem gerou o arquivo).
    HTML + CSS resolve isso de verdade: o navegador aplica a media query
    `prefers-color-scheme` com base no modo do APARELHO DE QUEM ESTÁ
    VENDO, e atualiza sozinho, ao vivo, se a pessoa alternar o modo do
    sistema com o arquivo já aberto -- sem precisar gerar o arquivo de
    novo.

    Como bônus, texto em HTML/SVG é renderizado pela fonte do sistema do
    navegador, então acentos e travessão funcionam sem o problema de
    fonte de fallback que existia na versão em PNG (Pillow, sem uma TTF
    disponível no dispositivo, caía numa fonte bitmap sem esses glifos).

    Mesma ideia visual da versão anterior: manchete em português simples
    com a conclusão, curva suavizada, área "morta" (do corte até o
    Nyquist esperado) destacada com hachura, seta apontando pro ponto do
    corte, linha de referência do limite da audição humana (~20kHz), e
    um parágrafo explicando o que aquele padrão significa.

    Salva como "<nome-da-faixa>-spec.html" na MESMA pasta do arquivo de
    áudio (confirmado via os.path.dirname(caminho_audio) -- nunca em
    outro lugar). Devolve o caminho do HTML gerado, ou None se os dados
    do espectro não estiverem disponíveis.
    """
    if not genuinidade.get("disponivel") or not genuinidade.get("freqs_hz"):
        return None

    freqs = genuinidade["freqs_hz"]
    media_db = _suavizar(genuinidade["media_db"])
    nyquist_hz = genuinidade["nyquist_hz"]
    corte_hz = genuinidade["corte_hz"]
    piso_db = genuinidade["piso_db"]
    pico_db = genuinidade["pico_db"]
    veredito = genuinidade["veredito"]
    corte_khz = corte_hz / 1000
    nyquist_khz = nyquist_hz / 1000
    nome_faixa = os.path.splitext(os.path.basename(caminho_audio))[0]

    largura, altura = 1200, 820
    margem_esq, margem_dir = 70, 40
    y0, y1 = 190, 560
    x0, x1 = margem_esq, largura - margem_dir

    db_min = piso_db - 10
    db_max = pico_db + 5

    def _x_px(hz):
        """Calculate X position for cover art pixels."""
        return x0 + (hz / nyquist_hz) * (x1 - x0) if nyquist_hz else x0

    def _y_px(db):
        """Calculate Y position for cover art pixels."""
        db_clamp = max(db_min, min(db_max, db))
        return y1 - (db_clamp - db_min) / (db_max - db_min) * (y1 - y0)

    # -- Manchete e explicação, adaptadas ao veredito --------------------
    manchete = {
        "genuino": "Este arquivo parece genuinamente Hi-Res / lossless",
        "inconclusivo": "Não dá pra confirmar com certeza -- resultado inconclusivo",
        "suspeito": "Este arquivo provavelmente NÃO é o Hi-Res genuíno que alega ser",
    }[veredito]
    explicacao = {
        "genuino": (
            f"O som continua até perto do limite máximo esperado ({nyquist_khz:.1f}kHz)",
            "sem nenhum degrau abrupto no meio do caminho.",
            "Isso é consistente com uma fonte genuinamente lossless/Hi-Res.",
            "Na prática: vale a pena manter esse arquivo na qualidade que está.",
        ),
        "inconclusivo": (
            f"O corte em {corte_khz:.1f}kHz está um pouco abaixo do esperado",
            f"({nyquist_khz:.1f}kHz), mas não o suficiente pra ter certeza sozinho.",
            "Pode ser uma gravação/masterização real, ou uma compressão leve.",
            "Na prática: não dá pra cravar upsample só com este teste -- se quiser",
            "mais certeza, compare com outra fonte da mesma faixa.",
        ),
        "suspeito": (
            f"O som para de repente em {corte_khz:.1f}kHz, bem abaixo dos",
            f"{nyquist_khz:.1f}kHz que esse arquivo deveria ter.",
            "Isso é o padrão clássico de um MP3/AAC comprimido, convertido",
            "depois pra parecer um FLAC/Hi-Res.",
            "Na prática: baixar em qualidade maior não vai trazer mais detalhe",
            "sonoro real aqui -- só ocupa mais espaço.",
        ),
    }[veredito]
    veredito_txt = {
        "genuino": "GENUÍNO",
        "inconclusivo": "INCONCLUSIVO",
        "suspeito": "SUSPEITO",
    }[veredito]

    saiba_mais = (
        (
            "O que significa \u201clossless\u201d e \u201clossy\u201d?",
            "Lossless (FLAC, ALAC, WAV) guarda o áudio sem descartar nenhuma "
            "informação do original. Lossy (MP3, AAC) descarta partes do som pra "
            "ocupar menos espaço -- geralmente as frequências mais agudas, que "
            "são as primeiras a serem cortadas.",
        ),
        (
            "O que é a \u201cfrequência de corte\u201d e o \u201cNyquist esperado\u201d?",
            "Todo áudio digital tem um limite teórico de frequência que consegue "
            "representar: a frequência de Nyquist, que é metade da taxa de "
            "amostragem (sample rate). Um arquivo de 48kHz, por exemplo, pode "
            "conter som até 24kHz. A \u201cfrequência de corte\u201d é onde o som DE "
            "VERDADE para de existir no arquivo -- se for bem menor que o "
            "Nyquist esperado, é sinal de que o conteúdo original já tinha "
            "menos informação do que o arquivo promete ter.",
        ),
        (
            "Por que alguém faria upsample e disfarçaria um arquivo?",
            "Converter um MP3 de baixa qualidade pra FLAC não adiciona nenhuma "
            "informação nova -- só reempacota o mesmo som (incluindo os cortes "
            "e perdas que já existiam) num formato que parece Hi-Res. Isso é "
            "feito às vezes pra vender ou distribuir arquivos como se fossem de "
            "qualidade maior do que realmente são.",
        ),
        (
            "Como esse teste funciona, exatamente?",
            "O inspetor decodifica pedaços do áudio, roda uma FFT (Transformada "
            "Rápida de Fourier) em cada um, e mede a energia média em cada "
            "frequência. Depois procura o ponto onde essa energia cai "
            "abruptamente pro nível de ruído de fundo -- esse é o \u201ccorte\u201d. "
            "É uma HEURÍSTICA, não uma prova matemática: masterizações antigas "
            "ou gravações com corte natural podem dar falso positivo, e um "
            "upsample de bitrate muito alto pode passar despercebido.",
        ),
    )

    # -- Elementos SVG: grade, hachura, curva, linhas de referência -------
    partes_svg = []

    px_corte = _x_px(corte_hz)
    if genuinidade["proporcao"] < 0.98:
        partes_svg.append(
            f'<rect x="{px_corte:.1f}" y="{y0}" width="{x1 - px_corte:.1f}" '
            f'height="{y1 - y0}" fill="url(#hachura)" '
            f'stroke="var(--cor-destaque)" stroke-width="1"/>'
        )

    passo_khz = 2000
    hz_atual = 0
    while hz_atual <= nyquist_hz:
        px = _x_px(hz_atual)
        partes_svg.append(
            f'<line x1="{px:.1f}" y1="{y0}" x2="{px:.1f}" y2="{y1}" '
            f'stroke="var(--cor-grade)" stroke-width="1" stroke-dasharray="1,5"/>'
        )
        partes_svg.append(
            f'<text x="{px - 16:.1f}" y="{y1 + 22}" class="rotulo-pequeno">'
            f"{hz_atual / 1000:.0f}kHz</text>"
        )
        hz_atual += passo_khz

    partes_svg.append(
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" '
        f'fill="none" stroke="var(--cor-secundario)" stroke-width="1"/>'
    )
    partes_svg.append(
        f'<text x="8" y="{y0 - 4}" class="rotulo-pequeno">MAIS SOM</text>'
    )
    partes_svg.append(
        f'<text x="8" y="{y1 + 16}" class="rotulo-pequeno">MENOS SOM</text>'
    )

    if 0 < 20000 < nyquist_hz * 1.02:
        px_20k = _x_px(20000)
        partes_svg.append(
            f'<line x1="{px_20k:.1f}" y1="{y0}" x2="{px_20k:.1f}" y2="{y1}" '
            f'stroke="var(--cor-secundario)" stroke-width="1" stroke-dasharray="2,4"/>'
        )
        partes_svg.append(
            f'<text x="{px_20k - 90:.1f}" y="{y0 - 10}" class="rotulo-pequeno">'
            f"LIMITE DA AUDIÇÃO HUMANA</text>"
        )

    pontos = " ".join(
        f"{_x_px(f):.1f},{_y_px(db):.1f}"
        for f, db in zip(freqs, media_db)
        if f <= nyquist_hz
    )
    partes_svg.append(
        f'<polyline points="{pontos}" fill="none" stroke="var(--cor-destaque)" '
        f'stroke-width="2.5" stroke-linejoin="round"/>'
    )

    partes_svg.append(
        f'<line x1="{px_corte:.1f}" y1="{y0}" x2="{px_corte:.1f}" y2="{y1}" '
        f'stroke="var(--cor-texto)" stroke-width="1.5" stroke-dasharray="4,5"/>'
    )
    idx_corte = min(range(len(freqs)), key=lambda i: abs(freqs[i] - corte_hz))
    y_curva_no_corte = _y_px(media_db[idx_corte])
    label_y = max(y0 + 30, y_curva_no_corte - 55)
    # Estimativa simples de largura de texto (monoespaçado, ~8.4px por
    # caractere em 14px de fonte) -- só pra decidir de que lado da linha
    # o rótulo cabe sem vazar da imagem, igual fazíamos com
    # multiline_textbbox no Pillow.
    texto_corte_linha1 = "AQUI O SOM PARA"
    largura_estimada = len(texto_corte_linha1) * 8.4
    if px_corte + 14 + largura_estimada > x1:
        label_x = px_corte - 14 - largura_estimada
        ancora = "start"
    else:
        label_x = px_corte + 14
        ancora = "start"
    partes_svg.append(
        f'<text x="{label_x:.1f}" y="{label_y:.1f}" text-anchor="{ancora}" '
        f'class="rotulo-corte">AQUI O SOM PARA'
        f'<tspan x="{label_x:.1f}" dy="18">({corte_khz:.1f}kHz)</tspan></text>'
    )
    origem_x = label_x + largura_estimada / 2
    partes_svg.append(
        f'<line x1="{origem_x:.1f}" y1="{label_y + 26:.1f}" x2="{px_corte:.1f}" '
        f'y2="{y_curva_no_corte:.1f}" stroke="var(--cor-texto)" stroke-width="1"/>'
    )
    partes_svg.append(
        f'<polygon points="{px_corte:.1f},{y_curva_no_corte:.1f} '
        f"{px_corte - 4:.1f},{y_curva_no_corte - 8:.1f} "
        f'{px_corte + 4:.1f},{y_curva_no_corte - 8:.1f}" fill="var(--cor-texto)"/>'
    )

    def _css(d):
        """Generate CSS for cover art pixel display."""
        return ";".join(f"{k}:{v}" for k, v in d.items())

    html_doc = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(nome_faixa)} -- Inspetor de Áudio</title>
<style>
  :root {{ {_css(_CSS_VARS_ESCURO)} }}
  @media (prefers-color-scheme: light) {{
    :root {{ {_css(_CSS_VARS_CLARO)} }}
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px 16px 40px;
    background: var(--cor-fundo); color: var(--cor-texto);
    font-family: "Menlo", "SF Mono", "Consolas", "DejaVu Sans Mono", ui-monospace, monospace;
  }}
  .container {{ max-width: 1200px; margin: 0 auto; }}
  h1 {{
    font-size: 1.6rem; margin: 0 0 4px; color: var(--cor-destaque);
    text-transform: none;
  }}
  .veredito-suspeito h1 {{ color: var(--cor-destaque); }}
  .veredito-genuino h1 {{ color: var(--cor-texto); }}
  .veredito-inconclusivo h1 {{ color: var(--cor-secundario); }}
  .subtitulo {{ color: var(--cor-secundario); font-size: 0.85rem; margin: 0 0 14px; }}
  .regua {{ border: none; border-top: 2px solid var(--cor-destaque); margin: 0 0 14px; }}
  .intro {{ color: var(--cor-secundario); font-size: 0.85rem; line-height: 1.5; margin: 0 0 18px; }}
  svg {{ width: 100%; height: auto; display: block; }}
  .rotulo-pequeno {{ fill: var(--cor-secundario); font-size: 12px; }}
  .rotulo-corte {{ fill: var(--cor-texto); font-size: 14px; font-weight: bold; }}
  .painel {{
    border: 1px solid var(--cor-grade); background: var(--cor-painel-fundo);
    border-radius: 4px; padding: 12px 16px; margin: 20px 0; font-size: 0.85rem;
    line-height: 1.7;
  }}
  .painel p:first-child {{ color: var(--cor-texto); margin: 0; }}
  .painel p {{ color: var(--cor-secundario); margin: 0; }}
  .rodape {{ display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }}
  .saiba-mais {{ margin: 20px 0; }}
  .saiba-mais summary {{
    cursor: pointer; font-size: 0.9rem; font-weight: bold; color: var(--cor-texto);
    padding: 4px 0;
  }}
  .saiba-mais summary:hover {{ color: var(--cor-destaque); }}
  details {{
    border: 1px solid var(--cor-grade); border-radius: 4px; padding: 10px 16px;
    margin-bottom: 8px; background: var(--cor-painel-fundo);
  }}
  details > summary {{ list-style: none; }}
  details > summary::-webkit-details-marker {{ display: none; }}
  details > summary::before {{ content: "+ "; color: var(--cor-destaque); }}
  details[open] > summary::before {{ content: "- "; }}
  details p {{
    color: var(--cor-secundario); font-size: 0.85rem; line-height: 1.6;
    margin: 10px 0 0;
  }}
  .badge {{
    display: inline-block; padding: 8px 18px; font-weight: bold; font-size: 1rem;
    border-radius: 3px; border: 1px solid var(--cor-secundario);
  }}
  .badge-suspeito {{ background: var(--cor-destaque); color: var(--cor-fundo); border-color: var(--cor-destaque); }}
  .badge-genuino {{ background: transparent; color: var(--cor-texto); border-color: var(--cor-texto); }}
  .badge-inconclusivo {{ background: transparent; color: var(--cor-secundario); }}
  .proporcao {{ color: var(--cor-secundario); font-size: 0.85rem; }}
  .marca {{ margin-top: 30px; color: var(--cor-grade); font-size: 0.7rem; text-align: right; }}
</style>
</head>
<body class="veredito-{veredito}">
<div class="container">
  <h1>{_esc(manchete)}</h1>
  <p class="subtitulo">{_esc(nome_faixa.upper())}</p>
  <hr class="regua">
  <p class="intro">
    Este gráfico mostra o quanto de som existe em cada frequência -- graves à
    esquerda, agudos à direita. Uma faixa saudável tem som até perto do
    limite da direita, sem um degrau abrupto no meio do caminho.
  </p>
  <svg viewBox="0 0 {largura} {y1 + 40}" xmlns="http://www.w3.org/2000/svg">
    <defs>
      <pattern id="hachura" width="11" height="11" patternTransform="rotate(45)"
               patternUnits="userSpaceOnUse">
        <line x1="0" y1="0" x2="0" y2="11" stroke="var(--cor-hachura)" stroke-width="4"/>
      </pattern>
    </defs>
    {"".join(partes_svg)}
  </svg>
  <div class="painel">
    {"".join(f"<p>{_esc(linha)}</p>" for linha in explicacao)}
  </div>
  <div class="saiba-mais">
    {
        "".join(
            f"<details><summary>{_esc(titulo)}</summary><p>{_esc(corpo)}</p></details>"
            for titulo, corpo in saiba_mais
        )
    }
  </div>
  <div class="rodape">
    <span class="badge badge-{veredito}">{veredito_txt}</span>
    <span class="proporcao">{
        genuinidade["proporcao"] * 100:.0f}% da banda com som de verdade</span>
  </div>
  <p class="marca">QOBUZ-DL INSPECTOR</p>
</div>
</body>
</html>
"""

    destino = os.path.join(os.path.dirname(caminho_audio), f"{nome_faixa}-spec.html")
    with open(destino, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return destino


def _mostrar_relatorio(caminho, dados, genuinidade):
    """Display full tag report for audio file."""
    ui.banner("🔍 INSPETOR DE ÁUDIO")
    ui.section("📄 ARQUIVO")
    for chave, valor in dados["tecnico"].items():
        ui.kv(chave, valor)

    ui.blank()
    ui.section(f"🏷️  TAGS ({len(dados['tags'])} campos)")
    if dados["tags"]:
        for chave, valor in dados["tags"].items():
            ui.kv(str(chave), valor)
    else:
        ui.detail("(nenhuma tag encontrada no arquivo)")

    if dados["capas"]:
        ui.blank()
        ui.section(f"🖼️  CAPAS EMBUTIDAS ({len(dados['capas'])})")
        for capa in dados["capas"]:
            ui.detail(capa)

    ui.blank()
    ui.section("🔬 AUTENTICIDADE (heurística espectral)")
    if not genuinidade["disponivel"]:
        ui.warn(genuinidade["motivo"])
    else:
        # Aplica a cor correta baseada no dicionário de genuinidade (verde/amarelo/vermelho)
        cor_fn = {"ok": ui.ok, "warn": ui.warn, "error": ui.error}[genuinidade["cor"]]
        cor_fn(genuinidade["mensagem"])

        ui.kv("Corte detectado", f"{genuinidade['corte_hz'] / 1000:.2f} kHz")
        ui.kv("Nyquist esperado", f"{genuinidade['nyquist_hz'] / 1000:.2f} kHz")
        ui.kv("Banda utilizada", f"{genuinidade['proporcao'] * 100:.0f}%")

        ui.blank()
        ui.detail("Gerando gráfico de resposta em frequência (HTML)...")

        try:
            destino = _gerar_grafico_html(caminho, genuinidade)
            if destino:
                # Confere que realmente caiu na mesma pasta do áudio,
                # não em outro lugar -- garantia explícita, não só
                # confiança no que a função devolveu.
                assert os.path.dirname(destino) == os.path.dirname(caminho)
                ui.ok(f"  Gráfico salvo em '{destino}' (abra no navegador)")
            else:
                ui.warn(
                    "Não havia dados de espectro suficientes pra desenhar o gráfico."
                )
        except Exception as e:
            ui.error(f"Erro ao gerar o gráfico: {e}")


async def run_inspector(diretorio_inicial=None):
    """
    Ponto de entrada do `qobuz-dl inspect`: abre o navegador de arquivos,
    extrai todas as tags do arquivo escolhido e roda a checagem de
    autenticidade espectral, mostrando tudo via ui.py.
    """
    # Se nenhum diretório foi informado, detecta a pasta padrão de música
    # de acordo com o dispositivo/SO (ver _detectar_pasta_padrao acima).
    if not diretorio_inicial:
        diretorio_inicial = _detectar_pasta_padrao()

    if not os.path.isdir(diretorio_inicial):
        diretorio_inicial = os.getcwd()

    caminho = await _navegar_arquivos(diretorio_inicial)
    if not caminho:
        ui.skip("Nenhum arquivo escolhido.")
        return 0

    ui.step(f"Lendo tags de {os.path.basename(caminho)}...")
    try:
        dados = _extrair_tudo(caminho)
    except Exception as e:
        ui.error(f"Não foi possível ler esse arquivo: {e}")
        return 1

    ui.step("Analisando o espectro de áudio (pode levar alguns segundos)...")
    genuinidade = _checar_genuinidade(
        caminho, dados.pop("_sample_rate", None), dados.pop("_duracao_s", None)
    )

    _mostrar_relatorio(caminho, dados, genuinidade)
    return 0
