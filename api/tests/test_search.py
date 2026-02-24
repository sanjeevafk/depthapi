import pytest

import services.search as search_module


@pytest.mark.asyncio
async def test_search_context_cache_hit(monkeypatch):
    async def fake_cache_get(_key):
        return {"content": "cached"}

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    monkeypatch.setattr(search_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(search_module, "cache_set", fake_cache_set)

    manager = search_module.SearchManager()
    result = await manager.get_search_context("cats")
    assert result == "cached"


def test_select_provider_visual_keyword(monkeypatch):
    monkeypatch.setattr(search_module.random, "random", lambda: 0.1)
    manager = search_module.SearchManager()
    assert manager._select_provider("image of cat") == "serper"


@pytest.mark.asyncio
async def test_search_context_fallback(monkeypatch):
    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    manager = search_module.SearchManager()

    async def fail(_query):
        raise RuntimeError("nope")

    async def ok(_query):
        return "fallback"

    monkeypatch.setattr(search_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(search_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(manager, "_search_tavily", fail)
    monkeypatch.setattr(manager, "_search_serper", ok)
    monkeypatch.setattr(manager, "_search_exa", fail)

    result = await manager.get_search_context("topic")
    assert result == "fallback"


@pytest.mark.asyncio
async def test_get_images_no_api_key(monkeypatch):
    manager = search_module.SearchManager()
    images = await manager.get_images("topic")
    assert images == []
