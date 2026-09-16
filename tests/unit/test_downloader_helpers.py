"""Testes de helpers pequenos e de baixo risco do downloader."""

import json
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


def test_build_letras_report_empty():
    assert downloader._build_letras_report(None, None, None) == {}


def test_build_letras_report_success_bilingual():
    result = downloader._build_letras_report(
        {
            "success": True,
            "language": "en+pt",
            "synchronized": True,
            "bilingual": True,
            "embedded": True,
            "saved_external": True,
            "source": "lrclib",
        },
        "pt",
        {"translated": True},
    )

    assert result == {
        "situacao": "sucesso",
        "sincronizada": True,
        "bilingue": True,
        "idioma_original": "en",
        "traducao_disponivel": True,
        "fonte": "lrclib",
        "destino": "metadata + .lrc/.txt",
        "observacao": "",
    }


def test_build_letras_report_failure():
    result = downloader._build_letras_report(
        {"error": "not found", "language": "unknown"},
        "pt",
        None,
    )

    assert result["situacao"] == "falha"
    assert result["idioma_original"] == ""
    assert result["observacao"] == "not found"
    assert result["traducao_disponivel"] is False


def test_emit_progress_json_desligado(capsys):
    downloader.emit_progress_json(SimpleNamespace(progress_json=False), "start", id="x")
    assert capsys.readouterr().out == ""


def test_emit_progress_json_ligado(capsys):
    downloader.emit_progress_json(
        SimpleNamespace(progress_json=True),
        "finish",
        id="track-1",
        status="ok",
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["event"] == "finish"
    assert payload["id"] == "track-1"
    assert payload["status"] == "ok"
    assert isinstance(payload["ts"], float)


def test_safe_print_junta_args_com_espaco_e_usa_ui_emit(monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        downloader.ui, "emit", lambda text="", end="\n": chamadas.append((text, end))
    )

    downloader.safe_print("a", 1, "b")
    downloader.safe_print("x", end="")

    assert chamadas == [("a 1 b", "\n"), ("x", "")]


def test_get_description_inclui_bit_depth_e_sampling_rate():
    assert (
        downloader._get_description({"bit_depth": 24, "sampling_rate": 96}, "Musica")
        == "Musica [24/96]"
    )


def test_get_description_com_multiple_adiciona_prefixo_de_cd():
    assert (
        downloader._get_description({}, "Musica", multiple=2) == "[CD 2] Musica [/]"
    )
