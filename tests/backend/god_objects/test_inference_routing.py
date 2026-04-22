from __future__ import annotations

from services.inference_classifier import IntentClassifier
from services.model_router import ModelRouter


def test_intent_classifier_detects_compare_and_depth() -> None:
    classifier = IntentClassifier()
    result = classifier.detect_intent_and_depth("Compare TCP vs UDP in depth")
    assert result["intent"] == "compare"
    assert result["depth"] == "deep"


def test_model_router_returns_alias_for_technical_query() -> None:
    router = ModelRouter()
    alias = router.route_model(
        "How to optimize SQL query latency?",
        intent="explain",
        mode="technical",
        level="technical",
    )
    assert isinstance(alias, str)
    assert alias


def test_model_router_score_model_returns_scored_candidates() -> None:
    router = ModelRouter()
    scores = router.score_model(
        "Explain cache invalidation",
        {"complexity": 0.4, "reasoning": 0.5, "explanation": 0.8, "latency_priority": 0.3},
        mode="learn",
    )
    assert isinstance(scores, dict)
    assert scores
    assert all(isinstance(value, float) for value in scores.values())
