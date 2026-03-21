"""Configuration and environment variables."""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    environment: str = "development"
    log_user_hash_salt: str = ""
    litellm_base_url: str = ""
    litellm_virtual_key: str = ""
    litellm_master_key: str = ""
    litellm_timeout_seconds: int = 60

    stream_max_seconds: int = 25
    technical_stream_max_seconds: int = 45
    stream_heartbeat_seconds: int = 2
    stream_start_timeout_seconds: int = 2
    technical_stream_start_timeout_seconds: float = 6.0
    stream_idempotency_ttl_seconds: int = 90
    stream_idempotency_stale_seconds: int = 20
    stream_fallback_budget_seconds: int = 6
    trusted_proxies: str = ""

    redis_url: str = "redis://localhost:6379"
    upstash_redis_rest_url: str = ""
    upstash_redis_rest_token: str = ""
    cache_ttl: int = 86400  # 24 hours
    rate_limit_strategy: str = "upstash_redis"
    rate_limit_per_user: int = 20  # Requests per minute
    rate_limit_burst: int = 5
    rate_limit_burst_window_seconds: int = 10
    rate_limit_sustained_window_seconds: int = 60
    anonymous_rate_limit_per_ip: int = 8
    anonymous_rate_limit_burst: int = 3
    anonymous_rate_limit_window_seconds: int = 60
    daily_token_quota_per_user: int = 50000
    quota_window_seconds: int = 86400
    circuit_breaker_tokens_per_minute: int = 300000
    circuit_breaker_open_seconds: int = 60
    circuit_breaker_action: str = "reject"
    estimated_output_tokens_per_request: int = 900
    message_rate_limit_max: int = 30
    message_rate_limit_window_seconds: int = 60
    message_cache_ttl_seconds: int = 3600
    pro_state_cache_ttl_seconds: int = 30
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    tavily_api_key: str = ""
    serper_api_key: str = ""
    exa_api_key: str = ""

    sentry_dsn: str = ""
    sentry_enabled: bool = True
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.0
    sentry_release: str = ""
    
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
    return Settings()
