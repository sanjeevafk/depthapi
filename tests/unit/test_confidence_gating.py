"""Unit tests for query confidence gating (Corrective RAG)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch
import pytest

from api.routers.query import QueryRequest, query
from api.services.security.api_key_auth import ApiKeyRecord


@pytest.mark.asyncio
async def test_confidence_insufficient_when_no_contexts():
    req = QueryRequest(query="Obscure unknown question?", rerank=False)
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="pro", is_pro=True)

    mock_rpc = AsyncMock(return_value=[])
    mock_embed = AsyncMock(return_value=[[0.01] * 768])

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ):
        res = await query(req, request=AsyncMock(), _api_key=fake_key)

    assert res.metadata["confidence"] == "insufficient"
    assert "could not find sufficient matching documentation" in res.answer.lower()


@pytest.mark.asyncio
async def test_confidence_high_with_good_rerank_scores():
    req = QueryRequest(query="What is Python?", rerank=True)
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="pro", is_pro=True)

    mock_rpc = AsyncMock(return_value=[{"document_id": "d1", "content": "Python is a language"}])
    mock_embed = AsyncMock(return_value=[[0.01] * 768])
    mock_reranker = AsyncMock()
    mock_reranker.rerank = AsyncMock(return_value=[{"document_id": "d1", "content": "Python is a language", "rerank_score": 2.5}])
    mock_gen = AsyncMock(return_value="Python is a language.")

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ), patch("api.routers.query.get_reranker_service", return_value=mock_reranker), patch(
        "api.routers.query.generate_response", mock_gen
    ):
        res = await query(req, request=AsyncMock(), _api_key=fake_key)

    assert res.metadata["confidence"] == "high"
    assert res.metadata["prompt_ordering"] == "lost_in_the_middle"
    assert res.answer == "Python is a language."


@pytest.mark.asyncio
async def test_confidence_low_with_negative_rerank_scores():
    req = QueryRequest(query="Unrelated query?", rerank=True)
    fake_key = ApiKeyRecord(id="00000000-0000-0000-0000-000000000001", plan="pro", is_pro=True)

    mock_rpc = AsyncMock(return_value=[{"document_id": "d1", "content": "Distantly related content"}])
    mock_embed = AsyncMock(return_value=[[0.01] * 768])
    mock_reranker = AsyncMock()
    mock_reranker.rerank = AsyncMock(return_value=[{"document_id": "d1", "content": "Distantly related content", "rerank_score": -3.5}])
    mock_gen = AsyncMock(return_value="Cautious answer.")

    with patch("api.routers.query.execute_rpc", mock_rpc), patch(
        "api.routers.query.embed_texts", mock_embed
    ), patch("api.routers.query.get_reranker_service", return_value=mock_reranker), patch(
        "api.routers.query.generate_response", mock_gen
    ):
        res = await query(req, request=AsyncMock(), _api_key=fake_key)

    assert res.metadata["confidence"] == "low"
    assert res.metadata["prompt_ordering"] == "lost_in_the_middle"
