-- Allow conversation shares without a message_id

ALTER TABLE shared_responses
  ALTER COLUMN message_id DROP NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'shared_responses_message_required_check'
  ) THEN
    ALTER TABLE shared_responses
      ADD CONSTRAINT shared_responses_message_required_check
      CHECK (
        (share_kind = 'response' AND message_id IS NOT NULL)
        OR (share_kind = 'conversation' AND message_id IS NULL)
      );
  END IF;
END
$$;
