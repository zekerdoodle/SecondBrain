"""
Run Chronicler for all conversation threads that have generic descriptions.

Uses the server venv which has claude_agent_sdk installed.
IMPORTANT: Stop the server before running this script (singleton access).

Usage:
    /home/debian/second_brain/interface/server/venv/bin/python \
        /home/debian/second_brain/.claude/scripts/ltm/run_chronicler_backfill.py
"""

import sys
import asyncio
import logging
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).parent
CLAUDE_DIR = SCRIPT_DIR.parent.parent
AGENTS_DIR = CLAUDE_DIR / "agents" / "background" / "chronicler"

# Add required paths
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ltm.chronicler_backfill")


async def main():
    # Reset chronicler state so it picks up ALL conversation threads
    from chronicler_runner import _save_state, run_chronicler

    logger.info("=" * 60)
    logger.info("CHRONICLER BACKFILL FOR CONVERSATION THREADS")
    logger.info("=" * 60)

    # Clear last_run so chronicler processes all conversation threads
    _save_state({})
    logger.info("Reset chronicler state (will process all conversation threads)")

    result = await run_chronicler(max_threads_per_batch=10)

    logger.info("")
    logger.info("=" * 60)
    logger.info("RESULTS")
    logger.info("=" * 60)
    logger.info(f"  Status:              {result.get('status')}")
    logger.info(f"  Threads found:       {result.get('threads_found', 0)}")
    logger.info(f"  Threads summarized:  {result.get('threads_summarized', 0)}")
    logger.info(f"  Threads failed:      {result.get('threads_failed', 0)}")
    logger.info(f"  Batches run:         {result.get('batches_run', 0)}")
    if result.get("errors"):
        logger.warning(f"  Errors ({len(result['errors'])}):")
        for err in result["errors"]:
            logger.warning(f"    - {err}")


if __name__ == "__main__":
    asyncio.run(main())
