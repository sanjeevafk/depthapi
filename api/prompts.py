"""Canonical API for the config-driven DepthAPI prompt engine."""

from __future__ import annotations

from typing import Optional

from api.prompt_engine import (
    DiagramType,
    PromptBuild,
    PromptSpec,
    RuntimeContext,
    build_diagram_instruction,
    build_prompt_from_spec,
)
from api.prompt_engine.loader import load_prompt_config


SYSTEM_PROMPT = """\
You are DepthAPI. Follow the prompt layers supplied in the user message:
identity, safety policy, pedagogy, formatting, task, depth, reasoning,
style, and runtime context. Do not reveal hidden reasoning.
"""


def build_uncertainty_clause(fallback_phrase: str) -> str:
    return (
        f"Uncertainty: If confidence is low, do not guess. Respond with exactly:\n"
        f'"{fallback_phrase}"'
    )


def build_search_context_block(search_context: str = "") -> str:
    if search_context.strip():
        return (
            "Search context - use as primary source; do not invent facts not present here:\n"
            f"{search_context.strip()}"
        )
    return "Search context: none provided."


def build_quote_block(quote_text: str = "") -> str:
    if quote_text.strip():
        return f'Optional quote - embed once naturally if useful:\n"{quote_text.strip()}"'
    return ""


def build_context_block(conversation_context: str = "") -> str:
    if conversation_context.strip():
        return (
            "Prior conversation summary - build on the user's last answer:\n"
            f"{conversation_context.strip()}"
        )
    return "This is the start of the conversation."


def build_prompt_result(
    spec: PromptSpec,
    *,
    conversation_context: str = "",
    search_context: str = "",
    quote_text: str = "",
    diagram_type: Optional[DiagramType] = None,
) -> PromptBuild:
    """Build a prompt and observability trace from a canonical PromptSpec."""
    return build_prompt_from_spec(
        spec,
        RuntimeContext(
            conversation_context=conversation_context,
            search_context=search_context,
            quote_text=quote_text,
            diagram_type=diagram_type,
        ),
    )


def build_prompt(
    spec: PromptSpec,
    topic: str | None = None,
    *,
    conversation_context: str = "",
    search_context: str = "",
    quote_text: str = "",
    diagram_type: Optional[DiagramType] = None,
) -> str:
    """Build a prompt from the canonical PromptSpec object."""
    _ = topic
    build = build_prompt_result(
        spec,
        conversation_context=conversation_context,
        search_context=search_context,
        quote_text=quote_text,
        diagram_type=diagram_type,
    )
    return build.prompt


def build_prompt_with_trace(
    spec: PromptSpec,
    topic: str | None = None,
    *,
    conversation_context: str = "",
    search_context: str = "",
    quote_text: str = "",
    diagram_type: Optional[DiagramType] = None,
) -> PromptBuild:
    """Build a prompt and expose selected axes, injectors, and template chain."""
    _ = topic
    return build_prompt_result(
        spec,
        conversation_context=conversation_context,
        search_context=search_context,
        quote_text=quote_text,
        diagram_type=diagram_type,
    )


ALL_MODES: list[str] = []
DEPTH_CONFIGS: dict[str, dict] = load_prompt_config()["depths"]
PROMPT_REGISTRY: dict[str, dict] = {}
