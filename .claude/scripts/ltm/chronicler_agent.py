"""
Chronicler Agent - COMPATIBILITY SHIM

This file is a backward compatibility shim.
The actual implementation lives at:
  .claude/agents/background/chronicler/chronicler_runner.py

All imports are re-exported from that location.
"""

import sys
from pathlib import Path

# Add the new location to path
_new_location = Path(__file__).parent.parent.parent / "agents" / "background" / "chronicler"
if str(_new_location) not in sys.path:
    sys.path.insert(0, str(_new_location))

# Re-export everything from the new location
from chronicler_runner import (  # noqa: F401
    run_chronicler,
    run_chronicler_cycle,
    run_chronicler_sync,
    CHRONICLER_OUTPUT_SCHEMA,
)

__all__ = [
    "run_chronicler",
    "run_chronicler_cycle",
    "run_chronicler_sync",
    "CHRONICLER_OUTPUT_SCHEMA",
]
