-- Migration: 202604230002_depthapi_legacy_purge.sql
-- Purges legacy consumer web app artifacts and bridges schema to B2B API Key model.

-- 1. DROP ORPHANED TABLES
DROP TABLE IF EXISTS public.shared_responses CASCADE;
DROP TABLE IF EXISTS public.email_logs CASCADE;
DROP TABLE IF EXISTS public.user_usage CASCADE;
DROP TABLE IF EXISTS public.llm_analytics CASCADE;

-- 2. DROP ORPHANED FUNCTIONS & RPCS
DROP FUNCTION IF EXISTS public.increment_shared_response_view CASCADE;
DROP FUNCTION IF EXISTS public.share_response_bundle CASCADE;
DROP FUNCTION IF EXISTS public.message_write_rpc CASCADE;

-- 3. CORE SCHEMA REFACTOR: BRIDGE TO API KEYS
-- We add api_key_id to conversations and history to scope them to projects.
-- We keep user_id as nullable for backward compatibility during the transition.

-- Refactor: conversations
ALTER TABLE public.conversations 
    ADD COLUMN IF NOT EXISTS api_key_id UUID REFERENCES public.api_keys(id) ON DELETE SET NULL;

ALTER TABLE public.conversations 
    ALTER COLUMN user_id DROP NOT NULL;

-- Index for hot-path conversation lookup by API Key
CREATE INDEX IF NOT EXISTS idx_conversations_api_key_id ON public.conversations(api_key_id);

-- Refactor: history (optional if table exists in this environment)
DO $$
BEGIN
    IF to_regclass('public.history') IS NOT NULL THEN
        ALTER TABLE public.history 
            ADD COLUMN IF NOT EXISTS api_key_id UUID REFERENCES public.api_keys(id) ON DELETE SET NULL;

        ALTER TABLE public.history 
            ALTER COLUMN user_id DROP NOT NULL;

        -- Index for history lookup by API Key
        CREATE INDEX IF NOT EXISTS idx_history_api_key_id ON public.history(api_key_id);
    END IF;
END
$$;

-- 4. CLEANUP AUTH TRIGGERS (BEST EFFORT)
-- If there is a trigger on auth.users that initializes legacy rows, we drop it.
DROP TRIGGER IF EXISTS on_auth_user_created ON auth.users;
DROP FUNCTION IF EXISTS public.handle_new_user CASCADE;

COMMENT ON TABLE public.conversations IS 'Core conversation state, scoped to an API Key project.';
DO $$
BEGIN
    IF to_regclass('public.history') IS NOT NULL THEN
        COMMENT ON TABLE public.history IS 'Query history, scoped to an API Key project.';
    END IF;
END
$$;
