"""Message construction helpers for inference flows."""

from __future__ import annotations

from prompts import SYSTEM_PROMPT
from utils import LEARNING_MODE, SOCRATIC_MODE, TECHNICAL_MODE

MODE_SYSTEM_PROMPTS = {
    LEARNING_MODE: (
        "Mode: Learning. Provide clear explanations and adapt depth to the user's request. "
        "Follow the user's query exactly. If the query asks for comparison, respond with a structured comparison. "
        "Do not ignore or override the latest user input."
    ),
    SOCRATIC_MODE: "Mode: Socratic. Guide the user with questions rather than direct answers.",
    TECHNICAL_MODE: "Mode: Technical. Provide precise, structured, technically rigorous responses.",
}

COMPARISON_SYSTEM_PROMPT = (
    "Compare the concepts clearly: definitions, key differences, use cases, and a concise table if helpful."
)


def is_comparison_query(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        " vs " in lowered
        or " versus " in lowered
        or "compare" in lowered
        or "comparison" in lowered
        or "difference between" in lowered
    )


def trim_history_for_cost(history: list[dict[str, str]] | None) -> list[dict[str, str]]:
    """Trim prior turns to keep prompt costs bounded."""
    if not history:
        return []
    max_turns = 6
    return history[-max_turns * 2 :]


def build_messages(
    prompt: str,
    *,
    conversation_messages: list[dict[str, str]] | None = None,
    intent_system_prompt: str | None = None,
    mode: str | None = None,
) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    system_parts: list[str] = []
    system_prompt = SYSTEM_PROMPT.strip()
    if system_prompt:
        system_parts.append(system_prompt)
    mode_prompt = MODE_SYSTEM_PROMPTS.get(mode or "", "").strip()
    if mode_prompt:
        system_parts.append(mode_prompt)
    if intent_system_prompt:
        system_parts.append(intent_system_prompt.strip())
    if mode == LEARNING_MODE and is_comparison_query(prompt):
        system_parts.append(COMPARISON_SYSTEM_PROMPT)
    if system_parts:
        messages.append({"role": "system", "content": "\n".join(system_parts)})
    if conversation_messages:
        messages.extend(trim_history_for_cost(conversation_messages))
    messages.append({"role": "user", "content": prompt})
    assert messages[-1].get("role") == "user"
    assert messages[-1].get("content") == prompt
    return messages
