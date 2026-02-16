"""
Backfill Conversation Threads from existing atomic memories.

Creates conversation-type threads for every room that has atoms but no
conversation thread yet. Then calls the Chronicler to generate real
descriptions for each one.

This script does NOT extract new atoms — it groups existing atoms by
their source_session_id and creates one conversation thread per room.

IMPORTANT: Stop the server before running this script (singleton access).

Usage:
    python backfill_conversation_threads.py [--dry-run] [--skip-chronicler]
"""

import sys
import json
import asyncio
import argparse
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

# Setup paths
SCRIPT_DIR = Path(__file__).parent
CLAUDE_DIR = SCRIPT_DIR.parent.parent
CHATS_DIR = CLAUDE_DIR / "chats"
MEMORY_DIR = CLAUDE_DIR / "memory"

# Add LTM scripts to path
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

# Add agents dir to path (for chronicler import)
AGENTS_DIR = CLAUDE_DIR / "agents" / "background" / "chronicler"
if str(AGENTS_DIR) not in sys.path:
    sys.path.insert(0, str(AGENTS_DIR))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ltm.backfill_conv_threads")


def get_chat_title(room_id: str) -> Optional[str]:
    """Get the chat title for a room from its chat file."""
    chat_file = CHATS_DIR / f"{room_id}.json"
    if not chat_file.exists():
        return None
    try:
        with open(chat_file, 'r') as f:
            data = json.load(f)
        return data.get("title")
    except Exception:
        return None


def format_thread_name(room_id: str) -> str:
    """Create the conversation thread name from a room ID."""
    title = get_chat_title(room_id)
    if title:
        return f"Conversation: {title}"
    return f"Conversation: {room_id[:12]}"


def build_room_atom_map() -> Dict[str, List[str]]:
    """
    Group all atoms by source_session_id, sorted chronologically.

    Returns:
        Dict mapping room_id -> list of atom IDs sorted by created_at.
    """
    from atomic_memory import get_atomic_manager

    atom_mgr = get_atomic_manager()
    all_atoms = atom_mgr.list_all()

    rooms: Dict[str, List[Tuple[str, str]]] = {}  # room_id -> [(created_at, atom_id)]

    for atom in all_atoms:
        rid = atom.source_session_id
        if not rid:
            continue
        rooms.setdefault(rid, []).append((atom.created_at, atom.id))

    # Sort atoms within each room by created_at (chronological order)
    result: Dict[str, List[str]] = {}
    for rid, items in rooms.items():
        items.sort(key=lambda x: x[0])  # Sort by created_at (ISO string sort works)
        result[rid] = [atom_id for _, atom_id in items]

    return result


def create_conversation_threads(dry_run: bool = False) -> Dict[str, Any]:
    """
    Create conversation threads for all rooms that have atoms but no
    conversation thread yet.

    Args:
        dry_run: If True, don't actually create threads.

    Returns:
        Dict with stats about threads created/skipped.
    """
    from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()

    room_atoms = build_room_atom_map()
    logger.info(f"Found {len(room_atoms)} rooms with atoms")

    stats = {
        "rooms_found": len(room_atoms),
        "threads_created": 0,
        "threads_already_exist": 0,
        "atoms_assigned": 0,
        "created_thread_ids": [],
        "errors": []
    }

    for room_id, atom_ids in room_atoms.items():
        # Check if conversation thread already exists
        existing = thread_mgr.get_conversation_thread_for_room(room_id)
        if existing:
            stats["threads_already_exist"] += 1
            logger.debug(f"Conversation thread already exists for room {room_id[:16]}...")
            continue

        thread_name = format_thread_name(room_id)
        description = (
            f"Conversation thread for room {room_id}. "
            f"Contains {len(atom_ids)} atoms."
        )

        if dry_run:
            logger.info(
                f"[DRY RUN] Would create: '{thread_name}' "
                f"with {len(atom_ids)} atoms (room: {room_id[:16]}...)"
            )
            stats["threads_created"] += 1
            stats["atoms_assigned"] += len(atom_ids)
            continue

        try:
            thread = thread_mgr.create(
                name=thread_name,
                description=description,
                memory_ids=atom_ids,
                embed=False,  # Chronicler will provide real description then embed
                scope=f"room:{room_id}",
                thread_type="conversation"
            )
            stats["threads_created"] += 1
            stats["atoms_assigned"] += len(atom_ids)
            stats["created_thread_ids"].append(thread.id)
            logger.info(
                f"Created conversation thread: '{thread_name}' "
                f"with {len(atom_ids)} atoms (room: {room_id[:16]}...)"
            )
        except Exception as e:
            logger.error(f"Failed to create thread for room {room_id}: {e}")
            stats["errors"].append(f"room {room_id}: {e}")

    return stats


async def run_chronicler_for_new_threads() -> Dict[str, Any]:
    """
    Run the Chronicler to summarize all conversation threads that
    have generic descriptions.

    Uses the existing chronicler_runner module.
    """
    try:
        from chronicler_runner import run_chronicler
        result = await run_chronicler(max_threads_per_batch=10)
        return result
    except Exception as e:
        import traceback
        logger.error(f"Chronicler failed: {e}\n{traceback.format_exc()}")
        return {"status": "error", "error": str(e)}


async def run_backfill(dry_run: bool = False, skip_chronicler: bool = False):
    """
    Run the full conversation thread backfill.

    1. Create conversation threads for all rooms with atoms
    2. Run Chronicler to generate summaries
    """
    logger.info("=" * 60)
    logger.info("CONVERSATION THREAD BACKFILL")
    logger.info("=" * 60)

    # Step 1: Create conversation threads
    logger.info("\n--- Step 1: Creating conversation threads ---")
    create_stats = create_conversation_threads(dry_run=dry_run)

    logger.info(f"\nConversation thread creation results:")
    logger.info(f"  Rooms found:            {create_stats['rooms_found']}")
    logger.info(f"  Threads created:        {create_stats['threads_created']}")
    logger.info(f"  Already existing:       {create_stats['threads_already_exist']}")
    logger.info(f"  Atoms assigned:         {create_stats['atoms_assigned']}")
    if create_stats["errors"]:
        logger.warning(f"  Errors:                 {len(create_stats['errors'])}")
        for err in create_stats["errors"]:
            logger.warning(f"    - {err}")

    if dry_run:
        logger.info("\n[DRY RUN] Skipping Chronicler (no threads were actually created)")
        return

    if create_stats["threads_created"] == 0:
        logger.info("\nNo new threads created — nothing for Chronicler to do.")
        return

    # Step 2: Run Chronicler
    if skip_chronicler:
        logger.info("\n--- Skipping Chronicler (--skip-chronicler flag) ---")
        return

    logger.info(f"\n--- Step 2: Running Chronicler for {create_stats['threads_created']} new threads ---")
    chronicler_stats = await run_chronicler_for_new_threads()

    logger.info(f"\nChronicler results:")
    logger.info(f"  Status:              {chronicler_stats.get('status')}")
    logger.info(f"  Threads summarized:  {chronicler_stats.get('threads_summarized', 0)}")
    logger.info(f"  Threads failed:      {chronicler_stats.get('threads_failed', 0)}")
    logger.info(f"  Batches run:         {chronicler_stats.get('batches_run', 0)}")
    if chronicler_stats.get("errors"):
        for err in chronicler_stats["errors"]:
            logger.warning(f"  Error: {err}")

    logger.info("\n" + "=" * 60)
    logger.info("BACKFILL COMPLETE")
    logger.info("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="Backfill conversation threads from existing atoms"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Don't create threads, just show what would happen"
    )
    parser.add_argument(
        "--skip-chronicler", action="store_true",
        help="Create threads but skip Chronicler summarization"
    )

    args = parser.parse_args()

    asyncio.run(run_backfill(
        dry_run=args.dry_run,
        skip_chronicler=args.skip_chronicler
    ))


if __name__ == "__main__":
    main()
