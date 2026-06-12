"""Prompt assembly and length-policy orchestration extracted from inference."""

from __future__ import annotations

from api.prompts import build_prompt
from api.prompt_engine import PromptSpec
from api.services.inference.inference_prompting import (
    _apply_length_constraint,
    _drain_complete_sentences,
    _enforce_length_constraint,
    _enforce_word_limit,
    _extract_length_constraint,
    _is_large_input,
    _learning_length_policy,
)


class PromptOrchestrator:
    def build_prompt(self, query: str, context: str | None, mode: str) -> str:
        if mode == "socratic":
            return build_prompt(
                PromptSpec(query, depth="accessible", task="explain", reasoning="socratic"),
                conversation_context=context or "",
            )
        return build_prompt(PromptSpec(query, depth=context or "accessible"))

    def extract_length_constraint(self, text: str) -> tuple[str, int] | None:
        return _extract_length_constraint(text)

    def apply_length_constraints(self, prompt: str, constraint: tuple[str, int] | None) -> str:
        return _apply_length_constraint(prompt, constraint)

    def enforce_response_length(self, response: str, constraint: tuple[str, int] | None) -> str:
        return _enforce_length_constraint(response, constraint)

    def compress_context(self, turns: list[dict[str, str]], target_tokens: int) -> list[dict[str, str]]:
        if target_tokens <= 0 or len(turns) <= 1:
            return turns
        # Approximation: 1 token ~= 4 chars. Keep newest turns inside budget.
        approx_limit_chars = target_tokens * 4
        kept: list[dict[str, str]] = []
        used = 0
        for turn in reversed(turns):
            content = str(turn.get("content", ""))
            turn_cost = len(content)
            if kept and used + turn_cost > approx_limit_chars:
                break
            kept.append(turn)
            used += turn_cost
        kept.reverse()
        return kept or turns[-1:]

    def is_large_input(self, text: str) -> bool:
        return _is_large_input(text)

    def learning_length_policy(self, topic: str, depth: str | None = None) -> tuple[int, str | None]:
        return _learning_length_policy(topic, depth=depth)

    def max_output_tokens_for_depth(self, depth: str | None, *, default: int = 1024) -> int:
        from api.services.inference.inference_prompting import _max_output_tokens_for_depth

        return _max_output_tokens_for_depth(depth, default=default)

    def enforce_word_limit(self, text: str, limit: int, cue: str | None = None) -> str:
        return _enforce_word_limit(text, limit, cue=cue)

    def drain_complete_sentences(self, buffer: str) -> tuple[list[str], str]:
        return _drain_complete_sentences(buffer)
