"""Testa a camada de similaridade e sua queda para a biblioteca padrao.

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
`cli.py` fazia `from rapidfuzz import process as fuzz_process, fuzz` no TOPO
do arquivo. O rapidfuzz e' um pacote COMPILADO (C++/Cython), e ambientes que
so' aceitam pacotes Python puros -- como o a-Shell no iPad -- nao conseguem
instala-lo. Resultado: o CLI inteiro ficava impossivel de importar. Nem
`qobuz-dl --help` rodava.

`qobuz_dl/fuzzy.py` resolveu isso usando rapidfuzz quando existe e o
`difflib` da biblioteca padrao quando nao existe. O risco NOVO que isso cria
e' os dois motores discordarem -- por exemplo devolverem escalas diferentes
(0-100 vs 0.0-1.0), o que estragaria silenciosamente todos os cortes de
similaridade do projeto. Por isso os testes abaixo rodam a MESMA bateria
forcando cada motor.
"""

import sys

import pytest

from qobuz_dl import fuzzy


def _forcar_difflib(monkeypatch):
    """Faz o modulo agir como se o rapidfuzz nao estivesse instalado."""
    monkeypatch.setattr(fuzzy, "RAPIDFUZZ_DISPONIVEL", False)


@pytest.fixture(params=["motor_disponivel", "difflib_forcado"])
def motor(request, monkeypatch):
    """Roda cada teste nos dois motores, para garantir que sao equivalentes."""
    if request.param == "difflib_forcado":
        _forcar_difflib(monkeypatch)
    return request.param


class TestRatio:
    def test_identico_da_1(self, motor):
        assert fuzzy.ratio("Nujabes", "Nujabes") == pytest.approx(1.0)

    def test_totalmente_diferente_fica_baixo(self, motor):
        assert fuzzy.ratio("Nujabes", "zzzzzzz") < 0.3

    def test_escala_normalizada_em_0_1(self, motor):
        """O rapidfuzz devolve 0-100 e o difflib devolve 0.0-1.0.

        Este e' o teste mais importante do arquivo: se a normalizacao do
        fuzzy.py se perder, todo corte do projeto (`corte=0.6`, o 0.85 de
        qopy.py) passa a comparar com a escala errada e aceita/rejeita tudo.
        """
        v = fuzzy.ratio("Cowboy Bebop OST", "Cowboy Bebop Soundtrack")
        assert 0.0 <= v <= 1.0
        assert v > 0.5  # sao parecidos de verdade

    def test_string_vazia_nao_explode(self, motor):
        assert 0.0 <= fuzzy.ratio("", "") <= 1.0
        assert fuzzy.ratio("", "Nujabes") == pytest.approx(0.0)

    def test_ordem_dos_argumentos_nao_importa(self, motor):
        a = fuzzy.ratio("Metaphorical Music", "Metaphorical Musik")
        b = fuzzy.ratio("Metaphorical Musik", "Metaphorical Music")
        assert a == pytest.approx(b, abs=0.02)


class TestMelhorMatch:
    OPCOES = [
        "track_format",
        "folder_format",
        "playlist_format",
        "multiple_disc_track_format",
    ]

    def test_acha_a_chave_com_erro_de_digitacao(self, motor):
        """Cenario real: usuario escreve `track_forma` no config.ini."""
        assert fuzzy.melhor_match("track_forma", self.OPCOES) == "track_format"

    def test_devolve_string_nao_tupla(self, motor):
        """BUGFIX: `fuzz_process.extractOne()` devolvia uma TUPLA e o cli.py
        usava `best[0]`. Se `melhor_match` devolvesse tupla, `best[0]` pegaria
        a primeira LETRA da string. A API foi deliberadamente simplificada
        para devolver so' o nome."""
        r = fuzzy.melhor_match("track_forma", self.OPCOES)
        assert isinstance(r, str)

    def test_sem_nada_parecido_devolve_none(self, motor):
        assert fuzzy.melhor_match("xyzabc123", self.OPCOES, corte=0.6) is None

    def test_corte_alto_rejeita_match_aproximado(self, motor):
        """Com corte 1.0, so' o texto identico passa."""
        assert fuzzy.melhor_match("track_forma", self.OPCOES, corte=1.0) is None
        assert fuzzy.melhor_match("track_format", self.OPCOES, corte=1.0) == (
            "track_format"
        )

    def test_corte_fora_da_faixa_nao_explode(self, motor):
        """Regressao: antes do clamp, corte fora de [0,1] levantava excecao --
        e uma excecao DIFERENTE em cada motor (TypeError no rapidfuzz,
        ValueError no difflib), furando a equivalencia entre os dois."""
        assert fuzzy.melhor_match("track_forma", self.OPCOES, corte=1.5) is None
        assert fuzzy.melhor_match("track_forma", self.OPCOES, corte=-2.0) is not None

    def test_lista_de_opcoes_vazia(self, motor):
        assert fuzzy.melhor_match("qualquer", []) is None


def test_nome_do_motor_e_honesto():
    """A funcao existe para diagnostico -- precisa dizer a verdade."""
    nome = fuzzy.nome_do_motor()
    assert ("rapidfuzz" in nome) is bool(fuzzy.RAPIDFUZZ_DISPONIVEL)
    assert ("difflib" in nome) is not bool(fuzzy.RAPIDFUZZ_DISPONIVEL)


def test_cli_importa_sem_rapidfuzz(monkeypatch):
    """A regressao original, testada de verdade: com o rapidfuzz bloqueado no
    nivel do import, o CLI ainda tem que importar."""
    for mod in list(sys.modules):
        if mod.startswith(("rapidfuzz", "qobuz_dl")):
            del sys.modules[mod]

    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def import_bloqueado(nome, *args, **kwargs):
        if nome.startswith("rapidfuzz"):
            raise ImportError("bloqueado pelo teste")
        return real_import(nome, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", import_bloqueado)

    import qobuz_dl.cli as cli_sem_rapidfuzz

    assert cli_sem_rapidfuzz is not None
    from qobuz_dl import fuzzy as fuzzy_recarregado

    assert fuzzy_recarregado.RAPIDFUZZ_DISPONIVEL is False
    assert "difflib" in fuzzy_recarregado.nome_do_motor()
