-- Migration 006: pgvector halfvec HNSW index for binary quantization pre-screening.
--
-- turbopuffer ANN v3 insight: binary quantization gives 16-32× vector compression
-- enabling a fast first-pass over quantized vectors before full-precision re-ranking.
-- pgvector ≥ 0.7.0 supports `halfvec` (float16) which halves HNSW memory with
-- similar recall. Run this migration only if your pgvector version supports it.
--
-- Check your version first:
--   SELECT extversion FROM pg_extension WHERE extname = 'vector';
--
-- If version < 0.7.0, this migration will fail — skip it and upgrade pgvector first.
-- The existing float32 HNSW index in migration 001 remains fully functional.

DO $$
DECLARE
    pg_vector_version text;
    major int;
    minor int;
BEGIN
    SELECT extversion INTO pg_vector_version
    FROM pg_extension
    WHERE extname = 'vector';

    IF pg_vector_version IS NULL THEN
        RAISE EXCEPTION 'pgvector extension not installed';
    END IF;

    -- Parse major.minor from version string (e.g. "0.8.0" → major=0, minor=8)
    major := split_part(pg_vector_version, '.', 1)::int;
    minor := split_part(pg_vector_version, '.', 2)::int;

    IF (major = 0 AND minor < 7) OR major < 0 THEN
        RAISE NOTICE 'pgvector % does not support halfvec — skipping halfvec index. Upgrade to ≥ 0.7.0 to enable.', pg_vector_version;
    ELSE
        RAISE NOTICE 'pgvector % supports halfvec — creating halfvec HNSW index.', pg_vector_version;

        -- Add a halfvec shadow column for the 768-dim embedding.
        -- Stores float16 instead of float32: half the HNSW memory, similar recall.
        -- The original `embedding vector(768)` column is untouched — dense_search_v5
        -- and hybrid_search_v5/v6 continue to work against full-precision vectors.
        EXECUTE '
            ALTER TABLE knowledge_chunks
            ADD COLUMN IF NOT EXISTS embedding_half halfvec(768)
            GENERATED ALWAYS AS (embedding::halfvec(768)) STORED
        ';

        -- HNSW index on halfvec for fast approximate pre-screening.
        -- ef_construction=128 and m=16 are pgvector defaults; tune after profiling.
        EXECUTE '
            CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_half_hnsw
            ON knowledge_chunks
            USING hnsw (embedding_half halfvec_cosine_ops)
            WITH (m = 16, ef_construction = 128)
        ';

        RAISE NOTICE 'halfvec HNSW index created. To use it in queries, cast the query: (embedding_half <=> $1::halfvec(768)).';
    END IF;
END $$;
