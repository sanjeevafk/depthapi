"""Mode-free response generation for retrieved contexts."""
import re
from collections.abc import AsyncIterator
from typing import Any

from api.config import get_settings

CITATION_PATTERN = re.compile(r"\[\d+\]")

_ABSTENTION_MARKERS = (
    "could not find sufficient",
    "no matching knowledge",
    "insufficient",
)


def _fallback_response(contexts: list[dict[str, Any]]) -> str:
    if not contexts:
        return "No matching knowledge was found."
    excerpts = [str(context.get("content", "")).strip() for context in contexts]
    return "\n\n".join(excerpt for excerpt in excerpts if excerpt) or "No matching knowledge was found."


def has_citation_markers(answer: str) -> bool:
    """True when the answer cites at least one numbered source like [1]."""
    return CITATION_PATTERN.search(answer or "") is not None


def looks_like_abstention(answer: str) -> bool:
    """True for honest no-match answers, which carry no citations by design."""
    lowered = (answer or "").lower()
    return any(marker in lowered for marker in _ABSTENTION_MARKERS)


def _numbered_sources(contexts: list[dict[str, Any]]) -> str:
    chunks = []
    for idx, item in enumerate(contexts, 1):
        chunks.append(f"[{idx}] {str(item.get('content', ''))[:6000]}")
    return "\n\n".join(chunks)


def _citation_system_prompt() -> str:
    return (
        "Answer using only the supplied knowledge, which is numbered [1], [2], ... . "
        "Cite every factual claim inline with its source number, e.g. [1]. "
        "If the knowledge is insufficient, say so plainly without citations."
    )


async def _complete_once(
    client: Any, model: str, temperature: float, query: str, source_text: str, nudge: str = ""
) -> str | None:
    user_content = f"Question: {query}\n\nKnowledge:\n{source_text}"
    if nudge:
        user_content += f"\n\n{nudge}"
    response = await client.chat.completions.create(
        model=model,
        temperature=temperature,
        messages=[
            {"role": "system", "content": _citation_system_prompt()},
            {"role": "user", "content": user_content},
        ],
    )
    answer = response.choices[0].message.content if response.choices else None
    return answer.strip() if answer else None


async def generate_response(
    query: str, contexts: list[dict[str, Any]], temperature: float = 0.7, enforce_citations: bool = True
) -> str:
    settings = get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key or not contexts:
        return _fallback_response(contexts)
    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, timeout=settings.llm_timeout_seconds)
        source_text = _numbered_sources(contexts)
        answer = await _complete_once(client, settings.llm_model, temperature, query, source_text)
        if not answer:
            return _fallback_response(contexts)
        if (
            enforce_citations
            and not has_citation_markers(answer)
            and not looks_like_abstention(answer)
        ):
            retried = await _complete_once(
                client,
                settings.llm_model,
                temperature,
                query,
                source_text,
                nudge="Your previous answer contained no citations like [1]. Answer again, citing every factual claim with its source number.",
            )
            if retried and (has_citation_markers(retried) or looks_like_abstention(retried)):
                return retried
        return answer
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
        source_text = _numbered_sources(contexts)
        stream = await client.chat.completions.create(
            model=settings.llm_model,
            temperature=temperature,
            stream=True,
            messages=[
                {"role": "system", "content": _citation_system_prompt()},
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
