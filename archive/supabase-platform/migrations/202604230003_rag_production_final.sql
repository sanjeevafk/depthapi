-- Migration: 202604230003_rag_production_final.sql
-- Production-grade RAG infrastructure for DepthAPI.
-- Implements Hybrid RRF, Soft Deletes, Ingestion Queue, and Partitioned Analytics.

-- ==========================================
-- 1. SETUP & EXTENSIONS
-- ==========================================
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
    CREATE TYPE ingestion_status AS ENUM ('queued', 'processing', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- ==========================================
-- 2. CORE TABLES
-- ==========================================

-- COLLECTIONS (Multi-tenant root)
CREATE TABLE IF NOT EXISTS public.knowledge_collections (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id        UUID NOT NULL REFERENCES public.api_keys(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    description       TEXT,
    metadata          JSONB DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ
);

-- DOCUMENTS (With denormalized language config for trigger performance)
CREATE TABLE IF NOT EXISTS public.knowledge_documents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id     UUID NOT NULL REFERENCES public.knowledge_collections(id) ON DELETE CASCADE,
    filename          TEXT NOT NULL,
    source_url        TEXT,
    content_hash      TEXT NOT NULL,
    language_config   TEXT NOT NULL DEFAULT 'english' 
                      CHECK (language_config IN ('simple', 'english', 'french', 'spanish', 'german', 'italian', 'portuguese', 'dutch')),
    version           INT NOT NULL DEFAULT 1,
    metadata          JSONB DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ,
    
    CONSTRAINT unique_doc_hash_per_collection UNIQUE(collection_id, content_hash)
);

-- CHUNKS (Retrieval Units)
CREATE TABLE IF NOT EXISTS public.knowledge_chunks (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id       UUID NOT NULL REFERENCES public.knowledge_documents(id) ON DELETE CASCADE,
    content           TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    embedding         VECTOR(1536), -- Standardized on 1536 (OpenAI text-embedding-3-small)
    fts_tokens        TSVECTOR,      -- Managed via trigger
    
    token_count       INT,
    chunk_order       INT NOT NULL,
    metadata          JSONB DEFAULT '{}'::jsonb,
    created_at        TIMESTAMPTZ DEFAULT now(),
    deleted_at        TIMESTAMPTZ,
    
    CONSTRAINT unique_chunk_order_per_doc UNIQUE(document_id, chunk_order),
    CONSTRAINT unique_chunk_hash_per_doc UNIQUE(document_id, content_hash)
);

-- ==========================================
-- 3. WORKER QUEUE (Safe Concurrency)
-- ==========================================
CREATE TABLE IF NOT EXISTS public.knowledge_ingestion_queue (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    api_key_id        UUID NOT NULL REFERENCES public.api_keys(id) ON DELETE CASCADE,
    document_id       UUID REFERENCES public.knowledge_documents(id) ON DELETE CASCADE,
    status            ingestion_status DEFAULT 'queued',
    retry_count       INT DEFAULT 0,
    max_retries       INT DEFAULT 3,
    worker_id         TEXT,
    locked_at         TIMESTAMPTZ,
    next_retry_at     TIMESTAMPTZ DEFAULT now(),
    last_error        TEXT,
    completed_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ DEFAULT now(),
    updated_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.knowledge_ingestion_queue
ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ;

-- ==========================================
-- 4. OBSERVABILITY (Partitioned Analytics)
-- ==========================================
CREATE TABLE IF NOT EXISTS public.knowledge_query_logs (
    id                UUID DEFAULT gen_random_uuid(),
    api_key_id        UUID NOT NULL,
    query_text        TEXT,
    latency_ms        INT,
    recall_count      INT,
    top_score         FLOAT,
    user_feedback     SMALLINT DEFAULT 0, -- -1 (bad), 0 (none), 1 (good)
    created_at        TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (id, created_at)
) PARTITION BY RANGE (created_at);

-- Initial Partition (April 2026)
CREATE TABLE IF NOT EXISTS public.knowledge_query_logs_2026_04 
    PARTITION OF public.knowledge_query_logs FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');

-- ==========================================
-- 5. PROCEDURES & SEARCH
-- ==========================================

-- Worker Dequeue: Claims a job atomically using SKIP LOCKED
CREATE OR REPLACE FUNCTION dequeue_ingestion_job(p_worker_id TEXT)
RETURNS TABLE (job_id UUID, document_id UUID, api_key_id UUID) 
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    UPDATE public.knowledge_ingestion_queue
    SET 
        status = 'processing',
        worker_id = p_worker_id,
        locked_at = now(),
        retry_count = retry_count + 1,
        updated_at = now()
    WHERE id = (
        SELECT id 
        FROM public.knowledge_ingestion_queue
        WHERE status IN ('queued', 'failed')
          AND next_retry_at <= now()
          AND retry_count < max_retries
        ORDER BY next_retry_at ASC
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id, document_id, api_key_id;
END;
$$;

-- Hybrid RRF Search: Combines Vector and Keyword retrieval
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
    SELECT kc.id, ROW_NUMBER() OVER (ORDER BY ts_rank_cd(kc.fts_tokens, websearch_to_tsquery(kd.language_config::regconfig, query_text)) DESC, kc.id) as rank
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

-- Neighbor Helper: Fetches context window for top hits
CREATE OR REPLACE FUNCTION get_neighbor_chunks(p_chunk_id UUID, p_window_size INT)
RETURNS TABLE (id UUID, content TEXT, chunk_order INT) 
LANGUAGE plpgsql AS $$
BEGIN
    RETURN QUERY
    SELECT kc.id, kc.content, kc.chunk_order
    FROM public.knowledge_chunks kc
    WHERE kc.document_id = (SELECT document_id FROM knowledge_chunks WHERE id = p_chunk_id)
      AND kc.chunk_order BETWEEN 
          (SELECT chunk_order FROM knowledge_chunks WHERE id = p_chunk_id) - p_window_size
          AND (SELECT chunk_order FROM knowledge_chunks WHERE id = p_chunk_id) + p_window_size
      AND kc.deleted_at IS NULL
    ORDER BY kc.chunk_order ASC;
END;
$$;

-- ==========================================
-- 6. TRIGGERS & INDEXES
-- ==========================================

-- FTS Trigger: Auto-populates tokens based on document language
CREATE OR REPLACE FUNCTION trg_update_fts_tokens_v4() RETURNS TRIGGER AS $$
DECLARE
    lang TEXT;
BEGIN
    SELECT language_config INTO lang FROM knowledge_documents WHERE id = NEW.document_id;
    NEW.fts_tokens = to_tsvector(COALESCE(lang, 'english')::regconfig, NEW.content);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_knowledge_chunks_fts ON knowledge_chunks;
CREATE TRIGGER trg_knowledge_chunks_fts BEFORE INSERT OR UPDATE OF content ON knowledge_chunks
FOR EACH ROW EXECUTE FUNCTION trg_update_fts_tokens_v4();

-- Standard Updated At Trigger
CREATE OR REPLACE FUNCTION update_updated_at() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_knowledge_collections_updated ON knowledge_collections;
CREATE TRIGGER trg_knowledge_collections_updated BEFORE UPDATE ON knowledge_collections FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS trg_knowledge_documents_updated ON knowledge_documents;
CREATE TRIGGER trg_knowledge_documents_updated BEFORE UPDATE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION update_updated_at();
DROP TRIGGER IF EXISTS trg_knowledge_ingestion_queue_updated ON knowledge_ingestion_queue;
CREATE TRIGGER trg_knowledge_ingestion_queue_updated BEFORE UPDATE ON knowledge_ingestion_queue FOR EACH ROW EXECUTE FUNCTION update_updated_at();

-- INDEXES
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_fts_gin ON knowledge_chunks USING gin (fts_tokens);
CREATE INDEX IF NOT EXISTS idx_chunks_doc_order ON knowledge_chunks(document_id, chunk_order);
CREATE INDEX IF NOT EXISTS idx_queue_polling ON knowledge_ingestion_queue (status, next_retry_at) WHERE status IN ('queued', 'failed');
