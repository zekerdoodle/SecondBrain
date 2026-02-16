#!/usr/bin/env python3
"""
Simple runner script for CUA Orchestrator.

Usage:
    python run.py "Open Firefox and search for weather"
    python run.py "Check Gmail for new messages" --mode foreground
"""

import sys
import os
from pathlib import Path

# Get script location and derive paths
script_dir = Path(__file__).parent.absolute()
lib_dir = script_dir / "lib"
# .claude/agents/cua_orchestrator -> second_brain
root_dir = script_dir.parent.parent.parent

# Add lib to path
sys.path.insert(0, str(lib_dir))

# Load environment variables from .env
env_file = root_dir / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value)

# Set display if not set
if "DISPLAY" not in os.environ:
    os.environ["DISPLAY"] = ":10"

from orchestrator import main

if __name__ == "__main__":
    sys.exit(main())
