"""
Active Memory Search Tool

Provides fuzzy text-based search for Claude to actively explore his semantic memory.
Unlike the embedding-based ltm_search, this supports:
- Case-insensitive matching
- Partial word matching
- Searching across atom content AND thread titles
- More exploratory, keyword-driven queries

This is for deliberate, active memory exploration vs. passive context injection.
"""

import re
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.memory.search")


def _format_timestamp(timestamp_str: str) -> str:
    """Format timestamp with human-friendly context."""
    if not timestamp_str:
        return ""
    try:
        dt = datetime.fromisoformat(timestamp_str)
        now = datetime.now()
        day_delta = (now.date() - dt.date()).days
        date_str = dt.strftime("%Y-%m-%d %H:%M")

        if day_delta == 0:
            return f"today | {date_str}"
        elif day_delta == 1:
            return f"yesterday | {date_str}"
        elif 2 <= day_delta <= 3:
            return f"a couple days ago | {date_str}"
        elif 4 <= day_delta <= 7:
            return f"this week | {date_str}"
        else:
            return date_str
    except Exception:
        return timestamp_str[:16] if len(timestamp_str) >= 16 else timestamp_str


def _fuzzy_match(text: str, query: str) -> tuple[bool, float]:
    """
    Check if text contains the query with fuzzy matching.

    Returns (matches, score) where score indicates quality of match.
    Supports:
    - Case-insensitive matching
    - Partial word matching
    - Multiple search terms (all must match)
    """
    text_lower = text.lower()

    # Split query into terms (handles "chess game" as two terms)
    terms = query.lower().split()

    if not terms:
        return False, 0.0

    matches = 0
    total_score = 0.0

    for term in terms:
        if term in text_lower:
            matches += 1
            # Score based on whether it's a whole word match
            # Whole word match gets higher score
            pattern = rf'\b{re.escape(term)}\b'
            if re.search(pattern, text_lower):
                total_score += 1.0  # Exact word boundary match
            else:
                total_score += 0.5  # Partial match (substring)

    # All terms must match
    if matches == len(terms):
        return True, total_score / len(terms)

    return False, 0.0


def search_atoms_fuzzy(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search atomic memories using fuzzy text matching."""
    import os
    import sys

    # Add scripts directory to path
    SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
    LTM_DIR = os.path.join(SCRIPTS_DIR, "ltm")
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if LTM_DIR not in sys.path:
        sys.path.insert(0, LTM_DIR)

    from ltm.atomic_memory import get_atomic_manager

    atom_mgr = get_atomic_manager()
    results = []

    for atom in atom_mgr.list_all():
        matches, score = _fuzzy_match(atom.content, query)
        if matches:
            # Also check tags for bonus score
            tag_bonus = 0.0
            for tag in atom.tags:
                tag_match, tag_score = _fuzzy_match(tag, query)
                if tag_match:
                    tag_bonus = max(tag_bonus, tag_score * 0.3)

            results.append({
                "type": "atom",
                "id": atom.id,
                "content": atom.content,
                "created_at": atom.created_at,
                "tags": atom.tags,
                "score": score + tag_bonus
            })

    # Sort by score (descending), then by recency
    results.sort(key=lambda x: (-x["score"], x["created_at"]), reverse=False)
    results.sort(key=lambda x: -x["score"])

    return results[:limit]


def search_threads_fuzzy(query: str, limit: int = 20) -> List[Dict[str, Any]]:
    """Search threads using fuzzy text matching on name and description."""
    import os
    import sys

    SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
    LTM_DIR = os.path.join(SCRIPTS_DIR, "ltm")
    if SCRIPTS_DIR not in sys.path:
        sys.path.insert(0, SCRIPTS_DIR)
    if LTM_DIR not in sys.path:
        sys.path.insert(0, LTM_DIR)

    from ltm.thread_memory import get_thread_manager

    thread_mgr = get_thread_manager()
    results = []

    for thread in thread_mgr.list_all():
        # Check name and description
        name_match, name_score = _fuzzy_match(thread.name, query)
        desc_match, desc_score = _fuzzy_match(thread.description, query)

        if name_match or desc_match:
            # Name matches are weighted higher than description
            combined_score = 0.0
            if name_match:
                combined_score += name_score * 1.5
            if desc_match:
                combined_score += desc_score

            results.append({
                "type": "thread",
                "id": thread.id,
                "name": thread.name,
                "description": thread.description,
                "atom_count": len(thread.memory_ids),
                "memory_ids": thread.memory_ids,
                "last_updated": thread.last_updated,
                "score": combined_score
            })

    results.sort(key=lambda x: -x["score"])
    return results[:limit]


@register_tool("memory")
@tool(
    name="memory_search",
    description="""Search Claude's semantic long-term memory using text/keyword matching.

Use this to ACTIVELY explore your memory - deliberately look up past conversations,
facts, topics, or anything you want to remember. This is different from the passive
memory injection that happens automatically.

Search is fuzzy and flexible:
- Case-insensitive
- Partial word matching (e.g., "chess" finds "chessboard")
- Multiple keywords (all must match)
- Searches both atom content AND thread titles/descriptions

Examples:
- memory_search("chess") - find memories about chess games
- memory_search("Gemini conversation") - find memories about talking with Gemini
- memory_search("user preference") - find stored user preferences
- memory_search("the user") - find memories mentioning the user

Returns atoms (individual facts) and threads (organized collections) with:
- Content/title
- Timestamps
- Thread associations
- Relevance scores""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query - can be keywords, topics, or natural language"
            },
            "limit": {
                "type": "integer",
                "description": "Max results to return (default: 20)",
                "default": 20
            },
            "search_type": {
                "type": "string",
                "enum": ["atoms", "threads", "all"],
                "description": "What to search: 'atoms' (facts), 'threads' (collections), or 'all' (default)",
                "default": "all"
            }
        },
        "required": ["query"]
    }
)
async def memory_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Search Claude's semantic long-term memory using fuzzy text matching.

    This enables active, deliberate memory exploration rather than passive injection.
    """
    try:
        query = args.get("query", "").strip()
        limit = args.get("limit", 20)
        search_type = args.get("search_type", "all")

        if not query:
            return {
                "content": [{"type": "text", "text": "Query is required"}],
                "is_error": True
            }

        atom_results = []
        thread_results = []

        # Search atoms
        if search_type in ("atoms", "all"):
            atom_results = search_atoms_fuzzy(query, limit)

        # Search threads
        if search_type in ("threads", "all"):
            thread_results = search_threads_fuzzy(query, limit)

        # Format output
        output_lines = [f"## Memory Search: \"{query}\"\n"]

        if not atom_results and not thread_results:
            output_lines.append(f"No memories found matching \"{query}\".")
            output_lines.append("\nTry:")
            output_lines.append("- Different keywords or variations")
            output_lines.append("- Fewer search terms")
            output_lines.append("- Using ltm_search for semantic/meaning-based search")
            return {"content": [{"type": "text", "text": "\n".join(output_lines)}]}

        # Thread results first (provide context groupings)
        if thread_results:
            output_lines.append(f"### Threads ({len(thread_results)} found)\n")
            for t in thread_results[:10]:  # Top 10 threads
                updated = _format_timestamp(t.get("last_updated", ""))
                output_lines.append(f"**{t['name']}** ({t['atom_count']} memories)")
                output_lines.append(f"  *{t['description']}*")
                output_lines.append(f"  Last updated: {updated}")
                output_lines.append(f"  ID: `{t['id']}`")
                output_lines.append("")

        # Atom results
        if atom_results:
            output_lines.append(f"### Individual Memories ({len(atom_results)} found)\n")
            for atom in atom_results[:15]:  # Top 15 atoms
                created = _format_timestamp(atom.get("created_at", ""))

                # Truncate long content
                content = atom["content"]
                if len(content) > 300:
                    content = content[:300] + "..."

                output_lines.append(f"**[{created}]**")
                output_lines.append(f"  {content}")

                if atom.get("tags"):
                    output_lines.append(f"  Tags: {', '.join(atom['tags'])}")

                output_lines.append(f"  ID: `{atom['id']}`")
                output_lines.append("")

        # Summary
        output_lines.append("---")
        output_lines.append(f"*Found {len(atom_results)} atoms and {len(thread_results)} threads*")
        output_lines.append("*Use ltm_search for semantic/embedding-based search*")

        return {"content": [{"type": "text", "text": "\n".join(output_lines)}]}

    except Exception as e:
        import traceback
        logger.error(f"memory_search error: {e}\n{traceback.format_exc()}")
        return {
            "content": [{"type": "text", "text": f"Error searching memory: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
