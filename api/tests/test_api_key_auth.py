from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
import pytest

import api.services.security.api_key_auth as auth_module
from api.config import reinitialize_cache
from api.services.security.api_key_auth import ApiKeyRecord


def _bearer(raw_key: str) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=raw_key)


@pytest.mark.asyncio
async def test_verify_api_key_uses_env_provider_when_dev_keys_configured(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER_MODE", "env")
    monkeypatch.setenv("DEV_API_KEYS", "sk-depth-local-dev")
    reinitialize_cache()

    async def fake_cache_get(_key_hash):
        return None

    cached_records: list[ApiKeyRecord] = []

    async def fake_cache_set(_key_hash, record):
        cached_records.append(record)

    async def fail_lookup(_key_hash):
        raise AssertionError("Supabase lookup should not run when env auth is enabled")

    monkeypatch.setattr(auth_module, "_cache_get", fake_cache_get)
    monkeypatch.setattr(auth_module, "_cache_set", fake_cache_set)
    monkeypatch.setattr(auth_module, "_lookup_in_db", fail_lookup)

    record = await auth_module.verify_api_key(_bearer("sk-depth-local-dev"))

    assert record.project_name == "Local Development"
    assert record.plan == "enterprise"
    assert record.id.startswith("00000000-0000-")
    assert cached_records and cached_records[0].id == record.id


@pytest.mark.asyncio
async def test_verify_api_key_falls_back_to_supabase_provider_in_auto_mode(monkeypatch):
    monkeypatch.delenv("DEV_API_KEYS", raising=False)
    monkeypatch.delenv("DEPTHAPI_API_KEYS", raising=False)
    monkeypatch.setenv("AUTH_PROVIDER_MODE", "auto")
    reinitialize_cache()

    expected = ApiKeyRecord(
        id="db-key-1",
        prefix="sk-depth-db",
        project_name="Database Project",
        owner_email="db@example.com",
        plan="pro",
        monthly_token_budget=10_000_000,
        requests_per_minute=100,
    )

    async def fake_cache_get(_key_hash):
        return None

    async def fake_cache_set(_key_hash, _record):
        return None

    async def fake_lookup(_key_hash):
        return expected

    monkeypatch.setattr(auth_module, "_cache_get", fake_cache_get)
    monkeypatch.setattr(auth_module, "_cache_set", fake_cache_set)
    monkeypatch.setattr(auth_module, "_lookup_in_db", fake_lookup)

    record = await auth_module.verify_api_key(_bearer("sk-depth-db-key"))

    assert record == expected


@pytest.mark.asyncio
async def test_verify_api_key_rejects_unknown_env_key(monkeypatch):
    monkeypatch.setenv("AUTH_PROVIDER_MODE", "env")
    monkeypatch.setenv("DEPTHAPI_API_KEYS", "sk-depth-allowed")
    monkeypatch.delenv("DEV_API_KEYS", raising=False)
    reinitialize_cache()

    async def fake_cache_get(_key_hash):
        return None

    async def fake_cache_set(_key_hash, _record):
        return None

    monkeypatch.setattr(auth_module, "_cache_get", fake_cache_get)
    monkeypatch.setattr(auth_module, "_cache_set", fake_cache_set)

    with pytest.raises(HTTPException) as exc_info:
        await auth_module.verify_api_key(_bearer("sk-depth-denied"))

    assert exc_info.value.status_code == 401
