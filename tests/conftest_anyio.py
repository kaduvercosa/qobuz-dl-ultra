# conftest.py — adicionado para suportar @pytest.mark.anyio nos testes de rota
import pytest

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
