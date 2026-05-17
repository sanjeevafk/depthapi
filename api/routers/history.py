from datetime import datetime
from typing import List, Dict, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from api.shared_types import PromptSpecRequest
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.auth import get_supabase_admin
from api.logging_config import anonymize_user_id
from pydantic import BaseModel

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["history"])

class HistoryItem(BaseModel):
    id: str
    topic: str
    prompt_specs: List[Dict[str, Any]]
    created_at: datetime

class HistoryCreate(BaseModel):
    topic: str
    prompt_specs: List[PromptSpecRequest]

@router.get("/history", response_model=List[HistoryItem])
async def get_history(api_key: ApiKeyRecord = Depends(verify_api_key)):
    user_id = api_key.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
    
    try:
        response = await (
            supabase.table("history")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return response.data

    except Exception as e:
        logger.error("get_history_error", error=str(e), user_id_hash=anonymize_user_id(str(user_id)))
        raise HTTPException(status_code=500, detail="Failed to fetch history")

@router.post("/history", response_model=HistoryItem)
async def add_history_item(data: HistoryCreate, api_key: ApiKeyRecord = Depends(verify_api_key)):
    user_id = api_key.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
        
    try:
        prompt_specs = [
            {
                "topic": spec.to_prompt_spec(data.topic).topic,
                "depth": spec.to_prompt_spec(data.topic).depth,
                "task": spec.to_prompt_spec(data.topic).task,
                "reasoning": spec.to_prompt_spec(data.topic).reasoning,
                "style": spec.to_prompt_spec(data.topic).style,
                "capabilities": sorted(spec.to_prompt_spec(data.topic).capabilities),
            }
            for spec in data.prompt_specs
        ]
        response = await supabase.table("history").insert({
                "user_id": user_id,
                "topic": data.topic,
                "prompt_specs": prompt_specs,
            }).execute()

        
        if not response.data:
            raise HTTPException(status_code=500, detail="Failed to save history")
            
        return response.data[0]
    except Exception as e:
        logger.error("add_history_error", error=str(e), user_id_hash=anonymize_user_id(str(user_id)))
        raise HTTPException(status_code=500, detail="Failed to save history")

@router.delete("/history/{item_id}")
async def delete_history_item(item_id: str, api_key: ApiKeyRecord = Depends(verify_api_key)):
    user_id = api_key.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
        
    try:
        # Securely delete only if user_id matches
        await (
            supabase.table("history")
            .delete()
            .eq("id", item_id)
            .eq("user_id", user_id)
            .execute()
        )
        return {"status": "deleted"}

    except Exception as e:
        logger.error(
            "delete_history_error",
            error=str(e),
            user_id_hash=anonymize_user_id(str(user_id)),
            item_id=item_id,
        )
        raise HTTPException(status_code=500, detail="Failed to delete history item")
@router.delete("/history")
async def clear_history(api_key: ApiKeyRecord = Depends(verify_api_key)):
    user_id = api_key.id
    
    supabase = get_supabase_admin()
    if not supabase:
        raise HTTPException(status_code=500, detail="Database connection error")
        
    try:
        await supabase.table("history").delete().eq("user_id", user_id).execute()
        return {"status": "cleared"}

    except Exception as e:
        logger.error("clear_history_error", error=str(e), user_id_hash=anonymize_user_id(str(user_id)))
        raise HTTPException(status_code=500, detail="Failed to clear history")
