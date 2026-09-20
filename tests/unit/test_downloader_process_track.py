"""Testes de `Download._process_track` (qobuz_dl/downloader.py).

Esse método foi extraído de dentro de `download_release()` -- antes era
uma função aninhada `process_track` que fechava sobre variáveis locais
do método (`dirn`, `album_meta`, `semaphore`, etc.) e sobre outra função
aninhada (`_report_track`), o que tornava impossível chamar essa lógica
sem rodar `download_release()` inteiro (metadados do álbum, formato,
diretórios, handler de SIGINT, capa...). Ver a docstring do método em
`qobuz_dl/downloader.py` pra mais contexto da extração.

Os testes abaixo cobrem os 4 desfechos de UMA faixa dentro do álbum:
abortada (CTRL+C), pulada (não-streamable OU URL retornada é só
amostra), falha de API, e sucesso.
"""

import asyncio
from types import SimpleNamespace

import pytest

from qobuz_dl import downloader
from qobuz_dl.downloader import Download

pytestmark = pytest.mark.unit


@pytest.fixture
def abort_event_limpo():
    """`abort_event` é um `threading.Event` GLOBAL do módulo (compartilhado
    entre downloads reais) -- garante que nenhum teste vaza esse estado
    pro próximo, nem entrando nem saindo."""
    downloader.abort_event.clear()
    yield downloader.abort_event
    downloader.abort_event.clear()


def _build_self(**overrides):
    self = SimpleNamespace(quality=6, client=SimpleNamespace())
    for key, value in overrides.items():
        setattr(self, key, value)
    return self


def _build_kwargs(tmp_path, report_track, **overrides):
    kwargs = dict(
        dirn=str(tmp_path),
        album_meta={},
        is_multiple=False,
        is_parallel=False,
        position_pool=None,
        semaphore=asyncio.Semaphore(1),
        report_track=report_track,
    )
    kwargs.update(overrides)
    return kwargs


async def test_process_track_aborta_sem_chamar_report(abort_event_limpo, tmp_path):
    """abort_event já setado (CTRL+C no meio do álbum): retorna False
    na hora, sem chamar a API nem o report_track."""
    abort_event_limpo.set()
    chamadas = []

    async def report_track(*args, **kwargs):
        chamadas.append((args, kwargs))

    self = _build_self()
    kwargs = _build_kwargs(tmp_path, report_track)

    result = await Download._process_track(self, 0, {"id": 1}, **kwargs)

    assert result is False
    assert chamadas == []


async def test_process_track_pula_faixa_nao_streamable(abort_event_limpo, tmp_path):
    """Faixa bloqueada (sem streamable/sampleable/purchasable): pula,
    cria o .missing.txt e reporta 'pulada' com o motivo do bloqueio."""
    chamadas = []

    async def report_track(i, t_num, status, motivo="", letras=None):
        chamadas.append((t_num, status, motivo))

    self = _build_self()
    kwargs = _build_kwargs(tmp_path, report_track)
    faixa = {"id": 1, "track_number": 3, "title": "Bloqueada", "streamable": False}

    result = await Download._process_track(self, 2, faixa, **kwargs)

    assert result == "skipped"
    assert len(chamadas) == 1
    t_num, status, _motivo = chamadas[0]
    assert t_num == "03"
    assert status == "pulada"
    assert len(list(tmp_path.glob("*.missing.txt"))) == 1


async def test_process_track_erro_de_api_marca_falha_sem_propagar(
    abort_event_limpo, tmp_path
):
    """`client.get_track_url` explode (erro de API/rede): não pode
    propagar -- vira placeholder + report 'falha' + retorno False, pra
    o `asyncio.gather` do álbum não derrubar as outras faixas por causa
    de uma só."""
    chamadas = []

    async def report_track(i, t_num, status, motivo="", letras=None):
        chamadas.append((t_num, status, motivo))

    async def get_track_url(track_id, fmt_id):
        raise RuntimeError("timeout simulado")

    self = _build_self(client=SimpleNamespace(get_track_url=get_track_url))
    kwargs = _build_kwargs(tmp_path, report_track)
    faixa = {"id": 1, "track_number": 1, "title": "Com erro", "streamable": True}

    result = await Download._process_track(self, 0, faixa, **kwargs)

    assert result is False
    assert chamadas[0][1] == "falha"
    assert "timeout simulado" in chamadas[0][2]
    assert len(list(tmp_path.glob("*.missing.txt"))) == 1


async def test_process_track_amostra_e_pulada_mesmo_com_streamable_true(
    abort_event_limpo, tmp_path
):
    """`get_track_url` devolve só amostra: pula igual, mesmo que o
    METADADO original da faixa diga streamable=True -- quem decide
    aqui é a URL de fato retornada pela API, não o metadado prévio."""
    chamadas = []

    async def report_track(i, t_num, status, motivo="", letras=None):
        chamadas.append((t_num, status, motivo))

    async def get_track_url(track_id, fmt_id):
        return {"sample": True, "sampling_rate": 44.1}

    self = _build_self(client=SimpleNamespace(get_track_url=get_track_url))
    kwargs = _build_kwargs(tmp_path, report_track)
    faixa = {"id": 1, "track_number": 1, "title": "Amostra", "streamable": True}

    result = await Download._process_track(self, 0, faixa, **kwargs)

    assert result == "skipped"
    assert chamadas[0][1] == "pulada"
    assert "amostra" in chamadas[0][2].lower()


async def test_process_track_sucesso_chama_download_and_tag_e_reporta_ok(
    abort_event_limpo, tmp_path
):
    """Caminho feliz: URL de stream de verdade (sem 'sample', com
    sampling_rate) -- chama `_download_and_tag` e reporta 'ok' quando
    ele devolve True. Não pode sobrar .missing.txt nenhum."""
    chamadas = []
    estado = {}

    async def report_track(i, t_num, status, motivo="", letras=None):
        chamadas.append((t_num, status, motivo))

    async def get_track_url(track_id, fmt_id):
        return {"sampling_rate": 44.1}

    async def download_and_tag(dirn, idx, parse, i, album_meta, *args, **kwargs):
        estado["chamado"] = True
        return True

    self = _build_self(
        client=SimpleNamespace(get_track_url=get_track_url),
        _download_and_tag=download_and_tag,
    )
    kwargs = _build_kwargs(tmp_path, report_track)
    faixa = {"id": 1, "track_number": 1, "title": "Boa faixa", "streamable": True}

    result = await Download._process_track(self, 0, faixa, **kwargs)

    assert result is True
    assert estado.get("chamado") is True
    assert chamadas[0][1] == "ok"
    assert len(list(tmp_path.glob("*.missing.txt"))) == 0
