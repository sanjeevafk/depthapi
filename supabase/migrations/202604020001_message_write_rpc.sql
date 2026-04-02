-- RPC to insert user + assistant messages and update conversation in one round-trip.

CREATE OR REPLACE FUNCTION public.insert_message_bundle(
  p_conversation_id uuid,
  p_user_content text,
  p_user_metadata jsonb,
  p_assistant_metadata jsonb,
  p_update_payload jsonb
) RETURNS uuid
LANGUAGE plpgsql
AS $$
DECLARE
  v_assistant_id uuid;
BEGIN
  INSERT INTO messages (conversation_id, role, content, metadata)
  VALUES (
    p_conversation_id,
    'user',
    p_user_content,
    COALESCE(p_user_metadata, '{}'::jsonb)
  );

  UPDATE conversations
  SET
    mode = COALESCE(p_update_payload->>'mode', mode),
    settings = COALESCE(p_update_payload->'settings', settings),
    updated_at = COALESCE((p_update_payload->>'updated_at')::timestamptz, updated_at)
  WHERE id = p_conversation_id;

  INSERT INTO messages (conversation_id, role, content, metadata)
  VALUES (
    p_conversation_id,
    'assistant',
    '',
    COALESCE(p_assistant_metadata, '{}'::jsonb)
  )
  RETURNING id INTO v_assistant_id;

  RETURN v_assistant_id;
END;
$$;
