"""Testes estáticos para evitar a inicialização do prompt_toolkit no iOS."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


CORE = Path(__file__).parents[2] / "qobuz_dl" / "core.py"


def _interactive_source():
    source = CORE.read_text(encoding="utf-8")
    start = source.index("    async def interactive(")
    end = source.find("\n    async def ", start + 1)
    return source[start:] if end == -1 else source[start:end]


def test_interactive_mantem_menu_de_tipos():
    source = _interactive_source()
    assert '"🎵 Tracks"' in source
    assert '"💿 Albums"' in source
    assert '"📀 Singles"' in source
    assert '"🎤 Artists"' in source
    assert '"📋 Playlists"' in source
    assert '"⭐ Favorites"' in source


def test_interactive_trata_cancelamento_do_tui():
    source = _interactive_source()
    assert "if not scelta_res:" in source


def test_interactive_separa_tipo_da_opcao_visual():
    source = _interactive_source()
    assert "scelta_raw_visual, _ = scelta_res" in source
    assert 'split(" ", 1)' in source


def test_interactive_define_qualidades():
    source = _interactive_source()
    assert '"q_string": "320"' in source
    assert '"q": 6' in source
    assert '"q": 7' in source
    assert '"q": 27' in source
