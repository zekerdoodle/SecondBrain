"""
Repair last_updated timestamps on conversation threads.

The backfill_conversation_threads.py script set last_updated to the
backfill time rather than the actual latest atom timestamp. This script
fixes that by computing max(atom.created_at) for each conversation thread
and setting last_updated accordingly.

IMPORTANT: Stop the server before running this script (singleton access).

Usage:
    python repair_conversation_timestamps.py [--dry-run]
"""

import sys
import argparse
import logging
from pathlib import Path

# Setup paths
SCRIPT_DIR = Path(__file__).parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Repair conversation thread last_updated timestamps")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without applying")
    args = parser.parse_args()

    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    fixed = 0
    skipped = 0

    for t in thread_mgr.list_all():
        if t.thread_type != "conversation":
            continue

        # Find the latest atom created_at in this thread
        max_created = ""
        for mid in t.memory_ids:
            atom = atom_mgr.get(mid)
            if atom and atom.created_at and atom.created_at > max_created:
                max_created = atom.created_at

        if not max_created:
            logger.info(f"SKIP {t.name}: no atoms with timestamps")
            skipped += 1
            continue

        if t.last_updated == max_created:
            skipped += 1
            continue

        logger.info(
            f"{'[DRY RUN] ' if args.dry_run else ''}"
            f"FIX {t.name}: {t.last_updated} -> {max_created}"
        )

        if not args.dry_run:
            t.last_updated = max_created
            fixed += 1

    if not args.dry_run and fixed > 0:
        thread_mgr._save()

    logger.info(f"Done: {fixed} fixed, {skipped} skipped" + (" (dry run)" if args.dry_run else ""))


if __name__ == "__main__":
    main()
