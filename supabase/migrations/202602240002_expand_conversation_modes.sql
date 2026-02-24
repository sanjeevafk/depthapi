-- Expand supported conversation modes for chat v2

ALTER TABLE conversations
  DROP CONSTRAINT IF EXISTS conversations_mode_check;

ALTER TABLE conversations
  ADD CONSTRAINT conversations_mode_check
  CHECK (
    mode IN (
      'eli5',
      'eli10',
      'eli12',
      'eli15',
      'meme-style',
      'classic60',
      'gentle70',
      'warm80',
      'ensemble',
      'technical-depth',
      'socratic',
      'technical',
      'meme'
    )
  );
