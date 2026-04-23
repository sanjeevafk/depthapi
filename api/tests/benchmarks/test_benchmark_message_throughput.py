"""Benchmark messages router throughput using fast deterministic stream mocks."""

from __future__ import annotations

import statistics
import time
from types import SimpleNamespace

import pytest

import main as main_app
import routers.messages as messages_module
import api.services.message_gate as message_gate


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
    monkeypatch.setattr(messages_module, "gatekeep_message_request", _allow)


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
        return None, []

    async def fast_stream(*_args: object, **_kwargs: object):
        yield "ok"

    _allow_gatekeeper(monkeypatch)
    main_app.app.dependency_overrides[messages_module.verify_token] = fake_verify_token
    monkeypatch.setattr(messages_module, "fetch_conversation_snapshot", fake_fetch_snapshot)
    monkeypatch.setattr(messages_module, "generate_stream_explanation", fast_stream)
    monkeypatch.setattr(messages_module, "get_supabase_admin", lambda: None)
    monkeypatch.setattr(messages_module, "get_settings", lambda: test_settings)

    payload_base = {
        "conversation_id": "bench-conversation",
        "content": "benchmark message",
        "mode": "learn",
        "prompt_mode": "eli5",
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
        main_app.app.dependency_overrides.pop(messages_module.verify_token, None)
