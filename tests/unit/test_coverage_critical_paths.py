"""Testes de regressão para ramificações críticas de CLI, download e App ID.

Sem chamadas à rede e sem autenticação real. Estes casos complementam a suíte
existente focando caminhos de erro/telemetria que podem regredir silenciosamente.
"""
from types import SimpleNamespace

import httpx
import pytest

from qobuz_dl import cli, downloader, qopy
from qobuz_dl.exceptions import NonStreamable

pytestmark = pytest.mark.unit


def _http_error(status: int, body: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.qobuz.com/api.json/0.2/test")
    response = httpx.Response(status, text=body, request=request)
    return httpx.HTTPStatusError("request rejected", request=request, response=response)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (400, "Invalid app_id", True),
        (401, "unknown application identifier", True),
        (403, "Application ID is invalid", True),
        (400, "Bad request", False),
        (401, "Invalid username or password", False),
        (403, "Region not allowed", False),
        (500, "invalid app_id", False),
    ],
)
def test_app_id_rejection_requires_status_and_explicit_marker(status, body, expected):
    assert qopy._is_app_id_rejection(_http_error(status, body)) is expected


def test_startup_timing_nao_emite_log_quando_desativado(monkeypatch, caplog):
    monkeypatch.delenv("QOBUZ_DL_STARTUP_TIMING", raising=False)
    with caplog.at_level("WARNING", logger=cli.__name__):
        cli._startup_timing("teste", 0.0)
    assert "[startup-timing]" not in caplog.text


def test_startup_timing_sem_inicio_registra_apenas_nome_da_etapa(monkeypatch, caplog):
    monkeypatch.setenv("QOBUZ_DL_STARTUP_TIMING", "yes")
    with caplog.at_level("WARNING", logger=cli.__name__):
        cli._startup_timing("etapa de teste")
    assert "[startup-timing] etapa de teste" in caplog.text
    assert ":" not in caplog.text.split("etapa de teste", 1)[-1]


def test_startup_timing_com_inicio_registra_duracao(monkeypatch, caplog):
    monkeypatch.setenv("QOBUZ_DL_STARTUP_TIMING", "1")
    monkeypatch.setattr(cli.time, "perf_counter", lambda: 10.125)
    with caplog.at_level("WARNING", logger=cli.__name__):
        cli._startup_timing("cliente HTTP", 10.0)
    assert "[startup-timing] cliente HTTP: 0.125s" in caplog.text


def test_get_format_rejeita_lancamento_sem_faixas():
    obj = SimpleNamespace()
    with pytest.raises(NonStreamable, match="nao tem faixas disponiveis"):
        # Chama o método sem construir a classe Download ou criar sessão HTTP.
        import asyncio
        asyncio.run(downloader.Download._get_format(obj, {"tracks": {"items": []}}))


def test_get_format_reconhece_downgrade_de_qualidade():
    async def get_track_url(track_id, fmt_id):
        assert (track_id, fmt_id) == ("track-1", 27)
        return {
            "bit_depth": 24,
            "sampling_rate": 96,
            "restrictions": [{"code": downloader.QL_DOWNGRADE}],
        }

    obj = SimpleNamespace(client=SimpleNamespace(get_track_url=get_track_url), quality=27)
    import asyncio
    result = asyncio.run(
        downloader.Download._get_format(obj, {"tracks": {"items": [{"id": "track-1"}]}})
    )
    assert result == ("FLAC", False, 24, 96)


def test_get_format_mp3_sem_restricao():
    async def get_track_url(track_id, fmt_id):
        return {"bit_depth": 16, "sampling_rate": 44.1, "restrictions": []}

    obj = SimpleNamespace(client=SimpleNamespace(get_track_url=get_track_url), quality=5)
    import asyncio
    result = asyncio.run(
        downloader.Download._get_format(
            obj, {"id": "track-2"}, is_track_id=True
        )
    )
    assert result == ("MP3", True, 16, 44.1)


@pytest.mark.parametrize("mode", ["empty_response", "exception", "missing_metadata"])
def test_get_format_falha_de_metadados_retorna_unknown(mode):
    async def get_track_url(track_id, fmt_id):
        if mode == "exception":
            raise httpx.ConnectError("offline")
        if mode == "empty_response":
            return {}
        return {"sampling_rate": 96}

    obj = SimpleNamespace(client=SimpleNamespace(get_track_url=get_track_url), quality=7)
    import asyncio
    result = asyncio.run(
        downloader.Download._get_format(obj, {"id": "track-3"}, is_track_id=True)
    )
    assert result == ("Unknown", True, None, None)
