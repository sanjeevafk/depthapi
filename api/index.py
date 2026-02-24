import sys
import os

# Add the directory containing this file to sys.path
# This allows 'import main' to work regardless of how Vercel sets the CWD
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from main import app


