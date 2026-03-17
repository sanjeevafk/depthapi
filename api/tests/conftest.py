import types
from types import SimpleNamespace
import pytest
import httpx

import main as main_app
import api.main as api_main_app
import config as config_module
import auth as auth_module
import services.cache as cache_module
import services.search as search_module
import services.llm_client as llm_client_module
import services.inference as inference_module
import services.ensemble as ensemble_module


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


@pytest.fixture(scope="session")
def test_settings():
    return SimpleNamespace(
        environment="development",
        groq_api_key="",
        gemini_api_key="",
        gemini_primary_model="gemini-2.5-pro",
        gemini_fallback_model="gemini-2.5-flash",
        huggingface_fallback_model="deepseek-ai/DeepSeek-R1",
        huggingface_secondary_model="microsoft/phi-4",
        huggingface_judge_model="MiniMaxAI/MiniMax-M2.5",
        openrouter_api_key="",
        openrouter_model="qwen/qwen3.5-9b",
        openrouter_fallback_model="anthropic/claude-sonnet-4.6",
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
    if hasattr(main_app, "get_settings"):
        monkeypatch.setattr(main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(api_main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(auth_module, "get_settings", lambda: test_settings)
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

    monkeypatch.setattr(cache_module, "get_redis", lambda: dummy_redis)
    monkeypatch.setattr(api_main_app, "get_redis", lambda: dummy_redis)
    monkeypatch.setattr(api_main_app, "close_redis", _noop_close)
    monkeypatch.setattr(api_main_app, "rate_limiter", None)
    monkeypatch.setattr(api_main_app, "redis_available", False)
    main_app.app.dependency_overrides = {}

    transport = httpx.ASGITransport(app=main_app.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        client.app = main_app.app
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
