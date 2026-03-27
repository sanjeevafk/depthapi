import inspect
import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import auth as auth_module
from auth import verify_token, verify_token_optional


@pytest.fixture(autouse=True)
def clear_supabase_client_caches():
    auth_module.get_supabase.cache_clear()
    auth_module.get_supabase_admin.cache_clear()
    yield
    auth_module.get_supabase.cache_clear()
    auth_module.get_supabase_admin.cache_clear()


@pytest.mark.asyncio
async def test_verify_token_valid():
    mock_supabase = MagicMock()
    mock_user = MagicMock()
    mock_user.user = {"id": "123", "email": "test@example.com"}
    mock_supabase.auth.get_user.return_value = mock_user

    with patch("auth.get_supabase", return_value=mock_supabase):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid_token")
        result = await verify_token(creds)
        assert result["token"] == "valid_token"
        assert result["user"] == {"id": "123", "email": "test@example.com"}


@pytest.mark.asyncio
async def test_verify_token_invalid():
    mock_supabase = MagicMock()
    mock_supabase.auth.get_user.return_value = MagicMock(user=None)

    with patch("auth.get_supabase", return_value=mock_supabase):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid_token")
        with pytest.raises(HTTPException) as excinfo:
            await verify_token(creds)
        assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_token_missing_credentials():
    with pytest.raises(HTTPException) as excinfo:
        await verify_token(None)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_token_optional_missing_returns_none():
    result = await verify_token_optional(None)  # type: ignore[arg-type]
    assert result is None


@pytest.mark.asyncio
async def test_verify_token_optional_invalid_credentials_raise():
    mock_supabase = MagicMock()
    mock_supabase.auth.get_user.return_value = MagicMock(user=None)

    with patch("auth.get_supabase", return_value=mock_supabase):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid_token")
        with pytest.raises(HTTPException) as excinfo:
            await verify_token_optional(creds)
        assert excinfo.value.status_code == 401


def test_auth_module_has_no_print_statements():
    assert "print(" not in inspect.getsource(auth_module)


def test_get_supabase_reuses_cached_client(monkeypatch):
    created = []

    def fake_create_client(url, key):
        created.append((url, key))
        return object()

    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon-key",
            supabase_service_role_key="service-key",
        ),
    )
    monkeypatch.setattr(auth_module, "create_client", fake_create_client)

    first = auth_module.get_supabase()
    second = auth_module.get_supabase()

    assert first is second
    assert len(created) == 1


def test_get_supabase_admin_reuses_cached_client(monkeypatch):
    created = []

    def fake_create_client(url, key):
        created.append((url, key))
        return object()

    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: SimpleNamespace(
            supabase_url="https://example.supabase.co",
            supabase_anon_key="anon-key",
            supabase_service_role_key="service-key",
        ),
    )
    monkeypatch.setattr(auth_module, "create_client", fake_create_client)

    first = auth_module.get_supabase_admin()
    second = auth_module.get_supabase_admin()

    assert first is second
    assert len(created) == 1
