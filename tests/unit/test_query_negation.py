"""Unit tests for Mosaic Negative Query Algebra wired into query router."""
from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from api.routers import query as query_module
from api.routers.query import QueryRequest, parse_query_negations, query
from api.services.security.api_key_auth import ApiKeyRecord


def _api_key() -> ApiKeyRecord:
    return ApiKeyRecord(str(uuid4()), "standard", True)


def test_parse_query_negations_syntax():
    # NOT syntax
    clean, negs = parse_query_negations("FastAPI authentication NOT OAuth2")
    assert clean == "FastAPI authentication"
    assert negs == ["oauth2"]

    # Dash syntax (-term)
    clean, negs = parse_query_negations("PostgreSQL vector search -pinecone -weaviate")
    assert clean == "PostgreSQL vector search"
    assert set(negs) == {"pinecone", "weaviate"}

    # without / except syntax
    clean, negs = parse_query_negations("LLM inference without transformers except onnx")
    assert clean == "LLM inference"
    assert set(negs) == {"transformers", "onnx"}

    # Explicit list combined with query syntax
    clean, negs = parse_query_negations("search NOT legacy", explicit_negatives=["deprecated"])
    assert clean == "search"
    assert set(negs) == {"deprecated", "legacy"}


@pytest.mark.asyncio
async def test_query_negation_penalizes_unwanted_candidates(monkeypatch, _isolated_query_cache):
    # Candidate 1 contains the negated term "oauth2"
    # Candidate 2 is clean
    mock_candidates = [
        {"document_id": "doc1", "content": "FastAPI with OAuth2 authentication and bearer tokens", "score": 0.8},
        {"document_id": "doc2", "content": "FastAPI with API Key tokens and headers", "score": 0.75},
    ]

    mock_rpc = AsyncMock(return_value=mock_candidates)
    mock_embed = AsyncMock(return_value=[[0.05] * 768])
    mock_gen = AsyncMock(return_value="Answer without oauth2.")

    monkeypatch.setattr(query_module, "execute_rpc", mock_rpc)
    monkeypatch.setattr(query_module, "embed_texts", mock_embed)
    monkeypatch.setattr(query_module, "generate_response", mock_gen)

    # Query with NOT oauth2
    req = QueryRequest(query="FastAPI authentication NOT oauth2", depth=3, explain=True)
    resp = await query(req, AsyncMock(), _api_key())

    # Doc 2 must be ranked first because doc 1 was penalized by Mosaic soft penalty
    assert len(resp.contexts) == 2
    assert resp.contexts[0]["document_id"] == "doc2"
    assert resp.contexts[1]["document_id"] == "doc1"

    # Verify negative terms in explanation metadata
    assert "oauth2" in resp.metadata["negative_terms"]
    assert "oauth2" in resp.metadata["explanation"]["negative_terms"]
