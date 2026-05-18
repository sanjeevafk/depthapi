-- Migration: 202605160001_add_collection_filter_to_hybrid_search_v5.sql
-- Updates hybrid_search_v5 to support targeted collection filtering.

BEGIN;

DROP FUNCTION IF EXISTS public.hybrid_search_v5(TEXT, VECTOR(768), UUID, TEXT, INT, INT, FLOAT);

CREATE OR REPLACE FUNCTION public.hybrid_search_v5(
  query_text           TEXT,
  query_embedding      VECTOR(768),
  target_api_key_id    UUID,
  target_collection_id UUID    DEFAULT NULL,  -- New optional filter
  query_mode           TEXT    DEFAULT 'conceptual', -- 'conceptual' | 'code'
  candidate_pool_size  INT     DEFAULT 100,
  final_count          INT     DEFAULT 10,
  min_similarity       FLOAT   DEFAULT 0.65
)
RETURNS TABLE (
  chunk_id         UUID,
  document_id      UUID,
  content          TEXT,
  metadata         JSONB,
  filename         TEXT,
  source_url       TEXT,
  chunk_order      INT,
  vector_similarity FLOAT,
  rrf_score        FLOAT,
  match_source     TEXT    -- 'dense' | 'sparse_english' | 'sparse_simple' | 'hybrid'
) LANGUAGE plpgsql AS $$
DECLARE
  k_dense          INT;
  k_sparse_simple  INT;
  k_sparse_english INT;
  cleaned_query    TEXT;
  cleaned_simple   TEXT;
BEGIN
  cleaned_query := regexp_replace(COALESCE(query_text, ''), '[^[:alnum:][:space:]]', ' ', 'g');
  cleaned_query := trim(regexp_replace(cleaned_query, '\s+', ' ', 'g'));
  cleaned_simple := regexp_replace(cleaned_query, '\s+', ' & ', 'g');

  -- Dynamic RRF k-values based on query mode
  IF query_mode = 'code' OR query_mode = 'technical' THEN
    k_dense          := 60;
    k_sparse_simple  := 30;  -- Low k = high weight for code/technical queries
    k_sparse_english := 60;
  ELSE
    -- Default: conceptual
    k_dense          := 30;  -- Low k = high weight for conceptual queries
    k_sparse_simple  := 60;
    k_sparse_english := 40;
  END IF;

  RETURN QUERY
  WITH vector_candidates AS (
    SELECT
      kc.id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd   ON kc.document_id = kd.id
    JOIN public.knowledge_collections c  ON kd.collection_id = c.id
    WHERE c.api_key_id = target_api_key_id
      AND (target_collection_id IS NULL OR c.id = target_collection_id) -- Filter applied
      AND kc.embedding IS NOT NULL
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND c.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding
    LIMIT candidate_pool_size
  ),
  vector_ranks AS (
    SELECT *
    FROM vector_candidates
    WHERE similarity >= min_similarity
  ),
  fts_english_ranks AS (
    SELECT
      kc.id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd   ON kc.document_id = kd.id
    JOIN public.knowledge_collections c  ON kd.collection_id = c.id
    WHERE c.api_key_id = target_api_key_id
      AND (target_collection_id IS NULL OR c.id = target_collection_id) -- Filter applied
      AND cleaned_query <> ''
      AND kc.fts_tokens @@ websearch_to_tsquery('english', cleaned_query)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND c.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC
    LIMIT candidate_pool_size
  ),
  fts_simple_ranks AS (
    SELECT
      kc.id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd   ON kc.document_id = kd.id
    JOIN public.knowledge_collections c  ON kd.collection_id = c.id
    WHERE c.api_key_id = target_api_key_id
      AND (target_collection_id IS NULL OR c.id = target_collection_id) -- Filter applied
      AND kc.fts_tokens_simple IS NOT NULL
      AND cleaned_simple <> ''
      AND kc.fts_tokens_simple @@ to_tsquery('simple', cleaned_simple)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND c.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC
    LIMIT candidate_pool_size
  ),
  all_candidates AS (
    SELECT id FROM vector_ranks
    UNION
    SELECT id FROM fts_english_ranks
    UNION
    SELECT id FROM fts_simple_ranks
  ),
  rrf_scores AS (
    SELECT
      ac.id,
      (
        COALESCE(1.0 / (k_dense          + vr.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_english + fer.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_simple  + fsr.rank), 0.0)
      )::FLOAT AS score,
      COALESCE(vr.similarity, 0.0) AS vsim,
      CASE
        WHEN vr.id IS NOT NULL AND (fer.id IS NOT NULL OR fsr.id IS NOT NULL) THEN 'hybrid'
        WHEN vr.id IS NOT NULL  THEN 'dense'
        WHEN fsr.id IS NOT NULL THEN 'sparse_simple'
        ELSE 'sparse_english'
      END AS match_src
    FROM all_candidates ac
    LEFT JOIN vector_ranks        vr  ON ac.id = vr.id
    LEFT JOIN fts_english_ranks   fer ON ac.id = fer.id
    LEFT JOIN fts_simple_ranks    fsr ON ac.id = fsr.id
  )
  SELECT
    kc.id, kc.document_id, kc.content, kc.metadata,
    kd.filename, kd.source_url, kc.chunk_order,
    rs.vsim, rs.score, rs.match_src
  FROM rrf_scores rs
  JOIN public.knowledge_chunks   kc ON rs.id = kc.id
  JOIN public.knowledge_documents kd ON kc.document_id = kd.id
  ORDER BY rs.score DESC
  LIMIT final_count;
END;
$$;

COMMIT;
