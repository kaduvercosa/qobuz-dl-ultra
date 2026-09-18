"""Testa `QobuzDL.download_from_txt_file()` e `QobuzDL.lucky_mode()`.

`download_from_txt_file` usa `get_url_info()` de verdade (não mockada)
pra decidir o que é uma URL válida -- já é testada isoladamente em
test_utils_pure.py, então usar a real aqui é mais simples e mais
realista que fakear.
"""

from types import SimpleNamespace

import pytest

from qobuz_dl import core

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------
# download_from_txt_file
# --------------------------------------------------------------------
async def test_filtra_comentarios_vazias_done_e_invalidas(tmp_path):
    arquivo = tmp_path / "urls.txt"
    arquivo.write_text(
        "# comentário\n"
        "\n"
        "https://play.qobuz.com/album/1 [DONE]\n"
        "https://play.qobuz.com/track/2\n"
        "isso não é uma url valida\n",
        encoding="utf-8",
    )
    recebido = {}

    async def download_list_of_urls(urls, txt_file=None):
        recebido["urls"] = urls
        recebido["txt_file"] = txt_file

    app = SimpleNamespace(download_list_of_urls=download_list_of_urls)

    await core.QobuzDL.download_from_txt_file(app, str(arquivo))

    assert recebido["urls"] == ["https://play.qobuz.com/track/2"]
    assert recebido["txt_file"] == str(arquivo)


async def test_nenhuma_url_valida_nao_chama_download(tmp_path):
    arquivo = tmp_path / "urls.txt"
    arquivo.write_text("# só comentário\n\n", encoding="utf-8")
    chamado = []
    app = SimpleNamespace(download_list_of_urls=lambda *a, **k: chamado.append(True))

    await core.QobuzDL.download_from_txt_file(app, str(arquivo))

    assert chamado == []


async def test_arquivo_inexistente_nao_lanca_excecao():
    chamado = []
    app = SimpleNamespace(download_list_of_urls=lambda *a, **k: chamado.append(True))

    await core.QobuzDL.download_from_txt_file(app, "/caminho/que/nao/existe.txt")

    assert chamado == []


# --------------------------------------------------------------------
# lucky_mode
# --------------------------------------------------------------------
async def test_query_curta_nao_busca():
    async def search_by_type(*a, **k):
        raise AssertionError("não deveria buscar com query curta")

    app = SimpleNamespace(search_by_type=search_by_type)

    result = await core.QobuzDL.lucky_mode(app, "ab")

    assert result is None


async def test_download_true_busca_e_baixa_os_resultados():
    chamadas = {}

    async def search_by_type(query, item_type, limit, lucky):
        chamadas["search"] = (query, item_type, limit, lucky)
        return ["https://play.qobuz.com/album/1"]

    async def download_list_of_urls(urls):
        chamadas["download"] = urls

    app = SimpleNamespace(
        lucky_type="album",
        lucky_limit=3,
        search_by_type=search_by_type,
        download_list_of_urls=download_list_of_urls,
    )

    result = await core.QobuzDL.lucky_mode(app, "some query")

    assert chamadas["search"] == ("some query", "album", 3, True)
    assert chamadas["download"] == ["https://play.qobuz.com/album/1"]
    assert result == ["https://play.qobuz.com/album/1"]


async def test_download_false_nao_baixa_mas_devolve_resultados():
    async def search_by_type(query, item_type, limit, lucky):
        return ["url1", "url2"]

    async def download_list_of_urls(*a, **k):
        raise AssertionError("não deveria baixar quando download=False")

    app = SimpleNamespace(
        lucky_type="track",
        lucky_limit=5,
        search_by_type=search_by_type,
        download_list_of_urls=download_list_of_urls,
    )

    result = await core.QobuzDL.lucky_mode(app, "some query", download=False)

    assert result == ["url1", "url2"]
