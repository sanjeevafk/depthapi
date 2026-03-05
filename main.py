"""Root uvicorn entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent
api_path = root / "api"
if str(api_path) not in sys.path:
    sys.path.insert(0, str(api_path))

from api.main import app  # noqa: E402

__all__ = ["app"]
