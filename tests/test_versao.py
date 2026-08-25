"""Testa a comparacao de versao do aviso de atualizacao.

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
`cli.py::check_for_updates()` comparava versoes assim:

    latest_tuple = tuple(map(int, latest.replace("v", "").split(".")))

Tres problemas, todos reais:

1. `int()` explode em qualquer tag que nao seja puramente numerica --
   "2.5.0-rc1", "2.5.0b1", "2.5.0.post1" viram ValueError.
2. `.replace("v", "")` apaga TODO "v" da string, nao so' o prefixo. A tag
   "v2.5.0-preview" virava "2.5.0-preiew".
3. O bloco inteiro estava dentro de `except Exception: pass`, entao o erro
   nunca aparecia: o aviso de atualizacao simplesmente NUNCA era mostrado, e
   ninguem descobria o porque.

A correcao usa `packaging.version.Version`, que implementa a especificacao de
versao da PyPA, e `.lstrip("vV")`, que so' mexe no prefixo.
"""

import pytest
from packaging.version import InvalidVersion, Version


def normalizar(tag: str) -> Version:
    """Reproduz exatamente o que o cli.py faz com a tag vinda do GitHub."""
    return Version(tag.lstrip("vV"))


class TestPrefixo:
    def test_lstrip_nao_come_v_do_meio(self):
        """O `.replace("v", "")` antigo estragava a palavra."""
        assert "v2.5.0-preview".lstrip("vV") == "2.5.0-preview"
        assert "v2.5.0-preview".replace("v", "") == "2.5.0-preiew"  # o bug

    def test_aceita_maiusculo(self):
        assert normalizar("V2.5.0") == Version("2.5.0")

    def test_sem_prefixo_tambem_funciona(self):
        assert normalizar("2.5.0") == Version("2.5.0")


class TestTagsQueQuebravamAntes:
    """Cada tag aqui levantava ValueError no codigo antigo."""

    @pytest.mark.parametrize(
        "tag",
        [
            "v2.5.0-rc1",
            "v2.5.0b1",
            "v2.5.0.post1",
            "v2.5.0a2",
            "v2.5.0.dev3",
            "v2.5.0rc1",
            "v3.0",
        ],
    )
    def test_tag_e_interpretada(self, tag):
        assert isinstance(normalizar(tag), Version)

    @pytest.mark.parametrize(
        "tag",
        ["v2.5.0-rc1", "v2.5.0b1", "v2.5.0a2", "v2.5.0.dev3"],
    )
    def test_pre_release_nao_e_maior_que_o_final(self, tag):
        """Semantica correta: rc/beta/alpha vem ANTES do lancamento final.

        Com o codigo antigo isto nem chegava a ser comparado -- explodia na
        conversao. Se alguem "consertasse" removendo os caracteres nao
        numericos, "2.5.0rc1" viraria "2.5.01" e ficaria MAIOR que "2.5.0",
        avisando o usuario para instalar um release candidate como se fosse
        estavel."""
        assert normalizar(tag) < Version("2.5.0")

    def test_post_release_e_maior_que_o_final(self):
        assert normalizar("v2.5.0.post1") > Version("2.5.0")


class TestComparacaoContraVersaoAtual:
    ATUAL = Version("2.4.8.2")  # a versao real do projeto quando isto foi escrito

    @pytest.mark.parametrize("tag", ["v2.4.9", "v2.5.0", "v3.0.0", "v2.4.8.3"])
    def test_detecta_versao_mais_nova(self, tag):
        assert normalizar(tag) > self.ATUAL

    @pytest.mark.parametrize("tag", ["v2.4.8.2", "v2.4.8.1", "v2.4.8", "v1.0.0"])
    def test_nao_avisa_para_versao_igual_ou_antiga(self, tag):
        assert not (normalizar(tag) > self.ATUAL)

    def test_comparacao_numerica_nao_lexicografica(self):
        """"2.4.10" > "2.4.9" numericamente, mas < como texto. Comparar tags
        como string daria a resposta errada e nunca ofereceria a 2.4.10."""
        assert Version("2.4.10") > Version("2.4.9")
        assert "2.4.10" < "2.4.9"  # o que aconteceria comparando texto


def test_versao_do_pacote_e_valida():
    """A propria versao do projeto tem que ser interpretavel -- senao o
    check_for_updates() falha comparando com ela mesma."""
    import qobuz_dl

    assert isinstance(Version(qobuz_dl.__version__), Version)


def test_tag_realmente_lixo_ainda_e_erro():
    """Controle de sanidade: o teste acima nao pode estar passando porque o
    `Version` aceita qualquer coisa. Ele tem limites."""
    with pytest.raises(InvalidVersion):
        normalizar("nao-e-uma-versao")
