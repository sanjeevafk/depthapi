"""Config-driven prompt engine public API."""

from api.prompt_engine.builder import (
    build_diagram_instruction,
    build_prompt_from_spec,
    with_capabilities,
)
from api.prompt_engine.models import (
    DiagramType,
    PromptBuild,
    PromptConfigError,
    PromptSpec,
    PromptSpecError,
    PromptTrace,
    RuntimeContext,
)

__all__ = [
    "DiagramType",
    "PromptBuild",
    "PromptConfigError",
    "PromptSpec",
    "PromptSpecError",
    "PromptTrace",
    "RuntimeContext",
    "build_diagram_instruction",
    "build_prompt_from_spec",
    "with_capabilities",
]
