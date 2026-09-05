"""Testa a camada de rede sem tocar em servico real, via pytest-httpx.

POR QUE ISTO IMPORTA
--------------------
Antes desta suite, NENHUM teste do projeto exercitava rede -- e rede e' onde
vive a maior parte dos bugs deste programa: resposta com formato inesperado,
tag de versao estranha, HTTP 403/404, timeout, JSON invalido. Todos esses
caminhos ficavam so' na esperanca de que a API se comportasse.

`pytest-httpx` intercepta o transporte do `httpx` (a biblioteca que o projeto
usa), entao os testes rodam offline, em milissegundos, e podem simular
respostas que na vida real sao dificeis de reproduzir de proposito.
"""

import httpx
import pytest

URL_RELEASES = "https://api.github.com/repos/kaduvercosa/qobuz-dl-ultra/releases/latest"


@pytest.fixture
def check_updates():
    from qobuz_dl.cli import check_for_updates

    return check_for_updates


class TestCheckForUpdates:
    """O aviso de atualizacao, ponta a ponta, com o GitHub simulado."""

    def test_avisa_quando_ha_versao_nova(self, httpx_mock, capsys, check_updates):
        httpx_mock.add_response(url=URL_RELEASES, json={"tag_name": "v99.0.0"})

        check_updates()

        saida = capsys.readouterr().out
        assert "ATUALIZAÇÃO DISPONÍVEL" in saida
        assert "99.0.0" in saida

    def test_silencio_quando_ja_esta_atualizado(
        self, httpx_mock, capsys, check_updates
    ):
        import qobuz_dl

        httpx_mock.add_response(
            url=URL_RELEASES, json={"tag_name": f"v{qobuz_dl.__version__}"}
        )

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    def test_nao_avisa_para_release_candidate(self, httpx_mock, capsys, check_updates):
        """REGRESSAO DIRETA do bug de versao: uma tag "-rc1" quebrava o
        `int()` do codigo antigo. E se alguem tivesse "consertado" removendo os
        caracteres nao numericos, "99.0.0rc1" viraria "99.0.01" -- maior que
        99.0.0 -- e o programa ofereceria um release candidate como estavel.

        O comportamento correto: um rc de uma versao FUTURA ainda e' mais novo
        (avisa), mas um rc da versao ATUAL nao e' (nao avisa)."""
        import qobuz_dl

        httpx_mock.add_response(
            url=URL_RELEASES, json={"tag_name": f"v{qobuz_dl.__version__}rc1"}
        )

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    @pytest.mark.parametrize("tag", ["v99.0.0-rc1", "v99.0.0b1", "v99.0.0.post1"])
    def test_tags_nao_numericas_nao_quebram(
        self, httpx_mock, capsys, check_updates, tag
    ):
        """Cada uma destas tags levantava ValueError antes -- engolido pelo
        `except Exception: pass`, entao o aviso nunca aparecia."""
        httpx_mock.add_response(url=URL_RELEASES, json={"tag_name": tag})

        check_updates()

        assert "ATUALIZAÇÃO DISPONÍVEL" in capsys.readouterr().out

    def test_erro_http_nao_derruba_o_programa(self, httpx_mock, capsys, check_updates):
        """Quem so' quer baixar musica nao pode ser interrompido porque o
        GitHub respondeu 403 (rate limit e' comum em IP compartilhado)."""
        httpx_mock.add_response(url=URL_RELEASES, status_code=403)

        check_updates()  # nao pode levantar

        assert capsys.readouterr().out.strip() == ""

    def test_sem_rede_nao_derruba_o_programa(self, httpx_mock, capsys, check_updates):
        httpx_mock.add_exception(httpx.ConnectError("sem rede"))

        check_updates()

        assert capsys.readouterr().out.strip() == ""

    def test_timeout_nao_derruba_o_programa(self, httpx_mock, capsys, check_updates):
        httpx_mock.add_exception(httpx.ReadTimeout("estourou"))

        check_updates()

        assert capsys.readouterr().out.strip() == ""

    def test_json_invalido_nao_derruba_o_programa(
        self, httpx_mock, capsys, check_updates
    ):
        httpx_mock.add_response(url=URL_RELEASES, content=b"<html>nao e json</html>")

        check_updates()

        assert capsys.readouterr().out.strip() == ""

    def test_resposta_sem_tag_name(self, httpx_mock, capsys, check_updates):
        """Formato inesperado: 200 OK, JSON valido, campo ausente. O codigo usa
        `.get("tag_name", "")`, entao vira Version("") -- que e' InvalidVersion.
        Tem que ser tratado como falha silenciosa, nao traceback."""
        httpx_mock.add_response(url=URL_RELEASES, json={"nome": "sem tag"})

        check_updates()

        assert capsys.readouterr().out.strip() == ""

    def test_falha_e_registrada_em_debug(self, httpx_mock, caplog, check_updates):
        """BUGFIX: era `except Exception: pass`. O erro desaparecia por
        completo -- foi assim que o bug de versao ficou invisivel por tanto
        tempo. Agora fica no log de debug: nao incomoda o usuario normal, mas
        `--verbose` mostra o motivo."""
        import logging

        httpx_mock.add_response(url=URL_RELEASES, status_code=500)

        with caplog.at_level(logging.DEBUG, logger="qobuz_dl.cli"):
            check_updates()

        assert [
            r for r in caplog.records if "atualização falhou" in r.getMessage().lower()
        ]
