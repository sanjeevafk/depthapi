-- Align RAG vector dimensions from 1536 to 768 for lower-cost embeddings.
-- Safe to run once after confirming all embedding writers are configured to 768.

BEGIN;

-- 1) Drop dependent search functions first.
DROP FUNCTION IF EXISTS public.hybrid_search_v4(TEXT, VECTOR(1536), UUID, INT, INT, FLOAT, INT);
DROP FUNCTION IF EXISTS public.hybrid_search_v4(TEXT, VECTOR(768), UUID, INT, INT, FLOAT, INT);

-- 2) Change column type.
ALTER TABLE public.knowledge_chunks
  ALTER COLUMN embedding TYPE VECTOR(768);

-- 3) Recreate ANN index for updated vector type.
DROP INDEX IF EXISTS public.idx_chunks_embedding_hnsw;
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
  ON public.knowledge_chunks USING hnsw (embedding vector_cosine_ops);

-- 4) Recreate search function with VECTOR(768).
CREATE OR REPLACE FUNCTION public.hybrid_search_v4(
  query_text TEXT,
  query_embedding VECTOR(768),
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
      ROW_NUMBER() OVER (ORDER BY kc.embedding <=> query_embedding, kc.id) as rank
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
    SELECT kc.id, ROW_NUMBER() OVER (
      ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery(kd.language_config::regconfig, query_text)) DESC, kc.id
    ) as rank
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
    kc.id, kc.document_id, kc.content, kc.metadata,
    kd.filename, kd.source_url, kc.chunk_order, vr.similarity,
    (COALESCE(1.0/(k + vr.rank), 0.0) + COALESCE(1.0/(k + fr.rank), 0.0))::FLOAT as rrf_score
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
