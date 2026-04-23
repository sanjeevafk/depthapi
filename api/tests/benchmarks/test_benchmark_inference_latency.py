"""Benchmark inference response latency with deterministic mocks."""

from __future__ import annotations

import asyncio
import statistics
import time

import pytest

import api.services.inference as inference_module


@pytest.mark.asyncio
async def test_benchmark_inference_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_search_context(_topic: str, *, mode: str) -> str:
        return f"context for {mode}"

    async def fake_call_model(_model: str, _prompt: str, **_kwargs: object) -> str:
        return "This is a stable benchmark response used for latency baselining."

    monkeypatch.setattr(inference_module.search_service, "get_search_context", fake_search_context)
    monkeypatch.setattr(inference_module, "call_model", fake_call_model)

    samples: list[float] = []
    for _ in range(30):
        started = time.perf_counter()
        _ = await inference_module.generate_explanation("dns caching", "eli10", mode="learn")
        samples.append((time.perf_counter() - started) * 1000.0)

    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    mean = statistics.mean(samples)

    print(
        "[benchmark] inference_latency_ms",
        {
            "runs": len(samples),
            "mean": round(mean, 2),
            "p50": round(p50, 2),
            "p95": round(p95, 2),
        },
    )

    # Guard against accidental pathological regressions in mock path.
    assert p95 < 1000
    await asyncio.sleep(0)
