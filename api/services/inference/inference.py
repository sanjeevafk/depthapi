"""Mode-free response generation for retrieved contexts."""
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from api.config import get_settings

logger = logging.getLogger(__name__)

CITATION_PATTERN = re.compile(r"\[\d+\]")

_ABSTENTION_MARKERS = (
    "could not find sufficient",
    "no matching knowledge",
    "insufficient",
)


@dataclass(frozen=True)
class _LLMTarget:
    client: Any
    model: str
    max_chunks: int | None


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


def _format_sources(contexts: list[dict[str, Any]], max_chunks: int | None) -> str:
    effective = contexts[:max_chunks] if max_chunks is not None and max_chunks > 0 else contexts
    return _numbered_sources(effective)


def _citation_system_prompt() -> str:
    return (
        "Answer using only the supplied knowledge, which is numbered [1], [2], ... . "
        "Cite every factual claim inline with its source number, e.g. [1]. "
        "If the knowledge is insufficient, say so plainly without citations."
    )


def _get_llm_targets(settings: Any) -> list[_LLMTarget]:
    """Resolve ordered LLM provider targets: primary OpenAI followed by local LLM fallback."""
    targets: list[_LLMTarget] = []
    api_key = settings.openai_api_key.get_secret_value() if hasattr(settings, "openai_api_key") else ""
    if api_key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key, timeout=settings.llm_timeout_seconds)
            targets.append(_LLMTarget(client=client, model=settings.llm_model, max_chunks=None))
        except Exception as exc:
            logger.warning("Failed to initialize primary OpenAI client: %s", exc)

    local_url = getattr(settings, "local_llm_base_url", "")
    if local_url:
        try:
            from openai import AsyncOpenAI

            local_key = (
                settings.local_llm_api_key.get_secret_value()
                if hasattr(settings, "local_llm_api_key")
                else "local"
            ) or "local"
            local_timeout = getattr(settings, "local_llm_timeout_seconds", 120)
            client = AsyncOpenAI(
                base_url=local_url,
                api_key=local_key,
                timeout=local_timeout,
            )
            local_model = getattr(settings, "local_llm_model", "") or settings.llm_model
            max_chunks = getattr(settings, "local_llm_max_context_chunks", 3)
            targets.append(_LLMTarget(client=client, model=local_model, max_chunks=max_chunks))
        except Exception as exc:
            logger.warning("Failed to initialize local LLM client: %s", exc)

    return targets


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
    if not contexts:
        return _fallback_response(contexts)

    settings = get_settings()
    targets = _get_llm_targets(settings)
    if not targets:
        return _fallback_response(contexts)

    for target in targets:
        try:
            source_text = _format_sources(contexts, target.max_chunks)
            answer = await _complete_once(target.client, target.model, temperature, query, source_text)
            if not answer:
                continue
            if (
                enforce_citations
                and not has_citation_markers(answer)
                and not looks_like_abstention(answer)
            ):
                retried = await _complete_once(
                    target.client,
                    target.model,
                    temperature,
                    query,
                    source_text,
                    nudge="Your previous answer contained no citations like [1]. Answer again, citing every factual claim with its source number.",
                )
                if retried and (has_citation_markers(retried) or looks_like_abstention(retried)):
                    return retried
            return answer
        except Exception as exc:
            logger.warning("LLM generation attempt failed on model %s: %s", target.model, exc)
            continue

    return _fallback_response(contexts)


async def generate_stream_response(
    query: str, contexts: list[dict[str, Any]], temperature: float = 0.7
) -> AsyncIterator[str]:
    """Yield answer tokens incrementally; cascades from primary to local LLM, falling back to chunked excerpts."""
    if not contexts:
        fallback = _fallback_response(contexts)
        chunk_size = 500
        for i in range(0, max(1, len(fallback)), chunk_size):
            yield fallback[i : i + chunk_size]
        return

    settings = get_settings()
    targets = _get_llm_targets(settings)

    for target in targets:
        streamed_any = False
        try:
            source_text = _format_sources(contexts, target.max_chunks)
            stream = await target.client.chat.completions.create(
                model=target.model,
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
                    streamed_any = True
                    yield delta
            if streamed_any:
                return
        except Exception as exc:
            logger.warning("LLM stream generation attempt failed on model %s: %s", target.model, exc)
            if streamed_any:
                return
            continue

    # Fallback to chunked context excerpts for progressive SSE
    fallback = _fallback_response(contexts)
    chunk_size = 500
    for i in range(0, max(1, len(fallback)), chunk_size):
        yield fallback[i : i + chunk_size]
