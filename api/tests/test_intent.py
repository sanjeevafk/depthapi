import pytest
from services.intent import (
    detect_intent_and_depth,
    detect_diagram_type,
    validate_technical_response,
)


class TestDetectIntentAndDepth:
    def test_default_returns_explain_medium(self):
        result = detect_intent_and_depth("transformer")
        assert result == {"intent": "explain", "depth": "medium"}

    def test_what_is_returns_shallow(self):
        result = detect_intent_and_depth("what is backpropagation")
        assert result["depth"] == "shallow"
        assert result["intent"] == "explain"

    def test_how_returns_medium(self):
        result = detect_intent_and_depth("how does attention work")
        assert result["depth"] == "medium"

    def test_in_depth_returns_deep(self):
        result = detect_intent_and_depth("explain attention in depth")
        assert result["depth"] == "deep"

    def test_compare_intent(self):
        result = detect_intent_and_depth("compare RLHF vs DPO")
        assert result["intent"] == "compare"

    def test_architecture_returns_brainstorm(self):
        result = detect_intent_and_depth("design a rate limiter architecture")
        assert result["intent"] == "brainstorm"

    def test_compare_beats_brainstorm(self):
        # "compare" should win over "architecture" - priority order
        result = detect_intent_and_depth("compare architecture approaches")
        assert result["intent"] == "compare"

    def test_independent_intent_and_depth(self):
        # "compare" intent + "deep" depth should both be detected
        result = detect_intent_and_depth("compare RLHF vs DPO in depth")
        assert result["intent"] == "compare"
        assert result["depth"] == "deep"

    def test_what_is_architecture_brainstorm_wins(self):
        # "architecture" (brainstorm) should win over "what is" depth signal
        result = detect_intent_and_depth("what is a good architecture for this")
        assert result["intent"] == "brainstorm"


class TestDetectDiagramType:
    def test_architecture_returns_flowchart(self):
        assert detect_diagram_type("explain the architecture") == "flowchart"

    def test_sequence_returns_sequence_diagram(self):
        assert detect_diagram_type("show the request sequence") == "sequenceDiagram"

    def test_state_machine_returns_state_diagram(self):
        assert detect_diagram_type("model as a state machine") == "stateDiagram-v2"

    def test_no_match_returns_none(self):
        assert detect_diagram_type("what is entropy") is None

    def test_er_diagram_specific(self):
        assert detect_diagram_type("draw the er diagram for this schema") == "erDiagram"


class TestValidateTechnicalResponse:
    def test_empty_string_invalid(self):
        valid, reason = validate_technical_response("", "explain")
        assert not valid
        assert reason == "empty"

    def test_too_short_invalid(self):
        valid, reason = validate_technical_response("Short.", "explain")
        assert not valid
        assert reason == "too_short"

    def test_truncated_invalid(self):
        response = "## Core Idea\nThis is cut off mid" + ("x" * 200)
        valid, reason = validate_technical_response(response, "explain")
        assert not valid
        assert reason == "truncated"

    def test_valid_default_structure(self):
        response = (
            "## Core Idea\nSomething.\n\n"
            "## First Principles Breakdown\nDetail.\n\n"
            "## Intuition\nAnalogy here.\n\n"
            "## Edge Cases / Limitations\nLimitations.\n\n"
            "## Connections\nRelated concepts."
        )
        valid, reason = validate_technical_response(response, "explain")
        assert valid
        assert reason == ""

    def test_compare_valid_structure(self):
        response = (
            "## Option A\nSummary.\nStrengths.\nWeaknesses.\n\n"
            "## Option B\nSummary.\nStrengths.\nWeaknesses.\n\n"
            "## Key Differences\n- A\n- B\n- C\n\n"
            "## Recommendation\nGo with A."
        )
        valid, reason = validate_technical_response(response, "compare")
        assert valid
        assert reason == ""

    def test_brainstorm_valid_structure(self):
        response = (
            "## Approach 1: Simple\nIdea.\nTradeoffs.\nWhen to use.\n\n"
            "## Approach 2: Scalable\nIdea.\nTradeoffs.\nWhen to use.\n\n"
            "## Approach 3: Unconventional\nIdea.\nTradeoffs.\nWhen to use."
        )
        valid, _ = validate_technical_response(response, "brainstorm")
        assert valid

    def test_brainstorm_uses_brainstorm_headers(self):
        # Response with default headers should fail brainstorm validation
        response = (
            "## Core Idea\nSomething.\n\n"
            "## First Principles Breakdown\nDetail.\n\n"
            "## Intuition\nAnalogy.\n\n"
            "## Edge Cases / Limitations\nLimits.\n\n"
            "## Connections\nMore."
        )
        valid, reason = validate_technical_response(response, "brainstorm")
        assert not valid
        assert reason == "missing_brainstorm_structure"
