"""Auth provider protocol for dependency-inversion across auth backends.

Concrete implementations live in ``api.services.security`` and can be
swapped without touching higher-level business logic.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class IAuthProvider(Protocol):
    """Minimal contract every auth backend must satisfy."""

    async def verify(self, token: str) -> dict[str, Any]:
        """Verify *token* and return a dict of claims / user metadata.

        Raises:
            ValueError: If the token is malformed or missing required fields.
            PermissionError: If the token is revoked, expired, or not recognised.
        """
        ...

    async def is_healthy(self) -> bool:
        """Return True when the underlying auth store is reachable."""
        ...
