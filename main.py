"""Root uvicorn entrypoint."""

from __future__ import annotations

import sys
from pathlib import Path

root = Path(__file__).resolve().parent
api_path = root / "api"
if str(api_path) not in sys.path:
    sys.path.insert(0, str(api_path))

from api import main as api_main  # noqa: E402

# Re-export the API module so legacy imports (including tests) patch the real app state.
sys.modules[__name__] = api_main

app = api_main.app
