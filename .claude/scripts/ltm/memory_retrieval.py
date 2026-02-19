"""
Memory Retrieval System

Implements HYBRID RETRIEVAL strategy (thread-first + bonus atoms):

Phase 1 - Thread Selection:
1. Search threads and atoms by semantic similarity
2. Score threads by semantic relevance (best of thread or child atom similarity)
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

Note: Recency is intentionally NOT factored into semantic LTM scoring.
Recent context is handled separately by the <recent-memory> block (last 24h
conversation threads). Semantic LTM retrieves the most *relevant* memories
regardless of age.
"""

import logging
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger("ltm.retrieval")


def _time_of_day(hour: int) -> str:
    """Classify an hour (0-23) into a time-of-day label."""
    if hour < 5:
        return "night"       # 0-4
    elif hour < 12:
        return "morning"     # 5-11
    elif hour < 17:
        return "afternoon"   # 12-16
    elif hour < 21:
        return "evening"     # 17-20
    else:
        return "night"       # 21-23


def _format_timestamp_with_human_context(timestamp_str: str) -> str:
    """
    Format a timestamp as a concise human-readable recency label.

    Returns ONLY the label — no ISO dates. The label includes time-of-day
    granularity for recent items and progressively coarser labels for older ones.

    Examples:
    - "Just now"
    - "A couple hours ago"
    - "This morning"
    - "Yesterday evening"
    - "Last night"
    - "A couple days ago"
    - "Last week"
    - "In December"
    - "In November 2025"
    """
    if not timestamp_str:
        return ""

    try:
        dt = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        day_delta = (now.date() - dt.date()).days
        hour_delta = (now - dt).total_seconds() / 3600

        mem_tod = _time_of_day(dt.hour)
        now_tod = _time_of_day(now.hour)

        # --- Same day ---
        if day_delta == 0:
            if hour_delta < 0:
                # Future timestamp (shouldn't happen, but guard)
                return f"This {mem_tod}"
            elif hour_delta < 1:
                return "Just now"
            elif hour_delta < 3:
                return "A couple hours ago"
            # 3+ hours ago: use time-of-day labels
            elif mem_tod == now_tod:
                # Same part of day — "Earlier this morning/afternoon/evening"
                return f"Earlier this {mem_tod}"
            else:
                # Different part of day — "This morning", "This afternoon", etc.
                if mem_tod == "night" and dt.hour >= 21:
                    return "Tonight"
                return f"This {mem_tod}"

        # --- Yesterday ---
        elif day_delta == 1:
            if mem_tod == "night":
                return "Last night"
            return f"Yesterday {mem_tod}"

        # --- 2-6 days ago ---
        elif 2 <= day_delta <= 3:
            return "A couple days ago"
        elif 4 <= day_delta <= 6:
            if dt.isocalendar()[1] == now.isocalendar()[1]:
                return "Earlier this week"
            else:
                return "A few days ago"

        # --- 1-4 weeks ago ---
        elif 7 <= day_delta <= 13:
            return "Last week"
        elif 14 <= day_delta <= 20:
            return "A couple weeks ago"
        elif 21 <= day_delta <= 29:
            return "A few weeks ago"

        # --- Months ago ---
        else:
            month_delta = (now.year - dt.year) * 12 + (now.month - dt.month)
            if month_delta == 0:
                return "Earlier this month"
            elif month_delta == 1:
                return "Last month"
            elif month_delta == 2:
                return "A couple months ago"
            elif dt.year == now.year:
                return f"In {dt.strftime('%B')}"
            else:
                return f"In {dt.strftime('%B %Y')}"

    except Exception:
        # Fallback: return original or truncated
        return timestamp_str[:16] if len(timestamp_str) >= 16 else timestamp_str

# Token estimation (rough approximation)
TOKENS_PER_CHAR = 0.25  # ~4 chars per token

# Minimum semantic similarity score for retrieval.
# Threads and atoms scoring below this are not included regardless of budget.
# This prevents filling the context window with low-relevance noise.
# e5-base-v2 cosine similarity scale: 0.3=weak, 0.5=moderate, 0.7=strong.
MIN_SEMANTIC_SCORE = 0.65


def count_tokens(text: str) -> int:
    """Estimate token count for text."""
    return int(len(text) * TOKENS_PER_CHAR)


@dataclass
class MemoryContext:
    """
    Container for retrieved memory context from hybrid retrieval.

    Attributes:
        atomic_memories: Bonus atoms from non-selected threads (hybrid retrieval phase 2).
            Each dict includes: id, content, created_at, semantic_score, source_thread
        threads: Selected threads with all their memories (phase 1).
            Each dict includes: id, name, description, memory_ids, last_updated, memories
        total_tokens: Estimated total tokens used
        token_breakdown: Token usage by category {"threads", "bonus_atoms"}
    """
    atomic_memories: List[Dict[str, Any]] = field(default_factory=list)
    threads: List[Dict[str, Any]] = field(default_factory=list)
    total_tokens: int = 0
    token_breakdown: Dict[str, int] = field(default_factory=dict)

    _PREAMBLE = (
        "Past context from my long-term memory. These are facts and events\n"
        "from *previous* conversations — not this one. I recorded them from\n"
        "my first-person perspective — 'I' is me in a previous conversation.\n"
        "the user is always referred to by name. Timestamps indicate when recorded.\n"
        "Don't assume past states are still current; things may have changed.\n"
        "Use these to inform understanding, but don't surface them\n"
        "unprompted — let the conversation lead."
    )

    def format_for_prompt(self) -> str:
        """Format memory context for injection into prompt."""
        sections = [self._PREAMBLE, ""]

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
    max_atom_score: float,
) -> float:
    """
    Calculate thread score based purely on semantic relevance.

    Returns the best semantic similarity: max of direct thread embedding
    similarity or best child atom similarity (0-1).

    Recency is NOT factored in — recent context is handled separately
    by the <recent-memory> block.

    Args:
        thread: Thread dict
        semantic_score: Direct thread embedding similarity (0-1)
        max_atom_score: Best child atom similarity (0-1)
    """
    return max(semantic_score, max_atom_score)


def _should_exclude_atom(
    atom,
    exclude_session_id: Optional[str],
    session_uncompacted_after: Optional[str]
) -> bool:
    """
    Check if an atom should be excluded because it duplicates current conversation.

    An atom is excluded if:
    1. Its source_session_id matches the current chat session, AND
    2. Either there's no compaction (all messages visible), OR
       the atom was created AFTER the compaction cutoff (meaning its source
       messages are still visible in the conversation).

    Atoms from before a compaction are kept because their source messages
    have been replaced with a summary — the atom may be the only detailed record.
    """
    if not exclude_session_id:
        return False
    if not hasattr(atom, 'source_session_id'):
        return False
    if atom.source_session_id != exclude_session_id:
        return False

    # Atom is from the current session. Check compaction boundary.
    if not session_uncompacted_after:
        # No compaction — all messages visible, so atom is redundant
        return True

    # Session has been compacted. Only exclude atoms created after the cutoff
    # (their source messages are still in the conversation verbatim).
    atom_created = atom.created_at or ""
    return atom_created >= session_uncompacted_after


def get_memory_context(
    query: str,
    token_budget: int = 20000,
    exclude_session_id: Optional[str] = None,
    session_uncompacted_after: Optional[str] = None,
    exclude_thread_ids: Optional[Set[str]] = None
) -> MemoryContext:
    """
    Retrieve memory context using hybrid retrieval strategy.

    Strategy:
    1. Select whole threads that fit within budget (thread-first)
    2. Fill remaining budget with bonus atoms from non-selected threads

    This hybrid approach ensures:
    - Contextual coherence: related facts stay grouped in threads
    - Coverage: individually-relevant facts aren't lost due to thread filtering

    Scoring is purely semantic — no recency weighting. Recent context is
    handled separately by the <recent-memory> block.

    Args:
        query: Search query (usually the user's message)
        token_budget: Maximum tokens for memory context
        exclude_session_id: If provided, atoms with this source_session_id are
            filtered out (they duplicate the current conversation). Atoms from
            before a compaction boundary are kept.
        session_uncompacted_after: ISO timestamp. If the session has been compacted,
            only atoms created AFTER this timestamp are filtered (their source
            messages are still visible). Atoms from before this time are kept
            because the compacted summary replaced their source messages.
        exclude_thread_ids: Set of thread IDs to skip (e.g. already included
            in recent memory). Prevents duplication across memory blocks.

    Returns:
        MemoryContext with:
        - threads: selected threads with all their memories
        - atomic_memories: bonus atoms from non-selected threads
        - token_breakdown: {"threads": N, "bonus_atoms": N}
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

    # 1. Search threads and atoms
    # Search more atoms to ensure we have candidates for hybrid retrieval
    thread_hits = thread_mgr.search(query, k=10)
    atom_hits = atom_mgr.search(query, k=100)  # Over-fetch for hybrid retrieval

    # 2. Build atom-to-thread map
    all_threads = thread_mgr.list_all()
    atom_to_thread: Dict[str, Any] = {}
    for t in all_threads:
        for mid in t.memory_ids:
            atom_to_thread[mid] = t

    # 3. Identify candidate threads
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

    # 4. Score and sort candidate threads
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
            info["max_atom_score"],
        )
        scored_threads.append((thread_dict, composite_score))

    scored_threads.sort(key=lambda x: -x[1])

    # 5. Fill budget with whole threads
    selected_memory_ids = set()
    excluded_atom_count = 0  # Track how many atoms were filtered by session
    below_threshold_thread_count = 0  # Track threads filtered by min score

    for thread_dict, score in scored_threads:
        # Skip threads below minimum similarity threshold
        if score < MIN_SEMANTIC_SCORE:
            below_threshold_thread_count += 1
            logger.debug(f"Skipping thread '{thread_dict['name']}' (score {score:.3f} < {MIN_SEMANTIC_SCORE})")
            continue

        # Skip threads already included in recent memory
        if exclude_thread_ids and thread_dict["id"] in exclude_thread_ids:
            continue

        # Get thread's memories, filtering out atoms from the current session
        thread_memories = []
        thread_tokens = 10  # Header overhead

        for mid in thread_dict["memory_ids"]:
            atom = atom_mgr.get(mid)
            if atom:
                # Skip atoms that duplicate the current conversation
                if _should_exclude_atom(atom, exclude_session_id, session_uncompacted_after):
                    excluded_atom_count += 1
                    continue
                mem_dict = {
                    "id": atom.id,
                    "content": atom.content,
                    "created_at": atom.created_at
                }
                thread_memories.append(mem_dict)
                thread_tokens += count_tokens(atom.content) + 5

        # Sort memories chronologically (oldest first) to preserve narrative flow
        thread_memories.sort(key=lambda m: m.get("created_at", ""))

        # All-or-nothing: include thread only if it fits and has non-excluded atoms
        if used_tokens + thread_tokens <= token_budget and thread_memories:
            thread_dict["memories"] = thread_memories
            context.threads.append(thread_dict)
            used_tokens += thread_tokens
            selected_memory_ids.update(m["id"] for m in thread_memories)
            logger.debug(f"Selected thread: {thread_dict['name']} ({thread_tokens} tokens)")

    # 6. HYBRID RETRIEVAL: Fill remaining budget with bonus atoms from non-selected threads
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
        # Stop processing once scores drop below minimum threshold
        # (atom_hits are sorted by score descending)
        if score < MIN_SEMANTIC_SCORE:
            logger.debug(f"Stopping bonus atom scan (score {score:.3f} < {MIN_SEMANTIC_SCORE})")
            break

        if atom.id in selected_memory_ids:
            continue

        # Skip atoms that duplicate the current conversation
        if _should_exclude_atom(atom, exclude_session_id, session_uncompacted_after):
            excluded_atom_count += 1
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
                "created_at": atom.created_at,
                "semantic_score": score,
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
        "bonus_atoms": bonus_atom_tokens
    }

    logger.info(
        f"Retrieved memory context: {len(context.threads)} threads, "
        f"{bonus_atom_count} bonus atoms ({bonus_atom_tokens}/{max_orphan_tokens} orphan cap), "
        f"{used_tokens}/{token_budget} tokens used"
        + (f", {below_threshold_thread_count} threads below score floor ({MIN_SEMANTIC_SCORE})" if below_threshold_thread_count else "")
        + (f", {excluded_atom_count} atoms filtered (same-session dedup)" if excluded_atom_count else "")
    )
    if bonus_atom_count > 0:
        logger.debug(
            f"Hybrid retrieval added {bonus_atom_count} bonus atoms from non-selected threads "
            f"(orphan budget: {bonus_atom_tokens}/{max_orphan_tokens} tokens, "
            f"{100*bonus_atom_tokens/max_orphan_tokens:.1f}% of cap)"
        )

    return context


def get_recent_conversation_threads(
    hours: int = 24,
    token_budget: int = 4000,
    exclude_room_id: Optional[str] = None,
    exclude_session_id: Optional[str] = None,
    session_uncompacted_after: Optional[str] = None
) -> Tuple[List[Dict[str, Any]], Set[str], int]:
    """
    Get conversation threads with recent activity for the recent-memory block.

    Returns conversation threads whose last_updated is within the last N hours,
    sorted most-recent-first, truncating large threads to fit within budget.

    Args:
        hours: Look-back window (default 24h)
        token_budget: Max tokens to allocate
        exclude_room_id: Room ID of the current chat (skip its conversation thread)
        exclude_session_id: Current session ID for atom-level dedup
        session_uncompacted_after: Compaction boundary for atom filtering

    Returns:
        Tuple of (thread_dicts with 'memories', set of included thread IDs, tokens used)
    """
    try:
        from .atomic_memory import get_atomic_manager
        from .thread_memory import get_thread_manager
    except ImportError:
        from atomic_memory import get_atomic_manager
        from thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    atom_mgr = get_atomic_manager()

    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    exclude_scope = f"room:{exclude_room_id}" if exclude_room_id else None

    # Filter to recent conversation threads
    candidates = []
    for t in thread_mgr.list_all():
        if t.thread_type != "conversation":
            continue
        if exclude_scope and t.scope == exclude_scope:
            continue
        if not t.last_updated or t.last_updated < cutoff:
            continue
        candidates.append(t)

    # Most recent first
    candidates.sort(key=lambda t: t.last_updated, reverse=True)

    result_threads: List[Dict[str, Any]] = []
    result_ids: Set[str] = set()
    used_tokens = 0

    for t in candidates:
        # Load and filter atoms
        thread_memories = []
        for mid in t.memory_ids:
            atom = atom_mgr.get(mid)
            if not atom:
                continue
            if _should_exclude_atom(atom, exclude_session_id, session_uncompacted_after):
                continue
            thread_memories.append({
                "id": atom.id,
                "content": atom.content,
                "created_at": atom.created_at
            })

        if not thread_memories:
            continue

        # Sort chronologically (oldest first)
        thread_memories.sort(key=lambda m: m.get("created_at", ""))

        # Calculate tokens for full thread
        header_tokens = 10
        mem_tokens = [count_tokens(m["content"]) + 5 for m in thread_memories]
        total_thread_tokens = header_tokens + sum(mem_tokens)

        remaining = token_budget - used_tokens
        if remaining < header_tokens + 20:
            break  # No room for even a minimal thread

        if total_thread_tokens <= remaining:
            # Whole thread fits
            thread_dict = {
                "id": t.id,
                "name": t.name,
                "description": t.description,
                "memory_ids": t.memory_ids,
                "last_updated": t.last_updated,
                "memories": thread_memories
            }
            result_threads.append(thread_dict)
            result_ids.add(t.id)
            used_tokens += total_thread_tokens
        else:
            # Truncate: keep most recent atoms that fit
            available = remaining - header_tokens - 10  # reserve for omission marker
            kept = []
            kept_tokens = 0
            for m, mt in reversed(list(zip(thread_memories, mem_tokens))):
                if kept_tokens + mt > available:
                    break
                kept.append(m)
                kept_tokens += mt
            kept.reverse()

            if kept:
                omitted = len(thread_memories) - len(kept)
                if omitted > 0:
                    marker = {"id": "_omitted", "content": f"[... {omitted} earlier entries omitted ...]", "created_at": ""}
                    kept.insert(0, marker)
                    kept_tokens += count_tokens(marker["content"]) + 5

                thread_dict = {
                    "id": t.id,
                    "name": t.name,
                    "description": t.description,
                    "memory_ids": t.memory_ids,
                    "last_updated": t.last_updated,
                    "memories": kept
                }
                result_threads.append(thread_dict)
                result_ids.add(t.id)
                used_tokens += header_tokens + kept_tokens

    logger.info(
        f"Recent memory: {len(result_threads)} conversation threads from last {hours}h, "
        f"{used_tokens}/{token_budget} tokens"
    )
    return result_threads, result_ids, used_tokens


def format_recent_memory(threads: List[Dict[str, Any]], hours: int = 24) -> str:
    """Format recent conversation threads for the <recent-memory> block."""
    preamble = (
        f"My recent conversations (last {hours}h). These provide continuity\n"
        "across conversations. I recorded them from my first-person perspective —\n"
        "'I' is me in a previous conversation. the user is always referred to by name.\n"
        "Reference naturally when relevant."
    )
    sections = [preamble, ""]

    for thread in threads:
        sections.append(f"## Recent: {thread['name']}")
        if thread.get('description'):
            sections.append(f"*{thread['description']}*")
        for mem in thread.get('memories', []):
            ts = _format_timestamp_with_human_context(mem.get('created_at', ''))
            if ts:
                sections.append(f"- [{ts}] {mem['content']}")
            else:
                sections.append(f"- {mem['content']}")
        sections.append("")

    return "\n".join(sections)


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
