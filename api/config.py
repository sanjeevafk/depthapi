"""Configuration and environment variables."""

import os
import sys
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    environment: str = "development"
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "qwen/qwen3.5-9b"
    openrouter_fallback_model: str = "anthropic/claude-sonnet-4.6"
    litellm_base_url: str = ""
    litellm_virtual_key: str = ""
    litellm_master_key: str = ""
    litellm_timeout_seconds: int = 60

    kaggle_api_token: str = ""
    gemini_api_key: str = ""
    gemini_primary_model: str = "gemini-2.5-pro"
    gemini_fallback_model: str = "gemini-2.5-flash"
    huggingface_fallback_model: str = "deepseek-ai/DeepSeek-R1"
    huggingface_secondary_model: str = "microsoft/phi-4"
    huggingface_judge_model: str = "MiniMaxAI/MiniMax-M2.5"
    redis_url: str = "redis://localhost:6379"
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    cache_ttl: int = 86400  # 24 hours
    rate_limit_per_user: int = 20  # Requests per minute
    rate_limit_burst: int = 5
    message_rate_limit_max: int = 30
    message_rate_limit_window_seconds: int = 60
    message_cache_ttl_seconds: int = 3600
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    tavily_api_key: str = ""
    serper_api_key: str = ""
    exa_api_key: str = ""
    
    # Dodo Payments Configuration
    dodo_api_key: str = ""
    dodo_webhook_secret: str = ""
    dodo_webhook_endpoint: str = ""
    dodo_webhook_url: str = ""
    dodo_payment_link_id: str = ""

    class Config:
        env_file = (".env", "../.env")

        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    settings = Settings()
    if not settings.gemini_api_key:
        print("WARNING: GEMINI_API_KEY not set. Gemini models will fail.", file=sys.stderr)
    if not settings.groq_api_key:
        print("WARNING: GROQ_API_KEY not set. Groq models will fail.", file=sys.stderr)
    if not settings.litellm_base_url:
        print("WARNING: LITELLM_BASE_URL not set. LiteLLM client will be unavailable.", file=sys.stderr)
    if not settings.litellm_virtual_key and not settings.litellm_master_key:
        print(
            "WARNING: LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY not set. LiteLLM client will be unavailable.",
            file=sys.stderr,
        )
    return settings
