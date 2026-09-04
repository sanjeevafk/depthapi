"""Unit tests for dense-first retrieval with hybrid fallback and rerank gating."""
from __future__ import annotations

from uuid import uuid4

import pytest
from starlette.requests import Request

from api.routers import query as query_module
from api.services.security.api_key_auth import ApiKeyRecord


def _dummy_request() -> Request:
    return Request({"type": "http", "method": "POST", "url": "http://testserver/api/query", "headers": []})


def _dense(rows):
    return [{"content": f"Doc {i}", "document_id": str(uuid4()), "score": s} for i, s in enumerate(rows)]


class _FakeReranker:
    called = False

    async def rerank(self, query: str, candidates: list, top_n: int = 5):
        _FakeReranker.called = True
        return candidates[:top_n]


def _run(monkeypatch, dense_rows, hybrid_rows=None, exc_dense=False, **req_kwargs):
    calls: list[str] = []
    _FakeReranker.called = False

    async def fake_rpc(fn_name, params):
        calls.append(fn_name)
        if fn_name == "dense_search_v5":
            if exc_dense:
                raise RuntimeError("dense down")
            return _dense(dense_rows)
        return hybrid_rows if hybrid_rows is not None else []

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: _FakeReranker())

    req = query_module.QueryRequest(query="What is DepthAPI?", **req_kwargs)
    return req, calls


@pytest.mark.asyncio
async def test_dense_hit_skips_hybrid_and_rerank(monkeypatch):
    req, calls = _run(monkeypatch, [0.9, 0.88, 0.85, 0.82, 0.8, 0.78], hybrid_rows=[])
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False))

    assert calls == ["dense_search_v5"]
    assert res.metadata["retrieval_mode"] == "dense"
    assert res.metadata["rerank_applied"] is False
    assert _FakeReranker.called is False
    assert res.metadata["confidence"] == "high"
    assert len(res.contexts) == 6


@pytest.mark.asyncio
async def test_dense_miss_falls_back_to_hybrid(monkeypatch):
    hybrid = [{"content": "Hybrid doc", "document_id": str(uuid4()), "score": 0.03}]
    req, calls = _run(monkeypatch, [0.4, 0.35], hybrid_rows=hybrid)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False))

    assert calls[0] == "dense_search_v5"
    assert "hybrid_search_trusted_v5" in calls
    assert res.metadata["retrieval_mode"] == "hybrid"
    assert res.contexts[0]["content"] == "Hybrid doc"


@pytest.mark.asyncio
async def test_dense_error_falls_back_to_hybrid(monkeypatch):
    hybrid = [{"content": "Hybrid doc", "document_id": str(uuid4()), "score": 0.03}]
    req, calls = _run(monkeypatch, [], hybrid_rows=hybrid, exc_dense=True)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False))

    assert "hybrid_search_trusted_v5" in calls
    assert res.metadata["retrieval_mode"] == "hybrid"


@pytest.mark.asyncio
async def test_marginal_dense_hit_still_reranks(monkeypatch):
    # Top similarity 0.6 clears the dense-hit bar (0.5) but not the rerank-skip bar (0.8).
    req, _ = _run(monkeypatch, [0.6, 0.58, 0.55, 0.52, 0.51, 0.5])
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False))

    assert res.metadata["retrieval_mode"] == "dense"
    assert res.metadata["rerank_applied"] is True
    assert _FakeReranker.called is True


@pytest.mark.asyncio
async def test_dense_low_similarity_confidence(monkeypatch):
    req, _ = _run(monkeypatch, [0.52, 0.51, 0.5, 0.5, 0.5, 0.5])
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False))

    assert res.metadata["retrieval_mode"] == "dense"
    assert res.metadata["confidence"] == "low"


@pytest.mark.asyncio
async def test_citations_enforced_flag_on_fallback_answer(monkeypatch):
    req, _ = _run(monkeypatch, [0.9, 0.88, 0.85, 0.82, 0.8, 0.78])
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False))

    # Fallback excerpt answers carry no [n] markers.
    assert res.metadata["citations_enforced"] is False


@pytest.mark.asyncio
async def test_query_bypasses_everything_without_contexts(monkeypatch):
    async def fake_rpc(fn_name, params):
        return []

    async def fake_embed(texts):
        return ["[0]"]

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)

    req = query_module.QueryRequest(query="Nothing matches this?", rerank=False)
    res = await query_module.query(
        req, _dummy_request(), ApiKeyRecord(str(uuid4()), "free", False)
    )

    assert res.metadata["confidence"] == "insufficient"
    assert res.metadata["citations_enforced"] is True
    assert res.cached is False
