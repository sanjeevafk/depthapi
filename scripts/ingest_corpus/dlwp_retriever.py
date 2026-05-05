"""
dlwp_retriever.py — Hybrid RAG retriever for DLWP corpus (§ 6 of indexing strategy)

Implements dynamic Reciprocal Rank Fusion with:
  - Query mode detection: 'code' vs 'conceptual'
  - Dual tsvector search: 'simple' (exact identifiers) + 'english' (stemmed)
  - Dense vector search via BGE-M3
  - Post-retrieval overlap deduplication using prev/next chunk UUIDs

Usage:
    from scripts.ingest_corpus.dlwp_retriever import DLWPRetriever

    retriever = DLWPRetriever(supabase_client, embed_service)
    results = await retriever.search("How does Conv2D handle padding?")
"""

from __future__ import annotations

import re
from typing import Any


# ─── Query mode detection ─────────────────────────────────────────────────────
_CODE_PATTERN = re.compile(
    r"""
    \w+\(\w*\)              |   # function/method calls: fit(), Conv2D()
    \w+\.\w+                |   # attribute access: model.layers
    _\w+                    |   # snake_case identifiers: binary_crossentropy
    [A-Z][a-z]+[A-Z]\w*    |   # CamelCase class names: ModelCheckpoint
    `[^`]+`                 |   # inline backtick literals
    import\s+\w+            |   # import statements
    from\s+\w+              |   # from-import
    \b(def|class|return|yield|lambda|await|async)\b  # Python keywords
    """,
    re.VERBOSE,
)

def detect_query_mode(query: str) -> str:
    """
    Returns 'code' if query looks code-heavy, else 'conceptual'.
    Matches § 6C: regex \\w+\\(\\w*\\) or contains _.
    """
    matches = _CODE_PATTERN.findall(query)
    if len(matches) >= 2 or "_" in query:
        return "code"
    return "conceptual"


# ─── RRF k-values per mode (§ 6C) ────────────────────────────────────────────
RRF_PARAMS: dict[str, dict[str, int]] = {
    "conceptual": {
        "k_dense":          30,   # low k = high weight for dense
        "k_sparse_simple":  60,
        "k_sparse_english": 40,
    },
    "code": {
        "k_dense":          60,
        "k_sparse_simple":  30,   # low k = high weight for simple
        "k_sparse_english": 60,
    },
}


# ─── Post-retrieval deduplication (§ 6D) ─────────────────────────────────────
def deduplicate_chunks(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Collapse adjacent overlapping chunks using prev/next UUID linkage.
    When two consecutive results share a prev/next relationship,
    keep only the higher-ranked one.
    """
    if not chunks:
        return chunks

    seen_ids: set[str] = set()
    deduped: list[dict[str, Any]] = []

    for chunk in chunks:
        chunk_id = str(chunk.get("chunk_id", ""))
        meta = chunk.get("metadata") or {}

        prev_id = str(meta.get("prev_chunk_id", ""))
        next_id = str(meta.get("next_chunk_id", ""))

        # If this chunk's neighbour is already in our result set, skip it
        if prev_id in seen_ids or next_id in seen_ids:
            continue

        seen_ids.add(chunk_id)
        deduped.append(chunk)

    return deduped


# ─── Main Retriever class ─────────────────────────────────────────────────────
class DLWPRetriever:
    """
    Hybrid retriever that calls hybrid_search_v5 with dynamic RRF parameters.

    Args:
        supabase: Supabase client (from api.auth.get_supabase_admin)
        embed_service: EmbeddingService instance
        api_key_id: UUID of the knowledge collection owner
        candidate_pool: Number of candidates per retriever arm (default 100)
        final_count: Number of results to return (default 10)
        min_similarity: Minimum cosine similarity for dense retrieval (default 0.65)
    """

    def __init__(
        self,
        supabase: Any,
        embed_service: Any,
        api_key_id: str,
        candidate_pool: int = 100,
        final_count: int = 10,
        min_similarity: float = 0.65,
    ):
        self.supabase        = supabase
        self.embed_service   = embed_service
        self.api_key_id      = api_key_id
        self.candidate_pool  = candidate_pool
        self.final_count     = final_count
        self.min_similarity  = min_similarity

    async def search(
        self,
        query: str,
        chapter_filter: str | None = None,
        query_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute hybrid search with dynamic RRF.

        Args:
            query: Natural language or code query string
            chapter_filter: Optional chapter number filter (e.g., '04')
            query_mode: Force 'code' or 'conceptual' (auto-detected if None)

        Returns:
            Deduplicated, ranked list of chunk dicts
        """
        # Step 1: Detect query mode
        mode = query_mode or detect_query_mode(query)

        # Step 2: Asymmetric embedding — BGE instruction prefix for queries (§ 4)
        query_prefix = "Represent this sentence for searching relevant passages: "
        prefixed_query = query_prefix + query

        vectors = await self.embed_service.create_embeddings([prefixed_query])
        query_embedding = vectors[0]

        # Step 3: Format embedding for Postgres
        embedding_literal = "[" + ",".join(f"{float(v):.8f}" for v in query_embedding) + "]"

        # Step 4: Call hybrid_search_v5 (§ 6C, defined in migration)
        resp = await self.supabase.rpc(
            "hybrid_search_v5",
            {
                "query_text":          query,
                "query_embedding":     embedding_literal,
                "target_api_key_id":   self.api_key_id,
                "query_mode":          mode,
                "candidate_pool_size": self.candidate_pool,
                "final_count":         self.final_count * 2,  # fetch extra for dedup
                "min_similarity":      self.min_similarity,
            },
        ).execute()

        results: list[dict] = resp.data or []

        # Step 5: Apply chapter pre-filter if requested (§ 6A)
        if chapter_filter:
            results = [
                r for r in results
                if str((r.get("metadata") or {}).get("chapter_number", "")) == str(chapter_filter)
            ]

        # Step 6: Post-retrieval overlap deduplication (§ 6D)
        results = deduplicate_chunks(results)

        # Step 7: Trim to final count
        return results[:self.final_count]
