-- History upsert helper and unique index for canonical PromptSpec history.
-- Safe to run multiple times.

ALTER TABLE public.history
    ADD COLUMN IF NOT EXISTS prompt_specs jsonb NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE public.history
    DROP COLUMN IF EXISTS levels,
    DROP COLUMN IF EXISTS mode;

DROP INDEX IF EXISTS public.history_user_topic_mode_idx;

CREATE UNIQUE INDEX IF NOT EXISTS history_user_topic_idx
    ON public.history (user_id, topic);

CREATE OR REPLACE FUNCTION public.upsert_history(
    p_user_id uuid,
    p_topic text,
    p_prompt_specs jsonb DEFAULT '[]'::jsonb
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO public.history (user_id, topic, prompt_specs)
    VALUES (p_user_id, p_topic, COALESCE(p_prompt_specs, '[]'::jsonb))
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
