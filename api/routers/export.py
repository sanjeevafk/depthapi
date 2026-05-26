"""Data export endpoints."""

import asyncio
import io
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.utils import DEFAULT_CHAT_MODE, PROMPT_DEPTHS, SUPPORTED_CHAT_MODES, normalize_mode, sanitize_filename
from api.services.inference.inference import generate_explanation

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    explanations: dict[str, str]
    format: str = Field(default="txt", pattern="^(txt|md)$")
    mode: str = DEFAULT_CHAT_MODE
    visuals: Optional[dict[str, str]] = None


@router.post("/export")
async def export_explanations(req: ExportRequest, api_key: ApiKeyRecord = Depends(verify_api_key)) -> StreamingResponse:
    """Export explanations. Requires Pro or Enterprise plan."""
    if not api_key.is_pro:
        raise HTTPException(status_code=403, detail="Exporting requires a Pro or Enterprise plan.")
        
    req.mode = normalize_mode(req.mode)
    if req.mode not in SUPPORTED_CHAT_MODES:
        req.mode = DEFAULT_CHAT_MODE

    target_levels = set(PROMPT_DEPTHS)
    
    current_levels = set(req.explanations.keys())
    missing_levels = list(target_levels - current_levels)

    if missing_levels:
        tasks = {
            lvl: generate_explanation(req.topic, lvl, mode=req.mode, is_pro=True)
            for lvl in missing_levels
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        for lvl, result in zip(tasks.keys(), results):
            if isinstance(result, str):
                req.explanations[lvl] = result
            else:
                req.explanations[lvl] = f"Error generating content: {str(result)}"
    
    ordered_explanations = {}
    for lvl in PROMPT_DEPTHS:
        if lvl in req.explanations:
            ordered_explanations[lvl] = req.explanations[lvl]
                
    req.explanations = ordered_explanations

    slug = sanitize_filename(req.topic)
    filename_base = f"depthapi-{slug}"
    
    if req.format == "txt":
        content = f"# {req.topic}\n\n"
        if len(req.explanations) > 1:
            content += "---\n\n"
        for level, text in req.explanations.items():
            if len(req.explanations) > 1:
                lvl_name = level.replace('eli', 'ELI-').upper()
                content += f"## {lvl_name}\n\n"
            content += f"{text.strip()}\n\n"
            if len(req.explanations) > 1:
                content += "---\n\n"
        
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="text/plain",
            headers={"Content-Disposition": f"attachment; filename={filename_base}.txt"},
        )
    else:
        content = f"# {req.topic}\n\n"
        if len(req.explanations) > 1:
            content += "---\n\n"
        for level, text in req.explanations.items():
            if len(req.explanations) > 1:
                lvl_name = level.replace('eli', 'ELI-').upper()
                content += f"## {lvl_name}\n\n"
            content += f"{text.strip()}\n\n"
            if len(req.explanations) > 1:
                content += "---\n\n"
                
        return StreamingResponse(
            io.BytesIO(content.encode()),
            media_type="text/markdown",
            headers={"Content-Disposition": f"attachment; filename={filename_base}.md"},
        )
