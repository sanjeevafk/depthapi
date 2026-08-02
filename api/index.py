"""Ensure the api directory is on sys.path for platform-specific CWDs."""

import sys
import os
from api.main import app

api_dir = os.path.dirname(os.path.abspath(__file__))
if api_dir not in sys.path:
    sys.path.insert(0, api_dir)


__all__ = ["app"]
