"""
Backfill Long-Term Memory from existing chat history.

Processes all chat files through the Librarian to extract memories
from historical conversations.

Usage:
    python backfill_chats.py [--dry-run] [--batch-size N] [--limit N]
"""

import os
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
BACKFILL_STATE_FILE = MEMORY_DIR / "backfill_state.json"

# Add LTM to path
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("ltm.backfill")


def load_backfill_state() -> Dict[str, Any]:
    """Load state of which chats have been processed."""
    if BACKFILL_STATE_FILE.exists():
        with open(BACKFILL_STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "processed_chats": [],
        "last_run": None,
        "total_exchanges_processed": 0,
        "total_memories_created": 0
    }


def save_backfill_state(state: Dict[str, Any]):
    """Save backfill progress state."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    state["last_run"] = datetime.now().isoformat()
    with open(BACKFILL_STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def load_chat(chat_file: Path) -> Dict[str, Any]:
    """Load a chat JSON file."""
    try:
        with open(chat_file, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load {chat_file}: {e}")
        return {}


def extract_exchanges(chat: Dict[str, Any], chat_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Extract user/assistant exchange pairs from a chat.

    Captures both:
    - Regular conversations: user -> assistant
    - Autonomous work: system -> assistant (scheduled/silent tasks)

    Excludes librarian and gardener maintenance runs.

    Args:
        chat: The parsed chat JSON data.
        chat_id: The chat/room ID (filename stem). Used as session_id for
                 deterministic room attribution. Falls back to chat's
                 sessionId field if not provided.

    Returns list of exchanges with user_message, assistant_message, timestamp, session_id.
    """
    messages = chat.get("messages", [])
    exchanges = []

    # Use the chat file's ID as the room identifier (matches live pipeline)
    room_id = chat_id or chat.get("sessionId", "unknown")

    i = 0
    while i < len(messages):
        msg = messages[i]
        role = msg.get("role")
        content = msg.get("content", "")

        # Accept user OR system as valid exchange triggers
        if role not in ("user", "system"):
            i += 1
            continue

        # Explicit librarian/gardener exclusion (these are maintenance tasks)
        lower_content = content.lower()
        if "librarian" in lower_content or "gardener" in lower_content:
            # Skip this exchange entirely
            i += 1
            continue

        trigger_content = content

        # Look for assistant response
        assistant_content = ""
        j = i + 1
        while j < len(messages):
            next_msg = messages[j]
            next_role = next_msg.get("role")
            if next_role == "assistant":
                assistant_content = next_msg.get("content", "")
                break
            elif next_role in ("user", "system"):
                # Hit another trigger without finding assistant response
                break
            j += 1

        # Only include if we have both trigger and assistant content
        if trigger_content.strip() and assistant_content.strip():
            # Skip very short exchanges (likely not meaningful)
            if len(trigger_content) > 20 or len(assistant_content) > 50:
                # Extract timestamp from message ID (Unix milliseconds)
                timestamp = datetime.now().isoformat()
                msg_id = msg.get("id")
                if msg_id:
                    try:
                        # Message IDs are Unix timestamps in milliseconds
                        unix_millis = int(msg_id)
                        timestamp = datetime.fromtimestamp(unix_millis / 1000).isoformat()
                    except (ValueError, TypeError, OSError):
                        pass  # Keep default if conversion fails

                exchanges.append({
                    "user_message": trigger_content,
                    "assistant_message": assistant_content,
                    "session_id": room_id,
                    "timestamp": timestamp
                })

        i = j + 1 if j < len(messages) else i + 1

    return exchanges


def get_all_chats() -> List[Tuple[str, Path]]:
    """Get all chat files sorted by modification time (oldest first)."""
    if not CHATS_DIR.exists():
        return []

    chat_files = []
    for f in CHATS_DIR.glob("*.json"):
        chat_id = f.stem
        mtime = f.stat().st_mtime
        chat_files.append((chat_id, f, mtime))

    # Sort by modification time (oldest first - process chronologically)
    chat_files.sort(key=lambda x: x[2])

    return [(chat_id, path) for chat_id, path, _ in chat_files]


async def process_batch(
    exchanges: List[Dict[str, Any]],
    dry_run: bool = False,
    use_sdk: bool = True
) -> Dict[str, Any]:
    """Process a batch of exchanges through the Librarian."""
    if dry_run:
        return {
            "status": "dry_run",
            "exchanges_count": len(exchanges),
            "memories_created": 0
        }

    try:
        from atomic_memory import get_atomic_manager
        from thread_memory import get_thread_manager

        atom_mgr = get_atomic_manager()
        thread_mgr = get_thread_manager()

        # Get existing memories for deduplication
        existing_memories = [
            {"content": m.content, "id": m.id}
            for m in atom_mgr.list_all()[-200:]  # More context for backfill
        ]

        # Try to use Agent SDK (only works within Claude Code context)
        if use_sdk:
            try:
                from librarian_agent import run_librarian, apply_librarian_results

                # Group exchanges by room for deterministic attribution
                room_groups: Dict[str, List[Dict[str, Any]]] = {}
                for ex in exchanges:
                    rid = ex.get("session_id", "unknown")
                    if rid not in room_groups:
                        room_groups[rid] = []
                    room_groups[rid].append(ex)

                batch_stats: Dict[str, Any] = {
                    "memories_created": 0,
                    "memories_skipped_duplicate": 0,
                    "new_atom_ids": [],
                    "errors": []
                }

                for rid, room_exs in room_groups.items():
                    results = await run_librarian(room_exs, existing_memories, room_id=rid)
                    stats = await apply_librarian_results(results)
                    batch_stats["memories_created"] += stats.get("memories_created", 0)
                    batch_stats["memories_skipped_duplicate"] += stats.get("memories_skipped_duplicate", 0)
                    batch_stats["new_atom_ids"].extend(stats.get("new_atom_ids", []))
                    batch_stats["errors"].extend(stats.get("errors", []))

                return {
                    "status": "completed",
                    "exchanges_count": len(exchanges),
                    **batch_stats
                }
            except ImportError as e:
                if "claude_agent_sdk" in str(e):
                    logger.warning("Agent SDK not available, falling back to simple extraction")
                    use_sdk = False
                else:
                    raise

        # Fallback: Simple extraction (no LLM, just pattern matching)
        if not use_sdk:
            stats = await simple_extract(exchanges, atom_mgr, thread_mgr, existing_memories)
            return {
                "status": "completed_simple",
                "exchanges_count": len(exchanges),
                **stats
            }

    except Exception as e:
        logger.error(f"Batch processing failed: {e}")
        return {
            "status": "error",
            "error": str(e),
            "exchanges_count": len(exchanges)
        }


async def simple_extract(
    exchanges: List[Dict[str, Any]],
    atom_mgr,
    thread_mgr,
    existing_memories: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Simple extraction without LLM - extracts key patterns from exchanges.

    This is a fallback when the Agent SDK isn't available.
    Looks for:
    - Explicit preferences ("I prefer X", "I like X")
    - Facts about the user
    - Technical decisions
    """
    import re

    stats = {
        "memories_created": 0,
        "memories_skipped_duplicate": 0,
        "threads_created": 0,
        "threads_updated": 0,
        "errors": []
    }

    # Patterns to look for in user messages
    patterns = [
        # Preferences
        (r"I (?:prefer|like|love|hate|dislike|want|need|use)\s+(.{10,100}?)(?:\.|$|,|\n)", "Preferences"),
        # Facts about self
        (r"I(?:'m| am)\s+(?:a|an)?\s*(.{10,80}?)(?:\.|$|,|\n)", "About User"),
        # Decisions
        (r"(?:Let's|We should|I'll|I will|Going to)\s+(.{10,100}?)(?:\.|$|,|\n)", "Decisions"),
    ]

    existing_content = {m.get("content", "")[:100].lower() for m in existing_memories}

    for exchange in exchanges:
        user_msg = exchange.get("user_message", "")

        for pattern, thread_name in patterns:
            matches = re.findall(pattern, user_msg, re.IGNORECASE)
            for match in matches:
                content = match.strip()
                if len(content) < 15:
                    continue

                # Check for duplicates
                if content[:100].lower() in existing_content:
                    stats["memories_skipped_duplicate"] += 1
                    continue

                # Check semantic similarity
                similar = atom_mgr.find_similar(content, threshold=0.85)
                if similar:
                    stats["memories_skipped_duplicate"] += 1
                    continue

                # Create memory
                try:
                    atom = atom_mgr.create(
                        content=content,
                        importance=40,  # Lower importance for auto-extracted
                        tags=["auto-extracted", "backfill"]
                    )
                    stats["memories_created"] += 1
                    existing_content.add(content[:100].lower())

                    # Add to thread
                    thread = thread_mgr.find_or_create_thread(
                        thread_name,
                        f"Auto-extracted {thread_name.lower()} from conversations"
                    )
                    thread_mgr.add_memory_to_thread(thread.id, atom.id)

                except Exception as e:
                    stats["errors"].append(str(e))

    return stats


async def run_backfill(
    dry_run: bool = False,
    batch_size: int = 10,
    limit: int = None,
    skip_processed: bool = True
):
    """
    Run the backfill process.

    Args:
        dry_run: If True, don't actually create memories
        batch_size: Number of exchanges per Librarian batch
        limit: Maximum number of chats to process
        skip_processed: Skip already-processed chats
    """
    state = load_backfill_state()
    processed_chats = set(state.get("processed_chats", []))

    all_chats = get_all_chats()
    logger.info(f"Found {len(all_chats)} total chats")

    # Filter out already processed
    if skip_processed:
        chats_to_process = [
            (cid, path) for cid, path in all_chats
            if cid not in processed_chats
        ]
    else:
        chats_to_process = all_chats

    if limit:
        chats_to_process = chats_to_process[:limit]

    logger.info(f"Processing {len(chats_to_process)} chats")

    total_exchanges = 0
    total_memories = 0
    all_exchanges = []

    # Extract exchanges from all chats
    for chat_id, chat_path in chats_to_process:
        chat = load_chat(chat_path)
        if not chat:
            continue

        exchanges = extract_exchanges(chat, chat_id=chat_id)
        logger.info(f"Chat {chat_id}: {len(exchanges)} exchanges")

        all_exchanges.extend(exchanges)
        processed_chats.add(chat_id)

    logger.info(f"Total exchanges to process: {len(all_exchanges)}")

    if not all_exchanges:
        logger.info("No exchanges to process")
        return

    # Process in batches
    for i in range(0, len(all_exchanges), batch_size):
        batch = all_exchanges[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(all_exchanges) + batch_size - 1) // batch_size

        logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} exchanges)")

        result = await process_batch(batch, dry_run=dry_run)

        total_exchanges += result.get("exchanges_count", 0)
        total_memories += result.get("memories_created", 0)

        logger.info(
            f"Batch {batch_num} result: {result.get('status')}, "
            f"created {result.get('memories_created', 0)} memories, "
            f"skipped {result.get('memories_skipped_duplicate', 0)} duplicates"
        )

        # Small delay between batches to avoid rate limiting
        # Note: Using time.sleep instead of asyncio.sleep due to SDK async cleanup issues
        if not dry_run and i + batch_size < len(all_exchanges):
            import time
            time.sleep(2)

    # Update state (only if not dry run)
    if not dry_run:
        state["processed_chats"] = list(processed_chats)
        state["total_exchanges_processed"] = state.get("total_exchanges_processed", 0) + total_exchanges
        state["total_memories_created"] = state.get("total_memories_created", 0) + total_memories
        save_backfill_state(state)

    logger.info(f"""
=== Backfill Complete ===
Chats processed: {len(chats_to_process)}
Exchanges processed: {total_exchanges}
Memories created: {total_memories}
Dry run: {dry_run}
""")


def main():
    parser = argparse.ArgumentParser(description="Backfill LTM from chat history")
    parser.add_argument("--dry-run", action="store_true", help="Don't create memories, just show what would be processed")
    parser.add_argument("--batch-size", type=int, default=10, help="Exchanges per Librarian batch (default: 10)")
    parser.add_argument("--limit", type=int, help="Maximum chats to process")
    parser.add_argument("--reprocess", action="store_true", help="Reprocess already-processed chats")
    parser.add_argument("--list-chats", action="store_true", help="Just list chats and exit")

    args = parser.parse_args()

    if args.list_chats:
        all_chats = get_all_chats()
        state = load_backfill_state()
        processed = set(state.get("processed_chats", []))

        print(f"\nTotal chats: {len(all_chats)}")
        print(f"Already processed: {len(processed)}")
        print(f"\nChats:")
        for chat_id, path in all_chats:
            chat = load_chat(path)
            title = chat.get("title", "Untitled")[:50]
            exchanges = len(extract_exchanges(chat, chat_id=chat_id))
            status = "✓" if chat_id in processed else " "
            print(f"  [{status}] {chat_id[:8]}... - {title} ({exchanges} exchanges)")
        return

    asyncio.run(run_backfill(
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        limit=args.limit,
        skip_processed=not args.reprocess
    ))


if __name__ == "__main__":
    main()
