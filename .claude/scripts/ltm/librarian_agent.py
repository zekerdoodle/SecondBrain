"""
Librarian Agent - COMPATIBILITY SHIM

This file is a backward compatibility shim.
The actual implementation has moved to:
  .claude/agents/background/librarian/librarian_runner.py

All imports are re-exported from the new location.
"""

import sys
from pathlib import Path

# Add the new location to path
_new_location = Path(__file__).parent.parent.parent / "agents" / "background" / "librarian"
if str(_new_location) not in sys.path:
    sys.path.insert(0, str(_new_location))

# Re-export everything from the new location
from librarian_runner import (
    run_librarian,
    run_librarian_cycle,
    apply_librarian_results,
    run_librarian_sync,
    LIBRARIAN_OUTPUT_SCHEMA,
)

# For backward compatibility, also expose the system prompt
def _get_system_prompt():
    """Load the librarian system prompt from the new location."""
    prompt_path = _new_location / "librarian.md"
    if prompt_path.exists():
        return prompt_path.read_text()
    return ""

LIBRARIAN_SYSTEM_PROMPT = _get_system_prompt()

__all__ = [
    "run_librarian",
    "run_librarian_cycle",
    "apply_librarian_results",
    "run_librarian_sync",
    "LIBRARIAN_SYSTEM_PROMPT",
    "LIBRARIAN_OUTPUT_SCHEMA",
]
