  -- RPC to fetch assistant message + conversation + nearest prompt message in one round-trip.

  CREATE OR REPLACE FUNCTION public.fetch_share_response_bundle(
    p_message_id uuid,
    p_owner_id uuid
  ) RETURNS jsonb
  LANGUAGE plpgsql
  AS $$
  DECLARE
    v_message record;
    v_conversation record;
    v_prompt record;
  BEGIN
    SELECT id, conversation_id, role, content, metadata, created_at
    INTO v_message
    FROM messages
    WHERE id = p_message_id
    LIMIT 1;

    IF v_message.id IS NULL THEN
      RETURN NULL;
    END IF;

    SELECT id, user_id, title, mode
    INTO v_conversation
    FROM conversations
    WHERE id = v_message.conversation_id
    LIMIT 1;

    IF v_conversation.id IS NULL OR v_conversation.user_id <> p_owner_id THEN
      RETURN NULL;
    END IF;

    SELECT id, content, created_at
    INTO v_prompt
    FROM messages
    WHERE conversation_id = v_message.conversation_id
      AND role = 'user'
      AND created_at <= v_message.created_at
    ORDER BY created_at DESC
    LIMIT 1;

    RETURN jsonb_build_object(
      'message', to_jsonb(v_message),
      'conversation', to_jsonb(v_conversation),
      'prompt', to_jsonb(v_prompt)
    );
  END;
  $$;
