import asyncio
import time
from collections import Counter
from typing import Any, Awaitable, Callable

from api.logging_config import logger

REDIS_CALL_TIMEOUT_SECONDS = 0.8
REDIS_CIRCUIT_BREAK_SECONDS = 30

_REDIS_DISABLED_UNTIL = 0.0
_REDIS_DISABLED_LOGGED_UNTIL = 0.0
_REDIS_RECOVERY_PENDING = False
_REDIS_METRICS: Counter[str] = Counter()


def redis_available() -> bool:
    return time.time() > _REDIS_DISABLED_UNTIL


def redis_circuit_active() -> bool:
    return not redis_available()


def redis_metrics_snapshot() -> dict[str, int]:
    return {
        "redis.success": int(_REDIS_METRICS.get("redis.success", 0)),
        "redis.timeout": int(_REDIS_METRICS.get("redis.timeout", 0)),
        "redis.circuit_break": int(_REDIS_METRICS.get("redis.circuit_break", 0)),
    }


def reset_redis_safety_state() -> None:
    """Reset circuit-breaker and counters (used by tests for isolation)."""
    global _REDIS_DISABLED_UNTIL, _REDIS_DISABLED_LOGGED_UNTIL, _REDIS_RECOVERY_PENDING
    _REDIS_DISABLED_UNTIL = 0.0
    _REDIS_DISABLED_LOGGED_UNTIL = 0.0
    _REDIS_RECOVERY_PENDING = False
    _REDIS_METRICS.clear()


def _metrics_increment(name: str) -> None:
    _REDIS_METRICS[name] += 1


def _log_recovered_if_needed() -> None:
    global _REDIS_RECOVERY_PENDING
    if _REDIS_RECOVERY_PENDING:
        logger.info("redis_recovered")
        _REDIS_RECOVERY_PENDING = False


def _open_circuit(error: Exception, *, operation: str) -> None:
    global _REDIS_DISABLED_UNTIL, _REDIS_DISABLED_LOGGED_UNTIL, _REDIS_RECOVERY_PENDING
    _REDIS_DISABLED_UNTIL = time.time() + REDIS_CIRCUIT_BREAK_SECONDS
    _REDIS_DISABLED_LOGGED_UNTIL = _REDIS_DISABLED_UNTIL
    _REDIS_RECOVERY_PENDING = True
    _metrics_increment("redis.circuit_break")
    logger.warning(
        "redis_call_failed",
        operation=operation,
        error_type=type(error).__name__,
        error=str(error),
    )
    logger.info("redis_disabled", duration=REDIS_CIRCUIT_BREAK_SECONDS)


async def safe_redis_call(
    fn: Callable[..., Awaitable[Any]],
    *args: Any,
    timeout: float = REDIS_CALL_TIMEOUT_SECONDS,
    operation: str = "unknown",
) -> Any | None:
    global _REDIS_DISABLED_LOGGED_UNTIL

    now = time.time()
    if now <= _REDIS_DISABLED_UNTIL:
        if now <= _REDIS_DISABLED_LOGGED_UNTIL:
            logger.info(
                "redis_circuit_open",
                operation=operation,
                disabled_for_seconds=max(int(_REDIS_DISABLED_UNTIL - now), 0),
            )
            _REDIS_DISABLED_LOGGED_UNTIL = 0.0
        return None

    bounded_timeout = max(float(timeout), 0.8)
    try:
        result = await asyncio.wait_for(fn(*args), timeout=bounded_timeout)
        _metrics_increment("redis.success")
        _log_recovered_if_needed()
        return result
    except asyncio.TimeoutError as exc:
        _metrics_increment("redis.timeout")
        _open_circuit(exc, operation=operation)
        return None
    except Exception as exc:
        _open_circuit(exc, operation=operation)
        return None


async def safe_redis_command(
    command: str,
    *args: Any,
    timeout: float = REDIS_CALL_TIMEOUT_SECONDS,
) -> Any | None:
    from api.services.cache import get_redis

    redis = await safe_redis_call(get_redis, timeout=timeout, operation="connect")
    if redis is None:
        return None

    method = getattr(redis, command, None)
    if method is None:
        logger.warning("redis_command_missing", command=command)
        return None

    return await safe_redis_call(method, *args, timeout=timeout, operation=command)
