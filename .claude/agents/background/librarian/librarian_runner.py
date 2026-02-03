"""
Librarian Background Agent Runner

Extracts memories from conversation exchanges using Claude Sonnet via Agent SDK.
Uses OAuth authentication (same as main Claude Code agent).

The Librarian:
1. Processes buffered exchanges
2. Extracts atomic facts and insights
3. Organizes facts into threads
4. Deduplicates against existing memories
"""

import json
import logging
import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("ltm.librarian")

# Load system prompt from librarian.md
_PROMPT_PATH = Path(__file__).parent / "librarian.md"

def _get_system_prompt() -> str:
    """Load the librarian system prompt from file."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    else:
        raise FileNotFoundError(f"Librarian prompt not found at {_PROMPT_PATH}")


# JSON Schema for structured output
# Network model: each atom specifies which threads it belongs to (usually 2-4)
LIBRARIAN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "atomic_memories": {
            "type": "array",
            "description": "List of memories to create. Each atom should belong to 2-4 relevant threads.",
            "items": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Clear, standalone fact about Zeke or our conversation"
                    },
                    "importance": {
                        "type": "integer",
                        "description": "Importance score 0-100",
                        "minimum": 0,
                        "maximum": 100
                    },
                    "thread_names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Names of threads this atom belongs to (usually 2-4). Creates network connections."
                    },
                    "tags": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Relevant categorization tags"
                    },
                    "source_context": {
                        "type": "string",
                        "description": "Brief context about the source"
                    }
                },
                "required": ["content", "importance", "thread_names"]
            }
        },
        "new_threads": {
            "type": "array",
            "description": "New threads to create (only if they don't exist yet)",
            "items": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Thread name (specific and descriptive)"
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of what this thread contains"
                    }
                },
                "required": ["name", "description"]
            }
        },
        "skipped_reason": {
            "type": "string",
            "description": "Why no memories were extracted, if applicable"
        }
    },
    "required": ["atomic_memories", "new_threads"]
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


def _format_existing_threads(threads: List[Dict[str, Any]], limit: int = 20) -> str:
    """Format existing threads for organization context."""
    if not threads:
        return "No existing threads."

    lines = []
    for thread in threads[:limit]:
        name = thread.get("name", "")
        desc = thread.get("description", "")[:100]
        count = len(thread.get("memory_ids", []))
        lines.append(f"- **{name}** ({count} memories): {desc}")

    return "\n".join(lines)


async def run_librarian(
    exchanges: List[Dict[str, Any]],
    existing_memories: Optional[List[Dict[str, Any]]] = None,
    existing_threads: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Run the Librarian agent to extract memories from exchanges.

    Uses Claude Agent SDK with OAuth (same auth as main agent).
    Uses structured outputs for validated JSON response.

    Args:
        exchanges: List of exchange dicts with user_message, assistant_message, timestamp
        existing_memories: Current memories for deduplication context
        existing_threads: Current threads for organization context

    Returns:
        Extraction results with atomic_memories, thread_updates, etc.
        Also includes 'batch_timestamp' - the earliest timestamp from the exchanges.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    if not exchanges:
        logger.info("No exchanges to process")
        return {"atomic_memories": [], "thread_updates": [], "skipped_reason": "No exchanges"}

    # Extract the earliest timestamp from exchanges for use in atom creation
    batch_timestamp = None
    for ex in exchanges:
        ts = ex.get("timestamp")
        if ts:
            if batch_timestamp is None or ts < batch_timestamp:
                batch_timestamp = ts

    # Load system prompt
    system_prompt = _get_system_prompt()

    # Build the prompt
    prompt = f"""## Exchanges to Process ({len(exchanges)} total)

{_format_exchanges(exchanges)}

## Existing Memories (for deduplication - don't extract duplicates)

{_format_existing_memories(existing_memories or [])}

## Existing Threads (for organization)

{_format_existing_threads(existing_threads or [])}

---

Analyze these exchanges and extract any important memories."""

    logger.info(f"Running Librarian on {len(exchanges)} exchanges")

    try:
        result = None
        # Consume entire async generator to avoid cleanup issues
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="sonnet",  # Better quality for memory extraction
                system_prompt=system_prompt,
                max_turns=2,  # Need 2 turns for structured output (tool call + result)
                permission_mode="bypassPermissions",
                allowed_tools=[],  # No tools needed - pure extraction
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
            result.setdefault("thread_updates", [])
            result["batch_timestamp"] = batch_timestamp
            logger.info(
                f"Librarian extracted {len(result['atomic_memories'])} memories, "
                f"{len(result['thread_updates'])} thread updates"
            )
            return result

        # Fallback if no structured output received
        logger.warning("No structured output received from Librarian")
        return {"atomic_memories": [], "thread_updates": [], "error": "No structured output", "batch_timestamp": batch_timestamp}

    except Exception as e:
        logger.error(f"Librarian agent failed: {e}")
        return {
            "atomic_memories": [],
            "thread_updates": [],
            "error": str(e),
            "batch_timestamp": batch_timestamp
        }


async def apply_librarian_results(results: Dict[str, Any], created_at: Optional[str] = None) -> Dict[str, Any]:
    """
    Apply the Librarian's extraction results to the memory store.

    Creates atomic memories and updates threads.

    Args:
        results: The Librarian's extraction results dict.
        created_at: Optional timestamp to use for created atoms. If not provided,
                   falls back to results.get('batch_timestamp'), then current time.
    """
    # Determine the timestamp to use for atom creation
    atom_timestamp = created_at or results.get("batch_timestamp")
    # Import from the original location (still in .claude/scripts/ltm/)
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    stats = {
        "memories_created": 0,
        "memories_skipped_duplicate": 0,
        "threads_created": 0,
        "thread_assignments": 0,
        "errors": []
    }

    # 1. Create any new threads first
    for thread_data in results.get("new_threads", []):
        try:
            thread_name = thread_data.get("name", "").strip()
            if not thread_name:
                continue

            # Check if thread already exists
            existing = thread_mgr.get_by_name(thread_name)
            if not existing:
                thread_mgr.create(
                    name=thread_name,
                    description=thread_data.get("description", "")
                )
                stats["threads_created"] += 1
                logger.debug(f"Created new thread: {thread_name}")

        except Exception as e:
            logger.error(f"Failed to create thread: {e}")
            stats["errors"].append(str(e))

    # 2. Create atomic memories and assign to their threads
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

            # Create the memory with original timestamp if available
            atom = atom_mgr.create(
                content=content,
                importance=mem_data.get("importance", 50),
                tags=mem_data.get("tags", []),
                created_at=atom_timestamp
            )
            stats["memories_created"] += 1

            # Assign to specified threads (network model: atom → multiple threads)
            thread_names = mem_data.get("thread_names", [])
            for thread_name in thread_names:
                thread_name = thread_name.strip()
                if not thread_name:
                    continue

                # Find or create thread
                thread = thread_mgr.get_by_name(thread_name)
                if not thread:
                    # Auto-create thread if referenced but doesn't exist
                    thread = thread_mgr.create(
                        name=thread_name,
                        description=f"Auto-created for atom: {content[:50]}..."
                    )
                    stats["threads_created"] += 1

                # Add atom to thread
                thread_mgr.add_memory_to_thread(thread.id, atom.id)
                stats["thread_assignments"] += 1

        except Exception as e:
            logger.error(f"Failed to create memory: {e}")
            stats["errors"].append(str(e))

    logger.info(
        f"Applied Librarian results: {stats['memories_created']} memories, "
        f"{stats['memories_skipped_duplicate']} duplicates skipped, "
        f"{stats['threads_created']} threads created, "
        f"{stats['thread_assignments']} thread assignments"
    )

    return stats


async def run_librarian_cycle() -> Dict[str, Any]:
    """
    Run a complete Librarian cycle:
    1. Check if we should run (throttle + buffer check)
    2. Consume the exchange buffer
    3. Run extraction
    4. Apply results

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
    from thread_memory import get_thread_manager

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

    # Get existing context for deduplication
    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    existing_memories = [
        {"content": m.content, "id": m.id, "importance": m.importance}
        for m in atom_mgr.list_all()[-100:]  # Last 100 for context
    ]
    existing_threads = [
        {"name": t.name, "description": t.description, "memory_ids": t.memory_ids}
        for t in thread_mgr.list_all()
    ]

    # Run extraction
    results = await run_librarian(exchanges, existing_memories, existing_threads)

    # Apply results
    stats = await apply_librarian_results(results)

    return {
        "status": "completed",
        "exchanges_processed": len(exchanges),
        **stats
    }


# Synchronous wrapper for non-async contexts
def run_librarian_sync(exchanges: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Synchronous wrapper for run_librarian."""
    return asyncio.run(run_librarian(exchanges))
