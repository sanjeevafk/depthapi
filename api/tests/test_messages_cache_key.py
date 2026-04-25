from api.routers.messages_helpers import message_cache_key as _message_cache_key


def test_message_cache_key_scoped_to_conversation_and_user():
    base_args = {
        "content": "hello",
        "mode": "chat",
        "prompt_mode": "eli5",
        "temperature": 0.7,
        "model_alias": "test-model",
        "system_prompt": "system",
        "context_signature": "ctx",
        "intent_type": "",
        "intent_payload": "",
    }

    key_a = _message_cache_key(**base_args, conversation_id="c1", user_id="u1")
    key_b = _message_cache_key(**base_args, conversation_id="c2", user_id="u1")
    key_c = _message_cache_key(**base_args, conversation_id="c1", user_id="u2")

    assert key_a != key_b
    assert key_a != key_c
