"""
Gardener Background Agent Runner

Structured-output memory maintenance agent. The Gardener receives atom data
with pre-computed candidate threads, returns assignment decisions as JSON,
and the runner applies them programmatically.

Workflow:
1. Pre-compute top candidate threads for each atom (via embeddings)
2. Format full context (atoms + candidates + thread summary) as prompt
3. Gardener returns structured JSON decisions
4. apply_gardener_decisions() executes assignments, thread creation, supersession

Runs chained after Librarian, receiving new atom IDs.
"""

import json
import logging
import asyncio
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger("ltm.gardener")

_CONFIG_PATH = Path(__file__).parent / "config.yaml"


def _load_config() -> Dict[str, Any]:
    """Load agent config from sibling config.yaml."""
    with open(_CONFIG_PATH) as f:
        return yaml.safe_load(f) or {}

_CONFIG = _load_config()

# Thread size limits (from memory_system_redesign_v1.md)
THREAD_SIZE_SOFT_CAP = 50  # Log warning, flag for split
THREAD_SIZE_HARD_CAP = 75  # Force split before allowing new assignments

# Load system prompt
_PROMPT_PATH = Path(__file__).parent / "prompt.md"


def _get_system_prompt() -> str:
    """Load the gardener system prompt from file."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    else:
        raise FileNotFoundError(f"Gardener prompt not found at {_PROMPT_PATH}")


def _ensure_ltm_path():
    """Ensure LTM scripts are on sys.path."""
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


# --- Structured output schema for Gardener decisions ---

GARDENER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "description": "List of decisions for each atom processed",
            "items": {
                "type": "object",
                "properties": {
                    "atom_id": {
                        "type": "string",
                        "description": "The atom ID being processed"
                    },
                    "action": {
                        "type": "string",
                        "enum": ["assign", "create_and_assign", "supersede", "skip"],
                        "description": "Action to take"
                    },
                    "thread_name": {
                        "type": "string",
                        "description": "Existing thread name to assign to (for 'assign')"
                    },
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                        "description": "Assignment confidence level"
                    },
                    "new_thread_name": {
                        "type": "string",
                        "description": "Name for new thread (for 'create_and_assign')"
                    },
                    "new_thread_scope": {
                        "type": "string",
                        "description": "Scope for new thread (for 'create_and_assign')"
                    },
                    "supersede_content": {
                        "type": "string",
                        "description": "Updated content (for 'supersede')"
                    },
                    "supersede_reason": {
                        "type": "string",
                        "description": "Why superseding (for 'supersede')"
                    },
                    "skip_reason": {
                        "type": "string",
                        "description": "Why skipping (for 'skip')"
                    }
                },
                "required": ["atom_id", "action"]
            }
        },
        "thread_maintenance": {
            "type": "array",
            "description": "Optional thread-level maintenance actions (split, merge)",
            "items": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["split", "merge"],
                        "description": "Maintenance action"
                    },
                    "source_thread": {
                        "type": "string",
                        "description": "Thread name to split (for 'split')"
                    },
                    "merge_threads": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Thread names to merge (for 'merge')"
                    },
                    "new_threads": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "scope": {"type": "string"},
                                "atom_ids": {
                                    "type": "array",
                                    "items": {"type": "string"}
                                }
                            },
                            "required": ["name", "scope", "atom_ids"]
                        },
                        "description": "New thread definitions (for 'split')"
                    },
                    "merged_name": {"type": "string"},
                    "merged_scope": {"type": "string"}
                },
                "required": ["action"]
            }
        }
    },
    "required": ["decisions"]
}


# --- Pre-computation: find candidate threads for each atom ---

def _find_candidate_threads(atom_content: str, top_k: int = 5) -> List[Dict[str, Any]]:
    """Find top candidate threads for an atom using embeddings.

    Skips conversation-type threads — those are managed by the Librarian.
    """
    _ensure_ltm_path()
    from embeddings import get_embedding_manager, ContentType
    from thread_memory import get_thread_manager

    emb_mgr = get_embedding_manager()
    thread_mgr = get_thread_manager()

    results = emb_mgr.retrieve(
        atom_content,
        k=top_k * 2,
        threshold=0.3,
        content_type_filter=ContentType.THREAD
    )

    candidates = []
    seen = set()
    for meta, score in results:
        tid = meta.get("thread_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)

        thread = thread_mgr.get(tid)
        if thread and thread.thread_type != "conversation":
            candidates.append({
                "name": thread.name,
                "score": round(score, 3),
                "atom_count": len(thread.memory_ids),
                "scope": thread.scope or thread.description
            })

        if len(candidates) >= top_k:
            break

    return candidates


def _format_atoms_with_candidates(atoms: List[Dict[str, Any]]) -> str:
    """Format atoms with their pre-computed candidate threads."""
    lines = []
    for atom in atoms:
        tags = ", ".join(atom.get("tags", []))
        candidates = atom.get("candidates", [])

        lines.append(f"### Atom: {atom['id']}")
        lines.append(f"- Created: {atom.get('created_at', 'unknown')[:10]}")
        lines.append(f"- Content: {atom['content']}")
        lines.append(f"- Tags: {tags or 'none'}")

        if candidates:
            lines.append("- **Candidate threads** (by embedding similarity):")
            for c in candidates:
                lines.append(
                    f"  - {c['name']} (score={c['score']}, {c['atom_count']} atoms): "
                    f"{c['scope'][:60]}"
                )
        else:
            lines.append("- No similar threads found — may need a new thread")
        lines.append("")

    return "\n".join(lines)


def _format_thread_summary() -> str:
    """Get a summary of existing topical threads for context.

    Excludes conversation-type threads — they are managed by the Librarian
    and not relevant to the Gardener's organizational decisions.
    """
    _ensure_ltm_path()
    from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    all_threads = thread_mgr.list_all()
    # Only show topical threads to the Gardener
    threads = [t for t in all_threads if t.thread_type != "conversation"]

    if not threads:
        return "No existing threads."

    lines = [f"**{len(threads)} existing topical threads:**"]
    for t in sorted(threads, key=lambda x: len(x.memory_ids), reverse=True)[:50]:
        scope = t.scope or t.description
        lines.append(f"- {t.name} ({len(t.memory_ids)} atoms): {scope[:80]}")

    if len(threads) > 50:
        lines.append(f"... and {len(threads) - 50} more threads")

    return "\n".join(lines)


def _get_triage_atoms() -> List[Dict[str, Any]]:
    """Get atoms with low-confidence assignments (triage queue)."""
    _ensure_ltm_path()
    from atomic_memory import get_atomic_manager

    atom_mgr = get_atomic_manager()
    triage = atom_mgr.get_low_confidence_atoms()

    return [
        {
            "id": a.id,
            "content": a.content,
            "created_at": a.created_at,
            "tags": a.tags,
            "assignment_confidence": a.assignment_confidence
        }
        for a in triage
    ]


# --- Thread size enforcement ---

def check_thread_size_limits() -> Dict[str, Any]:
    """
    Check all topical threads against size limits and return enforcement status.

    Skips conversation-type threads — they are managed separately with no size limits.

    Returns:
        Dict with 'blocked_threads' (over hard cap), 'warning_threads' (over soft cap),
        and 'requires_split' (thread IDs that must be split before new assignments)
    """
    _ensure_ltm_path()
    from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    threads = [t for t in thread_mgr.list_all() if t.thread_type != "conversation"]

    blocked = []  # Over hard cap (75)
    warning = []  # Over soft cap (50)

    for t in threads:
        size = len(t.memory_ids)
        if size >= THREAD_SIZE_HARD_CAP:
            blocked.append({
                "id": t.id,
                "name": t.name,
                "size": size,
                "over_by": size - THREAD_SIZE_HARD_CAP
            })
            logger.error(
                f"THREAD SIZE HARD CAP EXCEEDED: '{t.name}' has {size} atoms "
                f"(limit: {THREAD_SIZE_HARD_CAP}). Must split before new assignments."
            )
        elif size >= THREAD_SIZE_SOFT_CAP:
            warning.append({
                "id": t.id,
                "name": t.name,
                "size": size,
                "over_by": size - THREAD_SIZE_SOFT_CAP
            })
            logger.warning(
                f"Thread size warning: '{t.name}' has {size} atoms "
                f"(soft cap: {THREAD_SIZE_SOFT_CAP}). Consider splitting."
            )

    return {
        "blocked_threads": blocked,
        "warning_threads": warning,
        "requires_split": [t["id"] for t in blocked]
    }


def can_assign_to_thread(thread_id: str) -> Tuple[bool, str]:
    """
    Check if a thread can accept new atom assignments.

    Returns:
        Tuple of (can_assign: bool, reason: str)
    """
    _ensure_ltm_path()
    from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    thread = thread_mgr.get(thread_id)

    if not thread:
        return False, f"Thread not found: {thread_id}"

    # Conversation threads are managed by the Librarian — Gardener never assigns to them
    if thread.thread_type == "conversation":
        return False, f"Thread '{thread.name}' is a system-managed conversation thread"

    size = len(thread.memory_ids)

    if size >= THREAD_SIZE_HARD_CAP:
        return False, (
            f"Thread '{thread.name}' has {size} atoms (hard cap: {THREAD_SIZE_HARD_CAP}). "
            f"Must split before accepting new assignments."
        )

    if size >= THREAD_SIZE_SOFT_CAP:
        logger.warning(
            f"Thread '{thread.name}' has {size} atoms (soft cap: {THREAD_SIZE_SOFT_CAP}). "
            f"Assignment allowed but split recommended."
        )

    return True, "OK"


# --- Apply decisions ---

async def apply_gardener_decisions(decisions: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply Gardener decisions to the memory store.

    Processes each decision by directly calling the underlying managers.
    """
    _ensure_ltm_path()
    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    stats = {
        "assigned": 0,
        "threads_created": 0,
        "superseded": 0,
        "skipped": 0,
        "splits": 0,
        "merges": 0,
        "errors": []
    }

    # Process atom decisions
    for decision in decisions.get("decisions", []):
        try:
            atom_id = decision.get("atom_id", "")
            action = decision.get("action", "")

            atom = atom_mgr.get(atom_id) if atom_id else None

            if action == "assign":
                thread_name = decision.get("thread_name", "")
                confidence = decision.get("confidence", "medium")
                thread = thread_mgr.get_by_name(thread_name) if thread_name else None

                if not atom:
                    stats["errors"].append(f"Atom not found: {atom_id}")
                    continue
                if not thread:
                    stats["errors"].append(f"Thread not found: {thread_name}")
                    continue

                # Enforce thread size limits before assignment
                can_assign, reason = can_assign_to_thread(thread.id)
                if not can_assign:
                    stats["errors"].append(
                        f"BLOCKED: Cannot assign {atom_id} to '{thread_name}': {reason}"
                    )
                    stats["blocked_by_size"] = stats.get("blocked_by_size", 0) + 1
                    continue

                # Add atom to thread
                if atom_id not in thread.memory_ids:
                    thread.memory_ids.append(atom_id)
                    thread.last_updated = datetime.now().isoformat()

                # Set confidence
                atom.assignment_confidence[thread.id] = confidence
                stats["assigned"] += 1

            elif action == "create_and_assign":
                new_name = decision.get("new_thread_name", "")
                new_scope = decision.get("new_thread_scope", "")
                confidence = decision.get("confidence", "high")

                if not new_name:
                    stats["errors"].append(f"Missing thread name for create_and_assign: {atom_id}")
                    continue

                # Check if thread already exists
                existing = thread_mgr.get_by_name(new_name)
                if existing:
                    # Enforce thread size limits for existing threads
                    can_assign, reason = can_assign_to_thread(existing.id)
                    if not can_assign:
                        stats["errors"].append(
                            f"BLOCKED: Cannot assign {atom_id} to existing '{new_name}': {reason}"
                        )
                        stats["blocked_by_size"] = stats.get("blocked_by_size", 0) + 1
                        continue
                    thread = existing
                else:
                    thread = thread_mgr.create(
                        name=new_name,
                        description=new_scope,
                        scope=new_scope
                    )
                    stats["threads_created"] += 1

                if atom and atom_id not in thread.memory_ids:
                    thread.memory_ids.append(atom_id)
                    thread.last_updated = datetime.now().isoformat()

                if atom:
                    atom.assignment_confidence[thread.id] = confidence
                stats["assigned"] += 1

            elif action == "supersede":
                new_content = decision.get("supersede_content", "")
                reason = decision.get("supersede_reason", "")

                if not atom:
                    stats["errors"].append(f"Atom not found for supersede: {atom_id}")
                    continue

                if new_content and new_content != atom.content:
                    atom_mgr.update(
                        atom_id,
                        content=new_content,
                        superseded_reason=reason or "Updated by Gardener"
                    )
                    stats["superseded"] += 1

            elif action == "skip":
                stats["skipped"] += 1

        except Exception as e:
            stats["errors"].append(f"Error processing {decision}: {e}")

    # Process thread maintenance
    for maint in decisions.get("thread_maintenance", []):
        try:
            action = maint.get("action", "")

            if action == "split":
                source_name = maint.get("source_thread", "")
                new_threads = maint.get("new_threads", [])
                source = thread_mgr.get_by_name(source_name)

                # Never split conversation threads
                if source and source.thread_type == "conversation":
                    stats["errors"].append(
                        f"BLOCKED: Cannot split conversation thread '{source_name}'"
                    )
                    continue

                if source and new_threads:
                    thread_defs = [
                        {"name": nt["name"], "scope": nt["scope"], "atom_ids": nt["atom_ids"]}
                        for nt in new_threads
                    ]
                    thread_mgr.split_thread(source.id, thread_defs)
                    stats["splits"] += 1

            elif action == "merge":
                thread_names = maint.get("merge_threads", [])
                merged_name = maint.get("merged_name", "")
                merged_scope = maint.get("merged_scope", "")

                if len(thread_names) >= 2 and merged_name:
                    all_atom_ids = []
                    sources = []
                    for name in thread_names:
                        t = thread_mgr.get_by_name(name)
                        if t:
                            # Never merge conversation threads
                            if t.thread_type == "conversation":
                                stats["errors"].append(
                                    f"BLOCKED: Cannot merge conversation thread '{name}'"
                                )
                                continue
                            all_atom_ids.extend(t.memory_ids)
                            sources.append(t)

                    unique_ids = list(dict.fromkeys(all_atom_ids))
                    merged = thread_mgr.create(
                        name=merged_name,
                        description=merged_scope,
                        memory_ids=unique_ids,
                        scope=merged_scope
                    )
                    for s in sources:
                        thread_mgr.delete(s.id)
                    stats["merges"] += 1

        except Exception as e:
            stats["errors"].append(f"Maintenance error: {e}")

    # Save changes
    thread_mgr._save()
    atom_mgr._save()

    return stats


# --- Main Gardener entry points ---

async def run_gardener(
    atom_ids: List[str],
    include_triage: bool = True
) -> Dict[str, Any]:
    """
    Run the Gardener agent on a batch of atoms.

    Uses structured output: pre-computes candidate threads, asks the Gardener
    for decisions, then applies them programmatically.

    Args:
        atom_ids: List of atom IDs to process
        include_triage: Whether to also process triage queue

    Returns:
        Dict with status and processing stats
    """
    _ensure_ltm_path()
    from atomic_memory import get_atomic_manager
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    atom_mgr = get_atomic_manager()

    # Pre-check thread size limits
    size_check = check_thread_size_limits()
    if size_check["blocked_threads"]:
        logger.error(
            f"WARNING: {len(size_check['blocked_threads'])} threads exceed hard cap "
            f"({THREAD_SIZE_HARD_CAP} atoms). Assignments to these will be blocked: "
            f"{[t['name'] for t in size_check['blocked_threads']]}"
        )

    # Gather atom data with pre-computed candidates
    atoms_data = []
    for aid in atom_ids:
        atom = atom_mgr.get(aid)
        if atom:
            atom_dict = {
                "id": atom.id,
                "content": atom.content,
                "created_at": atom.created_at,
                "tags": atom.tags,
                "candidates": _find_candidate_threads(atom.content, top_k=5)
            }
            atoms_data.append(atom_dict)

    if not atoms_data and not include_triage:
        return {"status": "empty", "atoms_processed": 0}

    # Get triage atoms (also with candidates)
    triage_atoms = []
    if include_triage:
        raw_triage = _get_triage_atoms()
        for ta in raw_triage:
            ta["candidates"] = _find_candidate_threads(ta["content"], top_k=5)
            triage_atoms.append(ta)

    # Build prompt
    system_prompt = _get_system_prompt()
    thread_summary = _format_thread_summary()

    prompt_parts = [f"## Thread Overview\n\n{thread_summary}\n"]

    # Add size warnings to prompt
    if size_check["blocked_threads"] or size_check["warning_threads"]:
        size_warnings = ["## ⚠️ Thread Size Alerts\n"]
        if size_check["blocked_threads"]:
            size_warnings.append(
                f"**BLOCKED ({THREAD_SIZE_HARD_CAP}+ atoms - cannot accept assignments):**"
            )
            for t in size_check["blocked_threads"]:
                size_warnings.append(f"  - {t['name']}: {t['size']} atoms (MUST SPLIT)")
            size_warnings.append("")
        if size_check["warning_threads"]:
            size_warnings.append(
                f"**Warning ({THREAD_SIZE_SOFT_CAP}+ atoms - consider splitting):**"
            )
            for t in size_check["warning_threads"]:
                size_warnings.append(f"  - {t['name']}: {t['size']} atoms")
            size_warnings.append("")
        prompt_parts.append("\n".join(size_warnings))

    if atoms_data:
        prompt_parts.append(
            f"## New Atoms to Process ({len(atoms_data)} atoms)\n\n"
            f"{_format_atoms_with_candidates(atoms_data)}\n"
        )

    if triage_atoms:
        prompt_parts.append(
            f"## Triage Queue ({len(triage_atoms)} atoms needing re-evaluation)\n\n"
            f"{_format_atoms_with_candidates(triage_atoms)}\n"
        )

    prompt_parts.append(
        "---\n\n"
        "For each atom, decide: assign to an existing thread, create a new thread, "
        "supersede with updated content, or skip. Use the candidate threads as starting "
        "points but also consider the full thread list. Output your decisions as JSON."
    )

    prompt = "\n".join(prompt_parts)

    logger.info(
        f"Running Gardener on {len(atoms_data)} new atoms, "
        f"{len(triage_atoms)} triage atoms"
    )

    timeout_seconds = _CONFIG.get("timeout_seconds") or _CONFIG.get("timeout") or 300

    try:
        result = None
        async with asyncio.timeout(timeout_seconds):
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    model=_CONFIG.get("model", "sonnet"),
                    system_prompt=system_prompt,
                    max_turns=_CONFIG.get("max_turns", 2),
                    permission_mode="bypassPermissions",
                    allowed_tools=_CONFIG.get("tools", []),
                    setting_sources=[],
                    output_format={
                        "type": "json_schema",
                        "schema": GARDENER_OUTPUT_SCHEMA
                    }
                )
            ):
                if isinstance(message, ResultMessage) and message.structured_output:
                    result = message.structured_output

        if not result:
            logger.error("Gardener returned no structured output")
            return {"status": "error", "error": "No output", "atoms_processed": 0}

        logger.info(
            f"Gardener returned {len(result.get('decisions', []))} decisions, "
            f"{len(result.get('thread_maintenance', []))} maintenance actions"
        )

        # Apply decisions
        stats = await apply_gardener_decisions(result)

        return {
            "status": "completed",
            "atoms_processed": len(atoms_data),
            "triage_processed": len(triage_atoms),
            **stats
        }

    except asyncio.TimeoutError:
        logger.error(f"Gardener agent timed out after {timeout_seconds}s")
        return {
            "status": "timeout",
            "error": f"Timed out after {timeout_seconds}s",
            "atoms_processed": 0
        }

    except Exception as e:
        import traceback
        logger.error(f"Gardener agent failed: {e}\n{traceback.format_exc()}")
        return {
            "status": "error",
            "error": str(e),
            "atoms_processed": 0
        }


async def run_gardener_batched(
    atom_ids: Optional[List[str]] = None,
    max_batch_size: int = 20
) -> Dict[str, Any]:
    """
    Run the Gardener in batches of max_batch_size atoms.

    If atom_ids is empty/None, processes only the triage queue.

    Args:
        atom_ids: Atom IDs to process (None = triage only)
        max_batch_size: Max atoms per Gardener run (default: 20)

    Returns:
        Combined stats from all batches
    """
    atom_ids = atom_ids or []

    combined = {
        "status": "completed",
        "atoms_processed": 0,
        "triage_processed": 0,
        "batches_run": 0,
        "assigned": 0,
        "threads_created": 0,
        "superseded": 0,
        "errors": []
    }

    if not atom_ids:
        # Just process triage queue
        result = await run_gardener([], include_triage=True)
        combined["triage_processed"] = result.get("triage_processed", 0)
        combined["batches_run"] = 1
        combined["assigned"] = result.get("assigned", 0)
        if result.get("error"):
            combined["errors"].append(result["error"])
        return combined

    # Split into batches
    batches = [atom_ids[i:i + max_batch_size] for i in range(0, len(atom_ids), max_batch_size)]

    for i, batch in enumerate(batches):
        is_last = (i == len(batches) - 1)
        logger.info(f"Gardener batch {i+1}/{len(batches)}: {len(batch)} atoms")

        result = await run_gardener(
            batch,
            include_triage=is_last  # Only process triage on last batch
        )

        combined["atoms_processed"] += result.get("atoms_processed", 0)
        combined["batches_run"] += 1
        combined["assigned"] += result.get("assigned", 0)
        combined["threads_created"] += result.get("threads_created", 0)
        combined["superseded"] += result.get("superseded", 0)

        if is_last:
            combined["triage_processed"] = result.get("triage_processed", 0)

        if result.get("error"):
            combined["errors"].append(result["error"])

        if result.get("errors"):
            combined["errors"].extend(result["errors"])

    if combined["errors"]:
        combined["status"] = "partial"

    logger.info(
        f"Gardener batched run complete: {combined['atoms_processed']} atoms, "
        f"{combined['batches_run']} batches, {combined['assigned']} assigned"
    )

    return combined


# --- Thread health check ---

def get_thread_health() -> Dict[str, Any]:
    """
    Check thread health metrics for maintenance reporting.

    Returns stats on oversized threads, orphan atoms, triage queue, etc.
    Conversation-type threads are reported separately and excluded from
    size-limit checks (they are immutable snapshots).
    """
    _ensure_ltm_path()
    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    all_threads = thread_mgr.list_all()
    atoms = atom_mgr.list_all()

    # Separate thread types
    topical_threads = [t for t in all_threads if t.thread_type != "conversation"]
    conversation_threads = [t for t in all_threads if t.thread_type == "conversation"]

    # Thread size distribution (topical only — conversation threads exempt)
    sizes = [len(t.memory_ids) for t in topical_threads]
    oversized = [t for t in topical_threads if len(t.memory_ids) > 50]
    warning = [t for t in topical_threads if 25 < len(t.memory_ids) <= 50]

    # Atoms with no thread assignments (count both types)
    assigned_atom_ids = set()
    for t in all_threads:
        assigned_atom_ids.update(t.memory_ids)
    orphan_atoms = [a for a in atoms if a.id not in assigned_atom_ids]

    # Triage queue (low confidence)
    triage = [a for a in atoms if any(
        v == "low" for v in (a.assignment_confidence or {}).values()
    )]

    return {
        "total_threads": len(all_threads),
        "topical_threads": len(topical_threads),
        "conversation_threads": len(conversation_threads),
        "total_atoms": len(atoms),
        "total_assignments": sum(sizes),
        "avg_thread_size": round(sum(sizes) / len(sizes), 1) if sizes else 0,
        "max_thread_size": max(sizes) if sizes else 0,
        "oversized_threads": [
            {"name": t.name, "size": len(t.memory_ids)} for t in oversized
        ],
        "warning_threads": [
            {"name": t.name, "size": len(t.memory_ids)} for t in warning
        ],
        "orphan_atoms": len(orphan_atoms),
        "triage_queue": len(triage),
        "orphan_atom_ids": [a.id for a in orphan_atoms[:50]]  # Cap at 50 for reporting
    }


async def run_maintenance() -> Dict[str, Any]:
    """
    Run a full Gardener maintenance cycle:
    1. Check thread health
    2. Process triage queue (low-confidence re-evaluation)
    3. Process orphan atoms (atoms with no thread assignment)
    4. Report results

    This is designed to run on a schedule (e.g., daily at 3 AM).
    """
    logger.info("Starting Gardener maintenance run")

    # Step 1: Health check
    health = get_thread_health()
    logger.info(
        f"Health: {health['total_threads']} threads, {health['total_atoms']} atoms, "
        f"{health['orphan_atoms']} orphans, {health['triage_queue']} triage"
    )

    results = {"health": health}

    # Step 2: Process orphan atoms (if any)
    orphan_ids = health.get("orphan_atom_ids", [])
    if orphan_ids:
        logger.info(f"Processing {len(orphan_ids)} orphan atoms")
        orphan_result = await run_gardener_batched(atom_ids=orphan_ids, max_batch_size=20)
        results["orphan_processing"] = orphan_result
    else:
        results["orphan_processing"] = {"status": "none_needed"}

    # Step 3: Process triage queue (runs via include_triage=True with empty atom list)
    if health["triage_queue"] > 0:
        logger.info(f"Processing {health['triage_queue']} triage atoms")
        triage_result = await run_gardener_batched(atom_ids=[], max_batch_size=20)
        results["triage_processing"] = triage_result
    else:
        results["triage_processing"] = {"status": "none_needed"}

    # Step 4: Flag oversized threads for splitting
    if health["oversized_threads"]:
        results["maintenance_needed"] = {
            "oversized_threads": health["oversized_threads"],
            "action": "These threads exceed 50 atoms and should be reviewed for splitting"
        }

    logger.info("Gardener maintenance run complete")
    return results


# Synchronous wrapper
def run_gardener_sync(atom_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Synchronous wrapper for run_gardener_batched."""
    return asyncio.run(run_gardener_batched(atom_ids=atom_ids))
