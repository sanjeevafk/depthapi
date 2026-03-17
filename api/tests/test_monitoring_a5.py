from __future__ import annotations

from types import SimpleNamespace

import monitoring


class _FakeScope:
    def __init__(self):
        self.extras: dict[str, object] = {}
        self.context: dict[str, object] = {}
        self.tags: dict[str, str] = {}
        self.level: str | None = None

    def set_extra(self, key: str, value: object) -> None:
        self.extras[key] = value

    def set_tag(self, key: str, value: str) -> None:
        self.tags[key] = value

    def set_level(self, level: str) -> None:
        self.level = level

    def set_context(self, key: str, value: object) -> None:
        self.context[key] = value


class _FakeScopeContext:
    def __init__(self, scope: _FakeScope):
        self._scope = scope

    def __enter__(self) -> _FakeScope:
        return self._scope

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001
        return False


class _FakeSentry:
    def __init__(self):
        self.init_calls: list[dict[str, object]] = []
        self.captured_messages: list[str] = []
        self.captured_exceptions: list[str] = []
        self.last_scope = _FakeScope()
        self.trace_headers: list[dict[str, str]] = []
        self.user: dict[str, str | None] | None = None

    def init(self, **kwargs):  # noqa: ANN003
        self.init_calls.append(kwargs)

    def push_scope(self) -> _FakeScopeContext:
        self.last_scope = _FakeScope()
        return _FakeScopeContext(self.last_scope)

    def new_scope(self) -> _FakeScopeContext:
        self.last_scope = _FakeScope()
        return _FakeScopeContext(self.last_scope)

    def capture_message(self, message: str) -> None:
        self.captured_messages.append(message)

    def capture_exception(self, exc: Exception) -> None:
        self.captured_exceptions.append(str(exc))

    def continue_trace(self, headers: dict[str, str | None]) -> None:
        self.trace_headers.append({
            "sentry-trace": str(headers.get("sentry-trace") or ""),
            "baggage": str(headers.get("baggage") or ""),
        })

    def configure_scope(self) -> _FakeScopeContext:
        return _FakeScopeContext(self.last_scope)

    def get_isolation_scope(self) -> _FakeScope:
        return self.last_scope

    def set_user(self, user: dict[str, str | None] | None) -> None:
        self.user = user


def test_sentry_disabled_by_env(monkeypatch):
    fake_sentry = _FakeSentry()
    monkeypatch.setattr(monitoring, "sentry_sdk", fake_sentry)

    settings = SimpleNamespace(
        environment="test",
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_enabled=False,
        sentry_traces_sample_rate=0.1,
        sentry_profiles_sample_rate=0.0,
    )

    assert monitoring.init_sentry(settings) is False
    assert monitoring.sentry_is_enabled() is False
    assert fake_sentry.init_calls == []


def test_sentry_init_uses_environment_and_sampling(monkeypatch):
    fake_sentry = _FakeSentry()
    monkeypatch.setattr(monitoring, "sentry_sdk", fake_sentry)

    settings = SimpleNamespace(
        environment="production",
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_enabled=True,
        sentry_traces_sample_rate=0.25,
        sentry_profiles_sample_rate=0.05,
        sentry_release="abc123",
    )

    assert monitoring.init_sentry(settings) is True
    assert monitoring.sentry_is_enabled() is True
    assert len(fake_sentry.init_calls) == 1
    init_call = fake_sentry.init_calls[0]
    assert init_call["environment"] == "production"
    assert init_call["release"] == "abc123"
    assert init_call["traces_sample_rate"] == 0.25
    assert init_call["profiles_sample_rate"] == 0.05
    assert init_call["send_default_pii"] is False


def test_redaction_removes_emails_headers_and_tokens():
    payload = {
        "email": "user@example.com",
        "authorization": "Bearer secret-token",
        "nested": {"headers": {"x-api-key": "value"}, "safe": "x"},
        "text": "Contact me at person@example.com",
    }

    redacted = monitoring.redact_pii(payload)

    assert redacted["email"] == "[REDACTED]"
    assert redacted["authorization"] == "[REDACTED]"
    assert redacted["nested"]["headers"] == "[REDACTED]"
    assert redacted["nested"]["safe"] == "x"
    assert redacted["text"] == "Contact me at [REDACTED]"


def test_capture_telemetry_event_redacts_payload(monkeypatch):
    fake_sentry = _FakeSentry()
    monkeypatch.setattr(monitoring, "sentry_sdk", fake_sentry)

    settings = SimpleNamespace(
        environment="test",
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_enabled=True,
        sentry_traces_sample_rate=0.1,
        sentry_profiles_sample_rate=0.0,
    )
    assert monitoring.init_sentry(settings) is True

    monitoring.capture_telemetry_event(
        "message_send",
        email="user@example.com",
        authorization="Bearer token",
        status="ok",
    )

    assert fake_sentry.captured_messages == ["telemetry.message_send"]
    assert fake_sentry.last_scope.tags["telemetry_event"] == "message_send"
    assert fake_sentry.last_scope.extras["email"] == "[REDACTED]"
    assert fake_sentry.last_scope.extras["authorization"] == "[REDACTED]"
    assert fake_sentry.last_scope.extras["status"] == "ok"


def test_continue_trace_and_request_context(monkeypatch):
    fake_sentry = _FakeSentry()
    monkeypatch.setattr(monitoring, "sentry_sdk", fake_sentry)

    settings = SimpleNamespace(
        environment="test",
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_enabled=True,
        sentry_traces_sample_rate=0.1,
        sentry_profiles_sample_rate=0.0,
    )
    assert monitoring.init_sentry(settings) is True

    monitoring.continue_trace_from_headers(
        {"sentry-trace": "1234abcd-1234abcd", "baggage": "sentry-release=x"}
    )
    monitoring.set_request_context(
        request_id="req-1",
        path="/api/messages",
        method="POST",
        client_ip="127.0.0.1",
    )

    assert fake_sentry.trace_headers == [{"sentry-trace": "1234abcd-1234abcd", "baggage": "sentry-release=x"}]
    assert fake_sentry.last_scope.tags["request_id"] == "req-1"
    assert fake_sentry.last_scope.tags["path"] == "/api/messages"
    assert fake_sentry.last_scope.tags["method"] == "POST"
    assert "client_ip_hash" in fake_sentry.last_scope.extras


def test_set_user_context_hashes_session_token(monkeypatch):
    fake_sentry = _FakeSentry()
    monkeypatch.setattr(monitoring, "sentry_sdk", fake_sentry)

    settings = SimpleNamespace(
        environment="test",
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_enabled=True,
        sentry_traces_sample_rate=0.1,
        sentry_profiles_sample_rate=0.0,
    )
    assert monitoring.init_sentry(settings) is True

    email_hash = monitoring.hash_for_monitoring("user@example.com")
    token_hash = monitoring.hash_for_monitoring("secret-token")
    monitoring.set_user_context(user_id="user-1", email_hash=email_hash, token_hash=token_hash)

    assert fake_sentry.user == {"id": "user-1", "email_hash": email_hash}
    session_context = fake_sentry.last_scope.context.get("session")
    assert isinstance(session_context, dict)
    assert session_context.get("token_hash") == token_hash
    assert "user@example.com" not in str(fake_sentry.user)
    assert "secret-token" not in str(fake_sentry.last_scope.context)
