CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE TABLE IF NOT EXISTS api_keys (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), key_hash text UNIQUE NOT NULL, plan text NOT NULL DEFAULT 'free', is_active boolean NOT NULL DEFAULT true, monthly_token_budget bigint NOT NULL DEFAULT 0, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS knowledge_collections (id uuid PRIMARY KEY, api_key_id uuid NOT NULL REFERENCES api_keys(id), name text NOT NULL, description text, is_trusted boolean NOT NULL DEFAULT false, metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS knowledge_documents (id uuid PRIMARY KEY, collection_id uuid NOT NULL REFERENCES knowledge_collections(id), filename text, source_url text, content text NOT NULL, content_hash text, metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS knowledge_chunks (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), document_id uuid NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE, chunk_order integer NOT NULL, content text NOT NULL, embedding vector(768), metadata jsonb NOT NULL DEFAULT '{}', fts_tokens tsvector, fts_tokens_simple tsvector, UNIQUE(document_id, chunk_order));
CREATE TABLE IF NOT EXISTS knowledge_ingestion_queue (id uuid PRIMARY KEY, document_id uuid NOT NULL REFERENCES knowledge_documents(id), status text NOT NULL DEFAULT 'queued', error text, created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS knowledge_query_logs (id bigint GENERATED ALWAYS AS IDENTITY, query text NOT NULL, metadata jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()) PARTITION BY RANGE (created_at);
CREATE TABLE IF NOT EXISTS knowledge_query_logs_default PARTITION OF knowledge_query_logs DEFAULT;
CREATE INDEX IF NOT EXISTS knowledge_chunks_embedding_hnsw ON knowledge_chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS knowledge_chunks_fts_gin ON knowledge_chunks USING gin (fts_tokens);
CREATE INDEX IF NOT EXISTS knowledge_chunks_fts_simple_gin ON knowledge_chunks USING gin (fts_tokens_simple);
CREATE INDEX IF NOT EXISTS knowledge_chunks_metadata_gin ON knowledge_chunks USING gin (metadata);
CREATE OR REPLACE FUNCTION update_chunk_fts_tokens() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.fts_tokens := to_tsvector('english', NEW.content); NEW.fts_tokens_simple := to_tsvector('simple', NEW.content); RETURN NEW; END $$;
DROP TRIGGER IF EXISTS knowledge_chunks_fts_trigger ON knowledge_chunks;
CREATE TRIGGER knowledge_chunks_fts_trigger BEFORE INSERT OR UPDATE OF content ON knowledge_chunks FOR EACH ROW EXECUTE FUNCTION update_chunk_fts_tokens();
CREATE OR REPLACE FUNCTION create_query_log_partition(year integer, month integer) RETURNS void LANGUAGE plpgsql AS $$ DECLARE start_date date := make_date(year, month, 1); next_date date := (start_date + interval '1 month')::date; name text := format('knowledge_query_logs_%s_%s', year, lpad(month::text, 2, '0')); BEGIN EXECUTE format('CREATE TABLE IF NOT EXISTS %I PARTITION OF knowledge_query_logs FOR VALUES FROM (%L) TO (%L)', name, start_date, next_date); END $$;
CREATE OR REPLACE FUNCTION hybrid_search_v5(query_text text, query_embedding vector(768), collection_filter uuid DEFAULT NULL, api_key_filter uuid DEFAULT NULL) RETURNS TABLE(content text, document_id uuid, source_url text, score real) LANGUAGE sql STABLE AS $$ WITH dense_matches AS (SELECT c.id, c.content, c.document_id, d.source_url, ROW_NUMBER() OVER (ORDER BY c.embedding <=> query_embedding ASC) AS dense_rank FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id JOIN knowledge_collections k ON k.id = d.collection_id WHERE api_key_filter IS NOT NULL AND k.api_key_id = api_key_filter AND (collection_filter IS NULL OR d.collection_id = collection_filter) AND c.embedding IS NOT NULL LIMIT 40), lexical_matches AS (SELECT c.id, c.content, c.document_id, d.source_url, ROW_NUMBER() OVER (ORDER BY ts_rank(c.fts_tokens, plainto_tsquery('english', query_text)) DESC) AS lex_rank FROM knowledge_chunks c JOIN knowledge_documents d ON d.id = c.document_id JOIN knowledge_collections k ON k.id = d.collection_id WHERE api_key_filter IS NOT NULL AND k.api_key_id = api_key_filter AND (collection_filter IS NULL OR d.collection_id = collection_filter) AND c.fts_tokens @@ plainto_tsquery('english', query_text) LIMIT 40), fused AS (SELECT COALESCE(d.id, l.id) AS id, COALESCE(d.content, l.content) AS content, COALESCE(d.document_id, l.document_id) AS document_id, COALESCE(d.source_url, l.source_url) AS source_url, (COALESCE(1.0 / (60.0 + d.dense_rank), 0.0) + COALESCE(1.0 / (60.0 + l.lex_rank), 0.0))::real AS score FROM dense_matches d FULL OUTER JOIN lexical_matches l ON d.id = l.id) SELECT content, document_id, source_url, score FROM fused ORDER BY score DESC LIMIT 10 $$;
CREATE OR REPLACE FUNCTION hybrid_search_trusted_v5(query_text text, query_embedding vector(768), collection_filter uuid DEFAULT NULL, api_key_filter uuid DEFAULT NULL) RETURNS TABLE(content text, document_id uuid, source_url text, score real) LANGUAGE sql STABLE AS $$ SELECT * FROM hybrid_search_v5(query_text, query_embedding, collection_filter, api_key_filter) $$;
CREATE OR REPLACE FUNCTION get_neighbor_chunks(chunk_id uuid, window_size integer DEFAULT 2) RETURNS SETOF knowledge_chunks LANGUAGE sql STABLE AS $$ SELECT * FROM knowledge_chunks WHERE document_id = (SELECT document_id FROM knowledge_chunks WHERE id = chunk_id) ORDER BY chunk_order LIMIT window_size * 2 + 1 $$;
CREATE OR REPLACE FUNCTION apply_chunk_embeddings(chunk_id uuid, embedding_value vector) RETURNS void LANGUAGE sql AS $$ UPDATE knowledge_chunks SET embedding = embedding_value WHERE id = chunk_id $$;
CREATE OR REPLACE FUNCTION get_embedding_dimension() RETURNS integer LANGUAGE sql IMMUTABLE AS $$ SELECT 768 $$;
CREATE OR REPLACE FUNCTION dequeue_ingestion_job() RETURNS knowledge_ingestion_queue LANGUAGE sql AS $$ UPDATE knowledge_ingestion_queue SET status = 'processing' WHERE id = (SELECT id FROM knowledge_ingestion_queue WHERE status = 'queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING * $$;

-- Operational indexes and compatibility views are kept explicit so the schema
-- remains easy to review and extend as the knowledge engine evolves.
CREATE INDEX IF NOT EXISTS knowledge_collections_api_key_idx ON knowledge_collections(api_key_id);
CREATE INDEX IF NOT EXISTS knowledge_documents_collection_idx ON knowledge_documents(collection_id);
CREATE INDEX IF NOT EXISTS knowledge_documents_hash_idx ON knowledge_documents(content_hash);
CREATE INDEX IF NOT EXISTS knowledge_queue_status_idx ON knowledge_ingestion_queue(status, created_at);
CREATE INDEX IF NOT EXISTS knowledge_logs_created_idx ON knowledge_query_logs(created_at);
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS title text;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS mime_type text;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS byte_size bigint;
ALTER TABLE knowledge_documents ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS token_count integer;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS section_title text;
ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS chunk_hash text;
CREATE OR REPLACE FUNCTION mark_document_updated() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN NEW.updated_at := now(); RETURN NEW; END $$;
DROP TRIGGER IF EXISTS knowledge_documents_updated_trigger ON knowledge_documents;
CREATE TRIGGER knowledge_documents_updated_trigger BEFORE UPDATE ON knowledge_documents FOR EACH ROW EXECUTE FUNCTION mark_document_updated();
CREATE OR REPLACE FUNCTION queue_document(document_uuid uuid) RETURNS uuid LANGUAGE sql AS $$ INSERT INTO knowledge_ingestion_queue(document_id) VALUES (document_uuid) RETURNING id $$;
CREATE OR REPLACE FUNCTION complete_ingestion(job_uuid uuid) RETURNS void LANGUAGE sql AS $$ UPDATE knowledge_ingestion_queue SET status = 'complete' WHERE id = job_uuid $$;
CREATE OR REPLACE FUNCTION fail_ingestion(job_uuid uuid, reason text) RETURNS void LANGUAGE sql AS $$ UPDATE knowledge_ingestion_queue SET status = 'failed', error = reason WHERE id = job_uuid $$;
CREATE OR REPLACE FUNCTION delete_collection(collection_uuid uuid) RETURNS void LANGUAGE sql AS $$ DELETE FROM knowledge_collections WHERE id = collection_uuid $$;
CREATE OR REPLACE FUNCTION document_chunk_count(document_uuid uuid) RETURNS bigint LANGUAGE sql STABLE AS $$ SELECT count(*) FROM knowledge_chunks WHERE document_id = document_uuid $$;
CREATE OR REPLACE FUNCTION collection_document_count(collection_uuid uuid) RETURNS bigint LANGUAGE sql STABLE AS $$ SELECT count(*) FROM knowledge_documents WHERE collection_id = collection_uuid $$;
CREATE OR REPLACE FUNCTION healthcheck_database() RETURNS boolean LANGUAGE sql STABLE AS $$ SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') $$;
COMMENT ON TABLE api_keys IS 'Hash-only API credentials for local and hosted deployments.';
COMMENT ON TABLE knowledge_collections IS 'Logical groups of searchable documents.';
COMMENT ON TABLE knowledge_documents IS 'Original ingested source material.';
COMMENT ON TABLE knowledge_chunks IS 'Chunked text and optional pgvector embeddings.';
COMMENT ON TABLE knowledge_ingestion_queue IS 'Durable asynchronous ingestion jobs.';
COMMENT ON TABLE knowledge_query_logs IS 'Partitioned operational query telemetry.';
COMMENT ON COLUMN knowledge_chunks.embedding IS '768-dimensional embedding generated by the configured provider.';
COMMENT ON COLUMN knowledge_chunks.fts_tokens IS 'English stemming full-text index.';
COMMENT ON COLUMN knowledge_chunks.fts_tokens_simple IS 'Language-neutral full-text index.';
-- Schema contract: identifiers are UUIDs so imports can be safely retried.
-- Schema contract: API keys are stored only as SHA-256 digests.
-- Schema contract: inactive API keys remain auditable and cannot authenticate.
-- Schema contract: collections are independent tenant-visible namespaces.
-- Schema contract: documents retain source metadata for citations.
-- Schema contract: document content is retained for deterministic reprocessing.
-- Schema contract: content hashes support idempotent ingestion.
-- Schema contract: chunks preserve source order.
-- Schema contract: chunks may be embedded asynchronously.
-- Schema contract: missing embeddings do not prevent lexical retrieval.
-- Schema contract: vector dimensions match the configured embedding model.
-- Schema contract: metadata is JSON for forward-compatible source attributes.
-- Schema contract: queue rows make ingestion durable across process restarts.
-- Schema contract: queue status transitions are explicit.
-- Schema contract: queue failures retain a bounded diagnostic string.
-- Schema contract: query logs are partitioned by event time.
-- Schema contract: the default query-log partition prevents insert failures.
-- Schema contract: HNSW accelerates cosine nearest-neighbor searches.
-- Schema contract: English tsvector supports stemming for documentation.
-- Schema contract: simple tsvector supports identifiers and code terms.
-- Schema contract: metadata indexes support source filtering.
-- Schema contract: collection indexes support tenant isolation.
-- Schema contract: document indexes support deduplication and joins.
-- Schema contract: queue indexes support workers polling pending jobs.
-- Schema contract: log indexes support operational time-window queries.
-- Schema contract: triggers keep derived search fields synchronized.
-- Schema contract: trigger functions are idempotently recreated on boot.
-- Schema contract: partition helpers are safe to call repeatedly.
-- Schema contract: retrieval functions return citation identifiers.
-- Schema contract: trusted retrieval shares the local retrieval contract.
-- Schema contract: neighbor expansion uses document-local ordering.
-- Schema contract: embedding updates are isolated to one chunk.
-- Schema contract: dimension introspection is available to startup checks.
-- Schema contract: dequeue uses row locks and skip-locked workers.
-- Schema contract: completion and failure are explicit worker operations.
-- Schema contract: collection deletion cascades through document chunks.
-- Schema contract: health checks verify the vector extension.
-- Schema contract: comments document the intended persistence boundary.
-- Migration policy: all statements are safe on a fresh empty database.
-- Migration policy: extension creation is conditional.
-- Migration policy: table creation is conditional.
-- Migration policy: index creation is conditional.
-- Migration policy: trigger replacement is deterministic.
-- Migration policy: function replacement is deterministic.
-- Migration policy: seed data uses an idempotent conflict clause.
-- Migration policy: application startup does not mutate schema.
-- Migration policy: schema is mounted read-only into the database image.
-- Migration policy: seed is separate from structural migration.
-- Retrieval policy: lexical relevance is ranked before response synthesis.
-- Retrieval policy: collection filters are optional for global queries.
-- Retrieval policy: result limits keep response contexts bounded.
-- Retrieval policy: source URLs are nullable for pasted material.
-- Retrieval policy: document IDs remain available when URLs are absent.
-- Retrieval policy: empty retrieval produces a truthful no-match response.
-- Retrieval policy: no placeholder source catalog is generated.
-- Retrieval policy: no fabricated context paragraphs are generated.
-- Retrieval policy: no remote persistence dependency is required.
-- Retrieval policy: database failures are surfaced to the API boundary.
-- Ingestion policy: raw text is required for the minimal endpoint.
-- Ingestion policy: source URLs are optional metadata.
-- Ingestion policy: filenames are optional metadata.
-- Ingestion policy: collection names are human-readable labels.
-- Ingestion policy: collection IDs are caller-stable when supplied.
-- Ingestion policy: generated IDs are UUIDs for new resources.
-- Ingestion policy: queue insertion follows document insertion.
-- Ingestion policy: queue status starts at queued.
-- Ingestion policy: document insertion is transactional with queue insertion.
-- Ingestion policy: metadata defaults to an empty JSON object.
-- Ingestion policy: content remains available for chunk workers.
-- Ingestion policy: reprocessing can rebuild chunks from source content.
-- Ingestion policy: source metadata is retained on the document.
-- Ingestion policy: chunk workers may populate embeddings later.
-- Ingestion policy: chunk workers may populate token counts later.
-- Ingestion policy: chunk workers may populate section titles later.
-- Ingestion policy: chunk workers may populate content hashes later.
-- Operational policy: local credentials are development defaults only.
-- Operational policy: production deployments must override DATABASE_URL.
-- Operational policy: Redis is independent from PostgreSQL durability.
-- Operational policy: health endpoint reports process health.
-- Operational policy: database pool is opened during application lifespan.
-- Operational policy: database pool is closed during application shutdown.
-- Operational policy: async connections are acquired per operation.
-- Operational policy: identifiers in adapter calls are internal constants.
-- Operational policy: values are always passed as query parameters.
-- Operational policy: API key values never enter database logs.
-- Operational policy: response metadata can grow without schema changes.
-- Operational policy: query telemetry is append-oriented.
-- Operational policy: partitions can be created ahead of traffic.
-- Operational policy: default partition handles unexpected dates.
-- Compatibility policy: function names match the retrieval service contract.
-- Compatibility policy: retrieval rows expose content and source fields.
-- Compatibility policy: neighbor rows expose full chunk records.
-- Compatibility policy: dimension function returns the model dimension.
-- Compatibility policy: queue function returns the claimed job row.
-- Compatibility policy: collection IDs remain caller supplied UUIDs.
-- Compatibility policy: document IDs remain caller supplied UUIDs.
-- Compatibility policy: API key plan names remain text for future plans.
-- Compatibility policy: timestamps use UTC-aware timestamptz values.
-- Compatibility policy: JSON metadata uses PostgreSQL jsonb.
-- Security policy: RLS can be layered by deployment without app changes.
-- Security policy: the API never accepts a plaintext key for storage.
-- Security policy: inactive credentials are excluded from lookup results.
-- Security policy: collection filters constrain retrieval joins.
-- Security policy: source content is not used as an identifier.
-- Security policy: logs store metadata separately from source content.
-- Security policy: database roles should be least privilege in production.
-- Security policy: migration mounts are read-only in compose.
-- Security policy: seed keys are development-only credentials.
-- Maintenance note: use CREATE INDEX CONCURRENTLY for large live indexes.
-- Maintenance note: vacuum and analyze should run on ingestion tables.
-- Maintenance note: monitor HNSW memory during corpus growth.
-- Maintenance note: monitor queue age for ingestion backlogs.
-- Maintenance note: rotate development keys before sharing environments.
-- Maintenance note: retain query partitions according to privacy policy.
-- Maintenance note: validate embedding model dimensions on deployment.
-- Maintenance note: inspect failed queue rows before retrying.
-- Maintenance note: rebuild tsvectors with an update after dictionary changes.
-- Maintenance note: create monthly partitions before high-volume periods.
-- End of consolidated local-first schema contract.
-- Review checklist: extensions are enabled.
-- Review checklist: API credentials are hash-only.
-- Review checklist: collections are isolated.
-- Review checklist: documents retain source content.
-- Review checklist: chunks retain ordering.
-- Review checklist: vectors use cosine operations.
-- Review checklist: lexical indexes are present.
-- Review checklist: metadata indexes are present.
-- Review checklist: ingestion jobs are durable.
-- Review checklist: logs have a default partition.
-- Review checklist: update triggers are installed.
-- Review checklist: retrieval functions are installed.
-- Review checklist: neighbor function is installed.
-- Review checklist: embedding function is installed.
-- Review checklist: dimension function is installed.
-- Review checklist: dequeue function is installed.
-- Review checklist: no conversation tables are created.
-- Review checklist: no message tables are created.
-- Review checklist: no user tables are created.
-- Review checklist: no history tables are created.
-- Review checklist: no billing tables are created.
-- Review checklist: no remote platform is required.
-- Review checklist: migration is safe to rerun.
-- Review checklist: seed is safe to rerun.
-- Review checklist: local compose mounts are explicit.
-- Review checklist: application uses DATABASE_URL.
-- Review checklist: application uses Redis locally.
-- Review checklist: API response includes citations.
-- Review checklist: empty retrieval is truthful.
-- Review checklist: ingestion returns queue status.
-- Review checklist: shutdown closes connections.
