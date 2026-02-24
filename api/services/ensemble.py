"""Ensemble voting and judging logic."""

import asyncio
import json
import re
from prompts import JUDGE_PROMPT, JUDGE_MODEL, FREE_MODELS, PREMIUM_MODELS, FAST_MODEL
from services.inference import call_model, generate_explanation


async def ensemble_generate(topic: str, level: str, use_premium: bool = False, mode: str = "ensemble") -> str:
    """Generate with multiple models, pick best via judge."""
    if mode == "fast":
        # Fast mode: single model, no judge
        try:
            return await generate_explanation(topic, level, FAST_MODEL, is_pro=use_premium)
        except Exception as e:
            raise RuntimeError(f"Fast model failed: {e}")

    models = PREMIUM_MODELS if use_premium else FREE_MODELS
    tasks = [generate_explanation(topic, level, m, is_pro=use_premium) for m in models]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    valid = [(i, r) for i, r in enumerate(results) if isinstance(r, str) and r]
    if not valid:
        errors = [str(r) for r in results]
        raise RuntimeError(f"All models failed. Errors: {errors}")
    if len(valid) == 1:
        return valid[0][1]
    return await judge_responses(topic, [r for _, r in valid])


async def judge_responses(topic: str, responses: list[str]) -> str:
    """Use judge model to pick best response."""
    # Give judge more context - 1500 chars from each response
    resp_text = "\n".join(f"[{i}]: {r[:1500]}" for i, r in enumerate(responses))
    prompt = JUDGE_PROMPT.format(topic=topic, responses=resp_text)
    try:
        # Increase tokens for reason
        result = await call_model(JUDGE_MODEL, prompt, max_tokens=200)
        match = re.search(r'"best"\s*:\s*(\d+)', result)
        idx = int(match.group(1)) if match else 0
        return responses[min(idx, len(responses) - 1)]
    except Exception:
        return responses[0]


async def generate_all_levels(topic: str, levels: list[str], premium: bool = False) -> dict[str, str]:
    """Generate explanations for all levels in parallel."""
    tasks = {lvl: ensemble_generate(topic, lvl, premium) for lvl in levels}
    results = {}
    for lvl, task in tasks.items():
        try:
            results[lvl] = await task
        except Exception as e:
            results[lvl] = f"Error: {str(e)}"
    return results
