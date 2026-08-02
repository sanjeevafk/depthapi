-- =============================================================================
-- MIGRATION: conversation_schema_baseline
-- Amalgamates: 202602240001 + 202602240002 + 202603270001
--              + 202603300001 + 202604020001 + 202605160007 (history/conv parts)
-- Canonical conversation, history, and user_usage schema for DepthAPI.
-- All mode constraints, sequence_id, and PromptSpec columns are included.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- =============================================================================
-- CONVERSATIONS
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.conversations (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid        REFERENCES auth.users(id) ON DELETE CASCADE,  -- nullable: B2B key-scoped
  api_key_id      uuid,       -- FK added in api_keys migration
  title           text,
  mode            text        CHECK (mode IN ('learn', 'technical', 'socratic')),
  settings        jsonb       NOT NULL DEFAULT '{}'::jsonb,
  prompt_spec     jsonb,      -- canonical PromptSpec for this conversation
  created_at      timestamptz NOT NULL DEFAULT NOW(),
  updated_at      timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id
  ON public.conversations(user_id);

CREATE INDEX IF NOT EXISTS idx_conversations_prompt_spec_gin
  ON public.conversations USING gin (prompt_spec);

-- =============================================================================
-- MESSAGES
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.messages (
  id              uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id uuid        NOT NULL REFERENCES public.conversations(id) ON DELETE CASCADE,
  role            text        NOT NULL CHECK (role IN ('user', 'assistant')),
  content         text        NOT NULL,
  attachments     jsonb       NOT NULL DEFAULT '[]'::jsonb,
  metadata        jsonb       NOT NULL DEFAULT '{}'::jsonb,
  sequence_id     bigint,
  created_at      timestamptz NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id_created
  ON public.messages(conversation_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_conversation_sequence_unique
  ON public.messages(conversation_id, sequence_id);

-- =============================================================================
-- HISTORY (PromptSpec-keyed query history)
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.history (
  id          uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid        NOT NULL,
  topic       text        NOT NULL,
  prompt_specs jsonb      NOT NULL DEFAULT '[]'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS history_user_topic_idx
  ON public.history (user_id, topic);

CREATE INDEX IF NOT EXISTS idx_history_prompt_specs_gin
  ON public.history USING gin (prompt_specs);

COMMENT ON COLUMN public.history.prompt_specs IS
  'Array of canonical PromptSpec objects used for generated explanations.';

-- =============================================================================
-- USER_USAGE (retained for any consumer-tier feature; nullable in B2B mode)
-- =============================================================================
CREATE TABLE IF NOT EXISTS public.user_usage (
  user_id                 uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  tier                    text NOT NULL DEFAULT 'free' CHECK (tier IN ('free','pro')),
  prompts_today           integer NOT NULL DEFAULT 0,
  last_reset_date         date NOT NULL DEFAULT CURRENT_DATE,
  payment_subscription_id text,
  created_at              timestamptz NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- RLS
-- =============================================================================
ALTER TABLE public.conversations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages       ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.user_usage     ENABLE ROW LEVEL SECURITY;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='conversations' AND policyname='conversations_user_access'
  ) THEN
    CREATE POLICY conversations_user_access ON public.conversations
      FOR ALL USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='messages' AND policyname='messages_user_access'
  ) THEN
    CREATE POLICY messages_user_access ON public.messages
      FOR ALL
      USING (EXISTS (
        SELECT 1 FROM public.conversations c
        WHERE c.id = messages.conversation_id AND c.user_id = auth.uid()
      ))
      WITH CHECK (EXISTS (
        SELECT 1 FROM public.conversations c
        WHERE c.id = messages.conversation_id AND c.user_id = auth.uid()
      ));
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='user_usage' AND policyname='user_usage_select'
  ) THEN
    CREATE POLICY user_usage_select ON public.user_usage FOR SELECT USING (user_id = auth.uid());
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies WHERE schemaname='public' AND tablename='user_usage' AND policyname='user_usage_update'
  ) THEN
    CREATE POLICY user_usage_update ON public.user_usage FOR UPDATE
      USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
  END IF;
END $$;

-- =============================================================================
-- TRIGGERS
-- =============================================================================
CREATE OR REPLACE FUNCTION public.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS update_conversations_updated_at ON public.conversations;
CREATE TRIGGER update_conversations_updated_at
  BEFORE UPDATE ON public.conversations
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();

-- =============================================================================
-- RPCs
-- =============================================================================

-- Upsert history entry (merges prompt_specs arrays)
CREATE OR REPLACE FUNCTION public.upsert_history(
  p_user_id     uuid,
  p_topic       text,
  p_prompt_specs jsonb DEFAULT '[]'::jsonb
) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO public.history (user_id, topic, prompt_specs)
  VALUES (p_user_id, p_topic, COALESCE(p_prompt_specs, '[]'::jsonb))
  ON CONFLICT (user_id, topic) DO UPDATE
    SET prompt_specs = (
      SELECT COALESCE(jsonb_agg(DISTINCT spec), '[]'::jsonb)
      FROM jsonb_array_elements(
        COALESCE(public.history.prompt_specs, '[]'::jsonb)
        || COALESCE(EXCLUDED.prompt_specs, '[]'::jsonb)
      ) AS spec
    );
END;
$$;

-- Insert user+assistant message pair and update conversation in one round-trip
CREATE OR REPLACE FUNCTION public.insert_message_bundle(
  p_conversation_id    uuid,
  p_user_content       text,
  p_user_metadata      jsonb,
  p_assistant_metadata jsonb,
  p_update_payload     jsonb
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
  v_assistant_id uuid;
BEGIN
  INSERT INTO public.messages (conversation_id, role, content, metadata)
  VALUES (p_conversation_id, 'user', p_user_content, COALESCE(p_user_metadata, '{}'::jsonb));

  UPDATE public.conversations SET
    mode        = COALESCE(p_update_payload->>'mode', mode),
    settings    = COALESCE(p_update_payload->'settings', settings),
    prompt_spec = COALESCE(p_update_payload->'prompt_spec', prompt_spec),
    updated_at  = COALESCE((p_update_payload->>'updated_at')::timestamptz, updated_at)
  WHERE id = p_conversation_id;

  INSERT INTO public.messages (conversation_id, role, content, metadata)
  VALUES (p_conversation_id, 'assistant', '', COALESCE(p_assistant_metadata, '{}'::jsonb))
  RETURNING id INTO v_assistant_id;

  RETURN v_assistant_id;
END;
$$;
