"""Intelligent query router for graph-assisted retrieval."""
from __future__ import annotations

import re

# Relationship, dependency, hierarchy, and architectural signals
_RELATIONAL_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b(depend(s|ency|encies|ent)?|depends\s+on)\b", re.IGNORECASE),
    re.compile(r"\b(lineage|provenance|origin(s)?)\b", re.IGNORECASE),
    re.compile(r"\b(connect(ed|ion|ions|s)?|link(ed|s)?|relations?(hip|hips)?)\b", re.IGNORECASE),
    re.compile(r"\b(upstream|downstream|caller(s)?|callee(s)?)\b", re.IGNORECASE),
    re.compile(r"\b(impact(s|ed)?|blast\s+radius|break(s)?\s+if)\b", re.IGNORECASE),
    re.compile(r"\b(hierarch(y|ical)|parent|child(ren)?|subclass(es)?|superclass(es)?)\b", re.IGNORECASE),
    re.compile(r"\b(architect(ure)?|flow|pipeline|interact(s|ion|ions)?)\b", re.IGNORECASE),
    re.compile(r"\bhow\s+does\s+.+\s+(relate|connect|interact|affect|interface)", re.IGNORECASE),
    re.compile(r"\b(what|who|which)\s+(uses|calls|imports|requires|references)\b", re.IGNORECASE),
    re.compile(r"\b(between\s+.+\s+and\s+.+)\b", re.IGNORECASE),
    re.compile(r"\b(trace|path\s+between|graph\s+of)\b", re.IGNORECASE),
]


def detect_graph_hops(query: str) -> int:
    """Deterministically detects if a query benefits from graph traversal.

    Returns:
        1 if relational, dependency, or structural intent is detected.
        0 for standard factual or entity lookup queries (to prevent topic drift).
    """
    if not query:
        return 0
    query_clean = query.strip()
    for pattern in _RELATIONAL_PATTERNS:
        if pattern.search(query_clean):
            return 1
    return 0
