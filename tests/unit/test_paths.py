"""Testes de cobertura total para qobuz_dl/paths.py."""

import os
import stat
from unittest.mock import patch

import pytest

from qobuz_dl.paths import (
    DirectoryNotUsable,
    ensure_directory_ready,
    resolve_library_directory,
)

# ---------------------------------------------------------------------------
# DirectoryNotUsable
# ---------------------------------------------------------------------------


def test_directory_not_usable_herda_de_value_error():
    assert issubclass(DirectoryNotUsable, ValueError)


def test_directory_not_usable_instancia_com_mensagem():
    exc = DirectoryNotUsable("pasta ruim")
    assert str(exc) == "pasta ruim"


# ---------------------------------------------------------------------------
# ensure_directory_ready — caminho feliz
# ---------------------------------------------------------------------------


def test_cria_pasta_que_nao_existe(tmp_path):
    nova = tmp_path / "nova" / "sub"
    resultado = ensure_directory_ready(str(nova))
    assert resultado == nova.resolve()
    assert nova.is_dir()


def test_retorna_path_absoluto_resolvido(tmp_path):
    resultado = ensure_directory_ready(str(tmp_path))
    assert resultado.is_absolute()
    assert resultado == tmp_path.resolve()


def test_pasta_ja_existente_retorna_path(tmp_path):
    resultado = ensure_directory_ready(str(tmp_path))
    assert resultado == tmp_path.resolve()


def test_expande_til(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(os.path, "expanduser", lambda p: p.replace("~", str(tmp_path)))
    destino = tmp_path / "musicas"
    resultado = ensure_directory_ready("~/musicas")
    assert resultado == destino.resolve()


def test_nao_deixa_arquivo_de_prova(tmp_path):
    ensure_directory_ready(str(tmp_path))
    arquivos_de_prova = list(tmp_path.glob(".qobuz-dl-write-test-*"))
    assert arquivos_de_prova == []


# ---------------------------------------------------------------------------
# ensure_directory_ready — caminho com arquivo no lugar da pasta
# ---------------------------------------------------------------------------


def test_levanta_quando_caminho_e_arquivo(tmp_path):
    arquivo = tmp_path / "sou_um_arquivo.txt"
    arquivo.write_text("conteúdo")
    with pytest.raises(DirectoryNotUsable, match="não é uma pasta"):
        ensure_directory_ready(str(arquivo))


# ---------------------------------------------------------------------------
# ensure_directory_ready — falha ao criar
# ---------------------------------------------------------------------------


def test_levanta_quando_mkdir_falha(tmp_path):
    caminho_impossivel = tmp_path / "fantasma" / "sub"
    with patch("qobuz_dl.paths.Path.mkdir", side_effect=OSError("sem permissão")):
        with pytest.raises(DirectoryNotUsable, match="não foi possível criar"):
            ensure_directory_ready(str(caminho_impossivel))


# ---------------------------------------------------------------------------
# ensure_directory_ready — falha ao escrever arquivo de prova
# ---------------------------------------------------------------------------


def test_levanta_quando_escrita_falha(tmp_path):
    with patch("qobuz_dl.paths.Path.write_text", side_effect=OSError("read-only")):
        with pytest.raises(
            DirectoryNotUsable, match="sem permissão de leitura/escrita"
        ):
            ensure_directory_ready(str(tmp_path))


# ---------------------------------------------------------------------------
# ensure_directory_ready — pasta somente-leitura real (POSIX)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name != "posix", reason="permissões POSIX necessárias")
def test_pasta_readonly_levanta_directory_not_usable(tmp_path):
    readonly = tmp_path / "readonly"
    readonly.mkdir()
    readonly.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x: sem escrita
    try:
        with pytest.raises(DirectoryNotUsable):
            ensure_directory_ready(str(readonly))
    finally:
        # restaura permissão para o cleanup do tmp_path funcionar
        readonly.chmod(stat.S_IRWXU)


# ---------------------------------------------------------------------------
# Idempotência
# ---------------------------------------------------------------------------


def test_chamar_duas_vezes_na_mesma_pasta_e_idempotente(tmp_path):
    r1 = ensure_directory_ready(str(tmp_path))
    r2 = ensure_directory_ready(str(tmp_path))
    assert r1 == r2


# ---------------------------------------------------------------------------
# OSError com strerror None (cobertura do ramo `or str(error)`)
# ---------------------------------------------------------------------------


def test_mkdir_oserror_sem_strerror_usa_str(tmp_path):
    erro = OSError("descrição do erro")
    erro.strerror = None  # força o ramo `str(error)` em vez de `error.strerror`
    destino = tmp_path / "novo_dir"
    with patch("qobuz_dl.paths.Path.mkdir", side_effect=erro):
        with pytest.raises(DirectoryNotUsable):
            ensure_directory_ready(str(destino))


def test_write_oserror_sem_strerror_usa_str(tmp_path):
    erro = OSError("erro de escrita")
    erro.strerror = None
    with patch("qobuz_dl.paths.Path.write_text", side_effect=erro):
        with pytest.raises(DirectoryNotUsable):
            ensure_directory_ready(str(tmp_path))


# ---------------------------------------------------------------------------
# resolve_library_directory -- precedência e compatibilidade iOS
# ---------------------------------------------------------------------------


def test_diretorio_explicito_tem_precedencia_sobre_config(tmp_path):
    explicit = tmp_path / "explicit"
    configured = tmp_path / "configured"
    result = resolve_library_directory(
        str(explicit), str(configured), home="", ios_home=""
    )
    assert result == str(explicit)


def test_caminho_relativo_comum_e_absolutizado(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = resolve_library_directory("FLAC", home="", ios_home="", windows=False)
    assert result == str(tmp_path / "FLAC")


def test_usa_diretorio_configurado_quando_nao_ha_argumento(tmp_path):
    configured = tmp_path / "biblioteca"
    result = resolve_library_directory(None, str(configured), home="", ios_home="")
    assert result == str(configured)


def test_caminho_relativo_ios_vai_para_documents_comportamento_lyrics(tmp_path):
    home = str(tmp_path / "Containers" / "Data" / "Application" / "APP")
    result = resolve_library_directory(
        None, "FLAC", home=home, ios_home="", windows=False
    )
    assert result == os.path.join(home, "Documents", "FLAC")


def test_caminho_ja_em_documents_ios_e_preservado(tmp_path):
    home = str(tmp_path / "Containers" / "Data" / "Application" / "APP")
    expected = os.path.join(home, "Documents", "Minha Biblioteca")
    result = resolve_library_directory(
        expected, "outra", home=home, ios_home="", windows=False
    )
    assert result == expected


def test_ios_home_aplica_base_antes_da_regra_documents(tmp_path):
    home = str(tmp_path / "Containers" / "Data" / "Application" / "APP")
    ios_home = str(tmp_path / "File Provider Storage")
    result = resolve_library_directory(
        None, "FLAC", home=home, ios_home=ios_home, windows=False
    )
    assert result == os.path.join(home, "Documents", "FLAC")


def test_windows_prefixo_extended_e_aplicado(tmp_path):
    result = resolve_library_directory(
        str(tmp_path), home="", ios_home="", windows=True
    )
    assert result.startswith("\\\\?\\")
