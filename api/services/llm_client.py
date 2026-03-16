"""LiteLLM OpenAI-compatible client adapter."""

from __future__ import annotations

from typing import AsyncGenerator
import asyncio

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from config import get_settings
from services.llm_errors import LLMUnavailable


_client: AsyncOpenAI | None = None
_client_base_url: str | None = None
_client_api_key: str | None = None
_client_lock: asyncio.Lock | None = None


def _get_lock() -> asyncio.Lock:
    global _client_lock
    if _client_lock is None:
        _client_lock = asyncio.Lock()
    return _client_lock


def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        raise LLMUnavailable("LITELLM_BASE_URL is not configured.")

    normalized = base_url.rstrip("/")
    if not normalized.endswith("/v1"):
        normalized = f"{normalized}/v1"
    return normalized


def _resolve_api_key() -> str:
    settings = get_settings()
    api_key = settings.litellm_virtual_key or settings.litellm_master_key
    if not api_key:
        raise LLMUnavailable("LITELLM_VIRTUAL_KEY or LITELLM_MASTER_KEY is required.")
    return api_key


async def get_llm_client() -> AsyncOpenAI:
    """Return a singleton AsyncOpenAI client configured for LiteLLM."""
    global _client, _client_base_url, _client_api_key

    settings = get_settings()
    base_url = _normalize_base_url(settings.litellm_base_url)
    api_key = _resolve_api_key()

    async with _get_lock():
        if _client and _client_base_url == base_url and _client_api_key == api_key:
            return _client

        if _client:
            await _client.close()

        _client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=settings.litellm_timeout_seconds,
        )
        _client_base_url = base_url
        _client_api_key = api_key
        return _client


async def create_chat_completion(model: str, messages: list[ChatCompletionMessageParam], **kwargs):
    """Create a chat completion via LiteLLM."""
    client = await get_llm_client()
    return await client.chat.completions.create(model=model, messages=messages, **kwargs)


async def stream_chat_completion(
    model: str, messages: list[ChatCompletionMessageParam], **kwargs
) -> AsyncGenerator[str, None]:
    """Stream chat completion text deltas via LiteLLM."""
    kwargs.pop("stream", None)
    client = await get_llm_client()
    stream = await client.chat.completions.create(
        model=model,
        messages=messages,
        stream=True,
        **kwargs,
    )
    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            yield content


async def close_llm_client() -> None:
    """Close the shared LiteLLM client."""
    global _client, _client_base_url, _client_api_key
    async with _get_lock():
        if _client is None:
            return
        await _client.close()
        _client = None
        _client_base_url = None
        _client_api_key = None
