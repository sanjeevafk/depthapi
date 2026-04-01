-- Add conversation share support

ALTER TABLE shared_responses
  ADD COLUMN IF NOT EXISTS share_kind text NOT NULL DEFAULT 'response',
  ADD COLUMN IF NOT EXISTS snapshot_messages jsonb NOT NULL DEFAULT '[]'::jsonb;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'shared_responses_share_kind_check'
  ) THEN
    ALTER TABLE shared_responses
      ADD CONSTRAINT shared_responses_share_kind_check
      CHECK (share_kind IN ('response', 'conversation'));
  END IF;
END
$$;
