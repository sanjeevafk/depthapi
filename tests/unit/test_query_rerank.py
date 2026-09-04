"""Unit tests for query endpoint with RRF and reranking toggle."""
from __future__ import annotations

from uuid import uuid4

import pytest
from starlette.requests import Request

from api.routers import query as query_module
from api.services.security.api_key_auth import ApiKeyRecord


def _dummy_request() -> Request:
    return Request({"type": "http", "method": "POST", "url": "http://testserver/api/query", "headers": []})


@pytest.mark.asyncio
async def test_query_executes_reranker_when_enabled(monkeypatch):
    rerank_called = False

    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(_function, params):
        return [
            {"content": "Candidate A", "document_id": str(uuid4()), "score": 0.03},
            {"content": "Candidate B", "document_id": str(uuid4()), "score": 0.02},
        ]

    class FakeReranker:
        async def rerank(self, query: str, candidates: list, top_n: int = 5):
            nonlocal rerank_called
            rerank_called = True
            # Invert order to prove reranker determined the final list
            return list(reversed(candidates))[:top_n]

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: FakeReranker())

    key_id = uuid4()
    req = query_module.QueryRequest(query="What is DepthAPI?", rerank=True)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(key_id), "free", False))

    assert rerank_called is True
    assert len(res.contexts) == 2
    assert res.contexts[0]["content"] == "Candidate B"
    assert res.contexts[1]["content"] == "Candidate A"


@pytest.mark.asyncio
async def test_query_bypasses_reranker_when_disabled(monkeypatch):
    rerank_called = False

    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(_function, params):
        return [
            {"content": "Candidate A", "document_id": str(uuid4()), "score": 0.03},
            {"content": "Candidate B", "document_id": str(uuid4()), "score": 0.02},
        ]

    class FakeReranker:
        async def rerank(self, query: str, candidates: list, top_n: int = 5):
            nonlocal rerank_called
            rerank_called = True
            return candidates

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: FakeReranker())

    key_id = uuid4()
    req = query_module.QueryRequest(query="What is DepthAPI?", rerank=False)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(key_id), "free", False))

    assert rerank_called is False
    assert len(res.contexts) == 2
    assert res.contexts[0]["content"] == "Candidate A"


@pytest.mark.asyncio
async def test_query_handles_reranker_failure_gracefully(monkeypatch):
    async def fake_embed(_texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_rpc(_function, params):
        return [{"content": "Original Context", "document_id": str(uuid4()), "score": 0.05}]

    class FailingReranker:
        async def rerank(self, query: str, candidates: list, top_n: int = 5):
            raise RuntimeError("CUDA out of memory or model unavailable")

    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: FailingReranker())

    key_id = uuid4()
    req = query_module.QueryRequest(query="Testing resilience", rerank=True)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(key_id), "free", False))

    # Should not raise 500/503; must fallback gracefully to original contexts
    assert len(res.contexts) == 1
    assert res.contexts[0]["content"] == "Original Context"
