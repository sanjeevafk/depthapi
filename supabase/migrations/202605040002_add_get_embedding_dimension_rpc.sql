-- Helper RPC used by app startup guard to validate embedding dimension.

CREATE OR REPLACE FUNCTION public.get_embedding_dimension()
RETURNS TABLE(dimension INT)
LANGUAGE sql
SECURITY DEFINER
AS $$
  SELECT COALESCE(
    NULLIF(
      regexp_replace(
        format_type(a.atttypid, a.atttypmod),
        '^vector\\(([0-9]+)\\)$',
        '\\1'
      ),
      format_type(a.atttypid, a.atttypmod)
    )::INT,
    0
  ) AS dimension
  FROM pg_attribute a
  WHERE a.attrelid = 'public.knowledge_chunks'::regclass
    AND a.attname = 'embedding'
    AND NOT a.attisdropped
  LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION public.get_embedding_dimension() TO anon, authenticated, service_role;
