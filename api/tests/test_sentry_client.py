import os
import types
import pytest

import config as config_module
import services.cache as cache_module
import services.sentry_client as sentry_client


@pytest.mark.asyncio
async def test_sentry_issues_cached(monkeypatch, dummy_redis):
    if hasattr(config_module.get_settings, "cache_clear"):
        config_module.get_settings.cache_clear()
    os.environ["SENTRY_AUTH_TOKEN"] = "token"

    async def _get_redis():
        return dummy_redis

    monkeypatch.setattr(cache_module, "get_redis", _get_redis)

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "id": "1",
                    "shortId": "KB-1",
                    "title": "Boom",
                    "permalink": "https://sentry.io/issue/1",
                    "count": "2",
                    "level": "error",
                    "firstSeen": "2024-01-01",
                    "lastSeen": "2024-01-02",
                    "status": "unresolved",
                }
            ]

    class DummyClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            return DummyResponse()

    monkeypatch.setattr(sentry_client.httpx, "AsyncClient", DummyClient)

    first = await sentry_client.fetch_sentry_issues(limit=5, cache_ttl_seconds=300)
    assert first and first[0]["title"] == "Boom"

    class FailingClient(DummyClient):
        async def get(self, *args, **kwargs):
            raise RuntimeError("should not be called")

    monkeypatch.setattr(sentry_client.httpx, "AsyncClient", FailingClient)
    second = await sentry_client.fetch_sentry_issues(limit=5, cache_ttl_seconds=300)
    assert second and second[0]["title"] == "Boom"
