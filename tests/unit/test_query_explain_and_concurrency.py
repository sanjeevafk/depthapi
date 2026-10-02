"""Unit tests for query explainability (explain: bool) and concurrent embedding/connection acquisition."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from api.routers import query as query_module
from api.services.security.api_key_auth import ApiKeyRecord


def _dummy_request() -> Request:
    return Request({"type": "http", "method": "POST", "url": "http://testserver/api/query", "headers": []})


def _dummy_key() -> ApiKeyRecord:
    return ApiKeyRecord(str(uuid4()), "free", False)


@pytest.mark.asyncio
async def test_query_explain_true_returns_diagnostics(monkeypatch):
    """When explain=True, response metadata contains full retrieval diagnostics and chunk scoring."""
    mock_rows = [
        {"document_id": str(uuid4()), "source_url": "https://example.com/doc1", "content": "Chunk 1", "score": 0.88, "match_source": "dense"},
        {"document_id": str(uuid4()), "source_url": "https://example.com/doc2", "content": "Chunk 2", "score": 0.82, "match_source": "dense"},
        {"document_id": str(uuid4()), "source_url": "https://example.com/doc3", "content": "Chunk 3", "score": 0.79, "match_source": "dense"},
        {"document_id": str(uuid4()), "source_url": "https://example.com/doc4", "content": "Chunk 4", "score": 0.75, "match_source": "dense"},
        {"document_id": str(uuid4()), "source_url": "https://example.com/doc5", "content": "Chunk 5", "score": 0.71, "match_source": "dense"},
    ]

    async def fake_rpc(fn_name, params):
        return mock_rows

    async def fake_embed(texts):
        return [[0.1] * 768]

    async def fake_generate(q, ctxs, temp):
        return "Explanation test answer."

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "generate_response", fake_generate)

    req = query_module.QueryRequest(query="How does vector search work?", explain=True)
    res = await query_module.query(req, _dummy_request(), _dummy_key())

    assert "explanation" in res.metadata
    exp = res.metadata["explanation"]
    assert exp["retrieval_mode"] == "dense"
    assert "effective_hops" in exp
    assert "confidence" in exp
    assert exp["total_chunks_retrieved"] == 5
    assert len(exp["chunks"]) == 5
    assert exp["chunks"][0]["score"] == 0.88
    assert exp["chunks"][0]["match_source"] == "dense"


@pytest.mark.asyncio
async def test_query_explain_false_omits_diagnostics(monkeypatch):
    """When explain=False, explanation diagnostics are excluded from metadata."""
    mock_rows = [
        {"document_id": str(uuid4()), "source_url": "https://example.com/doc", "content": "Chunk", "score": 0.9, "match_source": "dense"}
    ]

    async def fake_rpc(fn_name, params):
        return mock_rows

    async def fake_embed(texts):
        return [[0.1] * 768]

    async def fake_generate(q, ctxs, temp):
        return "Answer without explanation."

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "generate_response", fake_generate)

    req = query_module.QueryRequest(query="Standard query", explain=False)
    res = await query_module.query(req, _dummy_request(), _dummy_key())

    assert "explanation" not in res.metadata


@pytest.mark.asyncio
async def test_query_embedding_and_connection_concurrency(monkeypatch):
    """Verify that embed_texts and pool.acquire execute concurrently via asyncio.gather."""
    events: list[str] = []

    async def slow_embed(texts):
        events.append("embed_start")
        await asyncio.sleep(0.02)
        events.append("embed_end")
        return [[0.05] * 768]

    mock_conn = AsyncMock()
    mock_pool = MagicMock()

    async def slow_acquire():
        events.append("acquire_start")
        await asyncio.sleep(0.01)
        events.append("acquire_end")
        return mock_conn

    mock_pool.acquire.side_effect = slow_acquire
    mock_pool.release = AsyncMock()
    monkeypatch.setattr(query_module, "get_pool", lambda: mock_pool)
    monkeypatch.setattr(query_module, "embed_texts", slow_embed)
    monkeypatch.setattr(query_module, "execute_rpc", AsyncMock(return_value=[]))
    monkeypatch.setattr(query_module, "generate_response", AsyncMock(return_value="Concurrent answer"))

    req = query_module.QueryRequest(query="Concurrency test query", depth=3)
    await query_module.query(req, _dummy_request(), _dummy_key())

    # Both tasks should start before either finishes
    assert "embed_start" in events
    assert "acquire_start" in events
    first_two_events = events[:2]
    assert set(first_two_events) == {"embed_start", "acquire_start"}
    mock_pool.release.assert_awaited()


@pytest.mark.asyncio
async def test_query_stream_explain_true(monkeypatch):
    """Streaming endpoint emits explanation in the final metadata event when explain=True."""
    mock_rows = [
        {"document_id": str(uuid4()), "source_url": "https://example.com/stream", "content": "Stream chunk", "score": 0.85, "match_source": "dense"}
    ]

    async def fake_rpc(fn_name, params):
        return mock_rows

    async def fake_embed(texts):
        return [[0.1] * 768]

    async def fake_stream_gen(q, ctxs, temp):
        yield "Streamed "
        yield "explanation answer."

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "generate_stream_response", fake_stream_gen)

    req = query_module.QueryRequest(query="Stream explain query", explain=True)
    resp = await query_module.query_stream(req, AsyncMock(), _dummy_key())

    chunks = []
    async for item in resp.body_iterator:
        chunks.append(item.decode("utf-8") if isinstance(item, bytes) else item)
    payload = "".join(chunks)

    assert "data: [DONE]" in payload
    # Find metadata JSON in events
    data_lines = [line.removeprefix("data: ") for line in payload.split("\n\n") if line.startswith("data: ") and not line.endswith("[DONE]")]
    final_event = json.loads(data_lines[-1])
    assert "metadata" in final_event
    assert "explanation" in final_event["metadata"]
    assert final_event["metadata"]["explanation"]["total_chunks_retrieved"] == 1
