import asyncio
from types import SimpleNamespace

import pytest

import services.search as search_module


@pytest.mark.asyncio
async def test_search_context_cache_hit(monkeypatch):
    settings = SimpleNamespace(tavily_api_key="tavily-key", serper_api_key="", exa_api_key="")

    async def fake_cache_get(_key):
        return {"content": "cached"}

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    monkeypatch.setattr(search_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(search_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(search_module.config_module, "get_settings", lambda: settings)

    manager = search_module.SearchManager()
    result = await manager.get_search_context("cats")
    assert result == "cached"


def test_select_provider_visual_keyword(monkeypatch):
    monkeypatch.setattr(search_module.random, "random", lambda: 0.1)
    manager = search_module.SearchManager()
    assert manager._select_provider("image of cat", ["serper", "exa"]) == "serper"


@pytest.mark.asyncio
async def test_search_context_uses_only_configured_provider(monkeypatch):
    settings = SimpleNamespace(tavily_api_key="", serper_api_key="serper-key", exa_api_key="")

    async def fake_cache_get(_key):
        return None

    async def fake_cache_set(_key, _value, ttl=None):
        return True

    manager = search_module.SearchManager()

    async def fail_if_called(_query):
        raise AssertionError("non-configured provider should not be called")

    async def serper_ok(_query):
        return "serper-result"

    monkeypatch.setattr(search_module.config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(search_module, "cache_get", fake_cache_get)
    monkeypatch.setattr(search_module, "cache_set", fake_cache_set)
    monkeypatch.setattr(manager, "_search_tavily", fail_if_called)
    monkeypatch.setattr(manager, "_search_exa", fail_if_called)
    monkeypatch.setattr(manager, "_search_serper", serper_ok)

    result = await manager.get_search_context("topic")
    assert result == "serper-result"


@pytest.mark.asyncio
async def test_fallback_search_cancels_pending_tasks(monkeypatch):
    manager = search_module.SearchManager()
    cancellation = {"slow_cancelled": False}

    async def fast_ok(_query):
        return "fast-result"

    async def slow_task(_query):
        try:
            await asyncio.sleep(1)
            return "slow-result"
        except asyncio.CancelledError:
            cancellation["slow_cancelled"] = True
            raise

    monkeypatch.setattr(manager, "_search_tavily", fast_ok)
    monkeypatch.setattr(manager, "_search_serper", slow_task)

    result = await manager._fallback_search(
        "topic",
        failed_provider="exa",
        configured_providers=["tavily", "serper", "exa"],
    )

    assert result == "fast-result"
    assert cancellation["slow_cancelled"] is True


@pytest.mark.asyncio
async def test_search_serper_parsing_is_resilient_to_partial_payload(monkeypatch):
    settings = SimpleNamespace(tavily_api_key="", serper_api_key="serper-key", exa_api_key="")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"organic": [{}]}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return FakeResponse()

    manager = search_module.SearchManager()
    monkeypatch.setattr(search_module.config_module, "get_settings", lambda: settings)
    monkeypatch.setattr(search_module.httpx, "AsyncClient", FakeClient)

    result = await manager._search_serper("resilient parsing")
    assert "Untitled" in result
    assert "No snippet available." in result


@pytest.mark.asyncio
async def test_get_images_no_api_key(monkeypatch):
    settings = SimpleNamespace(tavily_api_key="", serper_api_key="", exa_api_key="")
    monkeypatch.setattr(search_module.config_module, "get_settings", lambda: settings)

    manager = search_module.SearchManager()
    images = await manager.get_images("topic")
    assert images == []
