"""Embedding generation with a deterministic local fallback."""
from __future__ import annotations

import hashlib
import math
from typing import Sequence

from api.config import get_settings

DIMENSION = 768

def _local_embedding(text: str) -> list[float]:
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
    if settings.embedding_provider.lower() == "openai" and settings.openai_api_key.get_secret_value():
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        response = await client.embeddings.create(model=settings.embedding_model, input=list(texts), dimensions=settings.embedding_dimension)
        if any(len(item.embedding) != settings.embedding_dimension for item in response.data):
            raise ValueError("Embedding provider returned an unexpected vector dimension")
        return [_vector_literal(item.embedding) for item in response.data]
    return [_vector_literal(_local_embedding(text)) for text in texts]
