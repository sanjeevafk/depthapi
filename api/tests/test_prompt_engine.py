from __future__ import annotations

import json

import pytest

from api.prompt_engine import DiagramType, PromptSpec, PromptSpecError, RuntimeContext
from api.prompt_engine.builder import build_prompt_from_spec
from api.prompt_engine.loader import CONFIG_ROOT, load_prompt_config
from api.prompts import build_prompt, build_prompt_with_trace


def test_schema_and_all_json_configs_load() -> None:
    for path in CONFIG_ROOT.rglob("*.json"):
        with path.open("r", encoding="utf-8") as handle:
            assert isinstance(json.load(handle), dict), path
    config = load_prompt_config()
    assert set(config["depths"]) == {"simple", "accessible", "technical", "expert"}
    assert set(config["tasks"]) == {"explain", "compare", "brainstorm", "analyze", "summarize"}
    assert set(config["reasoning"]) == {"direct", "socratic", "debate", "guided"}
    assert set(config["styles"]) == {"normal", "meme", "concise", "academic"}


def test_prompt_spec_composes_axes_without_compound_mode() -> None:
    build = build_prompt_from_spec(
        PromptSpec(
            topic="Vector databases",
            depth="technical",
            task="compare",
            reasoning="direct",
            style="academic",
        )
    )
    assert "Task template - compare" in build.prompt
    assert "Depth modifier - technical" in build.prompt
    assert "Presentation style - academic" in build.prompt
    assert "technical_compare" not in build.prompt


def test_runtime_capability_gating_fails_early() -> None:
    spec = PromptSpec(
        topic="Raft consensus",
        depth="technical",
        task="analyze",
        reasoning="direct",
        style="academic",
        capabilities=frozenset({"requires_diagram"}),
    )
    with pytest.raises(PromptSpecError, match="requires_diagram"):
        build_prompt_from_spec(spec)


def test_runtime_injectors_and_observability_trace() -> None:
    spec = PromptSpec(
        topic="Raft consensus",
        depth="technical",
        task="analyze",
        reasoning="direct",
        style="academic",
        capabilities=frozenset({"requires_diagram", "requires_search"}),
    )
    build = build_prompt_from_spec(
        spec,
        RuntimeContext(
            search_context="RFC-style notes with https://example.test/raft",
            diagram_type=DiagramType.FLOWCHART_TD,
        ),
    )
    assert "Runtime context - search" in build.prompt
    assert "Runtime context - diagram" in build.prompt
    assert build.trace.applied_injectors == ("search_context", "diagram")
    assert "tasks/analyze" in build.trace.template_chain
    assert build.trace.depth == "technical"
    serialized = build.trace.to_dict()
    assert serialized["applied_injectors"] == ["search_context", "diagram"]
    assert serialized["depth"] == "technical"


def test_incompatible_combination_is_rejected() -> None:
    spec = PromptSpec(
        topic="Distributed systems",
        depth="expert",
        task="explain",
        reasoning="direct",
        style="meme",
    )
    with pytest.raises(PromptSpecError, match="Meme style"):
        build_prompt_from_spec(spec)


def test_prompt_spec_is_canonical() -> None:
    spec = PromptSpec(
        topic="Postgres vs Redis",
        depth="technical",
        task="compare",
        reasoning="direct",
        style="academic",
    )
    prompt = build_prompt(spec)
    trace = build_prompt_with_trace(spec).trace
    assert "Task template - compare" in prompt
    assert trace.task == "compare"


def test_snapshot_accessible_explain_prompt_shape() -> None:
    prompt = build_prompt(
        PromptSpec(
            topic="DNS caching",
            depth="accessible",
            task="explain",
            reasoning="direct",
            style="normal",
        )
    )
    expected_sections = [
        "Core identity:",
        "Safety policy:",
        "Task template - explain:",
        "Depth modifier - accessible:",
        "Reasoning mode - direct:",
        "Presentation style - normal:",
    ]
    positions = [prompt.index(section) for section in expected_sections]
    assert positions == sorted(positions)
