"""
Gardener Agent - COMPATIBILITY SHIM

This file is a backward compatibility shim.
The actual implementation has moved to:
  .claude/agents/background/gardener/gardener_runner.py

All imports are re-exported from the new location.
"""

import sys
from pathlib import Path

# Add the new location to path
_new_location = Path(__file__).parent.parent.parent / "agents" / "background" / "gardener"
if str(_new_location) not in sys.path:
    sys.path.insert(0, str(_new_location))

# Re-export everything from the new location
from gardener_runner import (
    run_gardener,
    run_gardener_cycle,
    apply_gardener_results,
    run_gardener_sync,
    GARDENER_OUTPUT_SCHEMA,
)

# For backward compatibility, also expose the system prompt
def _get_system_prompt():
    """Load the gardener system prompt from the new location."""
    prompt_path = _new_location / "gardener.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return ""

GARDENER_SYSTEM_PROMPT = _get_system_prompt()

__all__ = [
    "run_gardener",
    "run_gardener_cycle",
    "apply_gardener_results",
    "run_gardener_sync",
    "GARDENER_SYSTEM_PROMPT",
    "GARDENER_OUTPUT_SCHEMA",
]
