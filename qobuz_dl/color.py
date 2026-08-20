from colorama import Style, Fore, init

init(autoreset=True)

# STYLE
DF = Style.NORMAL
BG = Style.BRIGHT
RESET = Style.RESET_ALL
OFF = Style.DIM

# --- Cores cruas (mantidas por compatibilidade -- varios arquivos importam
# estas direto) ---
RED = Fore.RED
BLUE = Fore.BLUE
GREEN = Fore.GREEN
YELLOW = Fore.YELLOW
CYAN = "\033[38;2;95;168;211m"
MAGENTA = Fore.MAGENTA

# --- Por que a paleta semantica abaixo existe ---
# Das 8 cores padrao do ANSI, so RED, GREEN e MAGENTA sao escuras o
# suficiente pra aparecer bem em fundo branco E saturadas o suficiente pra
# aparecer bem em fundo preto ao mesmo tempo. As outras sofrem de um jeito
# ou de outro:
#   - YELLOW e CYAN quase soem em fundo branco (sao as duas cores "mais
#     claras" do conjunto padrao) -- e sao justamente as duas mais usadas
#     no projeto historicamente, depois do RED.
#   - BLUE puro fica dificil de ler em fundo preto (vira um "navy" escuro
#     demais contra o preto).
#   - BLACK/WHITE puros so funcionam bem num dos dois modos, nunca nos dois.
#
# As variantes "LIGHT*_EX" do colorama (bit de alta intensidade do ANSI)
# NAO sao todas iguais nesse quesito: LIGHTBLUE_EX, LIGHTMAGENTA_EX,
# LIGHTGREEN_EX e LIGHTRED_EX tendem a ficar mais vivas/saturadas (ajudam
# no fundo preto sem ficarem palidas demais no branco). Ja LIGHTYELLOW_EX
# e LIGHTCYAN_EX pioram o problema do claro (ficam ainda mais palidas,
# quase brancas). Por isso a paleta abaixo usa LIGHTBLUE_EX como
# substituto do CYAN puro pra texto de progresso/info -- e' visivel nos
# dois modos, ao contrario do CYAN.
#
# Pra WARNING mantive YELLOW por convencao (amarelo = aviso e' quase
# universal, e trocar quebraria a leitura intuitiva de anos de mensagens),
# mas com uma ressalva: se no a-Shell modo claro ele ainda ficar fraco na
# pratica, troque WARNING abaixo pra WARNING_SAFE (definido logo depois) --
# e' so' mudar essa uma linha, todo o resto do projeto que usa WARNING
# (em vez de YELLOW cru) acompanha automaticamente.

ERROR = Fore.RED
SUCCESS = Fore.GREEN
HIGHLIGHT = "\033[38;2;95;168;211m"          # nomes de faixa/album em destaque, IDs
INFO = "\033[38;2;95;168;211m"                  # substitui CYAN em texto de progresso/status
PROGRESS = "\033[38;2;95;168;211m"               # alias de INFO, mais claro no contexto de download
WARNING = Fore.YELLOW             # ver ressalva no comentario acima
WARNING_SAFE = Fore.LIGHTRED_EX   # troca de emergencia se YELLOW nao aparecer bem
MUTED = Style.DIM                 # texto secundario -- relativo ao brilho do
# terminal, entao funciona igual nos dois modos
