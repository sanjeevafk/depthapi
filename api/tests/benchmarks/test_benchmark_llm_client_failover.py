"""Benchmark provider failover path in llm_client."""

from __future__ import annotations

import importlib
import statistics
import time
from types import SimpleNamespace

import pytest

import api.services.llm_client as llm_client_module


class DummyProviderState:
    async def should_attempt(self, _provider):
        return True

    async def mark_success(self, _provider):
        return None

    async def mark_failure(self, _provider):
        return None


@pytest.mark.asyncio
async def test_benchmark_llm_failover(monkeypatch: pytest.MonkeyPatch) -> None:
    importlib.reload(llm_client_module)

    class FakeCompletions:
        def __init__(self, provider: str):
            self.provider = provider

        async def create(self, **kwargs: object):
            if self.provider == "groq":
                raise RuntimeError("primary unavailable")
            return SimpleNamespace(
                model=kwargs.get("model", "gemini-2.5-flash"),
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            )

    async def fake_get_provider_client(provider: str):
        return SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions(provider)))

    monkeypatch.setattr(llm_client_module, "_provider_state_manager", DummyProviderState())
    monkeypatch.setattr(llm_client_module, "_get_provider_client", fake_get_provider_client)
    monkeypatch.setattr(llm_client_module, "_is_retryable_error", lambda exc: isinstance(exc, RuntimeError))

    async def fake_provider_within_runtime_limits(*_args, **_kwargs):
        return True

    async def fake_increment_provider_usage(*_args, **_kwargs):
        return None

    monkeypatch.setattr(llm_client_module, "_provider_within_runtime_limits", fake_provider_within_runtime_limits)
    monkeypatch.setattr(llm_client_module, "_increment_provider_usage", fake_increment_provider_usage)

    samples: list[float] = []
    for _ in range(25):
        started = time.perf_counter()
        response = await llm_client_module.create_chat_completion(
            model="default-fast",
            messages=[{"role": "user", "content": "hello"}],
        )
        samples.append((time.perf_counter() - started) * 1000.0)
        assert response.choices[0].message.content == "ok"

    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    print(
        "[benchmark] llm_failover_latency_ms",
        {
            "runs": len(samples),
            "mean": round(statistics.mean(samples), 2),
            "p50": round(statistics.median(samples), 2),
            "p95": round(p95, 2),
        },
    )

    assert p95 < 1000
