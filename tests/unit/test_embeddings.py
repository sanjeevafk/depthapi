"""Unit tests for embedding generation."""
from __future__ import annotations

import json
import math
import pytest

from api.services.rag import embeddings as emb_module


def _parse_vector(vector_literal: str) -> list[float]:
    return json.loads(vector_literal)


def _cosine_similarity(v1: list[float], v2: list[float]) -> float:
    return sum(a * b for a, b in zip(v1, v2))


@pytest.mark.asyncio
async def test_embed_texts_produces_768_dim_normalized_vectors():
    texts = ["Database indexing with PostgreSQL", "Machine learning inference"]
    results = await emb_module.embed_texts(texts)

    assert len(results) == 2
    for r in results:
        vec = _parse_vector(r)
        assert len(vec) == 768
        norm = math.sqrt(sum(x * x for x in vec))
        assert abs(norm - 1.0) < 1e-4


@pytest.mark.asyncio
async def test_semantic_similarity_ranking():
    texts = ["puppy", "dog", "quantum physics equations"]
    results = await emb_module.embed_texts(texts)

    v_puppy = _parse_vector(results[0])
    v_dog = _parse_vector(results[1])
    v_quantum = _parse_vector(results[2])

    sim_related = _cosine_similarity(v_puppy, v_dog)
    sim_unrelated = _cosine_similarity(v_puppy, v_quantum)

    assert sim_related > sim_unrelated
    assert sim_related > 0.6


@pytest.mark.asyncio
async def test_hash_fallback_when_model_is_none(monkeypatch):
    monkeypatch.setattr(emb_module, "get_local_transformer", lambda *args, **kwargs: None)

    texts = ["fallback text test"]
    results = await emb_module.embed_texts(texts)

    assert len(results) == 1
    vec = _parse_vector(results[0])
    assert len(vec) == 768
    norm = math.sqrt(sum(x * x for x in vec))
    assert abs(norm - 1.0) < 1e-4
