"""Testa a declaracao de dependencias -- a parte do projeto que mais divergiu.

HISTORICO DE PROBLEMAS REAIS QUE ESTE ARQUIVO TRAVA
---------------------------------------------------
1. TRES fontes de verdade concorrentes (pyproject.toml, setup.py,
   requirements.txt) que JA' divergiram: `Pillow>10.0.0` num arquivo e
   `Pillow>=10.0.0` nos outros -- e o primeiro EXCLUI a propria 10.0.0.
2. `beautifulsoup4` declarado como dependencia sem NENHUM uso no codigo.
3. `lyricsgenius` usado em lyrics_engine.py mas declarado em lugar nenhum --
   a busca de letras no Genius nunca funcionava numa instalacao limpa, e
   falhava em silencio (o import estava num try/except).
4. Pacotes COMPILADOS no nucleo (rapidfuzz, Pillow, watchdog, brotli), o que
   torna `pip install qobuz-dl-ultra` impossivel no a-Shell do iPad, que so'
   instala pacotes Python puros.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

RAIZ = Path(__file__).resolve().parent.parent
PYPROJECT = RAIZ / "pyproject.toml"
PACOTE = RAIZ / "qobuz_dl"


@pytest.fixture(scope="module")
def meta():
    with PYPROJECT.open("rb") as f:
        return tomllib.load(f)["project"]


class TestFonteDeVerdadeUnica:
    def test_setup_py_nao_duplica_dependencias(self):
        """O setup.py virou shim. Se alguem reintroduzir `install_requires`
        ali, as duas listas voltam a poder divergir em silencio."""
        texto = (RAIZ / "setup.py").read_text(encoding="utf-8")
        codigo = "\n".join(
            linha
            for linha in texto.splitlines()
            if not linha.strip().startswith("#")
        )
        # Ignora a docstring, que MENCIONA install_requires ao explicar o
        # historico -- so' o codigo executavel importa aqui.
        codigo = re.sub(r'""".*?"""', "", codigo, flags=re.DOTALL)
        assert "install_requires" not in codigo
        assert "python_requires" not in codigo

    def test_requirements_txt_em_sincronia_com_pyproject(self):
        """Roda o proprio verificador do projeto. Falha aqui = requirements.txt
        editado a mao; rode `python tools/gerar_requirements.py`."""
        r = subprocess.run(
            [sys.executable, str(RAIZ / "tools" / "gerar_requirements.py"), "--check"],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 0, r.stderr


class TestNucleoInstalavelEmAmbienteRestrito:
    """O a-Shell (iOS/iPadOS) so' instala pacotes Python PUROS via pip.

    Cada pacote compilado no nucleo torna o programa impossivel de instalar
    la'. O unico aceito e' o `cryptography` (AES/HKDF sao insubstituiveis e o
    a-Shell o embute pre-compilado).
    """

    COMPILADOS = {"rapidfuzz", "pillow", "watchdog", "brotli", "pyacoustid"}
    EXCECAO_JUSTIFICADA = {"cryptography"}

    def test_sem_pacote_compilado_desnecessario_no_nucleo(self, meta):
        nomes = {re.split(r"[<>=!\[; ]", d)[0].strip().lower()
                 for d in meta["dependencies"]}
        infratores = (nomes & self.COMPILADOS) - self.EXCECAO_JUSTIFICADA
        assert not infratores, (
            f"pacote compilado voltou pro nucleo: {sorted(infratores)}. "
            "Isso quebra `pip install` no a-Shell -- mova pra um extra."
        )

    def test_cryptography_sem_piso_de_versao(self, meta):
        """DE PROPOSITO. O a-Shell embute o cryptography numa versao antiga e
        forcar upgrade la' instala um wheel que falha no dlopen
        (github.com/holzschu/a-shell/issues/797). Sem piso, o pip aceita o que
        ja' esta instalado."""
        decl = [d for d in meta["dependencies"] if d.lower().startswith("cryptography")]
        assert decl == ["cryptography"], (
            f"cryptography ganhou um especificador de versao: {decl}. "
            "Ver o comentario no pyproject.toml antes de mudar isso."
        )


class TestExtras:
    def test_extras_esperados_existem(self, meta):
        extras = meta["optional-dependencies"]
        for nome in ("speed", "covers", "watch", "duplicates", "genius", "all", "dev"):
            assert nome in extras, f"extra '{nome}' desapareceu"

    def test_extra_all_cobre_todos_os_outros(self, meta):
        """Se um extra novo for criado e esquecido no `all`, quem instala
        `[all]` acha que tem tudo e nao tem."""
        extras = meta["optional-dependencies"]
        declarado = extras["all"][0]
        for nome in extras:
            if nome in ("all", "dev"):
                continue
            assert nome in declarado, f"extra '{nome}' faltando no [all]"

    def test_features_opcionais_nao_estao_no_nucleo(self, meta):
        nucleo = {re.split(r"[<>=!\[; ]", d)[0].strip().lower()
                  for d in meta["dependencies"]}
        for pkg in ("pillow", "watchdog", "pyacoustid", "rapidfuzz", "lyricsgenius"):
            assert pkg not in nucleo


class TestSemDependenciaMorta:
    def test_beautifulsoup4_nao_voltou(self, meta):
        """Estava declarado com ZERO usos no codigo -- peso morto no install."""
        todos = list(meta["dependencies"])
        for lista in meta["optional-dependencies"].values():
            todos += lista
        texto = " ".join(todos).lower()
        assert "beautifulsoup" not in texto and "bs4" not in texto

    def test_nenhum_import_de_bs4_no_codigo(self):
        """O contrario do teste acima: se alguem passar a USAR bs4, precisa
        declarar. Os dois testes juntos impedem os dois tipos de divergencia."""
        for py in PACOTE.rglob("*.py"):
            texto = py.read_text(encoding="utf-8")
            assert "import bs4" not in texto, py
            assert "from bs4" not in texto, py


class TestPisoDePython:
    def test_piso_declarado_e_3_10(self, meta):
        """O mutagen 1.48.0 removeu suporte a 3.9. Enquanto isto dizia >=3.9,
        quem instalava em 3.9 recebia silenciosamente mutagen 1.47.0 -- uma
        combinacao nunca testada."""
        assert meta["requires-python"] == ">=3.10"

    def test_classifiers_nao_mentem_sobre_3_9(self, meta):
        assert "Programming Language :: Python :: 3.9" not in meta["classifiers"]

    def test_classifiers_cobrem_o_piso(self, meta):
        assert "Programming Language :: Python :: 3.10" in meta["classifiers"]


class TestImportsDoNucleoSaoDeclarados:
    """Pega o caso `lyricsgenius`: usado no codigo, declarado em lugar nenhum."""

    def test_lyricsgenius_declarado_como_extra(self, meta):
        usa = any(
            "lyricsgenius" in p.read_text(encoding="utf-8") for p in PACOTE.rglob("*.py")
        )
        if not usa:
            pytest.skip("o codigo nao usa mais lyricsgenius")
        todos = " ".join(
            d for lista in meta["optional-dependencies"].values() for d in lista
        )
        assert "lyricsgenius" in todos, (
            "lyrics_engine.py importa lyricsgenius mas ele nao esta declarado "
            "em nenhum extra -- a busca no Genius falharia em silencio."
        )
