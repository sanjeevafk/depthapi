-- V2 conversation schema + user_usage extensions
-- Safe, idempotent, Supabase-compatible version

-- Ensure required extension (safe even if already enabled)
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =========================
-- Conversations
-- =========================
CREATE TABLE IF NOT EXISTS conversations (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  title       text,
  mode        text CHECK (mode IN ('eli5', 'ensemble', 'technical', 'socratic')),
  settings    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT NOW(),
  updated_at  timestamptz NOT NULL DEFAULT NOW()
);

-- =========================
-- Messages
-- =========================
CREATE TABLE IF NOT EXISTS messages (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  role            text NOT NULL CHECK (role IN ('user', 'assistant')),
  content         text NOT NULL,
  attachments     jsonb NOT NULL DEFAULT '[]'::jsonb,
  metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at      timestamptz NOT NULL DEFAULT NOW()
);

-- =========================
-- Indexes
-- =========================
CREATE INDEX IF NOT EXISTS idx_conversations_user_id 
  ON conversations(user_id);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_created 
  ON messages(conversation_id, created_at);

-- =========================
-- user_usage (Create or Extend)
-- =========================
CREATE TABLE IF NOT EXISTS user_usage (
  user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  tier text NOT NULL DEFAULT 'free' CHECK (tier IN ('free','pro')),
  prompts_today integer NOT NULL DEFAULT 0,
  last_reset_date date NOT NULL DEFAULT CURRENT_DATE,
  payment_subscription_id text,
  created_at timestamptz NOT NULL DEFAULT NOW()
);

-- If table already existed but missing columns, ensure they exist
ALTER TABLE user_usage
  ADD COLUMN IF NOT EXISTS tier text DEFAULT 'free'
    CHECK (tier IN ('free','pro')),
  ADD COLUMN IF NOT EXISTS prompts_today integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_reset_date date NOT NULL DEFAULT CURRENT_DATE,
  ADD COLUMN IF NOT EXISTS payment_subscription_id text;

-- =========================
-- Enable RLS
-- =========================
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_usage ENABLE ROW LEVEL SECURITY;

-- =========================
-- RLS: Conversations
-- =========================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'conversations'
      AND policyname = 'conversations_user_access'
  ) THEN
    CREATE POLICY conversations_user_access
      ON conversations
      FOR ALL
      USING (user_id = auth.uid())
      WITH CHECK (user_id = auth.uid());
  END IF;
END
$$;

-- =========================
-- RLS: Messages
-- =========================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'messages'
      AND policyname = 'messages_user_access'
  ) THEN
    CREATE POLICY messages_user_access
      ON messages
      FOR ALL
      USING (
        EXISTS (
          SELECT 1
          FROM conversations c
          WHERE c.id = messages.conversation_id
            AND c.user_id = auth.uid()
        )
      )
      WITH CHECK (
        EXISTS (
          SELECT 1
          FROM conversations c
          WHERE c.id = messages.conversation_id
            AND c.user_id = auth.uid()
        )
      );
  END IF;
END
$$;

-- =========================
-- RLS: user_usage
-- =========================
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'user_usage'
      AND policyname = 'user_usage_select'
  ) THEN
    CREATE POLICY user_usage_select
      ON user_usage
      FOR SELECT
      USING (user_id = auth.uid());
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename = 'user_usage'
      AND policyname = 'user_usage_update'
  ) THEN
    CREATE POLICY user_usage_update
      ON user_usage
      FOR UPDATE
      USING (user_id = auth.uid())
      WITH CHECK (user_id = auth.uid());
  END IF;
END
$$;

-- =========================
-- Auto-update updated_at
-- =========================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_conversations_updated_at ON conversations;

CREATE TRIGGER update_conversations_updated_at
BEFORE UPDATE ON conversations
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();