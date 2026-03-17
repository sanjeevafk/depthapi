import importlib
import hashlib
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

import services.inference as inference_module
import services.llm_client as llm_client_module
from logging_config import anonymize_text, anonymize_user_id, redact_sensitive_processor
import logging_config as logging_config_module


@pytest.mark.asyncio
async def test_request_id_echoed_in_response_header(app_client):
    request_id = str(uuid.uuid4())
    response = await app_client.get("/api/health", headers={"x-request-id": request_id})

    assert response.status_code == 200
    assert response.headers.get("X-Request-ID") == request_id


@pytest.mark.asyncio
async def test_inference_logs_structured_observability_fields(monkeypatch):
    captured: dict[str, object] = {}

    class DummyChoice:
        def __init__(self, content: str):
            self.message = SimpleNamespace(content=content)

    class DummyUsage:
        def model_dump(self):
            return {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            }

    class DummyResponse:
        def __init__(self):
            self.choices = [DummyChoice("hello")]
            self.usage = DummyUsage()
            self.model = "openai/gpt-4o-mini"
            self.response_cost = 0.0012

    async def fake_create_chat_completion(*_args, **_kwargs):
        return DummyResponse()

    def fake_log_sampled_success(event: str, **fields):
        captured["event"] = event
        captured.update(fields)

    monkeypatch.setattr(inference_module, "create_chat_completion", fake_create_chat_completion)
    monkeypatch.setattr(inference_module, "log_sampled_success", fake_log_sampled_success)

    telemetry_sink: dict[str, object] = {}
    result = await inference_module.call_model(
        "default-fast",
        "explain DNS",
        request_id="req-1",
        user_id="user-1",
        regenerate=True,
        telemetry_sink=telemetry_sink,
    )

    assert result == "hello"
    assert captured["event"] == "llm_completion_observed"
    assert captured["request_id"] == "req-1"
    assert captured["user_id_hash"] == anonymize_user_id("user-1")
    assert captured["model_alias"] == "default-fast"
    assert isinstance(captured["latency_ms"], float)
    assert captured["token_usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert captured["estimated_cost_usd"] == 0.0012
    assert captured["retry"] is True
    assert "prompt" not in captured
    assert telemetry_sink["token_usage"] == captured["token_usage"]


@pytest.mark.asyncio
async def test_litellm_client_receives_request_id_and_stream_telemetry(monkeypatch):
    class Chunk:
        def __init__(self, content: str | None):
            self.model = "openai/gpt-4o-mini"
            self.usage = {
                "prompt_tokens": 5,
                "completion_tokens": 3,
                "total_tokens": 8,
            }
            self._hidden_params = {"response_cost": 0.0004}
            self.choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]

    class FakeCompletions:
        def __init__(self):
            self.calls: list[dict[str, Any]] = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs.get("stream"):
                async def generator():
                    yield Chunk("hello")
                    yield Chunk(None)

                return generator()
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    # Reload to restore real llm_client helpers because conftest autouse patches these.
    importlib.reload(llm_client_module)

    fake_completions = FakeCompletions()
    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=fake_completions))

    async def fake_get_llm_client():
        return fake_client

    monkeypatch.setattr(llm_client_module, "get_llm_client", fake_get_llm_client)

    result = await llm_client_module.create_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hi"}],
        request_id="req-create",
    )

    assert result.choices[0].message.content == "ok"
    assert fake_completions.calls[0]["extra_headers"]["x-request-id"] == "req-create"

    telemetry_sink: dict[str, object] = {}
    chunks: list[str] = []
    async for chunk in llm_client_module.stream_chat_completion(
        model="default-fast",
        messages=[{"role": "user", "content": "hi"}],
        request_id="req-stream",
        telemetry_sink=telemetry_sink,
    ):
        chunks.append(chunk)

    assert chunks == ["hello"]
    assert fake_completions.calls[1]["extra_headers"]["x-request-id"] == "req-stream"
    assert fake_completions.calls[1]["stream_options"]["include_usage"] is True
    assert telemetry_sink["token_usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
    }
    assert telemetry_sink["estimated_cost_usd"] == 0.0004
    assert isinstance(telemetry_sink["stream_duration_ms"], float)


def test_redaction_removes_sensitive_values_but_keeps_usage_metrics():
    event = {
        "authorization": "Bearer secret",
        "prompt": "raw prompt text",
        "token_usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
        "nested": {"headers": {"x-api-key": "secret"}, "safe": True},
    }

    redacted = redact_sensitive_processor(None, "event", event)

    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["prompt"] == "[REDACTED]"
    assert redacted["nested"]["headers"] == "[REDACTED]"
    assert redacted["token_usage"] == {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}


def test_anonymize_text_uses_salt(monkeypatch):
    monkeypatch.setenv("LOG_USER_HASH_SALT", "test-salt")
    logging_config_module._user_hash_salt_cache = None

    expected = hashlib.sha256("test-salt:hello".encode("utf-8")).hexdigest()[:16]
    assert anonymize_text("hello") == expected
    assert anonymize_text("") is None
