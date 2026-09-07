"""
conftest.py — adds the project root to sys.path so all app modules are importable
from inside tests/evals/ without installation.
"""
import sys
import os

# Project root = two levels up from this file (tests/evals/conftest.py)
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
