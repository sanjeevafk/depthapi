from __future__ import annotations

from types import SimpleNamespace

import services.rate_limit as rate_limit_module


def test_estimate_tokens_for_text_uses_output_buffer(monkeypatch) -> None:
    monkeypatch.setattr(rate_limit_module, "count_prompt_tokens", lambda _text: 120)
    estimated = rate_limit_module.estimate_tokens_for_text("hello", output_buffer=80)
    assert estimated == 200


def test_quota_keys_include_identifier_and_mode() -> None:
    daily_key, hourly_key = rate_limit_module._quota_keys("user:123", "technical")
    assert daily_key.endswith("user:123:technical")
    assert hourly_key.endswith("user:123:technical")


def test_resolve_limits_for_anonymous_uses_anon_limits() -> None:
    settings = SimpleNamespace(
        rate_limit_burst_window_seconds=8,
        rate_limit_sustained_window_seconds=60,
        anon_daily_token_quota=1000,
        anon_rph=20,
    )
    daily, hourly, rpm, burst, sustained_window, burst_window = rate_limit_module._resolve_limits(
        settings=settings,
        is_authenticated=False,
        is_pro=False,
        mode="learn",
    )
    assert daily == 1000
    assert hourly == 0
    assert rpm == 20
    assert burst == 0
    assert sustained_window == 3600
    assert burst_window == 8


def test_resolve_limits_for_pro_uses_pro_fields() -> None:
    settings = SimpleNamespace(
        rate_limit_burst_window_seconds=10,
        rate_limit_sustained_window_seconds=45,
        pro_daily_token_quota=9000,
        pro_hourly_token_quota=1200,
        pro_rpm=60,
        pro_burst=15,
    )
    daily, hourly, rpm, burst, sustained_window, burst_window = rate_limit_module._resolve_limits(
        settings=settings,
        is_authenticated=True,
        is_pro=True,
        mode="technical",
    )
    assert (daily, hourly, rpm, burst, sustained_window, burst_window) == (9000, 1200, 60, 15, 45, 10)
