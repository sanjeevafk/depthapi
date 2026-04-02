-- History upsert helper and unique index for conflict target
-- Safe to run multiple times.

CREATE UNIQUE INDEX IF NOT EXISTS history_user_topic_mode_idx
    ON history (user_id, topic, mode);

CREATE OR REPLACE FUNCTION upsert_history(
    p_user_id uuid,
    p_topic text,
    p_mode text,
    p_levels text[]
) RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    INSERT INTO history (user_id, topic, mode, levels)
    VALUES (p_user_id, p_topic, p_mode, p_levels)
    ON CONFLICT (user_id, topic, mode)
    DO UPDATE
        SET levels = (
            SELECT ARRAY(
                SELECT DISTINCT UNNEST(COALESCE(history.levels, '{}'::text[]) || COALESCE(EXCLUDED.levels, '{}'::text[]))
            )
        ),
        mode = EXCLUDED.mode;
END;
$$;
