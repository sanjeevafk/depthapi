"""Ensemble generation and HuggingFace judging."""

import asyncio
import json
from typing import Any

from prompts import JUDGE_PROMPT, LEARNING_CANDIDATE_MODELS
from services.inference import generate_explanation
from services.model_provider import ModelProvider
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


async def judge_responses(topic: str, responses: list[str], mode: str = LEARNING_MODE) -> str:
    """Use the HuggingFace judge model to select or synthesize the final response."""
    response_preview = "\n".join(f"[{index}]: {response[:2000]}" for index, response in enumerate(responses))
    prompt = JUDGE_PROMPT.format(topic=topic, responses=response_preview)

    provider = ModelProvider.get_instance()
    raw_result = await provider.judge_candidates(prompt)

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
