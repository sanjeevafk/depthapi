"""Model execution helpers (non-technical)."""

from __future__ import annotations

import time
import httpx
from typing import Any, AsyncGenerator

from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from logging_config import logger, anonymize_user_id, log_sampled_success
from services.llm_client import create_chat_completion


def _extract_usage_dict(usage_obj) -> dict[str, int] | None:
    if usage_obj is None:
        return None
    if hasattr(usage_obj, "model_dump"):
        usage_obj = usage_obj.model_dump()
    elif hasattr(usage_obj, "dict"):
        usage_obj = usage_obj.dict()
    if not isinstance(usage_obj, dict):
        return None

    prompt_tokens = usage_obj.get("prompt_tokens")
    completion_tokens = usage_obj.get("completion_tokens")
    total_tokens = usage_obj.get("total_tokens")
    try:
        return {
            "prompt_tokens": int(prompt_tokens or 0),
            "completion_tokens": int(completion_tokens or 0),
            "total_tokens": int(total_tokens or 0),
        }
    except (TypeError, ValueError):
        return None


def _extract_estimated_cost(result, usage: dict[str, int] | None) -> float | None:
    direct_cost = getattr(result, "response_cost", None)
    if isinstance(direct_cost, (int, float)):
        return float(direct_cost)

    hidden_params = getattr(result, "_hidden_params", None)
    if isinstance(hidden_params, dict):
        hidden_cost = hidden_params.get("response_cost")
        if isinstance(hidden_cost, (int, float)):
            return float(hidden_cost)

    if isinstance(usage, dict):
        usage_cost = usage.get("cost")
        if isinstance(usage_cost, (int, float)):
            return float(usage_cost)

    return None


@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException, httpx.HTTPStatusError)),
    reraise=True,
)
async def call_model(model: str | None, prompt: str, max_tokens: int = 300, **kwargs) -> str:
    """Call API with given model and prompt."""
    create_chat_completion_fn = kwargs.pop("create_chat_completion_fn", None)
    log_sampled_success_fn = kwargs.pop("log_sampled_success_fn", None)


    try:
        alias = model or "default-fast"
        request_id = kwargs.get("request_id")
        retry_flag = bool(kwargs.get("regenerate", False))
        anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
        telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None
        model_start = time.perf_counter()
        create_fn = create_chat_completion_fn or create_chat_completion
        result = await create_fn(
            alias,
            [{"role": "user", "content": prompt}],
            max_tokens=300,
            temperature=kwargs.get("temperature", 0.7),
            request_id=request_id,
        )
        model_inference_ms = round((time.perf_counter() - model_start) * 1000, 2)
        usage = _extract_usage_dict(getattr(result, "usage", None))
        estimated_cost_usd = _extract_estimated_cost(result, usage)
        model_name = getattr(result, "model", None)
        if telemetry_sink is not None:
            telemetry_sink["token_usage"] = usage
            telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
            telemetry_sink["model_inference_ms"] = model_inference_ms
            telemetry_sink["model_alias"] = alias

        if not result.choices:
            raise RuntimeError("LLM response missing choices.")

        first_choice = result.choices[0]
        message = getattr(first_choice, "message", None)
        if message is None:
            raise RuntimeError("LLM response missing message.")

        content = getattr(message, "content", "") or ""

        log_fn = log_sampled_success_fn or log_sampled_success
        log_fn(
            "llm_completion_observed",
            request_id=request_id,
            user_id_hash=anonymized_user_id,
            model_alias=alias,
            model=model_name,
            latency_ms=model_inference_ms,
            token_usage=usage,
            estimated_cost_usd=estimated_cost_usd,
            retry=retry_flag,
            sampled=True,
        )
        return content
    except Exception as e:
        logger.error(
            "inference_failed",
            error=str(e),
            model_alias=model or "default-fast",
            request_id=kwargs.get("request_id"),
            user_id_hash=anonymize_user_id(str(kwargs.get("user_id") or "") or None),
            retry=bool(kwargs.get("regenerate", False)),
            sampled=False,
        )
        raise


async def stream_model(
    *,
    model: str,
    messages: list[dict[str, str]],
    temperature: float,
    request_id: str | None,
    user_id_hash: str | None,
    retry: bool,
    telemetry_sink: dict[str, Any] | None,
    stream_chat_completion,
    route_telemetry_sink: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    stream_telemetry: dict[str, object] = (
        telemetry_sink if isinstance(telemetry_sink, dict) else {}
    )
    stream_start = time.perf_counter()
    async for chunk in stream_chat_completion(
        model=model,
        messages=messages,
        temperature=temperature,
        request_id=request_id,
        telemetry_sink=stream_telemetry,
    ):
        yield chunk

    stream_duration_ms = round((time.perf_counter() - stream_start) * 1000, 2)
    model_inference_ms = stream_telemetry.get("model_inference_ms")
    token_usage = stream_telemetry.get("token_usage")
    estimated_cost_usd = stream_telemetry.get("estimated_cost_usd")
    model_name = stream_telemetry.get("model")

    if route_telemetry_sink is not None:
        route_telemetry_sink["token_usage"] = token_usage
        route_telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
        route_telemetry_sink["model_inference_ms"] = model_inference_ms
        route_telemetry_sink["stream_duration_ms"] = stream_duration_ms
        route_telemetry_sink["model_alias"] = model
        route_telemetry_sink["model"] = model_name

    log_sampled_success(
        "llm_stream_observed",
        request_id=request_id,
        user_id_hash=user_id_hash,
        model_alias=model,
        model=model_name,
        latency_ms=model_inference_ms,
        stream_duration_ms=stream_duration_ms,
        token_usage=token_usage,
        estimated_cost_usd=estimated_cost_usd,
        retry=retry,
        sampled=True,
    )
