-- Fix dimension introspection using pgvector typmod encoding.
CREATE OR REPLACE FUNCTION public.get_embedding_dimension()
RETURNS TABLE(dimension INT)
LANGUAGE sql
SECURITY DEFINER
AS $$
  SELECT CASE
    WHEN a.atttypmod > 0 THEN (a.atttypmod - 4)
    ELSE NULL
  END::int AS dimension
  FROM pg_attribute a
  WHERE a.attrelid = 'public.knowledge_chunks'::regclass
    AND a.attname = 'embedding'
    AND NOT a.attisdropped
  LIMIT 1;
$$;

GRANT EXECUTE ON FUNCTION public.get_embedding_dimension() TO anon, authenticated, service_role;
