"""Provider-backed OpenAI-compatible client and fallback runtime.

Responsibilities:
- Resolve provider/model candidate chains from registry aliases.
- Apply per-provider authentication and runtime health checks.
- Execute chat completion and streaming requests with failover.
- Expose provider configuration state for health and degraded-mode routing.
"""

from __future__ import annotations

from typing import Any, AsyncGenerator, cast
import asyncio
import json
import time

import sentry_sdk

from openai import (
    AsyncOpenAI,
    APIStatusError,
)
from openai.types.chat import ChatCompletionMessageParam

from config import get_settings
from logging_config import logger
from services.cache import get_redis
from services.llm_errors import LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable
from services.provider_authenticator import ProviderAuthenticator
from services.provider_registry import (
    PROVIDER_BASE_URLS,
    PROVIDER_PRIORITY,
    ProviderName,
    ProviderRegistry,
    ProviderTarget,
)
from services.provider_usage_tracker import ProviderUsageTracker
from services.fallback_orchestrator import FallbackOrchestrator
from services.redis_safe import safe_redis_call
from services.utils_shared import (
    extract_estimated_cost as extract_shared_estimated_cost,
    extract_usage_dict as extract_shared_usage_dict,
)


_provider_registry = ProviderRegistry()
_provider_authenticator = ProviderAuthenticator(_provider_registry)
_provider_usage_tracker = ProviderUsageTracker()
_fallback_orchestrator = FallbackOrchestrator(_provider_registry)


class ProviderStateManager:
    """Tracks transient provider health in Redis with in-memory fallback."""

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        cooldown_seconds: int = 60,
        state_ttl_seconds: int = 300,
    ) -> None:
        self.failure_threshold = max(failure_threshold, 1)
        self.cooldown_seconds = max(cooldown_seconds, 1)
        self.state_ttl_seconds = max(state_ttl_seconds, 30)
        self._lock = asyncio.Lock()
        self._memory_state: dict[str, dict[str, int | str]] = {}

    def _redis_key(self, provider: ProviderName) -> str:
        return f"knowbear:provider_state:{provider}"

    async def _read_state_unlocked(self, provider: ProviderName) -> dict[str, int | str]:
        state = dict(
            self._memory_state.get(
                provider,
                {
                    "status": "healthy",
                    "failure_count": 0,
                    "last_failure_ts": 0,
                    "blocked_until": 0,
                },
            )
        )

        try:
            redis = await safe_redis_call(get_redis, operation="connect")
            raw = await safe_redis_call(redis.get, self._redis_key(provider), operation="get") if redis else None
            if raw is not None:
                if isinstance(raw, (bytes, bytearray)):
                    payload = raw.decode("utf-8")
                else:
                    payload = str(raw)
                loaded = json.loads(payload)
                if isinstance(loaded, dict):
                    state = {
                        "status": str(loaded.get("status", "healthy") or "healthy"),
                        "failure_count": int(loaded.get("failure_count", loaded.get("failures", 0)) or 0),
                        "last_failure_ts": int(loaded.get("last_failure_ts", 0) or 0),
                        "blocked_until": int(loaded.get("blocked_until", 0) or 0),
                    }
                    self._memory_state[provider] = dict(state)
        except Exception as exc:
            # Redis may be unavailable; keep local memory state.
            logger.debug("provider_state_read_failed", provider=provider, error=str(exc))
            pass

        return state

    async def _read_state(self, provider: ProviderName) -> dict[str, int | str]:
        async with self._lock:
            return await self._read_state_unlocked(provider)

    async def _write_state_unlocked(self, provider: ProviderName, state: dict[str, int | str]) -> None:
        status = str(state.get("status", "healthy") or "healthy").lower()
        if status not in {"healthy", "degraded", "down"}:
            status = "degraded"
        normalized = {
            "status": status,
            "failure_count": int(state.get("failure_count", state.get("failures", 0)) or 0),
            "last_failure_ts": int(state.get("last_failure_ts", 0) or 0),
            "blocked_until": int(state.get("blocked_until", 0) or 0),
        }
        self._memory_state[provider] = dict(normalized)

        try:
            redis = await safe_redis_call(get_redis, operation="connect")
            if redis is not None:
                await safe_redis_call(
                    redis.setex,
                    self._redis_key(provider),
                    self.state_ttl_seconds,
                    json.dumps(normalized),
                    operation="setex",
                )
        except Exception as exc:
            logger.debug("provider_state_write_failed", provider=provider, error=str(exc))
            pass

    async def _write_state(self, provider: ProviderName, state: dict[str, int | str]) -> None:
        async with self._lock:
            await self._write_state_unlocked(provider, state)

    async def should_attempt(self, provider: ProviderName) -> bool:
        """Check provider health state and recover after cooldown."""
        now = int(time.time())
        async with self._lock:
            state = await self._read_state_unlocked(provider)
            blocked_until = int(state.get("blocked_until", 0) or 0)
            if blocked_until > now:
                return False

            # Cooldown elapsed: recover provider and clear transient failure state.
            if str(state.get("status", "healthy")) == "down":
                await self._write_state_unlocked(
                    provider,
                    {
                        "status": "healthy",
                        "failure_count": 0,
                        "last_failure_ts": int(state.get("last_failure_ts", 0) or 0),
                        "blocked_until": 0,
                    },
                )
            return True

    async def mark_success(self, provider: ProviderName) -> None:
        """Reset provider failure state after a successful call."""
        async with self._lock:
            await self._write_state_unlocked(
                provider,
                {"status": "healthy", "failure_count": 0, "last_failure_ts": 0, "blocked_until": 0},
            )

    async def mark_failure(self, provider: ProviderName) -> None:
        """Increment failure state and open cooldown when threshold is reached."""
        async with self._lock:
            now = int(time.time())
            state = await self._read_state_unlocked(provider)
            failures = int(state.get("failure_count", state.get("failures", 0)) or 0) + 1
            blocked_until = int(state.get("blocked_until", 0) or 0)
            status = "degraded"
            if failures >= self.failure_threshold:
                blocked_until = now + self.cooldown_seconds
                status = "down"
            await self._write_state_unlocked(
                provider,
                {
                    "status": status,
                    "failure_count": failures,
                    "last_failure_ts": now,
                    "blocked_until": blocked_until,
                },
            )


_clients: dict[ProviderName, AsyncOpenAI] = {}
_client_signatures: dict[ProviderName, str] = {}
_client_lock = asyncio.Lock()
_provider_state_manager = ProviderStateManager()


def _get_lock() -> asyncio.Lock:
    return _client_lock


def _merge_trace_headers(extra_headers: dict[str, str], trace_headers: dict[str, str] | None) -> dict[str, str]:
    merged = dict(extra_headers)
    if trace_headers:
        sentry_trace = trace_headers.get("sentry-trace")
        baggage = trace_headers.get("baggage")
        if sentry_trace:
            merged["sentry-trace"] = sentry_trace
        if baggage:
            merged["baggage"] = baggage

    current_trace = sentry_sdk.get_traceparent()
    current_baggage = sentry_sdk.get_baggage()
    if current_trace and "sentry-trace" not in merged:
        merged["sentry-trace"] = current_trace
    if current_baggage and "baggage" not in merged:
        merged["baggage"] = current_baggage
    return merged


def _get_timeout_seconds(provider: ProviderName | None = None) -> float:
    settings = get_settings()
    raw_timeout = getattr(settings, "llm_timeout_seconds", 60)
    if provider == "openrouter":
        raw_timeout = getattr(settings, "openrouter_timeout_seconds", raw_timeout)
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 60.0
    return max(timeout, 1.0)


def _provider_api_key(provider: ProviderName) -> str:
    return _provider_authenticator.get_api_key(provider)


def _openrouter_headers() -> dict[str, str]:
    return _provider_authenticator.openrouter_headers()


async def _get_provider_client(provider: ProviderName) -> AsyncOpenAI:
    api_key = _provider_api_key(provider)
    if not api_key:
        raise LLMUnavailable(f"Provider {provider} API key is not configured.")

    timeout = _get_timeout_seconds(provider)
    signature = f"{api_key}:{timeout}"

    async with _get_lock():
        existing = _clients.get(provider)
        if existing and _client_signatures.get(provider) == signature:
            return existing

        if existing is not None:
            await existing.close()

        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": PROVIDER_BASE_URLS[provider],
            "timeout": timeout,
        }
        if provider == "openrouter":
            kwargs["default_headers"] = _openrouter_headers()

        client = AsyncOpenAI(**kwargs)
        _clients[provider] = client
        _client_signatures[provider] = signature
        return client


def _build_candidate_chain(model_alias: str | None) -> list[ProviderTarget]:
    return _fallback_orchestrator.build_candidate_chain(model_alias)


def _is_retryable_error(exc: Exception) -> bool:
    return _fallback_orchestrator.is_retryable_error(exc)


def _is_auth_error(exc: Exception) -> bool:
    return _fallback_orchestrator.is_auth_error(exc)


def _extract_usage_dict(usage_obj: object) -> dict[str, int] | None:
    return extract_shared_usage_dict(usage_obj)


def _extract_estimated_cost(obj: object) -> float | None:
    return extract_shared_estimated_cost(obj, None)


def _day_bucket() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


def _provider_requests_key(provider: ProviderName) -> str:
    return f"knowbear:provider_usage:{provider}:requests:{_day_bucket()}"


def _provider_tokens_key(provider: ProviderName) -> str:
    return f"knowbear:provider_usage:{provider}:tokens:{_day_bucket()}"


async def _increment_provider_usage(provider: ProviderName, usage: dict[str, int] | None) -> None:
    await _provider_usage_tracker.record_usage(provider, usage)


async def _provider_within_runtime_limits(provider: ProviderName) -> bool:
    return await _provider_usage_tracker.within_runtime_limits(provider)


def get_provider_config_state() -> dict[str, object]:
    """Return provider config validation state without exposing secrets."""
    _provider_registry.reload_from_env()
    configured = _provider_registry.configured_providers()
    primary_configured = any(configured[p] for p in PROVIDER_PRIORITY)
    any_configured = primary_configured or configured["openrouter"]

    issues: list[dict[str, str]] = []
    if not any_configured:
        issues.append(
            {
                "code": "missing_provider_keys",
                "severity": "error",
                "message": "No AI provider API keys are configured.",
            }
        )

    for provider in PROVIDER_PRIORITY:
        if not configured[provider]:
            issues.append(
                {
                    "code": f"missing_{provider}_api_key",
                    "severity": "warning",
                    "message": f"{provider.upper()}_API_KEY is not configured.",
                }
            )

    if not configured["openrouter"]:
        issues.append(
            {
                "code": "missing_openrouter_api_key",
                "severity": "warning",
                "message": "OPENROUTER_API_KEY is not configured (optional fallback).",
            }
        )

    return {
        "chat_enabled": any_configured,
        "status": "ok" if any_configured else "degraded",
        "issues": issues,
        "providers": configured,
        "has_api_key": any_configured,
        "base_url": "",
    }




async def create_chat_completion(
    model: str,
    messages: list[ChatCompletionMessageParam],
    **kwargs,
) -> Any:
    """Create a chat completion with manual provider fallback."""
    request_id = kwargs.pop("request_id", None)
    trace_headers = kwargs.pop("trace_headers", None)

    if request_id:
        existing_headers = kwargs.get("extra_headers")
        merged_headers: dict[str, str] = {}
        if isinstance(existing_headers, dict):
            merged_headers.update({str(k): str(v) for k, v in existing_headers.items()})
        merged_headers["x-request-id"] = str(request_id)
        kwargs["extra_headers"] = _merge_trace_headers(
            merged_headers,
            trace_headers if isinstance(trace_headers, dict) else None,
        )
    elif isinstance(trace_headers, dict):
        existing_headers = kwargs.get("extra_headers")
        merged_headers: dict[str, str] = {}
        if isinstance(existing_headers, dict):
            merged_headers.update({str(k): str(v) for k, v in existing_headers.items()})
        kwargs["extra_headers"] = _merge_trace_headers(merged_headers, trace_headers)

    candidates = _build_candidate_chain(model)
    if not candidates:
        raise LLMUnavailable("No provider candidates were resolved for the request.")

    last_error: Exception | None = None
    alias = model or "default-fast"
    eligible_candidates: list[ProviderTarget] = []

    for candidate in candidates:
        provider = candidate.provider

        if not await _provider_state_manager.should_attempt(provider):
            logger.warning("provider_temporarily_blocked", provider=provider, model_alias=alias)
            continue
        if not await _provider_within_runtime_limits(provider):
            continue
        eligible_candidates.append(candidate)

    if not eligible_candidates:
        raise LLMUnavailable("No healthy providers are currently available.")

    for candidate in eligible_candidates:
        provider = candidate.provider
        provider_model = candidate.model

        try:
            client = await _get_provider_client(provider)
        except LLMUnavailable as exc:
            last_error = exc
            continue

        try:
            with sentry_sdk.start_span(op="llm.call", name=f"llm.completion.{provider}.{provider_model}") as span:
                span.set_data("llm.model_alias", alias)
                span.set_data("llm.provider", provider)
                span.set_data("llm.model", provider_model)
                response = await client.chat.completions.create(
                    model=provider_model,
                    messages=messages,
                    **kwargs,
                )
                usage = _extract_usage_dict(getattr(response, "usage", None))
                if isinstance(usage, dict):
                    span.set_data("llm.tokens.prompt", usage.get("prompt_tokens"))
                    span.set_data("llm.tokens.completion", usage.get("completion_tokens"))
                    span.set_data("llm.tokens.total", usage.get("total_tokens"))
                resolved_model = str(getattr(response, "model", "") or "")
                if resolved_model:
                    span.set_data("llm.model", resolved_model)
                await _provider_state_manager.mark_success(provider)
                await _increment_provider_usage(provider, usage)
                return response
        except Exception as exc:
            await _provider_state_manager.mark_failure(provider)
            sentry_sdk.capture_exception(exc)

            if _is_auth_error(exc):
                raise LLMInvalidAPIKey(f"Provider {provider} rejected credentials.") from exc

            if isinstance(exc, APIStatusError) and int(getattr(exc, "status_code", 0) or 0) == 400:
                raise LLMBadRequest(f"Provider {provider} rejected the request payload.") from exc

            last_error = exc
            if _is_retryable_error(exc):
                continue
            raise

    if last_error:
        raise LLMUnavailable("All configured providers failed to generate a response.") from last_error

    raise LLMUnavailable("No healthy providers are currently available.")


async def stream_chat_completion(
    model: str,
    messages: list[ChatCompletionMessageParam],
    **kwargs,
) -> AsyncGenerator[str, None]:
    """Stream chat completion text deltas with manual provider fallback."""
    kwargs.pop("stream", None)
    telemetry_sink = kwargs.pop("telemetry_sink", None)
    request_id = kwargs.pop("request_id", None)
    trace_headers = kwargs.pop("trace_headers", None)

    if request_id:
        existing_headers = kwargs.get("extra_headers")
        merged_headers: dict[str, str] = {}
        if isinstance(existing_headers, dict):
            merged_headers.update({str(k): str(v) for k, v in existing_headers.items()})
        merged_headers["x-request-id"] = str(request_id)
        kwargs["extra_headers"] = _merge_trace_headers(
            merged_headers,
            trace_headers if isinstance(trace_headers, dict) else None,
        )
    elif isinstance(trace_headers, dict):
        existing_headers = kwargs.get("extra_headers")
        merged_headers: dict[str, str] = {}
        if isinstance(existing_headers, dict):
            merged_headers.update({str(k): str(v) for k, v in existing_headers.items()})
        kwargs["extra_headers"] = _merge_trace_headers(merged_headers, trace_headers)

    stream_options = kwargs.get("stream_options")
    merged_stream_options: dict[str, Any] = {"include_usage": True}
    if isinstance(stream_options, dict):
        merged_stream_options.update(stream_options)
    kwargs["stream_options"] = merged_stream_options

    candidates = _build_candidate_chain(model)
    if not candidates:
        raise LLMUnavailable("No provider candidates were resolved for the request.")

    alias = model or "default-fast"
    last_error: Exception | None = None
    eligible_candidates: list[ProviderTarget] = []

    for candidate in candidates:
        provider = candidate.provider

        if not await _provider_state_manager.should_attempt(provider):
            logger.warning("provider_temporarily_blocked", provider=provider, model_alias=alias)
            continue
        if not await _provider_within_runtime_limits(provider):
            continue
        eligible_candidates.append(candidate)

    if not eligible_candidates:
        raise LLMUnavailable("No healthy providers are currently available.")

    for candidate in eligible_candidates:
        provider = candidate.provider
        provider_model = candidate.model

        try:
            client = await _get_provider_client(provider)
        except LLMUnavailable as exc:
            last_error = exc
            continue

        emitted_content = False
        stream_start = time.perf_counter()
        first_token_ms: float | None = None
        usage_summary: dict[str, int] | None = None
        estimated_cost_usd: float | None = None
        model_name: str | None = None

        try:
            stream = await client.chat.completions.create(
                model=provider_model,
                messages=messages,
                stream=True,
                **kwargs,
            )
        except Exception as exc:
            await _provider_state_manager.mark_failure(provider)
            sentry_sdk.capture_exception(exc)
            if _is_auth_error(exc):
                raise LLMInvalidAPIKey(f"Provider {provider} rejected credentials.") from exc
            if isinstance(exc, APIStatusError) and int(getattr(exc, "status_code", 0) or 0) == 400:
                raise LLMBadRequest(f"Provider {provider} rejected the request payload.") from exc
            last_error = exc
            if _is_retryable_error(exc):
                continue
            raise

        with sentry_sdk.start_span(op="llm.call", name=f"llm.stream.{provider}.{provider_model}") as llm_span:
            llm_span.set_data("llm.model_alias", alias)
            llm_span.set_data("llm.provider", provider)
            llm_span.set_data("llm.model", provider_model)

            try:
                async for chunk in stream:
                    model_name = getattr(chunk, "model", model_name)
                    usage_summary = _extract_usage_dict(getattr(chunk, "usage", None)) or usage_summary
                    extracted_cost = _extract_estimated_cost(chunk)
                    if extracted_cost is not None:
                        estimated_cost_usd = extracted_cost

                    if not getattr(chunk, "choices", None):
                        continue

                    delta = chunk.choices[0].delta
                    content = getattr(delta, "content", None)
                    if content:
                        if first_token_ms is None:
                            first_token_ms = round((time.perf_counter() - stream_start) * 1000, 2)
                        emitted_content = True
                        yield content

                await _provider_state_manager.mark_success(provider)
                await _increment_provider_usage(provider, usage_summary)
                return
            except Exception as exc:
                await _provider_state_manager.mark_failure(provider)
                sentry_sdk.capture_exception(exc)
                if emitted_content:
                    raise

                if _is_auth_error(exc):
                    raise LLMInvalidAPIKey(f"Provider {provider} rejected credentials.") from exc

                if isinstance(exc, APIStatusError) and int(getattr(exc, "status_code", 0) or 0) == 400:
                    raise LLMBadRequest(f"Provider {provider} rejected the request payload.") from exc

                last_error = exc
                if _is_retryable_error(exc):
                    continue
                raise
            finally:
                llm_span.set_data("llm.model", model_name or provider_model)
                llm_span.set_data("llm.provider", provider)
                llm_span.set_data(
                    "llm.stream_duration_ms",
                    round((time.perf_counter() - stream_start) * 1000, 2),
                )
                if isinstance(usage_summary, dict):
                    llm_span.set_data("llm.tokens.prompt", usage_summary.get("prompt_tokens"))
                    llm_span.set_data("llm.tokens.completion", usage_summary.get("completion_tokens"))
                    llm_span.set_data("llm.tokens.total", usage_summary.get("total_tokens"))
                if isinstance(estimated_cost_usd, float):
                    llm_span.set_data("llm.cost_usd", estimated_cost_usd)

                if isinstance(telemetry_sink, dict):
                    telemetry_sink["token_usage"] = usage_summary
                    telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
                    telemetry_sink["model"] = model_name or provider_model
                    telemetry_sink["provider"] = provider
                    telemetry_sink["model_inference_ms"] = first_token_ms
                    telemetry_sink["stream_duration_ms"] = round((time.perf_counter() - stream_start) * 1000, 2)

    if last_error:
        raise LLMUnavailable("All configured providers failed to stream a response.") from last_error

    raise LLMUnavailable("No healthy providers are currently available.")


async def close_llm_client() -> None:
    """Close all shared native provider clients."""
    async with _get_lock():
        clients = list(_clients.values())
        _clients.clear()
        _client_signatures.clear()

    for client in clients:
        await client.close()
