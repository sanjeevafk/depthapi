-- Migration: 202605160005_add_fts_simple_index.sql
-- Optimizes code-identifier searches by adding GIN index for the simple tsvector.

BEGIN;

CREATE INDEX IF NOT EXISTS idx_chunks_fts_simple 
ON public.knowledge_chunks 
USING GIN (fts_tokens_simple);

-- Add comment for documentation
COMMENT ON INDEX idx_chunks_fts_simple IS 'Optimizes code-specific hybrid search using the simple tsvector configuration.';

COMMIT;
