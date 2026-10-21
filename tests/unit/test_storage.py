"""Testes da pré-verificação de armazenamento para dispositivos móveis."""

from types import SimpleNamespace

import pytest

from qobuz_dl.storage import (
    InsufficientStorageError,
    check_free_space,
    ensure_free_space,
    estimate_audio_bytes,
    format_bytes,
)

pytestmark = pytest.mark.unit


def test_estimativa_de_audio_cresce_com_a_duracao_e_qualidade():
    cd = estimate_audio_bytes(600, 6, bit_depth=16, sampling_rate=44100)
    hires = estimate_audio_bytes(600, 27, bit_depth=24, sampling_rate=192000)
    assert cd > 64 * 1024 * 1024
    assert hires > cd


def test_estimativa_invalida_nao_inventa_tamanho():
    assert estimate_audio_bytes(None, 27) == 0
    assert estimate_audio_bytes(0, 27) == 0
    assert estimate_audio_bytes("invalido", 27) == 0


def test_check_free_space_usa_o_filesystem_do_caminho(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "qobuz_dl.storage.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=60),
    )
    check = check_free_space(str(tmp_path), 50)
    assert check.free_bytes == 60
    assert check.enough is True


def test_ensure_free_space_falha_com_mensagem_util(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "qobuz_dl.storage.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=10),
    )
    with pytest.raises(InsufficientStorageError, match="Espaço insuficiente"):
        ensure_free_space(str(tmp_path), 20)


def test_format_bytes_tem_unidades_legiveis():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024 * 1024) == "1.0 MB"
