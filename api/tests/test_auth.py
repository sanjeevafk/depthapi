import pytest
from unittest.mock import MagicMock, patch
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from auth import verify_token, verify_token_optional


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
        await verify_token(None)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_verify_token_optional_missing_returns_none():
    result = await verify_token_optional(None)
    assert result is None
