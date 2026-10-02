"""Configuration and environment variables for DepthAPI."""
from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    environment: str = "development"
    database_url: str = "postgresql://depthapi:depthapi@localhost:5432/depthapi"
    redis_url: str = "redis://localhost:6379"
    allowed_origins: str = "http://localhost:3000,http://localhost:5173"

    # Inference (OpenAI-compatible)
    openai_api_key: SecretStr = SecretStr("")
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: int = 60

    # Local LLM fallback (Ollama, vLLM, llama.cpp)
    local_llm_base_url: str = ""
    local_llm_model: str = "qwen2.5:1.5b"
    local_llm_api_key: SecretStr = SecretStr("local")
    local_llm_timeout_seconds: int = 120
    local_llm_max_context_chunks: int = 3

    # Embeddings
    embedding_provider: str = "local"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimension: int = 768

    # Retrieval strategy: dense-first with hybrid fallback
    dense_hit_min_results: int = 5
    dense_hit_min_similarity: float = 0.5
    rerank_skip_similarity: float = 0.8

    # Query-result cache (Redis) and per-key daily token quotas
    query_cache_ttl_seconds: int = 3600
    quota_enabled: bool = True
    daily_token_quota_per_user: int = 50000
    pro_daily_token_quota: int = 200000

    # Rate limiting
    slowapi_enabled: bool = False
    slowapi_default_limit_per_minute: int = 120

    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("openai_api_key", "local_llm_api_key", mode="before")
    @classmethod
    def _normalize_provider_key(cls, value: object) -> SecretStr:
        if value is None:
            return SecretStr("")
        if isinstance(value, SecretStr):
            return SecretStr(value.get_secret_value().strip())
        if not isinstance(value, str):
            raise TypeError("Provider API keys must be strings.")
        return SecretStr(value.strip())

    @field_validator("llm_timeout_seconds", "local_llm_timeout_seconds")
    @classmethod
    def _validate_llm_timeout(cls, value: int) -> int:
        if value < 1:
            raise ValueError("LLM timeout must be at least 1 second.")
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


def reinitialize_cache() -> None:
    """Clear cache and recompute on next access (for testing)."""
    get_settings.cache_clear()
