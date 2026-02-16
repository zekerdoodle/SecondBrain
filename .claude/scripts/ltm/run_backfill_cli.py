#!/usr/bin/env python3
"""
CLI runner for thread assignment backfill.

Run from server venv:
    source interface/server/venv/bin/activate
    python .claude/scripts/ltm/run_backfill_cli.py
"""

import sys
import os
import json
import asyncio
import logging

# Setup paths - same as how the MCP server does it
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.dirname(SCRIPT_DIR)
LTM_DIR = SCRIPT_DIR

# Add paths
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if LTM_DIR not in sys.path:
    sys.path.insert(0, LTM_DIR)

# Also add the server directory (for mcp_tools imports)
SERVER_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, "../../../interface/server"))
if SERVER_DIR not in sys.path:
    sys.path.insert(0, SERVER_DIR)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("backfill_cli")


async def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    batch_size = int(sys.argv[2]) if len(sys.argv) > 2 else 50

    from backfill_thread_assignments import (
        run_backfill, continue_backfill, rollback_assignments,
        get_backfill_status
    )

    if action == "status":
        status = get_backfill_status()
        print(json.dumps(status, indent=2))

    elif action == "full":
        # Run straight through without checkpoint (for re-runs after validation)
        logger.info(f"Starting FULL backfill (no checkpoint) with batch_size={batch_size}")
        result = await run_backfill(batch_size=batch_size, resume=False, checkpoint_pct=1.1)
        print(json.dumps(result, indent=2, default=str))

    elif action == "start":
        logger.info(f"Starting backfill with batch_size={batch_size}")
        result = await run_backfill(batch_size=batch_size, resume=False)
        print(json.dumps(result, indent=2, default=str))

    elif action == "continue":
        logger.info("Continuing backfill after checkpoint")
        result = await continue_backfill()
        print(json.dumps(result, indent=2, default=str))

    elif action == "rollback":
        result = rollback_assignments()
        print(json.dumps(result, indent=2, default=str))

    else:
        print(f"Unknown action: {action}")
        print("Usage: python run_backfill_cli.py [start|status|continue|rollback] [batch_size]")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
