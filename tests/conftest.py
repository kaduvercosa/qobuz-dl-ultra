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

import os
import sys
import tempfile
from pathlib import Path

# Precisa acontecer antes de importar qobuz_dl: o pacote le' estas variaveis
# no import, nao na primeira chamada.
_TMP = Path(tempfile.mkdtemp(prefix="qdl-testes-"))
os.environ["CONFIG_DIR"] = str(_TMP)
os.environ["NO_COLOR"] = "1"

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