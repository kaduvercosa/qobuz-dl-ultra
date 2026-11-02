"""Resultado de cada faixa do álbum: uma falha não herda o resultado da anterior.

O resultado viaja num dict `outcome_out`, criado POR CHAMADA de
`_download_and_tag` (não existe mais um atributo único no objeto). A decisão
final (downloaded/existing/skipped/failed) está em
`test_downloader_resultado_por_faixa.py`; aqui ficam as duas pontas do dict:
o wrapper repassa o dict da chamada e a implementação o limpa ao começar.
"""

import types

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


class _Parou(Exception):
    """Interrompe a implementação logo depois de ela limpar o `outcome_out`."""


class TestWrapperRepassaOResultadoDaChamada:
    async def test_download_and_tag_repassa_o_mesmo_dict_para_a_implementacao(
        self, tmp_path
    ):
        visto = {}

        async def impl(*args, **kwargs):
            visto["outcome_out"] = kwargs.get("outcome_out")
            return False  # falha sem registrar resultado

        obj = types.SimpleNamespace(_download_and_tag_impl=impl)
        resultado_info: dict[str, str] = {}
        track = {"title": "Faixa", "track_number": 2}
        album = {"tracks_count": 3, "media_count": 1}

        result = await downloader.Download._download_and_tag(
            obj,
            str(tmp_path),
            1,
            {},
            track,
            album,
            False,
            False,
            outcome_out=resultado_info,
        )

        assert result is False
        assert visto["outcome_out"] is resultado_info
        # Falha sem registro: o dict continua vazio, o chamador a conta como falha.
        assert resultado_info == {}

    async def test_download_and_tag_nao_cria_atributo_compartilhado(self, tmp_path):
        async def impl(*args, **kwargs):
            return True

        obj = types.SimpleNamespace(_download_and_tag_impl=impl)
        track = {"title": "Faixa", "track_number": 1}
        album = {"tracks_count": 1, "media_count": 1}

        await downloader.Download._download_and_tag(
            obj, str(tmp_path), 1, {}, track, album, False, False
        )

        assert not hasattr(obj, "last_download_outcome")


class TestImplementacaoLimpaOResultadoAnterior:
    async def test_impl_zera_outcome_out_ao_comecar(self, tmp_path):
        """Um `outcome_out` reaproveitado não pode carregar o resultado antigo."""

        def interrompe(*args, **kwargs):
            raise _Parou

        # `_get_filename_attr` é a primeira coisa que a implementação usa de
        # `self` depois de limpar o dict: serve de ponto de parada.
        obj = types.SimpleNamespace(_get_filename_attr=interrompe)
        resultado_info = {"outcome": "downloaded"}

        with pytest.raises(_Parou):
            await downloader.Download._download_and_tag_impl(
                obj,
                str(tmp_path),
                1,
                {},
                {"title": "Faixa", "track_number": 2},
                {"tracks_count": 3, "media_count": 1},
                False,
                False,
                outcome_out=resultado_info,
            )

        assert "outcome" not in resultado_info
