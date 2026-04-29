from __future__ import annotations

import pytest

from services.message_dispatcher import MessageDispatcher


async def _collect(response) -> str:
    parts: list[str] = []
    async for item in response.body_iterator:
        if isinstance(item, bytes):
            parts.append(item.decode("utf-8"))
        else:
            parts.append(str(item))
    return "".join(parts)


@pytest.mark.asyncio
async def test_dispatch_normal_message_emits_replay_sequence() -> None:
    dispatcher = MessageDispatcher()
    response = dispatcher.dispatch_normal_message(
        content="hello world",
        message_id="m-1",
        assistant_message_id="a-1",
        mode="learn",
        prompt_mode="simple",
    )

    payload = await _collect(response)
    assert response.media_type == "text/event-stream"
    assert "event: meta" in payload
    assert '"replay":true' in payload
    assert "event: delta" in payload
    assert "event: done" in payload


@pytest.mark.asyncio
async def test_dispatch_streaming_message_uses_factory() -> None:
    dispatcher = MessageDispatcher()

    async def _stream():
        yield "event: start\ndata: {}\n\n"
        yield "event: done\ndata: [DONE]\n\n"

    response = dispatcher.dispatch_streaming_message(_stream)
    payload = await _collect(response)

    assert response.media_type == "text/event-stream"
    assert "event: start" in payload
    assert "event: done" in payload


@pytest.mark.asyncio
async def test_dispatch_selects_streaming_branch() -> None:
    dispatcher = MessageDispatcher()

    async def _stream():
        yield "event: done\ndata: [DONE]\n\n"

    response = await dispatcher.dispatch(
        streaming=True,
        stream_factory=_stream,
        mode="learn",
        prompt_mode="simple",
    )
    payload = await _collect(response)

    assert "event: done" in payload


@pytest.mark.asyncio
async def test_dispatch_mode_specific_falls_back_to_normal_for_unknown_mode() -> None:
    dispatcher = MessageDispatcher()

    async def _unused_stream():
        yield "event: should_not_happen\ndata: {}\n\n"

    response = dispatcher.dispatch_mode_specific(
        mode="other",
        stream_factory=_unused_stream,
        normal_payload={
            "content": "fallback",
            "message_id": "m-2",
            "assistant_message_id": "a-2",
            "prompt_mode": "simple",
        },
    )
    payload = await _collect(response)

    assert "event: meta" in payload
    assert "fallback" in payload
