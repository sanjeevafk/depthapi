-- Weighted RRF hybrid search functions (v7)
-- Adds configurable dense_weight / lexical_weight to RRF fusion and returns up to
-- 25 candidates so the CrossEncoder reranker can promote relevant hits from ranks 11-25.

CREATE OR REPLACE FUNCTION hybrid_search_v7(
    query_text           text,
    query_embedding      vector(768),
    collection_filter    uuid    DEFAULT NULL,
    api_key_filter       uuid    DEFAULT NULL,
    recency_weight       float   DEFAULT 0.0,
    quality_weight       float   DEFAULT 0.0,
    dense_weight         float   DEFAULT 1.5,
    lexical_weight       float   DEFAULT 0.5
)
RETURNS TABLE(content text, document_id uuid, source_url text, score real)
LANGUAGE sql STABLE AS $$
WITH dense_matches AS (
    SELECT
        c.id,
        c.content,
        c.document_id,
        d.source_url,
        ROW_NUMBER() OVER (ORDER BY c.embedding <=> query_embedding ASC) AS dense_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL
      AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.embedding IS NOT NULL
    LIMIT 40
),
lexical_matches AS (
    SELECT
        c.id,
        c.content,
        c.document_id,
        d.source_url,
        ROW_NUMBER() OVER (
            ORDER BY (
                ts_rank(c.fts_tokens, plainto_tsquery('english', query_text))
                + recency_weight * (
                    1.0 / (1.0 + exp(EXTRACT(EPOCH FROM (now() - d.created_at)) / 86400.0 / 90.0))
                )
                + quality_weight * COALESCE(NULLIF(d.metadata->>'quality_score', '')::float, 0.5)
            ) DESC
        ) AS lex_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL
      AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.fts_tokens @@ plainto_tsquery('english', query_text)
    LIMIT 40
),
fused AS (
    SELECT
        COALESCE(d.id, l.id)                   AS id,
        COALESCE(d.content, l.content)         AS content,
        COALESCE(d.document_id, l.document_id) AS document_id,
        COALESCE(d.source_url, l.source_url)   AS source_url,
        (
            COALESCE(dense_weight / (60.0 + d.dense_rank), 0.0)
            + COALESCE(lexical_weight / (60.0 + l.lex_rank), 0.0)
        )::real AS score
    FROM dense_matches d
    FULL OUTER JOIN lexical_matches l ON d.id = l.id
)
SELECT content, document_id, source_url, score
FROM fused
ORDER BY score DESC
LIMIT 25
$$;

CREATE OR REPLACE FUNCTION hybrid_search_trusted_v7(
    query_text           text,
    query_embedding      vector(768),
    collection_filter    uuid    DEFAULT NULL,
    api_key_filter       uuid    DEFAULT NULL,
    recency_weight       float   DEFAULT 0.0,
    quality_weight       float   DEFAULT 0.0,
    dense_weight         float   DEFAULT 1.5,
    lexical_weight       float   DEFAULT 0.5
)
RETURNS TABLE(content text, document_id uuid, source_url text, score real)
LANGUAGE sql STABLE AS $$
    SELECT * FROM hybrid_search_v7(
        query_text, query_embedding,
        collection_filter, api_key_filter,
        recency_weight, quality_weight,
        dense_weight, lexical_weight
    )
$$;

CREATE OR REPLACE FUNCTION hybrid_search_with_graph_v7(
    query_text           text,
    query_embedding      vector(768),
    collection_filter    uuid    DEFAULT NULL,
    api_key_filter       uuid    DEFAULT NULL,
    graph_hops           integer DEFAULT 1,
    graph_weight         float   DEFAULT 0.25,
    dense_weight         float   DEFAULT 1.5,
    lexical_weight       float   DEFAULT 0.5
)
RETURNS TABLE(content text, document_id uuid, source_url text, score real)
LANGUAGE sql STABLE AS $$
WITH dense_matches AS (
    SELECT
        c.id,
        ROW_NUMBER() OVER (ORDER BY c.embedding <=> query_embedding ASC) AS dense_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL
      AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.embedding IS NOT NULL
    LIMIT 40
),
lexical_matches AS (
    SELECT
        c.id,
        ROW_NUMBER() OVER (ORDER BY ts_rank(c.fts_tokens, plainto_tsquery('english', query_text)) DESC) AS lex_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL
      AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.fts_tokens @@ plainto_tsquery('english', query_text)
    LIMIT 40
),
base_fused AS (
    SELECT
        COALESCE(d.id, l.id) AS id,
        (
            COALESCE(dense_weight / (60.0 + d.dense_rank), 0.0)
            + COALESCE(lexical_weight / (60.0 + l.lex_rank), 0.0)
        )::real AS base_score
    FROM dense_matches d
    FULL OUTER JOIN lexical_matches l ON d.id = l.id
),
top_seed_concepts AS (
    SELECT cc.concept_id
    FROM base_fused bf
    JOIN knowledge_chunk_concepts cc ON cc.chunk_id = bf.id
    GROUP BY cc.concept_id
    ORDER BY MAX(bf.base_score) DESC
    LIMIT 5
),
related_concepts AS (
    SELECT concept_id, 1.0 AS decay FROM top_seed_concepts
    UNION
    SELECT e.target_concept_id AS concept_id, 0.7 * e.weight AS decay
    FROM top_seed_concepts sc
    JOIN knowledge_edges e ON e.source_concept_id = sc.concept_id
    WHERE graph_hops >= 1
    UNION
    SELECT e2.target_concept_id AS concept_id, 0.45 * e1.weight * e2.weight AS decay
    FROM top_seed_concepts sc
    JOIN knowledge_edges e1 ON e1.source_concept_id = sc.concept_id
    JOIN knowledge_edges e2 ON e2.source_concept_id = e1.target_concept_id
    WHERE graph_hops >= 2
),
graph_connected_chunks AS (
    SELECT cc.chunk_id, MAX(rc.decay * cc.confidence) AS graph_score
    FROM related_concepts rc
    JOIN knowledge_chunk_concepts cc ON cc.concept_id = rc.concept_id
    GROUP BY cc.chunk_id
)
SELECT
    c.content,
    c.document_id,
    d.source_url,
    (COALESCE(bf.base_score, 0.0) + (COALESCE(gc.graph_score, 0.0) * graph_weight * (1.0 / 60.0)))::real AS score
FROM base_fused bf
FULL OUTER JOIN graph_connected_chunks gc ON bf.id = gc.chunk_id
JOIN knowledge_chunks c ON c.id = COALESCE(bf.id, gc.chunk_id)
JOIN knowledge_documents d ON d.id = c.document_id
JOIN knowledge_collections k ON k.id = d.collection_id
WHERE api_key_filter IS NOT NULL
  AND k.api_key_id = api_key_filter
  AND (collection_filter IS NULL OR d.collection_id = collection_filter)
ORDER BY score DESC
LIMIT 25
$$;

CREATE OR REPLACE FUNCTION hybrid_search_trusted_with_graph_v7(
    query_text           text,
    query_embedding      vector(768),
    collection_filter    uuid    DEFAULT NULL,
    api_key_filter       uuid    DEFAULT NULL,
    graph_hops           integer DEFAULT 1,
    graph_weight         float   DEFAULT 0.25,
    dense_weight         float   DEFAULT 1.5,
    lexical_weight       float   DEFAULT 0.5
)
RETURNS TABLE(content text, document_id uuid, source_url text, score real)
LANGUAGE sql STABLE AS $$
    SELECT * FROM hybrid_search_with_graph_v7(
        query_text, query_embedding,
        collection_filter, api_key_filter,
        graph_hops, graph_weight,
        dense_weight, lexical_weight
    )
$$;
