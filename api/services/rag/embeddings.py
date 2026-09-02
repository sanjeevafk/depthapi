"""Embedding generation with local SentenceTransformer and deterministic fallback."""
from __future__ import annotations

import functools
import hashlib
import logging
import math
from typing import Any, Sequence

from api.config import get_settings

log = logging.getLogger(__name__)

DIMENSION = 768
DEFAULT_LOCAL_MODEL = "BAAI/bge-base-en-v1.5"


@functools.cache
def get_local_transformer(model_name: str = DEFAULT_LOCAL_MODEL) -> Any:
    """Lazy-loaded singleton SentenceTransformer model."""
    try:
        from sentence_transformers import SentenceTransformer

        log.info("Loading local embedding model: %s", model_name)
        return SentenceTransformer(model_name)
    except Exception as exc:
        log.warning(
            "Could not load SentenceTransformer '%s', falling back to hash embeddings: %s",
            model_name,
            exc,
        )
        return None


def _local_hash_embedding(text: str) -> list[float]:
    """Deterministic hash fallback when neural embedding model cannot be loaded."""
    vector = [0.0] * DIMENSION
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % DIMENSION
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [round(value / norm, 8) for value in vector]


def _vector_literal(vector: Sequence[float]) -> str:
    return "[" + ",".join(str(value) for value in vector) + "]"


async def embed_texts(texts: Sequence[str]) -> list[str]:
    settings = get_settings()

    # 1. Cloud OpenAI provider if configured
    if settings.embedding_provider.lower() == "openai" and settings.openai_api_key.get_secret_value():
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        response = await client.embeddings.create(
            model=settings.embedding_model,
            input=list(texts),
            dimensions=settings.embedding_dimension,
        )
        if any(len(item.embedding) != settings.embedding_dimension for item in response.data):
            raise ValueError("Embedding provider returned an unexpected vector dimension")
        return [_vector_literal(item.embedding) for item in response.data]

    # 2. Real local neural embeddings via sentence-transformers
    model = get_local_transformer(DEFAULT_LOCAL_MODEL)
    if model is not None:
        try:
            embeddings = model.encode(list(texts), normalize_embeddings=True)
            return [_vector_literal(emb.round(8).tolist()) for emb in embeddings]
        except Exception as exc:
            log.warning("Local neural embedding failed, falling back to hash: %s", exc)

    # 3. Deterministic hash fallback
    return [_vector_literal(_local_hash_embedding(text)) for text in texts]
