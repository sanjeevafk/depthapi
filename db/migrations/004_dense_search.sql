-- Migration 004: dense-only retrieval for dense-first strategy.
-- hybrid_search_v5 fuses dense+lexical in one RRF step, which dilutes a
-- strong dense signal with a weak lexical one. The application now tries
-- a dense-only lookup first and falls back to the hybrid functions only
-- when dense coverage is weak. Score is cosine similarity (1 - distance),
-- so higher is better, matching the lexical ts_rank direction.
CREATE OR REPLACE FUNCTION dense_search_v5(
    query_embedding vector(768),
    collection_filter uuid DEFAULT NULL,
    api_key_filter uuid DEFAULT NULL
) RETURNS TABLE(content text, document_id uuid, source_url text, score real) LANGUAGE sql STABLE AS $$
  SELECT c.content, c.document_id, d.source_url,
         (1.0 - (c.embedding <=> query_embedding))::real AS score
  FROM knowledge_chunks c
  JOIN knowledge_documents d ON d.id = c.document_id
  JOIN knowledge_collections k ON k.id = d.collection_id
  WHERE api_key_filter IS NOT NULL
    AND k.api_key_id = api_key_filter
    AND (collection_filter IS NULL OR d.collection_id = collection_filter)
    AND c.embedding IS NOT NULL
  ORDER BY c.embedding <=> query_embedding ASC
  LIMIT 10
$$;
