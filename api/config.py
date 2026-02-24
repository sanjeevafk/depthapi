"""Configuration and environment variables."""

import os
import sys
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    environment: str = "development"
    groq_api_key: str = ""

    kaggle_api_token: str = ""
    gemini_api_key: str = ""
    redis_url: str = "redis://localhost:6379"
    cache_ttl: int = 86400  # 24 hours
    rate_limit_per_user: int = 20  # Requests per minute
    rate_limit_burst: int = 5
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
    return settings
