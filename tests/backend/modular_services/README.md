# Backend Modular Services Regression Tests

This directory contains focused backend tests for services extracted during the
backend modularization refactor.

Status:
- Backend refactor is complete.
- Former monolithic responsibilities are now split across modular services.
- Tests in this folder validate those extracted modules and guard against
  regressions in orchestration behavior.

Coverage examples:
- Routing and classification (`test_inference_routing.py`, `test_inference_classifier.py`)
- Provider stack (`test_provider_registry.py`, `test_provider_authenticator.py`, `test_provider_usage_tracker.py`)
- Message and response flow (`test_message_workflow.py`, `test_message_streaming.py`, `test_response_orchestrator.py`)
- Reliability controls (`test_quota_manager.py`, `test_rate_limit_complete.py`, `test_circuit_breaker.py`)

Related suites:
- `api/tests/` for API-level behavior
- `api/tests/benchmarks/` for baseline and performance-oriented checks
