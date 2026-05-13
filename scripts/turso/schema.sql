-- =============================================================================
-- Turso Distributed Retrieval Platform: Schema
-- Production schema for the knowledge_chunks read replica.
-- Apply via: turso db shell <DB_NAME> < scripts/turso/schema.sql
-- =============================================================================

-- Core retrieval table
CREATE TABLE IF NOT EXISTS knowledge_chunks_platform (
    -- Primary Identity
    id             TEXT PRIMARY KEY,
    document_id    TEXT NOT NULL,
    content        TEXT NOT NULL,

    -- Structured Metadata (Query-Critical — Indexed)
    -- Enforce: Do NOT duplicate these fields inside aux_metadata JSON.
    tenant_id      TEXT NOT NULL,
    topic          TEXT,
    document_type  TEXT,
    source         TEXT,
    language       TEXT DEFAULT 'en',

    -- Auxiliary metadata (non-indexed, non-critical attributes only)
    aux_metadata   JSON,

    -- Vector Storage (float32 hot tier — no quantization on active replica)
    embedding      BLOB NOT NULL,
    embedding_dim  INTEGER DEFAULT 768,

    -- Embedding Lifecycle & Version Management
    embedding_model   TEXT NOT NULL, -- e.g. 'bge-base-en-v1.5'
    embedding_version TEXT NOT NULL, -- e.g. 'v1'
    chunking_version  TEXT NOT NULL, -- e.g. 'v2-semantic'

    -- Integrity & Sync State
    content_hash   TEXT NOT NULL,
    sync_hash      TEXT NOT NULL, -- SHA256(id + "|" + content_hash)

    created_at     DATETIME,
    updated_at     DATETIME,
    synced_at      DATETIME DEFAULT CURRENT_TIMESTAMP,

    -- Tombstone & Delete Propagation
    -- Tombstones are purged ONLY after ack_watermark is confirmed.
    is_deleted     INTEGER DEFAULT 0,
    deleted_at     DATETIME,
    ack_watermark  TEXT -- Replication acknowledgment token from integrity checker
);

-- =============================================================================
-- Indexes: Optimized for pre-filtered vector retrieval at scale
-- =============================================================================

-- Primary retrieval filter (tenant + topic + live records)
CREATE INDEX IF NOT EXISTS idx_tenant_retrieval
    ON knowledge_chunks_platform(tenant_id, topic, is_deleted);

-- Cursor-based incremental sync (overlap-window queries)
CREATE INDEX IF NOT EXISTS idx_sync_cursor
    ON knowledge_chunks_platform(updated_at, id);

-- Tombstone acknowledgment pass
CREATE INDEX IF NOT EXISTS idx_tombstone_ack
    ON knowledge_chunks_platform(is_deleted, ack_watermark)
    WHERE is_deleted = 1;

-- Lifecycle queries (model version routing for A/B retrieval)
CREATE INDEX IF NOT EXISTS idx_embedding_version
    ON knowledge_chunks_platform(embedding_model, embedding_version);

-- =============================================================================
-- Sync State Table: Tracks cursor checkpoints for resumable sync
-- =============================================================================

CREATE TABLE IF NOT EXISTS sync_state (
    id              INTEGER PRIMARY KEY DEFAULT 1, -- single-row sentinel
    last_synced_at  DATETIME NOT NULL,             -- cursor for incremental sync
    last_run_at     DATETIME,
    rows_processed  INTEGER DEFAULT 0,
    rows_failed     INTEGER DEFAULT 0,
    checksum_mismatches INTEGER DEFAULT 0
);
