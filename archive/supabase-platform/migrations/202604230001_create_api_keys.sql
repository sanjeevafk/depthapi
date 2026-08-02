-- Migration: 202604230001_create_api_keys.sql
-- Creates the API key table for DepthAPI B2B authentication.
-- API keys replace Supabase JWT auth for all v1 endpoints.

CREATE TABLE IF NOT EXISTS api_keys (
    id                   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The key is NEVER stored in plaintext.
    -- We store a SHA-256 hex digest of the raw "sk-depth-xxx" string.
    -- SHA-256 is appropriate here because API keys are long random strings
    -- (not passwords), so brute-force is computationally infeasible.
    key_hash             TEXT        NOT NULL UNIQUE,

    -- First 16 chars of the raw key, e.g. "sk-depth-a1b2c3d4"
    -- Used for display-only (dashboard, logs). Never enough to reconstruct the key.
    prefix               TEXT        NOT NULL,

    -- Human-readable project name set by the owner at key generation time.
    project_name         TEXT        NOT NULL,

    -- Contact email for billing/alerts. Not used for auth.
    owner_email          TEXT        NOT NULL,

    -- Plan tier. Drives token budget limits in the rate limiter.
    -- Values: 'free' | 'starter' | 'pro' | 'enterprise'
    plan                 TEXT        NOT NULL DEFAULT 'free'
                         CHECK (plan IN ('free', 'starter', 'pro', 'enterprise')),

    -- Soft monthly token ceiling. 0 means unlimited (enterprise).
    -- The rate_limit service enforces this via Redis.
    monthly_token_budget BIGINT      NOT NULL DEFAULT 100000,

    -- Hard request ceiling per minute (0 = use plan default).
    requests_per_minute  INT         NOT NULL DEFAULT 0,

    is_active            BOOLEAN     NOT NULL DEFAULT true,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Set on revocation. Soft-delete: key stays in DB for audit trail.
    revoked_at           TIMESTAMPTZ
);

-- Index for the hot path: every API request hashes the key and does this lookup.
CREATE INDEX IF NOT EXISTS idx_api_keys_key_hash ON api_keys (key_hash) WHERE is_active = true;

-- Index for the admin dashboard: list keys by owner.
CREATE INDEX IF NOT EXISTS idx_api_keys_owner_email ON api_keys (owner_email);

-- Row-level security: the admin service role bypasses RLS.
-- No public access to this table. Ever.
ALTER TABLE api_keys ENABLE ROW LEVEL SECURITY;

-- Only the service role (used by the FastAPI backend) can read/write.
DROP POLICY IF EXISTS "service_role_full_access" ON api_keys;
CREATE POLICY "service_role_full_access" ON api_keys
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

COMMENT ON TABLE api_keys IS
    'B2B API keys for DepthAPI. Keys are hashed (SHA-256). Raw key is shown once at generation and never stored.';
