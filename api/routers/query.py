"""Query endpoint for judged ensemble explanations."""

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import check_is_pro, ensure_user_exists, get_supabase_admin, verify_token_optional
from logging_config import logger
from services.cache import cache_get, cache_set
from services.ensemble import ensemble_generate, ensemble_stream_generate
from utils import (
    DEFAULT_CHAT_MODE,
    FREE_LEVELS,
    SOCRATIC_MODE,
    TECHNICAL_MODE,
    PROMPT_MODE_ALIASES,
    SUPPORTED_CHAT_MODES,
    normalize_mode,
    sanitize_topic,
    topic_cache_key,
)

router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    levels: list[str] = Field(default=FREE_LEVELS)
    premium: bool = False
    mode: str = DEFAULT_CHAT_MODE
    bypass_cache: bool = False
    temperature: float = 0.7
    regenerate: bool = False


class QueryResponse(BaseModel):
    topic: str
    explanations: dict[str, str]
    cached: bool = False


def _normalize_levels(levels: list[str]) -> list[str]:
    normalized = []
    for level in levels or []:
        normalized.append(PROMPT_MODE_ALIASES.get(level, level))
    return normalized


def _cache_key(topic: str, level: str, mode: str) -> str:
    return topic_cache_key(topic, level, mode=normalize_mode(mode))


@router.post("/query", response_model=QueryResponse)
async def query_topic(req: QueryRequest, auth_data: dict = Depends(verify_token_optional)) -> QueryResponse:
    try:
        topic = sanitize_topic(req.topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    mode = normalize_mode(req.mode)
    if mode not in SUPPORTED_CHAT_MODES:
        mode = DEFAULT_CHAT_MODE

    is_verified_pro = False
    if req.premium and auth_data:
        is_verified_pro = await check_is_pro(auth_data["user"].id)
    req.premium = bool(req.premium and is_verified_pro)
    if mode == TECHNICAL_MODE and not is_verified_pro:
        mode = DEFAULT_CHAT_MODE

    allowed_levels = FREE_LEVELS
    levels = [level for level in _normalize_levels(req.levels) if level in allowed_levels]
    if not levels:
        levels = ["eli15"]

    explanations: dict[str, str] = {}
    missing_levels: list[str] = []

    if not req.bypass_cache:
        for level in levels:
            cached = await cache_get(_cache_key(topic, level, mode))
            if cached:
                explanations[level] = cached.get("text", "")
            else:
                missing_levels.append(level)
    else:
        missing_levels = levels

    if not missing_levels and not req.bypass_cache:
        if auth_data:
            asyncio.create_task(save_to_history(auth_data["user"], topic, levels, mode))
        return QueryResponse(topic=topic, explanations=explanations, cached=True)

    tasks = {
        level: ensemble_generate(
            topic,
            level,
            use_premium=req.premium,
            mode=mode,
            temperature=req.temperature,
            regenerate=req.regenerate,
        )
        for level in missing_levels
    }
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for level, result in zip(tasks.keys(), results):
        if isinstance(result, str):
            explanations[level] = result
            await cache_set(_cache_key(topic, level, mode), {"text": result})
        else:
            explanations[level] = f"Error generating {level}: {result}"
            logger.error("query_generation_failed", level=level, error=str(result), mode=mode)

    if auth_data:
        asyncio.create_task(save_to_history(auth_data["user"], topic, levels, mode))

    return QueryResponse(topic=topic, explanations=explanations, cached=False)


@router.post("/query/stream")
async def query_topic_stream(req: QueryRequest, auth_data: dict = Depends(verify_token_optional)):
    """Stream the final judged response in chunks."""
    try:
        topic = sanitize_topic(req.topic)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    mode = normalize_mode(req.mode)
    if mode not in SUPPORTED_CHAT_MODES:
        mode = DEFAULT_CHAT_MODE

    is_verified_pro = False
    if req.premium and auth_data:
        is_verified_pro = await check_is_pro(auth_data["user"].id)
    req.premium = bool(req.premium and is_verified_pro)
    if mode == TECHNICAL_MODE and not is_verified_pro:
        mode = DEFAULT_CHAT_MODE

    allowed_levels = FREE_LEVELS
    normalized_levels = [level for level in _normalize_levels(req.levels) if level in allowed_levels]
    level = normalized_levels[0] if normalized_levels else "eli15"

    async def event_generator():
        full_content = ""
        try:
            yield f"data: {json.dumps({'topic': topic, 'level': level, 'mode': mode})}\n\n"

            if not req.bypass_cache:
                cached = await cache_get(_cache_key(topic, level, mode))
                if cached and cached.get("text"):
                    content = cached["text"]
                    for index in range(0, len(content), 400):
                        yield f"data: {json.dumps({'chunk': content[index:index + 400]})}\n\n"
                    yield "data: [DONE]\n\n"
                    if auth_data:
                        asyncio.create_task(save_to_history(auth_data["user"], topic, [level], mode))
                    return

            async for chunk in ensemble_stream_generate(
                topic,
                level,
                mode=mode,
                use_premium=req.premium,
                temperature=req.temperature,
                regenerate=req.regenerate,
            ):
                full_content += chunk
                yield f"data: {json.dumps({'chunk': chunk})}\n\n"

            yield "data: [DONE]\n\n"

            if full_content.strip():
                await cache_set(_cache_key(topic, level, mode), {"text": full_content})
            if auth_data:
                asyncio.create_task(save_to_history(auth_data["user"], topic, [level], mode))
        except Exception as exc:
            logger.error("streaming_failed", error=str(exc), topic=topic, mode=mode)
            yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def save_to_history(user, topic: str, levels: list[str], mode: str):
    """Background task to save query to history."""
    try:
        await ensure_user_exists(user)
        supabase = get_supabase_admin()
        if not supabase:
            logger.error("save_to_history_task_no_supabase_admin")
            return

        existing = await asyncio.to_thread(
            supabase.table("history").select("id, levels").eq("user_id", user.id).eq("topic", topic).execute
        )
        normalized_mode = normalize_mode(mode)

        if existing.data:
            item_id = existing.data[0]["id"]
            existing_levels = set(existing.data[0]["levels"])
            new_levels = list(existing_levels.union(set(levels)))
            await asyncio.to_thread(
                supabase.table("history")
                .update({"levels": new_levels, "mode": normalized_mode, "created_at": "now()"})
                .eq("id", item_id)
                .execute
            )
        else:
            await asyncio.to_thread(
                supabase.table("history")
                .insert({"user_id": user.id, "topic": topic, "levels": levels, "mode": normalized_mode})
                .execute
            )
    except Exception as exc:
        logger.error("save_to_history_task_error", error=str(exc), user_id=user.id, topic=topic)
