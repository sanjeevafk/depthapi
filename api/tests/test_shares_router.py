import auth as auth_module
import services.share_manager as share_manager


async def test_create_and_fetch_share(app_client, monkeypatch, fake_user, fake_supabase):
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth
    app_client.app.dependency_overrides[auth_module.verify_token_optional] = fake_auth
    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(share_manager, "ensure_unique_token", lambda _sb: "token-123")

    message_id = "11111111-1111-1111-1111-111111111111"
    conversation_id = "22222222-2222-2222-2222-222222222222"

    fake_supabase.responses["messages"] = [
        {
            "id": message_id,
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "Shared response",
            "metadata": {},
            "created_at": "2026-04-01T10:00:00+00:00",
        }
    ]
    fake_supabase.responses["conversations"] = [
        {
            "id": conversation_id,
            "user_id": fake_user.id,
            "title": "Test Conversation",
            "mode": "eli5",
        }
    ]
    fake_supabase.responses["shared_responses"] = [
        {
            "id": "33333333-3333-3333-3333-333333333333",
            "share_token": "token-123",
            "access_level": "public",
            "share_kind": "response",
            "prompt_text": "Prompt",
            "response_text": "Shared response",
            "metadata": {},
            "snapshot_messages": [],
            "created_at": "2026-04-01T10:00:00+00:00",
            "view_count": 0,
            "owner_id": fake_user.id,
        }
    ]

    response = await app_client.post(
        "/api/shares",
        json={"message_id": message_id, "access_level": "public"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["share_token"] == "token-123"
    assert payload["share_url"].endswith("/share/token-123")

    fetch_response = await app_client.get("/api/shares/token-123")
    assert fetch_response.status_code == 200
    fetch_payload = fetch_response.json()
    assert fetch_payload["response_text"] == "Shared response"
    assert fetch_payload["share_kind"] == "response"
    assert any(call[0] == "increment_shared_response_view" for call in fake_supabase.rpcs)


async def test_revoke_share(app_client, monkeypatch, fake_user, fake_supabase):
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth
    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)

    share_id = "44444444-4444-4444-4444-444444444444"
    fake_supabase.responses["shared_responses"] = [
        {
            "id": share_id,
            "owner_id": fake_user.id,
            "access_level": "public",
            "share_kind": "response",
        }
    ]

    response = await app_client.post(f"/api/shares/{share_id}/revoke")
    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert "shared_responses" in fake_supabase.deletes


async def test_create_conversation_share(app_client, monkeypatch, fake_user, fake_supabase):
    async def fake_auth():
        return {"user": fake_user}

    app_client.app.dependency_overrides[auth_module.verify_token] = fake_auth
    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(share_manager, "ensure_unique_token", lambda _sb: "token-456")

    conversation_id = "55555555-5555-5555-5555-555555555555"

    fake_supabase.responses["conversations"] = [
        {
            "id": conversation_id,
            "user_id": fake_user.id,
            "title": "Snapshot Conversation",
            "mode": "eli5",
        }
    ]
    fake_supabase.responses["messages"] = [
        {
            "id": "msg-1",
            "role": "user",
            "content": "Hello",
            "created_at": "2026-04-01T10:00:00+00:00",
        },
        {
            "id": "msg-2",
            "role": "assistant",
            "content": "Hi there",
            "created_at": "2026-04-01T10:00:05+00:00",
        },
    ]
    fake_supabase.responses["shared_responses"] = [
        {
            "id": "66666666-6666-6666-6666-666666666666",
            "share_token": "token-456",
            "access_level": "public",
            "share_kind": "conversation",
            "prompt_text": "",
            "response_text": "",
            "snapshot_messages": fake_supabase.responses["messages"],
            "metadata": {},
            "created_at": "2026-04-01T10:10:00+00:00",
            "view_count": 0,
            "owner_id": fake_user.id,
        }
    ]

    response = await app_client.post(
        "/api/shares",
        json={
            "conversation_id": conversation_id,
            "share_kind": "conversation",
            "access_level": "public",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["share_token"] == "token-456"
