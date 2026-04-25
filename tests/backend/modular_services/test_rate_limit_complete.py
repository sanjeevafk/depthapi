from __future__ import annotations

import services.rate_limit as rate_limit_module
from services.api_key_auth import ApiKeyRecord


def test_estimate_tokens_for_text_uses_output_buffer(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit_module, "count_prompt_tokens", lambda _text: 120)
    estimated = rate_limit_module.estimate_tokens_for_text("hello", output_buffer=80)
    assert estimated == 200


def test_quota_keys_include_identifier_and_mode() -> None:
    daily_key, hourly_key = rate_limit_module._quota_keys("user:123", "technical")
    assert daily_key.endswith("user:123:technical")
    assert hourly_key.endswith("user:123:technical")


def test_resolve_limits_for_anonymous_uses_anon_limits() -> None:
    settings = object()
    api_key = ApiKeyRecord(
        id="starter-key",
        prefix="sk-depth-starter",
        project_name="Starter",
        owner_email="starter@example.com",
        plan="starter",
        monthly_token_budget=3_000_000,
        requests_per_minute=20,
    )
    daily, hourly, rpm, burst, sustained_window, burst_window = rate_limit_module._resolve_limits(
        settings=settings,
        api_key=api_key,
    )
    assert daily == 100000
    assert hourly == 16666
    assert rpm == 20
    assert burst == 30
    assert sustained_window == 60
    assert burst_window == 10


def test_resolve_limits_for_pro_uses_pro_fields() -> None:
    settings = object()
    api_key = ApiKeyRecord(
        id="pro-key",
        prefix="sk-depth-pro",
        project_name="Pro",
        owner_email="pro@example.com",
        plan="pro",
        monthly_token_budget=9_000_000,
        requests_per_minute=60,
    )
    daily, hourly, rpm, burst, sustained_window, burst_window = rate_limit_module._resolve_limits(
        settings=settings,
        api_key=api_key,
    )
    assert (daily, hourly, rpm, burst, sustained_window, burst_window) == (300000, 50000, 60, 90, 60, 10)
