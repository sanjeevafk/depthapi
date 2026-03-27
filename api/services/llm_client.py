"""Native provider-backed OpenAI-compatible client adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal, cast
import asyncio
import json
import time

import sentry_sdk

from pydantic import SecretStr
from openai import (
    AsyncOpenAI,
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    PermissionDeniedError,
)
from openai.types.chat import ChatCompletionMessageParam

from config import get_settings
from logging_config import logger
from services.cache import get_redis
from services.llm_errors import LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable


ProviderName = Literal["groq", "cerebras", "gemini", "openrouter"]

PROVIDER_PRIORITY: tuple[ProviderName, ...] = ("groq", "cerebras", "gemini")
PROVIDER_BASE_URLS: dict[ProviderName, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openrouter": "https://openrouter.ai/api/v1",
}
RETRYABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}

# Semantic alias -> provider-specific model IDs in fallback order.
MODEL_FALLBACK_MAP: dict[str, dict[ProviderName, str]] = {
    "default-fast": {
        "groq": "llama-3.1-8b-instant",
        "cerebras": "llama3.1-8b",
        "gemini": "gemini-2.5-flash",
    },
    "learning-detailed": {
        "groq": "llama-3.3-70b-versatile",
        "cerebras": "llama-3.3-70b",
        "gemini": "gemini-2.5-pro",
    },
    "technical-primary": {
        "groq": "llama-3.3-70b-versatile",
        "cerebras": "llama-3.3-70b",
        "gemini": "gemini-2.5-pro",
    },
    "technical-fallback": {
        "groq": "llama-3.1-8b-instant",
        "cerebras": "llama3.1-8b",
        "gemini": "gemini-2.5-flash",
    },
}
SOCRATIC_OPENROUTER_MODEL = "meta-llama/llama-3.3-70b-instruct"


@dataclass(frozen=True)
class ProviderTarget:
    provider: ProviderName
    model: str


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
        self._memory_state: dict[str, dict[str, int]] = {}

    def _redis_key(self, provider: ProviderName) -> str:
        return f"knowbear:provider_state:{provider}"

    async def _read_state_unlocked(self, provider: ProviderName) -> dict[str, int]:
        state = dict(self._memory_state.get(provider, {"failures": 0, "blocked_until": 0}))

        try:
            redis = await get_redis()
            raw = await redis.get(self._redis_key(provider))
            if raw is not None:
                if isinstance(raw, (bytes, bytearray)):
                    payload = raw.decode("utf-8")
                else:
                    payload = str(raw)
                loaded = json.loads(payload)
                if isinstance(loaded, dict):
                    state = {
                        "failures": int(loaded.get("failures", 0) or 0),
                        "blocked_until": int(loaded.get("blocked_until", 0) or 0),
                    }
                    self._memory_state[provider] = dict(state)
        except Exception:
            # Redis may be unavailable; keep local memory state.
            pass

        return state

    async def _read_state(self, provider: ProviderName) -> dict[str, int]:
        async with self._lock:
            return await self._read_state_unlocked(provider)

    async def _write_state_unlocked(self, provider: ProviderName, state: dict[str, int]) -> None:
        normalized = {
            "failures": int(state.get("failures", 0) or 0),
            "blocked_until": int(state.get("blocked_until", 0) or 0),
        }
        self._memory_state[provider] = dict(normalized)

        try:
            redis = await get_redis()
            await redis.setex(self._redis_key(provider), self.state_ttl_seconds, json.dumps(normalized))
        except Exception:
            pass

    async def _write_state(self, provider: ProviderName, state: dict[str, int]) -> None:
        async with self._lock:
            await self._write_state_unlocked(provider, state)

    async def should_attempt(self, provider: ProviderName) -> bool:
        state = await self._read_state(provider)
        return int(state.get("blocked_until", 0) or 0) <= int(time.time())

    async def mark_success(self, provider: ProviderName) -> None:
        async with self._lock:
            await self._write_state_unlocked(provider, {"failures": 0, "blocked_until": 0})

    async def mark_failure(self, provider: ProviderName) -> None:
        async with self._lock:
            state = await self._read_state_unlocked(provider)
            failures = int(state.get("failures", 0) or 0) + 1
            blocked_until = int(state.get("blocked_until", 0) or 0)
            if failures >= self.failure_threshold:
                blocked_until = int(time.time()) + self.cooldown_seconds
            await self._write_state_unlocked(provider, {"failures": failures, "blocked_until": blocked_until})


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


def _get_timeout_seconds() -> float:
    settings = get_settings()
    raw_timeout = getattr(settings, "llm_timeout_seconds", getattr(settings, "litellm_timeout_seconds", 60))
    try:
        timeout = float(raw_timeout)
    except (TypeError, ValueError):
        timeout = 60.0
    return max(timeout, 1.0)


def _provider_api_key(provider: ProviderName) -> str:
    settings = get_settings()
    lookup = {
        "groq": "groq_api_key",
        "cerebras": "cerebras_api_key",
        "gemini": "gemini_api_key",
        "openrouter": "openrouter_api_key",
    }
    value = getattr(settings, lookup[provider], "")
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    if not isinstance(value, str):
        return ""
    return value.strip()


def _openrouter_headers() -> dict[str, str]:
    return {
        "HTTP-Referer": "https://knowbear.vercel.app",
        "X-Title": "KnowBear",
    }


async def _get_provider_client(provider: ProviderName) -> AsyncOpenAI:
    api_key = _provider_api_key(provider)
    if not api_key:
        raise LLMUnavailable(f"Provider {provider} API key is not configured.")

    timeout = _get_timeout_seconds()
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
    alias = (model_alias or "default-fast").strip().lower()

    # Direct provider/model route support: e.g. "groq/llama-3.1-8b-instant".
    if "/" in alias:
        provider_name, raw_model = alias.split("/", 1)
        if provider_name in PROVIDER_BASE_URLS and raw_model:
            return [ProviderTarget(provider=provider_name, model=raw_model)]

    if alias == "socratic":
        return [ProviderTarget(provider="openrouter", model=SOCRATIC_OPENROUTER_MODEL)]

    model_map = MODEL_FALLBACK_MAP.get(alias) or MODEL_FALLBACK_MAP["default-fast"]
    return [
        ProviderTarget(provider=provider, model=model_map[provider])
        for provider in PROVIDER_PRIORITY
        if provider in model_map
    ]


def _is_retryable_error(exc: Exception) -> bool:
    if isinstance(exc, APIConnectionError):
        return True
    if isinstance(exc, APIStatusError):
        return int(getattr(exc, "status_code", 0) or 0) in RETRYABLE_STATUS_CODES
    return False


def _is_auth_error(exc: Exception) -> bool:
    if isinstance(exc, (AuthenticationError, PermissionDeniedError)):
        return True
    if isinstance(exc, APIStatusError):
        return int(getattr(exc, "status_code", 0) or 0) in {401, 403}
    return False


def _extract_usage_dict(usage_obj: object) -> dict[str, int] | None:
    if usage_obj is None:
        return None
    if hasattr(usage_obj, "model_dump"):
        usage_obj = cast(Any, usage_obj).model_dump()
    elif hasattr(usage_obj, "dict"):
        usage_obj = cast(Any, usage_obj).dict()
    if not isinstance(usage_obj, dict):
        return None

    try:
        return {
            "prompt_tokens": int(usage_obj.get("prompt_tokens") or 0),
            "completion_tokens": int(usage_obj.get("completion_tokens") or 0),
            "total_tokens": int(usage_obj.get("total_tokens") or 0),
        }
    except (TypeError, ValueError):
        return None


def _extract_estimated_cost(obj: object) -> float | None:
    direct_cost = getattr(obj, "response_cost", None)
    if isinstance(direct_cost, (int, float)):
        return float(direct_cost)

    hidden_params = getattr(obj, "_hidden_params", None)
    if isinstance(hidden_params, dict):
        hidden_cost = hidden_params.get("response_cost")
        if isinstance(hidden_cost, (int, float)):
            return float(hidden_cost)

    return None


def get_provider_config_state() -> dict[str, object]:
    """Return provider config validation state without exposing secrets."""
    configured = {provider: bool(_provider_api_key(provider)) for provider in PROVIDER_BASE_URLS}
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


def get_litellm_config_state() -> dict[str, object]:
    """Backward-compatible config probe name used by existing routers."""
    return get_provider_config_state()


async def create_chat_completion(model: str, messages: list[ChatCompletionMessageParam], **kwargs):
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

    for candidate in candidates:
        provider = candidate.provider
        provider_model = candidate.model

        if not await _provider_state_manager.should_attempt(provider):
            logger.warning("provider_temporarily_blocked", provider=provider, model_alias=alias)
            continue

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

    for candidate in candidates:
        provider = candidate.provider
        provider_model = candidate.model

        if not await _provider_state_manager.should_attempt(provider):
            logger.warning("provider_temporarily_blocked", provider=provider, model_alias=alias)
            continue

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
