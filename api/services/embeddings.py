"""Embedding service for DepthAPI.
Supports OpenAI and Google Gemini.
"""

from typing import List, Optional

import structlog
import google.generativeai as genai
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from api.config import get_settings

logger = structlog.get_logger(__name__)

class EmbeddingService:
    def __init__(self):
        settings = get_settings()
        self.provider = getattr(settings, "embedding_provider", "openai")
        self.model = getattr(settings, "embedding_model", "text-embedding-3-small")
        self.dimensions = getattr(settings, "embedding_dimension", 1536)
        
        if self.provider == "gemini":
            api_key = getattr(settings, "gemini_api_key", "")
            if hasattr(api_key, "get_secret_value"):
                api_key = api_key.get_secret_value()
            api_key = str(api_key or "").strip()
            if not api_key:
                raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")
            genai.configure(api_key=api_key)
            self.gemini_client = genai
        else:
            api_key = getattr(settings, "openai_api_key", "")
            if hasattr(api_key, "get_secret_value"):
                api_key = api_key.get_secret_value()
            api_key = str(api_key or "").strip()
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
            self.openai_client = AsyncOpenAI(api_key=api_key)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of strings."""
        if not texts:
            return []
            
        try:
            cleaned_texts = [t.replace("\n", " ").strip() for t in texts]
            # Filter empty strings
            valid_texts = [t for t in cleaned_texts if t]
            if not valid_texts:
                return []

            if self.provider == "gemini":
                # Gemini text-embedding-004 handles batches natively
                result = self.gemini_client.embed_content(
                    model=f"models/{self.model}",
                    content=valid_texts,
                    task_type="retrieval_document",
                )
                return result["embedding"]
            else:
                response = await self.openai_client.embeddings.create(
                    input=valid_texts,
                    model=self.model,
                    dimensions=self.dimensions
                )
                return [data.embedding for data in response.data]
            
        except Exception as exc:
            logger.error("embedding_failed", provider=self.provider, error=str(exc), model=self.model)
            raise

# Global singleton instance
_service: Optional[EmbeddingService] = None

def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
