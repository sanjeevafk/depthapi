import asyncio
from fastapi import APIRouter, Depends, HTTPException

from auth import verify_token, get_supabase_admin
from pydantic import BaseModel
from typing import List
from datetime import datetime
import structlog

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["history"])

class HistoryItem(BaseModel):
    id: str
    topic: str
    levels: List[str]
    mode: str = "fast"
    created_at: datetime

class HistoryCreate(BaseModel):
    topic: str
    levels: List[str]
    mode: str = "fast"

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
        allowed_modes = {"fast", "ensemble"}
        for item in response.data:
            if item.get("mode") not in allowed_modes:
                item["mode"] = "fast"
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
        allowed_modes = {"fast", "ensemble"}
        mode = data.mode if data.mode in allowed_modes else "fast"
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
