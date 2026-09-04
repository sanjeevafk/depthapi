"""Adapter utilities for deprecation tracking and API migration."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from api.logging_config import logger

P = ParamSpec("P")
R = TypeVar("R")


def deprecated_in_favor_of(new_module: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Emit a warning log when a deprecated API is called.

    Parameters
    ----------
    new_module:
        The replacement module path or API identifier.
    """

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            logger.warning(
                "deprecated_api_called",
                old_api=f"{func.__module__}.{func.__name__}",
                replacement=new_module,
            )
            return func(*args, **kwargs)

        return wrapper

    return decorator


__all__ = ["deprecated_in_favor_of"]
