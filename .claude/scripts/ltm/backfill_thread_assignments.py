"""
Backfill Thread Assignments

Reprocesses all atoms through the Gardener to rebuild thread assignments.
Designed to be run after the memory system redesign.

Phases:
1. Snapshot current thread→atom mappings (for rollback)
2. Clear all thread assignments from atoms
3. Process atoms chronologically in batches through Gardener
4. Checkpoint at 10% for validation
5. Supersession sweep after all atoms processed
6. Triage review at end

State is tracked in .claude/memory/thread_backfill_state.json for resumability.
"""

import json
import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime

logger = logging.getLogger("ltm.backfill")

# Paths
MEMORY_DIR = Path(__file__).parent.parent.parent / "memory"
STATE_FILE = MEMORY_DIR / "thread_backfill_state.json"
SNAPSHOT_FILE = MEMORY_DIR / "thread_assignment_snapshot.json"


def _load_state() -> Dict[str, Any]:
    """Load backfill state from disk."""
    if STATE_FILE.exists():
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {
        "phase": "not_started",
        "atoms_processed": 0,
        "total_atoms": 0,
        "checkpoint_reached": False,
        "checkpoint_validated": False,
        "batches_completed": 0,
        "started_at": None,
        "last_updated": None,
        "errors": []
    }


def _save_state(state: Dict[str, Any]):
    """Save backfill state to disk."""
    state["last_updated"] = datetime.now().isoformat()
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)


def snapshot_thread_assignments() -> Dict[str, Any]:
    """
    Phase 1: Snapshot current thread→atom mappings for rollback.

    Returns:
        Dict with snapshot details
    """
    from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    threads = thread_mgr.list_all()

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "threads": []
    }

    for t in threads:
        snapshot["threads"].append({
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "scope": t.scope,
            "memory_ids": t.memory_ids.copy(),
            "split_from": t.split_from,
            "split_into": t.split_into
        })

    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(SNAPSHOT_FILE, 'w') as f:
        json.dump(snapshot, f, indent=2)

    logger.info(
        f"Snapshot saved: {len(snapshot['threads'])} threads, "
        f"{sum(len(t['memory_ids']) for t in snapshot['threads'])} assignments"
    )

    return {
        "threads": len(snapshot["threads"]),
        "total_assignments": sum(len(t["memory_ids"]) for t in snapshot["threads"]),
        "file": str(SNAPSHOT_FILE)
    }


def clear_thread_assignments():
    """
    Phase 2: Clear all thread assignments from atoms.

    Keeps atoms and threads intact - only removes atom→thread relationships.
    Also clears assignment_confidence on atoms.
    """
    from thread_memory import get_thread_manager
    from atomic_memory import get_atomic_manager

    thread_mgr = get_thread_manager()
    atom_mgr = get_atomic_manager()

    # Clear memory_ids from all threads
    for thread in thread_mgr.list_all():
        if thread.memory_ids:
            thread.memory_ids = []
            thread.last_updated = datetime.now().isoformat()
    thread_mgr._save()

    # Clear assignment_confidence from all atoms
    for atom in atom_mgr.list_all():
        if atom.assignment_confidence:
            atom.assignment_confidence = {}
    atom_mgr._save()

    logger.info("Cleared all thread assignments and confidence scores")


def get_atoms_chronological() -> List[str]:
    """Get all atom IDs sorted by created_at (oldest first)."""
    from atomic_memory import get_atomic_manager

    atom_mgr = get_atomic_manager()
    atoms = atom_mgr.list_all()

    # Sort by created_at
    sorted_atoms = sorted(atoms, key=lambda a: a.created_at or "")

    return [a.id for a in sorted_atoms]


async def run_supersession_sweep() -> Dict[str, Any]:
    """
    Dedicated supersession sweep that catches cross-batch supersession patterns.

    Groups atoms by entity/topic using thread co-membership, then uses Claude
    to identify temporal supersession patterns within each group.

    Returns:
        Dict with supersession stats
    """
    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    logger.info("Running supersession sweep...")

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    # Group atoms by thread membership
    # Atoms in the same thread are candidates for supersession
    thread_atom_groups: Dict[str, List[str]] = {}
    for thread in thread_mgr.list_all():
        if len(thread.memory_ids) > 1:  # Only threads with multiple atoms
            thread_atom_groups[thread.id] = thread.memory_ids

    stats = {
        "threads_analyzed": 0,
        "supersessions_found": 0,
        "atoms_updated": 0,
        "errors": []
    }

    # Define the structured output schema for supersession detection
    supersession_schema = {
        "type": "object",
        "properties": {
            "supersessions": {
                "type": "array",
                "description": "List of supersession relationships found",
                "items": {
                    "type": "object",
                    "properties": {
                        "old_atom_id": {
                            "type": "string",
                            "description": "ID of the older atom being superseded"
                        },
                        "new_atom_id": {
                            "type": "string",
                            "description": "ID of the newer atom that supersedes it"
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why the old fact is superseded (e.g., 'Status changed', 'Preference updated')"
                        },
                        "merged_content": {
                            "type": "string",
                            "description": "Updated content for the old atom that reflects it's been superseded"
                        }
                    },
                    "required": ["old_atom_id", "new_atom_id", "reason", "merged_content"]
                }
            }
        },
        "required": ["supersessions"]
    }

    # Process each thread's atoms for supersession
    for thread_id, atom_ids in thread_atom_groups.items():
        thread = thread_mgr.get(thread_id)
        if not thread:
            continue

        # Get all atoms in this thread, sorted by timestamp
        atoms = []
        for aid in atom_ids:
            atom = atom_mgr.get(aid)
            if atom:
                atoms.append({
                    "id": atom.id,
                    "content": atom.content,
                    "created_at": atom.created_at,
                    "already_superseded": len(atom.previous_versions) > 0
                })

        # Skip if too few atoms or all already superseded
        if len(atoms) < 2:
            continue

        # Sort by timestamp
        atoms.sort(key=lambda a: a["created_at"] or "")

        # Build prompt for supersession detection
        atoms_text = []
        for a in atoms:
            superseded_note = " [already has previous versions]" if a["already_superseded"] else ""
            atoms_text.append(
                f"- **{a['id']}** ({a['created_at'][:10] if a['created_at'] else '?'}){superseded_note}\n"
                f"  {a['content']}"
            )

        prompt = f"""## Thread: {thread.name}
Scope: {thread.scope or thread.description}

## Atoms (chronological order, oldest first)

{chr(10).join(atoms_text)}

---

Analyze these atoms for temporal supersession patterns. Look for cases where:
1. A newer fact updates or replaces an older fact about the same thing
2. Status changes (e.g., "is looking for job" → "got a job")
3. Preference updates (e.g., "prefers X" → "now prefers Y")
4. Corrections or updates to previously stated information

For each supersession found, provide:
- The OLD atom ID (the one being superseded)
- The NEW atom ID (the one that supersedes it)
- A brief reason for supersession
- Updated content for the OLD atom that reflects it's been superseded (e.g., "[Superseded] Was looking for job - see atom_xxx for update")

If no supersession relationships exist, return an empty supersessions array.
Only flag clear supersession - not atoms that are merely related."""

        try:
            result = None
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    model="haiku",  # Use Haiku for efficiency
                    system_prompt=(
                        "You are a supersession detector. Analyze atoms for temporal "
                        "supersession patterns where newer facts replace older ones. "
                        "Be conservative - only flag clear supersession, not mere relations."
                    ),
                    max_turns=1,
                    permission_mode="bypassPermissions",
                    allowed_tools=[],
                    setting_sources=[],
                    output_format={
                        "type": "json_schema",
                        "schema": supersession_schema
                    }
                )
            ):
                if isinstance(message, ResultMessage) and message.structured_output:
                    result = message.structured_output

            stats["threads_analyzed"] += 1

            if result and result.get("supersessions"):
                for sup in result["supersessions"]:
                    old_id = sup.get("old_atom_id", "")
                    new_id = sup.get("new_atom_id", "")
                    reason = sup.get("reason", "Superseded by newer atom")
                    merged = sup.get("merged_content", "")

                    # Verify both atoms exist
                    old_atom = atom_mgr.get(old_id)
                    new_atom = atom_mgr.get(new_id)

                    if not old_atom or not new_atom:
                        stats["errors"].append(f"Invalid atom IDs: {old_id}, {new_id}")
                        continue

                    # Skip if old atom already has this supersession
                    if old_atom.previous_versions and any(
                        new_id in str(v) for v in old_atom.previous_versions
                    ):
                        continue

                    # Apply supersession
                    if merged:
                        atom_mgr.update(
                            old_id,
                            content=merged,
                            superseded_reason=f"{reason} (superseded by {new_id})"
                        )
                        stats["atoms_updated"] += 1
                        stats["supersessions_found"] += 1
                        logger.info(
                            f"Supersession: {old_id} → {new_id} ({reason})"
                        )

        except Exception as e:
            stats["errors"].append(f"Error processing thread {thread.name}: {e}")

    logger.info(
        f"Supersession sweep complete: {stats['threads_analyzed']} threads, "
        f"{stats['supersessions_found']} supersessions found"
    )

    return stats


async def run_backfill(
    batch_size: int = 50,
    checkpoint_pct: float = 0.10,
    resume: bool = True
) -> Dict[str, Any]:
    """
    Run the full backfill pipeline.

    Args:
        batch_size: Atoms per Gardener batch (default: 50)
        checkpoint_pct: Stop at this percentage for validation (default: 10%)
        resume: If True, resume from previous state; if False, start fresh

    Returns:
        Backfill results dict
    """
    import sys
    gardener_dir = Path(__file__).parent.parent.parent / "agents" / "background" / "gardener"
    if str(gardener_dir) not in sys.path:
        sys.path.insert(0, str(gardener_dir))

    from gardener_runner import run_gardener_batched

    state = _load_state() if resume else {}

    # Phase 1: Snapshot (if not done)
    if state.get("phase") in (None, "not_started"):
        logger.info("Phase 1: Snapshotting current assignments")
        snapshot_info = snapshot_thread_assignments()
        state = {
            "phase": "snapshot_done",
            "atoms_processed": 0,
            "total_atoms": 0,
            "checkpoint_reached": False,
            "checkpoint_validated": False,
            "batches_completed": 0,
            "started_at": datetime.now().isoformat(),
            "snapshot": snapshot_info,
            "errors": []
        }
        _save_state(state)

    # Phase 2: Clear assignments (if not done)
    if state.get("phase") == "snapshot_done":
        logger.info("Phase 2: Clearing thread assignments")
        clear_thread_assignments()
        state["phase"] = "cleared"
        _save_state(state)

    # Phase 3: Process atoms chronologically
    if state.get("phase") in ("cleared", "processing"):
        atom_ids = get_atoms_chronological()
        state["total_atoms"] = len(atom_ids)
        state["phase"] = "processing"

        # Resume from where we left off
        start_idx = state.get("atoms_processed", 0)
        checkpoint_idx = int(len(atom_ids) * checkpoint_pct)

        logger.info(
            f"Phase 3: Processing {len(atom_ids)} atoms "
            f"(resuming from {start_idx}, checkpoint at {checkpoint_idx})"
        )

        # Process in batches
        for batch_start in range(start_idx, len(atom_ids), batch_size):
            batch_end = min(batch_start + batch_size, len(atom_ids))
            batch = atom_ids[batch_start:batch_end]

            logger.info(f"Batch {state['batches_completed'] + 1}: atoms {batch_start}-{batch_end}")

            try:
                result = await run_gardener_batched(
                    atom_ids=batch,
                    max_batch_size=20  # Gardener processes max 20 at a time
                )

                state["atoms_processed"] = batch_end
                state["batches_completed"] += 1

                if result.get("errors"):
                    state["errors"].extend(result["errors"])

            except Exception as e:
                logger.error(f"Batch failed: {e}")
                state["errors"].append(str(e))

            _save_state(state)

            # Check if we've reached the checkpoint
            if not state["checkpoint_reached"] and batch_end >= checkpoint_idx:
                state["checkpoint_reached"] = True
                state["phase"] = "checkpoint"
                _save_state(state)
                logger.info(
                    f"CHECKPOINT: Processed {batch_end}/{len(atom_ids)} atoms "
                    f"({100 * batch_end / len(atom_ids):.1f}%). "
                    f"Review thread assignments and call continue_backfill() to proceed."
                )
                return {
                    "status": "checkpoint",
                    "atoms_processed": batch_end,
                    "total_atoms": len(atom_ids),
                    "percentage": 100 * batch_end / len(atom_ids),
                    "message": "Checkpoint reached. Validate assignments before continuing.",
                    "errors": state["errors"]
                }

        state["phase"] = "processing_done"
        _save_state(state)

    # Phase 4: Supersession sweep (dedicated pass to catch cross-batch supersession)
    if state.get("phase") == "processing_done":
        logger.info("Phase 4: Supersession sweep (catching cross-batch patterns)")
        try:
            supersession_result = await run_supersession_sweep()
            state["supersession_result"] = supersession_result
            logger.info(
                f"Supersession sweep: {supersession_result.get('supersessions_found', 0)} found, "
                f"{supersession_result.get('atoms_updated', 0)} atoms updated"
            )
        except Exception as e:
            import traceback
            state["errors"].append(f"Supersession sweep failed: {e}\n{traceback.format_exc()}")

        # Also run triage cleanup after supersession sweep
        logger.info("Phase 4b: Triage queue cleanup")
        try:
            triage_result = await run_gardener_batched(atom_ids=[], max_batch_size=20)
            state["triage_result"] = triage_result
        except Exception as e:
            state["errors"].append(f"Triage cleanup failed: {e}")

        state["phase"] = "completed"
        state["completed_at"] = datetime.now().isoformat()
        _save_state(state)

    return {
        "status": state.get("phase", "unknown"),
        "atoms_processed": state.get("atoms_processed", 0),
        "total_atoms": state.get("total_atoms", 0),
        "batches_completed": state.get("batches_completed", 0),
        "errors": state.get("errors", [])
    }


async def continue_backfill() -> Dict[str, Any]:
    """
    Continue backfill after checkpoint validation.

    Call this after reviewing the 10% checkpoint results.
    """
    state = _load_state()

    if state.get("phase") != "checkpoint":
        return {
            "status": "error",
            "message": f"Cannot continue - current phase is '{state.get('phase')}', expected 'checkpoint'"
        }

    state["checkpoint_validated"] = True
    state["phase"] = "processing"  # Resume processing
    _save_state(state)

    return await run_backfill(resume=True)


def rollback_assignments() -> Dict[str, Any]:
    """
    Rollback thread assignments from the snapshot.

    Restores the exact thread→atom mappings from before the backfill.
    """
    if not SNAPSHOT_FILE.exists():
        return {"status": "error", "message": "No snapshot file found"}

    from thread_memory import get_thread_manager

    with open(SNAPSHOT_FILE, 'r') as f:
        snapshot = json.load(f)

    thread_mgr = get_thread_manager()

    restored = 0
    for thread_data in snapshot.get("threads", []):
        thread = thread_mgr.get(thread_data["id"])
        if thread:
            thread.memory_ids = thread_data["memory_ids"]
            thread.last_updated = datetime.now().isoformat()
            restored += 1

    thread_mgr._save()

    return {
        "status": "restored",
        "threads_restored": restored,
        "total_in_snapshot": len(snapshot.get("threads", []))
    }


def get_backfill_status() -> Dict[str, Any]:
    """Get current backfill status."""
    state = _load_state()
    return {
        "phase": state.get("phase", "not_started"),
        "atoms_processed": state.get("atoms_processed", 0),
        "total_atoms": state.get("total_atoms", 0),
        "percentage": (
            100 * state["atoms_processed"] / state["total_atoms"]
            if state.get("total_atoms", 0) > 0 else 0
        ),
        "checkpoint_reached": state.get("checkpoint_reached", False),
        "checkpoint_validated": state.get("checkpoint_validated", False),
        "batches_completed": state.get("batches_completed", 0),
        "errors": len(state.get("errors", [])),
        "started_at": state.get("started_at"),
        "last_updated": state.get("last_updated")
    }
