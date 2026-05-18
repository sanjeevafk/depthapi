import asyncio
from types import SimpleNamespace

import pytest
import api.services.infra.cache as cache_module


@pytest.mark.asyncio
async def test_cache_set_and_get(monkeypatch):
    store = {}

    class LocalRedis:
        async def get(self, key):
            return store.get(key)

        async def setex(self, key, _ttl, value):
            store[key] = value

    async def fake_get_redis():
        return LocalRedis()

    monkeypatch.setattr(cache_module, "get_redis", fake_get_redis)

    payload = {"text": "hello"}
    assert await cache_module.cache_set("k1", payload, ttl=10) is True
    value = await cache_module.cache_get("k1")
    assert value == payload


@pytest.mark.asyncio
async def test_cache_get_failure(monkeypatch):
    async def bad_get_redis():
        raise RuntimeError("boom")

    monkeypatch.setattr(cache_module, "get_redis", bad_get_redis)
    assert await cache_module.cache_get("missing") is None


@pytest.mark.asyncio
async def test_cache_set_failure(monkeypatch):
    async def bad_get_redis():
        raise RuntimeError("boom")

    monkeypatch.setattr(cache_module, "get_redis", bad_get_redis)
    assert await cache_module.cache_set("k", {"x": 1}) is False


@pytest.mark.asyncio
async def test_get_redis_singleton_is_lock_safe(monkeypatch):
    class FakeRedisClient:
        instances = 0

        def __init__(self, *, base_url, token):
            assert base_url
            assert token
            type(self).instances += 1

        async def close(self):
            return None

    monkeypatch.setattr(
        cache_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstash_redis_rest_url="https://example.upstash.io",
            upstash_redis_rest_token="token",
        ),
    )
    monkeypatch.setattr(cache_module, "UpstashRedisCompat", FakeRedisClient)
    monkeypatch.setattr(cache_module, "_client", None)

    clients = await asyncio.gather(*(cache_module.get_redis() for _ in range(25)))

    assert all(client is clients[0] for client in clients)
    assert FakeRedisClient.instances == 1


@pytest.mark.asyncio
async def test_get_redis_uses_local_redis_when_upstash_is_unset(monkeypatch):
    class FakeLocalRedisClient:
        instances = 0

        def __init__(self, url):
            assert url == "redis://redis:6379/0"
            type(self).instances += 1

        async def close(self):
            return None

    monkeypatch.setattr(
        cache_module,
        "get_settings",
        lambda: SimpleNamespace(
            upstash_redis_rest_url="",
            upstash_redis_rest_token="",
            redis_url="redis://redis:6379/0",
        ),
    )
    monkeypatch.setattr(cache_module, "LocalRedisCompat", FakeLocalRedisClient)
    monkeypatch.setattr(cache_module, "_client", None)

    client = await cache_module.get_redis()

    assert isinstance(client, FakeLocalRedisClient)
    assert FakeLocalRedisClient.instances == 1


@pytest.mark.asyncio
async def test_close_redis_is_lock_safe_under_parallel_calls(monkeypatch):
    class FakeRedisClient:
        close_calls = 0

        async def close(self):
            await asyncio.sleep(0.01)
            type(self).close_calls += 1

    monkeypatch.setattr(cache_module, "_client", FakeRedisClient())

    await asyncio.gather(*(cache_module.close_redis() for _ in range(20)))

    assert cache_module._client is None
    assert FakeRedisClient.close_calls == 1
