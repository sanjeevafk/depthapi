"""Public prompt-axis request models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from api.prompt_engine import PromptSpec

DepthValue = Literal["simple", "accessible", "technical", "expert"]
TaskValue = Literal["explain", "compare", "brainstorm", "analyze", "summarize"]
ReasoningValue = Literal["direct", "socratic", "debate", "guided"]
StyleValue = Literal["normal", "meme", "concise", "academic"]
CapabilityValue = Literal[
    "requires_search",
    "requires_diagram",
    "requires_context",
    "requires_citations",
]


class PromptSpecRequest(BaseModel):
    """Prompt axes accepted by public API payloads."""

    topic: str | None = Field(default=None, min_length=1, max_length=8000)
    depth: DepthValue = "accessible"
    task: TaskValue = "explain"
    reasoning: ReasoningValue = "direct"
    style: StyleValue = "normal"
    capabilities: list[CapabilityValue] = Field(default_factory=list)

    def to_prompt_spec(self, fallback_topic: str) -> PromptSpec:
        return PromptSpec(
            topic=(self.topic or fallback_topic).strip(),
            depth=self.depth,
            task=self.task,
            reasoning=self.reasoning,
            style=self.style,
            capabilities=frozenset(self.capabilities),
        )
