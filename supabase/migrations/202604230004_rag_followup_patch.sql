-- Migration: 202604230004_rag_followup_patch.sql
-- Safe follow-up patch for already-applied 202604230003.
-- Adds queue completion timestamp and upgrades hybrid_search_v4 signature/logic.

BEGIN;

-- 1) Queue completion timestamp (idempotent)
ALTER TABLE public.knowledge_ingestion_queue
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- 2) Upgrade hybrid search RPC with min_similarity + vector_similarity output
CREATE OR REPLACE FUNCTION hybrid_search_v4(
  query_text TEXT,
  query_embedding VECTOR(1536),
  target_api_key_id UUID,
  candidate_pool_size INT DEFAULT 100,
  final_count INT DEFAULT 10,
  min_similarity FLOAT DEFAULT 0.75,
  k INT DEFAULT 60
)
RETURNS TABLE (
  chunk_id UUID,
  document_id UUID,
  content TEXT,
  metadata JSONB,
  filename TEXT,
  source_url TEXT,
  chunk_order INT,
  vector_similarity FLOAT,
  rrf_score FLOAT
) LANGUAGE plpgsql AS $$
BEGIN
  RETURN QUERY
  WITH vector_ranks AS (
    SELECT
      kc.id,
      (1 - (kc.embedding <=> query_embedding))::FLOAT AS similarity,
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) AS rank
    FROM knowledge_chunks kc
    JOIN knowledge_documents kd ON kc.document_id = kd.id
    JOIN knowledge_collections coll ON kd.collection_id = coll.id
    WHERE coll.api_key_id = target_api_key_id
      AND kc.embedding IS NOT NULL
      AND (1 - (kc.embedding <=> query_embedding)) >= min_similarity
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND coll.deleted_at IS NULL
    ORDER BY kc.embedding <=> query_embedding, kc.id
    LIMIT candidate_pool_size
  ),
  fts_ranks AS (
    SELECT
      kc.id,
      ROW_NUMBER() OVER (
        ORDER BY ts_rank_cd(
          kc.fts_tokens,
          websearch_to_tsquery(kd.language_config::regconfig, query_text)
        ) DESC, kc.id
      ) AS rank
    FROM knowledge_chunks kc
    JOIN knowledge_documents kd ON kc.document_id = kd.id
    JOIN knowledge_collections coll ON kd.collection_id = coll.id
    WHERE coll.api_key_id = target_api_key_id
      AND kc.fts_tokens @@ websearch_to_tsquery(kd.language_config::regconfig, query_text)
      AND kc.deleted_at IS NULL
      AND kd.deleted_at IS NULL
      AND coll.deleted_at IS NULL
    ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery(kd.language_config::regconfig, query_text)) DESC, kc.id
    LIMIT candidate_pool_size
  )
  SELECT
    kc.id,
    kc.document_id,
    kc.content,
    kc.metadata,
    kd.filename,
    kd.source_url,
    kc.chunk_order,
    vr.similarity AS vector_similarity,
    (
      COALESCE(1.0 / (k + vr.rank), 0.0) +
      COALESCE(1.0 / (k + fr.rank), 0.0)
    )::FLOAT AS rrf_score
  FROM knowledge_chunks kc
  JOIN knowledge_documents kd ON kc.document_id = kd.id
  LEFT JOIN vector_ranks vr ON kc.id = vr.id
  LEFT JOIN fts_ranks fr ON kc.id = fr.id
  WHERE (vr.id IS NOT NULL OR fr.id IS NOT NULL)
  ORDER BY rrf_score DESC
  LIMIT final_count;
END;
$$;

COMMIT;

