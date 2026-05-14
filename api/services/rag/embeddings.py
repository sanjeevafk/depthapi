"""Embedding service for DepthAPI.
Supports OpenAI, Google Gemini, and local BGE models.
"""

import asyncio
from typing import List, Optional

import structlog
from google import genai
from google.genai import types as genai_types
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from sentence_transformers import SentenceTransformer
from api.config import get_settings

logger = structlog.get_logger(__name__)


class EmbeddingService:
    def __init__(self):
        settings = get_settings()
        self.provider = getattr(settings, "embedding_provider", "openai")
        self.model = getattr(settings, "embedding_model", "text-embedding-3-small")
        self.dimensions = getattr(settings, "embedding_dimension", 1536)

        self.reload_clients()

    def reload_clients(self):
        """Re-initializes all model clients based on current self.provider and self.model."""
        settings = get_settings()
        if self.provider == "gemini":
            api_key = getattr(settings, "gemini_api_key", "")
            if hasattr(api_key, "get_secret_value"):
                api_key = api_key.get_secret_value()
            api_key = str(api_key or "").strip()
            if not api_key:
                raise ValueError("GEMINI_API_KEY is required for Gemini embeddings")
            self.gemini_client = genai.Client(api_key=api_key)
        elif self.provider == "openai":
            api_key = getattr(settings, "openai_api_key", "")
            if hasattr(api_key, "get_secret_value"):
                api_key = api_key.get_secret_value()
            api_key = str(api_key or "").strip()
            if not api_key:
                raise ValueError("OPENAI_API_KEY is required for OpenAI embeddings")
            self.openai_client = AsyncOpenAI(api_key=api_key)
        elif self.provider == "local_bge":
            # Forcing CPU for stability as requested
            device = "cpu"
            self.local_model = SentenceTransformer(str(self.model), device=device)
            logger.info("local_model_loaded", model=self.model, device=device)
        else:
            raise ValueError(
                f"Unsupported embedding provider '{self.provider}'. "
                "Use one of: gemini, openai, local_bge."
            )

    def _gemini_model_candidates(self) -> list[str]:
        model = str(self.model or "").strip().removeprefix("models/")
        if not model:
            model = "gemini-embedding-001"
        return [model]

    @staticmethod
    def _extract_gemini_embeddings(result: object) -> List[List[float]]:
        embeddings = getattr(result, "embeddings", None)
        if embeddings is not None:
            out: List[List[float]] = []
            for item in embeddings:
                values = getattr(item, "values", None)
                if values is None:
                    continue
                out.append([float(v) for v in values])
            if out:
                return out
        raise ValueError("Unrecognized Gemini embedding response shape")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
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
                last_err: Exception | None = None
                for model_name in self._gemini_model_candidates():
                    try:
                        result = self.gemini_client.models.embed_content(
                            model=model_name,
                            contents=valid_texts,
                            config=genai_types.EmbedContentConfig(
                                task_type="RETRIEVAL_DOCUMENT",
                                output_dimensionality=self.dimensions,
                            ),
                        )
                        vectors = self._extract_gemini_embeddings(result)
                        if len(vectors) == len(valid_texts):
                            return vectors
                        # Some versions may collapse to one vector; keep deterministic behavior.
                        if len(vectors) == 1 and len(valid_texts) == 1:
                            return vectors
                        raise ValueError(
                            f"Gemini returned {len(vectors)} embeddings for {len(valid_texts)} inputs"
                        )
                    except Exception as exc:
                        last_err = exc
                        logger.warning(
                            "gemini_embedding_model_attempt_failed",
                            model=model_name,
                            error=str(exc),
                        )
                        continue
                if last_err:
                    raise last_err
                raise RuntimeError("No Gemini embedding model candidates available")
            elif self.provider == "openai":
                response = await self.openai_client.embeddings.create(
                    input=valid_texts, model=self.model, dimensions=self.dimensions
                )
                return [data.embedding for data in response.data]
            else:
                encode_kwargs = {
                    "normalize_embeddings": True,
                    "convert_to_numpy": True,
                    "show_progress_bar": False,
                    "batch_size": 16, # As requested
                    "precision": "float32", # float16 not supported on CPU, using float32 for accuracy
                }

                try:
                    # Try passing output_dimensionality directly (supported in newer sentence-transformers)
                    if int(self.dimensions) != 1024:
                        encode_kwargs["output_dimensionality"] = int(self.dimensions)

                    vectors = await asyncio.to_thread(
                        self.local_model.encode, valid_texts, **encode_kwargs
                    )
                except (TypeError, ValueError):
                    # Fallback for older versions or unsupported models: Encode at full dim then slice
                    # Note: Slicing works for Matryoshka-trained models like BGE-M3
                    del encode_kwargs["output_dimensionality"]
                    vectors = await asyncio.to_thread(
                        self.local_model.encode, valid_texts, **encode_kwargs
                    )
                    if int(self.dimensions) < vectors.shape[1]:
                        # Slice and re-normalize
                        import numpy as np

                        vectors = vectors[:, : int(self.dimensions)]
                        norms = np.linalg.norm(vectors, axis=1, keepdims=True)
                        vectors = vectors / np.where(norms == 0, 1, norms)

                out = vectors.tolist()
                if not out:
                    return []
                actual_dim = len(out[0])
                if actual_dim != int(self.dimensions):
                    raise ValueError(
                        f"local_bge dimension mismatch: model produced {actual_dim}, "
                        f"but EMBEDDING_DIMENSION is {self.dimensions}"
                    )
                return [[float(v) for v in row] for row in out]

        except Exception as exc:
            logger.error(
                "embedding_failed",
                provider=self.provider,
                error=str(exc),
                model=self.model,
            )
            raise


# Global singleton instance
_service: Optional[EmbeddingService] = None


def get_embedding_service() -> EmbeddingService:
    global _service
    if _service is None:
        _service = EmbeddingService()
    return _service
