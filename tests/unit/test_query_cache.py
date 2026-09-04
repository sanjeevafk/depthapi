"""Unit tests for Redis query cache and per-key quotas (FakeRedis-backed)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from api.routers import query as query_module
from api.routers.query import QueryRequest, query, query_stream
from api.services import cache as cache_mod
from api.services.security.api_key_auth import ApiKeyRecord


def _key():
    return ApiKeyRecord(str(uuid4()), "free", False)


def _mocks(monkeypatch, answer="Mocked answer."):
    mock_rpc = AsyncMock(return_value=[{"document_id": "d1", "content": "Doc content", "score": 0.03}])
    mock_embed = AsyncMock(return_value=[[0.01] * 768])
    mock_gen = AsyncMock(return_value=answer)
    monkeypatch.setattr(query_module, "execute_rpc", mock_rpc)
    monkeypatch.setattr(query_module, "embed_texts", mock_embed)
    monkeypatch.setattr(query_module, "generate_response", mock_gen)
    return mock_rpc, mock_gen


@pytest.mark.asyncio
async def test_cache_miss_then_hit(monkeypatch, _isolated_query_cache):
    mock_rpc, _ = _mocks(monkeypatch)
    req = QueryRequest(query="Cache me?")
    key = _key()

    first = await query(req, AsyncMock(), key)
    assert first.cached is False
    assert mock_rpc.await_count >= 1

    mock_rpc.reset_mock()
    mock_rpc.side_effect = AssertionError("RPC must not run on cache hit")
    second = await query(req, AsyncMock(), key)

    assert second.cached is True
    assert second.answer == first.answer
    assert second.metadata["citations_enforced"] == first.metadata["citations_enforced"]


@pytest.mark.asyncio
async def test_bypass_cache_skips_lookup(monkeypatch, _isolated_query_cache):
    mock_rpc, _ = _mocks(monkeypatch)
    key = _key()
    await query(QueryRequest(query="Bypass me?"), AsyncMock(), key)
    assert mock_rpc.await_count >= 1

    mock_rpc.reset_mock()
    res = await query(QueryRequest(query="Bypass me?", bypass_cache=True), AsyncMock(), key)

    assert res.cached is False
    assert mock_rpc.await_count >= 1


@pytest.mark.asyncio
async def test_save_to_wiki_never_populates_cache(monkeypatch, _isolated_query_cache):
    _mocks(monkeypatch)
    with patch("api.routers.query.get_vault_manager"):
        req = QueryRequest(query="Wiki write?", save_to_wiki=True)
        await query(req, AsyncMock(), _key())

    assert [k for k in _isolated_query_cache.store if k.startswith("depthapi:q:")] == []


@pytest.mark.asyncio
async def test_redis_down_serves_uncached(monkeypatch):
    _mocks(monkeypatch)
    monkeypatch.setattr(cache_mod, "get_client", lambda: None)

    res = await query(QueryRequest(query="No redis?"), AsyncMock(), _key())

    assert res.cached is False
    assert res.answer == "Mocked answer."


@pytest.mark.asyncio
async def test_quota_exceeded_returns_429(monkeypatch, _isolated_query_cache):
    _mocks(monkeypatch)
    key = _key()
    _isolated_query_cache.store[cache_mod._quota_redis_key(key.id)] = "999999999"

    with pytest.raises(HTTPException) as exc_info:
        await query(QueryRequest(query="Over quota?"), AsyncMock(), key)

    assert exc_info.value.status_code == 429


@pytest.mark.asyncio
async def test_quota_consumed_on_miss(monkeypatch, _isolated_query_cache):
    _mocks(monkeypatch)
    key = _key()

    await query(QueryRequest(query="Count me?"), AsyncMock(), key)

    used = int(_isolated_query_cache.store[cache_mod._quota_redis_key(key.id)])
    assert used > 0


@pytest.mark.asyncio
async def test_quota_fail_open_when_redis_raises(monkeypatch):
    _mocks(monkeypatch)

    class _Boom:
        def __getattr__(self, _):
            raise RuntimeError("redis down")

    monkeypatch.setattr(cache_mod, "get_client", lambda: _Boom())

    res = await query(QueryRequest(query="Still served?"), AsyncMock(), _key())
    assert res.cached is False


@pytest.mark.asyncio
async def test_stream_replays_cache_hit(monkeypatch, _isolated_query_cache):
    _mocks(monkeypatch, answer="Cached stream answer.")
    key = _key()
    await query(QueryRequest(query="Stream me?"), AsyncMock(), key)

    mock_rpc = AsyncMock(side_effect=AssertionError("RPC must not run on stream hit"))
    monkeypatch.setattr(query_module, "execute_rpc", mock_rpc)

    resp = await query_stream(QueryRequest(query="Stream me?"), AsyncMock(), key)
    body = "".join([c.decode() if isinstance(c, bytes) else c async for c in resp.body_iterator])

    assert ": stream start" in body
    assert "Cached stream answer." in body
    assert '"cached": true' in body
    assert body.strip().endswith("data: [DONE]")
