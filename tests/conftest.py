"""Configuracao compartilhada da suite de testes.

Ponto importante: o `qobuz_dl` decide caminhos de config e cor no momento do
IMPORT (ver `utils.get_config_paths()` e `color._detect_color_capability()`).
Se a suite rodar contra o config real do usuario, ela pode ler credenciais de
verdade ou -- pior -- sobrescrever o config.ini dele. Por isso o `CONFIG_DIR`
e' redirecionado para um diretorio temporario ANTES de qualquer import do
pacote, e a cor e' desligada para que as asserts comparem texto limpo.

ARMADILHA QUE ISTO EVITA
------------------------
Um CONFIG_DIR temporario VAZIO nao basta. Sem config.ini o programa abre o
assistente de primeira execucao, imprime um formulario e fica esperando
`input()` -- num subprocesso capturado isso da' EOFError. Pior que o erro: um
teste que so' verificasse "imprimiu alguma coisa" passaria, medindo o
assistente em vez da tela que dizia estar medindo. Falso verde.

Por isso a fixture abaixo escreve um config.ini minimo com credenciais
DELIBERADAMENTE invalidas: o programa passa da inicializacao e chega nas telas
reais, mas qualquer tentativa de login falha em `AuthenticationError` -- ou
seja, nenhum teste consegue tocar a API do Qobuz por acidente.
"""

import logging
import os
import sys
import tempfile
from pathlib import Path

# Precisa acontecer antes de importar qobuz_dl: o pacote le' estas variaveis
# no import, nao na primeira chamada.
_TMP = Path(tempfile.mkdtemp(prefix="qdl-testes-"))
os.environ["CONFIG_DIR"] = str(_TMP)
os.environ["NO_COLOR"] = "1"
# A checagem de versao no PyPI e' ao vivo (sem cache): nos testes fica desligada
# para nao tocar a rede nem mudar a tela inicial. Os testes dela religam.
os.environ["QOBUZ_DL_NO_UPDATE_CHECK"] = "1"
# SQLite sem WAL nos testes: evita locks/mmap do -shm (travavam no a-Shell).
os.environ["QOBUZ_DL_SQLITE_WAL"] = "0"
# A fila consulta o nome dos links na API do Qobuz; nos testes, nunca.
os.environ["QOBUZ_DL_FILA_REDE"] = "0"
# O vigia do modo Atalho mata o processo se nada andar; nos testes, nunca.
os.environ["QOBUZ_DL_ATALHO_LIMITE_S"] = "0"
# O anel fino e a batida do Atalho mudam o `progress` enviado; os testes antigos
# contam 1 unidade por faixa e sem batida (os testes novos passam os valores).
os.environ["QOBUZ_DL_ATALHO_ESCALA"] = "1"
os.environ["QOBUZ_DL_ATALHO_BATIDA_S"] = "0"

# O a-Shell define APPDIR e o projeto procura ffmpeg em $APPDIR/bin. Se a
# variavel existir no ambiente de quem roda os testes, os testes que simulam
# "sem ffmpeg" achariam o binario e passariam sem testar nada.
os.environ.pop("APPDIR", None)

# config.ini minimo. As credenciais sao invalidas de proposito -- ver a
# docstring do modulo.
_CONFIG = _TMP / "qobuz-dl" / "config.ini"
_CONFIG.parent.mkdir(parents=True, exist_ok=True)
_CONFIG.write_text(
    "[qobuz]\n"
    "email = testes@invalido.local\n"
    "password = nao-e-uma-senha\n"
    "app_id = 1\n"
    "secrets = a\n"
    "default_limit = 20\n"
    "default_quality = 6\n"
    f"directory = {_TMP / 'downloads'}\n",
    encoding="utf-8",
)

# Guarda contra o prompt de verdade. `PromptSession()` sem entrada/saida
# explicitas abre o terminal real; no a-Shell (iOS) isso derruba o runtime com
# segfault, e em CI o teste ficaria esperando teclado. A troca acontece ANTES
# de importar o pacote, para todo `from prompt_toolkit import PromptSession`
# receber o guarda. Quem passa `input=`/`output=` (teste do `_ask` com pipe) ou
# troca o PromptSession por um falso continua funcionando como antes.
try:
    import prompt_toolkit as _pt

    _PromptSessionReal = _pt.PromptSession

    def _prompt_session_guardado(*args, **kwargs):
        if kwargs.get("input") is None and kwargs.get("output") is None:
            raise AssertionError(
                "PromptSession() abriria o terminal real dentro de um teste "
                "(segfault no a-Shell). Troque-o por um falso com "
                "monkeypatch.setattr(modulo, 'PromptSession', ...) ou passe "
                "input=/output= de pipe."
            )
        return _PromptSessionReal(*args, **kwargs)

    _pt.PromptSession = _prompt_session_guardado  # type: ignore[assignment]
except ImportError:  # sem prompt_toolkit nao ha o que guardar
    pass

# Garante que o pacote do repositorio ganha do que estiver instalado no
# sistema -- senao o teste pode validar uma versao antiga sem avisar.
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

# NOTA: O mock do cryptography foi REMOVIDO. Em builds recentes do a-Shell
# (iOS/iPadOS), o pacote cryptography funciona corretamente porque vem
# pre-compilado. O mock foi criado para contornar builds antigas onde o
# cryptography nao funcionava, mas ele quebra os testes de criptografia real
# (TestDeriveSessionKey e TestUnwrapTrackKey) que dependem do cryptography
# verdadeiro. Se em algum ambiente o cryptography realmente falhar, a solucao
# correta e' usar skips condicionais nesses testes especificos, nao mockar
# o pacote inteiro.

import pytest  # noqa: E402

# Mobile (a-Shell/iOS) e alguns runners tem um ulimit de file descriptors
# bem mais baixo que desktop Linux comum (as vezes bem abaixo de 1024).
try:
    import resource

    _soft, _hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    if _hard != resource.RLIM_INFINITY and _soft < _hard:
        resource.setrlimit(resource.RLIMIT_NOFILE, (_hard, _hard))
    elif _hard == resource.RLIM_INFINITY and _soft < 4096:
        resource.setrlimit(resource.RLIMIT_NOFILE, (4096, _hard))
except (ImportError, ValueError, OSError):
    pass


@pytest.fixture
def dir_config_temp():
    """Diretorio de configuracao isolado desta sessao de testes."""
    return _TMP


@pytest.fixture
def ambiente_isolado():
    """`os.environ` pronto para passar a um subprocesso do programa."""
    return dict(
        os.environ,
        CONFIG_DIR=str(_TMP),
        NO_COLOR="1",
        COLUMNS="80",
    )


@pytest.fixture
def sem_binarios(monkeypatch):
    """Simula um sistema sem ffmpeg e sem fpcalc no PATH."""
    from qobuz_dl import utils

    utils._BINARIOS_CHECADOS.clear()
    monkeypatch.setattr(utils.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(utils, "_DIRS_EXTRA", [])
    yield
    utils._BINARIOS_CHECADOS.clear()


@pytest.fixture(autouse=True)
def _restaura_logging_do_ui():
    """Desfaz o `ui.install_logging()` feito por um teste.

    `install_logging` troca os handlers do logger raiz pelo TqdmLoggingHandler,
    que manda todo warning para `ui.emit_always`. Se o handler vazar para os
    testes seguintes, qualquer `logger.warning` do codigo cai no meio da saida
    que eles capturam (ex.: um aviso antes do JSON). So mexe no que o ui criou,
    sem tocar nos handlers do proprio pytest.
    """
    root = logging.getLogger()
    nivel = root.level
    ruidosos = ("httpx", "httpcore", "urllib3", "asyncio", "PIL")
    niveis = {nome: logging.getLogger(nome).level for nome in ruidosos}
    yield
    ui = sys.modules.get("qobuz_dl.ui")
    if ui is None:  # o teste nem importou o ui: nada a desfazer
        return
    for handler in list(root.handlers):
        if isinstance(handler, ui.TqdmLoggingHandler):
            root.removeHandler(handler)
    root.setLevel(nivel)
    for nome, valor in niveis.items():
        logging.getLogger(nome).setLevel(valor)


@pytest.fixture(autouse=True)
def _estado_do_ui_limpo():
    """Zera `ui.estado` antes de cada teste (cor e unicode são redetectados).

    `ui.estado.unicode_enabled` é detectado uma vez e fica em cache. Um teste que
    o deixe em False (ou que chame o ui com um stdout sem UTF-8) e não restaure faz
    os seguintes receberem bordas ASCII ("+-|") no lugar de "╭│├". Com o
    pytest-randomly a ordem muda a cada execução, então o problema aparecia só em
    algumas rodadas.

    Troca na mão, sem pedir o fixture `monkeypatch` (veja o motivo em
    `_terminal_nao_interativo`). Só age se o teste já importou o ui.
    """
    ui = sys.modules.get("qobuz_dl.ui")
    if ui is not None:
        ui.redefinir_estado()
    yield


@pytest.fixture(autouse=True)
def _terminal_nao_interativo():
    """`library_cmd._interactive()` dá False por padrão nos testes.

    No a-Shell `isatty()` pode ser True mesmo com a saida capturada, e o codigo
    entraria nos menus que perguntam algo. Os testes dos menus ligam de novo com
    `monkeypatch.setattr(lc, "_interactive", lambda: True)`.

    Troca na mao, sem o fixture `monkeypatch`: pedi-lo aqui antecipa o setup do
    `monkeypatch` de todos os testes e muda a ordem de teardown em relacao aos
    fixtures autouse dos modulos (um deles chegou a limpar um cache depois de o
    teste ja ter desfeito o seu patch).
    """
    modulo = sys.modules.get("qobuz_dl.library_cmd")
    if modulo is None:
        yield
        return
    original = modulo._interactive
    modulo._interactive = lambda: False
    try:
        yield
    finally:
        modulo._interactive = original


@pytest.fixture(autouse=True)
def _restaura_estado_do_ui():
    """Devolve `ui.estado` ao que era antes do teste, passe o que passar.

    `ui.unicode_enabled()` e `ui.color_enabled()` sao detectados na PRIMEIRA
    chamada e ficam guardados em `ui.estado`. Se essa primeira chamada cair
    num teste que troca o `sys.stdout` por um `io.StringIO()` (sem encoding),
    o cache vira "sem Unicode" e vaza para o resto da sessao: todo painel
    passa a sair em ASCII (`+- ... -+`) e os testes que esperam `╭─ ... ─╮`
    falham. Como o pytest-randomly muda a ordem a cada execucao, a falha
    aparecia e sumia sem motivo aparente.

    Aqui o estado e' fotografado antes e restaurado depois de cada teste.
    Nao pede o fixture `monkeypatch` (ver a nota do fixture de baixo); o
    teardown roda depois do dos fixtures do proprio teste, entao o que eles
    desfazem ja' voltou ao lugar quando este restaura.
    """
    import dataclasses

    ui = sys.modules.get("qobuz_dl.ui")
    antes = dataclasses.asdict(ui.estado) if ui is not None else None
    yield
    ui = sys.modules.get("qobuz_dl.ui")
    if ui is None:
        return
    if antes is None:
        # O teste foi o primeiro a importar o ui: o estado de antes era o
        # padrao de fabrica.
        ui.redefinir_estado()
        return
    for nome, valor in antes.items():
        setattr(ui.estado, nome, valor)


# --- Terminal limpo + vigia de travamento ------------------------------------
# (adicionado por aplicar_correcao_travamento.py)
import faulthandler  # noqa: E402


def _devolver_terminal_ao_normal() -> None:
    """Escreve ESC[0m no terminal de verdade.

    Alguns testes ligam a cor e imprimem negrito (ESC[1m) sem o reset. Com a
    captura do pytest desligada (-s) ou no a-Shell, isso vai direto ao terminal
    e o negrito fica preso ate o fim. O reset aqui nao afeta a saida capturada.
    """
    for fluxo in (sys.__stdout__, sys.__stderr__):
        try:
            if fluxo is not None and fluxo.isatty():
                fluxo.write("\033[0m")
                fluxo.flush()
        except Exception:  # noqa: BLE001 - nunca derrubar um teste por causa disto
            pass


@pytest.fixture(autouse=True)
def _terminal_sem_negrito_preso():
    yield
    _devolver_terminal_ao_normal()


_ARQ_TRAVAMENTO = None


def _arquivo_de_travamento():
    """Abre (uma vez) travamento.txt na raiz do projeto."""
    global _ARQ_TRAVAMENTO
    if _ARQ_TRAVAMENTO is None:
        _ARQ_TRAVAMENTO = open(  # noqa: SIM115 - fica aberto a sessao toda
            Path(__file__).resolve().parent.parent / "travamento.txt",
            "w",
            encoding="utf-8",
        )
    return _ARQ_TRAVAMENTO


@pytest.fixture(autouse=True)
def _vigia_de_travamento(request):
    """Se um teste passar de QDL_TESTE_LIMITE_S (padrao 30 s), grava a pilha.

    A pilha de TODAS as threads vai para `travamento.txt` (na raiz do projeto),
    com o nome do teste no topo. Nao mata o teste. 0 desliga.
    """
    try:
        limite = float(os.environ.get("QDL_TESTE_LIMITE_S", "30"))
    except ValueError:
        limite = 30.0
    armado = False
    if limite > 0:
        try:
            arq = _arquivo_de_travamento()
            arq.seek(0)
            arq.truncate()
            arq.write(
                f"TESTE: {request.node.nodeid}\n(fase: setup/chamada, "
                f"limite {limite:g}s)\n\n"
            )
            arq.flush()
            faulthandler.dump_traceback_later(limite, repeat=False, file=arq)
            armado = True
        except Exception:  # noqa: BLE001
            armado = False
    yield
    if armado:
        faulthandler.cancel_dump_traceback_later()


def pytest_unconfigure(config):
    global _ARQ_TRAVAMENTO
    if _ARQ_TRAVAMENTO is not None:
        try:
            faulthandler.cancel_dump_traceback_later()
            _ARQ_TRAVAMENTO.close()
        except Exception:  # noqa: BLE001
            pass
        _ARQ_TRAVAMENTO = None
    _devolver_terminal_ao_normal()


# --- Nenhum teste pode encerrar o processo do pytest -------------------------
# (adicionado por aplicar_melhorias_testes_e_readme.py)
@pytest.fixture(autouse=True)
def _sem_saida_forcada():
    """Impede o vigia do modo Atalho de chamar os._exit() dentro da suite.

    `atalho.encerrar()` arma um `threading.Timer` que, depois de 15 s, encerra
    o processo a' forca. Os testes de test_atalho.py passam `{}` como ambiente e
    `limite_do_ambiente({})` devolve 420 s (ignora o QOBUZ_DL_ATALHO_LIMITE_S=0
    do conftest): o Timer fica pendente e dispara em OUTRO teste, matando o
    pytest sem resumo. Aqui `armar_saida` sem `forcar` explicito ganha um
    `forcar` que nao faz nada, e `forcar_saida` (usada pelo Vigia) tambem.
    """
    from qobuz_dl import vigia

    original = vigia.armar_saida

    def armar_saida_segura(codigo=0, graca=vigia.GRACA_SAIDA_S, forcar=None):
        if forcar is None:

            def forcar(_codigo):
                return None

        return original(codigo, graca, forcar)

    # MonkeyPatch proprio: ver o comentario em `_sem_os_system`.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(vigia, "armar_saida", armar_saida_segura)
        mp.setattr(vigia, "forcar_saida", lambda *a, **k: None)
        yield


# --- Nenhum teste roda comando de shell de verdade ---------------------------
# (adicionado por aplicar_correcao_os_system_ashell.py)
@pytest.fixture(autouse=True)
def _sem_os_system():
    """Troca `os.system` por um falso que devolve 0 em todos os testes.

    No a-Shell (sys.platform == "ios") o Atalho e o `_partida` usam
    `os.system("progress ... >/dev/null 2>&1")`, e ali o os.system roda o
    comando DENTRO do processo do pytest, redirecionando e fechando
    stdout/stderr: a captura perde os descritores e tudo falha com
    `OSError: [Errno 9] Bad file descriptor`. O `atalho.preparar()` chama o
    `progress` antes de o teste trocar o executor, entao a troca tem que ser
    global. Quem quiser observar o comando faz monkeypatch por cima (ex.:
    tests/unit/test_artist_review.py).
    """
    # MonkeyPatch proprio (e nao o fixture `monkeypatch`): o compartilhado seria
    # criado ANTES dos autouse dos modulos e desfeito DEPOIS, invertendo a
    # ordem de teardown (quebrava test_http_download_ssl.py).
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(os, "system", lambda comando: 0)
        yield


# --- Marcadores por pasta ----------------------------------------------------
# (adicionado por aplicar_organizacao_config_e_testes.py)
_MARCADOR_POR_PASTA = {
    "unit": "unit",
    "ui": "ui",
    "network": "network",
    "regression": "regression",
    "lyrics": "lyrics",
}


def pytest_collection_modifyitems(config, items):
    """Aplica unit/ui/network/regression/lyrics pela PASTA do teste.

    Antes so' os arquivos com `pytestmark` tinham marcador, entao `-m ui` pegava
    4 dos 11 arquivos de tests/ui. Arquivos `test_lyrics*.py` tambem ganham `lyrics`.
    """
    for item in items:
        caminho = os.path.normpath(str(getattr(item, "path", None) or item.fspath))
        partes = caminho.split(os.sep)
        marcas = {
            _MARCADOR_POR_PASTA[p] for p in partes[:-1] if p in _MARCADOR_POR_PASTA
        }
        if partes[-1].startswith("test_lyrics"):
            marcas.add("lyrics")
        for marca in sorted(marcas):
            if item.get_closest_marker(marca) is None:
                item.add_marker(getattr(pytest.mark, marca))
