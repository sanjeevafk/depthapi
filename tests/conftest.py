"""Shared fixtures: keep query tests hermetic (no real Redis)."""
from __future__ import annotations

import pytest

from api.services import cache as query_cache


class FakeRedis:
    """Minimal in-memory stand-in for the Redis surface query_cache uses."""

    def __init__(self):
        self.store: dict[str, str] = {}

    def ping(self):
        return True

    def get(self, key: str):
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None):
        self.store[key] = value
        return True

    def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        return True

    def incrby(self, key: str, amount: int):
        self.store[key] = str(int(self.store.get(key, "0")) + amount)
        return int(self.store[key])

    def expire(self, key: str, ttl: int):
        return True


@pytest.fixture(autouse=True)
def _isolated_query_cache(monkeypatch):
    """Every test gets a fresh fake Redis; prod code paths unchanged."""
    fake = FakeRedis()
    monkeypatch.setattr(query_cache, "get_client", lambda: fake)
    query_cache.reset_client()
    return fake
