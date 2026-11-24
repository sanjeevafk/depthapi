"""Embedding generation with local SentenceTransformer and deterministic fallback."""
from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import math
from collections.abc import Sequence
from typing import Any

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


@functools.lru_cache(maxsize=4)
def _cached_embedding_client(client_cls: Any, base_url: str | None, api_key: str) -> Any:
    if base_url:
        return client_cls(base_url=base_url, api_key=api_key)
    return client_cls(api_key=api_key)


async def embed_texts(texts: Sequence[str]) -> list[str]:
    settings = get_settings()
    provider = settings.embedding_provider.lower()

    # 1. Cloud OpenAI-compatible provider (explicit "openai" or "auto" when API key is set)
    if provider in {"openai", "auto"} and settings.openai_api_key.get_secret_value():
        try:
            from openai import AsyncOpenAI

            client = _cached_embedding_client(
                AsyncOpenAI,
                settings.openai_base_url or None,
                settings.openai_api_key.get_secret_value(),
            )
            response = await client.embeddings.create(
                model=settings.embedding_model,
                input=list(texts),
                dimensions=settings.embedding_dimension,
            )
            if any(len(item.embedding) != settings.embedding_dimension for item in response.data):
                raise ValueError("Embedding provider returned an unexpected vector dimension")
            return [_vector_literal(item.embedding) for item in response.data]
        except Exception as exc:
            log.warning("API embedding failed, falling back to local/hash: %s", exc)

    # 2. Local neural embeddings via sentence-transformers (if installed)
    local_model = (
        settings.embedding_model
        if settings.embedding_model and settings.embedding_model != "text-embedding-3-small"
        else DEFAULT_LOCAL_MODEL
    )
    model = get_local_transformer(local_model)
    if model is not None:
        try:
            embeddings = await asyncio.to_thread(
                model.encode,
                list(texts),
                batch_size=64,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [_vector_literal(emb.round(8).tolist()) for emb in embeddings]
        except Exception as exc:
            log.warning("Local neural embedding failed, falling back to hash: %s", exc)

    # 3. Deterministic hash fallback
    return [_vector_literal(_local_hash_embedding(text)) for text in texts]
