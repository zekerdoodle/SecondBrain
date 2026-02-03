"""
Memory Throttle System

Rate limits the Librarian agent to run at most once per 20 minutes.
Accumulates exchanges between runs in a buffer.
"""

import json
import time
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime
from filelock import FileLock

logger = logging.getLogger("ltm.throttle")

# Paths
MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
THROTTLE_FILE = MEMORY_DIR / "throttle_state.json"
BUFFER_FILE = MEMORY_DIR / "exchange_buffer.json"
LOCK_FILE = MEMORY_DIR / "throttle.lock"

# Configuration
THROTTLE_SECONDS = 20 * 60  # 20 minutes
MAX_BUFFER_SIZE = 100  # Max exchanges to buffer before forcing processing


def _ensure_dirs():
    """Ensure memory directories exist."""
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)


def get_throttle_state() -> Dict[str, Any]:
    """Get current throttle state."""
    _ensure_dirs()
    if THROTTLE_FILE.exists():
        try:
            with open(THROTTLE_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load throttle state: {e}")
    return {
        "last_librarian_run": 0,
        "last_gardener_run": 0,
        "total_librarian_runs": 0,
        "total_exchanges_processed": 0
    }


def _save_throttle_state(state: Dict[str, Any]):
    """Save throttle state to disk."""
    _ensure_dirs()
    with open(THROTTLE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def get_exchange_buffer() -> List[Dict[str, Any]]:
    """Get current exchange buffer."""
    _ensure_dirs()
    if BUFFER_FILE.exists():
        try:
            with open(BUFFER_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load exchange buffer: {e}")
    return []


def _save_exchange_buffer(buffer: List[Dict[str, Any]]):
    """Save exchange buffer to disk."""
    _ensure_dirs()
    with open(BUFFER_FILE, 'w') as f:
        json.dump(buffer, f, indent=2)


def add_exchange_to_buffer(exchange: Dict[str, Any]) -> bool:
    """
    Add an exchange to the buffer.

    Args:
        exchange: Dict with user_message, assistant_message, timestamp, session_id

    Returns:
        True if the Librarian should run (throttle allows it)
    """
    with FileLock(LOCK_FILE):
        buffer = get_exchange_buffer()

        # Add exchange with timestamp
        buffer.append({
            **exchange,
            "buffered_at": time.time(),
            "buffered_at_iso": datetime.now().isoformat()
        })

        # Trim buffer if too large
        if len(buffer) > MAX_BUFFER_SIZE:
            logger.warning(f"Buffer overflow, trimming to {MAX_BUFFER_SIZE}")
            buffer = buffer[-MAX_BUFFER_SIZE:]

        _save_exchange_buffer(buffer)

        # Check if Librarian should run
        return should_run_librarian()


def should_run_librarian() -> bool:
    """
    Check if the Librarian should run based on throttle.

    Returns True if:
    1. At least THROTTLE_SECONDS have passed since last run, AND
    2. There are exchanges in the buffer
    """
    state = get_throttle_state()
    buffer = get_exchange_buffer()

    if not buffer:
        return False

    time_since_last = time.time() - state.get("last_librarian_run", 0)
    return time_since_last >= THROTTLE_SECONDS


def consume_exchange_buffer() -> List[Dict[str, Any]]:
    """
    Consume and clear the exchange buffer.

    Call this when the Librarian runs.
    Returns the exchanges that were in the buffer.

    IMPORTANT: Only updates throttle state if there were actually exchanges.
    This prevents wasted runs and timer resets on empty buffers.
    """
    with FileLock(LOCK_FILE):
        buffer = get_exchange_buffer()

        # Only proceed if there are exchanges to process
        if not buffer:
            logger.info("Buffer empty, nothing to consume")
            return []

        _save_exchange_buffer([])  # Clear buffer

        # Update throttle state only when we actually processed exchanges
        state = get_throttle_state()
        state["last_librarian_run"] = time.time()
        state["last_librarian_run_iso"] = datetime.now().isoformat()
        state["total_librarian_runs"] = state.get("total_librarian_runs", 0) + 1
        state["total_exchanges_processed"] = state.get("total_exchanges_processed", 0) + len(buffer)
        _save_throttle_state(state)

        logger.info(f"Consumed {len(buffer)} exchanges from buffer")
        return buffer


def mark_gardener_run():
    """Mark that the Gardener ran."""
    with FileLock(LOCK_FILE):
        state = get_throttle_state()
        state["last_gardener_run"] = time.time()
        state["last_gardener_run_iso"] = datetime.now().isoformat()
        state["total_gardener_runs"] = state.get("total_gardener_runs", 0) + 1
        _save_throttle_state(state)


def get_buffer_stats() -> Dict[str, Any]:
    """Get buffer and throttle statistics."""
    state = get_throttle_state()
    buffer = get_exchange_buffer()

    time_since_librarian = time.time() - state.get("last_librarian_run", 0)
    time_until_next = max(0, THROTTLE_SECONDS - time_since_librarian)

    return {
        "buffer_size": len(buffer),
        "can_run_now": should_run_librarian(),
        "seconds_until_next_run": time_until_next,
        "minutes_until_next_run": round(time_until_next / 60, 1),
        "last_librarian_run": state.get("last_librarian_run_iso", "never"),
        "last_gardener_run": state.get("last_gardener_run_iso", "never"),
        "total_librarian_runs": state.get("total_librarian_runs", 0),
        "total_exchanges_processed": state.get("total_exchanges_processed", 0)
    }


def force_librarian_ready():
    """Force the Librarian to be ready to run (for manual triggers)."""
    with FileLock(LOCK_FILE):
        state = get_throttle_state()
        state["last_librarian_run"] = 0  # Reset timer
        _save_throttle_state(state)


def peek_buffer() -> List[Dict[str, Any]]:
    """Peek at buffer contents without consuming."""
    return get_exchange_buffer()


# Helper to format exchanges for display
def format_exchange_summary(exchange: Dict[str, Any]) -> str:
    """Format an exchange for logging/display."""
    user_msg = exchange.get("user_message", "")[:100]
    assistant_msg = exchange.get("assistant_message", "")[:100]
    ts = exchange.get("timestamp", exchange.get("buffered_at_iso", "?"))
    return f"[{ts}] User: {user_msg}... | Assistant: {assistant_msg}..."
