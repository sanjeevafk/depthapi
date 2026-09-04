"""Deterministic concept and lineage extractor from document structure and content."""
from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

try:
    import depth_engine
    _HAS_DEPTH_ENGINE = True
except ImportError:
    depth_engine = None  # type: ignore[assignment]
    _HAS_DEPTH_ENGINE = False


class Concept(BaseModel):
    name: str
    concept_type: str = "topic"
    description: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConceptEdge(BaseModel):
    source_concept: str
    target_concept: str
    relation_type: str = "relates_to"
    weight: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkConceptLink(BaseModel):
    chunk_index: int
    concept_name: str
    confidence: float = 1.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedGraph(BaseModel):
    concepts: list[Concept] = Field(default_factory=list)
    edges: list[ConceptEdge] = Field(default_factory=list)
    chunk_links: list[ChunkConceptLink] = Field(default_factory=list)


_WIKILINK_PATTERN = re.compile(r"\[\[([^\]\|]+)(?:\|([^\]]+))?\]\]")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
_CLEAN_PREFIX_PATTERN = re.compile(r"^[0-9\.\-\s]+|^[#*_`\s]+|[#*_`\s]+$")


def _normalize_concept_name(name: str) -> str:
    """Normalize concept name while retaining readable casing."""
    cleaned = _CLEAN_PREFIX_PATTERN.sub("", name).strip()
    return cleaned if cleaned else name.strip()


def extract_concepts_and_edges(
    raw_text: str,
    chunks: Sequence[Any] | None = None,
    document_title: str | None = None,
    user_metadata: dict[str, Any] | None = None,
    known_entities: Sequence[str] | set[str] | dict[str, str] | None = None,
) -> ExtractedGraph:
    """Deterministically extracts concepts, hierarchical/relational edges, and chunk associations."""
    if _HAS_DEPTH_ENGINE:
        try:
            chunks_data = None
            if chunks is not None:
                chunks_data = [
                    c.model_dump()
                    if hasattr(c, "model_dump")
                    else dict(c)
                    if isinstance(c, dict)
                    else {
                        "content": getattr(c, "content", ""),
                        "metadata": getattr(c, "metadata", {}) or {},
                    }
                    for c in chunks
                ]
            entities_list = list(known_entities) if known_entities else None
            res = depth_engine.extract_concepts_and_edges(
                raw_text=raw_text,
                chunks=chunks_data,
                document_title=document_title,
                user_metadata=user_metadata,
                known_entities=entities_list,
            )
            return ExtractedGraph.model_validate(res)
        except Exception:
            pass

    concepts_by_key: dict[str, Concept] = {}
    edges_set: set[tuple[str, str, str]] = set()
    edges: list[ConceptEdge] = []
    chunk_links: list[ChunkConceptLink] = []

    def add_concept(name: str, c_type: str = "topic", desc: str | None = None) -> str:
        norm = _normalize_concept_name(name)
        if not norm:
            return ""
        key = norm.lower()
        if key not in concepts_by_key:
            concepts_by_key[key] = Concept(
                name=norm,
                concept_type=c_type,
                description=desc,
                metadata={"canonical_key": key},
            )
        return concepts_by_key[key].name

    def add_edge(source: str, target: str, rel: str = "relates_to", weight: float = 1.0) -> None:
        s_norm = add_concept(source)
        t_norm = add_concept(target)
        if not s_norm or not t_norm or s_norm.lower() == t_norm.lower():
            return
        edge_key = (s_norm.lower(), t_norm.lower(), rel)
        if edge_key not in edges_set:
            edges_set.add(edge_key)
            edges.append(
                ConceptEdge(
                    source_concept=s_norm,
                    target_concept=t_norm,
                    relation_type=rel,
                    weight=weight,
                )
            )

    # 1. Add document root concept if provided
    root_name = ""
    if document_title:
        root_name = add_concept(document_title, c_type="document")

    # 2. Extract heading hierarchy and structural edges
    heading_stack: list[tuple[int, str]] = []
    if root_name:
        heading_stack.append((0, root_name))

    for line in raw_text.splitlines():
        line_str = line.strip()
        match = _HEADING_PATTERN.match(line_str)
        if match:
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            concept_name = add_concept(heading_text, c_type="section")

            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()

            if heading_stack:
                parent_concept = heading_stack[-1][1]
                add_edge(parent_concept, concept_name, rel="contains", weight=1.0)
            elif root_name and concept_name != root_name:
                add_edge(root_name, concept_name, rel="contains", weight=1.0)

            heading_stack.append((level, concept_name))

        # Check for cross-references like [[Target]] on current line
        current_context = heading_stack[-1][1] if heading_stack else root_name
        for wiki_match in _WIKILINK_PATTERN.finditer(line_str):
            target_raw = wiki_match.group(1).strip()
            target_name = add_concept(target_raw, c_type="entity")
            if current_context and target_name:
                is_dependency = (
                    "depend" in line_str.lower()
                    or "require" in line_str.lower()
                    or "depend" in current_context.lower()
                    or "require" in current_context.lower()
                )
                rel = "depends_on" if is_dependency else "references"
                add_edge(current_context, target_name, rel=rel, weight=1.0)

    # 3. Match known entity mentions across text if entity catalog provided
    if known_entities:
        raw_text_lower = raw_text.lower()
        context_concept = heading_stack[-1][1] if heading_stack else root_name
        for entity in known_entities:
            ent_norm = _normalize_concept_name(entity)
            if not ent_norm or len(ent_norm) < 3:
                continue
            if root_name and ent_norm.lower() == root_name.lower():
                continue
            if ent_norm.lower() in raw_text_lower:
                target_name = add_concept(ent_norm, c_type="entity")
                if context_concept and target_name:
                    add_edge(context_concept, target_name, rel="references", weight=1.0)

    # 4. Map chunks to concepts (only if concepts were discovered)
    if concepts_by_key and chunks:
        for idx, chunk in enumerate(chunks):
            linked_for_chunk: set[str] = set()

            # Retrieve content and metadata safely
            if isinstance(chunk, dict):
                c_content = chunk.get("content", "")
                meta = chunk.get("metadata", {})
            else:
                c_content = getattr(chunk, "content", "")
                meta = getattr(chunk, "metadata", {}) or {}

            hierarchy = meta.get("hierarchy")

            if isinstance(hierarchy, list) and hierarchy:
                for h_level, h_item in enumerate(hierarchy):
                    c_name = add_concept(str(h_item), c_type="section")
                    if c_name and c_name not in linked_for_chunk:
                        linked_for_chunk.add(c_name)
                        confidence = 1.0 if h_level == len(hierarchy) - 1 else 0.8
                        chunk_links.append(
                            ChunkConceptLink(
                                chunk_index=idx,
                                concept_name=c_name,
                                confidence=confidence,
                            )
                        )
            elif root_name:
                chunk_links.append(
                    ChunkConceptLink(
                        chunk_index=idx,
                        concept_name=root_name,
                        confidence=1.0,
                    )
                )

            # Detect inline concepts in chunk content
            for wiki_match in _WIKILINK_PATTERN.finditer(c_content):
                target_raw = wiki_match.group(1).strip()
                target_name = add_concept(target_raw, c_type="entity")
                if target_name and target_name not in linked_for_chunk:
                    linked_for_chunk.add(target_name)
                    chunk_links.append(
                        ChunkConceptLink(
                            chunk_index=idx,
                            concept_name=target_name,
                            confidence=0.7,
                            metadata={"source": "inline_wikilink"},
                        )
                    )

            # Detect mentions of known entities in chunk content
            if known_entities:
                c_content_lower = c_content.lower()
                for entity in known_entities:
                    ent_norm = _normalize_concept_name(entity)
                    if not ent_norm or len(ent_norm) < 3:
                        continue
                    if root_name and ent_norm.lower() == root_name.lower():
                        continue
                    if ent_norm.lower() not in linked_for_chunk and ent_norm.lower() in c_content_lower:
                        target_name = add_concept(ent_norm, c_type="entity")
                        if target_name:
                            linked_for_chunk.add(ent_norm.lower())
                            chunk_links.append(
                                ChunkConceptLink(
                                    chunk_index=idx,
                                    concept_name=target_name,
                                    confidence=0.75,
                                    metadata={"source": "entity_mention"},
                                )
                            )

    return ExtractedGraph(
        concepts=list(concepts_by_key.values()),
        edges=edges,
        chunk_links=chunk_links,
    )
