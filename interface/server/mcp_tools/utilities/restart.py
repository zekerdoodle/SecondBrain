"""
Server Restart tool.

Restarts the Second Brain server with conversation continuity.
"""

import os
import sys
import asyncio
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

_ALLOWED_RESTART_CONSUMER = "main_streaming_finalizer"


def _legacy_visible_chat_bootstrap_allowed(
    *,
    restart_consumer: str,
    session_id: str | None,
    source_chat_id: str | None,
    source_agent: str,
    running_invocations: list[dict[str, Any]],
) -> bool:
    """Allow the first visible-chat restart after this gate is deployed.

    The live backend may still be running the old main.py/ClaudeWrapper code and
    therefore cannot pass ``--restart-consumer main_streaming_finalizer`` into
    the Codex MCP bridge yet. The old backend finalizer can still consume the
    marker and spawn the restart, but only for the visible WebSocket chat path.

    Keep this bootstrap deliberately tiny: one active running_agents entry, it
    is a chat entry, and it is the same chat/agent that invoked restart_server.
    Scheduled/invoked agents may carry the same source_chat_id, so any extra
    entry or non-chat kind fails closed before marker writes.
    """
    if restart_consumer != "none":
        return False
    trigger_chat_id = source_chat_id or session_id
    if not trigger_chat_id or len(running_invocations) != 1:
        return False
    entry = running_invocations[0]
    return (
        entry.get("kind") == "chat"
        and entry.get("source_chat_id") == trigger_chat_id
        and entry.get("agent") == source_agent
    )


# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@register_tool("utilities")
@tool(
    name="restart_server",
    description="""Restart the Second Brain server to apply changes. Use this when you've made changes that require a server restart (e.g., modified server code, updated MCP tools, changed configurations).

Two modes available:
- **Quick restart** (default): Only restarts the Python server. Fast (~5 seconds).
- **Full restart with rebuild**: Rebuilds the frontend first, then restarts. Use when frontend code changed.

IMPORTANT: This tool will:
1. Save the current conversation state
2. Stop the server gracefully
3. Optionally rebuild the frontend (if rebuild=true)
4. Restart the server with your changes applied
5. Automatically continue ALL active conversations after restart (both yours and any other agents)

You will receive a system message after restart confirming it worked. Use this to verify your changes.""",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Current session ID to continue after restart (auto-detected if not provided)"},
            "reason": {"type": "string", "description": "Why you're restarting — describe the change you made or why the restart is needed. Defaults to a generic message if omitted."},
            "rebuild": {"type": "boolean", "description": "If true, rebuild frontend before restart. Use when frontend code changed. Default: false (quick restart).", "default": False},
            "pending_messages": {"type": "array", "description": "Messages not yet saved (will be preserved)", "items": {"type": "object"}}
        }
    }
)
async def restart_server(args: Dict[str, Any]) -> Dict[str, Any]:
    """Restart the server with conversation continuity."""
    try:
        session_id = args.get("session_id")
        source_chat_id = args.get("_source_chat_id")
        calling_agent_name = args.get("_agent_name")
        reason = args.get("reason", "Server restart requested")
        rebuild = args.get("rebuild", False)

        # Import tools
        import restart_tool as rt
        import sys

        # Access the active conversations from the main server module
        main_module = sys.modules.get('main') or sys.modules.get('__main__')
        active_convs = {}
        chat_manager = None
        active_processing = {}
        if main_module:
            active_convs = getattr(main_module, 'active_conversations', {})
            chat_manager = getattr(main_module, 'chat_manager', None)
            active_processing = getattr(main_module, 'active_processing_sessions', {})

        # Prefer the MCP server's injected calling chat. Explicit session_id still
        # wins for manual/advanced use; active_processing remains only a fallback.
        if not session_id and source_chat_id:
            chat_file = rt.CHATS_DIR / f"{source_chat_id}.json"
            if source_chat_id in active_convs or chat_file.exists():
                session_id = source_chat_id

        if not session_id:
            for sid in active_processing:
                if sid in active_convs:
                    session_id = sid
                    break

        if not session_id:
            try:
                active_room_file = rt.CLAUDE_DIR / "active_room.json"
                if active_room_file.exists():
                    import json
                    active_room = json.loads(active_room_file.read_text()).get("room")
                    if active_room and (rt.CHATS_DIR / f"{active_room}.json").exists():
                        session_id = active_room
            except Exception:
                pass

        if not session_id:
            return {
                "content": [{"type": "text", "text": "Error: Could not determine session_id. No active conversations found."}],
                "is_error": True
            }

        # Auto-detect the source agent from the trigger chat's stored agent field.
        # MCP tools may run outside main.py's process, so fall back to direct chat
        # JSON and finally the injected calling agent.
        source_agent = calling_agent_name or "character"
        try:
            stored_chat = None
            if chat_manager:
                stored_chat = chat_manager.load_chat(session_id)
            if stored_chat is None:
                chat_file = rt.CHATS_DIR / f"{session_id}.json"
                if chat_file.exists():
                    import json
                    stored_chat = json.loads(chat_file.read_text())
            if stored_chat:
                stored_agent = stored_chat.get("agent")
                if stored_agent:
                    source_agent = stored_agent
        except Exception:
            pass

        restart_consumer = args.get("_restart_consumer") or "none"

        import running_agents

        running_agents_bootstrap_note = ""
        try:
            running_invocations = await running_agents.list_source_of_truth()
        except running_agents.RunningAgentsEndpointMissingError as e:
            if restart_consumer != _ALLOWED_RESTART_CONSUMER:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            "Error: restart_server cannot safely restart from this invocation context. "
                            "This MCP call was not launched with the main.py streaming finalizer "
                            "that performs the clean save and detached restart subprocess spawn, "
                            "and the authoritative running_agents endpoint is not available to "
                            "prove a legacy visible-chat bootstrap context. "
                            "No pending restart or continuation marker was written."
                        ),
                    }],
                    "is_error": True,
                }
            # First-load/deployment bootstrap only: this code can be live in the
            # MCP subprocess before the backend has restarted into the new
            # /api/internal/running-agents route. Let that specific explicit
            # finalizer restart proceed, visibly degraded, so the endpoint can
            # be loaded. Once the endpoint exists, every other authoritative-read
            # failure still fails closed below.
            running_invocations = []
            running_agents_bootstrap_note = (
                "\nWarning: authoritative running_agents endpoint is not loaded yet "
                f"({e}). Proceeding through the narrow deployment-bootstrap "
                "path; the live backend shutdown hook should merge its "
                "in-process running_agents snapshot during shutdown. After "
                "this endpoint is loaded, restart will fail closed on "
                "authoritative read failure."
            )
        except Exception as e:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: could not read authoritative running_agents "
                        f"source before restart: {e}"
                    ),
                }],
                "is_error": True,
            }

        legacy_visible_chat_bootstrap = _legacy_visible_chat_bootstrap_allowed(
            restart_consumer=restart_consumer,
            session_id=session_id,
            source_chat_id=source_chat_id,
            source_agent=source_agent,
            running_invocations=running_invocations,
        )
        if restart_consumer != _ALLOWED_RESTART_CONSUMER and not legacy_visible_chat_bootstrap:
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: restart_server cannot safely restart from this invocation context. "
                        "This MCP call was not launched with the main.py streaming finalizer "
                        "that performs the clean save and detached restart subprocess spawn, "
                        "and it did not match the single-active-visible-chat bootstrap guard. "
                        "No pending restart or continuation marker was written."
                    ),
                }],
                "is_error": True,
            }
        if legacy_visible_chat_bootstrap:
            running_agents_bootstrap_note = (
                "\nWarning: accepted through the legacy visible-chat restart bootstrap path. "
                "The live backend has not loaded restart_consumer forwarding yet, but "
                "authoritative running_agents shows this caller is the sole active "
                "visible chat, whose main.py finalizer can consume pending_restart.json "
                "and spawn the detached restart subprocess. After this restart loads "
                "the saved code, visible-chat restarts must use the explicit "
                "main_streaming_finalizer consumer."
            )

        # Build a map of ALL actively processing sessions -> their agent names.
        # If the MCP tool is process-isolated from main.py, preserve at least the
        # triggering session so restart continuation has a truthful agent.
        all_active = {}
        for sid in active_processing:
            agent = "character"  # Default
            try:
                if chat_manager:
                    sc = chat_manager.load_chat(sid)
                    if sc and sc.get("agent"):
                        agent = sc["agent"]
                else:
                    chat_file = rt.CHATS_DIR / f"{sid}.json"
                    if chat_file.exists():
                        import json
                        sc = json.loads(chat_file.read_text())
                        if sc.get("agent"):
                            agent = sc["agent"]
            except Exception:
                pass
            all_active[sid] = agent
        if session_id not in all_active:
            all_active[session_id] = source_agent

        # NOTE: We do NOT save conv.messages here or spawn the restart subprocess.
        # The streaming loop in main.py detects that restart_server completed,
        # halts the model stream, does a clean finalization/save (with proper
        # display_messages including block model), and THEN spawns the restart.
        # This prevents duplicate content from WAL recovery.

        # Choose restart script based on rebuild flag
        if rebuild:
            restart_script = rt.SECOND_BRAIN_ROOT / "interface" / "restart-server-full.sh"
            restart_type = "full (with frontend rebuild)"
            wait_time = 30
        else:
            restart_script = rt.QUICK_RESTART_SCRIPT
            restart_type = "quick (server only)"
            wait_time = 5

        log_file = rt.CLAUDE_DIR / "server_restart.log"

        # Save the continuation marker and pending restart config as one
        # logical operation. If the second write fails, remove the marker this
        # attempt just created so a failed-to-spawn restart cannot leave stale
        # continuation state behind.
        import json
        pending_restart_file = rt.CLAUDE_DIR / "pending_restart.json"
        continuation = None
        wrote_continuation = False
        try:
            continuation = rt.save_continuation_state(
                session_id=session_id,
                reason=reason,
                source=source_agent,
                all_active_sessions=all_active,
                running_invocations=running_invocations,
            )
            wrote_continuation = True
            pending_restart_file.write_text(json.dumps({
                "rebuild": rebuild,
                "restart_script": str(restart_script),
                "log_file": str(log_file),
                "restart_type": restart_type,
                "wait_time": wait_time,
            }))
        except Exception:
            try:
                pending_restart_file.unlink()
            except FileNotFoundError:
                pass
            if wrote_continuation:
                try:
                    rt.RESTART_MARKER.unlink()
                except FileNotFoundError:
                    pass
            raise

        agent_invocation_count = len(continuation.get("agent_invocations", []))
        bystander_count = len(all_active) - 1  # Exclude the triggering session

        bystander_note = ""
        if bystander_count > 0:
            bystander_note = f"\n{bystander_count} other active session(s) will also be resumed after restart."
        if agent_invocation_count > 0:
            bystander_note += f"\n{agent_invocation_count} active agent invocation(s) will also be resumed after restart."

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Restart initiated for session {session_id}.\n"
                    f"Source: {source_agent}\n"
                    f"Reason: {reason}\n"
                    f"Mode: {restart_type}\n"
                    f"The server will restart in ~{wait_time} seconds.\n"
                    f"After restart, you'll receive a continuation message."
                    f"{bystander_note}"
                    f"{running_agents_bootstrap_note}"
                )
            }]
        }

    except Exception as e:
        import traceback
        return {
            "content": [{
                "type": "text",
                "text": f"Error initiating restart: {str(e)}\n{traceback.format_exc()}"
            }],
            "is_error": True
        }
