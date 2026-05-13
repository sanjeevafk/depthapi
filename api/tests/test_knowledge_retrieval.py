import pytest

import api.services.knowledge_retrieval as retrieval_module
from api.tests.conftest import FakeSupabase


class DummyEmbeddingService:
    async def create_embeddings(self, texts):
        return [[0.0] * 3 for _ in texts]


class DummyReranker:
    async def rerank(self, _query, candidates, top_n=10):
        return candidates[:top_n]


@pytest.mark.asyncio
async def test_retrieve_context_skips_trusted_when_disabled(monkeypatch, fake_supabase):
    fake_primary = fake_supabase
    fake_trusted = FakeSupabase()

    fake_primary.responses["hybrid_search_v4"] = []
    fake_trusted.responses["hybrid_search_trusted_v4"] = []

    monkeypatch.setattr(retrieval_module, "get_supabase_admin", lambda: fake_primary)
    monkeypatch.setattr(retrieval_module, "get_trusted_corpus_admin", lambda: fake_trusted)
    monkeypatch.setattr(retrieval_module, "get_reranker_service", lambda: DummyReranker())

    service = retrieval_module.RetrievalService()
    service.embed_service = DummyEmbeddingService()

    await service.retrieve_context("test", api_key_id="key-1", use_trusted_corpus=False)

    assert fake_primary.rpcs
    assert fake_primary.rpcs[0][0] == "hybrid_search_v4"
    assert fake_trusted.rpcs == []
