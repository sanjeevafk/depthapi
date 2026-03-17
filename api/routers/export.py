import asyncio
import io
import structlog
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from auth import verify_token, check_is_pro
from utils import DEFAULT_CHAT_MODE, FREE_LEVELS, LEARNING_MODE, SUPPORTED_CHAT_MODES, normalize_mode
from services.ensemble import ensemble_generate
from services.inference import generate_explanation

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    explanations: dict[str, str]
    format: str = Field(default="txt", pattern="^(txt|md)$")
    premium: bool = False
    mode: str = DEFAULT_CHAT_MODE
    visuals: Optional[dict[str, str]] = None

@router.post("/export")
async def export_explanations(req: ExportRequest, auth_data: dict = Depends(verify_token)) -> StreamingResponse:
    """Export explanations in requested format."""
    # Verify pro status
    user = auth_data["user"]
    is_verified_pro = await check_is_pro(user.id)
    
    if not is_verified_pro:
        raise HTTPException(status_code=403, detail="Exporting is a premium feature. Please upgrade to use this functionality.")
        
    req.mode = normalize_mode(req.mode)
    if req.mode not in SUPPORTED_CHAT_MODES:
        req.mode = DEFAULT_CHAT_MODE

    # Identify levels to include based on mode
    target_levels = set(FREE_LEVELS)
    
    current_levels = set(req.explanations.keys())
    missing_levels = list(target_levels - current_levels)

    if missing_levels:
        if req.mode == LEARNING_MODE:
            tasks = {lvl: ensemble_generate(req.topic, lvl, is_verified_pro, req.mode) for lvl in missing_levels}
        else:
            tasks = {lvl: generate_explanation(req.topic, lvl, mode=req.mode) for lvl in missing_levels}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        for lvl, result in zip(tasks.keys(), results):
            if isinstance(result, str):
                req.explanations[lvl] = result
            else:
                req.explanations[lvl] = f"Error generating content: {str(result)}"
    
    ordered_explanations = {}
    for lvl in FREE_LEVELS:
        if lvl in req.explanations:
            ordered_explanations[lvl] = req.explanations[lvl]
                
    req.explanations = ordered_explanations

    slug = req.topic.lower().replace(" ", "-")[:30]
    filename_base = f"knowbear-{slug}"

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
    elif req.format == "md":
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
    raise HTTPException(400, "Requested format is currently disabled or invalid")
