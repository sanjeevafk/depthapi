from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import Request

from services.message_streaming import build_message_replay_response, build_message_stream_response


async def _collect_stream(response) -> list[str]:
    events: list[str] = []
    async for part in response.body_iterator:
        if isinstance(part, bytes):
            events.append(part.decode("utf-8"))
        else:
            events.append(str(part))
    return events


@pytest.mark.asyncio
async def test_replay_response_emits_meta_delta_done() -> None:
    response = build_message_replay_response(
        content="hello world",
        message_id="m1",
        assistant_message_id="a1",
        mode="learn",
        prompt_mode="eli10",
    )
    events = await _collect_stream(response)
    payload = "".join(events)
    assert "event: meta" in payload
    assert "event: delta" in payload
    assert "event: done" in payload


@pytest.mark.asyncio
async def test_stream_response_uses_cached_response_path() -> None:
    async def _noop_generate_stream(*_args, **_kwargs):
        if False:
            yield ""

    async def _noop_generate(*_args, **_kwargs):
        return "unused"

    cached_writes: list[tuple[str, dict, int]] = []

    async def _cache_set(key: str, payload: dict, ttl: int) -> None:
        cached_writes.append((key, payload, ttl))

    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({"type": "http", "method": "POST", "path": "/query", "headers": []}, _receive)
    req = SimpleNamespace(regenerate=False)

    response = build_message_stream_response(
        request=request,
        req=req,
        request_id="r1",
        request_received=0.0,
        user_id="u1",
        user_id_hash="u1hash",
        content="topic",
        content_hash="h1",
        selected_mode="learn",
        prompt_mode="eli10",
        assistant_message_id="a1",
        client_message_id="m1",
        conversation_id="c1",
        request_temperature=0.3,
        cached_response="cached answer",
        cache_key="cache-key",
        cache_ttl_seconds=60,
        stream_max_seconds=30,
        stream_start_timeout_seconds=1.0,
        heartbeat_seconds=5.0,
        fallback_timeout_seconds=2.0,
        idempotency_key="idem1",
        idempotency_ttl_seconds=120,
        idempotency_started_at=1,
        is_pro=False,
        generate_stream_explanation=_noop_generate_stream,
        generate_explanation=_noop_generate,
        cache_set=_cache_set,
        log_context={},
    )

    assert response.media_type == "text/event-stream"
    assert cached_writes == []
