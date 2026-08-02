"""Helpers for draining local response streams."""
from collections.abc import AsyncIterable

async def drain_stream_chunks(chunks: AsyncIterable[str]) -> str:
    return "".join(chunk async for chunk in chunks)
