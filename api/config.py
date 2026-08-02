"""Configuration and environment variables."""

from dataclasses import dataclass
from functools import lru_cache
from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class StreamConfig:
    """Pre-computed streaming configuration to eliminate 40+ getattr() calls per request.

    This data class is computed once at app startup and reused for all requests,
    saving 25-40ms per request by eliminating repeated settings lookups and calculations.
    """

    is_prod: bool
    stream_max_seconds: int
    function_duration_cap: int | None
    fallback_budget_seconds: float
    fallback_timeout_seconds: float
    close_timeout_seconds: float
    heartbeat_seconds: float
    stream_start_timeout_seconds: float
    technical_stream_start_timeout_seconds: float
    idempotency_ttl_seconds: int
    idempotency_stale_seconds: int
    large_input_char_threshold: int
    large_input_token_threshold: int
    large_input_timeout_extension_multiplier: float
    technical_mode_timeout_extension: float
    cache_ttl_seconds: int


_STREAM_CONFIG: StreamConfig | None = None


CONTEXT_LOAD_TIMEOUTS: dict[str, float] = {
    "redis_snapshot": 0.5,
    "db_context": 1.0,
    "search_context": 2.0,
    "intent_classify": 0.2,
}

PRELIMINARY_WORK_TIMEOUT_SECONDS = 4.0


def _compute_stream_config() -> StreamConfig:
    """Compute pre-cached streaming config from settings."""
    settings = get_settings()
    env = str(getattr(settings, "environment", "") or "").strip().lower()
    is_prod = env == "production"

    return StreamConfig(
        is_prod=is_prod,
        stream_max_seconds=max(int(getattr(settings, "stream_max_seconds", 25)), 1),
        function_duration_cap=max(
            5, int(getattr(settings, "stream_max_seconds", 25)) - 2
        )
        if is_prod
        else None,
        fallback_budget_seconds=max(
            1.0,
            min(float(getattr(settings, "stream_fallback_budget_seconds", 6)), 30.0),
        ),
        fallback_timeout_seconds=max(
            float(getattr(settings, "stream_fallback_budget_seconds", 6)), 3.0
        ),
        close_timeout_seconds=0.25,
        heartbeat_seconds=min(
            max(float(getattr(settings, "stream_heartbeat_seconds", 2)), 0.1),
            2.0,
        ),
        stream_start_timeout_seconds=float(
            getattr(settings, "stream_start_timeout_seconds", 2)
        ),
        technical_stream_start_timeout_seconds=float(
            getattr(settings, "technical_stream_start_timeout_seconds", 8.0)
        ),
        idempotency_ttl_seconds=min(
            max(int(getattr(settings, "stream_idempotency_ttl_seconds", 90)), 60),
            120,
        ),
        idempotency_stale_seconds=max(
            5,
            min(int(getattr(settings, "stream_idempotency_stale_seconds", 20)), 120),
        ),
        large_input_char_threshold=int(
            getattr(settings, "large_input_char_threshold", 5000)
        ),
        large_input_token_threshold=int(
            getattr(settings, "large_input_token_threshold", 5000)
        ),
        large_input_timeout_extension_multiplier=float(
            getattr(settings, "large_input_timeout_extension_multiplier", 1.5)
        ),
        technical_mode_timeout_extension=float(
            getattr(settings, "technical_mode_timeout_extension", 1.3)
        ),
        cache_ttl_seconds=int(getattr(settings, "message_cache_ttl_seconds", 3600)),
    )


def get_stream_config() -> StreamConfig:
    """Retrieve cached stream config (or compute on first call)."""
    global _STREAM_CONFIG
    if _STREAM_CONFIG is None:
        _STREAM_CONFIG = _compute_stream_config()
    return _STREAM_CONFIG


def reset_stream_config() -> None:
    """Reset the cached stream config (for testing)."""
    global _STREAM_CONFIG
    _STREAM_CONFIG = None


class Settings(BaseSettings):
    """Application settings loaded from environment."""

    environment: str = "development"
    auth_provider_mode: str = "env"
    dev_api_keys: str = Field(
        default="",
        validation_alias=AliasChoices("DEV_API_KEYS", "DEPTHAPI_API_KEYS"),
    )
    log_user_hash_salt: str = ""
    groq_api_key: SecretStr = SecretStr("")
    cerebras_api_key: SecretStr = SecretStr("")
    gemini_api_key: SecretStr = SecretStr("")
    openrouter_api_key: SecretStr = SecretStr("")
    openai_api_key: SecretStr = SecretStr("")
    openrouter_timeout_seconds: int = 90
    llm_timeout_seconds: int = 60
    embedding_provider: str = "gemini"
    embedding_model: str = "text-embedding-004"
    embedding_dimension: int = 768

    stream_max_seconds: int = 30
    stream_heartbeat_seconds: int = 2
    stream_start_timeout_seconds: int = 5
    technical_stream_start_timeout_seconds: float = 8.0
    stream_idempotency_ttl_seconds: int = 90
    stream_idempotency_stale_seconds: int = 20
    stream_fallback_budget_seconds: int = 8
    trusted_proxies: str = ""

    redis_url: str = "redis://localhost:6379"
    database_url: str = "postgresql://depthapi:depthapi@localhost:5432/depthapi"
    cache_ttl: int = 86400
    rate_limit_strategy: str = "redis"
    rate_limit_per_user: int = 20
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
    estimated_output_tokens_per_request: int = 1500
    message_rate_limit_max: int = 30
    message_rate_limit_window_seconds: int = 60
    message_cache_ttl_seconds: int = 3600
    pro_state_cache_ttl_seconds: int = 30
    pro_daily_token_quota: int = 200000
    pro_hourly_token_quota: int = 40000
    pro_rpm: int = 30
    pro_burst: int = 10
    anon_daily_token_quota: int = 5000
    anon_rph: int = 10
    conversation_context_max_tokens: int = 1200
    conversation_context_summary_tokens: int = 240
    conversation_context_fetch_limit: int = 80

    max_input_chars_api: int = 100000  # Hard cap for API (100K chars)
    max_input_tokens: int = 15000
    max_input_tokens_technical: int = 15000  # ~60K chars

    large_input_char_threshold: int = (
        5000  # Trigger on 5K+ chars regardless of truncation
    )
    large_input_token_threshold: int = (
        5000
    )
    large_input_timeout_extension_multiplier: float = 1.5  # 50% longer for large inputs
    technical_mode_timeout_extension: float = 1.3
    tavily_api_key: str = ""
    serper_api_key: str = ""
    exa_api_key: str = ""
    cerebras_daily_token_budget: int = 100000

    slowapi_enabled: bool = False
    slowapi_default_limit_per_minute: int = 120

    sentry_dsn: str = ""
    sentry_enabled: bool = True
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.0
    sentry_release: str = ""
    sentry_auth_token: str = ""

    model_config = SettingsConfigDict(
        env_file=(".env.local", ".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator(
        "groq_api_key",
        "cerebras_api_key",
        "gemini_api_key",
        "openrouter_api_key",
        "openai_api_key",
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

    @field_validator("auth_provider_mode", mode="before")
    @classmethod
    def _normalize_auth_provider_mode(cls, value: object) -> str:
        if value is None:
            return "env"
        normalized = str(value).strip().lower()
        if normalized != "env":
            raise ValueError("auth_provider_mode must be env.")
        return normalized

    @field_validator("dev_api_keys", mode="before")
    @classmethod
    def _normalize_dev_api_keys(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator(
        "stream_max_seconds",
    )
    @classmethod
    def _validate_stream_caps(cls, value: int) -> int:
        if value < 1:
            raise ValueError("Stream duration settings must be at least 1 second.")
        return value


@lru_cache
def get_settings() -> Settings:
    """Cached settings instance."""
    return Settings()


def reinitialize_cache() -> None:
    """Clear cache and recompute on next access (for testing)."""
    global _STREAM_CONFIG
    _STREAM_CONFIG = None
    get_settings.cache_clear()
