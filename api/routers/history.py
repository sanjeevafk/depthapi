import asyncio
from datetime import datetime
from typing import List

import structlog
from fastapi import APIRouter, Depends, HTTPException

from auth import verify_token, get_supabase_admin
from pydantic import BaseModel
from utils import DEFAULT_CHAT_MODE, SUPPORTED_CHAT_MODES, normalize_mode

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["history"])

class HistoryItem(BaseModel):
    id: str
    topic: str
    levels: List[str]
    mode: str = DEFAULT_CHAT_MODE
    created_at: datetime

class HistoryCreate(BaseModel):
    topic: str
    levels: List[str]
    mode: str = DEFAULT_CHAT_MODE

@router.get("/history", response_model=List[HistoryItem])
async def get_history(auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]
    user_id = user.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        response = await asyncio.to_thread(
            supabase.table("history").select("*").eq("user_id", user_id).order("created_at", desc=True).limit(50).execute
        )
        for item in response.data:
            normalized_mode = normalize_mode(item.get("mode"))
            item["mode"] = normalized_mode if normalized_mode in SUPPORTED_CHAT_MODES else DEFAULT_CHAT_MODE
        return response.data

    except Exception as e:
        logger.error("get_history_error", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to fetch history")

@router.post("/history", response_model=HistoryItem)
async def add_history_item(data: HistoryCreate, auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]
    user_id = user.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
        
    try:
        normalized_mode = normalize_mode(data.mode)
        mode = normalized_mode if normalized_mode in SUPPORTED_CHAT_MODES else DEFAULT_CHAT_MODE
        response = await asyncio.to_thread(
            supabase.table("history").insert({
                "user_id": user_id,
                "topic": data.topic,
                "levels": data.levels,
                "mode": mode
            }).execute
        )

        
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to save history")
            
        return response.data[0]
    except Exception as e:
        logger.error("add_history_error", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to save history")

@router.delete("/history/{item_id}")
async def delete_history_item(item_id: str, auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]
    user_id = user.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
        
    try:
        # Securely delete only if user_id matches
        await asyncio.to_thread(
            supabase.table("history").delete().eq("id", item_id).eq("user_id", user_id).execute
        )
        return {"status": "deleted"}

    except Exception as e:
        logger.error("delete_history_error", error=str(e), user_id=user_id, item_id=item_id)
        raise HTTPException(status_code=500, detail="Failed to delete history item")
@router.delete("/history")
async def clear_history(auth_data: dict = Depends(verify_token)):
    user = auth_data["user"]
    user_id = user.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
        
    try:
        await asyncio.to_thread(
            supabase.table("history").delete().eq("user_id", user_id).execute
        )
        return {"status": "cleared"}

    except Exception as e:
        logger.error("clear_history_error", error=str(e), user_id=user_id)
        raise HTTPException(status_code=500, detail="Failed to clear history")
