"""Qualidade do álbum vem da faixa de maior resolução, não da faixa 1.

Edição Deluxe com a faixa 1 só em CD e o resto em 24-bit fazia o formato, a
estimativa de espaço e o teste de qualidade saírem da faixa errada.
"""

import asyncio
import inspect

import pytest

from qobuz_dl import downloader

pytestmark = pytest.mark.unit


def _f(tid, bits=None, taxa=None, **extra):
    faixa = {"id": tid, **extra}
    if bits is not None:
        faixa["maximum_bit_depth"] = bits
    if taxa is not None:
        faixa["maximum_sampling_rate"] = taxa
    return faixa


def test_escolhe_a_faixa_de_maior_resolucao():
    faixas = [_f(1, 16, 44.1), _f(2, 24, 96), _f(3, 24, 192), _f(4, 24, 96)]
    assert downloader._faixa_de_maior_qualidade(faixas)["id"] == 3


def test_bits_pesam_mais_que_a_taxa():
    faixas = [_f(1, 16, 192), _f(2, 24, 44.1)]
    assert downloader._faixa_de_maior_qualidade(faixas)["id"] == 2


def test_empate_fica_com_a_primeira():
    faixas = [_f(1, 24, 96), _f(2, 24, 96)]
    assert downloader._faixa_de_maior_qualidade(faixas)["id"] == 1


def test_faixa_que_nao_toca_perde_para_a_que_toca():
    faixas = [_f(1, 24, 192, streamable=False), _f(2, 16, 44.1, streamable=True)]
    assert downloader._faixa_de_maior_qualidade(faixas)["id"] == 2


@pytest.mark.parametrize(
    "faixas",
    [
        [_f(1), _f(2)],  # sem metadado de resolução
        [_f(1, None, None), {"id": 2, "maximum_bit_depth": "?"}],
    ],
)
def test_sem_metadado_ou_com_lixo_fica_com_a_primeira(faixas):
    assert downloader._faixa_de_maior_qualidade(faixas)["id"] == 1


def test_item_que_nao_e_dict_nao_derruba():
    assert downloader._faixa_de_maior_qualidade([None, _f(2, 24, 96)])["id"] == 2


def test_get_format_sonda_a_faixa_escolhida_e_nao_a_primeira():
    sondadas = []

    class Cliente:
        async def get_track_url(self, track_id, fmt_id):
            sondadas.append(track_id)
            return {"bit_depth": 24, "sampling_rate": 96}

    dl = downloader.Download.__new__(downloader.Download)
    dl.client = Cliente()
    dl.quality = 7
    album = {"tracks": {"items": [_f(10, 16, 44.1), _f(11, 24, 96)]}}

    formato = asyncio.run(dl._get_format(album))

    assert sondadas == [11]
    assert formato == ("FLAC", True, 24, 96)


def test_faixa_avulsa_continua_usando_o_proprio_item():
    fonte = inspect.getsource(downloader.Download._get_format)
    assert "if is_track_id" in fonte and "_faixa_de_maior_qualidade" in fonte
