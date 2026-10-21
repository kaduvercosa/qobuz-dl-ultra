import asyncio

from qobuz_dl.metadata_cache import MetadataCache, cache_key


def test_cache_key_ignora_assinatura_e_timestamp():
    a = cache_key("album/get", {"album_id": "1", "request_ts": 1, "request_sig": "a"})
    b = cache_key("album/get", {"album_id": "1", "request_ts": 2, "request_sig": "b"})
    assert a == b


def test_cache_persiste_json_e_respeita_ttl(tmp_path):
    async def scenario():
        cache = MetadataCache(tmp_path / "cache.sqlite3", ttl=60, max_entries=32)
        await cache.set("album", {"title": "Teste"})
        assert await cache.get("album") == {"title": "Teste"}

        expired = MetadataCache(tmp_path / "expired.sqlite3", ttl=1, max_entries=32)
        await expired.set("x", {"value": 1})
        async with __import__("aiosqlite").connect(expired.path) as conn:
            await conn.execute("UPDATE metadata_cache SET created_at=0")
            await conn.commit()
        assert await expired.get("x") is None

    asyncio.run(scenario())


def test_cache_nao_serializa_payload_invalido(tmp_path):
    async def scenario():
        cache = MetadataCache(tmp_path / "cache.sqlite3")
        await cache.set("bad", {"value": object()})
        assert await cache.get("bad") is None

    asyncio.run(scenario())
