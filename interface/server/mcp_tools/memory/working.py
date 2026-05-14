"""
Working Memory tools.

Ephemeral notes that persist across exchanges but auto-expire based on TTL.
Each agent gets its own private working memory store, isolated by agent name.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

# Base .claude directory (for resolving per-agent memory.md paths)
_CLAUDE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude"))


def _get_agent_store(args: Dict[str, Any]):
    """Extract agent name from args and return the appropriate store."""
    sys.path.insert(0, SCRIPTS_DIR)
    from working_memory import get_store
    agent_name = args.pop("_agent_name", None) or "character"
    return get_store(agent_name=agent_name), agent_name


def _resolve_time_expiration(expires_in: Any, expires_at: Any):
    """Resolve `expires_in` / `expires_at` to a single absolute datetime.

    Returns a tuple (expires_at_dt, error_message). Exactly one of the two
    is non-None. If neither was passed, returns (None, None). If both were
    passed, returns (None, error). If parsing fails, returns (None, error).
    Naive ISO timestamps are interpreted as America/Chicago local time.

    `expires_in` accepts:
    - Duration shorthand: '30m', '2h', '1d', '1w' (minutes/hours/days/weeks).
    - Aliases: 'eod' or 'today' → today 23:59 America/Chicago. If that
      moment has already passed (e.g. caller invoked at 23:59:30), the
      resulting timestamp will fall through to the past-timestamp guard
      and be rejected.
    """
    from datetime import datetime, timezone
    import zoneinfo

    has_in = isinstance(expires_in, str) and expires_in.strip()
    has_at = isinstance(expires_at, str) and expires_at.strip()

    if has_in and has_at:
        return None, "Pass either expires_in or expires_at, not both."

    if has_in:
        token = expires_in.strip().lower()
        if token in ("eod", "today"):
            chicago = zoneinfo.ZoneInfo("America/Chicago")
            now_local = datetime.now(chicago)
            eod = now_local.replace(hour=23, minute=59, second=0, microsecond=0)
            return eod, None

        sys.path.insert(0, SCRIPTS_DIR)
        from working_memory import parse_duration
        seconds = parse_duration(token)
        if seconds <= 0:
            return None, (
                f"Invalid expires_in '{expires_in}'. "
                "Accepted: '30m', '2h', '1d', '1w' (minutes/hours/days/weeks), "
                "or aliases 'eod'/'today' (today 23:59 America/Chicago)."
            )
        from datetime import timedelta
        return datetime.now(timezone.utc) + timedelta(seconds=seconds), None

    if has_at:
        try:
            parsed = datetime.fromisoformat(expires_at.strip())
        except Exception as e:
            return None, f"Invalid expires_at format: {e}"
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zoneinfo.ZoneInfo("America/Chicago"))
        return parsed, None

    return None, None


@register_tool("memory")
@tool(
    name="working_memory_add",
    description="""Add a note to working memory. Working memory items persist across exchanges and across invocations, and:
- Auto-expire by TTL — either exchange-count (`ttl`) or wall-clock (`expires_in` / `expires_at`)
- Support deadlines with countdown display (reminder semantics, not expiration)
- Are injected into every prompt for context

Use this for:
- State tracking across discrete invocations — ponder-points, in-flight threads of thought, "still thinking about X" notes filed during silent or scheduled runs so the next wake picks up from a real artifact instead of starting cold. Continuity as infrastructure.
- Observations you want to track temporarily
- Reminders about ongoing context
- Things to check back on later

Pick the TTL unit that fits the use case. Both kinds can be set on the same item — whichever fires first kills it. Pinned items ignore both.
- `ttl` — exchange count. Default 5, max 10. Right unit when each turn matters.
- `expires_in` — relative duration: '30m', '2h', '1d', '1w' (minutes/hours/days/weeks), or the aliases 'eod'/'today' (today 23:59 America/Chicago). Right unit for "hold this for the rest of the day."
- `expires_at` — absolute ISO timestamp, e.g. '2026-05-12T09:00'. Right unit for "until tomorrow morning when the user pings me." Naive timestamps are interpreted as America/Chicago local time.

Passing both `expires_in` and `expires_at` in the same call is an error.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The note content"},
            "tag": {"type": "string", "description": "Optional category tag (e.g., 'reminder', 'observation', 'todo')"},
            "ttl": {"type": "integer", "description": "Time-to-live in exchanges (default: 5, max: 10)"},
            "expires_in": {"type": "string", "description": "Relative time-to-live: '30m', '2h', '1d', '1w' (minutes/hours/days/weeks), or 'eod'/'today' (today 23:59 America/Chicago)."},
            "expires_at": {"type": "string", "description": "Absolute expiration as ISO timestamp (e.g. '2026-05-12T09:00'). Naive timestamps assumed America/Chicago."},
            "pinned": {"type": "boolean", "description": "If true, item never auto-expires (max 3 pinned items)"},
            "deadline": {"type": "string", "description": "Optional reminder deadline as ISO timestamp (e.g., '2026-01-25T14:00:00'). Reminder only, does not expire the item."},
            "remind_before": {"type": "string", "description": "When to show 'due soon' warning (e.g., '2h', '24h')"}
        },
        "required": ["content"]
    }
)
async def working_memory_add(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a working memory item."""
    try:
        from working_memory import WorkingMemoryError
        from datetime import datetime

        store, agent_name = _get_agent_store(args)

        content = args.get("content", "").strip()
        if not content:
            return {"content": [{"type": "text", "text": "content is required"}], "is_error": True}

        tag = args.get("tag")
        ttl = args.get("ttl")
        pinned = args.get("pinned", False)
        deadline_str = args.get("deadline")
        remind_before = args.get("remind_before")

        # Parse reminder deadline if provided
        deadline_at = None
        if deadline_str:
            try:
                deadline_at = datetime.fromisoformat(deadline_str)
                if deadline_at.tzinfo is None:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo("America/Chicago")
                    deadline_at = deadline_at.replace(tzinfo=tz)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid deadline format: {e}"}], "is_error": True}

        # Parse time-based expiration (expires_in or expires_at)
        expires_at, err = _resolve_time_expiration(args.get("expires_in"), args.get("expires_at"))
        if err:
            return {"content": [{"type": "text", "text": err}], "is_error": True}

        item = store.add_item(
            content=content,
            tag=tag,
            ttl=ttl,
            pinned=pinned,
            deadline_at=deadline_at,
            remind_before=remind_before,
            expires_at=expires_at,
        )

        if item.pinned:
            status = "pinned"
        else:
            bits = [f"TTL={item.ttl_initial}"]
            if item.expires_at:
                bits.append(f"expires_at={item.expires_at.isoformat()}")
            status = ", ".join(bits)
        return {"content": [{"type": "text", "text": f"Added to working memory [{status}]: {content[:80]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_update",
    description="""Update an existing working memory item by its display index (1-based).

Use this to evolve a state-tracking note across invocations (refine a ponder-point, append progress to an in-flight thread of thought), or edit any other working memory item.

You can update content, TTL (exchange-count or time-based), tag, pinned status, or deadline. For time-based TTL: pass `expires_in` ('30m', '2h', '1d', '1w' or 'eod'/'today') or `expires_at` (ISO timestamp). Passing both is an error; whichever fires first wins against exchange-count `ttl`.""",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to update (1-based, as shown in prompt)"},
            "content": {"type": "string", "description": "New content (replaces existing)"},
            "append": {"type": "string", "description": "Text to append to existing content"},
            "tag": {"type": "string", "description": "New tag (empty string to clear)"},
            "ttl": {"type": "integer", "description": "Reset exchange-count TTL to this value"},
            "expires_in": {"type": "string", "description": "Relative time-based TTL: '30m', '2h', '1d', '1w' (minutes/hours/days/weeks), or 'eod'/'today' (today 23:59 America/Chicago)."},
            "expires_at": {"type": "string", "description": "Absolute expiration ISO timestamp. Naive timestamps assumed America/Chicago."},
            "pinned": {"type": "boolean", "description": "Set pinned status"},
            "deadline": {"type": "string", "description": "New reminder deadline as ISO timestamp"},
            "remind_before": {"type": "string", "description": "When to show 'due soon' warning"}
        },
        "required": ["index"]
    }
)
async def working_memory_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update a working memory item."""
    try:
        from working_memory import WorkingMemoryError
        from datetime import datetime

        store, agent_name = _get_agent_store(args)

        index = args.get("index")
        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        # Parse reminder deadline if provided
        deadline_at = None
        deadline_str = args.get("deadline")
        if deadline_str:
            try:
                deadline_at = datetime.fromisoformat(deadline_str)
                if deadline_at.tzinfo is None:
                    import zoneinfo
                    tz = zoneinfo.ZoneInfo("America/Chicago")
                    deadline_at = deadline_at.replace(tzinfo=tz)
            except Exception as e:
                return {"content": [{"type": "text", "text": f"Invalid deadline format: {e}"}], "is_error": True}

        # Parse time-based expiration if provided
        expires_at, err = _resolve_time_expiration(args.get("expires_in"), args.get("expires_at"))
        if err:
            return {"content": [{"type": "text", "text": err}], "is_error": True}

        item = store.update_item(
            index=index,
            new_content=args.get("content"),
            append=args.get("append"),
            ttl=args.get("ttl"),
            tag=args.get("tag"),
            pinned=args.get("pinned"),
            deadline_at=deadline_at,
            remind_before=args.get("remind_before"),
            expires_at=expires_at,
        )

        return {"content": [{"type": "text", "text": f"Updated item {index}: {item.content[:80]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_remove",
    description="Remove a working memory item by its display index (1-based).",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to remove (1-based)"}
        },
        "required": ["index"]
    }
)
async def working_memory_remove(args: Dict[str, Any]) -> Dict[str, Any]:
    """Remove a working memory item."""
    try:
        from working_memory import WorkingMemoryError

        store, agent_name = _get_agent_store(args)

        index = args.get("index")
        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        removed = store.remove_item(index)

        return {"content": [{"type": "text", "text": f"Removed: {removed.content[:80]}..."}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_list",
    description="List all current working memory items with their status, including any state-tracking notes carried across invocations.",
    input_schema={"type": "object", "properties": {}}
)
async def working_memory_list(args: Dict[str, Any]) -> Dict[str, Any]:
    """List working memory items."""
    try:
        store, agent_name = _get_agent_store(args)
        items = store.list_items()

        if not items:
            return {"content": [{"type": "text", "text": "Working memory is empty."}]}

        lines = []
        for i, item in enumerate(items, 1):
            status = "[PINNED]" if item.pinned else f"[TTL {item.ttl_remaining}/{item.ttl_initial}]"
            tag = f"[{item.tag}]" if item.tag else ""
            lines.append(f"{i}. {status} {tag} {item.content[:100]}...")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="working_memory_snapshot",
    description="""Promote a working memory item to permanent storage (memories.json with always_load=true).

This "snapshots" an ephemeral working memory note into your permanent memory store.
Use this when a temporary observation or note becomes important enough to persist.

The item is saved as an always_load memory entry. By default, the
original working memory item is removed after promotion (set keep=true to retain it).""",
    input_schema={
        "type": "object",
        "properties": {
            "index": {"type": "integer", "description": "The item number to promote (1-based)"},
            "section": {
                "type": "string",
                "description": "Section label for organization (e.g., 'Lessons Learned', 'User Preferences')",
                "default": "Promoted from Working Memory"
            },
            "keep": {
                "type": "boolean",
                "description": "If true, keep the item in working memory after promotion (default: false)",
                "default": False
            },
            "note": {
                "type": "string",
                "description": "Optional note to append to the content when saving"
            }
        },
        "required": ["index"]
    }
)
async def working_memory_snapshot(args: Dict[str, Any]) -> Dict[str, Any]:
    """Promote working memory item to memories.json as always_load."""
    try:
        from working_memory import WorkingMemoryError

        store, agent_name = _get_agent_store(args)

        index = args.get("index")
        section = args.get("section", "Promoted from Working Memory")
        keep = args.get("keep", False)
        note = args.get("note", "")

        if not index or index < 1:
            return {"content": [{"type": "text", "text": "Valid index (1+) is required"}], "is_error": True}

        # Get the working memory item
        items = store.list_items()

        if not items:
            return {"content": [{"type": "text", "text": "Working memory is empty."}], "is_error": True}

        if index > len(items):
            return {"content": [{"type": "text", "text": f"No item at index {index}. Valid: 1-{len(items)}"}], "is_error": True}

        item = items[index - 1]
        content = item.content

        # Add note if provided
        if note:
            content = f"{content} — {note}"

        # Add tag context if present
        if item.tag:
            content = f"[{item.tag}] {content}"

        # Save to memories.json using unified memory helpers (atomic writes + locking)
        from .unified import _load_memories, _save_memories, _next_id, _now_iso, _reindex_agent

        agent_name = agent_name or "character"
        memories_path = Path(_CLAUDE_DIR) / "agents" / agent_name / "memories.json"

        memories = _load_memories(memories_path)

        now = _now_iso()
        next_id = _next_id(memories)
        new_memory = {
            "id": next_id,
            "triggers": [section, content[:60]],
            "content": content,
            "always_load": True,
            "private": False,
            "created": now,
            "updated": now,
            "type": "observation",
        }

        memories.append(new_memory)

        # Atomic save (temp file + rename + locking) and reindex for search
        memories_path.parent.mkdir(parents=True, exist_ok=True)
        _save_memories(memories_path, memories)
        _reindex_agent(agent_name)

        result = f"Promoted to permanent memory #{next_id} [always_load]: {content[:80]}..."

        # Remove from working memory unless keep=true
        if not keep:
            store.remove_item(index)
            result += "\nRemoved from working memory."
        else:
            result += "\nKept in working memory."

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
