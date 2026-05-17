from __future__ import annotations

import pytest

from api.prompt_engine import PromptSpec
from api.services.inference.llm_intent_classifier import classify_intent


@pytest.mark.asyncio
async def test_classifier_outputs_prompt_axes_and_prompt_spec() -> None:
    result = await classify_intent("Compare Redis vs Postgres in depth", use_llm=False)
    assert result.task == "compare"
    assert result.depth == "technical"
    assert result.reasoning == "direct"

    spec = result.to_prompt_spec("Compare Redis vs Postgres in depth")
    assert isinstance(spec, PromptSpec)
    assert spec.task == "compare"
    assert spec.depth == "technical"


@pytest.mark.asyncio
async def test_classifier_detects_guided_reasoning() -> None:
    result = await classify_intent("Walk me through Raft consensus step by step", use_llm=False)
    assert result.reasoning == "guided"
    assert result.task == "explain"
