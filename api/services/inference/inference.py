"""Mode-free response generation for retrieved contexts."""
from collections.abc import AsyncIterator
from typing import Any

def generate_response(query: str, contexts: list[dict[str, Any]], temperature: float = 0.7) -> str:
    del temperature
    if not contexts:
        return "No matching knowledge was found."
    excerpts = [str(context.get("content", "")).strip() for context in contexts]
    return "\n\n".join(excerpt for excerpt in excerpts if excerpt) or "No matching knowledge was found."

async def generate_stream_response(query: str, contexts: list[dict[str, Any]], temperature: float = 0.7) -> AsyncIterator[str]:
    yield generate_response(query, contexts, temperature)
