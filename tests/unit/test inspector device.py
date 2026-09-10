"""Testa `_detectar_pasta_padrao()` (qobuz_dl/inspector.py): decide de onde
o navegador de arquivos do comando `inspect` abre quando a pessoa NÃO digita
um caminho, olhando pro tipo de dispositivo/SO -- em vez de assumir sempre o
diretório atual (ou, pior, uma pasta de downloads relativa que podia nem
existir -- ver o bug de integração corrigido em commands.py/cli.py, onde o
argparse preenchia o argumento antes desta função ter a chance de rodar).

Nenhum destes testes toca o sistema de arquivos de verdade: `os.path.isdir`
é substituído por um fake que só reconhece as pastas explicitamente
"criadas" em cada teste. Isso evita depender da estrutura real do
dispositivo que roda a suíte (nunca faz sentido, por exemplo, criar de
verdade "/storage/emulated/0/Music" só para testar) e deixa a ordem de
prioridade das etapas 100% determinística.
"""

import os

import pytest

from qobuz_dl import inspector

pytestmark = pytest.mark.unit


@pytest.fixture
def ambiente_limpo(monkeypatch):
    """Remove todas as variáveis que a função consulta, pra cada teste
    começar de um estado limpo e previsível (sem herdar nada do ambiente
    de quem está rodando a suíte)."""
    for var in ("QOBUZ_DL_IOS_HOME", "HOME", "ANDROID_ROOT", "ANDROID_DATA"):
        monkeypatch.delenv(var, raising=False)


def _apenas_estas_pastas_existem(monkeypatch, *pastas_existentes):
    """Substitui os.path.isdir por um fake que só diz \"sim\" pras pastas
    passadas aqui -- ver docstring do módulo sobre por que isso é melhor
    que criar diretórios reais."""
    normalizadas = set(pastas_existentes)
    monkeypatch.setattr(
        inspector.os.path, "isdir", lambda caminho: caminho in normalizadas
    )


class TestOverrideManual:
    """QOBUZ_DL_IOS_HOME é a mesma convenção de override já usada em
    utils.get_config_paths() -- tem prioridade sobre tudo, MAS só se a
    pasta apontada realmente existir."""

    def test_vence_se_a_pasta_existir(self, ambiente_limpo, monkeypatch):
        monkeypatch.setenv("QOBUZ_DL_IOS_HOME", "/caminho/escolhido")
        _apenas_estas_pastas_existem(monkeypatch, "/caminho/escolhido")

        assert inspector._detectar_pasta_padrao() == "/caminho/escolhido"

    def test_e_ignorado_se_a_pasta_nao_existir(self, ambiente_limpo, monkeypatch):
        # A variável está definida, mas aponta pra algo que não existe --
        # a função tem que pular pro próximo candidato, não travar nem
        # devolver um caminho fantasma que não existe de verdade.
        monkeypatch.setenv("QOBUZ_DL_IOS_HOME", "/nao/existe")
        monkeypatch.setenv("HOME", "/home/usuario")
        _apenas_estas_pastas_existem(monkeypatch, "/home/usuario")

        assert inspector._detectar_pasta_padrao() == "/home/usuario"


class TestDeteccaoPorDispositivo:
    def test_ios_a_shell_usa_pasta_documents(self, ambiente_limpo, monkeypatch):
        home = "/private/var/mobile/Containers/Data/Application/ABC-123"
        monkeypatch.setenv("HOME", home)
        # IMPORTANTE: a função monta o candidato com os.path.join(home,
        # "Documents") -- no Windows isso produz separador "\\", não "/".
        # Construir aqui com f"{home}/Documents" (string literal fixa)
        # funciona por acaso no Linux/macOS mas quebra no Windows, porque
        # o candidato real nunca bate com essa string. Usar os.path.join
        # nos dois lados garante que o teste compara "a mesma forma de
        # caminho" que o código de produção realmente constrói, em
        # qualquer SO.
        esperado = os.path.join(home, "Documents")
        _apenas_estas_pastas_existem(monkeypatch, esperado)

        assert inspector._detectar_pasta_padrao() == esperado

    def test_ios_sem_documents_nao_trava_e_cai_pro_home(
        self, ambiente_limpo, monkeypatch
    ):
        home = "/private/var/mobile/Containers/Data/Application/ABC-123"
        monkeypatch.setenv("HOME", home)
        _apenas_estas_pastas_existem(monkeypatch, home)  # só o HOME em si existe

        assert inspector._detectar_pasta_padrao() == home

    def test_android_termux_usa_storage_emulated(self, ambiente_limpo, monkeypatch):
        monkeypatch.setenv("HOME", "/data/data/com.termux/files/home")
        _apenas_estas_pastas_existem(monkeypatch, "/storage/emulated/0/Music")

        assert inspector._detectar_pasta_padrao() == "/storage/emulated/0/Music"

    def test_android_detectado_via_variavel_de_ambiente_sem_termux(
        self, ambiente_limpo, monkeypatch
    ):
        # Nem todo Android roda Termux -- ANDROID_ROOT/ANDROID_DATA é o
        # sinal alternativo, independente do conteúdo de HOME.
        monkeypatch.setenv("HOME", "/qualquer/coisa")
        monkeypatch.setenv("ANDROID_ROOT", "/system")
        _apenas_estas_pastas_existem(monkeypatch, "/sdcard/Music")

        assert inspector._detectar_pasta_padrao() == "/sdcard/Music"

    def test_desktop_usa_pasta_music(self, ambiente_limpo, monkeypatch):
        home = "/home/usuario"
        monkeypatch.setenv("HOME", home)
        # Mesmo motivo do teste de iOS acima: o candidato real é
        # os.path.join(home, "Music"), que no Windows vem com "\\" em vez
        # de "/". Construir o esperado do mesmo jeito evita que o teste
        # dependa do separador de caminho do SO que roda a suíte.
        esperado = os.path.join(home, "Music")
        _apenas_estas_pastas_existem(monkeypatch, esperado)

        assert inspector._detectar_pasta_padrao() == esperado

    def test_desktop_aceita_musica_em_portugues(self, ambiente_limpo, monkeypatch):
        home = "/home/usuario"
        monkeypatch.setenv("HOME", home)
        esperado = os.path.join(home, "Música")
        _apenas_estas_pastas_existem(monkeypatch, esperado)

        assert inspector._detectar_pasta_padrao() == esperado

    def test_prioriza_music_em_ingles_quando_as_duas_existem(
        self, ambiente_limpo, monkeypatch
    ):
        home = "/home/usuario"
        monkeypatch.setenv("HOME", home)
        pasta_music = os.path.join(home, "Music")
        pasta_musica = os.path.join(home, "Música")
        _apenas_estas_pastas_existem(monkeypatch, pasta_music, pasta_musica)

        assert inspector._detectar_pasta_padrao() == pasta_music


class TestFallbackFinal:
    def test_nada_existe_cai_pro_home(self, ambiente_limpo, monkeypatch):
        monkeypatch.setenv("HOME", "/home/usuario")
        _apenas_estas_pastas_existem(monkeypatch, "/home/usuario")

        assert inspector._detectar_pasta_padrao() == "/home/usuario"

    def test_home_tambem_nao_existe_cai_pro_diretorio_atual(
        self, ambiente_limpo, monkeypatch
    ):
        monkeypatch.setenv("HOME", "/home/fantasma")
        _apenas_estas_pastas_existem(monkeypatch)  # nenhuma pasta existe

        assert inspector._detectar_pasta_padrao() == os.getcwd()
