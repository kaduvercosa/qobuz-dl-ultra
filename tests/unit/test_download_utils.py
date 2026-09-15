"""Testes unitários para qobuz_dl.download_utils."""

from pathlib import PurePath

import pytest

from qobuz_dl import download_utils as du

pytestmark = pytest.mark.unit


def _portable_path(value):
    """Converte separadores nativos para uma representação comparável."""
    return PurePath(str(value).replace("\\", "/"))


class TestProcessFolderFormatComSubdirs:
    def test_parte_que_sanitiza_para_vazio_e_ignorada(self):
        resultado = du.process_folder_format_with_subdirs(
            "normal/ /outro", {}
        )

        assert _portable_path(resultado) == PurePath("normal/outro")

    def test_chave_de_formato_inexistente_cai_no_except_e_usa_texto_original(self):
        resultado = du.process_folder_format_with_subdirs(
            "{artista}/{nao_existe}", {"artista": "Artista X"}
        )

        assert _portable_path(resultado) == PurePath("Artista X/{nao_existe}")

    def test_texto_original_muito_longo_no_except_tambem_e_truncado(self):
        parte_longa = "{nao_existe}" + "a" * 150
        resultado = du.process_folder_format_with_subdirs(parte_longa, {})

        assert "..." in str(resultado)
        assert len(str(resultado)) < len(parte_longa)


class TestArtistLabel:
    def test_get_album_artist_explodindo_cai_no_fallback(self, monkeypatch):
        def _explode(item_dict):
            raise RuntimeError("erro inesperado de metadata")

        monkeypatch.setattr(du, "get_album_artist", _explode)

        assert du._artist_label({"qualquer": 1}, fallback="Fallback X") == "Fallback X"
