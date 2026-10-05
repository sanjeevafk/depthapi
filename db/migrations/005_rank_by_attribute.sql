-- Migration 005: Rank-by-attribute hybrid search.
--
-- Adds hybrid_search_v6, which blends two numeric quality signals into the
-- BM25 first-stage scoring expression before RRF fusion — the pattern described
-- in turbopuffer's "Mixing numeric attributes into text search" (Apr 2026).
--
-- Signals:
--   recency_weight  — sigmoid decay on knowledge_documents.created_at.
--                     decay(age_days) = midpoint / (age_days + midpoint)
--                     midpoint = 30 days  →  score halves at 30 days, ~0 at 120 days
--   quality_weight  — direct use of knowledge_chunks.quality_score [0.0, 1.0].
--
-- Both weights default to 0.0, making v6 identical to v5 for callers that do
-- not pass them. Use non-zero values only after running recall evals.
--
-- Design choices:
--   * The blending happens at the lex_rank CTE level (before RRF), so the FULL
--     OUTER JOIN sees a quality-aware lexical ordering — matching turbopuffer's
--     approach of incorporating attributes into the first-stage scorer, not the
--     reranker.
--   * We use Robertson's sigmoid form (midpoint / (x + midpoint)) for recency,
--     which is bounded [0, 1] and avoids the score explosion that a raw inverse
--     would cause on very fresh documents.
--   * quality_score is already guaranteed [0, 1] by the ingestion pipeline, so
--     no normalisation is needed.

-- Add quality_score column to knowledge_chunks if not yet present.
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS quality_score float;
CREATE INDEX IF NOT EXISTS knowledge_chunks_quality_idx
    ON knowledge_chunks (quality_score)
    WHERE quality_score IS NOT NULL;

CREATE OR REPLACE FUNCTION hybrid_search_v6(
    query_text           text,
    query_embedding      vector(768),
    collection_filter    uuid    DEFAULT NULL,
    api_key_filter       uuid    DEFAULT NULL,
    recency_weight       float   DEFAULT 0.0,
    quality_weight       float   DEFAULT 0.0
) RETURNS TABLE(
    content     text,
    document_id uuid,
    source_url  text,
    score       real
) LANGUAGE sql STABLE AS $$
WITH dense_matches AS (
    SELECT
        c.id,
        c.content,
        c.document_id,
        d.source_url,
        ROW_NUMBER() OVER (ORDER BY c.embedding <=> query_embedding ASC) AS dense_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL
      AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.embedding IS NOT NULL
    LIMIT 40
),
lexical_matches AS (
    SELECT
        c.id,
        c.content,
        c.document_id,
        d.source_url,
        -- Attribute-blended BM25 score (turbopuffer rank-by-attribute pattern).
        -- Base: ts_rank over fts_tokens.
        -- +recency: Robertson sigmoid decay, midpoint = 30 days.
        -- +quality: direct quality_score contribution.
        -- The composite expression is used for ordering, so higher = more relevant.
        (
            ts_rank(c.fts_tokens, plainto_tsquery('english', query_text))
            + recency_weight * (30.0 / (
                GREATEST(EXTRACT(EPOCH FROM NOW() - d.created_at) / 86400.0, 0.0) + 30.0
              ))::float
            + quality_weight * COALESCE(c.quality_score, 0.0)
        ) AS blended_score,
        ROW_NUMBER() OVER (
            ORDER BY (
                ts_rank(c.fts_tokens, plainto_tsquery('english', query_text))
                + recency_weight * (30.0 / (
                    GREATEST(EXTRACT(EPOCH FROM NOW() - d.created_at) / 86400.0, 0.0) + 30.0
                  ))::float
                + quality_weight * COALESCE(c.quality_score, 0.0)
            ) DESC
        ) AS lex_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL
      AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.fts_tokens @@ plainto_tsquery('english', query_text)
    LIMIT 40
),
fused AS (
    SELECT
        COALESCE(d.id, l.id)                         AS id,
        COALESCE(d.content, l.content)               AS content,
        COALESCE(d.document_id, l.document_id)       AS document_id,
        COALESCE(d.source_url, l.source_url)         AS source_url,
        (
            COALESCE(1.0 / (60.0 + d.dense_rank), 0.0)
            + COALESCE(1.0 / (60.0 + l.lex_rank), 0.0)
        )::real AS score
    FROM dense_matches d
    FULL OUTER JOIN lexical_matches l ON d.id = l.id
)
SELECT content, document_id, source_url, score
FROM fused
ORDER BY score DESC
LIMIT 10
$$;

-- Trusted variant mirrors v5's trusted wrapper pattern.
CREATE OR REPLACE FUNCTION hybrid_search_trusted_v6(
    query_text           text,
    query_embedding      vector(768),
    collection_filter    uuid    DEFAULT NULL,
    api_key_filter       uuid    DEFAULT NULL,
    recency_weight       float   DEFAULT 0.0,
    quality_weight       float   DEFAULT 0.0
) RETURNS TABLE(
    content     text,
    document_id uuid,
    source_url  text,
    score       real
) LANGUAGE sql STABLE AS $$
    SELECT * FROM hybrid_search_v6(
        query_text, query_embedding,
        collection_filter, api_key_filter,
        recency_weight, quality_weight
    )
$$;
