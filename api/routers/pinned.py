"""Pinned topics endpoint."""

from fastapi import APIRouter

router = APIRouter(tags=["pinned"])

PINNED_TOPICS = [
    {"id": "ai-basics", "title": "AI Basics", "description": "Fundamentals of artificial intelligence"},
    {"id": "quantum-physics", "title": "Quantum Physics", "description": "The strange world of quantum mechanics"},
    {"id": "photosynthesis", "title": "Photosynthesis", "description": "How plants convert sunlight to energy"},
    {"id": "blockchain", "title": "Blockchain", "description": "Distributed ledger technology explained"},
    {"id": "climate-change", "title": "Climate Change", "description": "Understanding global warming"},
    {"id": "neural-networks", "title": "Neural Networks", "description": "How machines learn like brains"},
    {"id": "evolution", "title": "Evolution", "description": "Natural selection and species adaptation"},
    {"id": "black-holes", "title": "Black Holes", "description": "Cosmic objects with extreme gravity"},
]


@router.get("/pinned")
async def get_pinned() -> list[dict]:
    """Return curated pinned topics."""
    return PINNED_TOPICS
