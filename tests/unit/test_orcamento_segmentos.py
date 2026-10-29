"""segment_workers padrão: orçamento de conexões dividido entre as faixas.

Colocar em tests/unit/.
"""

import configparser
from types import SimpleNamespace

import pytest

from qobuz_dl.settings import (
    SEGMENT_CONNECTION_BUDGET,
    QobuzDLSettings,
    segment_workers_default,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("cpus", range(1, 17))
def test_sem_paralelismo_continua_como_antes(cpus):
    antigo = min(8, max(2, cpus * 2))
    assert segment_workers_default(1, cpu_count=cpus) == antigo


@pytest.mark.parametrize(
    "max_workers, esperado",
    [(1, 8), (2, 8), (3, 5), (4, 4), (5, 3), (8, 2), (16, 2)],
)
def test_valores_com_cpus_suficientes(max_workers, esperado):
    assert segment_workers_default(max_workers, cpu_count=8) == esperado


@pytest.mark.parametrize("max_workers", range(1, 17))
@pytest.mark.parametrize("cpus", [1, 2, 4, 8])
def test_total_de_conexoes_fica_dentro_do_orcamento(max_workers, cpus):
    por_faixa = segment_workers_default(max_workers, cpu_count=cpus)
    assert por_faixa >= 2
    assert por_faixa == 2 or max_workers * por_faixa <= SEGMENT_CONNECTION_BUDGET


def test_nunca_passa_do_padrao_antigo():
    for cpus in range(1, 17):
        antigo = min(8, max(2, cpus * 2))
        for max_workers in range(1, 17):
            assert segment_workers_default(max_workers, cpu_count=cpus) <= antigo


@pytest.mark.parametrize("lixo", [None, "x", 0, -3])
def test_max_workers_invalido_conta_como_um(lixo):
    assert segment_workers_default(lixo, cpu_count=4) == segment_workers_default(
        1, cpu_count=4
    )


# ------------------------------------------------------------ configurações
def _settings(**valores):
    config = configparser.ConfigParser(interpolation=None)
    config["qobuz"] = valores
    return QobuzDLSettings.from_arguments_configparser(SimpleNamespace(), config)


def test_settings_sem_valor_explicito_usa_a_divisao():
    s = QobuzDLSettings(max_workers=5)
    assert s.segment_workers == segment_workers_default(5)


def test_settings_valor_explicito_nao_e_alterado():
    s = QobuzDLSettings(max_workers=5, segment_workers=8)
    assert s.segment_workers == 8


def test_config_ini_sem_segment_workers_usa_a_divisao():
    s = _settings(max_workers="5")
    assert s.max_workers == 5
    assert s.segment_workers == segment_workers_default(5)


def test_config_ini_com_segment_workers_explicito_vale():
    s = _settings(max_workers="5", segment_workers="6")
    assert s.segment_workers == 6
