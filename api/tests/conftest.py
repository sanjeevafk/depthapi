import sys
import json
from pathlib import Path
import base64
import os
import time
import types
from types import SimpleNamespace
import pytest
import httpx

# Add parent directory to path so 'api' module is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

os.environ.setdefault("LOG_USER_HASH_SALT", "test-log-salt")
_RUN_REAL_PROVIDER_TESTS = os.getenv("RUN_REAL_PROVIDER_TESTS", "").strip() == "1"

import main as main_app
import api.main as api_main_app
import config as config_module
import auth as auth_module
import services.cache as cache_module
import services.search as search_module
import services.llm_client as llm_client_module
import services.inference as inference_module
import services.rate_limit as rate_limit_module
import services.message_gate as message_gate_module
import services.conversation_cache as conversation_cache_module
import services.user_cache as user_cache_module


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

    async def delete(self, key):
        if key in self.store:
            del self.store[key]
            return 1
        return 0

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

    async def rpush(self, key, *values):
        lst = self.store.get(key)
        if not isinstance(lst, list):
            lst = []
        lst.extend(list(values))
        self.store[key] = lst
        return len(lst)

    async def ltrim(self, key, start, stop):
        lst = self.store.get(key)
        if not isinstance(lst, list):
            return True
        length = len(lst)
        if start < 0:
            start = max(length + start, 0)
        if stop < 0:
            stop = length + stop
        self.store[key] = lst[start: stop + 1]
        return True

    async def lrange(self, key, start, stop):
        lst = self.store.get(key)
        if not isinstance(lst, list):
            return []
        length = len(lst)
        if start < 0:
            start = max(length + start, 0)
        if stop < 0:
            stop = length + stop
        return lst[start: stop + 1]

    async def hget(self, key, field):
        h = self.store.get(key)
        if not isinstance(h, dict):
            return None
        return h.get(field)

    async def hset(self, key, field, value):
        h = self.store.get(key)
        if not isinstance(h, dict):
            h = {}
        h[field] = value
        self.store[key] = h
        return 1

    async def hgetall(self, key):
        h = self.store.get(key)
        if not isinstance(h, dict):
            return {}
        return dict(h)

    async def pipeline(self, commands):
        results = []
        for command in commands:
            op = str(command[0]).upper()
            if op == "DEL":
                results.append(await self.delete(command[1]))
            elif op == "SETEX":
                results.append(await self.setex(command[1], int(command[2]), command[3]))
            elif op == "RPUSH":
                results.append(await self.rpush(command[1], *command[2:]))
            elif op == "LTRIM":
                results.append(await self.ltrim(command[1], int(command[2]), int(command[3])))
            else:
                results.append(None)
        return results

    async def eval(self, script, _num_keys, *args):
        script_text = str(script)
        if "unified_idempotency_cache" in script_text:
            keys = list(args[:_num_keys])
            argv = list(args[_num_keys:])
            idempotency_key = str(keys[0])
            cache_key = str(keys[1])
            now_ts = int(argv[0])
            idempotency_ttl = int(argv[1])
            idempotency_stale = int(argv[2])
            set_in_progress = int(argv[3])
            check_cache = int(argv[4])

            raw = self.store.get(idempotency_key)
            if isinstance(raw, str):
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = None
                if isinstance(payload, dict):
                    status = payload.get("status")
                    if status == "completed" and payload.get("response"):
                        return [1, payload.get("response")]
                    if status == "in_progress":
                        started_at = int(payload.get("started_at") or now_ts)
                        if now_ts - started_at < idempotency_stale:
                            return [2, ""]

            if check_cache == 1:
                cached = self.store.get(cache_key)
                if cached is not None:
                    return [3, cached]

            if set_in_progress == 1:
                payload = json.dumps({"status": "in_progress", "started_at": now_ts})
                self.store[idempotency_key] = payload
            return [0, ""]
        if "meta_key" in script_text and "list_key" in script_text and "RPUSH" in script_text:
            meta_key = str(args[0])
            list_key = str(args[1])
            meta_json = args[2]
            ttl = int(args[3])
            max_messages = int(args[4])
            payloads = list(args[5:])

            await self.delete(list_key)
            if ttl > 0:
                await self.setex(meta_key, ttl, meta_json)
            else:
                self.store[meta_key] = meta_json

            if payloads:
                await self.rpush(list_key, *payloads)
                if max_messages > 0:
                    await self.ltrim(list_key, -max_messages, -1)
                if ttl > 0:
                    await self.expire(list_key, ttl)
            return 1
        if "unified_controls" in script_text:
            keys = list(args[:_num_keys])
            argv = list(args[_num_keys:])
            burst_key = str(keys[0])
            sustained_key = str(keys[1])
            daily_key = str(keys[2])
            hourly_key = str(keys[3])
            circuit_open_key = str(keys[4])
            circuit_usage_key = str(keys[5])

            burst_limit = int(argv[0])
            sustained_limit = int(argv[1])
            daily_limit = int(argv[2])
            hourly_limit = int(argv[3])
            circuit_threshold = int(argv[4])
            requested_tokens = int(argv[5])
            now_minute = int(argv[6])
            burst_window = int(argv[7])
            sustained_window = int(argv[8])
            daily_window = int(argv[9])
            hourly_window = int(argv[10])
            circuit_open_seconds = int(argv[11])

            if circuit_threshold > 0 and self.store.get(circuit_open_key):
                return [0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, circuit_open_seconds]

            burst_count = 0
            sustained_count = 0
            daily_consumed = 0
            hourly_consumed = 0

            burst_ok = 1
            sustained_ok = 1
            daily_ok = 1
            hourly_ok = 1
            circuit_ok = 1

            if burst_limit > 0:
                burst_count = int(self.store.get(burst_key, 0)) + 1
                self.store[burst_key] = burst_count
                burst_ok = 1 if burst_count <= burst_limit else 0

            if sustained_limit > 0:
                sustained_count = int(self.store.get(sustained_key, 0)) + 1
                self.store[sustained_key] = sustained_count
                sustained_ok = 1 if sustained_count <= sustained_limit else 0

            if daily_limit > 0:
                daily_consumed = int(self.store.get(daily_key, 0))
                if daily_consumed + requested_tokens <= daily_limit:
                    daily_consumed += requested_tokens
                    self.store[daily_key] = daily_consumed
                    daily_ok = 1
                else:
                    daily_ok = 0

            if hourly_limit > 0:
                buckets = self.store.get(hourly_key)
                if not isinstance(buckets, dict):
                    buckets = {}
                stale_before = now_minute - hourly_window + 1
                for bucket in list(buckets.keys()):
                    if int(bucket) < stale_before:
                        buckets.pop(bucket, None)
                hourly_consumed = sum(int(value) for value in buckets.values())
                if hourly_consumed + requested_tokens <= hourly_limit:
                    buckets[str(now_minute)] = int(buckets.get(str(now_minute), 0)) + requested_tokens
                    self.store[hourly_key] = buckets
                    hourly_consumed += requested_tokens
                    hourly_ok = 1
                else:
                    hourly_ok = 0

            if circuit_threshold > 0:
                circuit_total = int(self.store.get(circuit_usage_key, 0)) + requested_tokens
                self.store[circuit_usage_key] = circuit_total
                if circuit_total > circuit_threshold:
                    self.store[circuit_open_key] = "1"
                    circuit_ok = 0

            return [
                burst_ok,
                sustained_ok,
                daily_ok,
                hourly_ok,
                circuit_ok,
                burst_count,
                sustained_count,
                daily_consumed,
                hourly_consumed,
                burst_window,
                sustained_window,
                daily_window,
                hourly_window * 60,
                circuit_open_seconds,
            ]
        if "HGETALL" in script_text and "HINCRBY" in script_text:
            key = str(args[0])
            now_minute = str(args[1])
            requested = int(args[2])
            limit = int(args[3])
            window = int(args[4])
            buckets = self.store.get(key)
            if not isinstance(buckets, dict):
                buckets = {}
            total = sum(int(value) for value in buckets.values())
            if total + requested > limit:
                return [0, total, window * 60]
            buckets[now_minute] = int(buckets.get(now_minute, 0)) + requested
            self.store[key] = buckets
            return [1, total + requested, window * 60]
        if "INCRBY" in script_text and "local requested" in script_text and "consumed > limit" in script_text:
            key = str(args[0])
            requested = int(args[1])
            limit = int(args[2])
            window = int(args[3])
            current = int(self.store.get(key, 0))
            consumed = current + requested
            if consumed > limit:
                return [0, current, window]
            self.store[key] = consumed
            return [1, consumed, window]
        if "refund" in script_text and "HSET" in script_text and "HGET" in script_text:
            key = str(args[0])
            bucket = str(args[1])
            refund = int(args[2])
            buckets = self.store.get(key)
            if not isinstance(buckets, dict):
                buckets = {}
            current = int(buckets.get(bucket, 0))
            next_value = max(current - refund, 0)
            buckets[bucket] = next_value
            self.store[key] = buckets
            return next_value
        if "refund" in script_text and "SET" in script_text:
            key = str(args[0])
            refund = int(args[1])
            current = int(self.store.get(key, 0))
            next_value = max(current - refund, 0)
            self.store[key] = next_value
            return next_value
        if "idempotency_ttl" in script_text and "PENDING" in script_text:
            keys = list(args[:_num_keys])
            argv = list(args[_num_keys:])
            token_bucket_key = str(keys[0])
            quota_key = str(keys[1])
            circuit_minute_key = str(keys[2])
            circuit_open_key = str(keys[3])
            idempotency_key = str(keys[4])
            try:
                now_ts = int(argv[0])
                capacity = float(argv[1])
                refill_per_sec = float(argv[2])
                cost = int(argv[3])
                quota_limit = int(argv[4])
                quota_window = int(argv[5])
                reserved_tokens = int(argv[6])
                circuit_threshold = int(argv[7])
                circuit_open_seconds = int(argv[8])
                idempotency_ttl = int(argv[9])
                idempotency_stale = int(argv[10])

                status = await self.hget(idempotency_key, "status")
                if status == "COMPLETED":
                    response = await self.hget(idempotency_key, "response")
                    return [1, 0, "COMPLETED", response]
                if status == "PENDING":
                    started_at = int(await self.hget(idempotency_key, "started_at") or 0)
                    if started_at > 0 and (now_ts - started_at) < idempotency_stale:
                        return [0, idempotency_ttl, "PENDING", None]
                    await self.hset(idempotency_key, "status", "EXPIRED")
                    await self.hset(idempotency_key, "expired_at", now_ts)

                if circuit_threshold > 0 and await self.get(circuit_open_key):
                    return [0, circuit_open_seconds, "CIRCUIT_OPEN", None]

                if capacity > 0 and refill_per_sec > 0:
                    raw_tokens = await self.hget(token_bucket_key, "tokens")
                    tokens = float(raw_tokens) if raw_tokens is not None else capacity
                    raw_last_ts = await self.hget(token_bucket_key, "last_ts")
                    last_ts = int(raw_last_ts) if raw_last_ts is not None else now_ts
                    delta = max(0, now_ts - last_ts)
                    refill = delta * refill_per_sec
                    new_tokens = min(capacity, tokens + refill)
                    if new_tokens < cost:
                        retry_after = int((cost - new_tokens) / refill_per_sec) + 1
                        return [0, retry_after, "RATE_LIMITED", None]
                    await self.hset(token_bucket_key, "tokens", new_tokens - cost)
                    await self.hset(token_bucket_key, "last_ts", now_ts)

                if quota_limit > 0:
                    consumed = int(await self.get(quota_key) or 0)
                    if consumed + reserved_tokens > quota_limit:
                        return [0, quota_window, "QUOTA_EXCEEDED", None]
                    await self.incrby(quota_key, reserved_tokens)

                if circuit_threshold > 0:
                    total = int(await self.incrby(circuit_minute_key, reserved_tokens))
                    if total > circuit_threshold:
                        await self.setex(circuit_open_key, circuit_open_seconds, "1")
                        return [0, circuit_open_seconds, "CIRCUIT_OPEN", None]

                await self.hset(idempotency_key, "status", "PENDING")
                await self.hset(idempotency_key, "started_at", now_ts)
                return [1, 0, "PENDING", None]
            except Exception:
                await self.hset(idempotency_key, "status", "PENDING")
                await self.hset(idempotency_key, "started_at", int(time.time()))
                return [1, 0, "PENDING", None]

        if "LRANGE" in script_text and "meta" in script_text:
            meta_key = str(args[0])
            list_key = str(args[1])
            max_messages = int(args[2])
            meta = await self.get(meta_key)
            msgs = await self.lrange(list_key, -max_messages, -1)
            return [meta, msgs]

        if "RPUSH" in script_text and "cjson.decode" in script_text:
            seq_key = str(args[0])
            list_key = str(args[1])
            message_json = str(args[2])
            max_messages = int(args[3])
            seq = await self.incr(seq_key)
            payload_obj = json.loads(message_json)
            payload_obj["sequence_id"] = seq
            payload = json.dumps(payload_obj)
            await self.rpush(list_key, payload)
            if max_messages > 0:
                await self.ltrim(list_key, -max_messages, -1)
            return seq

        if script_text.strip() == "return redis.call('GET', KEYS[1])":
            return await self.get(str(args[0]))

        if "SETEX" in script_text and "INCRBY" not in script_text:
            key = str(args[0])
            ttl = int(args[1])
            value = args[2]
            await self.setex(key, ttl, value)
            return True

        if "HGETALL" in script_text:
            key = str(args[0])
            now_min = int(args[1])
            requested_value = int(args[2])
            limit_value = int(args[3])
            window_value = int(args[4])

            hash_store = self.store.setdefault(key, {})
            total = 0
            stale_before = now_min - window_value + 1
            for bucket, value in list(hash_store.items()):
                bucket_int = int(bucket)
                if bucket_int < stale_before:
                    hash_store.pop(bucket, None)
                else:
                    total += int(value)

            if total + requested_value > limit_value:
                return [0, total, window_value * 60]

            hash_store[str(now_min)] = int(hash_store.get(str(now_min), 0)) + requested_value
            self.store[key] = hash_store
            return [1, total + requested_value, window_value * 60]

        key = str(args[0])
        requested_value = int(args[1])
        limit_value = int(args[2])
        window_value = int(args[3])

        current = int(self.store.get(key, 0))
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

    def gte(self, *_args, **_kwargs):
        return self

    def lte(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
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
        self.rpcs = []

    def table(self, table):
        return FakeSupabaseQuery(self, table)

    def rpc(self, function_name, _params=None):
        self.rpcs.append((function_name, _params))
        query = FakeSupabaseQuery(self, function_name)
        query._response = self.responses.get(function_name, [])
        return query


@pytest.fixture(scope="session")
def test_settings():
    return SimpleNamespace(
        environment="development",
        groq_api_key="gsk-test",
        cerebras_api_key="cs-test",
        gemini_api_key="gm-test",
        openrouter_api_key="or-test",
        llm_timeout_seconds=60,
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
        free_daily_token_quota_learning=50000,
        free_hourly_token_quota_learning=5000,
        free_rpm_learning=20,
        free_burst_learning=4,
        pro_daily_token_quota=200000,
        pro_hourly_token_quota=40000,
        pro_rpm=30,
        pro_burst=10,
        anon_daily_token_quota=5000,
        anon_rph=10,
        max_output_tokens_learning=1024,
        max_output_tokens_socratic=1024,
        supabase_url="https://example.supabase.co",
        supabase_publishable_key="publishable",
        supabase_secret_key="secret",
        tavily_api_key="",
        serper_api_key="",
        exa_api_key="",
        cerebras_daily_token_budget=100000,
        dodo_api_key="",
        dodo_webhook_secret="whsec_"
        + base64.b64encode(b"knowbear-test-webhook-secret-1").decode("ascii"),
        dodo_webhook_endpoint="",
        dodo_webhook_url="",
        dodo_payment_link_id="pay_123",
        dodo_environment="test_mode",
        checkout_rate_limit_per_minute=10,
    )


@pytest.fixture(autouse=True)
def patch_settings(monkeypatch, test_settings):
    if _RUN_REAL_PROVIDER_TESTS:
        # Use real runtime configuration for sampled real-provider tests.
        return config_module.get_settings()

    monkeypatch.setattr(config_module, "get_settings", lambda: test_settings)
    if hasattr(main_app, "get_settings"):
        monkeypatch.setattr(main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(api_main_app, "get_settings", lambda: test_settings)
    monkeypatch.setattr(cache_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(auth_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(llm_client_module, "get_settings", lambda: test_settings)
    # Reset the cached stream config so it recomputes with test settings
    config_module.reset_stream_config()
    return test_settings


@pytest.fixture(autouse=True)
def patch_llm_client(monkeypatch):
    if _RUN_REAL_PROVIDER_TESTS:
        return

    class DummyChoice:
        def __init__(self, content: str):
            self.message = type("Msg", (), {"content": content})

    class DummyResponse:
        def __init__(self, content: str, model: str):
            self.choices = [DummyChoice(content)]
            self.model = model
            self.usage = None

    async def fake_create_chat_completion(model: str, messages: list, **_kwargs):
        return DummyResponse("ok", model)

    async def fake_stream_chat_completion(model: str, messages: list, **_kwargs):
        yield "ok"

    monkeypatch.setattr(llm_client_module, "create_chat_completion", fake_create_chat_completion)
    monkeypatch.setattr(llm_client_module, "stream_chat_completion", fake_stream_chat_completion)
    monkeypatch.setattr(inference_module, "create_chat_completion", fake_create_chat_completion)
    monkeypatch.setattr(inference_module, "stream_chat_completion", fake_stream_chat_completion)


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
    monkeypatch.setattr(message_gate_module, "get_redis", _get_redis)
    monkeypatch.setattr(conversation_cache_module, "get_redis", _get_redis)
    monkeypatch.setattr(user_cache_module, "get_redis", _get_redis)
    monkeypatch.setattr(api_main_app, "redis_circuit_active", lambda: False)
    monkeypatch.setattr(api_main_app, "close_redis", _noop_close)
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
