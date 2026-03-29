"""Configuration and environment variables."""

import os
from functools import lru_cache
from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    environment: str = "development"
    log_user_hash_salt: str = ""
    groq_api_key: SecretStr = SecretStr("")
    cerebras_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    llm_timeout_seconds: int = 60

    stream_max_seconds: int = 20
    technical_stream_max_seconds: int = 22
    stream_heartbeat_seconds: int = 2
    stream_start_timeout_seconds: int = 5
    technical_stream_start_timeout_seconds: float = 8.0
    stream_idempotency_ttl_seconds: int = 90
    stream_idempotency_stale_seconds: int = 20
    stream_fallback_budget_seconds: int = 8
    vercel_function_max_duration_seconds: int = 25
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
    free_daily_token_quota_learning: int = 50000
    free_hourly_token_quota_learning: int = 5000
    free_rpm_learning: int = 20
    free_burst_learning: int = 4
    pro_daily_token_quota: int = 200000
    pro_hourly_token_quota: int = 40000
    pro_rpm: int = 30
    pro_burst: int = 10
    anon_daily_token_quota: int = 5000
    anon_rph: int = 10
    max_output_tokens_learning: int = 1024
    max_output_tokens_socratic: int = 1024
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    tavily_api_key: str = ""
    serper_api_key: str = ""
    exa_api_key: str = ""
    cerebras_daily_token_budget: int = 100000

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
    # test_mode or live_mode (Dodo API / future SDK usage)
    dodo_environment: str = "test_mode"
    checkout_rate_limit_per_minute: int = 10

    class Config:
        env_file = (".env", "../.env")

        env_file_encoding = "utf-8"
        extra = "ignore"

    @field_validator(
        "groq_api_key",
        "cerebras_api_key",
        "gemini_api_key",
        "openrouter_api_key",
        mode="before",
    )
    @classmethod
    def _normalize_provider_key(cls, value: object) -> SecretStr:
        if value is None:
            return SecretStr("")
        if isinstance(value, SecretStr):
            return SecretStr(value.get_secret_value().strip())
        if not isinstance(value, str):
            raise TypeError("Provider API keys must be strings.")
        return SecretStr(value.strip())

    @field_validator("llm_timeout_seconds")
    @classmethod
    def _validate_llm_timeout(cls, value: int) -> int:
        if value < 1:
            raise ValueError("LLM timeout must be at least 1 second.")
        return value

    @field_validator("stream_max_seconds", "technical_stream_max_seconds", "vercel_function_max_duration_seconds")
    @classmethod
    def _validate_stream_caps(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Stream duration settings must be at least 1 second.")
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()
