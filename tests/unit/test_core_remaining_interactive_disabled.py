"""Testes desabilitados no iOS: aguardam refatoração da entrada interativa."""

import pytest

pytestmark = [
    pytest.mark.unit,
    pytest.mark.skip(
        reason="interactive() depende de prompt_toolkit e usa formatos interativos reais; "
        "cobertura temporariamente feita por teste estático no iOS",
    ),
]


def test_interactive_artista_catalogo_albuns():
    pass


def test_interactive_artista_sem_catalogo():
    pass


def test_interactive_busca_novamente():
    pass
