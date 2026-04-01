-- Shareable responses schema
-- Safe, idempotent, Supabase-compatible

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS shared_responses (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  message_id uuid NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  share_token text NOT NULL UNIQUE,
  access_level text NOT NULL CHECK (access_level IN ('public', 'unlisted', 'private')),
  title text,
  prompt_text text NOT NULL DEFAULT '',
  response_text text NOT NULL DEFAULT '',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  password_hash text,
  expiry_days integer,
  expires_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT NOW(),
  updated_at timestamptz NOT NULL DEFAULT NOW(),
  view_count integer NOT NULL DEFAULT 0,
  last_viewed_at timestamptz
);

CREATE INDEX IF NOT EXISTS idx_shared_responses_owner_id
  ON shared_responses(owner_id);

CREATE INDEX IF NOT EXISTS idx_shared_responses_share_token
  ON shared_responses(share_token);

CREATE INDEX IF NOT EXISTS idx_shared_responses_created_at
  ON shared_responses(created_at);

CREATE INDEX IF NOT EXISTS idx_shared_responses_access_level
  ON shared_responses(access_level);

ALTER TABLE shared_responses ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'shared_responses'
      AND policyname = 'shared_responses_select'
  ) THEN
    CREATE POLICY shared_responses_select
      ON shared_responses
      FOR SELECT
      USING (
        access_level IN ('public', 'unlisted')
        OR owner_id = auth.uid()
      );
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'shared_responses'
      AND policyname = 'shared_responses_insert'
  ) THEN
    CREATE POLICY shared_responses_insert
      ON shared_responses
      FOR INSERT
      WITH CHECK (owner_id = auth.uid());
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'shared_responses'
      AND policyname = 'shared_responses_update'
  ) THEN
    CREATE POLICY shared_responses_update
      ON shared_responses
      FOR UPDATE
      USING (owner_id = auth.uid())
      WITH CHECK (owner_id = auth.uid());
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'shared_responses'
      AND policyname = 'shared_responses_delete'
  ) THEN
    CREATE POLICY shared_responses_delete
      ON shared_responses
      FOR DELETE
      USING (owner_id = auth.uid());
  END IF;
END
$$;

DROP TRIGGER IF EXISTS update_shared_responses_updated_at ON shared_responses;

CREATE TRIGGER update_shared_responses_updated_at
BEFORE UPDATE ON shared_responses
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
