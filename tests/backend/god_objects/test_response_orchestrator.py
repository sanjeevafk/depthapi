from __future__ import annotations

import asyncio

import pytest

from services.response_orchestrator import ResponseOrchestrator


def test_format_sse_event_supports_json_payload() -> None:
    orchestrator = ResponseOrchestrator()

    event = orchestrator.format_sse_event("meta", {"message_id": "m1", "mode": "learn"})

    assert "event: meta" in event
    assert '"message_id":"m1"' in event
    assert event.endswith("\n\n")


def test_format_sse_event_supports_text_payload() -> None:
    orchestrator = ResponseOrchestrator()

    event = orchestrator.format_sse_event("done", "[DONE]")

    assert "event: done" in event
    assert "data: [DONE]" in event
    assert event.endswith("\n\n")


@pytest.mark.asyncio
async def test_build_sse_stream_handles_sync_iterable() -> None:
    orchestrator = ResponseOrchestrator()
    parts: list[str] = []
    events = [
        ("meta", {"message_id": "m1"}),
        ("delta", {"delta": "hello"}),
        ("done", "[DONE]"),
    ]

    async for chunk in orchestrator.build_sse_stream(events, "conv-1"):
        parts.append(chunk)

    payload = "".join(parts)
    assert "event: meta" in payload
    assert "event: delta" in payload
    assert "event: done" in payload


@pytest.mark.asyncio
async def test_build_sse_stream_handles_async_iterable() -> None:
    orchestrator = ResponseOrchestrator()

    async def _events():
        yield ("meta", {"message_id": "m2"})
        await asyncio.sleep(0)
        yield ("done", "[DONE]")

    parts: list[str] = []
    async for chunk in orchestrator.build_sse_stream(_events(), "conv-2"):
        parts.append(chunk)

    payload = "".join(parts)
    assert "event: meta" in payload
    assert "event: done" in payload


@pytest.mark.asyncio
async def test_persist_message_stream_updates_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    orchestrator = ResponseOrchestrator()
    captured: list[tuple[str, str]] = []

    def _fake_update(conversation_id: str, content: str) -> None:
        captured.append((conversation_id, content))

    async def _fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(
        "services.response_orchestrator.ChatRepository.update_assistant_message",
        _fake_update,
    )
    monkeypatch.setattr("services.response_orchestrator.asyncio.to_thread", _fake_to_thread)

    await orchestrator.persist_message_stream("assistant output", "msg-1")

    assert captured == [("msg-1", "assistant output")]
