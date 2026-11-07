"""docs/COMANDOS.md é gerado de commands.py: tem de estar em dia e ser estável."""

import importlib.util
import os

import pytest

from qobuz_dl.commands import CODIGOS_DE_SAIDA, SAIDA_POR_COMANDO, qobuz_dl_args

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GERADOR = os.path.join(RAIZ, "tools", "gerar_referencia.py")
DOC = os.path.join(RAIZ, "docs", "COMANDOS.md")

pytestmark = pytest.mark.skipif(
    not os.path.isfile(GERADOR), reason="tools/ não faz parte desta instalação"
)


@pytest.fixture(scope="module")
def gerador():
    spec = importlib.util.spec_from_file_location("gerar_referencia", GERADOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture(scope="module")
def texto(gerador):
    return gerador.gerar()


def test_arquivo_commitado_esta_em_dia(texto):
    with open(DOC, encoding="utf-8") as f:
        assert f.read() == texto, (
            "docs/COMANDOS.md está desatualizado: rode "
            "`python tools/gerar_referencia.py` e inclua o arquivo no commit."
        )


def test_geracao_e_estavel(gerador, texto):
    assert gerador.gerar() == texto


def test_todo_subcomando_tem_secao(texto):
    parser = qobuz_dl_args()
    acao = next(a for a in parser._actions if hasattr(a, "_choices_actions"))
    for escolha in acao._choices_actions:
        assert f"\n## {escolha.dest}\n" in texto


def test_apelidos_aparecem(texto):
    assert "`cfg`" in texto and "`dups`" in texto and "`clean`" in texto


def test_codigos_de_saida_documentados(texto):
    for codigo, _significado in CODIGOS_DE_SAIDA:
        assert f"| `{codigo}` |" in texto
    for comando in SAIDA_POR_COMANDO:
        assert f"\n## {comando}\n" in texto


def test_saida_por_comando_so_cita_comandos_que_existem():
    parser = qobuz_dl_args()
    acao = next(a for a in parser._actions if hasattr(a, "_choices_actions"))
    nomes = {e.dest for e in acao._choices_actions}
    assert set(SAIDA_POR_COMANDO) <= nomes


def test_sem_caminho_da_maquina_no_texto(texto):
    assert "/home/" not in texto and "/tmp/" not in texto and "/Users/" not in texto
