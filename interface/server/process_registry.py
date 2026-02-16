"""
Process Registry - Tracks running Claude processes (agents + primary).

Simple JSON file at .claude/process_registry.json.
Provides register/deregister/read with file locking for concurrent safety.

Each entry has a unique `id` for deregistration. The `pid` field is used
for dead-process pruning (if the OS PID is gone, the entry is stale).
"""

import json
import fcntl
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("process_registry")

REGISTRY_FILE = Path("/home/debian/second_brain/.claude/process_registry.json")


def _read_registry() -> list:
    """Read the registry file. Returns empty list if missing/corrupt."""
    if not REGISTRY_FILE.exists():
        return []
    try:
        with open(REGISTRY_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _write_registry(entries: list) -> None:
    """Write the registry file atomically."""
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = REGISTRY_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(entries, f, indent=2)
    tmp.rename(REGISTRY_FILE)


def _locked_update(fn):
    """
    Execute fn(entries) -> entries under an exclusive file lock.
    fn receives current entries and must return the new entries list.
    """
    REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_path = REGISTRY_FILE.with_suffix(".lock")
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            entries = _read_registry()
            entries = fn(entries)
            _write_registry(entries)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _unique_agent_name(entries: list, base_name: str) -> str:
    """
    Return a unique agent name. If base_name already exists in entries,
    suffix with _1, _2, etc.
    """
    existing = {e["agent"] for e in entries}
    if base_name not in existing:
        return base_name
    i = 1
    while f"{base_name}_{i}" in existing:
        i += 1
    return f"{base_name}_{i}"


def clear_registry() -> None:
    """
    Clear all entries from the registry.

    Called at server startup to remove stale entries from previous runs.
    """
    _write_registry([])
    logger.info("Cleared process registry")


_SENTINEL = object()

def register_process(
    agent_name: str,
    task: str = "active",
    pid: Optional[int] = _SENTINEL,
) -> str:
    """
    Register a running process.

    Args:
        agent_name: Base name (e.g. "librarian", "primary_claude").
                    Will be suffixed if duplicates exist.
        task: Description of what the process is doing (truncated to 80 chars).
        pid: OS process ID for dead-process pruning. Defaults to os.getpid().
             Pass None explicitly for managed processes (e.g. SDK agents)
             where the real subprocess PID is not accessible.

    Returns:
        A unique registration ID (use this with deregister_process).
    """
    if pid is _SENTINEL:
        pid = os.getpid()
    task_truncated = task[:80] if task else "active"
    reg_id = str(uuid.uuid4())[:8]
    registered_name = None

    def _do_register(entries):
        nonlocal registered_name
        registered_name = _unique_agent_name(entries, agent_name)
        entries.append({
            "id": reg_id,
            "pid": pid,
            "agent": registered_name,
            "task": task_truncated,
            "started": datetime.utcnow().isoformat(),
        })
        return entries

    _locked_update(_do_register)
    pid_label = f"PID {pid}" if pid is not None else "managed"
    logger.info(f"Registered process: {registered_name} ({pid_label}, id={reg_id})")
    return reg_id


def deregister_process(reg_id: str) -> None:
    """
    Remove a process from the registry by its registration ID.

    Args:
        reg_id: The registration ID returned by register_process().
    """
    def _do_deregister(entries):
        return [e for e in entries if e.get("id") != reg_id]

    _locked_update(_do_deregister)
    logger.info(f"Deregistered process id={reg_id}")


def deregister_by_pid(pid: Optional[int] = None) -> None:
    """
    Remove all entries for a given PID. Used for server shutdown cleanup.

    Args:
        pid: Process ID to remove. Defaults to os.getpid().
    """
    if pid is None:
        pid = os.getpid()

    def _do_deregister(entries):
        return [e for e in entries if e.get("pid") != pid]

    _locked_update(_do_deregister)
    logger.info(f"Deregistered all processes with PID {pid}")


def get_process_list() -> list:
    """
    Read the current registry and prune dead PIDs.

    Returns entries for processes whose OS PID is still alive.
    """
    entries = _read_registry()

    # Prune dead PIDs (entries with pid=None are managed and always kept)
    alive = []
    pruned = False
    for entry in entries:
        pid = entry.get("pid")
        if pid is None:
            # Managed process (e.g. SDK agent) — no PID to check, keep it
            alive.append(entry)
        elif _pid_alive(pid):
            alive.append(entry)
        else:
            pruned = True
            logger.debug(f"Pruned dead process: {entry.get('agent')} (PID {pid})")

    # Write back if we pruned anything
    if pruned:
        try:
            _locked_update(lambda _: alive)
        except Exception:
            pass  # Non-critical — next read will prune again

    return alive


def _pid_alive(pid: int) -> bool:
    """Check if a PID is still running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
