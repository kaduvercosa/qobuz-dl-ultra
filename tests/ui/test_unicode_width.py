"""Regressões para dimensionamento por células visuais no terminal."""

import pytest

from qobuz_dl import ui


@pytest.mark.parametrize(
    "text,limit,expected",
    [
        ("abc", 3, "abc"),
        ("界界", 4, "界界"),
        ("界界界", 5, "界..."),
        ("🙂🙂🙂", 5, "🙂..."),
    ],
)
def test_truncate_considera_largura_visual(text, limit, expected):
    assert ui.truncate(text, limit) == expected
    assert ui._cell_width(ui.truncate(text, limit)) <= limit


def test_wrap_quebra_texto_cjk_pela_largura_visual():
    lines = ui._wrap_lines("界界界界界界界 界界界界界界界", 12)
    assert lines == ["界界界界界界", "界", "界界界界界界", "界"]
    assert all(ui._cell_width(line) <= 12 for line in lines)


def test_wrap_divide_token_unico_longo_sem_estourar_largura_visual():
    lines = ui._wrap_lines("界" * 8, 12)
    assert lines == ["界" * 6, "界" * 2]
    assert all(ui._cell_width(line) <= 12 for line in lines)
