"""
Chronicler Background Agent Runner

Summarizes conversation threads so their descriptions are useful for
semantic search. Conversation threads are created by the Librarian with
generic descriptions; the Chronicler replaces those with natural 2-3
sentence summaries of what the conversation actually covered.

TRIGGERING:
    The Chronicler does NOT run on its own schedule. It is chained from the
    Librarian pipeline in main.py (_run_librarian_background). When the
    Librarian creates or updates conversation threads, it returns
    "affected_thread_ids" in its result. main.py then fires off this
    Chronicler with those specific thread IDs via asyncio.create_task.

    This means the Chronicler runs roughly every ~20 minutes (whenever the
    exchange buffer flushes and produces conversation-sourced atoms), but
    ONLY when conversation threads were actually touched.

    The scheduled task "ltm_chronicler" in scheduled_tasks.json is
    intentionally DISABLED — this chain is the only trigger.

HOW TO VERIFY IT'S RUNNING:
    grep "Starting background Chronicler" server.log
    grep "Chronicler completed" server.log
    Expected output:
      LTM: Starting background Chronicler run (threads=['thread_...'])
      LTM: Chronicler completed - completed, summarized N threads

Workflow:
1. Receive specific thread_ids from the Librarian chain (preferred), OR
   fall back to scanning for threads updated since last Chronicler run
2. Read all atom contents from those threads
3. Send atom contents to Haiku for summarization
4. Update thread descriptions with the summaries
5. ThreadMemoryManager.update() handles re-embedding automatically
6. Persist last_chronicler_run timestamp
"""

import json
import logging
import asyncio
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("ltm.chronicler")

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> Dict[str, Any]:
    """Load agent config from sibling config.yaml."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

_CONFIG = _load_config()

# Paths
_PROMPT_PATH = Path(__file__).parent / "prompt.md"
_STATE_DIR = Path(__file__).parent / "state"
_STATE_FILE = _STATE_DIR / "state.json"


def _get_system_prompt() -> str:
    """Load the chronicler system prompt from file."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    else:
        raise FileNotFoundError(f"Chronicler prompt not found at {_PROMPT_PATH}")


def _ensure_ltm_path():
    """Ensure LTM scripts are on sys.path."""
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


# --- State management ---

def _load_state() -> Dict[str, Any]:
    """Load Chronicler state from disk."""
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load Chronicler state: {e}")
    return {}


def _save_state(state: Dict[str, Any]):
    """Save Chronicler state to disk."""
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def _get_last_run() -> Optional[str]:
    """Get last_chronicler_run ISO timestamp, or None if never run."""
    state = _load_state()
    return state.get("last_chronicler_run")


def _set_last_run(timestamp: str):
    """Set last_chronicler_run ISO timestamp."""
    state = _load_state()
    state["last_chronicler_run"] = timestamp
    _save_state(state)


# --- Structured output schema ---

CHRONICLER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summaries": {
            "type": "array",
            "description": "List of thread summaries",
            "items": {
                "type": "object",
                "properties": {
                    "thread_id": {
                        "type": "string",
                        "description": "The thread ID being summarized"
                    },
                    "summary": {
                        "type": "string",
                        "description": "2-3 sentence natural summary of the conversation"
                    }
                },
                "required": ["thread_id", "summary"]
            }
        }
    },
    "required": ["summaries"]
}


# --- Core logic ---

def _find_threads_needing_summary() -> List[Any]:
    """Find conversation threads updated since last Chronicler run.

    Returns Thread objects that need (re-)summarization.
    """
    _ensure_ltm_path()
    from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    all_threads = thread_mgr.list_all()

    last_run = _get_last_run()

    candidates = []
    for t in all_threads:
        if t.thread_type != "conversation":
            continue
        # No atoms = nothing to summarize
        if not t.memory_ids:
            continue
        # If never run, all conversation threads are candidates
        if last_run is None:
            candidates.append(t)
            continue
        # If thread was updated after last run, it's a candidate
        if t.last_updated and t.last_updated > last_run:
            candidates.append(t)

    return candidates


def _get_atom_contents(memory_ids: List[str]) -> List[str]:
    """Get atom content strings for a list of atom IDs."""
    _ensure_ltm_path()
    from atomic_memory import get_atomic_manager

    atom_mgr = get_atomic_manager()
    contents = []
    for mid in memory_ids:
        atom = atom_mgr.get(mid)
        if atom:
            contents.append(atom.content)
    return contents


def _build_prompt(threads_data: List[Dict[str, Any]]) -> str:
    """Build the prompt for the Chronicler with thread atom contents."""
    parts = [
        f"Summarize each of the following {len(threads_data)} conversation threads.\n"
        "For each thread, write a 2-3 sentence summary based on the atoms (facts) extracted from that conversation.\n"
    ]

    for td in threads_data:
        parts.append(f"## Thread: {td['thread_id']}")
        parts.append(f"Name: {td['name']}")
        parts.append(f"Atom count: {len(td['atoms'])}")
        parts.append("Atoms:")
        for i, content in enumerate(td["atoms"], 1):
            parts.append(f"  {i}. {content}")
        parts.append("")

    parts.append(
        "---\n\n"
        "For each thread, return its thread_id and your summary."
    )

    return "\n".join(parts)


async def run_chronicler(
    max_threads_per_batch: int = 10,
    thread_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Run the Chronicler agent to summarize conversation threads.

    If thread_ids is provided, only those specific threads are processed
    (used when chaining from Librarian). Otherwise, finds all threads
    needing summarization since last run (used for scheduled runs).

    Args:
        max_threads_per_batch: Max threads to process in a single LLM call.
        thread_ids: Specific thread IDs to process. If None, scans for all
                    threads updated since last run.

    Returns:
        Dict with status and processing stats.
    """
    _ensure_ltm_path()
    from thread_memory import get_thread_manager

    # Record the run timestamp at the START so we don't miss threads
    # updated while we're processing
    run_timestamp = datetime.now().isoformat()

    # Find threads that need summarization
    if thread_ids:
        # Targeted mode: only process specific threads (from Librarian chain)
        thread_mgr = get_thread_manager()
        threads = []
        for tid in thread_ids:
            t = thread_mgr.get(tid)
            if t and t.thread_type == "conversation" and t.memory_ids:
                threads.append(t)
        logger.info(f"Chronicler: Targeted mode - processing {len(threads)} specific thread(s)")
    else:
        # Scan mode: find all threads needing summarization (scheduled runs)
        threads = _find_threads_needing_summary()

    if not threads:
        logger.info("Chronicler: No conversation threads need summarization")
        # Still update last_run so we don't re-scan unchanged threads
        _set_last_run(run_timestamp)
        return {"status": "no_work", "threads_processed": 0}

    logger.info(f"Chronicler: Found {len(threads)} threads needing summarization")

    # Prepare thread data (read atom contents)
    threads_data = []
    for t in threads:
        atoms = _get_atom_contents(t.memory_ids)
        if atoms:  # Only summarize threads with actual atom content
            threads_data.append({
                "thread_id": t.id,
                "name": t.name,
                "atoms": atoms
            })

    if not threads_data:
        logger.info("Chronicler: No threads with atom content to summarize")
        _set_last_run(run_timestamp)
        return {"status": "no_content", "threads_processed": 0}

    # Process in batches
    stats = {
        "status": "completed",
        "threads_found": len(threads_data),
        "threads_summarized": 0,
        "threads_failed": 0,
        "batches_run": 0,
        "errors": []
    }

    batches = [
        threads_data[i:i + max_threads_per_batch]
        for i in range(0, len(threads_data), max_threads_per_batch)
    ]

    for batch_idx, batch in enumerate(batches):
        logger.info(
            f"Chronicler: Processing batch {batch_idx + 1}/{len(batches)} "
            f"({len(batch)} threads)"
        )

        batch_result = await _run_chronicler_batch(batch)
        stats["batches_run"] += 1

        if batch_result.get("error"):
            stats["errors"].append(batch_result["error"])
            stats["threads_failed"] += len(batch)
            continue

        # Apply summaries
        summaries = batch_result.get("summaries", [])
        thread_mgr = get_thread_manager()

        for summary_item in summaries:
            tid = summary_item.get("thread_id", "")
            summary = summary_item.get("summary", "").strip()

            if not tid or not summary:
                stats["errors"].append(f"Empty thread_id or summary in response")
                stats["threads_failed"] += 1
                continue

            thread = thread_mgr.get(tid)
            if not thread:
                stats["errors"].append(f"Thread not found: {tid}")
                stats["threads_failed"] += 1
                continue

            # Update the description — ThreadMemoryManager.update() handles
            # re-embedding automatically when description changes
            thread_mgr.update(
                thread_id=tid,
                description=summary
            )
            stats["threads_summarized"] += 1
            logger.info(
                f"Chronicler: Updated thread '{thread.name}' description "
                f"({len(summary)} chars)"
            )

    # Update last run timestamp
    _set_last_run(run_timestamp)

    if stats["errors"]:
        stats["status"] = "partial" if stats["threads_summarized"] > 0 else "error"

    logger.info(
        f"Chronicler complete: {stats['threads_summarized']} summarized, "
        f"{stats['threads_failed']} failed, {stats['batches_run']} batches"
    )

    return stats


async def _run_chronicler_batch(
    threads_data: List[Dict[str, Any]]
) -> Dict[str, Any]:
    """
    Run a single Chronicler batch via the SDK.

    Args:
        threads_data: List of thread dicts with thread_id, name, atoms.

    Returns:
        Dict with summaries list or error.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    system_prompt = _get_system_prompt()
    prompt = _build_prompt(threads_data)

    timeout_seconds = _CONFIG.get("timeout_seconds") or _CONFIG.get("timeout") or 120

    try:
        result = None
        async with asyncio.timeout(timeout_seconds):
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    model=_CONFIG.get("model", "haiku"),
                    system_prompt=system_prompt,
                    max_turns=_CONFIG.get("max_turns", 2),
                    permission_mode="bypassPermissions",
                    allowed_tools=_CONFIG.get("tools", []),
                    setting_sources=[],
                    output_format={
                        "type": "json_schema",
                        "schema": CHRONICLER_OUTPUT_SCHEMA
                    }
                )
            ):
                if isinstance(message, ResultMessage) and message.structured_output:
                    result = message.structured_output

        if not result:
            logger.error("Chronicler: No structured output received")
            return {"error": "No structured output", "summaries": []}

        return result

    except asyncio.TimeoutError:
        logger.error(f"Chronicler batch timed out after {timeout_seconds}s")
        return {"error": f"Timed out after {timeout_seconds}s", "summaries": []}

    except Exception as e:
        import traceback
        logger.error(f"Chronicler batch failed: {e}\n{traceback.format_exc()}")
        return {"error": str(e), "summaries": []}


# --- Entry points ---

async def run_chronicler_cycle(
    thread_ids: Optional[List[str]] = None
) -> Dict[str, Any]:
    """
    Main entry point for scheduled/triggered Chronicler runs.

    Args:
        thread_ids: If provided, only summarize these specific threads.
                    Used when chaining from Librarian (targeted mode).
                    If None, scans for all threads needing summarization.

    Wraps run_chronicler() with logging and error handling.
    """
    logger.info("Starting Chronicler cycle")
    try:
        result = await run_chronicler(thread_ids=thread_ids)
        logger.info(f"Chronicler cycle complete: {result}")
        return result
    except Exception as e:
        import traceback
        logger.error(f"Chronicler cycle failed: {e}\n{traceback.format_exc()}")
        return {"status": "error", "error": str(e)}


def run_chronicler_sync() -> Dict[str, Any]:
    """Synchronous wrapper for run_chronicler_cycle."""
    return asyncio.run(run_chronicler_cycle())
