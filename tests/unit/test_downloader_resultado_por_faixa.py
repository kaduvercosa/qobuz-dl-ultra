"""Regressão: o resultado de cada faixa vem POR CHAMADA, não de um atributo.

BUG QUE ESTE ARQUIVO TRAVA
--------------------------
`_process_track` lia `self.last_download_outcome`, um atributo único do
objeto, e `_download_and_tag_impl` só o definia em alguns caminhos. Quando uma
faixa falhava num caminho que não o definia (abortar, faixa sem URL, falha na
verificação de integridade...), valia o resultado da faixa ANTERIOR: uma
falha depois de uma faixa baixada era reportada como "ok" e entrava na conta
como sucesso. Com downloads em paralelo, outra faixa também podia sobrescrever
o atributo no meio do caminho.

Agora `_download_and_tag` recebe `outcome_out` (um dict por chamada, como
`letras_out`) e qualquer retorno diferente de True conta como falha, exceto
quando a própria faixa registrou "skipped".
"""

import asyncio
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader
from qobuz_dl.downloader import Download

pytestmark = [pytest.mark.unit, pytest.mark.regression]


@pytest.fixture(autouse=True)
def abort_event_limpo():
    downloader.abort_event.clear()
    yield
    downloader.abort_event.clear()


def _build_self(download_and_tag):
    async def get_track_url(track_id, fmt_id):
        return {"sampling_rate": 44.1}

    return SimpleNamespace(
        quality=6,
        client=SimpleNamespace(get_track_url=get_track_url),
        _download_and_tag=download_and_tag,
    )


def _build_kwargs(tmp_path, report_track):
    return dict(
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=asyncio.Semaphore(4),
        report_track=report_track,
    )


def _faixa(n):
    return {"id": n, "track_number": n, "title": f"Faixa {n}", "streamable": True}


class _Relatorio:
    """Guarda (número da faixa, status) de cada `report_track`."""

    def __init__(self):
        self.itens = []

    async def __call__(self, i, t_num, status, motivo="", letras=None):
        self.itens.append((t_num, status))

    def status_de(self, t_num):
        return [s for n, s in self.itens if n == t_num]


async def test_falha_sem_resultado_registrado_depois_de_faixa_baixada(tmp_path):
    """O caso do bug: faixa 1 baixa; faixa 2 falha SEM registrar resultado."""
    chamadas = []

    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        chamadas.append(i["id"])
        if i["id"] == 1:
            kwargs["outcome_out"]["outcome"] = "downloaded"
            return True
        return False  # ex.: sem URL / abortada / integridade: nada registrado

    self = _build_self(download_and_tag)
    # Reproduz o estado "pegajoso" do antigo atributo compartilhado: mesmo que
    # ele exista e diga "downloaded", não pode mais influenciar a faixa 2.
    self.last_download_outcome = "downloaded"
    relatorio = _Relatorio()
    kwargs = _build_kwargs(tmp_path, relatorio)

    r1 = await Download._process_track(self, 0, _faixa(1), **kwargs)
    r2 = await Download._process_track(self, 1, _faixa(2), **kwargs)

    assert chamadas == [1, 2]
    assert r1 is True
    assert r2 is False
    assert relatorio.status_de("01") == ["ok"]
    assert relatorio.status_de("02") == ["falha"]


async def test_retorno_false_com_resultado_downloaded_inconsistente_e_falha(tmp_path):
    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        kwargs["outcome_out"]["outcome"] = "downloaded"
        return False

    relatorio = _Relatorio()
    result = await Download._process_track(
        _build_self(download_and_tag),
        0,
        _faixa(1),
        **_build_kwargs(tmp_path, relatorio),
    )

    assert result is False
    assert relatorio.status_de("01") == ["falha"]


async def test_skipped_continua_sendo_pulada_e_nao_falha(tmp_path):
    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        kwargs["outcome_out"]["outcome"] = "skipped"
        return False

    relatorio = _Relatorio()
    result = await Download._process_track(
        _build_self(download_and_tag),
        0,
        _faixa(1),
        **_build_kwargs(tmp_path, relatorio),
    )

    assert result == "skipped"
    assert relatorio.status_de("01") == ["pulada"]


async def test_arquivo_existente_continua_ok(tmp_path):
    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        kwargs["outcome_out"]["outcome"] = "existing"
        return True

    relatorio = _Relatorio()
    result = await Download._process_track(
        _build_self(download_and_tag),
        0,
        _faixa(1),
        **_build_kwargs(tmp_path, relatorio),
    )

    assert result == "existing"
    assert relatorio.status_de("01") == ["ok"]


async def test_sem_resultado_registrado_true_vira_downloaded(tmp_path):
    """Compatível com quem só devolve True (sem preencher `outcome_out`)."""

    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        return True

    relatorio = _Relatorio()
    result = await Download._process_track(
        _build_self(download_and_tag),
        0,
        _faixa(1),
        **_build_kwargs(tmp_path, relatorio),
    )

    assert result is True
    assert relatorio.status_de("01") == ["ok"]


async def test_faixas_em_paralelo_nao_misturam_resultados(tmp_path):
    """A faixa 1 (lenta) baixa; a 2 (rápida) falha sem registrar nada."""

    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        if i["id"] == 1:
            await asyncio.sleep(0.05)
            kwargs["outcome_out"]["outcome"] = "downloaded"
            return True
        await asyncio.sleep(0)
        return False

    self = _build_self(download_and_tag)
    relatorio = _Relatorio()
    kwargs = _build_kwargs(tmp_path, relatorio)

    r1, r2 = await asyncio.gather(
        Download._process_track(self, 0, _faixa(1), **kwargs),
        Download._process_track(self, 1, _faixa(2), **kwargs),
    )

    assert (r1, r2) == (True, False)
    assert relatorio.status_de("01") == ["ok"]
    assert relatorio.status_de("02") == ["falha"]
