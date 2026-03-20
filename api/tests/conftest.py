import os
import types
from types import SimpleNamespace
import pytest
import httpx

os.environ.setdefault("LOG_USER_HASH_SALT", "test-log-salt")

import main as main_app
import api.main as api_main_app
import config as config_module
import auth as auth_module
import services.cache as cache_module
import services.search as search_module
import services.llm_client as llm_client_module
import services.inference as inference_module
import services.ensemble as ensemble_module
import services.rate_limit as rate_limit_module


class AppClientWrapper:
    """Expose FastAPI app for dependency overrides while delegating to AsyncClient."""

    def __init__(self, client: httpx.AsyncClient, app):
        self._client = client
        self.app = app

    def __getattr__(self, name):
        return getattr(self._client, name)


class DummyRedis:
    def __init__(self):
        self.store = {}

    async def ping(self):
        return True

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def set_if_not_exists(self, key, ttl, value):
        if key in self.store:
            return False
        self.store[key] = value
        return True

    async def incr(self, key):
        value = int(self.store.get(key, 0)) + 1
        self.store[key] = value
        return value

    async def incrby(self, key, amount):
        value = int(self.store.get(key, 0)) + int(amount)
        self.store[key] = value
        return value

    async def expire(self, key, ttl_seconds):
        return True

    async def ttl(self, key):
        return 60

    async def eval(self, _script, _num_keys, key, requested, limit, window_seconds):
        current = int(self.store.get(key, 0))
        requested_value = int(requested)
        limit_value = int(limit)
        window_value = int(window_seconds)

        consumed = current + requested_value
        if consumed > limit_value:
            return [0, current, window_value]

        self.store[key] = consumed
        return [1, consumed, window_value]

    async def close(self):
        return True


class FakeSupabaseQuery:
    def __init__(self, supabase, table):
        self.supabase = supabase
        self.table = table
        self._response = None

    def select(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self.supabase.inserts.append((self.table, payload))
        return self

    def update(self, payload):
        self.supabase.updates.append((self.table, payload))
        if self._response is None:
            self._response = [{"id": "stub"}]
        return self

    def delete(self):
        self.supabase.deletes.append(self.table)
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def single(self):
        return self

    def execute(self):
        if self._response is not None:
            return SimpleNamespace(data=self._response)
        return SimpleNamespace(data=self.supabase.responses.get(self.table, []))


class FakeSupabase:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.inserts = []
        self.updates = []
        self.deletes = []

    def table(self, table):
        return FakeSupabaseQuery(self, table)


@pytest.fixture(scope="session")
def test_settings():
    return SimpleNamespace(
        environment="development",
        litellm_base_url="http://localhost:4000",
        litellm_virtual_key="test-virtual-key",
        litellm_master_key="",
        litellm_timeout_seconds=60,
        stream_max_seconds=5,
        stream_heartbeat_seconds=1,
        stream_start_timeout_seconds=1,
        stream_idempotency_ttl_seconds=90,
        redis_url="redis://localhost:6379",
        upstash_redis_rest_url="https://upstash.example.com",
        upstash_redis_rest_token="token",
        cache_ttl=5,
        rate_limit_strategy="upstash_redis",
        rate_limit_per_user=20,
        rate_limit_burst=5,
        rate_limit_burst_window_seconds=10,
        rate_limit_sustained_window_seconds=60,
        anonymous_rate_limit_per_ip=8,
        anonymous_rate_limit_burst=3,
        anonymous_rate_limit_window_seconds=60,
        daily_token_quota_per_user=50000,
        quota_window_seconds=86400,
        circuit_breaker_tokens_per_minute=300000,
        circuit_breaker_open_seconds=60,
        circuit_breaker_action="reject",
        estimated_output_tokens_per_request=900,
        supabase_url="https://example.supabase.co",
        supabase_anon_key="anon",
        supabase_service_role_key="service",
        tavily_api_key="",
        serper_api_key="",
        exa_api_key="",
        dodo_api_key="",
        dodo_webhook_secret="",
        dodo_webhook_endpoint="",
        dodo_webhook_url="",
        dodo_payment_link_id="pay_123"
    )


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, test_settings):
    monkeypatch.setattr(config_module, "get_settings", lambda: test_settings)
    if hasattr(main_app, "get_settings"):
        monkeypatch.setattr(main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(api_main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(auth_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(llm_client_module, "get_settings", lambda: test_settings)
    search_module.settings = test_settings
    return test_settings


@pytest.fixture(autouse=True)
def patch_llm_client(monkeypatch):
    class DummyChoice:
        def __init__(self, content: str):
            self.message = type("Msg", (), {"content": content})

    class DummyResponse:
        def __init__(self, content: str, model: str):
            self.choices = [DummyChoice(content)]
            self.model = model
            self.usage = None

    async def fake_create_chat_completion(model, _messages, **_kwargs):
        return DummyResponse("ok", model)

    async def fake_stream_chat_completion(_model, _messages, **_kwargs):
        yield "ok"

    monkeypatch.setattr(llm_client_module, "create_chat_completion", fake_create_chat_completion)
    monkeypatch.setattr(llm_client_module, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(inference_module, "create_chat_completion", fake_create_chat_completion)
    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(ensemble_module, "create_chat_completion", fake_create_chat_completion)


@pytest.fixture(autouse=True)
def patch_asyncio_to_thread(monkeypatch):
    async def fake_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(auth_module.asyncio, "to_thread", fake_to_thread)


@pytest.fixture
def dummy_redis():
    return DummyRedis()


@pytest.fixture
async def app_client(monkeypatch, dummy_redis):
    async def _noop_close():
        return None

    async def _get_redis():
        return dummy_redis

    monkeypatch.setattr(cache_module, "get_redis", _get_redis)
    monkeypatch.setattr(rate_limit_module, "get_redis", _get_redis)
    monkeypatch.setattr(api_main_app, "get_redis", _get_redis)
    monkeypatch.setattr(api_main_app, "close_redis", _noop_close)
    monkeypatch.setattr(api_main_app, "redis_available", False)
    main_app.app.dependency_overrides = {}

    transport = httpx.ASGITransport(app=main_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield AppClientWrapper(client, main_app.app)

    main_app.app.dependency_overrides = {}


@pytest.fixture
def fake_user():
    return SimpleNamespace(
        id="user-123",
        email="user@example.com",
        user_metadata={"full_name": "Test User", "avatar_url": "https://example.com/avatar.png"}
    )


@pytest.fixture
def fake_supabase():
    return FakeSupabase()
