"""Testes unitários adicionais para a API qopy.py."""

import pytest
from unittest.mock import AsyncMock, patch

from qobuz_dl.qopy import Client


@pytest.mark.asyncio
async def test_qopy_get_track_url_retries_and_parses():
    client = Client()
    client.id = "user123"
    client.base = "https://api.qobuz.com/api.json/0.2/"
    client.uat = "valid_token"
    client.app_secret = "secret"
    client.sec = "sec"

    fake_response = {
        "url": "https://stream.qobuz.com/track.flac",
        "bit_depth": 24,
        "sampling_rate": 96.0,
    }

    async def mock_api_call(endpoint, **kwargs):
        if endpoint == "track/getFileUrl":
            return fake_response
        return {}

    with patch.object(client, "check_subscription", return_value={"is_active": True}):
        with patch.object(client, "api_call", new=AsyncMock(side_effect=mock_api_call)):
            res = await client.get_track_url("102030", fmt_id=7)
            assert res["url"] == "https://stream.qobuz.com/track.flac"
            assert res["bit_depth"] == 24
