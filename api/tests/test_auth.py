import inspect
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from collections import OrderedDict
from types import SimpleNamespace
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import auth as auth_module
from auth import verify_token, verify_token_optional


@pytest.fixture(autouse=True)
def clear_supabase_client_caches():
    if hasattr(auth_module.get_supabase, "cache_clear"):
        auth_module.get_supabase.cache_clear()
    if hasattr(auth_module.get_supabase_admin, "cache_clear"):
        auth_module.get_supabase_admin.cache_clear()
    auth_module._PRO_STATE_CACHE.clear()
    yield
    if hasattr(auth_module.get_supabase, "cache_clear"):
        auth_module.get_supabase.cache_clear()
    if hasattr(auth_module.get_supabase_admin, "cache_clear"):
        auth_module.get_supabase_admin.cache_clear()
    auth_module._PRO_STATE_CACHE.clear()


@pytest.mark.asyncio
async def test_verify_token_valid():
    with patch("auth.get_settings", return_value=SimpleNamespace(supabase_url="https://example.supabase.co")), \
        patch("auth.jwt.get_unverified_header", return_value={"kid": "kid1"}), \
        patch("auth._get_jwks", new=AsyncMock(return_value={"keys": [{"kid": "kid1", "alg": "RS256"}]})), \
        patch("auth.jwt.algorithms.RSAAlgorithm.from_jwk", return_value=object()), \
        patch("auth.jwt.decode", return_value={"sub": "123", "email": "test@example.com", "is_pro": True}):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid_token")
        result = await verify_token(creds)
        assert result["token"] == "valid_token"
        assert result["user"].id == "123"
        assert result["user"].email == "test@example.com"


@pytest.mark.asyncio
async def test_verify_token_hs256_without_kid_uses_jwt_secret():
    settings = SimpleNamespace(
        supabase_url="https://example.supabase.co",
        supabase_jwt_secret="secret",
    )
    with patch("auth.get_settings", return_value=settings), \
        patch("auth.jwt.get_unverified_header", return_value={"alg": "HS256"}), \
        patch("auth.jwt.decode", return_value={"sub": "123", "email": "test@example.com"}):
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid_token")
        result = await verify_token(creds)
        assert result["token"] == "valid_token"
        assert result["user"].id == "123"


@pytest.mark.asyncio
async def test_verify_token_invalid():
    with patch("auth.get_settings", return_value=SimpleNamespace(supabase_url="https://example.supabase.co")), \
        patch("auth.jwt.get_unverified_header", side_effect=Exception("bad header")):
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
    with patch("auth.get_settings", return_value=SimpleNamespace(supabase_url="https://example.supabase.co")), \
        patch("auth.jwt.get_unverified_header", side_effect=Exception("bad header")):
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
            supabase_publishable_key="publishable-key",
            supabase_secret_key="secret-key",
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
            supabase_publishable_key="publishable-key",
            supabase_secret_key="secret-key",
        ),
    )
    monkeypatch.setattr(auth_module, "create_client", fake_create_client)

    first = auth_module.get_supabase_admin()
    second = auth_module.get_supabase_admin()

    assert first is second
    assert len(created) == 1


@pytest.mark.asyncio
async def test_check_is_pro_cache_is_bounded_with_many_users(monkeypatch):
    class FakeSupabaseQuery:
        def __init__(self, owner):
            self.owner = owner
            self.user_id = ""

        def select(self, *_args, **_kwargs):
            return self

        def eq(self, key, value):
            if key == "id":
                self.user_id = str(value)
            return self

        def single(self):
            return self

        def execute(self):
            self.owner.calls += 1
            return SimpleNamespace(data={"is_pro": False})

    class FakeSupabase:
        def __init__(self):
            self.calls = 0

        def table(self, _name):
            return FakeSupabaseQuery(self)

    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    fake_supabase = FakeSupabase()
    monkeypatch.setattr(auth_module, "get_supabase_admin", lambda: fake_supabase)
    monkeypatch.setattr(auth_module.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(
        auth_module,
        "get_settings",
        lambda: SimpleNamespace(
            pro_state_cache_ttl_seconds=30,
            pro_state_cache_max_entries=5,
        ),
    )
    auth_module._PRO_STATE_CACHE = OrderedDict()

    for idx in range(20):
        await auth_module.check_is_pro(f"user-{idx}", force_refresh=False)

    assert len(auth_module._PRO_STATE_CACHE) == 5
    assert list(auth_module._PRO_STATE_CACHE.keys()) == [
        "user-15",
        "user-16",
        "user-17",
        "user-18",
        "user-19",
    ]
    assert fake_supabase.calls == 20
