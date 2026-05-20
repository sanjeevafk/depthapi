-- =============================================================================
-- MIGRATION: embedding_dimension_rpc_final
-- Amalgamates: 202605040002 + 202605040003 + 202605040004 + 202605040005
-- Final, correct implementations of get_embedding_dimension and
-- apply_chunk_embeddings RPCs. Previous fix iterations are superseded.
-- =============================================================================

-- Final correct dimension introspection using raw atttypmod value.
-- pgvector stores dimension as (dim + 4) in atttypmod; we return the raw
-- atttypmod so callers can compare directly against (expected_dim + 4) OR
-- use the value as-is in application-layer validation.
CREATE OR REPLACE FUNCTION public.get_embedding_dimension()
RETURNS TABLE(dimension INT)
LANGUAGE sql
SECURITY DEFINER
AS $$
  SELECT CASE
    WHEN a.atttypmod > 0 THEN a.atttypmod
    ELSE NULL
  END::int AS dimension
  FROM pg_attribute a
  WHERE a.attrelid = 'public.knowledge_chunks'::regclass
    AND a.attname = 'embedding'
    AND NOT a.attisdropped
  LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION public.get_embedding_dimension() TO anon, authenticated, service_role;

-- Bulk embedding updater for efficient remote backfills.
CREATE OR REPLACE FUNCTION public.apply_chunk_embeddings(p_rows jsonb)
RETURNS TABLE(updated_count INT)
LANGUAGE sql
SECURITY DEFINER
AS $$
  WITH input_rows AS (
    SELECT
      (elem->>'id')::uuid      AS id,
      (elem->>'embedding')::vector(768) AS embedding
    FROM jsonb_array_elements(p_rows) AS elem
  ), updated AS (
    UPDATE public.knowledge_chunks kc
    SET embedding = ir.embedding
    FROM input_rows ir
    WHERE kc.id = ir.id
      AND kc.deleted_at IS NULL
    RETURNING 1
  )
  SELECT COUNT(*)::int AS updated_count FROM updated;
$$;

GRANT EXECUTE ON FUNCTION public.apply_chunk_embeddings(jsonb) TO anon, authenticated, service_role;
