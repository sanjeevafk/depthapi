"""Composable prompt builder."""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from api.prompt_engine.loader import load_prompt_config
from api.prompt_engine.models import (
    DiagramType,
    PromptBuild,
    PromptSpec,
    PromptSpecError,
    PromptTrace,
    RuntimeContext,
    VALID_DEPTHS,
    VALID_REASONING,
    VALID_STYLES,
    VALID_TASKS,
)


def build_diagram_instruction(diagram_type: DiagramType) -> str:
    return (
        f"Include a valid Mermaid code block using `{diagram_type.value}` syntax.\n"
        "The diagram must represent the core mechanism, not restate the text.\n"
        "Keep it under 15 nodes. Use short, clear labels."
    )


def build_prompt_from_spec(
    spec: PromptSpec,
    runtime: RuntimeContext | None = None,
) -> PromptBuild:
    runtime = runtime or RuntimeContext()
    config = load_prompt_config()
    _validate_spec(spec, runtime, config)

    layer_names = (
        "core_identity",
        "safety_policy",
        "pedagogy_rules",
        "formatting_rules",
    )
    selected = [
        ("layers", name, config["layers"][name])
        for name in layer_names
    ]
    selected.extend(
        [
            ("tasks", spec.task, config["tasks"][spec.task]),
            ("depths", spec.depth, config["depths"][spec.depth]),
            ("reasoning", spec.reasoning, config["reasoning"][spec.reasoning]),
            ("styles", spec.style, config["styles"][spec.style]),
        ]
    )

    injector_names = _resolve_injectors(spec, selected, config["schema"].get("capabilities", []))
    if runtime.search_context.strip() and "search_context" not in injector_names:
        injector_names.append("search_context")
    if runtime.diagram_type is not None and "diagram" not in injector_names:
        injector_names.append("diagram")
    injector_blocks = []
    applied_injectors = []
    for name in injector_names:
        block = _render_injector(name, config["injectors"][name], spec, runtime)
        if block:
            injector_blocks.append(block)
            applied_injectors.append(name)

    values = _template_values(spec, runtime)
    blocks = [fragment["template"].format(**values).strip() for _, _, fragment in selected]
    blocks.extend(injector_blocks)
    prompt = "\n\n".join(block for block in blocks if block).strip()

    trace = PromptTrace(
        depth=spec.depth,
        task=spec.task,
        reasoning=spec.reasoning,
        style=spec.style,
        requested_capabilities=tuple(sorted(spec.capabilities)),
        applied_injectors=tuple(applied_injectors),
        template_chain=tuple(f"{group}/{name}" for group, name, _ in selected)
        + tuple(f"injectors/{name}" for name in applied_injectors),
    )
    return PromptBuild(prompt=prompt, trace=trace)


def _validate_spec(spec: PromptSpec, runtime: RuntimeContext, config: dict) -> None:
    if not isinstance(spec.topic, str) or not spec.topic.strip():
        raise PromptSpecError("PromptSpec.topic must be a non-empty string")
    for field_name, value, valid in (
        ("depth", spec.depth, VALID_DEPTHS),
        ("task", spec.task, VALID_TASKS),
        ("reasoning", spec.reasoning, VALID_REASONING),
        ("style", spec.style, VALID_STYLES),
    ):
        if value not in valid:
            raise PromptSpecError(
                f"Unsupported {field_name} '{value}'. Valid values: {sorted(valid)}"
            )

    capabilities = set(spec.capabilities)
    known_capabilities = set(config["schema"].get("capabilities", []))
    unknown = capabilities - known_capabilities
    if unknown:
        raise PromptSpecError(f"Unknown capabilities requested: {sorted(unknown)}")

    for rule in config["schema"].get("incompatible", []):
        if all(getattr(spec, key) == value for key, value in rule["when"].items()):
            raise PromptSpecError(rule["message"])

    runtime_requirements = {
        "requires_context": bool(runtime.conversation_context.strip()),
        "requires_search": bool(runtime.search_context.strip()),
        "requires_citations": bool(runtime.search_context.strip()),
        "requires_diagram": runtime.diagram_type is not None,
    }
    requirement_order = list(config["schema"].get("capabilities", []))
    for requirement in requirement_order:
        if requirement not in capabilities:
            continue
        if requirement in runtime_requirements and not runtime_requirements[requirement]:
            raise PromptSpecError(f"Capability '{requirement}' requires missing runtime data")


def _resolve_injectors(
    spec: PromptSpec,
    selected: Iterable[tuple[str, str, dict]],
    capability_order: Iterable[str],
) -> list[str]:
    injectors: list[str] = []
    for _, _, fragment in selected:
        for injector in fragment.get("injectors", []):
            if injector not in injectors:
                injectors.append(injector)
    capability_injectors = {
        "requires_context": "conversation_context",
        "requires_search": "search_context",
        "requires_citations": "search_context",
        "requires_diagram": "diagram",
    }
    for capability in capability_order:
        if capability not in spec.capabilities:
            continue
        injector = capability_injectors.get(capability)
        if injector and injector not in injectors:
            injectors.append(injector)
    return injectors


def _render_injector(
    name: str,
    fragment: dict,
    spec: PromptSpec,
    runtime: RuntimeContext,
) -> str:
    values = _template_values(spec, runtime)
    if name == "search_context" and not runtime.search_context.strip():
        return ""
    if name == "conversation_context" and not runtime.conversation_context.strip():
        values = values | {
            "conversation_context": (
                "This is the start of the conversation. Ask the most fundamental "
                f"clarifying question about {spec.topic}."
            )
        }
    if name == "diagram":
        if runtime.diagram_type is None:
            return ""
        values = values | {"diagram_instruction": build_diagram_instruction(runtime.diagram_type)}
    return fragment["template"].format(**values).strip()


def _template_values(spec: PromptSpec, runtime: RuntimeContext) -> dict[str, str]:
    return {
        "topic": spec.topic,
        "conversation_context": runtime.conversation_context.strip(),
        "search_context": runtime.search_context.strip(),
        "diagram_instruction": (
            build_diagram_instruction(runtime.diagram_type)
            if runtime.diagram_type is not None
            else ""
        ),
    }


def with_capabilities(spec: PromptSpec, capabilities: Iterable[str]) -> PromptSpec:
    return replace(spec, capabilities=frozenset(set(spec.capabilities) | set(capabilities)))
