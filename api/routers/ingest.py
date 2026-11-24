"""Document ingestion into local PostgreSQL using the declarative pipeline."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from api.adapters.pg_adapter import get_pool
from api.services.rag.chunker import ChunkData, chunk_markdown, normalize_urls, strip_toc
from api.services.rag.embeddings import embed_texts
from api.services.rag.graph.concept_extractor import extract_concepts_and_edges
from api.services.security.api_key_auth import ApiKeyRecord, verify_api_key

log = logging.getLogger(__name__)


router = APIRouter(tags=["ingest"])


class IngestRequest(BaseModel):
    collection_id: str | None = None
    collection_name: str | None = None
    filename: str | None = None
    source_url: str | None = None
    raw_text: str | None = Field(default=None, max_length=100000)
    metadata: dict[str, Any] | None = None
    engine: str | None = None



class IngestResponse(BaseModel):
    collection_id: str
    document_id: str
    queue_id: str
    status: str


def _chunk_document(
    raw_text: str,
    source_url: str | None,
) -> list[ChunkData]:
    """Normalize Markdown and split into chunks."""
    cleaned = strip_toc(raw_text)
    if source_url:
        cleaned = normalize_urls(cleaned, source_url)
    chunks = chunk_markdown(cleaned, max_tokens=480, min_tokens=1)
    if not chunks and raw_text.strip():
        fallback = cleaned or raw_text.strip()
        chunks = [
            ChunkData(
                content=fallback,
                token_count=max(1, len(fallback.split())),
                chunk_order=0,
                content_hash=hashlib.sha256(fallback.encode("utf-8")).hexdigest(),
                section_title=None,
            )
        ]
    return chunks


async def _process_and_store_document(
    raw_text: str,
    document_id: UUID,
    collection_id: UUID,
    owner_id: UUID,
    filename: str | None,
    source_url: str | None,
    collection_name: str | None,
    user_metadata: dict[str, Any],
    engine: str | None = None,
    skip_pre_idempotency: bool = False,
) -> IngestResponse:
    queue_id = uuid4()
    content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

    # Fast idempotency short-circuit for caller-supplied collections: avoids
    # paying for chunking/embeddings on exact duplicates.
    if not skip_pre_idempotency:
        try:
            async with get_pool().acquire() as pre_conn:
                pre_existing = await pre_conn.fetchrow(
                    """SELECT id FROM knowledge_documents
                       WHERE collection_id = $1 AND content_hash = $2
                         AND EXISTS (
                             SELECT 1 FROM knowledge_collections
                             WHERE id = $1 AND api_key_id = $3
                         )
                       LIMIT 1""",
                    collection_id,
                    content_hash,
                    owner_id,
                )
                if pre_existing is not None:
                    doc_id_str = str(pre_existing["id"])
                    return IngestResponse(
                        collection_id=str(collection_id),
                        document_id=doc_id_str,
                        queue_id=doc_id_str,
                        status="complete",
                    )
        except HTTPException:
            raise
        except Exception as exc:
            log.debug("Pre-txn idempotency check skipped: %s", exc)

    resolved_collection_id = collection_id

    # CPU-bound chunking + network-bound embeddings run outside the transaction.
    chunks = await asyncio.to_thread(_chunk_document, raw_text, source_url)

    embeddings = await embed_texts([c.content for c in chunks])
    if len(embeddings) != len(chunks):
        raise RuntimeError("Mismatch between chunk count and embedding count")

    try:
        graph = extract_concepts_and_edges(
            raw_text=raw_text,
            chunks=chunks,
            document_title=filename,
            user_metadata=user_metadata,
        )
    except Exception as g_exc:
        log.warning("Concept graph extraction skipped or encountered error: %s", g_exc)
        graph = None

    try:
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                encoded_metadata = json.dumps(user_metadata)

                collection = await conn.fetchrow(
                    """INSERT INTO knowledge_collections (id, api_key_id, name, metadata)
                       VALUES ($1, $2, $3, $4::jsonb)
                       ON CONFLICT (id) DO UPDATE SET name = knowledge_collections.name
                       WHERE knowledge_collections.api_key_id = EXCLUDED.api_key_id
                       RETURNING id""",
                    collection_id,
                    owner_id,
                    collection_name or "default",
                    encoded_metadata,
                )
                if collection is None:
                    raise HTTPException(403, "Collection belongs to a different API key")

                resolved_collection_id = collection["id"]

                existing_doc = await conn.fetchrow(
                    """SELECT id FROM knowledge_documents
                       WHERE collection_id = $1 AND content_hash = $2
                       LIMIT 1""",
                    resolved_collection_id,
                    content_hash,
                )
                if existing_doc is not None:
                    doc_id_str = str(existing_doc["id"])
                    return IngestResponse(
                        collection_id=str(resolved_collection_id),
                        document_id=doc_id_str,
                        queue_id=doc_id_str,
                        status="complete",
                    )

                await conn.execute(
                    """INSERT INTO knowledge_documents (
                        id, collection_id, filename, source_url, content, content_hash, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
                    document_id,
                    resolved_collection_id,
                    filename,
                    source_url,
                    raw_text,
                    content_hash,
                    encoded_metadata,
                )

                chunk_rows = []
                for chunk, embedding in zip(chunks, embeddings):
                    chunk_metadata = {
                        **user_metadata,
                        "source_content_hash": content_hash,
                        "content_hash": chunk.content_hash,
                    }
                    chunk_rows.append((
                        document_id,
                        chunk.chunk_order,
                        chunk.content,
                        chunk.token_count,
                        embedding,
                        json.dumps(chunk_metadata),
                        chunk.section_title,
                        chunk.content_hash,
                    ))

                if chunk_rows:
                    await conn.executemany(
                        """INSERT INTO knowledge_chunks (
                            document_id, chunk_order, content, token_count, embedding, metadata, section_title, chunk_hash
                        ) VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb, $7, $8)""",
                        chunk_rows,
                    )

                if graph is not None:
                    try:
                        concept_id_map: dict[str, UUID] = {}
                        concept_rows = []
                        for concept in graph.concepts:
                            c_id = uuid5(NAMESPACE_URL, f"concept:{resolved_collection_id}:{concept.name.lower()}")
                            concept_id_map[concept.name.lower()] = c_id
                            concept_rows.append((
                                c_id,
                                resolved_collection_id,
                                concept.name,
                                concept.concept_type,
                                concept.description,
                                json.dumps(concept.metadata),
                            ))
                        if concept_rows:
                            await conn.executemany(
                                """INSERT INTO knowledge_concepts (id, collection_id, name, concept_type, description, metadata)
                                   VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                                   ON CONFLICT (collection_id, name) DO UPDATE
                                   SET metadata = knowledge_concepts.metadata || EXCLUDED.metadata""",
                                concept_rows,
                            )

                        edge_rows = []
                        for edge in graph.edges:
                            src_id = concept_id_map.get(edge.source_concept.lower())
                            tgt_id = concept_id_map.get(edge.target_concept.lower())
                            if src_id and tgt_id:
                                edge_id = uuid5(NAMESPACE_URL, f"edge:{resolved_collection_id}:{src_id}:{tgt_id}:{edge.relation_type}")
                                edge_rows.append((
                                    edge_id,
                                    resolved_collection_id,
                                    src_id,
                                    tgt_id,
                                    edge.relation_type,
                                    edge.weight,
                                    json.dumps(edge.metadata),
                                ))
                        if edge_rows:
                            await conn.executemany(
                                """INSERT INTO knowledge_edges (id, collection_id, source_concept_id, target_concept_id, relation_type, weight, metadata)
                                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                                   ON CONFLICT (collection_id, source_concept_id, target_concept_id, relation_type) DO UPDATE
                                   SET weight = EXCLUDED.weight""",
                                edge_rows,
                            )

                        link_rows = []
                        for link in graph.chunk_links:
                            c_id = concept_id_map.get(link.concept_name.lower())
                            if c_id:
                                link_rows.append((
                                    document_id,
                                    link.chunk_index,
                                    c_id,
                                    link.confidence,
                                    json.dumps(link.metadata),
                                ))
                        if link_rows:
                            await conn.executemany(
                                """SELECT link_chunk_to_concept($1, $2, $3, $4, $5::jsonb)""",
                                link_rows,
                            )
                    except Exception as g_exc:
                        log.warning("Concept graph upsert skipped or encountered error: %s", g_exc)

                await conn.execute(
                    """INSERT INTO knowledge_ingestion_queue (id, document_id, status)
                       VALUES ($1, $2, 'complete')""",
                    queue_id,
                    document_id,
                )

    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, "PostgreSQL is unavailable") from exc

    return IngestResponse(
        collection_id=str(resolved_collection_id),
        document_id=str(document_id),
        queue_id=str(queue_id),
        status="complete",
    )


@router.post("/ingest", response_model=IngestResponse)
async def ingest(
    req: IngestRequest,
    request: Request,
    _api_key: ApiKeyRecord = Depends(verify_api_key),
) -> IngestResponse:
    if not req.raw_text or not req.raw_text.strip():
        raise HTTPException(400, "raw_text is required")

    try:
        collection_id = UUID(req.collection_id) if req.collection_id else uuid4()
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc

    document_id = uuid4()
    user_metadata = req.metadata or {}
    owner_id = UUID(_api_key.id)

    return await _process_and_store_document(
        raw_text=req.raw_text,
        document_id=document_id,
        collection_id=collection_id,
        owner_id=owner_id,
        filename=req.filename,
        source_url=req.source_url,
        collection_name=req.collection_name,
        user_metadata=user_metadata,
        engine=req.engine,
        skip_pre_idempotency=(req.collection_id is None),
    )


MAX_FILE_BYTES = 75 * 1024 * 1024  # 75 MB limit
MAX_EXTRACTED_CHARS = 2_000_000  # bounds chunking + embedding work per request


@router.post("/ingest/file", response_model=IngestResponse)
async def ingest_file(
    file: UploadFile = File(...),
    collection_id: str | None = Form(None),
    collection_name: str | None = Form(None),
    source_url: str | None = Form(None),
    metadata: str | None = Form(None),
    engine: str | None = Form(None),
    _api_key: ApiKeyRecord = Depends(verify_api_key),
) -> IngestResponse:
    """Unified document file ingestion: converts PDF, DOCX, XLSX, PPTX, CSV, EPUB, and Markdown via anydoc."""
    if file.size and file.size > MAX_FILE_BYTES:
        raise HTTPException(413, f"Uploaded file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit")

    file_bytes = await file.read(MAX_FILE_BYTES + 1)
    if len(file_bytes) > MAX_FILE_BYTES:
        raise HTTPException(413, f"Uploaded file exceeds the {MAX_FILE_BYTES // (1024 * 1024)} MB limit")

    if not file_bytes:
        raise HTTPException(400, "Uploaded file is empty")

    user_metadata: dict[str, Any] = {}
    if metadata:
        try:
            parsed_meta = json.loads(metadata)
            if isinstance(parsed_meta, dict):
                user_metadata = parsed_meta
        except json.JSONDecodeError as exc:
            raise HTTPException(400, "metadata must be valid JSON") from exc

    raw_text: str = ""
    filename = file.filename or "uploaded_file"
    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else None

    if ext in {".md", ".markdown", ".txt"}:
        try:
            raw_text = file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(415, f"File '{filename}' is not valid UTF-8 text.") from exc
    else:
        try:
            from markitdown import MarkItDown

            def _convert() -> str:
                res = MarkItDown().convert_stream(io.BytesIO(file_bytes), file_extension=ext)
                return (res.text_content or "").strip()

            raw_text = await asyncio.to_thread(_convert)
            user_metadata["parser_engine"] = "markitdown"
        except Exception as exc:
            log.warning("markitdown conversion failed on %s: %s", filename, exc)

    if not raw_text:
        try:
            raw_text = file_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(
                415,
                f"Unsupported or unreadable binary format for '{filename}'.",
            ) from exc

    if not raw_text.strip():
        raise HTTPException(400, "No readable text content extracted from file")
    if len(raw_text) > MAX_EXTRACTED_CHARS:
        raise HTTPException(413, f"Extracted text exceeds the {MAX_EXTRACTED_CHARS:,} character limit")

    try:
        col_uuid = UUID(collection_id) if collection_id else uuid4()
    except ValueError as exc:
        raise HTTPException(400, "collection_id must be a UUID") from exc

    doc_uuid = uuid4()
    owner_uuid = UUID(_api_key.id)

    return await _process_and_store_document(
        raw_text=raw_text,
        document_id=doc_uuid,
        collection_id=col_uuid,
        owner_id=owner_uuid,
        filename=filename,
        source_url=source_url,
        collection_name=collection_name,
        user_metadata=user_metadata,
        engine=engine,
        skip_pre_idempotency=(collection_id is None),
    )
