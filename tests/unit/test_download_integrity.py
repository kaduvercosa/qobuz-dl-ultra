"""Regressões dos caminhos de transporte e montagem de áudio."""

import asyncio
import os

import httpx
import pytest
from tenacity import wait_none

from qobuz_dl import downloader


pytestmark = pytest.mark.unit


class _FalhaDepoisDoPrimeiroChunk(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"a" * 524288
        raise httpx.ReadError("conexão interrompida")


@pytest.mark.asyncio
async def test_retomada_reinicia_quando_cdn_ignora_range(tmp_path, monkeypatch):
    chamadas = 0
    parcial = b"a" * 524288
    completo = parcial + b"defghi"

    def handler(request):
        nonlocal chamadas
        chamadas += 1
        if chamadas == 1:
            return httpx.Response(
                200,
                headers={"content-length": str(len(completo))},
                stream=_FalhaDepoisDoPrimeiroChunk(),
            )
        assert request.headers["range"] == f"bytes={len(parcial)}-"
        return httpx.Response(
            200,
            headers={"content-length": str(len(completo))},
            content=completo,
        )

    monkeypatch.setattr(downloader, "wait_exponential", lambda **_kwargs: wait_none())
    destino = tmp_path / "faixa.tmp"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        await downloader.tqdm_download(
            "https://cdn.invalid/faixa",
            str(destino),
            "Faixa",
            is_parallel=True,
            session=session,
        )

    assert chamadas == 2
    assert destino.read_bytes() == completo


@pytest.mark.asyncio
async def test_416_sem_tamanho_usa_total_ja_conhecido(tmp_path, monkeypatch):
    chamadas = 0
    completo = b"a" * 524288

    def handler(_request):
        nonlocal chamadas
        chamadas += 1
        if chamadas == 1:
            return httpx.Response(
                200,
                headers={"content-length": str(len(completo))},
                stream=_FalhaDepoisDoPrimeiroChunk(),
            )
        return httpx.Response(416)

    monkeypatch.setattr(downloader, "wait_exponential", lambda **_kwargs: wait_none())
    destino = tmp_path / "faixa.tmp"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as session:
        await downloader.tqdm_download(
            "https://cdn.invalid/faixa",
            str(destino),
            "Faixa",
            is_parallel=True,
            session=session,
        )

    assert chamadas == 2
    assert destino.read_bytes() == completo


@pytest.mark.asyncio
async def test_segmentos_iniciais_sao_gravados_e_memoria_e_limitada(
    tmp_path, monkeypatch
):
    segmentos = {i: f"s{i}".encode() for i in range(6)}
    gets_ativos = 0
    pico_gets = 0

    async def handler(request):
        nonlocal gets_ativos, pico_gets
        numero = int(request.url.path.rsplit("/", 1)[-1])
        if request.method == "HEAD":
            return httpx.Response(200, headers={"content-length": "2"})
        gets_ativos += 1
        pico_gets = max(pico_gets, gets_ativos)
        try:
            await asyncio.sleep(0)
            return httpx.Response(200, content=segmentos[numero])
        finally:
            gets_ativos -= 1

    conteudo_remux = None

    class _ProcessoFalso:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def subprocess_falso(*args, **_kwargs):
        nonlocal conteudo_remux
        entrada = args[args.index("-i") + 1]
        conteudo_remux = open(entrada, "rb").read()
        return _ProcessoFalso()

    monkeypatch.setattr(downloader, "_get_qobuz_segment_uuid", lambda _data: b"u" * 16)
    monkeypatch.setattr(
        downloader,
        "_decrypt_qobuz_segment",
        lambda data, _key, uuid: bytes(data) if uuid is None else b"d" + bytes(data),
    )
    monkeypatch.setattr(downloader.asyncio, "create_subprocess_exec", subprocess_falso)

    destino = tmp_path / "faixa.flac"
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as session:
        await downloader.tqdm_download_segments(
            {
                "n_segments": 5,
                "url_template": "https://cdn.invalid/$SEGMENT$",
                "raw_key": b"k" * 16,
            },
            str(destino),
            "Faixa",
            is_parallel=True,
            session=session,
            segment_workers=2,
        )

    assert conteudo_remux == b"s0ds1ds2ds3ds4ds5"
    assert pico_gets <= 2
    assert not os.path.exists(f"{destino}.mp4")
