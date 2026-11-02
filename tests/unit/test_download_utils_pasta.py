"""Utilitários de pasta do download: tamanho de áudio e limpeza de .missing.txt."""

import os

import pytest

from qobuz_dl import download_utils

pytestmark = pytest.mark.unit


class TestAudioBytes:
    def test_soma_so_audio_incluindo_subpastas(self, tmp_path):
        (tmp_path / "01.flac").write_bytes(b"a" * 100)
        (tmp_path / "02.mp3").write_bytes(b"b" * 50)
        (tmp_path / "cover.jpg").write_bytes(b"c" * 999)
        (tmp_path / "03.flac.part").write_bytes(b"d" * 999)
        (tmp_path / "CD2").mkdir()
        (tmp_path / "CD2" / "01.FLAC").write_bytes(b"e" * 10)
        assert download_utils.audio_bytes(str(tmp_path)) == 160

    def test_pasta_inexistente_devolve_zero(self, tmp_path):
        assert download_utils.audio_bytes(str(tmp_path / "nao_existe")) == 0


class TestRemoveMissingPlaceholder:
    def test_remove_so_o_da_faixa_pedida(self, tmp_path):
        alvo = tmp_path / "03. Giorgio by Moroder [INDISPONÍVEL].missing.txt"
        outro = tmp_path / "07. Fragments of Time [INDISPONÍVEL].missing.txt"
        alvo.write_text("x", encoding="utf-8")
        outro.write_text("x", encoding="utf-8")
        assert download_utils.remove_missing_placeholder(
            str(tmp_path), "Giorgio by Moroder"
        )
        assert sorted(os.listdir(tmp_path)) == [outro.name]

    def test_sem_placeholder_devolve_false(self, tmp_path):
        assert not download_utils.remove_missing_placeholder(str(tmp_path), "Nada")
        assert not download_utils.remove_missing_placeholder(
            str(tmp_path / "nao_existe"), "Nada"
        )

    def test_titulo_com_barra_segue_a_regra_do_criador(self, tmp_path):
        arquivo = tmp_path / "01. AC-DC [INDISPONÍVEL].missing.txt"
        arquivo.write_text("x", encoding="utf-8")
        assert download_utils.remove_missing_placeholder(str(tmp_path), "AC/DC")
