from __future__ import annotations

from services.inference_classifier import IntentClassifier


def test_detect_intent_returns_label_and_confidence() -> None:
    classifier = IntentClassifier()
    intent, confidence = classifier.detect_intent("Compare TCP vs UDP")
    assert intent == "compare"
    assert confidence > 0


def test_detect_depth_detects_deep_queries() -> None:
    classifier = IntentClassifier()
    depth = classifier.detect_depth("Explain caching in depth")
    assert depth == "deep"


def test_detect_diagram_type_detects_flow_like_queries() -> None:
    classifier = IntentClassifier()
    diagram = classifier.detect_diagram_type("Show architecture flow for requests")
    assert diagram in {"flowchart", "sequenceDiagram", "classDiagram", "erDiagram", "stateDiagram-v2", "timeline"}
