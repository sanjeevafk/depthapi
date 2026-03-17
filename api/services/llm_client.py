"""LiteLLM OpenAI-compatible client adapter."""

from __future__ import annotations

from typing import Any, AsyncGenerator
import asyncio
import time
from urllib.parse import urlparse

import sentry_sdk

from openai import AsyncOpenAI, APIStatusError, AuthenticationError, PermissionDeniedError
from openai.types.chat import ChatCompletionMessageParam

from config import get_settings
from services.llm_errors import LLMBadRequest, LLMInvalidAPIKey, LLMUnavailable


_client: AsyncOpenAI | None = None
_client_base_url: str | None = None
_client_api_key: str | None = None
_client_lock: asyncio.Lock | None = None


def _resolve_provider(model_name: str | None) -> str:
    if not model_name:
        return "unknown"
    if "/" not in model_name:
        return "alias"
    return model_name.split("/", 1)[0]


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


def _is_valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def get_litellm_config_state() -> dict[str, object]:
    """Return config validation state for LiteLLM without exposing secrets."""
    settings = get_settings()
    base_url_raw = (settings.litellm_base_url or "").strip()
    api_key = (settings.litellm_virtual_key or settings.litellm_master_key or "").strip()
    issues: list[dict[str, str]] = []

    if not base_url_raw:
        issues.append(
            {
                "code": "missing_base_url",
                "severity": "warning",
                "message": "LITELLM_BASE_URL is not configured.",
            }
        )
    elif not _is_valid_http_url(base_url_raw):
        issues.append(
            {
                "code": "invalid_base_url",
                "severity": "error",
                "message": "LITELLM_BASE_URL must be an absolute http(s) URL.",
            }
        )

    if not api_key:
        issues.append(
            {
                "code": "missing_api_key",
                "severity": "warning",
                "message": "LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY is not configured.",
            }
        )

    chat_enabled = not issues
    return {
        "chat_enabled": chat_enabled,
        "status": "ok" if chat_enabled else "degraded",
        "issues": issues,
        "base_url": base_url_raw,
        "has_api_key": bool(api_key),
    }


def _get_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        raise LLMUnavailable("LITELLM_BASE_URL is not configured.")

    normalized = base_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def _resolve_api_key() -> str:
    settings = get_settings()
    api_key = settings.litellm_virtual_key or settings.litellm_master_key
    if not api_key:
        raise LLMUnavailable("LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY is required.")
    return api_key


async def get_llm_client() -> AsyncOpenAI:
    """Return a singleton AsyncOpenAI client configured for LiteLLM."""
    global _client, _client_base_url, _client_api_key

    settings = get_settings()
    base_url = _normalize_base_url(settings.litellm_base_url)
    api_key = _resolve_api_key()

    async with _get_lock():
        if _client and _client_base_url == base_url and _client_api_key == api_key:
            return _client

        if _client:
            await _client.close()

        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.litellm_timeout_seconds,
        )
        _client_base_url = base_url
        _client_api_key = api_key
        return _client


async def create_chat_completion(model: str, messages: list[ChatCompletionMessageParam], **kwargs):
    """Create a chat completion via LiteLLM."""
    client = await get_llm_client()
    request_id = kwargs.pop("request_id", None)
    trace_headers = kwargs.pop("trace_headers", None)
    if request_id:
        existing_headers = kwargs.get("extra_headers")
        merged_headers: dict[str, str] = {}
        if isinstance(existing_headers, dict):
            merged_headers.update({str(k): str(v) for k, v in existing_headers.items()})
        merged_headers["x-request-id"] = str(request_id)
        kwargs["extra_headers"] = _merge_trace_headers(merged_headers, trace_headers if isinstance(trace_headers, dict) else None)
    elif isinstance(trace_headers, dict):
        existing_headers = kwargs.get("extra_headers")
        merged_headers: dict[str, str] = {}
        if isinstance(existing_headers, dict):
            merged_headers.update({str(k): str(v) for k, v in existing_headers.items()})
        kwargs["extra_headers"] = _merge_trace_headers(merged_headers, trace_headers)
    try:
        with sentry_sdk.start_span(op="llm.call", name=f"litellm.completion.{model}") as span:
            span.set_data("llm.model_alias", model)
            span.set_data("llm.provider", _resolve_provider(model))
            response = await client.chat.completions.create(model=model, messages=messages, **kwargs)
            usage_obj = getattr(response, "usage", None)
            usage = None
            if usage_obj is not None:
                if hasattr(usage_obj, "model_dump"):
                    usage = usage_obj.model_dump()
                elif hasattr(usage_obj, "dict"):
                    usage = usage_obj.dict()
                else:
                    usage = usage_obj
            if isinstance(usage, dict):
                span.set_data("llm.tokens.prompt", int(usage.get("prompt_tokens") or 0))
                span.set_data("llm.tokens.completion", int(usage.get("completion_tokens") or 0))
                span.set_data("llm.tokens.total", int(usage.get("total_tokens") or 0))
            resolved_model = str(getattr(response, "model", "") or "")
            if resolved_model:
                span.set_data("llm.model", resolved_model)
                span.set_data("llm.provider", _resolve_provider(resolved_model))
            return response
    except (AuthenticationError, PermissionDeniedError) as exc:
        sentry_sdk.capture_exception(exc)
        raise LLMInvalidAPIKey(
            "LiteLLM rejected credentials. Verify LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY."
        ) from exc
    except APIStatusError as exc:
        sentry_sdk.capture_exception(exc)
        if getattr(exc, "status_code", None) in {401, 403}:
            raise LLMInvalidAPIKey(
                "LiteLLM rejected credentials. Verify LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY."
            ) from exc
        if getattr(exc, "status_code", None) == 400:
            raise LLMBadRequest("LiteLLM rejected the request payload.") from exc
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise


async def stream_chat_completion(
    model: str, messages: list[ChatCompletionMessageParam], **kwargs
) -> AsyncGenerator[str, None]:
    """Stream chat completion text deltas via LiteLLM."""
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
    # Request usage metadata in the terminal stream chunk when available.
    stream_options = kwargs.get("stream_options")
    merged_stream_options: dict[str, Any] = {"include_usage": True}
    if isinstance(stream_options, dict):
        merged_stream_options.update(stream_options)
    kwargs["stream_options"] = merged_stream_options

    client = await get_llm_client()
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            **kwargs,
        )
    except (AuthenticationError, PermissionDeniedError) as exc:
        sentry_sdk.capture_exception(exc)
        raise LLMInvalidAPIKey(
            "LiteLLM rejected credentials. Verify LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY."
        ) from exc
    except APIStatusError as exc:
        sentry_sdk.capture_exception(exc)
        if getattr(exc, "status_code", None) in {401, 403}:
            raise LLMInvalidAPIKey(
                "LiteLLM rejected credentials. Verify LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY."
            ) from exc
        if getattr(exc, "status_code", None) == 400:
            raise LLMBadRequest("LiteLLM rejected the request payload.") from exc
        raise
    except Exception as exc:
        sentry_sdk.capture_exception(exc)
        raise

    stream_start = time.perf_counter()
    first_token_ms: float | None = None
    usage_summary: dict[str, int] | None = None
    estimated_cost_usd: float | None = None
    model_name: str | None = None

    with sentry_sdk.start_span(op="llm.call", name=f"litellm.stream.{model}") as llm_span:
        llm_span.set_data("llm.model_alias", model)
        llm_span.set_data("llm.provider", _resolve_provider(model))
        try:
            async for chunk in stream:
                model_name = getattr(chunk, "model", model_name)
                usage_obj = getattr(chunk, "usage", None)
                if usage_obj is not None:
                    if hasattr(usage_obj, "model_dump"):
                        usage_obj = usage_obj.model_dump()
                    elif hasattr(usage_obj, "dict"):
                        usage_obj = usage_obj.dict()
                    if isinstance(usage_obj, dict):
                        usage_summary = {
                            "prompt_tokens": int(usage_obj.get("prompt_tokens") or 0),
                            "completion_tokens": int(usage_obj.get("completion_tokens") or 0),
                            "total_tokens": int(usage_obj.get("total_tokens") or 0),
                        }

                direct_cost = getattr(chunk, "response_cost", None)
                if isinstance(direct_cost, (int, float)):
                    estimated_cost_usd = float(direct_cost)
                elif estimated_cost_usd is None:
                    # Fallback to hidden params only if direct cost not available
                    hidden_params = getattr(chunk, "_hidden_params", None)
                    if isinstance(hidden_params, dict):
                        hidden_cost = hidden_params.get("response_cost")
                        if isinstance(hidden_cost, (int, float)):
                            estimated_cost_usd = float(hidden_cost)

                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                content = getattr(delta, "content", None)
                if content:
                    if first_token_ms is None:
                        first_token_ms = round((time.perf_counter() - stream_start) * 1000, 2)
                    yield content
        except Exception as exc:
            sentry_sdk.capture_exception(exc)
            raise
        finally:
            llm_span.set_data("llm.model", model_name or model)
            llm_span.set_data("llm.provider", _resolve_provider(model_name or model))
            llm_span.set_data("llm.stream_duration_ms", round((time.perf_counter() - stream_start) * 1000, 2))
            if isinstance(usage_summary, dict):
                llm_span.set_data("llm.tokens.prompt", usage_summary.get("prompt_tokens"))
                llm_span.set_data("llm.tokens.completion", usage_summary.get("completion_tokens"))
                llm_span.set_data("llm.tokens.total", usage_summary.get("total_tokens"))
            if isinstance(estimated_cost_usd, float):
                llm_span.set_data("llm.cost_usd", estimated_cost_usd)

            if isinstance(telemetry_sink, dict):
                telemetry_sink["token_usage"] = usage_summary
                telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
                telemetry_sink["model"] = model_name
                telemetry_sink["model_inference_ms"] = first_token_ms
                telemetry_sink["stream_duration_ms"] = round((time.perf_counter() - stream_start) * 1000, 2)


async def close_llm_client() -> None:
    """Close the shared LiteLLM client."""
    global _client, _client_base_url, _client_api_key
    async with _get_lock():
        if _client is None:
            return
        await _client.close()
        _client = None
        _client_base_url = None
        _client_api_key = None
