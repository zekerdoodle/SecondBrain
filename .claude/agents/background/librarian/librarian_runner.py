"""
Librarian Background Agent Runner

Extracts memories from conversation exchanges using Claude Sonnet via Agent SDK.
Uses OAuth authentication (same as main Claude Code agent).

The Librarian:
1. Processes buffered exchanges
2. Extracts atomic facts (content + tags)
3. Deduplicates against existing memories

Thread assignment and organization is handled by the Gardener agent.
"""

import json
import logging
import asyncio
import time
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("ltm.librarian")

# Load system prompt from prompt.md
_PROMPT_PATH = Path(__file__).parent / "prompt.md"
_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> Dict[str, Any]:
    """Load agent config from sibling config.yaml."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

_CONFIG = _load_config()


def _get_system_prompt() -> str:
    """Load the librarian system prompt from file."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    else:
        raise FileNotFoundError(f"Librarian prompt not found at {_PROMPT_PATH}")


# Simplified JSON Schema — extraction only, no threads or importance
LIBRARIAN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "atomic_memories": {
            "type": "array",
            "description": "List of atomic facts extracted from the conversation.",
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Clear, standalone fact about the user or our conversation"
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "1-3 categorization tags (lowercase, underscore-separated)"
                    }
                },
                "required": ["content"]
            }
        },
        "skipped_reason": {
            "type": "string",
            "description": "Why no memories were extracted, if applicable"
        }
    },
    "required": ["atomic_memories"]
}


def _format_exchanges(exchanges: List[Dict[str, Any]]) -> str:
    """Format exchanges for the Librarian prompt."""
    formatted = []
    for i, ex in enumerate(exchanges, 1):
        ts = ex.get("timestamp", ex.get("buffered_at_iso", ""))
        user = ex.get("user_message", "")[:2000]
        assistant = ex.get("assistant_message", "")[:2000]
        session = ex.get("session_id", "unknown")

        formatted.append(f"""### Exchange {i} [{ts}]
**Session**: {session}

**User**: {user}

**Assistant**: {assistant}
""")
    return "\n---\n".join(formatted)


def _format_existing_memories(memories: List[Dict[str, Any]], limit: int = 50) -> str:
    """Format existing memories for deduplication context."""
    if not memories:
        return "No existing memories."

    lines = []
    for mem in memories[:limit]:
        content = mem.get("content", "")[:200]
        lines.append(f"- {content}")

    return "\n".join(lines)


async def run_librarian(
    exchanges: List[Dict[str, Any]],
    existing_memories: Optional[List[Dict[str, Any]]] = None,
    room_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    Run the Librarian agent to extract memories from exchanges.

    Uses Claude Agent SDK with OAuth (same auth as main agent).
    Uses structured outputs for validated JSON response.

    All exchanges in a single call should come from the SAME room/chat.
    The room_id is set deterministically — no LLM attribution needed.

    Args:
        exchanges: List of exchange dicts with user_message, assistant_message, timestamp
        existing_memories: Current memories for deduplication context
        room_id: The room/chat ID that all exchanges belong to (set by caller)

    Returns:
        Extraction results with atomic_memories list.
        Also includes 'batch_timestamp' and 'room_id'.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    if not exchanges:
        logger.info("No exchanges to process")
        return {"atomic_memories": [], "skipped_reason": "No exchanges"}

    # Extract the earliest timestamp from exchanges for use in atom creation
    batch_timestamp = None
    for ex in exchanges:
        ts = ex.get("timestamp")
        if ts:
            if batch_timestamp is None or ts < batch_timestamp:
                batch_timestamp = ts

    # Room ID is set deterministically by the caller — no LLM guessing
    if not room_id:
        # Fallback: use session_id from first exchange (backward compat)
        room_id = exchanges[0].get("session_id", "unknown")

    # Load system prompt
    system_prompt = _get_system_prompt()

    # Build the prompt — no thread context needed
    prompt = f"""## Exchanges to Process ({len(exchanges)} total)

{_format_exchanges(exchanges)}

## Existing Memories (for deduplication - don't extract duplicates)

{_format_existing_memories(existing_memories or [])}

---

Analyze these exchanges and extract any important atomic facts."""

    logger.info(f"Running Librarian on {len(exchanges)} exchanges (room: {room_id[:16]}...)")

    timeout_seconds = _CONFIG.get("timeout_seconds") or _CONFIG.get("timeout") or 120

    try:
        result = None
        # Consume entire async generator to avoid cleanup issues
        async with asyncio.timeout(timeout_seconds):
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    model=_CONFIG.get("model", "sonnet"),
                    system_prompt=system_prompt,
                    max_turns=_CONFIG.get("max_turns", 2),
                    permission_mode="bypassPermissions",
                    allowed_tools=_CONFIG.get("tools", []),
                    output_format={
                        "type": "json_schema",
                        "schema": LIBRARIAN_OUTPUT_SCHEMA
                    }
                )
            ):
                # With structured outputs, we get validated JSON directly
                if isinstance(message, ResultMessage) and message.structured_output:
                    result = message.structured_output

        # Process result after generator is fully consumed
        if result:
            result.setdefault("atomic_memories", [])
            result["batch_timestamp"] = batch_timestamp
            result["room_id"] = room_id
            logger.info(
                f"Librarian extracted {len(result['atomic_memories'])} memories"
            )
            return result

        # Fallback if no structured output received
        logger.warning("No structured output received from Librarian")
        return {"atomic_memories": [], "error": "No structured output", "batch_timestamp": batch_timestamp, "room_id": room_id}

    except asyncio.TimeoutError:
        logger.error(f"Librarian agent timed out after {timeout_seconds}s")
        return {
            "atomic_memories": [],
            "error": f"Timed out after {timeout_seconds}s",
            "batch_timestamp": batch_timestamp,
            "room_id": room_id
        }

    except Exception as e:
        logger.error(f"Librarian agent failed: {e}")
        return {
            "atomic_memories": [],
            "error": str(e),
            "batch_timestamp": batch_timestamp,
            "room_id": room_id
        }


async def apply_librarian_results(results: Dict[str, Any], created_at: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply the Librarian's extraction results to the memory store.

    Creates atomic memories only — no thread assignment (that's the Gardener's job).
    All atoms get the room_id from results (set deterministically by the caller).

    Args:
        results: The Librarian's extraction results dict.
        created_at: Optional timestamp to use for created atoms. If not provided,
                   falls back to results.get('batch_timestamp'), then current time.

    Returns:
        Dict with stats including 'new_atom_ids' for Gardener chaining.
    """
    # Determine the timestamp to use for atom creation
    atom_timestamp = created_at or results.get("batch_timestamp")
    # Room ID set deterministically — all atoms in this batch belong to the same room
    room_id = results.get("room_id")
    # Import from the original location (still in .claude/scripts/ltm/)
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from atomic_memory import get_atomic_manager

    atom_mgr = get_atomic_manager()

    stats = {
        "memories_created": 0,
        "memories_skipped_duplicate": 0,
        "new_atom_ids": [],
        "errors": []
    }

    for mem_data in results.get("atomic_memories", []):
        try:
            content = mem_data.get("content", "").strip()
            if not content:
                continue

            # Check for duplicates
            existing = atom_mgr.find_similar(content, threshold=0.88)
            if existing:
                logger.debug(f"Skipping duplicate: {content[:50]}")
                stats["memories_skipped_duplicate"] += 1
                continue

            # Create the memory — room_id is deterministic, not LLM-guessed
            atom = atom_mgr.create(
                content=content,
                tags=mem_data.get("tags", []),
                created_at=atom_timestamp,
                source_session_id=room_id
            )
            stats["memories_created"] += 1
            stats["new_atom_ids"].append(atom.id)

        except Exception as e:
            logger.error(f"Failed to create memory: {e}")
            stats["errors"].append(str(e))

    logger.info(
        f"Applied Librarian results: {stats['memories_created']} memories created, "
        f"{stats['memories_skipped_duplicate']} duplicates skipped"
    )

    return stats


def _get_room_title(room_id: str) -> Optional[str]:
    """Look up the chat title for a room/chat ID from disk.

    Returns the title string, or None if the chat file doesn't exist
    or has no title.
    """
    chats_dir = Path(__file__).parent.parent.parent.parent / "chats"
    chat_file = chats_dir / f"{room_id}.json"
    if not chat_file.exists():
        return None
    try:
        with open(chat_file, 'r') as f:
            data = json.load(f)
        return data.get("title")
    except Exception:
        return None


def _format_conversation_thread_name(room_id: str) -> str:
    """Create a human-readable conversation thread name for a room.

    Tries to use the room's chat title. Falls back to a truncated room ID.
    Example: "Conversation: Memory system redesign"
    """
    title = _get_room_title(room_id)
    if title:
        return f"Conversation: {title}"
    return f"Conversation: {room_id[:12]}"


async def create_conversation_threads(
    new_atom_ids: List[str],
    exchanges: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Create or update conversation-type threads, grouping atoms by room.

    This is a deterministic post-processing step — no LLM calls needed.
    Each room gets exactly ONE conversation thread that grows as the
    conversation grows. The Gardener will never touch these threads
    (split, merge, or reorganize).

    Atoms remain multi-threaded: they belong to the conversation thread
    AND whatever topical threads the Gardener assigns them to.

    Args:
        new_atom_ids: Atom IDs just created by the Librarian.
        exchanges: The raw exchanges that were processed (for timestamp context).

    Returns:
        Dict with stats about conversation threads created/updated.
    """
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    stats = {
        "conversation_threads_created": 0,
        "conversation_threads_updated": 0,
        "atoms_assigned_to_conversations": 0,
        "affected_thread_ids": [],
        "errors": []
    }

    if not new_atom_ids:
        return stats

    # Group atoms by source_session_id (which IS the room/chat ID)
    room_atoms: Dict[str, List[str]] = {}

    for atom_id in new_atom_ids:
        atom = atom_mgr.get(atom_id)
        if not atom:
            continue

        room_id = atom.source_session_id or "unknown"
        if room_id not in room_atoms:
            room_atoms[room_id] = []
        room_atoms[room_id].append(atom_id)

    # One conversation thread per room — create or append
    for room_id, atom_ids in room_atoms.items():
        try:
            # Check if a conversation thread already exists for this room
            existing = thread_mgr.get_conversation_thread_for_room(room_id)

            if existing:
                # Append new atoms to the existing conversation thread
                added = 0
                for aid in atom_ids:
                    if aid not in existing.memory_ids:
                        existing.memory_ids.append(aid)
                        added += 1
                existing.last_updated = datetime.now().isoformat()

                # Update name in case the chat title has changed
                existing.name = _format_conversation_thread_name(room_id)

                thread_mgr._save()
                stats["conversation_threads_updated"] += 1
                stats["atoms_assigned_to_conversations"] += added
                stats["affected_thread_ids"].append(existing.id)
                logger.info(
                    f"Appended {added} atoms to existing conversation thread "
                    f"for room {room_id[:16]}..."
                )
                continue

            # Create a new conversation thread for this room
            thread_name = _format_conversation_thread_name(room_id)
            description = (
                f"Conversation thread for room {room_id}. "
                f"Contains {len(atom_ids)} atoms."
            )

            thread = thread_mgr.create(
                name=thread_name,
                description=description,
                memory_ids=atom_ids,
                embed=False,  # Chronicler will provide a real description then embed
                scope=f"room:{room_id}",
                thread_type="conversation"
            )

            stats["conversation_threads_created"] += 1
            stats["atoms_assigned_to_conversations"] += len(atom_ids)
            stats["affected_thread_ids"].append(thread.id)
            logger.info(
                f"Created conversation thread: '{thread_name}' with {len(atom_ids)} atoms "
                f"(room: {room_id[:16]}...)"
            )

        except Exception as e:
            logger.error(f"Failed to create conversation thread for room {room_id}: {e}")
            stats["errors"].append(str(e))

    return stats


async def run_librarian_cycle() -> Dict[str, Any]:
    """
    Run a complete Librarian cycle:
    1. Check if we should run (throttle + buffer check)
    2. Consume the exchange buffer
    3. Group exchanges by room/chat ID
    4. Run one extraction per room (deterministic attribution)
    5. Apply results (create atoms)
    6. Create/update conversation threads (one per room/chat)
    7. Chain to Gardener for topical thread assignment

    Returns stats about the run.
    """
    # Import from the original location
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from memory_throttle import (
        get_exchange_buffer, get_throttle_state,
        consume_exchange_buffer, THROTTLE_SECONDS
    )
    from atomic_memory import get_atomic_manager

    # Check buffer and throttle separately for clear status reporting
    buffer = get_exchange_buffer()
    if not buffer:
        return {"status": "empty_buffer", "message": "No exchanges in buffer to process"}

    state = get_throttle_state()
    time_since_last = time.time() - state.get("last_librarian_run", 0)
    if time_since_last < THROTTLE_SECONDS:
        minutes_remaining = (THROTTLE_SECONDS - time_since_last) / 60
        return {
            "status": "throttled",
            "message": f"Throttled - {minutes_remaining:.1f} minutes until next run",
            "buffer_size": len(buffer)
        }

    # Consume buffer (atomic operation that also updates throttle state)
    exchanges = consume_exchange_buffer()
    if not exchanges:
        # Race condition: buffer emptied between check and consume
        return {"status": "empty_buffer", "message": "Buffer was emptied by another process"}

    # Group exchanges by room/chat ID for deterministic attribution
    room_exchanges: Dict[str, List[Dict[str, Any]]] = {}
    for ex in exchanges:
        room_id = ex.get("session_id", "unknown")
        if room_id not in room_exchanges:
            room_exchanges[room_id] = []
        room_exchanges[room_id].append(ex)

    logger.info(
        f"Processing {len(exchanges)} exchanges from {len(room_exchanges)} rooms"
    )

    # Get existing context for deduplication (no threads needed)
    atom_mgr = get_atomic_manager()
    existing_memories = [
        {"content": m.content, "id": m.id}
        for m in atom_mgr.list_all()[-100:]  # Last 100 for context
    ]

    # Run extraction per room — each call produces atoms for exactly one room
    all_new_atom_ids = []
    total_stats = {
        "memories_created": 0,
        "memories_skipped_duplicate": 0,
        "new_atom_ids": [],
        "errors": []
    }

    for room_id, room_exs in room_exchanges.items():
        results = await run_librarian(room_exs, existing_memories, room_id=room_id)
        stats = await apply_librarian_results(results)

        total_stats["memories_created"] += stats.get("memories_created", 0)
        total_stats["memories_skipped_duplicate"] += stats.get("memories_skipped_duplicate", 0)
        total_stats["new_atom_ids"].extend(stats.get("new_atom_ids", []))
        total_stats["errors"].extend(stats.get("errors", []))
        all_new_atom_ids.extend(stats.get("new_atom_ids", []))

    # -------------------------------------------------------------------------
    # Post-extraction pipeline: Conversation Threads + Gardener
    #
    # After atom extraction, two things happen in parallel (conceptually):
    #
    # 1. CONVERSATION THREADS: Atoms from chat exchanges are grouped by room_id
    #    into conversation-type threads. These threads get "affected_thread_ids"
    #    returned in conversation_stats, which main.py uses to chain the
    #    Chronicler (to write human-readable summaries of the conversations).
    #    → The Chronicler is NOT called here — it's chained in main.py after
    #      this function returns. See _run_librarian_background() in main.py.
    #
    # 2. GARDENER: ALL new atoms (regardless of source) are sent to the Gardener
    #    for assignment to topical threads. This runs here, inside the Librarian.
    # -------------------------------------------------------------------------

    # Step 1: Create/update conversation threads (deterministic grouping by room)
    # The returned "affected_thread_ids" tells main.py which threads to send
    # to the Chronicler for summarization.
    conversation_stats = {}
    if all_new_atom_ids:
        try:
            conversation_stats = await create_conversation_threads(all_new_atom_ids, exchanges)
            logger.info(
                f"Conversation threads: {conversation_stats.get('conversation_threads_created', 0)} created, "
                f"{conversation_stats.get('conversation_threads_updated', 0)} updated, "
                f"{conversation_stats.get('atoms_assigned_to_conversations', 0)} atoms assigned"
            )
        except Exception as e:
            logger.error(f"Conversation thread creation failed: {e}")
            conversation_stats = {"error": str(e)}

    # Step 2: Chain to Gardener — assigns ALL new atoms to topical threads
    gardener_stats = {}
    if all_new_atom_ids:
        try:
            from gardener_agent import run_gardener_batched
            gardener_stats = await run_gardener_batched(atom_ids=all_new_atom_ids)
            logger.info(f"Gardener chained: processed {len(all_new_atom_ids)} new atoms")
        except Exception as e:
            logger.error(f"Gardener chain failed: {e}")
            gardener_stats = {"error": str(e)}

    return {
        "status": "completed",
        "exchanges_processed": len(exchanges),
        "rooms_processed": len(room_exchanges),
        **total_stats,
        "conversation_threads": conversation_stats,
        "gardener": gardener_stats
    }


# Synchronous wrapper for non-async contexts
def run_librarian_sync(exchanges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Synchronous wrapper for run_librarian."""
    return asyncio.run(run_librarian(exchanges))
