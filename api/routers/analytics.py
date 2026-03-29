from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

import auth as auth_module
from services.analytics import resolve_time_range
from services.sentry_client import fetch_sentry_issues

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _ensure_supabase():
    supabase = auth_module.get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=503, detail="Database connection error")
    return supabase


@router.get("/usage")
async def get_usage(
    start: str | None = None,
    end: str | None = None,
    model: str | None = None,
    mode: str | None = None,
    user_id: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    _auth: dict = Depends(auth_module.require_admin),
):
    supabase = _ensure_supabase()
    start_ts, end_ts = resolve_time_range(start, end)
    offset = (page - 1) * page_size
    end_index = offset + page_size - 1

    def _query():
        query = supabase.table("llm_requests").select(
            "id, request_id, user_id, conversation_id, model_alias, model_name, provider, mode, status, "
            "tokens_prompt, tokens_completion, tokens_total, estimated_cost_usd, latency_ms, model_inference_ms, "
            "stream_duration_ms, error_type, error_message, created_at"
        )
        query = query.gte("created_at", start_ts.isoformat()).lte("created_at", end_ts.isoformat())
        if model:
            query = query.eq("model_alias", model)
        if mode:
            query = query.eq("mode", mode)
        if user_id:
            query = query.eq("user_id", user_id)
        if status:
            query = query.eq("status", status)
        query = query.order("created_at", desc=True).range(offset, end_index)
        return query.execute()

    response = await asyncio.to_thread(_query)
    data = getattr(response, "data", None)
    items = data if isinstance(data, list) else []
    total = getattr(response, "count", None)
    if total is None:
        total = len(items)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/cost")
async def get_cost(
    start: str | None = None,
    end: str | None = None,
    bucket: str = Query("day", pattern="^(hour|day)$"),
    _auth: dict = Depends(auth_module.require_admin),
):
    supabase = _ensure_supabase()
    start_ts, end_ts = resolve_time_range(start, end, default_days=30)

    def _query():
        return supabase.rpc(
            "llm_cost_agg",
            {"start_ts": start_ts.isoformat(), "end_ts": end_ts.isoformat(), "bucket": bucket},
        ).execute()

    response = await asyncio.to_thread(_query)
    data = getattr(response, "data", None)
    return {"items": data if isinstance(data, list) else []}


@router.get("/latency")
async def get_latency(
    start: str | None = None,
    end: str | None = None,
    bucket: str = Query("day", pattern="^(hour|day)$"),
    _auth: dict = Depends(auth_module.require_admin),
):
    supabase = _ensure_supabase()
    start_ts, end_ts = resolve_time_range(start, end, default_days=30)

    def _query():
        return supabase.rpc(
            "llm_latency_agg",
            {"start_ts": start_ts.isoformat(), "end_ts": end_ts.isoformat(), "bucket": bucket},
        ).execute()

    response = await asyncio.to_thread(_query)
    data = getattr(response, "data", None)
    return {"items": data if isinstance(data, list) else []}


@router.get("/errors")
async def get_errors(
    start: str | None = None,
    end: str | None = None,
    bucket: str = Query("day", pattern="^(hour|day)$"),
    _auth: dict = Depends(auth_module.require_admin),
):
    supabase = _ensure_supabase()
    start_ts, end_ts = resolve_time_range(start, end, default_days=30)

    def _query_errors():
        return supabase.rpc(
            "llm_error_agg",
            {"start_ts": start_ts.isoformat(), "end_ts": end_ts.isoformat(), "bucket": bucket},
        ).execute()

    def _query_top():
        return supabase.rpc(
            "llm_top_errors",
            {"start_ts": start_ts.isoformat(), "end_ts": end_ts.isoformat(), "error_limit": 10},
        ).execute()

    errors_response, top_response = await asyncio.gather(
        asyncio.to_thread(_query_errors),
        asyncio.to_thread(_query_top),
    )
    errors = getattr(errors_response, "data", None)
    top = getattr(top_response, "data", None)
    return {
        "items": errors if isinstance(errors, list) else [],
        "top_errors": top if isinstance(top, list) else [],
    }


@router.get("/sentry/issues")
async def get_sentry_issues(
    limit: int = Query(10, ge=1, le=50),
    _auth: dict = Depends(auth_module.require_admin),
):
    issues = await fetch_sentry_issues(limit=limit)
    return {"issues": issues}
