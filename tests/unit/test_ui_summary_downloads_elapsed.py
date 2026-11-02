"""`summary_downloads` só mostra "Tempo total" quando o tempo é informado."""

import pytest

from qobuz_dl import ui

pytestmark = pytest.mark.unit


@pytest.fixture
def chamadas_summary(monkeypatch):
    """Substitui `ui.summary` e guarda os argumentos recebidos."""
    recebidos = []

    def falso(title, rows, **kwargs):
        recebidos.append(kwargs)

    monkeypatch.setattr(ui, "summary", falso)
    return recebidos


def test_sem_elapsed_nao_passa_tempo_zero(chamadas_summary):
    """O padrão era 0: uma chamada sem o valor mostraria "Tempo total 0.0 s"."""
    ui.summary_downloads("DO ÁLBUM", "Disco", 3, 3)

    assert chamadas_summary[0]["elapsed"] is None


def test_elapsed_informado_e_repassado(chamadas_summary):
    ui.summary_downloads("DO ÁLBUM", "Disco", 3, 3, elapsed=12.5)

    assert chamadas_summary[0]["elapsed"] == 12.5


def test_elapsed_zero_explicito_ainda_e_repassado(chamadas_summary):
    ui.summary_downloads("DO ÁLBUM", "Disco", 3, 3, elapsed=0)

    assert chamadas_summary[0]["elapsed"] == 0
