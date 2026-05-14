"""Benchmark messages router throughput using fast deterministic stream mocks."""

from __future__ import annotations

import statistics
import time
from types import SimpleNamespace

import pytest

import main as main_app
import api.services.messaging.message_gate as message_gate
import api.services.inference.inference as inference_module
import api.auth as auth_module
import api.services.messaging.streaming_message_pipeline as pipeline_module


def _allow_gatekeeper(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _allow(**_kwargs: object) -> message_gate.GatekeeperResult:
        return message_gate.GatekeeperResult(
            allowed=True,
            retry_after=0,
            idempotency_status=None,
            idempotency_response=None,
            degraded=True,
            redis_eval_ms=0.0,
        )

    monkeypatch.setattr(message_gate, "gatekeep_message_request", _allow)


@pytest.mark.asyncio
async def test_benchmark_message_throughput(
    app_client,
    monkeypatch: pytest.MonkeyPatch,
    test_settings,
) -> None:
    user = SimpleNamespace(id="bench-user", email="bench@example.com", user_metadata={})

    async def fake_verify_token() -> dict[str, object]:
        return {"user": user, "is_pro": True, "exp": time.time() + 600}

    async def fake_fetch_snapshot(**_kwargs: object) -> tuple[str | None, list[str]]:
        # Return a valid conversation meta so the pipeline doesn't 404
        return "{}", []

    async def fast_stream(*_args: object, **_kwargs: object):
        yield "ok"

    _allow_gatekeeper(monkeypatch)
    # The messages endpoint now uses verify_api_key from api.services.security.api_key_auth
    from api.services.security.api_key_auth import verify_api_key, ApiKeyRecord
    bench_api_key = ApiKeyRecord(
        id="bench-key-uuid",
        prefix="sk-bench",
        project_name="Bench Project",
        owner_email="bench@example.com",
        plan="pro",
        monthly_token_budget=10000000,
        requests_per_minute=100,
    )
    main_app.app.dependency_overrides[verify_api_key] = lambda: bench_api_key
    monkeypatch.setattr(message_gate, "fetch_conversation_snapshot", fake_fetch_snapshot)
    monkeypatch.setattr(inference_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: None)
    monkeypatch.setattr(auth_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(pipeline_module, "get_supabase_admin", lambda: None)

    payload_base = {
        "conversation_id": "bench-conversation",
        "content": "benchmark message",
        "mode": "learn",
        "prompt_mode": "simple",
    }

    samples: list[float] = []
    try:
        for idx in range(20):
            payload = {
                **payload_base,
                "client_generated_id": f"00000000-0000-4000-8000-{idx:012d}",
                "assistant_client_id": f"10000000-0000-4000-8000-{idx:012d}",
            }
            started = time.perf_counter()
            resp = await app_client.post("/api/messages", json=payload)
            samples.append((time.perf_counter() - started) * 1000.0)
            assert resp.status_code == 200

        total_seconds = sum(samples) / 1000.0
        throughput_rps = len(samples) / total_seconds if total_seconds > 0 else 0.0
        p95 = sorted(samples)[int(len(samples) * 0.95) - 1]

        print(
            "[benchmark] message_throughput",
            {
                "requests": len(samples),
                "throughput_rps": round(throughput_rps, 2),
                "mean_latency_ms": round(statistics.mean(samples), 2),
                "p95_latency_ms": round(p95, 2),
            },
        )

        assert throughput_rps > 0
    finally:
        main_app.app.dependency_overrides.pop(verify_api_key, None)
