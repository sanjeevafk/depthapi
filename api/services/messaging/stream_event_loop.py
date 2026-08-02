"""Local stream event loop with no persistence side effects."""
from collections.abc import AsyncIterator

async def stream_events(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    async for chunk in chunks:
        yield chunk
