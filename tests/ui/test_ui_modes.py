"""Testa o modo visual explícito usado entre celular e iPad."""

import pytest

from qobuz_dl import ui

pytestmark = pytest.mark.ui


def test_auto_respeita_a_largura(monkeypatch):
    ui.configure(ui_mode="auto")
    monkeypatch.setattr(ui, "raw_width", lambda: 60)
    assert ui.compact_mode() is True
    assert ui.layout() == ui.LAYOUT_MEDIUM


def test_compact_forca_cartoes_em_tela_larga(monkeypatch):
    monkeypatch.setattr(ui, "raw_width", lambda: 120)
    ui.configure(ui_mode="compact")
    try:
        assert ui.compact_mode() is True
        assert ui.layout() == ui.LAYOUT_NARROW
    finally:
        ui.configure(ui_mode="auto")


def test_comfortable_desativa_compactacao_em_tela_estreita(monkeypatch):
    monkeypatch.setattr(ui, "raw_width", lambda: 40)
    ui.configure(ui_mode="comfortable")
    try:
        assert ui.compact_mode() is False
        assert ui.layout() == ui.LAYOUT_WIDE
    finally:
        ui.configure(ui_mode="auto")


def test_modo_invalido_e_rejeitado():
    with pytest.raises(ValueError, match="modo de UI inválido"):
        ui.configure(ui_mode="tablet")
