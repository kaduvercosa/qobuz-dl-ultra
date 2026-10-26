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

import re

import httpx
import pytest

# O aviso consulta o PyPI (com um parametro "?t=<minuto>" para furar a CDN).
URL_RELEASES = re.compile(r"https://pypi\.org/pypi/qobuz-dl-ultra/json(\?.*)?")


def _pypi(versao: str) -> dict:
    """Formato real da resposta do PyPI: a versao esta em info.version."""
    return {"info": {"version": versao}}


@pytest.fixture(autouse=True)
def _checagem_ligada(monkeypatch):
    monkeypatch.delenv("QOBUZ_DL_NO_UPDATE_CHECK", raising=False)


@pytest.fixture
def check_updates():
    from qobuz_dl.cli import check_for_updates

    return check_for_updates


class TestCheckForUpdates:
    """O aviso de atualizacao, ponta a ponta, com o PyPI simulado."""

    def test_avisa_quando_ha_versao_nova(self, httpx_mock, capsys, check_updates):
        httpx_mock.add_response(url=URL_RELEASES, json=_pypi("99.0.0"))

        check_updates()

        saida = capsys.readouterr().out
        assert "ATUALIZAÇÃO DISPONÍVEL" in saida
        assert "99.0.0" in saida

    def test_silencio_quando_ja_esta_atualizado(
        self, httpx_mock, capsys, check_updates
    ):
        import qobuz_dl

        httpx_mock.add_response(url=URL_RELEASES, json=_pypi(qobuz_dl.__version__))

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
            url=URL_RELEASES, json=_pypi(f"{qobuz_dl.__version__}rc1")
        )

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    @pytest.mark.parametrize("tag", ["v99.0.0-rc1", "v99.0.0b1", "v99.0.0.post1"])
    def test_tags_nao_numericas_nao_quebram(
        self, httpx_mock, capsys, check_updates, tag
    ):
        """Cada uma destas tags levantava ValueError antes -- engolido pelo
        `except Exception: pass`, entao o aviso nunca aparecia."""
        httpx_mock.add_response(url=URL_RELEASES, json=_pypi(tag.lstrip("v")))

        check_updates()

        assert "ATUALIZAÇÃO DISPONÍVEL" in capsys.readouterr().out

    def test_erro_http_nao_derruba_o_programa(self, httpx_mock, capsys, check_updates):
        """Quem so' quer baixar musica nao pode ser interrompido porque o
        PyPI respondeu 403/5xx (acontece em redes corporativas e proxies)."""
        httpx_mock.add_response(url=URL_RELEASES, status_code=403)

        check_updates()  # nao pode levantar

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    def test_sem_rede_nao_derruba_o_programa(self, httpx_mock, capsys, check_updates):
        httpx_mock.add_exception(httpx.ConnectError("sem rede"))

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    def test_timeout_nao_derruba_o_programa(self, httpx_mock, capsys, check_updates):
        httpx_mock.add_exception(httpx.ReadTimeout("estourou"))

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    def test_json_invalido_nao_derruba_o_programa(
        self, httpx_mock, capsys, check_updates
    ):
        httpx_mock.add_response(url=URL_RELEASES, content=b"<html>nao e json</html>")

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    def test_resposta_sem_versao(self, httpx_mock, capsys, check_updates):
        """Formato inesperado: 200 OK, JSON valido, sem `info.version`.
        Tem que ser tratado como falha silenciosa, nao traceback."""
        httpx_mock.add_response(url=URL_RELEASES, json={"nome": "sem info"})

        check_updates()

        assert "ATUALIZAÇÃO" not in capsys.readouterr().out

    def test_falha_e_registrada_em_debug(
        self, httpx_mock, caplog, capsys, check_updates
    ):
        import logging

        httpx_mock.add_response(url=URL_RELEASES, status_code=500)

        with caplog.at_level(logging.DEBUG, logger="qobuz_dl.cli"):
            check_updates()

        # Verifica que o log existe
        records = [
            r for r in caplog.records if "atualização falhou" in r.getMessage().lower()
        ]

        assert records, f"Log de falha não encontrado. Records: {caplog.records}"
        # Se houver output, ele deve vir do logger, não de print cru
        # Em produção, --verbose mostraria; em teste, aceitamos que caplog basta


class TestSemCache:
    """A checagem tem que ser SEMPRE ao vivo: e' ela que faz o aviso aparecer
    minutos apos o lancamento e sumir assim que a pessoa atualiza."""

    def test_pede_resposta_sem_cache(self, httpx_mock):
        from qobuz_dl import update_check

        httpx_mock.add_response(url=URL_RELEASES, json=_pypi("99.0.0"))

        assert update_check.nova_versao_disponivel("1.0.0") == "99.0.0"

        req = httpx_mock.get_requests()[0]
        assert req.headers["cache-control"] == "no-cache"
        assert req.url.params.get("t")  # muda a cada minuto: fura a CDN

    def test_nao_grava_nada_em_disco(self, httpx_mock, tmp_path, monkeypatch):
        """Sem cache local: nenhum arquivo e' criado no diretorio de trabalho."""
        from qobuz_dl import update_check

        monkeypatch.chdir(tmp_path)
        httpx_mock.add_response(url=URL_RELEASES, json=_pypi("99.0.0"))
        update_check.nova_versao_disponivel("1.0.0")
        assert list(tmp_path.iterdir()) == []

    def test_variavel_de_ambiente_desliga(self, monkeypatch, capsys, check_updates):
        monkeypatch.setenv("QOBUZ_DL_NO_UPDATE_CHECK", "1")
        check_updates()  # sem httpx_mock: qualquer requisicao falharia o teste
        assert "ATUALIZAÇÃO" not in capsys.readouterr().out
