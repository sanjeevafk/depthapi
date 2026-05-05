-- Migration: 202605050001_dlwp_dual_tsvector_and_metadata.sql
-- Implements the DLWP indexing strategy:
-- 1. Add fts_tokens_simple (tsvector) for code-aware exact matching
-- 2. Add chapter_number, chapter_title, has_code, content_type, indexed_at to metadata pattern
-- 3. GIN index on fts_tokens_simple
-- 4. Upgrade hybrid_search to dynamic RRF with dual tsvector

BEGIN;

-- ============================================================
-- 1. ADD DUAL TSVECTOR COLUMN FOR CODE-AWARE SPARSE RETRIEVAL
-- ============================================================

-- The existing fts_tokens column uses the document's language_config (usually 'english').
-- We add a second column using the 'simple' dictionary which does NO stemming/stopwords,
-- preserving exact function names and API identifiers like Conv2D, binary_crossentropy, etc.

ALTER TABLE public.knowledge_chunks
  ADD COLUMN IF NOT EXISTS fts_tokens_simple TSVECTOR;

-- Populate fts_tokens_simple from existing content
UPDATE public.knowledge_chunks
SET fts_tokens_simple = to_tsvector('simple', content)
WHERE fts_tokens_simple IS NULL
  AND content IS NOT NULL;

-- GIN index for fast sparse retrieval on the simple tsvector
CREATE INDEX IF NOT EXISTS idx_chunks_fts_simple_gin
  ON public.knowledge_chunks USING gin (fts_tokens_simple);

-- Also ensure GIN index exists on the original fts_tokens (english)
CREATE INDEX IF NOT EXISTS idx_chunks_fts_english_gin
  ON public.knowledge_chunks USING gin (fts_tokens);

-- ============================================================
-- 2. TRIGGER: Auto-populate BOTH tsvector columns on insert/update
-- ============================================================

CREATE OR REPLACE FUNCTION public.update_chunk_fts_tokens()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  -- English-stemmed: good for conceptual queries
  NEW.fts_tokens := to_tsvector('english', COALESCE(NEW.content, ''));
  -- Simple (no stemming): good for exact API/code identifier matching
  NEW.fts_tokens_simple := to_tsvector('simple', COALESCE(NEW.content, ''));
  RETURN NEW;
END;
$$;

-- Drop old trigger if it existed (single-column version)
DROP TRIGGER IF EXISTS tsvector_update_trigger ON public.knowledge_chunks;
DROP TRIGGER IF EXISTS update_chunk_fts_tokens_trigger ON public.knowledge_chunks;

-- Create new trigger that populates both columns
CREATE TRIGGER update_chunk_fts_tokens_trigger
  BEFORE INSERT OR UPDATE OF content
  ON public.knowledge_chunks
  FOR EACH ROW
  EXECUTE FUNCTION public.update_chunk_fts_tokens();

-- ============================================================
-- 3. GIN INDEX ON METADATA JSONB for fast chapter/tag filtering
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_chunks_metadata_gin
  ON public.knowledge_chunks USING gin (metadata jsonb_path_ops);

-- ============================================================
-- 4. HYBRID SEARCH v5: Dynamic RRF with dual tsvector
-- ============================================================
-- Supports two RRF modes:
--   query_mode = 'conceptual':  k_dense=30,  k_simple=60, k_english=40
--   query_mode = 'code':        k_dense=60,  k_simple=30, k_english=60

DROP FUNCTION IF EXISTS public.hybrid_search_v5(TEXT, VECTOR(768), UUID, TEXT, INT, INT, FLOAT);

CREATE OR REPLACE FUNCTION public.hybrid_search_v5(
  query_text           TEXT,
  query_embedding      VECTOR(768),
  target_api_key_id    UUID,
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
BEGIN
  -- Dynamic RRF k-values based on query mode
  IF query_mode = 'code' THEN
    k_dense          := 60;
    k_sparse_simple  := 30;  -- Low k = high weight for code queries
    k_sparse_english := 60;
  ELSE
    -- Default: conceptual
    k_dense          := 30;  -- Low k = high weight for conceptual queries
    k_sparse_simple  := 60;
    k_sparse_english := 40;
  END IF;

  RETURN QUERY
  WITH vector_ranks AS (
    SELECT
      kc.id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd   ON kc.document_id = kd.id
    JOIN public.knowledge_collections c  ON kd.collection_id = c.id
    WHERE c.api_key_id = target_api_key_id
      AND kc.embedding IS NOT NULL
      AND (1 - (kc.embedding <=> query_embedding)) >= min_similarity
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND c.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding
    LIMIT candidate_pool_size
  ),
  fts_english_ranks AS (
    SELECT
      kc.id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', query_text)) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd   ON kc.document_id = kd.id
    JOIN public.knowledge_collections c  ON kd.collection_id = c.id
    WHERE c.api_key_id = target_api_key_id
      AND kc.fts_tokens @@ websearch_to_tsquery('english', query_text)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND c.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery('english', query_text)) DESC
    LIMIT candidate_pool_size
  ),
  fts_simple_ranks AS (
    SELECT
      kc.id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', regexp_replace(query_text, '\s+', ' & ', 'g'))) DESC, kc.id
      ) AS rank
    FROM public.knowledge_chunks kc
    JOIN public.knowledge_documents kd   ON kc.document_id = kd.id
    JOIN public.knowledge_collections c  ON kd.collection_id = c.id
    WHERE c.api_key_id = target_api_key_id
      AND kc.fts_tokens_simple IS NOT NULL
      AND kc.fts_tokens_simple @@ to_tsquery('simple', regexp_replace(query_text, '\s+', ' & ', 'g'))
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND c.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens_simple, to_tsquery('simple', regexp_replace(query_text, '\s+', ' & ', 'g'))) DESC
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
