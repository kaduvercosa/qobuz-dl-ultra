"""`--json` uniforme: stats e library (e o contrato de `ui.emit_json`)."""

import asyncio
import json
from datetime import date
from types import SimpleNamespace

import pytest

from qobuz_dl import cli, library_cmd as lc, stats_view, ui
from qobuz_dl.commands import qobuz_dl_args
from qobuz_dl.library_db import LibraryDB


@pytest.fixture
def lib(tmp_path):
    return LibraryDB(tmp_path / "l.db")


def _lib_args(**kw):
    base = dict(TARGET=None, limit=None, fix=False, dry_run=False, json=True)
    base.update(kw)
    return SimpleNamespace(**base)


def _run(lib, tmp_path, **kw):
    return asyncio.run(
        lc.cmd_library(_lib_args(**kw), directory=str(tmp_path), lib=lib)
    )


def _json(capsys):
    return json.loads(capsys.readouterr().out)


# --- ui.emit_json ------------------------------------------------------------


def test_emit_json_mantem_acentos_e_converte_tipos_desconhecidos(capsys):
    ui.emit_json({"nome": "Café", "dia": date(2026, 11, 3)})
    bruto = capsys.readouterr().out
    assert "Café" in bruto
    assert json.loads(bruto) == {"nome": "Café", "dia": "2026-11-03"}


def test_emit_json_sai_mesmo_com_quiet(capsys, monkeypatch):
    monkeypatch.setattr(ui.estado, "quiet", True)
    ui.emit_json([1])
    assert json.loads(capsys.readouterr().out) == [1]


# --- library --json ------------------------------------------------------------


def test_library_status_json(lib, tmp_path, capsys):
    a = lib.upsert_album("qobuz", "1", "Titulo", "Artista")
    lib.update_status(a, "downloading")
    assert _run(lib, tmp_path, action="status") == 0
    dados = _json(capsys)
    assert dados["contagens"] == [
        {"origem": "qobuz", "estado": "downloading", "total": 1}
    ]
    assert dados["presos"] == 1
    assert dados["completos_sem_pasta"] == 0


def test_library_status_json_com_catalogo_vazio(lib, tmp_path, capsys):
    assert _run(lib, tmp_path, action="status") == 0
    assert _json(capsys)["contagens"] == []


def test_library_missing_json_lista_so_o_que_falta(lib, tmp_path, capsys):
    lib.upsert_album("qobuz", "1", "Um", "A")
    ok = lib.upsert_album("qobuz", "2", "Dois", "B")
    lib.update_status(ok, "complete")
    assert _run(lib, tmp_path, action="missing") == 0
    dados = _json(capsys)
    assert [d["id"] for d in dados] == ["1"]
    assert dados[0]["artista"] == "A" and dados[0]["titulo"] == "Um"
    assert dados[0]["estado"] == "not_downloaded"
    assert dados[0]["removido_da_conta"] is False


def test_library_list_json_vazio_e_lista_vazia(lib, tmp_path, capsys):
    assert _run(lib, tmp_path, action="list") == 0
    assert _json(capsys) == []


def test_library_list_json_respeita_limit(lib, tmp_path, capsys):
    for i in range(3):
        lib.upsert_album("qobuz", str(i), f"T{i}", f"A{i}")
    _run(lib, tmp_path, action="list", limit=2)
    assert len(_json(capsys)) == 2


def test_library_history_json_vazio(lib, tmp_path, capsys):
    assert _run(lib, tmp_path, action="history") == 0
    assert _json(capsys) == []


def test_library_reconcile_json(lib, tmp_path, capsys):
    assert _run(lib, tmp_path, action="reconcile", dry_run=True) == 0
    dados = _json(capsys)
    assert set(dados) == {
        "sentinelas_vistas",
        "reconciliadas",
        "invalidas",
        "sem_pasta",
    }


@pytest.mark.parametrize("acao", ["reset-stuck", "unmark"])
def test_library_json_recusa_acoes_que_alteram(lib, tmp_path, acao):
    a = lib.upsert_album("qobuz", "1", "T", "A")
    lib.update_status(a, "downloading")
    assert _run(lib, tmp_path, action=acao) == 2
    assert lib.get_album(a)["download_status"] == "downloading"


def test_library_sem_json_continua_em_painel(lib, tmp_path, capsys):
    lib.upsert_album("qobuz", "1", "T", "A")
    asyncio.run(
        lc.cmd_library(
            _lib_args(action="status", json=False), directory=str(tmp_path), lib=lib
        )
    )
    assert "BIBLIOTECA" in capsys.readouterr().out


# --- stats --json --------------------------------------------------------------


def _stats(monkeypatch, dados):
    monkeypatch.setattr(stats_view, "get_stats", lambda _p: dados)


def test_stats_json_inclui_numeros_e_omite_lista_de_artistas(monkeypatch, capsys):
    _stats(
        monkeypatch,
        {"total": 3, "top_artists": [("A", 2)], "artist_list": ["A", "B"]},
    )
    assert stats_view.render_stats("x.db", como_json=True) == 0
    dados = _json(capsys)
    assert dados["total"] == 3
    assert dados["top_artists"] == [["A", 2]]
    assert "artist_list" not in dados


def test_stats_json_com_artistas_traz_a_lista(monkeypatch, capsys):
    _stats(monkeypatch, {"total": 1, "artist_list": ["A", "B"]})
    stats_view.render_stats("x.db", show_all_artists=True, como_json=True)
    assert _json(capsys)["artist_list"] == ["A", "B"]


def test_stats_json_banco_vazio_vira_json_e_nao_texto(monkeypatch, capsys):
    _stats(monkeypatch, {"total": 0})
    assert stats_view.render_stats("x.db", como_json=True) == 0
    assert _json(capsys) == {"total": 0}


def test_stats_json_sem_banco(monkeypatch, capsys):
    _stats(monkeypatch, {})
    stats_view.render_stats(None, como_json=True)
    assert _json(capsys) == {}


# --- parser e silêncio ------------------------------------------------------------


@pytest.mark.parametrize("cmd", ["stats", "library", "config", "duplicates", "doctor"])
def test_comandos_aceitam_json(cmd):
    assert qobuz_dl_args().parse_args([cmd, "--json"]).json is True


@pytest.mark.parametrize(
    "argv,esperado",
    [
        (["stats", "--json"], True),
        (["library", "missing", "--json"], True),
        (["--json", "doctor"], True),
        (["config", "--json"], True),
        (["stats"], False),
        (["scan", "--json", "rel.json"], False),  # aqui --json é um ARQUIVO
        (["inspect", "--json"], False),
        ([], False),
    ],
)
def test_saida_so_json(argv, esperado):
    assert cli._saida_so_json(argv) is esperado
