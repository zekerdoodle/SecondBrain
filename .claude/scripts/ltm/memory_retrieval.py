"""
Memory Retrieval System

Implements HYBRID RETRIEVAL strategy (thread-first + bonus atoms):

Phase 1 - Thread Selection:
1. Search threads and atoms by semantic similarity
2. Score threads by composite (semantic + child relevance + recency)
3. Fill budget with whole threads (all-or-nothing inclusion)

Phase 2 - Bonus Atom Retrieval:
4. With remaining budget, retrieve individually-relevant atoms from
   threads that WEREN'T selected in Phase 1
5. These "bonus atoms" catch important facts that would otherwise be
   missed because their parent thread didn't make the priority cut

Example:
- Budget: 20k tokens
- Phase 1: Thread retrieval uses 15k tokens (selected threads)
- Phase 2: Remaining 5k tokens filled with top-scoring atoms from
  non-selected threads, ranked by semantic similarity to query

This hybrid approach ensures both contextual coherence (through threads)
and individual fact coverage (through bonus atoms).
"""

import logging
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("ltm.retrieval")


def _format_timestamp_with_human_context(timestamp_str: str) -> str:
    """
    Format a timestamp with a human-friendly recency label when within a week.

    Examples:
    - Today | 2026-01-25 14:30
    - yesterday | 2026-01-24 08:00
    - a couple days ago | 2026-01-23 16:10
    - a few days ago | 2026-01-20 12:00
    - 2026-01-18 09:15 (older than a week, no label)
    """
    if not timestamp_str:
        return ""

    try:
        dt = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        day_delta = (now.date() - dt.date()).days

        # Format the precise timestamp portion
        date_str = dt.strftime("%Y-%m-%d %H:%M")

        # Determine human-readable label
        if day_delta == 0:
            return f"Today | {date_str}"
        elif day_delta == 1:
            return f"yesterday | {date_str}"
        elif 2 <= day_delta <= 3:
            return f"a couple days ago | {date_str}"
        elif 4 <= day_delta <= 6:
            # Check if same ISO week
            if dt.isocalendar()[1] == now.isocalendar()[1]:
                return f"earlier this week | {date_str}"
            else:
                return f"a few days ago | {date_str}"
        else:
            # Older than a week - just the date, no label
            return date_str
    except Exception:
        # Fallback: return original or truncated
        return timestamp_str[:16] if len(timestamp_str) >= 16 else timestamp_str

# Token estimation (rough approximation)
TOKENS_PER_CHAR = 0.25  # ~4 chars per token


def count_tokens(text: str) -> int:
    """Estimate token count for text."""
    return int(len(text) * TOKENS_PER_CHAR)


@dataclass
class MemoryContext:
    """
    Container for retrieved memory context from hybrid retrieval.

    Attributes:
        atomic_memories: Bonus atoms from non-selected threads (hybrid retrieval phase 2).
            Each dict includes: id, content, importance, created_at, semantic_score, source_thread
        threads: Selected threads with all their memories (phase 1).
            Each dict includes: id, name, description, memory_ids, last_updated, memories
        guaranteed_memories: Always-included atoms with importance=100.
            Each dict includes: id, content, importance, created_at
        total_tokens: Estimated total tokens used
        token_breakdown: Token usage by category {"threads", "bonus_atoms", "guaranteed"}
    """
    atomic_memories: List[Dict[str, Any]] = field(default_factory=list)
    threads: List[Dict[str, Any]] = field(default_factory=list)
    guaranteed_memories: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    token_breakdown: Dict[str, int] = field(default_factory=dict)

    def format_for_prompt(self) -> str:
        """Format memory context for injection into prompt."""
        sections = []

        # Guaranteed memories (importance=100) first
        if self.guaranteed_memories:
            sections.append("## Core Context (Always Included)")
            for mem in self.guaranteed_memories:
                sections.append(f"- {mem['content']}")
            sections.append("")

        # Threads with their memories
        if self.threads:
            for thread in self.threads:
                sections.append(f"## Thread: {thread['name']}")
                if thread.get('description'):
                    sections.append(f"*{thread['description']}*")
                for mem in thread.get('memories', []):
                    ts = _format_timestamp_with_human_context(mem.get('created_at', ''))
                    sections.append(f"- [{ts}] {mem['content']}")
                sections.append("")

        # Bonus atoms from non-selected threads (hybrid retrieval)
        # These are individually relevant facts that weren't included because their
        # parent thread didn't make the overall priority cut
        bonus_atoms = [m for m in self.atomic_memories
                       if not any(m['id'] in t.get('memory_ids', []) for t in self.threads)]
        if bonus_atoms:
            sections.append("## Additional Relevant Facts")
            sections.append("*Individually relevant facts from other contexts:*")
            for mem in bonus_atoms:
                ts = _format_timestamp_with_human_context(mem.get('created_at', ''))
                source = mem.get('source_thread')
                if source:
                    sections.append(f"- [{ts}] {mem['content']} *(from: {source})*")
                else:
                    sections.append(f"- [{ts}] {mem['content']}")
            sections.append("")

        return "\n".join(sections) if sections else ""


def _score_thread(
    thread: Dict[str, Any],
    semantic_score: float,
    max_atom_score: float
) -> float:
    """
    Calculate composite thread score with 70% semantic / 30% recency weighting.

    Semantic: max of direct thread similarity or best child atom similarity (0-1)
    Recency: normalized score based on last_updated, decays over 7 days (0-1)
    """
    # Base semantic score (0-1 range)
    base_semantic = max(semantic_score, max_atom_score)

    # Recency score (0-1 range, decays over 7 days)
    try:
        last_updated = datetime.fromisoformat(thread.get("last_updated", ""))
        hours_old = (datetime.now() - last_updated).total_seconds() / 3600
        # Normalize: 0 hours = 1.0, 168 hours (7 days) = 0.0
        recency_score = max(0.0, 1.0 - (hours_old / (24 * 7)))
    except Exception:
        recency_score = 0.0

    # 70% semantic / 30% recency
    SEMANTIC_WEIGHT = 0.7
    RECENCY_WEIGHT = 0.3

    return (SEMANTIC_WEIGHT * base_semantic) + (RECENCY_WEIGHT * recency_score)


def get_memory_context(
    query: str,
    token_budget: int = 20000,
    include_guaranteed: bool = True
) -> MemoryContext:
    """
    Retrieve memory context using hybrid retrieval strategy.

    Strategy:
    1. Select whole threads that fit within budget (thread-first)
    2. Fill remaining budget with bonus atoms from non-selected threads

    This hybrid approach ensures:
    - Contextual coherence: related facts stay grouped in threads
    - Coverage: individually-relevant facts aren't lost due to thread filtering

    Args:
        query: Search query (usually the user's message)
        token_budget: Maximum tokens for memory context
        include_guaranteed: Include importance=100 memories regardless of budget

    Returns:
        MemoryContext with:
        - threads: selected threads with all their memories
        - atomic_memories: bonus atoms from non-selected threads
        - guaranteed_memories: importance=100 atoms (always included)
        - token_breakdown: {"threads": N, "bonus_atoms": N, "guaranteed": N}
    """
    try:
        from .atomic_memory import get_atomic_manager
        from .thread_memory import get_thread_manager
    except ImportError:
        from atomic_memory import get_atomic_manager
        from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    context = MemoryContext()
    used_tokens = 0

    # 1. Get guaranteed memories first (importance=100)
    if include_guaranteed:
        guaranteed = atom_mgr.get_guaranteed()
        for atom in guaranteed:
            mem_dict = {
                "id": atom.id,
                "content": atom.content,
                "importance": atom.importance,
                "created_at": atom.created_at
            }
            context.guaranteed_memories.append(mem_dict)
            # Guaranteed memories don't count against budget
        logger.info(f"Found {len(guaranteed)} guaranteed memories")

    # 2. Search threads and atoms
    # Search more atoms to ensure we have candidates for hybrid retrieval
    thread_hits = thread_mgr.search(query, k=10)
    atom_hits = atom_mgr.search(query, k=100)  # Over-fetch for hybrid retrieval

    # 3. Build atom-to-thread map
    all_threads = thread_mgr.list_all()
    atom_to_thread: Dict[str, Any] = {}
    for t in all_threads:
        for mid in t.memory_ids:
            atom_to_thread[mid] = t

    # 4. Identify candidate threads
    candidate_threads: Dict[str, Dict[str, Any]] = {}

    # Direct thread hits
    for thread, score in thread_hits:
        tid = thread.id
        if tid not in candidate_threads:
            candidate_threads[tid] = {
                "thread": thread,
                "semantic_score": score,
                "max_atom_score": 0.0
            }
        else:
            candidate_threads[tid]["semantic_score"] = max(
                candidate_threads[tid]["semantic_score"], score
            )

    # Implied ownership hits (threads containing high-scoring atoms)
    for atom, score in atom_hits:
        parent_thread = atom_to_thread.get(atom.id)
        if parent_thread:
            tid = parent_thread.id
            if tid not in candidate_threads:
                candidate_threads[tid] = {
                    "thread": parent_thread,
                    "semantic_score": 0.0,
                    "max_atom_score": score
                }
            else:
                candidate_threads[tid]["max_atom_score"] = max(
                    candidate_threads[tid]["max_atom_score"], score
                )

    # 5. Score and sort candidate threads
    scored_threads = []
    for tid, info in candidate_threads.items():
        thread = info["thread"]
        thread_dict = {
            "id": thread.id,
            "name": thread.name,
            "description": thread.description,
            "memory_ids": thread.memory_ids,
            "last_updated": thread.last_updated
        }
        composite_score = _score_thread(
            thread_dict,
            info["semantic_score"],
            info["max_atom_score"]
        )
        scored_threads.append((thread_dict, composite_score))

    scored_threads.sort(key=lambda x: -x[1])

    # 6. Fill budget with whole threads
    selected_memory_ids = set()
    guaranteed_ids = {m["id"] for m in context.guaranteed_memories}

    for thread_dict, score in scored_threads:
        # Get thread's memories
        thread_memories = []
        thread_tokens = 10  # Header overhead

        for mid in thread_dict["memory_ids"]:
            if mid in guaranteed_ids:
                continue  # Skip already-included guaranteed memories

            atom = atom_mgr.get(mid)
            if atom:
                mem_dict = {
                    "id": atom.id,
                    "content": atom.content,
                    "importance": atom.importance,
                    "created_at": atom.created_at
                }
                thread_memories.append(mem_dict)
                thread_tokens += count_tokens(atom.content) + 5

        # All-or-nothing: include thread only if it fits
        if used_tokens + thread_tokens <= token_budget and thread_memories:
            thread_dict["memories"] = thread_memories
            context.threads.append(thread_dict)
            used_tokens += thread_tokens
            selected_memory_ids.update(m["id"] for m in thread_memories)
            logger.debug(f"Selected thread: {thread_dict['name']} ({thread_tokens} tokens)")

    # 7. HYBRID RETRIEVAL: Fill remaining budget with bonus atoms from non-selected threads
    # These are individually highly-relevant atoms that weren't included because their
    # parent thread didn't make the cut. This catches important facts that would
    # otherwise be missed due to thread-level filtering.
    #
    # Cap orphan atoms at 25% of total budget to prevent thread context from being
    # overwhelmed by random individual atoms. This ensures threads remain the primary
    # context source while still catching important standalone facts.
    ORPHAN_BUDGET_CAP = 0.25
    max_orphan_tokens = int(token_budget * ORPHAN_BUDGET_CAP)

    selected_thread_ids = {t["id"] for t in context.threads}
    bonus_atom_count = 0
    bonus_atom_tokens = 0

    for atom, score in atom_hits:
        if atom.id in selected_memory_ids or atom.id in guaranteed_ids:
            continue

        # Check if this atom's parent thread was already selected
        # If so, skip it (it should have been included with the thread)
        parent_thread = atom_to_thread.get(atom.id)
        if parent_thread and parent_thread.id in selected_thread_ids:
            # This shouldn't happen normally, but guard against it
            continue

        mem_tokens = count_tokens(atom.content) + 5

        # Respect both overall budget AND orphan cap
        if bonus_atom_tokens + mem_tokens > max_orphan_tokens:
            logger.debug(f"Orphan atom cap reached ({bonus_atom_tokens}/{max_orphan_tokens} tokens)")
            break

        if used_tokens + mem_tokens <= token_budget:
            context.atomic_memories.append({
                "id": atom.id,
                "content": atom.content,
                "importance": atom.importance,
                "created_at": atom.created_at,
                "semantic_score": score,  # Include score for debugging/transparency
                "source_thread": parent_thread.name if parent_thread else None
            })
            used_tokens += mem_tokens
            selected_memory_ids.add(atom.id)
            bonus_atom_count += 1
            bonus_atom_tokens += mem_tokens

    context.total_tokens = used_tokens
    context.token_breakdown = {
        "threads": sum(
            sum(count_tokens(m["content"]) for m in t.get("memories", []))
            for t in context.threads
        ),
        "bonus_atoms": bonus_atom_tokens,
        "guaranteed": sum(count_tokens(m["content"]) for m in context.guaranteed_memories)
    }

    logger.info(
        f"Retrieved memory context: {len(context.threads)} threads, "
        f"{bonus_atom_count} bonus atoms ({bonus_atom_tokens}/{max_orphan_tokens} orphan cap), "
        f"{len(context.guaranteed_memories)} guaranteed, "
        f"{used_tokens}/{token_budget} tokens used"
    )
    if bonus_atom_count > 0:
        logger.debug(
            f"Hybrid retrieval added {bonus_atom_count} bonus atoms from non-selected threads "
            f"(orphan budget: {bonus_atom_tokens}/{max_orphan_tokens} tokens, "
            f"{100*bonus_atom_tokens/max_orphan_tokens:.1f}% of cap)"
        )

    return context


def search_memories(
    query: str,
    k: int = 10,
    include_threads: bool = True
) -> List[Dict[str, Any]]:
    """
    Simple search across all memories.

    Returns a flat list of memories with scores.
    """
    try:
        from .atomic_memory import get_atomic_manager
        from .thread_memory import get_thread_manager
    except ImportError:
        from atomic_memory import get_atomic_manager
        from thread_memory import get_thread_manager

    results = []

    # Search atoms
    atom_mgr = get_atomic_manager()
    for atom, score in atom_mgr.search(query, k=k):
        results.append({
            "type": "atomic",
            "id": atom.id,
            "content": atom.content,
            "importance": atom.importance,
            "score": score
        })

    # Search threads
    if include_threads:
        thread_mgr = get_thread_manager()
        for thread, score in thread_mgr.search(query, k=k):
            results.append({
                "type": "thread",
                "id": thread.id,
                "name": thread.name,
                "description": thread.description,
                "memory_count": len(thread.memory_ids),
                "score": score
            })

    # Sort by score
    results.sort(key=lambda x: -x["score"])
    return results[:k]
