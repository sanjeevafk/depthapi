from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher

from .config import DedupConfig


def _normalize(text: str) -> str:
    return " ".join(text.lower().split())


def _token_jaccard(left: str, right: str) -> float:
    left_set = set(_normalize(left).split())
    right_set = set(_normalize(right).split())
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / max(1, len(left_set | right_set))


def deduplicate_chunks(chunks: list[dict], config: DedupConfig) -> tuple[list[dict], list[dict], dict]:
    exact_seen: dict[str, dict] = {}
    semantic_groups: defaultdict[str, list[dict]] = defaultdict(list)
    kept: list[dict] = []
    removed: list[dict] = []
    duplicate_reasons = Counter()

    for chunk in chunks:
        content_hash = str(chunk.get("content_hash") or "")
        if content_hash in exact_seen:
            removed.append({**chunk, "duplicate_reason": "exact"})
            duplicate_reasons["exact"] += 1
            continue
        exact_seen[content_hash] = chunk
        semantic_key = " ".join(_normalize(chunk["content"]).split()[:20])
        semantic_groups[semantic_key].append(chunk)

    for group in semantic_groups.values():
        survivors: list[dict] = []
        for chunk in group:
            duplicate_reason = None
            for prior in survivors:
                fuzzy_ratio = SequenceMatcher(None, _normalize(chunk["content"]), _normalize(prior["content"])).ratio()
                semantic_score = _token_jaccard(chunk["content"], prior["content"])
                if fuzzy_ratio >= config.fuzzy_threshold:
                    duplicate_reason = "fuzzy"
                    break
                if semantic_score >= config.semantic_similarity_threshold:
                    duplicate_reason = "semantic"
                    break
            if duplicate_reason:
                removed.append({**chunk, "duplicate_reason": duplicate_reason})
                duplicate_reasons[duplicate_reason] += 1
            else:
                survivors.append(chunk)
        kept.extend(survivors)

    source_counts = Counter(chunk.get("source", "unknown") for chunk in chunks)
    removed_source_counts = Counter(chunk.get("source", "unknown") for chunk in removed)
    source_redundancy = {
        source: round(removed_source_counts[source] / count, 4)
        for source, count in source_counts.items()
    }

    stats = {
        "input_chunks": len(chunks),
        "kept_chunks": len(kept),
        "removed_chunks": len(removed),
        "duplicate_ratio": round(len(removed) / max(1, len(chunks)), 4),
        "source_redundancy": source_redundancy,
        "semantic_overlap_percent": round(
            100.0 * duplicate_reasons["semantic"] / max(1, len(chunks)),
            2,
        ),
        "duplicate_reasons": dict(duplicate_reasons),
    }
    kept.sort(key=lambda row: row["chunk_id"])
    removed.sort(key=lambda row: row["chunk_id"])
    return kept, removed, stats
