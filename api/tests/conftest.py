import types
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient

import main as main_app
import config as config_module
import auth as auth_module
import services.cache as cache_module
import services.search as search_module
import services.model_provider as model_provider_module


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

    async def close(self):
        return True


class DummyLimiter:
    @classmethod
    async def init(cls, _redis):
        return None


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


class DummyProvider:
    gemini_configured = False

    async def initialize(self):
        return None

    async def close(self):
        return None

    async def route_inference(self, *args, **_kwargs):
        return {"provider": "dummy", "model": "dummy", "content": "ok"}

    async def route_inference_stream(self, _prompt, **_kwargs):
        yield "ok"


@pytest.fixture(scope="session")
def test_settings():
    return SimpleNamespace(
        environment="development",
        groq_api_key="",
        gemini_api_key="",
        redis_url="redis://localhost:6379",
        cache_ttl=5,
        rate_limit_per_user=20,
        rate_limit_burst=5,
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
    monkeypatch.setattr(main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(auth_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(model_provider_module, "get_settings", lambda: test_settings)
    search_module.settings = test_settings
    return test_settings


@pytest.fixture
def dummy_redis():
    return DummyRedis()


@pytest.fixture
def app_client(monkeypatch, dummy_redis):
    monkeypatch.setattr(cache_module, "get_redis", lambda: dummy_redis)
    monkeypatch.setattr(main_app, "FastAPILimiter", DummyLimiter)
    monkeypatch.setattr(
        model_provider_module.ModelProvider,
        "get_instance",
        classmethod(lambda cls: DummyProvider())
    )
    main_app.redis_available = True
    main_app.app.dependency_overrides = {}
    with TestClient(main_app.app) as client:
        yield client
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
