import sys
import os
from main import app

# Add the directory containing this file to sys.path
# This allows 'import main' to work regardless of how Vercel sets the CWD
api_dir = os.path.dirname(os.path.abspath(__file__))
if api_dir not in sys.path:
    sys.path.insert(0, api_dir)


__all__ = ["app"]
