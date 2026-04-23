from __future__ import annotations

from services.response_builder import ResponseBuilder


def test_build_response_formats_learning_mode() -> None:
    builder = ResponseBuilder()
    response = builder.build_response("part1\n\n\npart2", mode="learn", query="q")
    assert response == "part1\n\npart2"


def test_build_response_trims_technical_mode() -> None:
    builder = ResponseBuilder()
    response = builder.build_response("  technical answer  ", mode="technical", query="q")
    assert response == "technical answer"


def test_apply_socratic_fallback_returns_non_empty_question() -> None:
    builder = ResponseBuilder()
    response = builder.apply_socratic_fallback("What is DNS?", "")
    assert isinstance(response, str)
    assert response.strip()
