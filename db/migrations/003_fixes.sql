-- Migration 003: auth hardening + ingestion/neighbor fixes (idempotent).
-- API keys: add expiry / scopes / revocation without breaking existing sha256 hashes.
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS scopes text[] NOT NULL DEFAULT '{}';
ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS revoked_at timestamptz;

-- UUID defaults: 001 left collections/documents/queue without defaults, so
-- queue_document(document_uuid) and direct inserts without explicit ids fail.
ALTER TABLE knowledge_collections ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE knowledge_documents ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE knowledge_ingestion_queue ALTER COLUMN id SET DEFAULT gen_random_uuid();

-- queue_document relied on the missing default; make it explicit as well.
CREATE OR REPLACE FUNCTION queue_document(document_uuid uuid) RETURNS uuid LANGUAGE sql AS
$$ INSERT INTO knowledge_ingestion_queue(id, document_id) VALUES (gen_random_uuid(), document_uuid) RETURNING id $$;

-- Neighbor window: 001 returned the head of the document instead of centering
-- on the anchor chunk. Center on chunk_order.
CREATE OR REPLACE FUNCTION get_neighbor_chunks(chunk_id uuid, window_size integer DEFAULT 2)
RETURNS SETOF knowledge_chunks LANGUAGE sql STABLE AS $$
  WITH anchor AS (SELECT document_id, chunk_order FROM knowledge_chunks WHERE id = chunk_id)
  SELECT c.* FROM knowledge_chunks c, anchor a
  WHERE c.document_id = a.document_id
    AND c.chunk_order BETWEEN a.chunk_order - window_size AND a.chunk_order + window_size
  ORDER BY c.chunk_order
$$;

-- delete_collection: 001 did a bare DELETE that FK-violates without CASCADE.
-- Delete dependents explicitly so it works on pre-003 databases too.
CREATE OR REPLACE FUNCTION delete_collection(collection_uuid uuid) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM knowledge_chunk_concepts WHERE chunk_id IN (
    SELECT c.id FROM knowledge_chunks c
    JOIN knowledge_documents d ON d.id = c.document_id
    WHERE d.collection_id = collection_uuid
  );
  DELETE FROM knowledge_edges WHERE collection_id = collection_uuid;
  DELETE FROM knowledge_concepts WHERE collection_id = collection_uuid;
  DELETE FROM knowledge_chunks WHERE document_id IN (
    SELECT id FROM knowledge_documents WHERE collection_id = collection_uuid
  );
  DELETE FROM knowledge_ingestion_queue WHERE document_id IN (
    SELECT id FROM knowledge_documents WHERE collection_id = collection_uuid
  );
  DELETE FROM knowledge_documents WHERE collection_id = collection_uuid;
  DELETE FROM knowledge_collections WHERE id = collection_uuid;
END $$;
