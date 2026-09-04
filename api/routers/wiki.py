"""
Wiki vault routing for Karpathy LLM-Wiki materialization and continuous linting.
"""
from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.adapters.pg_adapter import get_pool
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key
from api.services.wiki.vault_manager import get_vault_manager

log = logging.getLogger(__name__)

router = APIRouter(prefix="/wiki", tags=["wiki"])


class WikiExportRequest(BaseModel):
    collection_id: str | None = None


class WikiExportResponse(BaseModel):
    status: str
    exported_count: int
    files: list[str] = Field(default_factory=list)
    vault_dir: str


class WikiLintResponse(BaseModel):
    total_notes: int
    broken_links: list[dict[str, Any]] = Field(default_factory=list)
    orphan_nodes: list[str] = Field(default_factory=list)
    cycles: list[list[str]] = Field(default_factory=list)
    valid: bool


@router.post("/export", response_model=WikiExportResponse)
async def export_wiki(
    req: WikiExportRequest | None = None,
    _api_key: ApiKeyRecord = Depends(verify_api_key),
) -> WikiExportResponse:
    """Export PostgreSQL concepts and graph edges to local Markdown vault."""
    collection_uuid = None
    if req and req.collection_id:
        try:
            collection_uuid = UUID(req.collection_id)
        except ValueError as exc:
            raise HTTPException(400, "collection_id must be a UUID") from exc

    concepts: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            # Query concepts isolated by tenant api_key
            concept_query = """
                SELECT c.id, c.name, c.concept_type, c.description, c.metadata, c.created_at
                FROM knowledge_concepts c
                JOIN knowledge_collections k ON k.id = c.collection_id
                WHERE k.api_key_id = $1
                  AND ($2::uuid IS NULL OR c.collection_id = $2::uuid)
                ORDER BY c.name ASC
            """
            rows = await conn.fetch(concept_query, UUID(_api_key.id), collection_uuid)
            concepts = [dict(r) for r in rows]

            # Query edges
            edge_query = """
                SELECT e.id, e.source_concept_id, e.target_concept_id, e.relation_type, e.weight
                FROM knowledge_edges e
                JOIN knowledge_collections k ON k.id = e.collection_id
                WHERE k.api_key_id = $1
                  AND ($2::uuid IS NULL OR e.collection_id = $2::uuid)
            """
            edge_rows = await conn.fetch(edge_query, UUID(_api_key.id), collection_uuid)
            edges = [dict(r) for r in edge_rows]
    except Exception as exc:
        # Fallback if DB is unavailable or empty
        log.warning("Wiki export DB query failed, exporting empty vault: %s", exc)

    manager = get_vault_manager()
    result = manager.export_concepts_to_vault(concepts, edges)
    return WikiExportResponse(**result)


@router.get("/lint", response_model=WikiLintResponse)
async def lint_wiki(_api_key: ApiKeyRecord = Depends(verify_api_key)) -> WikiLintResponse:
    """Runs high-speed vault linter detecting broken [[WikiLinks]], orphans, and cycles."""
    manager = get_vault_manager()
    report = manager.lint_vault()
    return WikiLintResponse(**report)


@router.get("/concepts", response_model=list[dict[str, Any]])
async def list_wiki_concepts(_api_key: ApiKeyRecord = Depends(verify_api_key)) -> list[dict[str, Any]]:
    """Lists all concept notes currently materialized in the vault."""
    manager = get_vault_manager()
    return manager.list_concepts()


@router.get("/concepts/{slug}")
async def get_wiki_concept(slug: str, _api_key: ApiKeyRecord = Depends(verify_api_key)) -> dict[str, Any]:
    """Retrieves a single concept note from the vault."""
    manager = get_vault_manager()
    concept = manager.read_concept(slug)
    if not concept:
        raise HTTPException(status_code=404, detail=f"Concept '{slug}' not found in wiki vault")
    return concept
