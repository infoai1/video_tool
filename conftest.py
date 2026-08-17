"""Make the top-level modules importable from tests/ regardless of how pytest
is invoked."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
