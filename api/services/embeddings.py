"""Direct OpenAI Embedding service for DepthAPI."""

from typing import List, Optional

import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from api.config import get_settings

logger = structlog.get_logger(__name__)

class EmbeddingService:
    def __init__(self):
        settings = get_settings()
        api_key = getattr(settings, "openai_api_key", "")
        if hasattr(api_key, "get_secret_value"):
            api_key = api_key.get_secret_value()
        api_key = str(api_key or "").strip()
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for embeddings")
            
        self.client = AsyncOpenAI(api_key=api_key)
        self.model = getattr(settings, "embedding_model", "text-embedding-3-small")
        self.dimensions = getattr(settings, "embedding_dimension", 1536)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True
    )
    async def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embeddings for a list of strings using direct OpenAI API."""
        if not texts:
            return []
            
        try:
            # Strip and clean texts to prevent API errors on empty/whitespace-only chunks
            cleaned_texts = [t.replace("\n", " ").strip() for t in texts]
            
            response = await self.client.embeddings.create(
                input=cleaned_texts,
                model=self.model,
                dimensions=self.dimensions
            )
            
            # OpenAI returns embeddings in the same order as input
            return [data.embedding for data in response.data]
            
        except Exception as exc:
            logger.error("openai_embedding_failed", error=str(exc), model=self.model)
            raise

# Global singleton instance
_service: Optional[EmbeddingService] = None

def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
