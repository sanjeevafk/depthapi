-- Bulk embedding updater for efficient remote backfills.

CREATE OR REPLACE FUNCTION public.apply_chunk_embeddings(p_rows jsonb)
RETURNS TABLE(updated_count INT)
LANGUAGE sql
SECURITY DEFINER
AS $$
  WITH input_rows AS (
    SELECT
      (elem->>'id')::uuid AS id,
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
