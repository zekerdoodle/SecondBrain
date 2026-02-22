"""
Tool call serialization for chat history.

Each tool gets its own serializer that decides which params and output to preserve.
Serialized tool calls are stored as hidden messages in chat history and injected
into conversation context on subsequent turns.
"""

from typing import Any, Callable, Dict, Optional
import json
import logging

logger = logging.getLogger(__name__)

# MCP prefix used by Second Brain tools
MCP_PREFIX = "mcp__brain__"


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text, preserving complete lines where possible."""
    if not text or len(text) <= max_chars:
        return text or ""
    # Try to break at a newline
    cut = text[:max_chars]
    last_nl = cut.rfind("\n")
    if last_nl > max_chars * 0.6:
        return cut[:last_nl] + "\n..."
    return cut + "..."


def _parse_args(args_raw) -> dict:
    """Parse args from string or dict."""
    if isinstance(args_raw, dict):
        return args_raw
    if isinstance(args_raw, str):
        try:
            return json.loads(args_raw)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": args_raw}
    return {}


def _pick(d: dict, keys: list) -> dict:
    """Pick specific keys from a dict."""
    return {k: d[k] for k in keys if k in d}


# ── Tier 1: Full params + truncated output ──

def serialize_bash(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["command", "description"]),
        "output_summary": _truncate(output, 500),
    }


def serialize_invoke_agent(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["agent", "mode", "model_override"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 300)
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_invoke_agent_chain(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["agents", "context"])
    if "initial_prompt" in args:
        kept["initial_prompt"] = _truncate(str(args["initial_prompt"]), 300)
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_consult_llm(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["provider", "model", "temperature"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 300)
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_generate_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["prompt", "aspect_ratio", "resolution"]),
        "output_summary": output,  # Just file path + dims, keep full
    }


def serialize_edit_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["image_path", "prompt", "aspect_ratio"]),
        "output_summary": output,
    }


def serialize_fal_text_to_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["prompt", "model", "image_size", "num_images", "negative_prompt", "seed"]),
        "output_summary": output,  # File paths + seed, keep full
    }


def serialize_fal_image_to_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["image_path", "prompt", "model", "strength", "image_size"]),
        "output_summary": output,
    }


def serialize_fal_multi_ref_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["image_paths", "prompt", "model", "image_size"]),
        "output_summary": output,
    }


# ── Tier 2: Key params + IDs from output ──

def serialize_gmail_send(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["to", "subject"]),
        "output_summary": _truncate(output, 300),
    }


def serialize_gmail_reply(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["message_id", "reply_all"]),
        "output_summary": _truncate(output, 300),
    }


def serialize_gmail_draft_create(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["to", "subject"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_gmail_trash(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["message_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_gmail_modify_labels(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["message_ids", "add_labels", "remove_labels"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_google_create_tasks_and_events(args: dict, output: str, is_error: bool) -> dict:
    kept = {}
    if "tasks" in args and isinstance(args["tasks"], list):
        kept["tasks"] = [t.get("title", "?") for t in args["tasks"][:5]]
    if "events" in args and isinstance(args["events"], list):
        kept["events"] = [e.get("summary", "?") for e in args["events"][:5]]
    return {
        "args": kept,
        "output_summary": _truncate(output, 300),
    }


def serialize_google_update_task(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id", "title", "status", "due", "notes"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_google_delete_task(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_moltbook_post(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["title", "submolt"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_moltbook_comment(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["post_id"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_spotify_create_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["name"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_spotify_add_to_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id", "track_ids"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_spotify_playback_control(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["action"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_ytmusic_create_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["title"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_ytmusic_add_to_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id", "song_ids"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_ytmusic_remove_from_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id", "song_ids"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_ytmusic_delete_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_schedule_self(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["schedule"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 200)
    return {
        "args": kept,
        "output_summary": _truncate(output, 200),
    }


def serialize_schedule_agent(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["agent", "schedule"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 200)
    return {
        "args": kept,
        "output_summary": _truncate(output, 200),
    }


def serialize_scheduler_update(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id", "schedule", "prompt", "enabled"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_scheduler_remove(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_forms_save(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["form_id", "submission_id"]),
        "output_summary": _truncate(output, 100),
    }


# ── Tier 3: Query tools — truncated results ──

def serialize_query_tool(keep_params: list, output_limit: int = 500):
    """Factory for query tool serializers."""
    def serializer(args: dict, output: str, is_error: bool) -> dict:
        return {
            "args": _pick(args, keep_params),
            "output_summary": _truncate(output, output_limit),
        }
    return serializer


# ── Tier 4: Compact — just note it happened ──

def serialize_working_memory_add(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["tag", "ttl"])
    if "content" in args:
        kept["content"] = _truncate(str(args["content"]), 100)
    return {"args": kept, "output_summary": ""}


def serialize_working_memory_update(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["index"])
    if "content" in args:
        kept["content"] = _truncate(str(args["content"]), 100)
    return {"args": kept, "output_summary": ""}


def serialize_compact(keep_params: list):
    """Factory for compact serializers (Tier 4) — minimal footprint."""
    def serializer(args: dict, output: str, is_error: bool) -> dict:
        return {
            "args": _pick(args, keep_params),
            "output_summary": _truncate(output, 100) if output else "",
        }
    return serializer


def serialize_memory_read(args: dict, output: str, is_error: bool) -> dict:
    return {"args": {}, "output_summary": "read memory.md"}


def serialize_restart_server(args: dict, output: str, is_error: bool) -> dict:
    return {"args": {}, "output_summary": "restarted server"}


# ── Registry ──
# Keys are tool names WITHOUT the MCP prefix.
# The lookup function strips the prefix before matching.

TOOL_SERIALIZERS: Dict[str, Callable[[dict, str, bool], dict]] = {
    # Tier 1
    "bash": serialize_bash,
    "Bash": serialize_bash,
    "invoke_agent": serialize_invoke_agent,
    "invoke_agent_chain": serialize_invoke_agent_chain,
    "consult_llm": serialize_consult_llm,
    "generate_image": serialize_generate_image,
    "edit_image": serialize_edit_image,
    "fal_text_to_image": serialize_fal_text_to_image,
    "fal_image_to_image": serialize_fal_image_to_image,
    "fal_multi_ref_image": serialize_fal_multi_ref_image,

    # Tier 2
    "gmail_send": serialize_gmail_send,
    "gmail_reply": serialize_gmail_reply,
    "gmail_draft_create": serialize_gmail_draft_create,
    "gmail_trash": serialize_gmail_trash,
    "gmail_modify_labels": serialize_gmail_modify_labels,
    "google_create_tasks_and_events": serialize_google_create_tasks_and_events,
    "google_update_task": serialize_google_update_task,
    "google_delete_task": serialize_google_delete_task,
    "moltbook_post": serialize_moltbook_post,
    "moltbook_comment": serialize_moltbook_comment,
    "spotify_create_playlist": serialize_spotify_create_playlist,
    "spotify_add_to_playlist": serialize_spotify_add_to_playlist,
    "spotify_playback_control": serialize_spotify_playback_control,
    "ytmusic_create_playlist": serialize_ytmusic_create_playlist,
    "ytmusic_add_to_playlist": serialize_ytmusic_add_to_playlist,
    "ytmusic_remove_from_playlist": serialize_ytmusic_remove_from_playlist,
    "ytmusic_delete_playlist": serialize_ytmusic_delete_playlist,
    "schedule_self": serialize_schedule_self,
    "schedule_agent": serialize_schedule_agent,
    "scheduler_update": serialize_scheduler_update,
    "scheduler_remove": serialize_scheduler_remove,
    "forms_save": serialize_forms_save,

    # Tier 3
    "gmail_list_messages": serialize_query_tool(["query", "max_results"]),
    "gmail_get_message": serialize_query_tool(["message_id"]),
    "gmail_list_labels": serialize_query_tool([], 300),
    "google_list": serialize_query_tool(["limit"]),
    "web_search": serialize_query_tool(["query"]),
    "page_parser": serialize_query_tool(["url"]),
    "moltbook_feed": serialize_query_tool(["sort", "submolt"]),
    "moltbook_get_post": serialize_query_tool(["post_id"]),
    "moltbook_notifications": serialize_query_tool([], 300),
    "moltbook_account_status": serialize_query_tool([], 500),
    "moltbook_check_dms": serialize_query_tool([], 500),
    "moltbook_respond_challenge": serialize_query_tool(["answer", "challenge_id"], 500),
    "moltbook_challenge_log": serialize_query_tool(["limit"], 500),
"spotify_search": serialize_query_tool(["query", "search_type"]),
    "spotify_recently_played": serialize_query_tool(["limit"]),
    "spotify_top_items": serialize_query_tool(["item_type", "time_range"]),
    "spotify_get_playlists": serialize_query_tool([]),
    "spotify_now_playing": serialize_query_tool([], 200),
    "ytmusic_search": serialize_query_tool(["query", "search_type"]),
    "ytmusic_get_playlists": serialize_query_tool([]),
    "ytmusic_get_playlist_items": serialize_query_tool(["playlist_id"]),
    "ytmusic_get_liked": serialize_query_tool([]),
    "fal_list_models": serialize_query_tool(["query", "category", "endpoint_id"]),

    # Tier 4
    "working_memory_add": serialize_working_memory_add,
    "working_memory_update": serialize_working_memory_update,
    "working_memory_remove": serialize_compact(["index"]),
    "working_memory_list": serialize_compact([]),
    "working_memory_snapshot": serialize_compact(["index", "section"]),
    "memory_append": serialize_compact(["section"]),
    "memory_read": serialize_memory_read,
    "forms_define": serialize_compact(["form_id", "title"]),
    "forms_show": serialize_compact(["form_id"]),
    "forms_list": serialize_compact(["form_id"]),
    "restart_server": serialize_restart_server,
    "chess": serialize_compact(["move", "action"]),
"scheduler_list": serialize_query_tool([], 300),
    "Skill": serialize_compact(["skill", "args"]),
    "compact_conversation": serialize_compact(["keep_exchanges", "reason"]),
}


def _default_serializer(args: dict, output: str, is_error: bool) -> dict:
    """Fallback for unknown tools: Tier 3 behavior."""
    truncated_args = {}
    for k, v in list(args.items())[:5]:
        truncated_args[k] = _truncate(str(v), 200) if isinstance(v, str) and len(str(v)) > 200 else v
    return {
        "args": truncated_args,
        "output_summary": _truncate(output, 300),
    }


def serialize_tool_call(
    tool_name: str,
    args_raw: Any,
    output: str,
    is_error: bool,
    tool_id: Optional[str] = None,
) -> dict:
    """
    Serialize a tool call for chat history storage.

    Returns a dict ready to be stored as a message with role="tool_call".
    """
    # Strip MCP prefix for registry lookup
    lookup_name = tool_name
    if lookup_name.startswith(MCP_PREFIX):
        lookup_name = lookup_name[len(MCP_PREFIX):]

    args = _parse_args(args_raw)
    serializer = TOOL_SERIALIZERS.get(lookup_name, _default_serializer)

    try:
        result = serializer(args, output or "", is_error)
    except Exception as e:
        logger.warning(f"Serializer error for {tool_name}: {e}")
        result = _default_serializer(args, output or "", is_error)

    return {
        "role": "tool_call",
        "hidden": True,
        "tool_name": tool_name,
        "tool_id": tool_id or "",
        "args": result.get("args", {}),
        "output_summary": result.get("output_summary", ""),
        "is_error": is_error,
    }


def format_tool_for_history(tool_msg: dict) -> str:
    """
    Format a stored tool_call message as a compact one-liner for history injection.

    Example output:
      [Tool: bash | cmd: `git status` | Output: On branch main...]
    """
    name = tool_msg.get("tool_name", "unknown")
    # Strip MCP prefix for display
    display_name = name
    if display_name.startswith(MCP_PREFIX):
        display_name = display_name[len(MCP_PREFIX):]

    args = tool_msg.get("args", {})
    output = tool_msg.get("output_summary", "")
    is_error = tool_msg.get("is_error", False)

    # Build param string from args
    param_parts = []
    for k, v in args.items():
        val = str(v)
        if len(val) > 120:
            val = val[:117] + "..."
        param_parts.append(f"{k}: {val}")
    params_str = " | ".join(param_parts) if param_parts else ""

    # Build output portion
    output_str = ""
    if output:
        prefix = "Error" if is_error else "Output"
        output_str = f" | {prefix}: {output}"

    if params_str:
        return f"[Tool: {display_name} | {params_str}{output_str}]"
    else:
        return f"[Tool: {display_name}{output_str}]"
