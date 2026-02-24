"""Query endpoint for generating explanations."""

import asyncio
from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from fastapi_limiter.depends import RateLimiter
from utils import sanitize_topic, topic_cache_key, FREE_LEVELS, PREMIUM_LEVELS, PROMPT_MODE_ALIASES
from services.cache import cache_get, cache_set
from services.ensemble import ensemble_generate
from services.inference import generate_stream_explanation
from auth import verify_token_optional, get_supabase_admin, ensure_user_exists, check_is_pro
from logging_config import logger
import json


router = APIRouter(tags=["query"])


class QueryRequest(BaseModel):
    topic: str = Field(..., min_length=1, max_length=200)
    levels: list[str] = Field(default=FREE_LEVELS)
    premium: bool = False
    mode: str = "ensemble"  # "fast", "ensemble"
    bypass_cache: bool = False
    temperature: float = 0.7
    regenerate: bool = False


def _normalize_levels(levels: list[str]) -> list[str]:
    normalized = []
    for lvl in levels or []:
        normalized.append(PROMPT_MODE_ALIASES.get(lvl, lvl))
    return normalized


class QueryResponse(BaseModel):
    topic: str
    explanations: dict[str, str]
    cached: bool = False


@router.post("/query", response_model=QueryResponse)
async def query_topic(
    req: QueryRequest,
    auth_data: dict = Depends(verify_token_optional)
) -> QueryResponse:
    """Generate explanations for a topic."""
    
    # Gating: Enforce Premium for 'ensemble', downgrading to 'fast' if needed (UX decision)
    # Server-Side Verification: We trust the backend's knowledge of the user, not the client request.
    if req.premium:
        # User claims to be premium. Verify.
        is_verified_pro = False
        if auth_data:
            user = auth_data["user"]
            is_verified_pro = await check_is_pro(user.id)
        
        if not is_verified_pro:
            req.premium = False # Downgrade if verification fails
            
    allowed_modes = {"fast", "ensemble"}
    if req.mode not in allowed_modes:
        req.mode = "fast"

    if req.mode == "ensemble" and not req.premium:
        req.mode = "fast"

    try:
        topic = sanitize_topic(req.topic)
    except ValueError as e:
        raise HTTPException(400, str(e))

    allowed_levels = FREE_LEVELS + (PREMIUM_LEVELS if req.premium else [])
    levels = [l for l in _normalize_levels(req.levels) if l in allowed_levels]
    
    # If no valid levels remain (e.g. user requested only premium levels but is free), default to eli5
    if not levels:
        levels = ["eli5"]

    explanations: dict[str, str] = {}
    uncached: list[str] = []
    
    if not req.bypass_cache:
        for lvl in levels:
            key = topic_cache_key(topic, lvl)
            cached = await cache_get(key)
            if cached:
                explanations[lvl] = cached.get("text", "")
            else:
                uncached.append(lvl)
    else:
        uncached = levels

    # If all levels are cached, we still want to record history if authenticated
    if not uncached and not req.bypass_cache:
        if auth_data:
            logger.info("query_cached_saving_history", user_id=auth_data["user"].id, topic=topic)
            asyncio.create_task(save_to_history(auth_data["user"], topic, levels, req.mode))
        else:
            logger.info("query_cached_no_auth", topic=topic)
        return QueryResponse(topic=topic, explanations=explanations, cached=True)

    logger.info("query_start_generation", topic=topic, levels=uncached, has_auth=bool(auth_data))
    tasks = {lvl: ensemble_generate(topic, lvl, req.premium, req.mode) for lvl in uncached}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    for lvl, result in zip(tasks.keys(), results):
        if isinstance(result, str):
            explanations[lvl] = result
            key = topic_cache_key(topic, lvl)
            await cache_set(key, {"text": result})
        else:
            error_msg = str(result) if result else "Unknown error"
            explanations[lvl] = f"Error generating {lvl}: {error_msg}"
            logger.error("query_generation_failed", level=lvl, error=error_msg)


    if auth_data:
        logger.info("query_success_saving_history", user_id=auth_data["user"].id, topic=topic)
        asyncio.create_task(save_to_history(auth_data["user"], topic, levels, req.mode))
    else:
        logger.info("query_success_no_auth", topic=topic)

    return QueryResponse(topic=topic, explanations=explanations, cached=False)


@router.post("/query/stream")
async def query_topic_stream(
    req: QueryRequest,
    auth_data: dict = Depends(verify_token_optional)
):
    """Stream explanations for a topic."""
    
    # Gating: Enforce Premium for 'ensemble'
    if req.premium:
        is_verified_pro = False
        if auth_data:
            user = auth_data["user"]
            is_verified_pro = await check_is_pro(user.id)
        
        if not is_verified_pro:
            req.premium = False

    allowed_modes = {"fast", "ensemble"}
    if req.mode not in allowed_modes:
        req.mode = "fast"

    if req.mode == "ensemble" and not req.premium:
        req.mode = "fast"

    try:
        topic = sanitize_topic(req.topic)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # For streaming, we usually handle one level at a time
    allowed_levels = FREE_LEVELS + (PREMIUM_LEVELS if req.premium else [])
    _normalized = [l for l in _normalize_levels(req.levels) if l in allowed_levels]
    level = _normalized[0] if _normalized else "eli5"

    async def event_generator():
        full_content = ""
        buffer = ""
        last_flush_time = asyncio.get_event_loop().time()
        
        try:
            # Yield metadata first
            yield f"data: {json.dumps({'topic': topic, 'level': level})}\n\n"
            
            # Check cache first for instant delivery
            if not req.bypass_cache:
                cache_key = topic_cache_key(topic, level)
                cached = await cache_get(cache_key)
                if cached and cached.get("text"):
                    logger.info("query_stream_cache_hit", topic=topic, level=level)
                    content = cached["text"]
                    # Yield in small chunks to simulate streaming for UI consistency if needed, 
                    # or just one big chunk. Let's do a few chunks for smooth UI.
                    chunk_size = 500
                    for i in range(0, len(content), chunk_size):
                        chunk = content[i:i+chunk_size]
                        yield f"data: {json.dumps({'chunk': chunk})}\n\n"
                        await asyncio.sleep(0.01) # Tiny sleep for UI smoothness
                    
                    yield "data: [DONE]\n\n"
                    if auth_data:
                        asyncio.create_task(save_to_history(auth_data["user"], topic, [level], req.mode))
                    return

            # If not cached or bypass requested, stream from model with smart adaptive buffering
            token_count = 0
            chunk_count = 0
            avg_chunk_size = 0.0
            
            async for chunk in generate_stream_explanation(
                topic, 
                level, 
                mode=req.mode, 
                premium=req.premium,
                is_pro=req.premium,
                temperature=req.temperature,
                regenerate=req.regenerate
            ):
                full_content += chunk
                chunk_count += 1
                
                # Track average chunk size to detect slow vs fast LLM responses
                avg_chunk_size = (avg_chunk_size * (chunk_count - 1) + len(chunk)) / chunk_count
                
                # Check for truncation marker
                if "__TRUNCATED__" in chunk:
                    # Remove marker from content
                    chunk = chunk.replace("__TRUNCATED__", "")
                    if chunk:
                        buffer += chunk
                        if buffer:
                            yield f"data: {json.dumps({'chunk': buffer})}\n\n"
                            buffer = ""
                    # Send truncation warning
                    yield f"data: {json.dumps({'warning': '⚠️ Response may be incomplete due to length limits. Try regenerating or asking for a shorter explanation.'})}\n\n"
                    break
                
                buffer += chunk
                token_count += len(chunk.split())  # Rough token estimate
                current_time = asyncio.get_event_loop().time()
                
                # Dynamic timeout based on response speed
                # Fast responses (avg > 10 chars/chunk): 150ms timeout
                # Slow responses (avg <= 10 chars/chunk): 250ms timeout for smoother feel
                dynamic_timeout = 0.15 if avg_chunk_size > 10 else 0.25
                
                # Smart adaptive flush conditions:
                # 1. First 10 tokens: flush immediately (fast perceived start)
                # 2. Paragraph break (double newline): always flush for readability
                # 3. After that: 50 chars OR sentence boundary OR dynamic timeout
                should_flush = (
                    token_count < 10 or  # First few tokens always flush
                    buffer.endswith('\n\n') or  # Paragraph break - always flush
                    len(buffer) > 50 or 
                    buffer.endswith('. ') or 
                    buffer.endswith('! ') or 
                    buffer.endswith('? ') or 
                    buffer.endswith('.\n') or 
                    buffer.endswith('!\n') or 
                    buffer.endswith('?\n') or
                    buffer.endswith(': ') or
                    buffer.endswith(';\n') or
                    (current_time - last_flush_time) > dynamic_timeout
                )
                
                if should_flush and buffer:
                    yield f"data: {json.dumps({'chunk': buffer})}\n\n"
                    buffer = ""
                    last_flush_time = current_time
            
            # Flush any remaining content in buffer
            if buffer:
                yield f"data: {json.dumps({'chunk': buffer})}\n\n"
            
            # Final event
            yield "data: [DONE]\n\n"
            
            # Cache the result for future "revisits"
            if full_content.strip():
                cache_key = topic_cache_key(topic, level)
                await cache_set(cache_key, {"text": full_content})
            
            # Record in history if authenticated
            if auth_data:
                asyncio.create_task(save_to_history(auth_data["user"], topic, [level], req.mode))
                
        except Exception as e:
            logger.error("streaming_failed", error=str(e), topic=topic)
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


async def save_to_history(user, topic: str, levels: list[str], mode: str):
    """Background task to save query to history. Deduplicates by topic per user."""
    logger.info("save_to_history_task_start", user_id=user.id, topic=topic)
    try:
        await ensure_user_exists(user)
        supabase = get_supabase_admin()
        if not supabase:
            logger.error("save_to_history_task_no_supabase_admin")
            return

        # Check for existing entry for this user and topic
        existing = await asyncio.to_thread(
            supabase.table("history").select("id, levels").eq("user_id", user.id).eq("topic", topic).execute
        )
        
        if existing.data:
            # Update existing entry
            item_id = existing.data[0]["id"]
            existing_levels = set(existing.data[0]["levels"])
            new_levels = list(existing_levels.union(set(levels)))
            
            await asyncio.to_thread(
                supabase.table("history").update({
                    "levels": new_levels,
                    "mode": mode,
                    "created_at": "now()" # Move to top
                }).eq("id", item_id).execute
            )
            logger.info("save_to_history_task_updated", user_id=user.id, topic=topic)
        else:
            # Insert new entry
            response = await asyncio.to_thread(
                supabase.table("history").insert({
                    "user_id": user.id,
                    "topic": topic,
                    "levels": levels,
                    "mode": mode
                }).execute
            )
            logger.info("save_to_history_task_success", user_id=user.id, topic=topic, data=bool(response.data))

            
    except Exception as e:
        logger.error("save_to_history_task_error", error=str(e), user_id=user.id, topic=topic)

