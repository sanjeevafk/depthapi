-- Migration 002: Relational Concept Graph & Lineage
-- Adds tenant-isolated concepts, edges, chunk-concept mapping, and bounded graph traversal.

CREATE TABLE IF NOT EXISTS knowledge_concepts (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    name text NOT NULL,
    concept_type text NOT NULL DEFAULT 'topic',
    description text,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(collection_id, name)
);

CREATE INDEX IF NOT EXISTS knowledge_concepts_collection_idx ON knowledge_concepts(collection_id);
CREATE INDEX IF NOT EXISTS knowledge_concepts_name_idx ON knowledge_concepts(name);
CREATE INDEX IF NOT EXISTS knowledge_concepts_metadata_gin ON knowledge_concepts USING gin (metadata);

CREATE TABLE IF NOT EXISTS knowledge_edges (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    collection_id uuid NOT NULL REFERENCES knowledge_collections(id) ON DELETE CASCADE,
    source_concept_id uuid NOT NULL REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
    target_concept_id uuid NOT NULL REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
    relation_type text NOT NULL DEFAULT 'relates_to',
    weight real NOT NULL DEFAULT 1.0,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(collection_id, source_concept_id, target_concept_id, relation_type)
);

CREATE INDEX IF NOT EXISTS knowledge_edges_source_idx ON knowledge_edges(collection_id, source_concept_id);
CREATE INDEX IF NOT EXISTS knowledge_edges_target_idx ON knowledge_edges(collection_id, target_concept_id);
CREATE INDEX IF NOT EXISTS knowledge_edges_relation_idx ON knowledge_edges(relation_type);

CREATE TABLE IF NOT EXISTS knowledge_chunk_concepts (
    chunk_id uuid NOT NULL REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
    concept_id uuid NOT NULL REFERENCES knowledge_concepts(id) ON DELETE CASCADE,
    confidence real NOT NULL DEFAULT 1.0,
    metadata jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (chunk_id, concept_id)
);

CREATE INDEX IF NOT EXISTS knowledge_chunk_concepts_chunk_idx ON knowledge_chunk_concepts(chunk_id);
CREATE INDEX IF NOT EXISTS knowledge_chunk_concepts_concept_idx ON knowledge_chunk_concepts(concept_id);

-- Helper function to associate chunk to concept by document_id and chunk_order
CREATE OR REPLACE FUNCTION link_chunk_to_concept(
    p_document_id uuid,
    p_chunk_order integer,
    p_concept_id uuid,
    p_confidence real,
    p_metadata jsonb DEFAULT '{}'
) RETURNS void LANGUAGE sql AS $$
    INSERT INTO knowledge_chunk_concepts (chunk_id, concept_id, confidence, metadata)
    SELECT c.id, p_concept_id, p_confidence, p_metadata
    FROM knowledge_chunks c
    WHERE c.document_id = p_document_id AND c.chunk_order = p_chunk_order
    ON CONFLICT (chunk_id, concept_id) DO UPDATE SET confidence = EXCLUDED.confidence;
$$;

-- Recursive bounded graph traversal (1 to N hops with cycle prevention)
CREATE OR REPLACE FUNCTION graph_traverse_concepts(
    root_concept_ids uuid[],
    max_hops integer DEFAULT 1,
    collection_filter uuid DEFAULT NULL
)
RETURNS TABLE (
    concept_id uuid,
    depth integer,
    path uuid[]
)
LANGUAGE sql STABLE AS $$
WITH RECURSIVE traversal AS (
    SELECT
        c.id AS concept_id,
        0 AS depth,
        ARRAY[c.id] AS path
    FROM knowledge_concepts c
    WHERE c.id = ANY(root_concept_ids)
      AND (collection_filter IS NULL OR c.collection_id = collection_filter)

    UNION

    SELECT
        e.target_concept_id AS concept_id,
        t.depth + 1 AS depth,
        t.path || e.target_concept_id AS path
    FROM traversal t
    JOIN knowledge_edges e ON e.source_concept_id = t.concept_id
    WHERE t.depth < max_hops
      AND NOT (e.target_concept_id = ANY(t.path))
      AND (collection_filter IS NULL OR e.collection_id = collection_filter)
)
SELECT DISTINCT ON (concept_id) concept_id, depth, path
FROM traversal
ORDER BY concept_id, depth ASC;
$$;

-- Graph-augmented hybrid retrieval supporting 0, 1, or 2 hops
CREATE OR REPLACE FUNCTION hybrid_search_with_graph_v5(
    query_text text,
    query_embedding vector(768),
    collection_filter uuid DEFAULT NULL,
    api_key_filter uuid DEFAULT NULL,
    graph_hops integer DEFAULT 1,
    graph_weight real DEFAULT 0.25
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
    WHERE api_key_filter IS NOT NULL AND k.api_key_id = api_key_filter
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
        ROW_NUMBER() OVER (ORDER BY ts_rank(c.fts_tokens, plainto_tsquery('english', query_text)) DESC) AS lex_rank
    FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
      AND c.fts_tokens @@ plainto_tsquery('english', query_text)
    LIMIT 40
),
base_fused AS (
    SELECT
        COALESCE(d.id, l.id) AS id,
        COALESCE(d.content, l.content) AS content,
        COALESCE(d.document_id, l.document_id) AS document_id,
        COALESCE(d.source_url, l.source_url) AS source_url,
        (COALESCE(1.0 / (60.0 + d.dense_rank), 0.0) + COALESCE(1.0 / (60.0 + l.lex_rank), 0.0))::real AS base_score
    FROM dense_matches d
    FULL OUTER JOIN lexical_matches l ON d.id = l.id
),
top_seed_concepts AS (
    SELECT DISTINCT cc.concept_id
    FROM (SELECT id AS chunk_id FROM base_fused ORDER BY base_score DESC LIMIT 5) seeds
    JOIN knowledge_chunk_concepts cc ON cc.chunk_id = seeds.chunk_id
    WHERE graph_hops > 0
),
traversed_concepts AS (
    SELECT concept_id, depth
    FROM graph_traverse_concepts(
        ARRAY(SELECT concept_id FROM top_seed_concepts),
        CASE WHEN graph_hops > 0 THEN graph_hops ELSE 0 END,
        collection_filter
    )
),
graph_connected_chunks AS (
    SELECT
        cc.chunk_id,
        MAX((1.0 / (1.0 + tc.depth)) * cc.confidence)::real AS graph_score
    FROM traversed_concepts tc
    JOIN knowledge_chunk_concepts cc ON cc.concept_id = tc.concept_id
    GROUP BY cc.chunk_id
),
combined AS (
    SELECT
        c.id,
        c.content,
        c.document_id,
        d.source_url,
        (COALESCE(bf.base_score, 0.0) + (COALESCE(gc.graph_score, 0.0) * graph_weight * (1.0 / 60.0)))::real AS score
    FROM base_fused bf
    FULL OUTER JOIN graph_connected_chunks gc ON bf.id = gc.chunk_id
    JOIN knowledge_chunks c ON c.id = COALESCE(bf.id, gc.chunk_id)
    JOIN knowledge_documents d ON d.id = c.document_id
    JOIN knowledge_collections k ON k.id = d.collection_id
    WHERE api_key_filter IS NOT NULL AND k.api_key_id = api_key_filter
      AND (collection_filter IS NULL OR d.collection_id = collection_filter)
)
SELECT content, document_id, source_url, score
FROM combined
ORDER BY score DESC
LIMIT 10;
$$;

CREATE OR REPLACE FUNCTION hybrid_search_trusted_with_graph_v5(
    query_text text,
    query_embedding vector(768),
    collection_filter uuid DEFAULT NULL,
    api_key_filter uuid DEFAULT NULL,
    graph_hops integer DEFAULT 1,
    graph_weight real DEFAULT 0.25
)
RETURNS TABLE(content text, document_id uuid, source_url text, score real)
LANGUAGE sql STABLE AS $$
    SELECT * FROM hybrid_search_with_graph_v5(
        query_text,
        query_embedding,
        collection_filter,
        api_key_filter,
        graph_hops,
        graph_weight
    );
$$;

-- Diagnostic lineage inspection function
CREATE OR REPLACE FUNCTION get_concept_lineage(
    concept_uuid uuid,
    max_hops integer DEFAULT 1
)
RETURNS TABLE(
    source_concept text,
    target_concept text,
    relation text,
    depth integer
)
LANGUAGE sql STABLE AS $$
    WITH traversed AS (
        SELECT concept_id, depth
        FROM graph_traverse_concepts(ARRAY[concept_uuid], max_hops)
    )
    SELECT
        sc.name AS source_concept,
        tc.name AS target_concept,
        e.relation_type AS relation,
        t.depth
    FROM traversed t
    JOIN knowledge_edges e ON e.source_concept_id = t.concept_id
    JOIN knowledge_concepts sc ON sc.id = e.source_concept_id
    JOIN knowledge_concepts tc ON tc.id = e.target_concept_id;
$$;

COMMENT ON TABLE knowledge_concepts IS 'Tenant-isolated semantic concepts and entities.';
COMMENT ON TABLE knowledge_edges IS 'Directed, typed relationships between concepts.';
COMMENT ON TABLE knowledge_chunk_concepts IS 'Junction table linking text chunks to resolved concepts.';
