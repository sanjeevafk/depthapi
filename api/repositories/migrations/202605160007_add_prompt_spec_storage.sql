-- Canonical PromptSpec storage.
-- Beta cutover: prompt history is no longer keyed by legacy mode/levels.

CREATE TABLE IF NOT EXISTS public.history (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id uuid NOT NULL,
  topic text NOT NULL,
  prompt_specs jsonb NOT NULL DEFAULT '[]'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.history
  ADD COLUMN IF NOT EXISTS prompt_specs jsonb NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name = 'history'
      AND column_name = 'levels'
  ) THEN
    UPDATE public.history
    SET prompt_specs = (
      SELECT COALESCE(
        jsonb_agg(
          jsonb_build_object(
            'topic', public.history.topic,
            'depth', level_value,
            'task', 'explain',
            'reasoning', 'direct',
            'style', 'normal',
            'capabilities', '[]'::jsonb
          )
        ),
        '[]'::jsonb
      )
      FROM unnest(COALESCE(public.history.levels, '{}'::text[])) AS level_value
    )
    WHERE prompt_specs = '[]'::jsonb
      AND COALESCE(array_length(levels, 1), 0) > 0;
  END IF;
END
$$;

DROP FUNCTION IF EXISTS public.upsert_history(uuid, text, text, text[]);
DROP FUNCTION IF EXISTS public.upsert_history(uuid, text, text, text[], jsonb);
DROP INDEX IF EXISTS public.history_user_topic_mode_idx;

ALTER TABLE public.history
  DROP COLUMN IF EXISTS levels,
  DROP COLUMN IF EXISTS mode;

CREATE UNIQUE INDEX IF NOT EXISTS history_user_topic_idx
  ON public.history (user_id, topic);

CREATE INDEX IF NOT EXISTS idx_history_prompt_specs_gin
  ON public.history USING gin (prompt_specs);

COMMENT ON COLUMN public.history.prompt_specs IS
  'Array of canonical PromptSpec objects used for generated explanations.';

CREATE OR REPLACE FUNCTION public.upsert_history(
  p_user_id uuid,
  p_topic text,
  p_prompt_specs jsonb DEFAULT '[]'::jsonb
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO public.history (user_id, topic, prompt_specs)
  VALUES (
    p_user_id,
    p_topic,
    COALESCE(p_prompt_specs, '[]'::jsonb)
  )
  ON CONFLICT (user_id, topic)
  DO UPDATE
    SET prompt_specs = (
      SELECT COALESCE(jsonb_agg(DISTINCT spec), '[]'::jsonb)
      FROM jsonb_array_elements(
        COALESCE(public.history.prompt_specs, '[]'::jsonb)
        || COALESCE(EXCLUDED.prompt_specs, '[]'::jsonb)
      ) AS spec
    );
END;
$$;

ALTER TABLE public.conversations
  ADD COLUMN IF NOT EXISTS prompt_spec jsonb;

CREATE INDEX IF NOT EXISTS idx_conversations_prompt_spec_gin
  ON public.conversations USING gin (prompt_spec);

COMMENT ON COLUMN public.conversations.prompt_spec IS
  'Optional default canonical PromptSpec object for this conversation.';
