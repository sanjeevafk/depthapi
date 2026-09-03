"""Document ingestion into local PostgreSQL using the declarative pipeline."""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from api.adapters.pg_adapter import get_pool
from api.services.rag.embeddings import embed_texts
from api.services.rag.graph.concept_extractor import extract_concepts_and_edges
from api.services.rag.pipeline.chunkers.semantic_chunker import SemanticChunker
from api.services.rag.pipeline.middleware.toc_stripper import TocStripper
from api.services.rag.pipeline.middleware.url_normalizer import UrlNormalizer
from api.services.rag.pipeline.models import (
    SCHEMA_VERSION,
    Chunk,
    Document,
    QualityScoreInputs,
)
from api.services.rag.pipeline.depth_engine_adapter import (
    has_depth_engine,
    run_depth_engine_pipeline,
)
from api.services.rag.pipeline.parsers.markdown_parser import MarkdownParser
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


PARSER = MarkdownParser()
TOC_STRIPPER = TocStripper()
URL_NORMALIZER = UrlNormalizer()
CHUNKER = SemanticChunker(config={"min_tokens": 1, "max_tokens": 480})


def _should_use_depth_engine(filename: str | None, engine: str | None) -> bool:
    if not has_depth_engine():
        return False
    if engine == "depth-engine":
        return True
    if engine == "python":
        return False
    if filename:
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext in {
            "pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "csv",
            "html", "htm", "odt", "ods", "odp", "rtf", "epub",
        }:
            return True
    return False


def _run_pipeline(
    raw_text: str,
    document_id: UUID,
    filename: str | None,
    source_url: str | None,
    collection_name: str | None,
    user_metadata: dict[str, Any],
    engine: str | None = None,
) -> tuple[Document, list[Chunk]]:
    """Process raw text through depth_engine (Rust native core) or declarative Python pipeline."""
    if _should_use_depth_engine(filename, engine):
        try:
            return run_depth_engine_pipeline(
                raw_text=raw_text,
                document_id=document_id,
                filename=filename,
                source_url=source_url,
                collection_name=collection_name,
                user_metadata=user_metadata,
                max_tokens=480,
                min_tokens=1,
            )
        except Exception as exc:
            log.warning("depth_engine processing failed, falling back to Python: %s", exc)

    source_uri = source_url or filename or f"direct://upload/{document_id}"

    raw_bytes = raw_text.encode("utf-8")
    content_hash = hashlib.sha256(raw_bytes).hexdigest()


    doc = Document.from_bytes(
        source_uri=source_uri,
        raw_content=raw_bytes,
        mime_type="text/markdown",
        metadata=user_metadata,
    )

    parsed_doc = PARSER.parse(doc)
    parsed_doc = TOC_STRIPPER.process(parsed_doc)

    if source_url:
        parsed_doc = URL_NORMALIZER.process(parsed_doc)

    chunks = CHUNKER.chunk(
        doc=parsed_doc,
        dataset_version="api-v1",
        source_name=filename or "api_upload",
        source_url=source_url,
        dataset_namespace=collection_name or "default",
    )

    if not chunks:
        clean_content = (
            parsed_doc.markdown_content.strip()
            if parsed_doc.markdown_content.strip()
            else raw_text.strip()
        )
        c_hash = hashlib.sha256(clean_content.encode("utf-8")).hexdigest()
        c_id = Chunk.build_chunk_id(str(document_id), 0, c_hash)
        token_count = max(1, len(clean_content.split()))
        quality_inputs = QualityScoreInputs(
            extraction_confidence=parsed_doc.extraction_confidence,
            markdown_cleanliness=1.0,
            header_continuity=1.0,
            token_validity=1.0,
        )
        fallback_chunk = Chunk(
            chunk_id=c_id,
            doc_id=str(document_id),
            content=clean_content,
            token_count=token_count,
            chunk_order=0,
            schema_version=SCHEMA_VERSION,
            parser_version=PARSER.version,
            chunker_version=f"{CHUNKER.name}@{CHUNKER.version}",
            middleware_versions=dict(parsed_doc.middleware_versions),
            source_name=filename or "api_upload",
            source_url=source_url,
            dataset_version="api-v1",
            dataset_namespace=collection_name or "default",
            source_content_hash=content_hash,
            content_hash=c_hash,
            quality_inputs=quality_inputs,
            quality_score=quality_inputs.compute_score(),
            extraction_method="direct_parse",
            is_fallback_result=True,
            metadata={
                "fallback": True,
                "applied_middleware": parsed_doc.applied_middleware,
                **user_metadata,
            },
        )
        chunks = [fallback_chunk]

    return doc, chunks


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

    document_id, queue_id = uuid4(), uuid4()

    try:
        async with get_pool().acquire() as conn:
            async with conn.transaction():
                user_metadata = req.metadata or {}
                encoded_metadata = json.dumps(user_metadata)
                owner_id = UUID(_api_key.id)

                collection = await conn.fetchrow(
                    """INSERT INTO knowledge_collections (id, api_key_id, name, metadata)
                       VALUES ($1, $2, $3, $4::jsonb)
                       ON CONFLICT (id) DO UPDATE SET name = knowledge_collections.name
                       WHERE knowledge_collections.api_key_id = EXCLUDED.api_key_id
                       RETURNING id""",
                    collection_id,
                    owner_id,
                    req.collection_name or "default",
                    encoded_metadata,
                )
                if collection is None:
                    raise HTTPException(404, "Collection not found")

                resolved_collection_id = collection["id"]
                content_hash = hashlib.sha256(req.raw_text.encode("utf-8")).hexdigest()

                # Idempotency check: short-circuit if identical content already ingested for this collection
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

                doc, chunks = _run_pipeline(
                    raw_text=req.raw_text,
                    document_id=document_id,
                    filename=req.filename,
                    source_url=req.source_url,
                    collection_name=req.collection_name,
                    user_metadata=user_metadata,
                    engine=req.engine,
                )


                embeddings = await embed_texts([c.content for c in chunks])
                if len(embeddings) != len(chunks):
                    raise RuntimeError("Mismatch between chunk count and embedding count")

                await conn.execute(
                    """INSERT INTO knowledge_documents (
                        id, collection_id, filename, source_url, content, content_hash, metadata
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)""",
                    document_id,
                    resolved_collection_id,
                    req.filename,
                    req.source_url,
                    req.raw_text,
                    content_hash,
                    encoded_metadata,
                )

                for chunk, embedding in zip(chunks, embeddings):
                    chunk_metadata = {
                        **user_metadata,
                        **(chunk.metadata or {}),
                        "schema_version": chunk.schema_version,
                        "parser_version": chunk.parser_version,
                        "chunker_version": chunk.chunker_version,
                        "middleware_versions": chunk.middleware_versions,
                        "quality_score": chunk.quality_score,
                        "source_content_hash": chunk.source_content_hash,
                        "content_hash": chunk.content_hash,
                        "chunk_id": chunk.chunk_id,
                    }
                    hierarchy = chunk.metadata.get("hierarchy")
                    section_title = (
                        hierarchy[-1]
                        if isinstance(hierarchy, list) and hierarchy
                        else None
                    )

                    await conn.execute(
                        """INSERT INTO knowledge_chunks (
                            document_id, chunk_order, content, token_count, embedding, metadata, section_title, chunk_hash
                        ) VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb, $7, $8)""",
                        document_id,
                        chunk.chunk_order,
                        chunk.content,
                        chunk.token_count,
                        embedding,
                        json.dumps(chunk_metadata),
                        section_title,
                        chunk.content_hash,
                    )

                # Deterministic concept and graph extraction & upsert
                try:
                    graph = extract_concepts_and_edges(
                        raw_text=req.raw_text,
                        chunks=chunks,
                        document_title=req.filename,
                        user_metadata=user_metadata,
                    )
                    concept_id_map: dict[str, UUID] = {}
                    for concept in graph.concepts:
                        c_id = uuid5(NAMESPACE_URL, f"concept:{resolved_collection_id}:{concept.name.lower()}")
                        await conn.execute(
                            """INSERT INTO knowledge_concepts (id, collection_id, name, concept_type, description, metadata)
                               VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                               ON CONFLICT (collection_id, name) DO UPDATE
                               SET metadata = knowledge_concepts.metadata || EXCLUDED.metadata""",
                            c_id,
                            resolved_collection_id,
                            concept.name,
                            concept.concept_type,
                            concept.description,
                            json.dumps(concept.metadata),
                        )
                        concept_id_map[concept.name.lower()] = c_id

                    for edge in graph.edges:
                        src_id = concept_id_map.get(edge.source_concept.lower())
                        tgt_id = concept_id_map.get(edge.target_concept.lower())
                        if src_id and tgt_id:
                            edge_id = uuid5(NAMESPACE_URL, f"edge:{resolved_collection_id}:{src_id}:{tgt_id}:{edge.relation_type}")
                            await conn.execute(
                                """INSERT INTO knowledge_edges (id, collection_id, source_concept_id, target_concept_id, relation_type, weight, metadata)
                                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
                                   ON CONFLICT (collection_id, source_concept_id, target_concept_id, relation_type) DO UPDATE
                                   SET weight = EXCLUDED.weight""",
                                edge_id,
                                resolved_collection_id,
                                src_id,
                                tgt_id,
                                edge.relation_type,
                                edge.weight,
                                json.dumps(edge.metadata),
                            )

                    for link in graph.chunk_links:
                        c_id = concept_id_map.get(link.concept_name.lower())
                        if c_id:
                            await conn.execute(
                                """SELECT link_chunk_to_concept($1, $2, $3, $4, $5::jsonb)""",
                                document_id,
                                link.chunk_index,
                                c_id,
                                link.confidence,
                                json.dumps(link.metadata),
                            )
                except Exception as g_exc:
                    log.warning("Concept graph extraction skipped or encountered error: %s", g_exc)

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
