import pytest
import services.cache as cache_module


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
