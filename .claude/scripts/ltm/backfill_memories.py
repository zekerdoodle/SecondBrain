#!/usr/bin/env python3
"""
Backfill Memories from Chat History

Reads all non-system chats and runs them through the Librarian
to regenerate the memory store.
"""

import os
import sys
import json
import asyncio
import logging
from pathlib import Path
from datetime import datetime

# Setup paths
SCRIPT_DIR = Path(__file__).parent
SCRIPTS_DIR = SCRIPT_DIR.parent
ROOT_DIR = SCRIPTS_DIR.parent.parent
CHATS_DIR = ROOT_DIR / ".claude" / "chats"

sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPTS_DIR))

from librarian_agent import run_librarian, apply_librarian_results
from memory_throttle import force_librarian_ready

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("backfill")


def load_all_chats():
    """Load all non-system chat files."""
    chats = []

    for chat_file in CHATS_DIR.glob("*.json"):
        try:
            with open(chat_file, 'r') as f:
                data = json.load(f)

            # Skip system chats (scheduled tasks, automations)
            if data.get("is_system", False):
                logger.info(f"Skipping system chat: {chat_file.name}")
                continue

            # Skip chats with no messages
            messages = data.get("messages", [])
            if not messages:
                continue

            # Get modification time for sorting
            mtime = chat_file.stat().st_mtime

            chats.append({
                "id": chat_file.stem,
                "title": data.get("title", "Untitled"),
                "messages": messages,
                "mtime": mtime
            })

        except Exception as e:
            logger.warning(f"Failed to load {chat_file}: {e}")

    # Sort by modification time (oldest first for chronological processing)
    chats.sort(key=lambda x: x["mtime"])

    return chats


def extract_exchanges(chat):
    """Extract user/assistant exchanges from a chat."""
    exchanges = []
    messages = chat["messages"]

    i = 0
    while i < len(messages):
        msg = messages[i]

        # Find user message
        if msg.get("role") == "user":
            user_content = msg.get("content", "")

            # Look for following assistant message(s)
            assistant_parts = []
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "assistant":
                assistant_parts.append(messages[j].get("content", ""))
                j += 1

            if assistant_parts:
                exchanges.append({
                    "user_message": user_content,
                    "assistant_message": "\n\n".join(assistant_parts),
                    "session_id": chat["id"],
                    "timestamp": datetime.fromtimestamp(chat["mtime"]).isoformat()
                })

            i = j
        else:
            i += 1

    return exchanges


async def process_chat(chat, existing_memories, existing_threads):
    """Process a single chat through the librarian."""
    exchanges = extract_exchanges(chat)

    if not exchanges:
        logger.info(f"No exchanges in chat: {chat['title'][:50]}")
        return {"memories_created": 0}

    logger.info(f"Processing chat: {chat['title'][:50]} ({len(exchanges)} exchanges)")

    # Run librarian on these exchanges
    results = await run_librarian(exchanges, existing_memories, existing_threads)

    if results.get("error"):
        logger.error(f"Librarian error: {results['error']}")
        return {"memories_created": 0, "error": results["error"]}

    # Apply results
    stats = await apply_librarian_results(results)

    return stats


async def run_backfill():
    """Run the full backfill process."""
    logger.info("=" * 60)
    logger.info("Starting memory backfill from chat history")
    logger.info("=" * 60)

    # Load all chats
    chats = load_all_chats()
    logger.info(f"Found {len(chats)} non-system chats to process")

    if not chats:
        logger.info("No chats to process")
        return

    # Process each chat
    total_memories = 0
    total_duplicates = 0
    errors = []

    # Import memory managers for context
    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    for i, chat in enumerate(chats, 1):
        logger.info(f"\n[{i}/{len(chats)}] Processing: {chat['title'][:50]}")

        # Get current memories for deduplication context
        atom_mgr = get_atomic_manager()
        thread_mgr = get_thread_manager()

        existing_memories = [
            {"content": m.content, "id": m.id}
            for m in atom_mgr.list_all()[-100:]
        ]
        existing_threads = [
            {"name": t.name, "description": t.description, "memory_ids": t.memory_ids}
            for t in thread_mgr.list_all()
        ]

        try:
            stats = await process_chat(chat, existing_memories, existing_threads)
            total_memories += stats.get("memories_created", 0)
            total_duplicates += stats.get("memories_skipped_duplicate", 0)

            if stats.get("errors"):
                errors.extend(stats["errors"])

        except Exception as e:
            logger.error(f"Failed to process chat: {e}")
            errors.append(str(e))

        # Small delay between chats to avoid rate limits
        await asyncio.sleep(1)

    logger.info("\n" + "=" * 60)
    logger.info("Backfill complete!")
    logger.info(f"Total memories created: {total_memories}")
    logger.info(f"Duplicates skipped: {total_duplicates}")
    logger.info(f"Errors: {len(errors)}")
    logger.info("=" * 60)

    return {
        "chats_processed": len(chats),
        "memories_created": total_memories,
        "duplicates_skipped": total_duplicates,
        "errors": errors
    }


async def run_gardener_after():
    """Run the gardener for maintenance."""
    logger.info("\n" + "=" * 60)
    logger.info("Running Gardener for memory maintenance...")
    logger.info("=" * 60)

    from gardener_agent import run_gardener_cycle

    try:
        results = await run_gardener_cycle(auto_apply=True)
        logger.info(f"Gardener results: {results}")
        return results
    except Exception as e:
        logger.error(f"Gardener failed: {e}")
        return {"error": str(e)}


async def main():
    """Main entry point."""
    # Force librarian to be ready (reset throttle)
    force_librarian_ready()

    # Run backfill
    backfill_results = await run_backfill()

    # Run gardener
    gardener_results = await run_gardener_after()

    # Print final summary
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(f"Backfill: {json.dumps(backfill_results, indent=2)}")
    print(f"Gardener: {json.dumps(gardener_results, indent=2)}")


if __name__ == "__main__":
    asyncio.run(main())
