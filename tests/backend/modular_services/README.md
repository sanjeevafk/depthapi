# Backend Modular Services Regression Tests

This directory contains focused backend tests for services extracted during the
backend modularization refactor.

Status:
- Backend refactor is complete.
- Former monolithic responsibilities are now split across modular services.
- NOTE: focused regression tests from the refactor were removed in cleanup
  (9a80947); only `conftest.py` remains here. Do not cite the files below as
  present until they are restored.

Coverage examples (historical, currently absent — restore before citing):
- Routing and classification (`test_inference_routing.py`, `test_inference_classifier.py`)
- Provider stack (`test_provider_registry.py`, `test_provider_authenticator.py`, `test_provider_usage_tracker.py`)
- Message and response flow (`test_message_workflow.py`, `test_message_streaming.py`, `test_response_orchestrator.py`)
- Reliability controls (`test_quota_manager.py`, `test_rate_limit_complete.py`, `test_circuit_breaker.py`)

Related suites:
- `api/tests/` for API-level behavior
- `api/tests/benchmarks/` for baseline and performance-oriented checks
