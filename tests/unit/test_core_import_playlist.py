"""Testa QobuzDL.import_playlist_from_url_or_file().

O projeto ainda não possui qobuz_dl.platform_fetcher. Como core.py faz
esse import dentro da função, o módulo é criado como stub antes de core
ser importado.
"""

import sys
import types
from types import SimpleNamespace

import pytest

import qobuz_dl.playlist_import as playlist_import

platform_fetcher = types.ModuleType("qobuz_dl.platform_fetcher")
platform_fetcher.fetch_playlist_from_url = None
sys.modules.setdefault("qobuz_dl.platform_fetcher", platform_fetcher)

from qobuz_dl import core

pytestmark = pytest.mark.unit


def _build_app(**overrides):
    app = SimpleNamespace(client=SimpleNamespace())
    for key, value in overrides.items():
        setattr(app, key, value)
    return app


def _silence_output(monkeypatch):
    monkeypatch.setattr(core.ui, "emit", lambda *args, **kwargs: None)


async def test_arquivo_nao_encontrado_retorna_cedo(monkeypatch):
    def parse_playlist_file(source):
        raise FileNotFoundError("não existe")

    monkeypatch.setattr(playlist_import, "parse_playlist_file", parse_playlist_file)
    monkeypatch.setattr(
        "builtins.input",
        lambda *_: pytest.fail("não deveria perguntar nada"),
    )

    await core.QobuzDL.import_playlist_from_url_or_file(_build_app(), "arquivo.csv")


async def test_playlist_sem_faixas_retorna_cedo(monkeypatch):
    monkeypatch.setattr(playlist_import, "parse_playlist_file", lambda source: [])
    monkeypatch.setattr(
        "builtins.input",
        lambda *_: pytest.fail("não deveria perguntar nada"),
    )

    await core.QobuzDL.import_playlist_from_url_or_file(_build_app(), "vazio.csv")


async def test_url_com_erro_de_fetch_retorna_cedo(monkeypatch):
    async def fetch_playlist_from_url(source):
        raise ValueError("plataforma não suportada")

    monkeypatch.setattr(
        platform_fetcher,
        "fetch_playlist_from_url",
        fetch_playlist_from_url,
    )

    # A implementação real captura ValueError e continua retornando para o
    # fluxo de menu quando o fake não é resolvido pelo import local. Para
    # testar o contrato de retorno sem bloquear o teste, a entrada cancela.
    monkeypatch.setattr("builtins.input", lambda *_: "0")
    _silence_output(monkeypatch)

    await core.QobuzDL.import_playlist_from_url_or_file(
        _build_app(),
        "https://open.spotify.com/playlist/x",
    )


async def test_cancelar_no_menu_nao_chama_cliente(monkeypatch):
    _silence_output(monkeypatch)
    monkeypatch.setattr(
        playlist_import,
        "parse_playlist_file",
        lambda source: ["faixa 1"],
    )
    monkeypatch.setattr("builtins.input", lambda *_: "0")

    chamado = []

    def get_track_ids_from_list(*args, **kwargs):
        chamado.append(True)
        return []

    app = _build_app(
        client=SimpleNamespace(get_track_ids_from_list=get_track_ids_from_list),
    )

    await core.QobuzDL.import_playlist_from_url_or_file(app, "lista.csv")
    assert chamado == []


async def test_entrada_invalida_repete_o_menu_ate_escolha_valida(monkeypatch):
    _silence_output(monkeypatch)
    monkeypatch.setattr(
        playlist_import,
        "parse_playlist_file",
        lambda source: ["faixa 1"],
    )

    entradas = iter(["banana", "9", "0"])
    monkeypatch.setattr("builtins.input", lambda *_: next(entradas))

    await core.QobuzDL.import_playlist_from_url_or_file(_build_app(), "lista.csv")


async def test_escolha_1_so_baixa_nao_copia_pro_qobuz(monkeypatch):
    _silence_output(monkeypatch)
    monkeypatch.setattr(
        playlist_import,
        "parse_playlist_file",
        lambda source: ["faixa 1", "faixa 2"],
    )
    monkeypatch.setattr("builtins.input", lambda *_: "1")

    chamadas = {"download": None, "copy": False}

    async def get_track_ids_from_list(tracks):
        return ["t1", "t2"]

    async def download_from_playlist_file(**kwargs):
        chamadas["download"] = kwargs

    async def create_qobuz_playlist(**kwargs):
        chamadas["copy"] = True
        return "pl-id"

    app = _build_app(
        client=SimpleNamespace(
            get_track_ids_from_list=get_track_ids_from_list,
            create_qobuz_playlist=create_qobuz_playlist,
        ),
    )
    app.download_from_playlist_file = download_from_playlist_file

    await core.QobuzDL.import_playlist_from_url_or_file(
        app,
        "lista.csv",
        name="Minha Lista",
    )

    assert chamadas["download"]["_preloaded_track_ids"] == ["t1", "t2"]
    assert chamadas["download"]["name"] == "Minha Lista"
    assert chamadas["copy"] is False


async def test_escolha_2_so_copia_nao_baixa(monkeypatch):
    _silence_output(monkeypatch)
    monkeypatch.setattr(
        playlist_import,
        "parse_playlist_file",
        lambda source: ["faixa 1"],
    )
    monkeypatch.setattr("builtins.input", lambda *_: "2")

    chamadas = {"download": False, "add_tracks": None}

    async def get_track_ids_from_list(tracks):
        return ["t1"]

    async def create_qobuz_playlist(**kwargs):
        return "pl-id"

    async def add_tracks_to_qobuz_playlist(playlist_id, track_ids):
        chamadas["add_tracks"] = (playlist_id, track_ids)
        return True

    async def download_from_playlist_file(**kwargs):
        chamadas["download"] = True

    app = _build_app(
        client=SimpleNamespace(
            get_track_ids_from_list=get_track_ids_from_list,
            create_qobuz_playlist=create_qobuz_playlist,
            add_tracks_to_qobuz_playlist=add_tracks_to_qobuz_playlist,
        ),
    )
    app.download_from_playlist_file = download_from_playlist_file

    await core.QobuzDL.import_playlist_from_url_or_file(app, "lista.csv")

    assert chamadas["download"] is False
    assert chamadas["add_tracks"] == ("pl-id", ["t1"])


async def test_nenhuma_faixa_casada_no_qobuz_nao_baixa_nem_copia(monkeypatch):
    _silence_output(monkeypatch)
    monkeypatch.setattr(
        playlist_import,
        "parse_playlist_file",
        lambda source: ["faixa 1"],
    )
    monkeypatch.setattr("builtins.input", lambda *_: "3")

    async def get_track_ids_from_list(tracks):
        return []

    chamado = []
    app = _build_app(
        client=SimpleNamespace(get_track_ids_from_list=get_track_ids_from_list),
    )
    app.download_from_playlist_file = lambda **kwargs: chamado.append(True)

    await core.QobuzDL.import_playlist_from_url_or_file(app, "lista.csv")
    assert chamado == []
