"""Formatos de tempo, tamanho e velocidade usados nos resumos."""

import pytest

from qobuz_dl import ui

pytestmark = pytest.mark.unit


class TestFormatElapsed:
    def test_abaixo_de_um_minuto(self):
        assert ui.format_elapsed(0.04) == "0.0 s"
        assert ui.format_elapsed(8.4) == "8.4 s"

    def test_minutos_e_horas(self):
        assert ui.format_elapsed(102.3) == "1 min 42 s"
        assert ui.format_elapsed(68) == "1 min 08 s"
        assert ui.format_elapsed(3725) == "1 h 02 min"

    def test_arredondamento_na_virada_do_minuto(self):
        assert ui.format_elapsed(59.96) == "1 min 00 s"

    def test_negativo_vira_zero(self):
        assert ui.format_elapsed(-3) == "0.0 s"


class TestFormatClock:
    def test_minutos_e_horas(self):
        assert ui.format_clock(369) == "06:09"
        assert ui.format_clock(4464) == "01:14:24"

    def test_sem_dado_devolve_vazio(self):
        for valor in (0, None, "x", -5):
            assert ui.format_clock(valor) == ""


class TestFormatBytes:
    def test_escalas(self):
        assert ui.format_bytes(0) == "0 B"
        assert ui.format_bytes(850) == "850 B"
        assert ui.format_bytes(12_300) == "12.3 KB"
        assert ui.format_bytes(3_100_000) == "3.1 MB"
        assert ui.format_bytes(312_000_000) == "312 MB"
        assert ui.format_bytes(2_500_000_000) == "2.5 GB"

    def test_velocidade(self):
        assert ui.format_speed(312_000_000, 102) == "3.1 MB/s"
        assert ui.format_speed(1, 0) == ""
        assert ui.format_speed("x", 5) == ""
