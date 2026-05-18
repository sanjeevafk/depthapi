"""Structured prompt-engine models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class DiagramType(str, Enum):
    FLOWCHART = "flowchart LR"
    FLOWCHART_TD = "flowchart TD"
    SEQUENCE = "sequenceDiagram"
    CLASS = "classDiagram"
    ER = "erDiagram"
    STATE = "stateDiagram-v2"


VALID_DEPTHS = {"simple", "accessible", "technical", "expert"}
VALID_TASKS = {"explain", "compare", "brainstorm", "analyze", "summarize"}
VALID_REASONING = {"direct", "socratic", "debate", "guided"}
VALID_STYLES = {"normal", "meme", "concise", "academic"}


@dataclass(frozen=True)
class PromptSpec:
    """The canonical prompt request.

    Each field models one independent prompt axis.
    """

    topic: str
    depth: str = "accessible"
    task: str = "explain"
    reasoning: str = "direct"
    style: str = "normal"
    capabilities: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class RuntimeContext:
    """Optional runtime data that prompt injectors may consume."""

    conversation_context: str = ""
    search_context: str = ""
    quote_text: str = ""
    diagram_type: DiagramType | None = None


@dataclass(frozen=True)
class PromptTrace:
    """Debug metadata emitted for prompt analytics and troubleshooting."""

    depth: str
    task: str
    reasoning: str
    style: str
    requested_capabilities: tuple[str, ...]
    applied_injectors: tuple[str, ...]
    template_chain: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "depth": self.depth,
            "task": self.task,
            "reasoning": self.reasoning,
            "style": self.style,
            "requested_capabilities": list(self.requested_capabilities),
            "applied_injectors": list(self.applied_injectors),
            "template_chain": list(self.template_chain),
        }


@dataclass(frozen=True)
class PromptBuild:
    prompt: str
    trace: PromptTrace


class PromptConfigError(ValueError):
    """Raised when prompt config files are missing, invalid, or inconsistent."""


class PromptSpecError(ValueError):
    """Raised when a PromptSpec is invalid for deterministic composition."""


JsonDict = dict[str, Any]
