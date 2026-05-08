"""
Salon MCP tools — implementations.

These tools are agent-context-sensitive: the calling agent's name is injected
as `_agent_name` so we can tag the creator/poster correctly. Register their
names in `_inject_agent_context` (mcp_tools/__init__.py) so the wrapper
knows to pass the agent identity through.

Note on the convener loop: these tools modify salon state (create / add /
post). They do NOT directly fire the convener — that's the server's job
after the change settles. We just record what happened, and the server's
salon-event handler picks it up. (For v1 backend-only build, the convener
fire is wired separately in main.py.)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.salons")


def _get_salon_manager():
    """Lazy import — keeps the tool module loadable even if salon_manager has issues."""
    from salon_manager import get_manager
    return get_manager()


# ------------------------------------------------------------------
# create_salon
# ------------------------------------------------------------------

CREATE_SALON_DESCRIPTION = """Create a new salon — a group chat between the user and multiple agents (or just agents among themselves).

A salon has a Convener that handles routing — when someone posts, the Convener decides who speaks next, including a possible chain of agents in sequence.

Use this when:
- You want to bring multiple agents (and optionally the user) into the same conversation
- A 1:1 thread isn't enough — the topic genuinely needs N people
- You're staging a design discussion, brainstorm, or multi-perspective review

Participants are agent names (or "user"). The salon's title is shown in the sidebar; pick something descriptive. The opening_message (optional) becomes the first post — you, the creator, are recorded as its author.

If `participants` includes "user", the user gets pinged: the salon shows up in his sidebar and he can post into it. If it's all agents, the user can still see it but won't be auto-notified.

Returns the salon_id. Anyone with the ID can post via `post_to_salon` (if they're a participant).
"""

CREATE_SALON_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {
            "type": "string",
            "description": "Title for the salon, shown in sidebar (e.g. 'Agent Presence Design').",
        },
        "participants": {
            "type": "array",
            "items": {"type": "string"},
            "description": "List of participants. Agent names (e.g. 'ash', 'patch') or 'user'. The calling agent is auto-included; you don't need to list yourself.",
        },
        "opening_message": {
            "type": "string",
            "description": "Optional first message to post — sets the topic / kicks the conversation off. Recorded as from the calling agent.",
        },
    },
    "required": ["title", "participants"],
}


@register_tool("salons")
@tool("create_salon", CREATE_SALON_DESCRIPTION, CREATE_SALON_SCHEMA)
async def create_salon(args: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = args.get("_agent_name") or "unknown"
    title = args.get("title", "(untitled salon)")
    participants = list(args.get("participants") or [])
    opening_message = args.get("opening_message")

    # Always include the creating agent.
    if agent_name and agent_name not in participants:
        participants.insert(0, agent_name)

    try:
        mgr = _get_salon_manager()
        salon_id = mgr.create(
            title=title,
            participants=participants,
            creator=agent_name,
            opening_message=opening_message,
        )
    except Exception as e:
        logger.error(f"create_salon failed: {e}", exc_info=True)
        return {
            "content": [{
                "type": "text",
                "text": f"Failed to create salon: {e}",
            }],
            "isError": True,
        }

    # Notify the server so it can fire the convener / broadcast.
    _emit_salon_event("salon_created", {
        "salon_id": salon_id,
        "title": title,
        "participants": participants,
        "creator": agent_name,
        "had_opening_message": bool(opening_message),
    })

    text = (
        f"✨ Salon created: '{title}'\n"
        f"ID: {salon_id}\n"
        f"Participants: {', '.join(participants)}\n"
    )
    if opening_message:
        text += f"\nOpening message posted from {agent_name}.\n"
    text += (
        "\nThe Convener will read the room and decide who speaks next. "
        "Use `post_to_salon` with this ID to send messages."
    )

    return {"content": [{"type": "text", "text": text}]}


# ------------------------------------------------------------------
# add_to_salon
# ------------------------------------------------------------------

ADD_TO_SALON_DESCRIPTION = """Add a participant to an existing salon you're already in.

The new participant becomes a candidate for the Convener to call in future turns. They don't immediately speak — the Convener reads the room first and decides whether to invoke them next.

Use this when the conversation has reached a point where another perspective is clearly needed and you want to formally include them. (Mentioning someone with @ in a message is a signal to the Convener but does NOT auto-add them.)
"""

ADD_TO_SALON_SCHEMA = {
    "type": "object",
    "properties": {
        "salon_id": {"type": "string", "description": "ID of the salon."},
        "participant": {
            "type": "string",
            "description": "Agent name or 'user' to add.",
        },
    },
    "required": ["salon_id", "participant"],
}


@register_tool("salons")
@tool("add_to_salon", ADD_TO_SALON_DESCRIPTION, ADD_TO_SALON_SCHEMA)
async def add_to_salon(args: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = args.get("_agent_name") or "unknown"
    salon_id = args.get("salon_id", "").strip()
    participant = args.get("participant", "").strip()

    if not salon_id or not participant:
        return {
            "content": [{"type": "text", "text": "salon_id and participant are required."}],
            "isError": True,
        }

    mgr = _get_salon_manager()
    salon = mgr.load(salon_id)
    if salon is None:
        return {
            "content": [{"type": "text", "text": f"Salon {salon_id} not found."}],
            "isError": True,
        }

    if agent_name not in (salon.get("participants") or []):
        return {
            "content": [{
                "type": "text",
                "text": f"You ({agent_name}) are not a participant in this salon. Only participants can add others.",
            }],
            "isError": True,
        }

    added = mgr.add_participant(salon_id, participant)
    if not added:
        return {
            "content": [{
                "type": "text",
                "text": f"{participant} was already in salon {salon_id}.",
            }],
        }

    _emit_salon_event("salon_participant_added", {
        "salon_id": salon_id,
        "added_by": agent_name,
        "participant": participant,
    })

    return {
        "content": [{
            "type": "text",
            "text": f"Added {participant} to salon '{salon.get('title')}'. The Convener will fold them in.",
        }],
    }


# ------------------------------------------------------------------
# list_salons
# ------------------------------------------------------------------

LIST_SALONS_DESCRIPTION = """List salons you're a participant in, sorted by most recent activity.

Returns title, participants, message count, last activity, and active/parked state for each salon. Use the IDs to read or post.
"""

LIST_SALONS_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "default": 20,
            "description": "Max salons to return (default 20).",
        },
        "all_salons": {
            "type": "boolean",
            "default": False,
            "description": "If true, list ALL salons in the system, not just yours. Default false.",
        },
    },
}


@register_tool("salons")
@tool("list_salons", LIST_SALONS_DESCRIPTION, LIST_SALONS_SCHEMA)
async def list_salons(args: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = args.get("_agent_name") or "unknown"
    limit = int(args.get("limit") or 20)
    all_salons = bool(args.get("all_salons"))

    mgr = _get_salon_manager()
    if all_salons:
        results = mgr.list_all(limit=limit)
    else:
        results = mgr.list_for_participant(agent_name, limit=limit)

    if not results:
        scope = "any salons" if all_salons else f"any salons including {agent_name}"
        return {"content": [{"type": "text", "text": f"No {scope} found."}]}

    lines = [f"# Salons ({len(results)})"]
    for r in results:
        active = "active" if r.get("gc_active") else "parked"
        last = r.get("last_message_at")
        from datetime import datetime, timezone
        last_str = (
            datetime.fromtimestamp(last, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            if last else "never"
        )
        participants = ", ".join(r.get("participants") or [])
        lines.append(
            f"\n## {r.get('title', '(untitled)')} ({active})\n"
            f"- ID: `{r.get('salon_id')}`\n"
            f"- Participants: {participants}\n"
            f"- Messages: {r.get('message_count', 0)}\n"
            f"- Last activity: {last_str}"
        )

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# ------------------------------------------------------------------
# read_salon
# ------------------------------------------------------------------

READ_SALON_DESCRIPTION = """Read the full message history of a salon.

You must be a participant. Returns each message with sender, timestamp, and content. Useful for catching up on a salon you've been quiet in or before posting.
"""

READ_SALON_SCHEMA = {
    "type": "object",
    "properties": {
        "salon_id": {"type": "string", "description": "ID of the salon."},
    },
    "required": ["salon_id"],
}


@register_tool("salons")
@tool("read_salon", READ_SALON_DESCRIPTION, READ_SALON_SCHEMA)
async def read_salon(args: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = args.get("_agent_name") or "unknown"
    salon_id = args.get("salon_id", "").strip()

    if not salon_id:
        return {
            "content": [{"type": "text", "text": "salon_id is required."}],
            "isError": True,
        }

    mgr = _get_salon_manager()
    salon = mgr.load(salon_id)
    if salon is None:
        return {
            "content": [{"type": "text", "text": f"Salon {salon_id} not found."}],
            "isError": True,
        }

    if agent_name not in (salon.get("participants") or []):
        return {
            "content": [{
                "type": "text",
                "text": f"You ({agent_name}) are not a participant in this salon.",
            }],
            "isError": True,
        }

    from datetime import datetime, timezone
    title = salon.get("title", "(untitled)")
    participants = ", ".join(salon.get("participants") or [])
    messages = salon.get("messages") or []

    lines = [
        f"# Salon: {title}",
        f"Participants: {participants}",
        f"Messages: {len(messages)}",
        f"State: {'active' if salon.get('gc_active') else 'parked'} "
        f"(recheck every {salon.get('gc_recheck_minutes')} min)",
        "",
        "---",
        "",
    ]

    for msg in messages:
        ts = msg.get("created_at")
        stamp = (
            datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
            if ts else "??:??:??"
        )
        sender = msg.get("from", "?")
        content = msg.get("content", "")
        lines.append(f"**[{stamp}] {sender}**: {content}")
        lines.append("")

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


# ------------------------------------------------------------------
# post_to_salon
# ------------------------------------------------------------------

POST_TO_SALON_DESCRIPTION = """Post a message to a salon you're a participant in.

Use this only when you've been called by the Convener — agents normally don't post unprompted. (The exception: if you've been added to a salon and you want to introduce yourself, this is the right tool, but use sparingly.)

After your message lands, the Convener reads the room and decides who speaks next.
"""

POST_TO_SALON_SCHEMA = {
    "type": "object",
    "properties": {
        "salon_id": {"type": "string", "description": "ID of the salon."},
        "content": {"type": "string", "description": "Your message content."},
    },
    "required": ["salon_id", "content"],
}


@register_tool("salons")
@tool("post_to_salon", POST_TO_SALON_DESCRIPTION, POST_TO_SALON_SCHEMA)
async def post_to_salon(args: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = args.get("_agent_name") or "unknown"
    salon_id = args.get("salon_id", "").strip()
    content = args.get("content", "").strip()

    if not salon_id or not content:
        return {
            "content": [{"type": "text", "text": "salon_id and content are required."}],
            "isError": True,
        }

    mgr = _get_salon_manager()
    salon = mgr.load(salon_id)
    if salon is None:
        return {
            "content": [{"type": "text", "text": f"Salon {salon_id} not found."}],
            "isError": True,
        }

    if agent_name not in (salon.get("participants") or []):
        return {
            "content": [{
                "type": "text",
                "text": f"You ({agent_name}) are not a participant in this salon.",
            }],
            "isError": True,
        }

    try:
        message_id = mgr.append_message(
            salon_id=salon_id,
            from_participant=agent_name,
            content=content,
        )
    except Exception as e:
        logger.error(f"post_to_salon failed: {e}", exc_info=True)
        return {
            "content": [{"type": "text", "text": f"Failed to post: {e}"}],
            "isError": True,
        }

    _emit_salon_event("salon_message_posted", {
        "salon_id": salon_id,
        "message_id": message_id,
        "from": agent_name,
    })

    return {
        "content": [{
            "type": "text",
            "text": f"Posted to '{salon.get('title')}'. Convener will route from here.",
        }],
    }


# ------------------------------------------------------------------
# Salon-event emitter — published to the server's salon event bus
# ------------------------------------------------------------------


def _emit_salon_event(event_type: str, payload: Dict[str, Any]) -> None:
    """Publish a salon event to the in-process event bus.

    Soft-fail: if the server isn't running this in-process (e.g. CLI
    testing), the event is just logged. The actual convener-fire wiring
    lives in main.py and reads from this bus.
    """
    try:
        # Lazy import to avoid circulars
        from salon_events import publish

        publish(event_type, payload)
    except Exception as e:
        logger.debug(f"salon event {event_type} not published (no bus?): {e}")
        logger.info(f"[salon-event] {event_type} {json.dumps(payload)}")
