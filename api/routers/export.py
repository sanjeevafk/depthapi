import asyncio
import io
import json
import base64
import re
import markdown
import structlog
from typing import Optional, Dict

from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
# 2026-01: Disabled PDF export to reduce serverless package size + fix output quality issues
# from fpdf import FPDF, HTMLMixin

from auth import verify_token, check_is_pro
from utils import FREE_LEVELS, PREMIUM_LEVELS
from services.ensemble import ensemble_generate

logger = structlog.get_logger(__name__)
router = APIRouter(tags=["export"])


class ExportRequest(BaseModel):
    topic: str = Field(..., min_length=1)
    explanations: dict[str, str]
    format: str = Field(default="txt", pattern="^(txt|md)$")
    premium: bool = False
    mode: str = "fast"
    visuals: Optional[dict[str, str]] = None


# 2026-01: PDF Export components temporarily disabled
"""
class StyledPDF(FPDF, HTMLMixin):
    def __init__(self, topic_name: str, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.topic_name = topic_name

    def header(self):
        # Fill background for every page
        self.set_fill_color(10, 10, 10)
        self.rect(0, 0, 210, 297, "F")
        
        if self.page_no() > 1:
            self.set_font("helvetica", "I", 8)
            self.set_text_color(150, 150, 150)
            self.cell(0, 10, f"KnowBear Export: {self.topic_name}", align="R")
            self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font("helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, f"Page {self.page_no()} / {{nb}}", align="C")


def safe_latin1(text: str) -> str:
    # Ensure text is safe for fpdf2's default helvetica font.
    return text.encode('latin-1', 'replace').decode('latin-1')
"""

@router.post("/export")
async def export_explanations(req: ExportRequest, auth_data: dict = Depends(verify_token)) -> StreamingResponse:
    """Export explanations in requested format."""
    # Verify pro status
    user = auth_data["user"]
    is_verified_pro = await check_is_pro(user.id)
    
    if not is_verified_pro:
        raise HTTPException(status_code=403, detail="Exporting is a premium feature. Please upgrade to use this functionality.")
        
    if not req.premium:
        raise HTTPException(status_code=403, detail="Exporting is a premium feature. Please upgrade to use this functionality.")

    allowed_modes = {"fast", "ensemble"}
    if req.mode not in allowed_modes:
        req.mode = "fast"

    # Identify levels to include based on mode
    target_levels = set(FREE_LEVELS)
    if is_verified_pro:
        target_levels.update(PREMIUM_LEVELS)
    
    current_levels = set(req.explanations.keys())
    missing_levels = list(target_levels - current_levels)

    if missing_levels:
        tasks = {lvl: ensemble_generate(req.topic, lvl, is_verified_pro, req.mode) for lvl in missing_levels}
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
    if is_verified_pro:
        for lvl in PREMIUM_LEVELS:
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
