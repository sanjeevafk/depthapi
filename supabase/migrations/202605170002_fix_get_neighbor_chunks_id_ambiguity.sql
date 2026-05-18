BEGIN;

CREATE OR REPLACE FUNCTION public.get_neighbor_chunks(p_chunk_id uuid, p_window_size integer)
RETURNS TABLE(id uuid, content text, chunk_order integer)
LANGUAGE plpgsql
AS $function$
BEGIN
    RETURN QUERY
    SELECT kc.id, kc.content, kc.chunk_order
    FROM public.knowledge_chunks kc
    WHERE kc.document_id = (
        SELECT anchor.document_id
        FROM public.knowledge_chunks anchor
        WHERE anchor.id = p_chunk_id
    )
      AND kc.chunk_order BETWEEN
          (
              SELECT anchor.chunk_order
              FROM public.knowledge_chunks anchor
              WHERE anchor.id = p_chunk_id
          ) - p_window_size
          AND (
              SELECT anchor.chunk_order
              FROM public.knowledge_chunks anchor
              WHERE anchor.id = p_chunk_id
          ) + p_window_size
      AND kc.deleted_at IS NULL
    ORDER BY kc.chunk_order ASC;
END;
$function$;

COMMIT;
