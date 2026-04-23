from __future__ import annotations

from services.prompt_orchestrator import PromptOrchestrator


def test_apply_length_constraints_appends_limit_instruction() -> None:
    orchestrator = PromptOrchestrator()
    constrained = orchestrator.apply_length_constraints("Base prompt", ("words", 12))
    assert "at most 12 words" in constrained


def test_compress_context_keeps_latest_turns() -> None:
    orchestrator = PromptOrchestrator()
    turns = [
        {"role": "user", "content": "old " * 120},
        {"role": "assistant", "content": "mid " * 120},
        {"role": "user", "content": "new " * 20},
    ]
    kept = orchestrator.compress_context(turns, target_tokens=60)
    assert kept
    assert kept[-1]["content"].startswith("new")


def test_enforce_word_limit_trims_response() -> None:
    orchestrator = PromptOrchestrator()
    text = "one two three four five six"
    result = orchestrator.enforce_word_limit(text, 4)
    assert len(result.split()) <= 4
