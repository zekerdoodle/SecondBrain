"""
Main entry point for CUA Orchestrator.

Can be run as:
    python -m cua_orchestrator "task description"

Or from the second_brain root:
    python .claude/agents/cua_orchestrator "task description"
"""

import sys
import os

# Add lib to path
lib_path = os.path.join(os.path.dirname(__file__), "lib")
sys.path.insert(0, lib_path)

from orchestrator import main

if __name__ == "__main__":
    sys.exit(main())
