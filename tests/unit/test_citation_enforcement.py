"""Unit tests for citation enforcement in response generation."""
from __future__ import annotations

import sys
import types

import pytest
from pydantic import SecretStr

from api.services.inference import inference as inference_module


class _StubSettings:
    openai_api_key = SecretStr("test-key")
    llm_model = "test-model"
    llm_timeout_seconds = 60


def _install_openai_stub(monkeypatch, answers: list[str | None]):
    calls: list[dict] = []

    class _Message:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.message = _Message(content)

    class _Response:
        def __init__(self, content):
            self.choices = [_Choice(content)] if content is not None else []

    class _Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return _Response(answers[min(len(calls) - 1, len(answers) - 1)])

    class _Chat:
        completions = _Completions()

    class AsyncOpenAI:
        def __init__(self, *args, **kwargs):
            self.chat = _Chat()

    stub = types.ModuleType("openai")
    stub.AsyncOpenAI = AsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", stub)
    monkeypatch.setattr(inference_module, "get_settings", lambda: _StubSettings())
    return calls


def test_has_citation_markers():
    assert inference_module.has_citation_markers("Paris is the capital [1].") is True
    assert inference_module.has_citation_markers("No markers here.") is False
    assert inference_module.has_citation_markers("") is False


def test_looks_like_abstention():
    assert inference_module.looks_like_abstention("I could not find sufficient documentation.") is True
    assert inference_module.looks_like_abstention("No matching knowledge was found.") is True
    assert inference_module.looks_like_abstention("Paris is the capital [1].") is False


@pytest.mark.asyncio
async def test_generate_response_retries_missing_citations(monkeypatch):
    calls = _install_openai_stub(monkeypatch, ["Paris is the capital.", "Paris is the capital [1]."])
    contexts = [{"content": "Paris is the capital of France."}]

    answer = await inference_module.generate_response("Capital?", contexts)

    assert answer == "Paris is the capital [1]."
    assert len(calls) == 2
    assert "[1] Paris" in calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_response_no_retry_when_cited(monkeypatch):
    calls = _install_openai_stub(monkeypatch, ["Paris is the capital [1]."])

    answer = await inference_module.generate_response("Capital?", [{"content": "Paris."}])

    assert answer == "Paris is the capital [1]."
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_generate_response_no_retry_on_abstention(monkeypatch):
    calls = _install_openai_stub(monkeypatch, ["I could not find sufficient documentation."])

    answer = await inference_module.generate_response("Capital?", [{"content": "Paris."}])

    assert "could not find sufficient" in answer
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_generate_response_keeps_first_answer_if_retry_still_uncited(monkeypatch):
    calls = _install_openai_stub(monkeypatch, ["First attempt.", "Second attempt."])

    answer = await inference_module.generate_response("Capital?", [{"content": "Paris."}])

    assert answer == "First attempt."
    assert len(calls) == 2
