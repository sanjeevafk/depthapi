"""Protocol definitions for pluggable application services."""

from __future__ import annotations

from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from api.services.security.api_key_auth import ApiKeyRecord


class IAuthProvider(Protocol):
    """Authenticate a raw API key and return its resolved metadata."""

    async def authenticate(self, raw_key: str) -> ApiKeyRecord | None:
        """Return a resolved API key record, or ``None`` when authentication fails."""

