from __future__ import annotations

import pytest

import services.inference as inference_module
from services.prompt_orchestrator import PromptOrchestrator
from services.response_builder import ResponseBuilder


def test_prompt_orchestrator_extracts_and_applies_length_constraints() -> None:
    orchestrator = PromptOrchestrator()
    constraint = orchestrator.extract_length_constraint("Explain this in 7 words")
    assert constraint == ("words", 7)
    prompt = orchestrator.apply_length_constraints("Base prompt", constraint)
    assert "at most 7 words" in prompt


def test_response_builder_applies_socratic_fallback_when_empty() -> None:
    builder = ResponseBuilder()
    response = builder.apply_socratic_fallback("What is DNS?", "")
    assert isinstance(response, str)
    assert response.strip()


@pytest.mark.asyncio
async def test_generate_explanation_enforces_word_limit_constraint(monkeypatch) -> None:
    async def fake_load_search_context(_topic: str, *, mode: str):
        _ = mode
        return ""

    async def fake_call_with_quality_escalation(*_args, **_kwargs):
        return "one two three four five six seven eight"

    monkeypatch.setattr(inference_module.search_service, "load_search_context", fake_load_search_context)
    monkeypatch.setattr(inference_module, "_call_with_quality_escalation", fake_call_with_quality_escalation)

    response = await inference_module.generate_explanation(
        "Explain cache eviction in 5 words",
        "accessible",
        mode="learn",
    )
    assert len(response.split()) <= 5
