-- Add sequence_id for strict ordering
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS sequence_id bigint;

CREATE INDEX IF NOT EXISTS idx_messages_conversation_sequence
  ON messages(conversation_id, sequence_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_conversation_sequence_unique
  ON messages(conversation_id, sequence_id);
