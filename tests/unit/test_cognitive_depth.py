"""
Unit tests for OKF Cognitive Depth (Levels 1-5) and compounding Q&A wiki loop.
"""
from __future__ import annotations

import shutil
import tempfile
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from starlette.requests import Request

from api.routers import query as query_module
from api.services.security.api_key_auth import ApiKeyRecord
from api.services.wiki.vault_manager import WikiVaultManager


def _dummy_request() -> Request:
    return Request({"type": "http", "method": "POST", "url": "http://testserver/api/query", "headers": []})


@pytest.fixture
def isolated_vault():
    temp_dir = tempfile.mkdtemp(prefix="test_depth_vault_")
    vault = WikiVaultManager(temp_dir)
    yield vault
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.mark.asyncio
async def test_cognitive_depth_1_direct_concept_summary(monkeypatch, isolated_vault):
    """Depth 1 delivers direct concept summaries with high token efficiency."""
    isolated_vault.export_concepts_to_vault([
        {
            "name": "VectorSearch",
            "concept_type": "retrieval",
            "description": "Dense vector similarity search using embeddings.",
        }
    ])
    monkeypatch.setattr(query_module, "get_vault_manager", lambda: isolated_vault)

    async def fake_generate(q, ctxs, temp):
        return "VectorSearch performs dense nearest neighbor search."

    monkeypatch.setattr(query_module, "generate_response", fake_generate)

    req = query_module.QueryRequest(query="VectorSearch", depth=1)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["cognitive_depth"] == 1
    assert res.metadata["depth"] == 1
    assert res.metadata["graph_hops"] == 0
    assert len(res.contexts) <= 2
    assert "VectorSearch" in res.contexts[0]["concept_name"]
    # Total characters in context are small (< 400 chars) -> ~95% token reduction vs full multi-chunk RAG
    assert len(res.contexts[0]["content"]) < 400


@pytest.mark.asyncio
async def test_cognitive_depth_3_scoped_hybrid(monkeypatch):
    """Depth 3 uses standard hybrid retrieval with intent-based graph hops."""
    called_rpc = None

    async def fake_rpc(fn_name, params):
        nonlocal called_rpc
        called_rpc = fn_name
        return [{"content": "Context chunk 1", "document_id": str(uuid4()), "score": 0.05}]

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    async def fake_generate(q, ctxs, temp):
        return "Answer at depth 3"

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "generate_response", fake_generate)

    req = query_module.QueryRequest(query="Explain the system components", depth=3)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["cognitive_depth"] == 3
    assert res.metadata["depth"] == 3
    assert called_rpc in ("hybrid_search_trusted_v5", "hybrid_search_trusted_with_graph_v5")


@pytest.mark.asyncio
async def test_cognitive_depth_5_deep_graph_and_forced_rerank(monkeypatch):
    """Depth 5 enforces 2-hop graph traversal and cross-encoder rerank."""
    rpc_params = None
    rerank_executed = False

    async def fake_rpc(fn_name, params):
        nonlocal rpc_params
        rpc_params = params
        return [
            {"content": f"Context chunk {i}", "document_id": str(uuid4()), "score": 0.05}
            for i in range(5)
        ]

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    class FakeReranker:
        async def rerank(self, query: str, candidates: list, top_n: int = 7):
            nonlocal rerank_executed
            rerank_executed = True
            return candidates[:top_n]

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "get_reranker_service", lambda: FakeReranker())
    monkeypatch.setattr(query_module, "generate_response", AsyncMock(return_value="Deep answer"))

    # Even with rerank=False in request, depth=5 forces rerank
    req = query_module.QueryRequest(query="Complete system architecture deep dive", depth=5, rerank=False)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["cognitive_depth"] == 5
    assert res.metadata["graph_hops"] == 2
    assert rpc_params.get("graph_hops") == 2
    assert rerank_executed is True
    assert res.metadata["prompt_ordering"] == "lost_in_the_middle"


@pytest.mark.asyncio
async def test_compounding_qa_save_to_wiki(monkeypatch, isolated_vault):
    """save_to_wiki=True writes synthesized insight to the vault and log."""
    monkeypatch.setattr(query_module, "get_vault_manager", lambda: isolated_vault)

    async def fake_rpc(fn_name, params):
        return [{"content": "Important chunk", "document_id": str(uuid4()), "concept_name": "Caching", "score": 0.05}]

    async def fake_embed(texts):
        return ["[" + ",".join(["0"] * 768) + "]"]

    monkeypatch.setattr(query_module, "execute_rpc", fake_rpc)
    monkeypatch.setattr(query_module, "embed_texts", fake_embed)
    monkeypatch.setattr(query_module, "generate_response", AsyncMock(return_value="Caching accelerates repeated requests."))

    req = query_module.QueryRequest(query="How does caching work?", depth=3, save_to_wiki=True)
    res = await query_module.query(req, _dummy_request(), ApiKeyRecord(str(uuid4()), "pro", True))

    assert res.metadata["saved_to_wiki"] is True

    # Check vault has the synthesis note
    concepts = isolated_vault.list_concepts()
    assert any("synthesis_" in c["slug"] for c in concepts)

    # Check log.md recorded the activity
    log_content = (isolated_vault.vault_dir / "log.md").read_text(encoding="utf-8")
    assert "Q&A Insight Saved" in log_content
    assert "How does caching work?" in log_content
