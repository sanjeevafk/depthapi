-- =============================================================================
-- MIGRATION: hybrid_search_v5_final
-- Amalgamates: 202605050001 + 202605120001 + 202605160001-006 + 202605170001-002
-- This is the canonical, production-grade hybrid search implementation.
--
-- Key capabilities:
--   - Dual-tsvector (english + simple) for conceptual and code/API queries
--   - Dynamic RRF k-weighting per query_mode ('conceptual' | 'code' | 'technical')
--   - Collection scoping via target_collection_id
--   - Trusted corpus (no api_key scoping) variant: hybrid_search_trusted_v5
--   - ID-ambiguity fix: all CTEs use aliased column names (candidate_id)
--   - statement_timeout = 60s guard on both functions
--   - get_neighbor_chunks: ID-ambiguity fix via aliased anchor subqueries
--   - FTS indexes: both GIN(fts_tokens) and GIN(fts_tokens_simple)
--   - fts_simple index: 202605160006
-- =============================================================================

BEGIN;

-- ============================================================
-- 1. SCHEMA ADDITIONS (idempotent)
-- ============================================================

-- Dual tsvector column for code-aware exact-match sparse retrieval
ALTER TABLE public.knowledge_chunks
  ADD COLUMN IF NOT EXISTS fts_tokens_simple TSVECTOR;

-- Backfill simple tsvector from existing content
UPDATE public.knowledge_chunks
SET fts_tokens_simple = to_tsvector('simple', content)
WHERE fts_tokens_simple IS NULL
  AND content IS NOT NULL;

-- ============================================================
-- 2. TRIGGERS: auto-populate both tsvector columns on write
-- ============================================================

CREATE OR REPLACE FUNCTION public.update_chunk_fts_tokens()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  -- Stemmed English: good for conceptual/semantic queries
  NEW.fts_tokens        := to_tsvector('english', COALESCE(NEW.content, ''));
  -- Simple (no stemming): preserves API names, function names, identifiers
  NEW.fts_tokens_simple := to_tsvector('simple',  COALESCE(NEW.content, ''));
  RETURN NEW;
END;
$$;

-- Drop old single-column trigger variants
DROP TRIGGER IF EXISTS tsvector_update_trigger           ON public.knowledge_chunks;
DROP TRIGGER IF EXISTS update_chunk_fts_tokens_trigger   ON public.knowledge_chunks;
DROP TRIGGER IF EXISTS trg_knowledge_chunks_fts          ON public.knowledge_chunks;

CREATE TRIGGER update_chunk_fts_tokens_trigger
  BEFORE INSERT OR UPDATE OF content
  ON public.knowledge_chunks
  FOR EACH ROW EXECUTE FUNCTION public.update_chunk_fts_tokens();

-- ============================================================
-- 3. INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_chunks_fts_gin
  ON public.knowledge_chunks USING gin (fts_tokens);

CREATE INDEX IF NOT EXISTS idx_chunks_fts_simple_gin
  ON public.knowledge_chunks USING gin (fts_tokens_simple);

-- Uses 'simple' dictionary to support stemming-free lookups
CREATE INDEX IF NOT EXISTS idx_chunks_fts_simple_dict
  ON public.knowledge_chunks USING gin (to_tsvector('simple', content));

CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin
  ON public.knowledge_chunks USING gin (metadata jsonb_path_ops);

-- ============================================================
-- 4. DROP LEGACY SEARCH FUNCTIONS
-- ============================================================

DROP FUNCTION IF EXISTS public.hybrid_search_v4(TEXT, VECTOR(1536), UUID, INT, INT, FLOAT, INT);
DROP FUNCTION IF EXISTS public.hybrid_search_v4(TEXT, VECTOR(768),  UUID, INT, INT, FLOAT, INT);
DROP FUNCTION IF EXISTS public.hybrid_search_trusted_v4(TEXT, VECTOR(768), INT, INT, FLOAT, INT);
DROP FUNCTION IF EXISTS public.hybrid_search_trusted_v4(TEXT, VECTOR(768), INT, FLOAT);
DROP FUNCTION IF EXISTS public.hybrid_search_v5(TEXT, VECTOR(768), UUID, TEXT, INT, INT, FLOAT);
DROP FUNCTION IF EXISTS public.hybrid_search_v5(TEXT, VECTOR(768), UUID, UUID, TEXT, INT, INT, FLOAT);
DROP FUNCTION IF EXISTS public.hybrid_search_trusted_v5(TEXT, VECTOR(768), TEXT, INT, INT, FLOAT);

-- ============================================================
-- 5. hybrid_search_v5 — api-key scoped
-- ============================================================
-- Searches within collections owned by target_api_key_id.
-- Optionally narrows to a single target_collection_id.
-- query_mode controls RRF k-weights:
--   'code' / 'technical' → favours sparse_simple (exact identifiers)
--   'conceptual'         → favours dense vector + sparse_english

CREATE OR REPLACE FUNCTION public.hybrid_search_v5(
  query_text           text,
  query_embedding      vector,
  target_api_key_id    uuid,
  query_mode           text              DEFAULT 'conceptual',
  candidate_pool_size  integer           DEFAULT 100,
  final_count          integer           DEFAULT 10,
  min_similarity       double precision  DEFAULT 0.65,
  target_collection_id uuid              DEFAULT NULL
)
RETURNS TABLE(
  chunk_id         uuid,
  document_id      uuid,
  content          text,
  metadata         jsonb,
  filename         text,
  source_url       text,
  chunk_order      integer,
  vector_similarity double precision,
  rrf_score        double precision,
  match_source     text
)
LANGUAGE plpgsql
SET statement_timeout TO '60s'
AS $function$
DECLARE
  k_dense          INT;
  k_sparse_simple  INT;
  k_sparse_english INT;
  cleaned_query    TEXT;
  cleaned_simple   TEXT;
  v_collection_ids UUID[];
BEGIN
  -- Resolve allowed collection IDs upfront to avoid repeated JOIN/filter
  SELECT array_agg(kc.id) INTO v_collection_ids
  FROM public.knowledge_collections kc
  WHERE kc.api_key_id = target_api_key_id
    AND (target_collection_id IS NULL OR kc.id = target_collection_id)
    AND kc.deleted_at IS NULL;

  IF v_collection_ids IS NULL OR array_length(v_collection_ids, 1) = 0 THEN
    RETURN;
  END IF;

  cleaned_query  := trim(regexp_replace(
    regexp_replace(COALESCE(query_text, ''), '[^[:alnum:][:space:]]', ' ', 'g'),
    '\s+', ' ', 'g'));
  cleaned_simple := regexp_replace(cleaned_query, '\s+', ' & ', 'g');

  IF query_mode IN ('code', 'technical') THEN
    k_dense := 100;  k_sparse_simple := 5;   k_sparse_english := 20;
  ELSE
    k_dense := 5;    k_sparse_simple := 100;  k_sparse_english := 60;
  END IF;

  RETURN QUERY
  WITH vector_candidates AS (
    SELECT
      kc.id AS candidate_id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd ON kc.document_id = kd.id
    WHERE kd.collection_id = ANY(v_collection_ids)
      AND kc.embedding IS NOT NULL
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding
    LIMIT candidate_pool_size
  ),
  vector_ranks AS (
    SELECT * FROM vector_candidates WHERE similarity >= min_similarity
  ),
  fts_english_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd ON kc.document_id = kd.id
    WHERE kd.collection_id = ANY(v_collection_ids)
      AND cleaned_query <> ''
      AND kc.fts_tokens @@ websearch_to_tsquery('english', cleaned_query)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC
    LIMIT candidate_pool_size
  ),
  fts_simple_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd ON kc.document_id = kd.id
    WHERE kd.collection_id = ANY(v_collection_ids)
      AND kc.fts_tokens_simple IS NOT NULL
      AND cleaned_simple <> ''
      AND kc.fts_tokens_simple @@ to_tsquery('simple', cleaned_simple)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC
    LIMIT candidate_pool_size
  ),
  all_candidates AS (
    SELECT candidate_id FROM vector_ranks
    UNION SELECT candidate_id FROM fts_english_ranks
    UNION SELECT candidate_id FROM fts_simple_ranks
  ),
  rrf_scores AS (
    SELECT
      ac.candidate_id,
      (
        COALESCE(1.0 / (k_dense          + vr.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_english + fer.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_simple  + fsr.rank), 0.0)
      )::FLOAT AS score,
      COALESCE(vr.similarity, 0.0) AS vsim,
      CASE
        WHEN vr.candidate_id  IS NOT NULL AND (fer.candidate_id IS NOT NULL OR fsr.candidate_id IS NOT NULL) THEN 'hybrid'
        WHEN vr.candidate_id  IS NOT NULL THEN 'dense'
        WHEN fsr.candidate_id IS NOT NULL THEN 'sparse_simple'
        ELSE 'sparse_english'
      END AS match_src
    FROM all_candidates ac
    LEFT JOIN vector_ranks      vr  ON ac.candidate_id = vr.candidate_id
    LEFT JOIN fts_english_ranks fer ON ac.candidate_id = fer.candidate_id
    LEFT JOIN fts_simple_ranks  fsr ON ac.candidate_id = fsr.candidate_id
  )
  SELECT
    kc.id, kc.document_id, kc.content, kc.metadata,
    kd.filename, kd.source_url, kc.chunk_order,
    rs.vsim, rs.score, rs.match_src
  FROM rrf_scores rs
  JOIN public.knowledge_chunks    kc ON rs.candidate_id = kc.id
  JOIN public.knowledge_documents kd ON kc.document_id  = kd.id
  ORDER BY rs.score DESC
  LIMIT final_count;
END;
$function$;

-- ============================================================
-- 6. hybrid_search_trusted_v5 — no api-key scoping
-- ============================================================
-- Searches across ALL knowledge_chunks (trusted internal corpus).
-- Used for DepthAPI's own pre-ingested knowledge base.

CREATE OR REPLACE FUNCTION public.hybrid_search_trusted_v5(
  query_text          text,
  query_embedding     vector,
  query_mode          text             DEFAULT 'conceptual',
  candidate_pool_size integer          DEFAULT 100,
  final_count         integer          DEFAULT 10,
  min_similarity      double precision DEFAULT 0.65
)
RETURNS TABLE(
  chunk_id         uuid,
  document_id      uuid,
  content          text,
  metadata         jsonb,
  filename         text,
  source_url       text,
  chunk_order      integer,
  vector_similarity double precision,
  rrf_score        double precision,
  match_source     text
)
LANGUAGE plpgsql
SET statement_timeout TO '60s'
AS $function$
DECLARE
  k_dense          INT;
  k_sparse_simple  INT;
  k_sparse_english INT;
  cleaned_query    TEXT;
  cleaned_simple   TEXT;
BEGIN
  cleaned_query  := trim(regexp_replace(
    regexp_replace(COALESCE(query_text, ''), '[^[:alnum:][:space:]]', ' ', 'g'),
    '\s+', ' ', 'g'));
  cleaned_simple := regexp_replace(cleaned_query, '\s+', ' & ', 'g');

  IF query_mode IN ('code', 'technical') THEN
    k_dense := 100;  k_sparse_simple := 5;   k_sparse_english := 20;
  ELSE
    k_dense := 5;    k_sparse_simple := 100;  k_sparse_english := 60;
  END IF;

  RETURN QUERY
  WITH vector_candidates AS (
    SELECT
      kc.id AS candidate_id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM public.knowledge_chunks kc
    WHERE kc.embedding IS NOT NULL
      AND kc.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding
    LIMIT candidate_pool_size
  ),
  vector_ranks AS (
    SELECT * FROM vector_candidates WHERE similarity >= min_similarity
  ),
  fts_english_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    WHERE cleaned_query <> ''
      AND kc.fts_tokens @@ websearch_to_tsquery('english', cleaned_query)
      AND kc.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', cleaned_query)) DESC
    LIMIT candidate_pool_size
  ),
  fts_simple_ranks AS (
    SELECT
      kc.id AS candidate_id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    WHERE kc.fts_tokens_simple IS NOT NULL
      AND cleaned_simple <> ''
      AND kc.fts_tokens_simple @@ to_tsquery('simple', cleaned_simple)
      AND kc.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', cleaned_simple)) DESC
    LIMIT candidate_pool_size
  ),
  all_candidates AS (
    SELECT candidate_id FROM vector_ranks
    UNION SELECT candidate_id FROM fts_english_ranks
    UNION SELECT candidate_id FROM fts_simple_ranks
  ),
  rrf_scores AS (
    SELECT
      ac.candidate_id,
      (
        COALESCE(1.0 / (k_dense          + vr.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_english + fer.rank), 0.0) +
        COALESCE(1.0 / (k_sparse_simple  + fsr.rank), 0.0)
      )::FLOAT AS score,
      COALESCE(vr.similarity, 0.0) AS vsim,
      CASE
        WHEN vr.candidate_id  IS NOT NULL AND (fer.candidate_id IS NOT NULL OR fsr.candidate_id IS NOT NULL) THEN 'hybrid'
        WHEN vr.candidate_id  IS NOT NULL THEN 'dense'
        WHEN fsr.candidate_id IS NOT NULL THEN 'sparse_simple'
        ELSE 'sparse_english'
      END AS match_src
    FROM all_candidates ac
    LEFT JOIN vector_ranks      vr  ON ac.candidate_id = vr.candidate_id
    LEFT JOIN fts_english_ranks fer ON ac.candidate_id = fer.candidate_id
    LEFT JOIN fts_simple_ranks  fsr ON ac.candidate_id = fsr.candidate_id
  )
  SELECT
    kc.id, kc.document_id, kc.content, kc.metadata,
    kd.filename, kd.source_url, kc.chunk_order,
    rs.vsim, rs.score, rs.match_src
  FROM rrf_scores rs
  JOIN public.knowledge_chunks    kc ON rs.candidate_id = kc.id
  JOIN public.knowledge_documents kd ON kc.document_id  = kd.id
  ORDER BY rs.score DESC
  LIMIT final_count;
END;
$function$;

-- ============================================================
-- 7. get_neighbor_chunks — context window fetcher (ID-ambiguity fixed)
-- ============================================================

CREATE OR REPLACE FUNCTION public.get_neighbor_chunks(p_chunk_id uuid, p_window_size integer)
RETURNS TABLE(id uuid, content text, chunk_order integer)
LANGUAGE plpgsql AS $function$
BEGIN
  RETURN QUERY
  SELECT kc.id, kc.content, kc.chunk_order
  FROM public.knowledge_chunks kc
  WHERE kc.document_id = (
    SELECT anchor.document_id FROM public.knowledge_chunks anchor WHERE anchor.id = p_chunk_id
  )
    AND kc.chunk_order BETWEEN
      (SELECT anchor.chunk_order FROM public.knowledge_chunks anchor WHERE anchor.id = p_chunk_id) - p_window_size
      AND
      (SELECT anchor.chunk_order FROM public.knowledge_chunks anchor WHERE anchor.id = p_chunk_id) + p_window_size
    AND kc.deleted_at IS NULL
  ORDER BY kc.chunk_order ASC;
END;
$function$;

COMMIT;
