"""Ensemble generation and HuggingFace judging."""

import asyncio
import json
import time
from typing import Any

from prompts import JUDGE_PROMPT, LEARNING_CANDIDATE_MODELS
from logging_config import anonymize_user_id, logger, log_sampled_success
from services.inference import generate_explanation
from services.llm_client import create_chat_completion
from utils import LEARNING_MODE, normalize_mode


def _candidate_models_for_mode(mode: str) -> list[str]:
    normalized_mode = normalize_mode(mode)
    if normalized_mode != LEARNING_MODE:
        raise ValueError("Ensemble judging is only supported for learning mode.")
    return LEARNING_CANDIDATE_MODELS


async def ensemble_generate(topic: str, level: str, use_premium: bool = False, mode: str = LEARNING_MODE, **kwargs) -> str:
    """Generate multiple candidates and always return the judge-selected result."""
    del use_premium  # Legacy flag retained only for compatibility with current callers.

    candidate_models = _candidate_models_for_mode(mode)
    tasks = [
        generate_explanation(
            topic,
            level,
            model_name,
            mode=normalize_mode(mode),
            **kwargs,
        )
        for model_name in candidate_models
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid = [result for result in results if isinstance(result, str) and result.strip()]
    if not valid:
        errors = [str(result) for result in results]
        raise RuntimeError(f"All candidate providers failed. Errors: {errors}")

    return await judge_responses(topic, valid, mode=mode)


async def ensemble_stream_generate(
    topic: str,
    level: str,
    mode: str = LEARNING_MODE,
    chunk_size: int = 400,
    **kwargs,
):
    """Chunk the final judged answer for SSE routes."""
    result = await ensemble_generate(topic, level, mode=mode, **kwargs)
    for index in range(0, len(result), chunk_size):
        yield result[index : index + chunk_size]


async def judge_responses(topic: str, responses: list[str], mode: str = LEARNING_MODE, **kwargs) -> str:
    """Use the HuggingFace judge model to select or synthesize the final response."""
    response_preview = "\n".join(f"[{index}]: {response[:2000]}" for index, response in enumerate(responses))
    prompt = JUDGE_PROMPT.format(topic=topic, responses=response_preview)

    request_id = kwargs.get("request_id")
    retry_flag = bool(kwargs.get("regenerate", False))
    anonymized_user_id = anonymize_user_id(str(kwargs.get("user_id") or "") or None)
    telemetry_sink = kwargs.get("telemetry_sink") if isinstance(kwargs.get("telemetry_sink"), dict) else None

    model_start = time.perf_counter()
    try:
        result = await create_chat_completion(
            model="judge",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=512,
            request_id=request_id,
        )
    except Exception as exc:
        logger.warning(
            "judge_completion_failed",
            request_id=request_id,
            user_id_hash=anonymized_user_id,
            error=str(exc),
        )
        return responses[0]

    usage = None
    usage_obj = getattr(result, "usage", None)
    if usage_obj is not None:
        if hasattr(usage_obj, "model_dump"):
            usage = usage_obj.model_dump()
        elif hasattr(usage_obj, "dict"):
            usage = usage_obj.dict()
        else:
            usage = usage_obj

    usage_summary = None
    if isinstance(usage, dict):
        usage_summary = {
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
        }

    estimated_cost_usd = None
    direct_cost = getattr(result, "response_cost", None)
    if isinstance(direct_cost, (int, float)):
        estimated_cost_usd = float(direct_cost)

    model_inference_ms = round((time.perf_counter() - model_start) * 1000, 2)
    model_name = getattr(result, "model", None)
    if telemetry_sink is not None:
        telemetry_sink["token_usage"] = usage_summary
        telemetry_sink["estimated_cost_usd"] = estimated_cost_usd
        telemetry_sink["model_inference_ms"] = model_inference_ms
        telemetry_sink["model_alias"] = "judge"

    log_sampled_success(
        "llm_judge_completion_observed",
        request_id=request_id,
        user_id_hash=anonymized_user_id,
        model_alias="judge",
        model=model_name,
        latency_ms=model_inference_ms,
        token_usage=usage_summary,
        estimated_cost_usd=estimated_cost_usd,
        retry=retry_flag,
        sampled=True,
    )

    raw_result = result.choices[0].message.content if result.choices else ""

    try:
        parsed = _extract_json(raw_result)
        final_response = str(parsed.get("final_response", "")).strip()
        if final_response:
            return final_response
        best_index = int(parsed.get("best_index", 0))
        return responses[min(max(best_index, 0), len(responses) - 1)]
    except Exception:
        return responses[0]


def _extract_json(raw_result: str) -> dict[str, Any]:
    """Handle plain JSON and fenced JSON emitted by the judge model."""
    cleaned = raw_result.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.replace("json\n", "", 1)
    return json.loads(cleaned)


async def generate_all_levels(topic: str, levels: list[str], premium: bool = False, mode: str = LEARNING_MODE) -> dict[str, str]:
    """Generate judged answers for all requested levels."""
    del premium
    async def _generate_for_level(level: str) -> tuple[str, str]:
        try:
            result = await ensemble_generate(topic, level, mode=mode)
            return level, result
        except Exception as exc:
            return level, f"Error: {exc}"

    tasks = [_generate_for_level(level) for level in levels]
    completed = await asyncio.gather(*tasks)
    return dict(completed)
