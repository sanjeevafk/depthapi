from __future__ import annotations

from services.model_router import ModelRouter


def test_route_model_returns_non_empty_alias_for_learning() -> None:
    router = ModelRouter()
    alias = router.route_model("Explain DNS simply", intent="explain", mode="learn", level="eli5")
    assert isinstance(alias, str)
    assert alias


def test_route_aliases_returns_unique_chain() -> None:
    router = ModelRouter()
    aliases = router.route_aliases(
        "How to design pagination API?",
        intent="brainstorm",
        mode="technical",
        level="technical",
        is_pro=True,
    )
    assert aliases
    assert len(aliases) == len(set(aliases))


def test_score_model_returns_float_scores() -> None:
    router = ModelRouter()
    scores = router.score_model(
        "Explain cache invalidation",
        {"complexity": 0.4, "reasoning": 0.6, "explanation": 0.8, "latency_priority": 0.2},
        mode="learn",
    )
    assert scores
    assert all(isinstance(v, float) for v in scores.values())
