"""Testa qobuz_dl/__init__.py -- o __getattr__ de módulo (PEP 562) que
carrega `main`/`Client` sob demanda pra evitar import circular
(qobuz_dl.cli importa de qobuz_dl, e vice-versa).

Nunca tinha teste dedicado: ninguém em toda a suíte fazia
`import qobuz_dl; qobuz_dl.main` ou `qobuz_dl.Client` -- só
`from qobuz_dl.cli import main` direto, que não passa por esse
__getattr__ nenhuma vez.

Os stand-ins em sys.modules evitam importar qobuz_dl/cli.py e
qobuz_dl/qopy.py de verdade (ambos pesados e com checagem de
dependências reais instaladas) -- só interessa aqui se __getattr__
acha o módulo certo e devolve o atributo certo, não o conteúdo real
de main/Client.
"""

import sys
import types

import pytest


@pytest.fixture
def sem_qobuz_dl_no_cache(monkeypatch):
    """Remove qobuz_dl (e cli/qopy) do cache de módulos antes e depois do
    teste -- garante um __getattr__ realmente fresco a cada teste, sem
    herdar de import anterior (o resto da suíte já importou qobuz_dl
    "de verdade" bem antes deste arquivo rodar)."""
    nomes = ("qobuz_dl", "qobuz_dl.cli", "qobuz_dl.qopy")
    originais = {n: sys.modules.get(n) for n in nomes}
    for n in nomes:
        sys.modules.pop(n, None)
    yield
    for n in nomes:
        sys.modules.pop(n, None)
        if originais[n] is not None:
            sys.modules[n] = originais[n]


def test_main_e_carregado_de_qobuz_dl_cli(sem_qobuz_dl_no_cache, monkeypatch):
    fake_cli = types.ModuleType("qobuz_dl.cli")
    fake_cli.main = lambda: "resultado do main"
    monkeypatch.setitem(sys.modules, "qobuz_dl.cli", fake_cli)

    import qobuz_dl

    assert qobuz_dl.main is fake_cli.main


def test_client_e_carregado_de_qobuz_dl_qopy(sem_qobuz_dl_no_cache, monkeypatch):
    fake_qopy = types.ModuleType("qobuz_dl.qopy")

    class ClienteFalso:
        pass

    fake_qopy.Client = ClienteFalso
    monkeypatch.setitem(sys.modules, "qobuz_dl.qopy", fake_qopy)

    import qobuz_dl

    assert qobuz_dl.Client is ClienteFalso


def test_atributo_desconhecido_levanta_attribute_error(sem_qobuz_dl_no_cache):
    import qobuz_dl

    with pytest.raises(AttributeError, match="nao_existe"):
        _ = qobuz_dl.nao_existe
