"""Demo frontend route — served at /demo."""

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["demo"])

_STATIC_DIR = Path(__file__).parent.parent / "static"
_DEMO_HTML_PATH = _STATIC_DIR / "demo.html"
_PRESENTATION_HTML_PATH = _STATIC_DIR / "presentation.html"


@router.get("/demo", response_class=HTMLResponse, include_in_schema=False)
async def serve_demo():
    """Serve the DepthAPI interactive demo frontend."""
    return HTMLResponse(content=_DEMO_HTML_PATH.read_text(encoding="utf-8"))


@router.get("/presentation", response_class=HTMLResponse, include_in_schema=False)
async def serve_presentation():
    """Serve the DepthAPI presentation slide deck."""
    return HTMLResponse(content=_PRESENTATION_HTML_PATH.read_text(encoding="utf-8"))
