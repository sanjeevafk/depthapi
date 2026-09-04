"""Mode-free response generation for retrieved contexts."""
from collections.abc import AsyncIterator
from typing import Any

from api.config import get_settings


def _fallback_response(contexts: list[dict[str, Any]]) -> str:
    if not contexts:
        return "No matching knowledge was found."
    excerpts = [str(context.get("content", "")).strip() for context in contexts]
    return "\n\n".join(excerpt for excerpt in excerpts if excerpt) or "No matching knowledge was found."

async def generate_response(query: str, contexts: list[dict[str, Any]], temperature: float = 0.7) -> str:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key or not contexts:
        return _fallback_response(contexts)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, timeout=settings.llm_timeout_seconds)
        source_text = "\n\n".join(str(item.get("content", ""))[:6000] for item in contexts)
        response = await client.chat.completions.create(
            model=settings.llm_model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": "Answer using only the supplied knowledge. If it is insufficient, say so."},
                {"role": "user", "content": f"Question: {query}\n\nKnowledge:\n{source_text}"},
            ],
        )
        answer = response.choices[0].message.content if response.choices else None
        return answer.strip() if answer else _fallback_response(contexts)
    except Exception:
        return _fallback_response(contexts)

async def generate_stream_response(query: str, contexts: list[dict[str, Any]], temperature: float = 0.7) -> AsyncIterator[str]:
    """Yield answer tokens incrementally; falls back to chunked excerpts without an LLM key."""
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key or not contexts:
        fallback = _fallback_response(contexts)
        # Chunk fallback so SSE clients still see progressive events.
        chunk_size = 500
        for i in range(0, max(1, len(fallback)), chunk_size):
            yield fallback[i : i + chunk_size]
        return
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, timeout=settings.llm_timeout_seconds)
        source_text = "\n\n".join(str(item.get("content", ""))[:6000] for item in contexts)
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            temperature=temperature,
            stream=True,
            messages=[
                {"role": "system", "content": "Answer using only the supplied knowledge. If it is insufficient, say so."},
                {"role": "user", "content": f"Question: {query}\n\nKnowledge:\n{source_text}"},
            ],
        )
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content if chunk.choices else None
            except Exception:
                delta = None
            if delta:
                yield delta
    except Exception:
        fallback = _fallback_response(contexts)
        yield fallback
