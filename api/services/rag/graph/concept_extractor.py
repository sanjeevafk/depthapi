"""Deterministic concept and lineage extractor from document structure and content."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, Field

_STOPWORDS = frozenset({
    "about", "above", "after", "again", "against", "all", "also", "among", "and",
    "any", "are", "around", "because", "been", "before", "being", "below",
    "between", "both", "but", "can", "could", "did", "does", "doing", "down",
    "during", "each", "either", "even", "few", "for", "from", "further", "had",
    "has", "have", "having", "her", "here", "hers", "herself", "him", "himself",
    "his", "how", "however", "into", "its", "itself", "just", "like", "many",
    "may", "more", "most", "much", "must", "neither", "nor", "not", "now",
    "off", "once", "only", "other", "our", "ours", "out", "over", "own",
    "same", "she", "should", "since", "some", "such", "than", "that", "the",
    "their", "theirs", "them", "themselves", "then", "there", "these", "they",
    "this", "those", "through", "thus", "too", "under", "until", "very", "was",
    "were", "what", "when", "where", "which", "while", "who", "whom", "why",
    "will", "with", "within", "without", "would", "you", "your", "yours",
})
_CAP_PHRASE_RE = re.compile(r"\b(?:[A-Z][a-zA-Z0-9-]{2,}(?:\s+[A-Z][a-zA-Z0-9-]{2,}){1,3}|[A-Z]{2,}[A-Z0-9-]*)\b")
_WORD_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9-]{2,}")


def _extract_prose_keyphrases(raw_text: str, max_terms: int = 6) -> list[str]:
    """Extract deterministic salient keyphrases/entities from unstructured prose."""
    if not raw_text or not raw_text.strip():
        return []

    scores: dict[str, float] = {}
    display_form: dict[str, str] = {}

    for match in _CAP_PHRASE_RE.finditer(raw_text):
        phrase = " ".join(match.group(0).split())
        key = phrase.lower()
        if key in _STOPWORDS or len(key) < 3:
            continue
        display_form.setdefault(key, phrase)
        scores[key] = scores.get(key, 0.0) + (3.0 if " " in key else 2.2)

    tokens = [t.lower() for t in _WORD_RE.findall(raw_text)]
    content_tokens = [t for t in tokens if len(t) >= 4 and t not in _STOPWORDS]

    bigram_counts: Counter[str] = Counter()
    for i in range(len(tokens) - 1):
        a, b = tokens[i], tokens[i + 1]
        if len(a) >= 4 and len(b) >= 4 and a not in _STOPWORDS and b not in _STOPWORDS:
            bigram_counts[f"{a} {b}"] += 1

    for bg, count in bigram_counts.most_common(12):
        if count >= 2 or bg not in scores:
            display_form.setdefault(bg, bg)
            scores[bg] = scores.get(bg, 0.0) + count * 2.0

    unigram_counts = Counter(content_tokens)
    for ug, count in unigram_counts.most_common(12):
        display_form.setdefault(ug, ug)
        scores[ug] = scores.get(ug, 0.0) + count * 1.0

    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    selected: list[str] = []
    for key, _ in ranked:
        if any(key != s.lower() and key in s.lower() for s in selected):
            continue
        selected.append(display_form[key])
        if len(selected) >= max_terms:
            break
    return selected


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


def _normalize_concept_name(name: str) -> str:
    """Normalize concept name while retaining readable casing."""
    cleaned = name.strip().lstrip("0123456789.-#*_` \t").rstrip("#*_` \t")
    return cleaned if cleaned else name.strip()


def _parse_heading(line_str: str) -> tuple[int, str] | None:
    if not line_str.startswith("#"):
        return None
    hashes = len(line_str) - len(line_str.lstrip("#"))
    if 1 <= hashes <= 6 and len(line_str) > hashes and line_str[hashes].isspace():
        text = line_str[hashes:].strip()
        return (hashes, text) if text else None
    return None


def _extract_wikilinks(line_str: str) -> list[str]:
    targets: list[str] = []
    pos = 0
    while True:
        start = line_str.find("[[", pos)
        if start == -1:
            break
        end = line_str.find("]]", start + 2)
        if end == -1:
            break
        inner = line_str[start + 2 : end]
        target = inner.split("|", 1)[0].strip()
        if target:
            targets.append(target)
        pos = end + 2
    return targets


def extract_concepts_and_edges(
    raw_text: str,
    chunks: Sequence[Any] | None = None,
    document_title: str | None = None,
    user_metadata: dict[str, Any] | None = None,
    known_entities: Sequence[str] | set[str] | dict[str, str] | None = None,
) -> ExtractedGraph:
    """Deterministically extracts concepts, hierarchical/relational edges, and chunk associations."""
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
        parsed_heading = _parse_heading(line_str)
        if parsed_heading is not None:
            level, heading_text = parsed_heading
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
        for target_raw in _extract_wikilinks(line_str):
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

    # 3b. Extract salient keyphrases when document lacks explicit Markdown/wikilink structure
    prose_keyphrases: list[str] = []
    non_root_concepts = len(concepts_by_key) - (1 if root_name else 0)
    if non_root_concepts == 0 and raw_text.strip():
        prose_keyphrases = _extract_prose_keyphrases(raw_text, max_terms=6)
        added_phrases: list[str] = []
        for phrase in prose_keyphrases:
            c_name = add_concept(phrase, c_type="keyphrase")
            if not c_name:
                continue
            added_phrases.append(c_name)
            if root_name and c_name.lower() != root_name.lower():
                add_edge(root_name, c_name, rel="mentions", weight=0.7)
        for i in range(len(added_phrases)):
            for j in range(i + 1, min(i + 3, len(added_phrases))):
                add_edge(added_phrases[i], added_phrases[j], rel="co_occurs", weight=0.5)

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
            for target_raw in _extract_wikilinks(c_content):
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

            if prose_keyphrases:
                c_content_lower = c_content.lower()
                for phrase in prose_keyphrases:
                    p_key = phrase.lower()
                    if p_key not in linked_for_chunk and p_key in c_content_lower:
                        linked_for_chunk.add(p_key)
                        chunk_links.append(
                            ChunkConceptLink(
                                chunk_index=idx,
                                concept_name=phrase,
                                confidence=0.6,
                                metadata={"source": "prose_keyphrase"},
                            )
                        )

    return ExtractedGraph(
        concepts=list(concepts_by_key.values()),
        edges=edges,
        chunk_links=chunk_links,
    )
