import types
import pytest

import auth as auth_module


@pytest.mark.asyncio
async def test_analytics_requires_admin(app_client, monkeypatch, fake_supabase):
    async def fake_verify_token():
        return {"user": types.SimpleNamespace(app_metadata={"role": "user"})}

    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = fake_verify_token

    response = await app_client.get("/api/analytics/usage")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_analytics_usage_returns_items(app_client, monkeypatch, fake_supabase):
    async def fake_verify_token():
        return {"user": types.SimpleNamespace(app_metadata={"role": "admin"})}

    fake_supabase.responses["llm_requests"] = [
        {"id": "1", "model_alias": "default-fast", "mode": "learn"}
    ]

    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = fake_verify_token

    response = await app_client.get("/api/analytics/usage")
    assert response.status_code == 200
    payload = response.json()
    assert payload["items"]
    assert payload["total"] >= 1


@pytest.mark.asyncio
async def test_analytics_aggregations(app_client, monkeypatch, fake_supabase):
    async def fake_verify_token():
        return {"user": types.SimpleNamespace(app_metadata={"role": "admin"})}

    fake_supabase.responses["llm_cost_agg"] = [{"bucket_start": "2024-01-01", "total_cost_usd": 1}]
    fake_supabase.responses["llm_latency_agg"] = [{"bucket_start": "2024-01-01", "p95_latency_ms": 120}]
    fake_supabase.responses["llm_error_agg"] = [{"bucket_start": "2024-01-01", "error_rate": 0.1}]
    fake_supabase.responses["llm_top_errors"] = [{"error_type": "timeout", "error_count": 2}]

    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)
    app_client.app.dependency_overrides[auth_module.verify_token] = fake_verify_token

    cost = await app_client.get("/api/analytics/cost")
    latency = await app_client.get("/api/analytics/latency")
    errors = await app_client.get("/api/analytics/errors")

    assert cost.status_code == 200
    assert latency.status_code == 200
    assert errors.status_code == 200
    assert cost.json()["items"]
    assert latency.json()["items"]
    assert errors.json()["items"]
    assert errors.json()["top_errors"]
