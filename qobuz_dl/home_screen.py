# ============================================================================
# home_screen.py
# Tela inicial do qobuz-dl-ultra (estilo "tiddl": painéis arredondados) que
# mostra, para CADA comando, o que ele faz, como usar e um exemplo pronto.
#
# - Os comandos e as flags globais vêm do parser real (commands.py): um
#   comando novo aparece sozinho. O que é escrito à mão aqui é só o USO e o
#   EXEMPLO de cada comando (dicionário EXEMPLOS); sem entrada, usa o `usage`
#   do próprio subparser.
# - Linhas horizontais separam cada comando/passo/flag, e há uma linha em
#   branco entre os painéis, para nada ficar colado.
# - Adapta-se à largura do terminal (a mesma do resto do programa):
#     >= 96 colunas : tabela de 3 colunas (comando | o que faz | como usar)
#     <  96 colunas : cartões empilhados (celular, tablet, janela estreita)
#     >  110 colunas: painéis limitados a 110 e centralizados
# Sem dependências externas (só ANSI + caixas Unicode).
# ============================================================================

import re
import shutil
import unicodedata

PROG = "qobuz-dl"
LARGURA_MAX = 110
LARGURA_MIN = 24
LARGURA_TABELA = 96

# nome -> (uso, exemplo). O prefixo "qobuz-dl " é acrescentado na tela.
EXEMPLOS = {
    "dl": (
        "dl [opções] <URL ou arquivo.txt>",
        'dl "https://play.qobuz.com/album/..." -q 27',
    ),
    "interactive": ("interactive [-l N] [-sd]", "interactive -l 30 -sd"),
    "lucky": (
        "lucky [-t TIPO] [-n N] <busca>",
        'lucky -t album -n 1 "Daft Punk Discovery"',
    ),
    "lyrics": ("lyrics [DIR]", "lyrics ~/Music"),
    "tags": ("tags [--tags-only | --covers-only] [DIR]", "tags --tags-only ~/Music"),
    "sync-playlist": (
        "sync-playlist [-y] [-d PASTA] <URL>",
        'sp "https://play.qobuz.com/playlist/12345" -d ~/Music/Mix',
    ),
    "sync-favorites": (
        "sync-favorites [--dry-run] [--download-new] [--limit N]",
        "sf --dry-run",
    ),
    "scan": ("scan [opções] [DIR]", "scan ~/Music --dry-run"),
    "library": ("library [ação] [opções]", "library missing --limit 20"),
    "doctor": ("doctor [--json]", "doctor"),
    "import-playlist": (
        "import-playlist [-n NOME] [--auto] <URL ou arquivo>",
        'ip lista.csv -n "Meu Mix" --auto',
    ),
    "stats": ("stats [--artistas]", "stats --artistas"),
    "inspect": ("inspect [caminho]", "inspect ~/Music"),
    "auth": ("auth", "auth"),
    "user": ("user [--json]", "user"),
}

INICIO_RAPIDO = [
    ("1", f"{PROG} -r", "configura conta, pasta e qualidade (só na primeira vez)"),
    ("2", f'{PROG} dl "<URL>"', "baixa um álbum, faixa, artista ou playlist"),
    ("3", f"{PROG} interactive", "ou procure pelo menu, sem copiar URL"),
]

DICAS = [
    (f"{PROG} <comando> -h", "ajuda completa de um comando e de todas as suas opções"),
    (f"{PROG} doctor", "se algo der errado, diagnostica config, banco e ambiente"),
]

SEP = None  # marcador de linha horizontal dentro de um painel

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _largura_visivel(texto: str) -> int:
    texto = _ANSI.sub("", texto)
    total = 0
    for ch in texto:
        if unicodedata.combining(ch):
            continue
        total += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return total


def _cortar(texto: str, largura: int) -> str:
    if _largura_visivel(texto) <= largura:
        return texto
    saida, usado = "", 0
    for ch in texto:
        w = _largura_visivel(ch)
        if usado + w > largura - 1:
            break
        saida, usado = saida + ch, usado + w
    return saida + "…"


def _quebrar(texto: str, largura: int) -> list:
    """Quebra por palavras; palavras maiores que a linha são partidas."""
    largura = max(1, largura)
    palavras = []
    for p in texto.split():
        while _largura_visivel(p) > largura:
            palavras.append(p[:largura])
            p = p[largura:]
        palavras.append(p)
    linhas, atual = [], ""
    for palavra in palavras:
        teste = f"{atual} {palavra}".strip()
        if _largura_visivel(teste) <= largura:
            atual = teste
        else:
            if atual:
                linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas or [""]


class _Estilo:
    def __init__(self, cor, destaque, mudo, reset, negrito):
        self.cor = cor
        self.d = destaque if cor else ""
        self.m = mudo if cor else ""
        self.b = negrito if cor else ""
        self.r = reset if cor else ""

    def _f(self, ini, t):
        return f"{ini}{t}{self.r}" if ini else t

    def dest(self, t):
        return self._f(self.d, t)

    def mudo(self, t):
        return self._f(self.m, t)

    def neg(self, t):
        return self._f(self.b, t)


def _com_separadores(blocos: list) -> list:
    """Junta blocos de linhas colocando uma linha horizontal entre eles."""
    saida = []
    for i, bloco in enumerate(blocos):
        if i:
            saida.append(SEP)
        saida += bloco
    return saida


def _painel(titulo: str, linhas: list, largura: int, st: _Estilo) -> list:
    """Caixa arredondada; um item None em `linhas` vira linha horizontal."""
    interno = largura - 4
    topo_txt = f" {_cortar(titulo, max(1, largura - 6))} "
    falta = max(0, largura - 3 - _largura_visivel(topo_txt))
    topo = st.mudo("╭─") + st.dest(topo_txt) + st.mudo("─" * falta + "╮")
    base = st.mudo("╰" + "─" * (largura - 2) + "╯")
    saida = [topo]
    for linha in linhas:
        if linha is SEP:
            saida.append(st.mudo("├" + "─" * (largura - 2) + "┤"))
            continue
        pad = max(0, interno - _largura_visivel(linha))
        saida.append(st.mudo("│ ") + linha + " " * pad + st.mudo(" │"))
    saida.append(base)
    return saida


def _normalizar(comandos) -> list:
    """Aceita dicts ou tuplas (nome, aliases, descrição[, uso[, exemplo]])."""
    saida = []
    for c in comandos:
        if isinstance(c, dict):
            d = dict(c)
        else:
            c = tuple(c) + (None,) * (5 - len(c))
            d = {
                "nome": c[0],
                "aliases": c[1],
                "desc": c[2],
                "uso": c[3],
                "exemplo": c[4],
            }
        uso_ex = EXEMPLOS.get(d["nome"], (None, None))
        d["uso"] = uso_ex[0] or d.get("uso") or d["nome"]
        d["exemplo"] = d.get("exemplo") or uso_ex[1]
        d["aliases"] = d.get("aliases") or ""
        d["desc"] = d.get("desc") or ""
        saida.append(d)
    return saida


def _painel_comandos_largo(cmds: list, largura: int, st: _Estilo) -> list:
    interno = largura - 4
    w_cmd = max(_largura_visivel(c["nome"]) for c in cmds + [{"nome": "COMANDO"}]) + 1
    w_desc = max(30, (interno - w_cmd) * 40 // 100)
    w_dir = interno - w_cmd - w_desc - 4

    cab = (
        st.neg("COMANDO".ljust(w_cmd))
        + "  "
        + st.neg("O QUE FAZ".ljust(w_desc))
        + "  "
        + st.neg("COMO USAR")
    )
    blocos = []
    for c in cmds:
        esq = _quebrar(c["desc"], w_desc)
        n_desc = len(esq)
        if c["aliases"]:
            esq += _quebrar(f"(alias: {c['aliases']})", w_desc)
        dir_ = [st.mudo(x) for x in _quebrar(f"{PROG} {c['uso']}", w_dir)]
        if c["exemplo"]:
            ex = _quebrar(f"{PROG} {c['exemplo']}", w_dir - 4)
            dir_ += [(st.neg("ex: ") if i == 0 else "    ") + x for i, x in enumerate(ex)]
        bloco = []
        for i in range(max(len(esq), len(dir_))):
            col1 = st.dest(c["nome"].ljust(w_cmd)) if i == 0 else " " * w_cmd
            e = esq[i] if i < len(esq) else ""
            pad = " " * max(0, w_desc - _largura_visivel(e))
            if i >= n_desc:
                e = st.mudo(e)
            col3 = dir_[i] if i < len(dir_) else ""
            bloco.append(f"{col1}  {e}{pad}  {col3}")
        blocos.append(bloco)
    return _painel("Comandos", [cab, SEP] + _com_separadores(blocos), largura, st)


def _painel_comandos_estreito(cmds: list, largura: int, st: _Estilo) -> list:
    interno = largura - 4
    recuo = 2 if interno >= 30 else 1
    pre = 4
    blocos = []
    for c in cmds:
        bloco = [st.dest(_cortar(c["nome"], interno))]
        if c["aliases"]:
            alias = f"({c['aliases']})"
            if _largura_visivel(c["nome"]) + 2 + len(alias) <= interno:
                bloco[0] += st.mudo("  " + alias)
            else:
                bloco += [st.mudo(l) for l in _quebrar(alias, interno)]
        bloco += [" " * recuo + l for l in _quebrar(c["desc"], interno - recuo)]
        for i, l in enumerate(_quebrar(f"{PROG} {c['uso']}", interno - recuo - pre)):
            marca = st.mudo("uso ".ljust(pre)) if i == 0 else " " * pre
            bloco.append(" " * recuo + marca + l)
        if c["exemplo"]:
            ex = _quebrar(f"{PROG} {c['exemplo']}", interno - recuo - pre)
            for i, l in enumerate(ex):
                marca = st.neg("ex  ".ljust(pre)) if i == 0 else " " * pre
                bloco.append(" " * recuo + marca + l)
        blocos.append(bloco)
    return _painel("Comandos", _com_separadores(blocos), largura, st)


def _painel_inicio(largura: int, st: _Estilo) -> list:
    interno = largura - 4
    blocos = []
    for n, cmd, nota in INICIO_RAPIDO:
        bloco = []
        for i, l in enumerate(_quebrar(cmd, interno - 3)):
            bloco.append((st.dest(n) + "  " if i == 0 else "   ") + l)
        bloco += ["   " + st.mudo(l) for l in _quebrar(nota, interno - 3)]
        blocos.append(bloco)
    return _painel("Início rápido", _com_separadores(blocos), largura, st)


def _painel_flags(flags: list, largura: int, st: _Estilo) -> list:
    interno = largura - 4
    w_op = max((_largura_visivel(f) for f, _ in flags), default=0) + 2
    lado_a_lado = w_op <= interno // 2
    if lado_a_lado:
        linhas = []
        for flag, desc in flags:
            for i, p in enumerate(_quebrar(desc, interno - w_op)):
                esq = st.dest(flag.ljust(w_op)) if i == 0 else " " * w_op
                linhas.append(esq + p)
        return _painel("Flags globais", linhas, largura, st)
    blocos = []
    for flag, desc in flags:
        bloco = [st.dest(l) for l in _quebrar(flag, interno)]
        bloco += ["  " + st.mudo(l) for l in _quebrar(desc, interno - 2)]
        blocos.append(bloco)
    return _painel("Flags globais", _com_separadores(blocos), largura, st)


def _painel_dicas(largura: int, st: _Estilo) -> list:
    interno = largura - 4
    blocos = []
    for cmd, nota in DICAS:
        bloco = [st.dest(l) for l in _quebrar(cmd, interno)]
        bloco += ["  " + st.mudo(l) for l in _quebrar(nota, interno - 2)]
        blocos.append(bloco)
    return _painel("Dicas", _com_separadores(blocos), largura, st)


def render_home(comandos, flags=(), largura: int = None, cor: bool = True,
                destaque: str = "\033[36m", mudo: str = "\033[2m",
                reset: str = "\033[0m", negrito: str = "\033[1m") -> str:
    """Devolve os painéis da tela inicial (sem logo) como string.

    comandos: lista de dicts/tuplas (nome, aliases, descrição[, uso[, exemplo]])
    flags:    lista de (flag, descrição)
    largura:  largura do terminal (padrão: detecta); nenhuma linha passa dela.
    """
    if largura is None:
        largura = shutil.get_terminal_size((100, 24)).columns
    largura = max(LARGURA_MIN, largura)
    caixa = min(largura, LARGURA_MAX)
    margem = " " * ((largura - caixa) // 2)
    st = _Estilo(cor, destaque, mudo, reset, negrito)
    cmds = _normalizar(comandos)

    uso = f"{st.neg('Uso:')} {PROG} [FLAGS] COMANDO [ARGUMENTOS]..."
    if _largura_visivel(uso) > caixa - 1:
        uso = f"{st.neg('Uso:')} {PROG} <comando>"
    paineis = [[" " + uso], _painel_inicio(caixa, st)]
    if caixa >= LARGURA_TABELA:
        paineis.append(_painel_comandos_largo(cmds, caixa, st))
    else:
        paineis.append(_painel_comandos_estreito(cmds, caixa, st))
    todas = list(flags) + [("-h, --help", "mostra a ajuda geral (ou de um comando)")]
    paineis.append(_painel_flags(todas, caixa, st))
    paineis.append(_painel_dicas(caixa, st))

    linhas = []
    for i, p in enumerate(paineis):
        if i:
            linhas.append("")
        linhas += p
    return "\n".join(margem + l if l else l for l in linhas)
