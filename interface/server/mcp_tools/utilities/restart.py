"""
Server Restart tool.

Restarts the Second Brain server with conversation continuity.
"""

import os
import sys
import asyncio
import subprocess
import logging
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.restart")

_ALLOWED_RESTART_CONSUMER = "main_streaming_finalizer"
_AGENT_MANAGED_RESTART_CONSUMER = "agent_managed_restart"
_AGENT_MANAGED_RESTART_KINDS = frozenset({
    "invoke_foreground",
    "invoke_ping",
    "invoke_trust",
    "background_processing",
    "agent_conversation_join",
})


def _summarize_running_entry(entry: dict[str, Any]) -> str:
    agent = entry.get("agent") or "unknown"
    kind = entry.get("kind") or "unknown"
    conversation_id = entry.get("conversation_id") or "no-thread"
    return f"{agent}/{kind}/{conversation_id}"


def _parse_agent_managed_restart_consumer(restart_consumer: str) -> tuple[str | None, str | None]:
    parts = (restart_consumer or "").split(":", 2)
    if len(parts) != 3 or parts[0] != _AGENT_MANAGED_RESTART_CONSUMER:
        return None, None
    mode, conversation_id = parts[1], parts[2]
    if not mode or not conversation_id:
        return None, None
    return mode, conversation_id


def _agent_managed_restart_error(
    *,
    restart_consumer: str,
    source_agent: str,
    running_invocations: list[dict[str, Any]],
) -> str | None:
    """Return None only for Patch's current runner invocation.

    Scheduled Patch wakes have two authoritative entries: the outer scheduler
    wrapper plus the inner durable agent-thread invocation. The inner
    conversation_id is carried in the restart consumer so extra active work still
    fails closed before marker writes.
    """
    mode, conversation_id = _parse_agent_managed_restart_consumer(restart_consumer)
    if not conversation_id:
        return "agent-managed restart consumer is missing the current conversation id"
    if source_agent != "patch":
        return "agent-managed restarts are Patch-only"

    current_entries = []
    scheduled_wrappers = []
    unexpected_entries = []
    for entry in running_invocations:
        if (
            entry.get("agent") == source_agent
            and entry.get("conversation_id") == conversation_id
            and entry.get("kind") in _AGENT_MANAGED_RESTART_KINDS
        ):
            current_entries.append(entry)
        elif (
            mode == "scheduled"
            and entry.get("agent") == source_agent
            and entry.get("kind") == "scheduled"
            and not entry.get("conversation_id")
        ):
            scheduled_wrappers.append(entry)
        else:
            unexpected_entries.append(entry)

    if len(current_entries) != 1:
        return (
            "authoritative running_agents did not show exactly one current "
            f"Patch invocation for thread {conversation_id}"
        )
    if len(scheduled_wrappers) > 1:
        return "authoritative running_agents showed multiple scheduled Patch wrappers"
    if unexpected_entries:
        details = ", ".join(_summarize_running_entry(entry) for entry in unexpected_entries[:3])
        more = "" if len(unexpected_entries) <= 3 else f", +{len(unexpected_entries) - 3} more"
        return f"authoritative running_agents showed other active invocation(s): {details}{more}"
    return None


def _is_agent_managed_restart_consumer(restart_consumer: str) -> bool:
    mode, conversation_id = _parse_agent_managed_restart_consumer(restart_consumer)
    return bool(mode and conversation_id)


def _spawn_managed_restart_subprocess(restart_script: str, log_file: str) -> None:
    subprocess.Popen(
        f"sleep 1 && bash {restart_script} > {log_file} 2>&1",
        shell=True,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
        restart_consumer = args.get("_restart_consumer") or "none"
        agent_managed_consumer = _is_agent_managed_restart_consumer(restart_consumer)

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

        if not session_id and not agent_managed_consumer:
            try:
                active_room_file = rt.CLAUDE_DIR / "active_room.json"
                if active_room_file.exists():
                    import json
                    active_room = json.loads(active_room_file.read_text()).get("room")
                    if active_room and (rt.CHATS_DIR / f"{active_room}.json").exists():
                        session_id = active_room
            except Exception:
                pass

        if not session_id and not agent_managed_consumer:
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
            if stored_chat is None and session_id:
                chat_file = rt.CHATS_DIR / f"{session_id}.json"
                if chat_file.exists():
                    import json
                    stored_chat = json.loads(chat_file.read_text())
            if stored_chat and not agent_managed_consumer:
                stored_agent = stored_chat.get("agent")
                if stored_agent:
                    source_agent = stored_agent
        except Exception:
            pass

        import running_agents

        running_agents_bootstrap_note = ""
        try:
            running_invocations = await running_agents.list_source_of_truth()
        except running_agents.RunningAgentsEndpointMissingError as e:
            if restart_consumer != _ALLOWED_RESTART_CONSUMER:
                if _is_agent_managed_restart_consumer(restart_consumer):
                    endpoint_rejection_reason = "agent_managed_guard_unavailable"
                elif restart_consumer == "none":
                    endpoint_rejection_reason = "no_restart_consumer"
                else:
                    endpoint_rejection_reason = "unsupported_restart_consumer"
                logger.warning(
                    "RESTART: rejected restart request before marker writes "
                    "(reason=%s, consumer=%s, "
                    "source_agent=%s, session_id=%s, source_chat_id=%s, "
                    "running_agents=endpoint_missing, error=%s)",
                    endpoint_rejection_reason,
                    restart_consumer,
                    source_agent,
                    session_id,
                    source_chat_id,
                    e,
                )
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            "Error: restart_server cannot safely restart from this invocation context. "
                            "This MCP call was not launched with the main.py streaming finalizer "
                            "that performs the clean save and detached restart subprocess spawn, "
                            "and the authoritative running_agents endpoint is not available to "
                            "validate a Patch-only agent-managed restart context. "
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
            logger.warning(
                "RESTART: rejected restart request before marker writes "
                "(reason=running_agents_read_failed, consumer=%s, "
                "source_agent=%s, session_id=%s, source_chat_id=%s, error=%s)",
                restart_consumer,
                source_agent,
                session_id,
                source_chat_id,
                e,
            )
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

        agent_managed_restart = False
        agent_managed_restart_error = ""
        if _is_agent_managed_restart_consumer(restart_consumer):
            agent_managed_restart_error = _agent_managed_restart_error(
                restart_consumer=restart_consumer,
                source_agent=source_agent,
                running_invocations=running_invocations,
            ) or ""
            agent_managed_restart = not agent_managed_restart_error

        acceptance_mode = None
        if restart_consumer == _ALLOWED_RESTART_CONSUMER:
            acceptance_mode = _ALLOWED_RESTART_CONSUMER
        elif agent_managed_restart:
            acceptance_mode = _AGENT_MANAGED_RESTART_CONSUMER

        if acceptance_mode is None:
            if restart_consumer == "none":
                rejection_detail = (
                    "This MCP call did not provide a restart consumer, so no "
                    "streaming finalizer or direct-spawn owner is known."
                )
                rejection_reason = "no_restart_consumer"
            elif agent_managed_restart_error:
                rejection_detail = (
                    "This MCP call did not satisfy the Patch-only "
                    f"agent-managed restart guard. Detail: {agent_managed_restart_error}."
                )
                rejection_reason = "agent_managed_guard_failed"
            else:
                rejection_detail = f"Unsupported restart consumer: {restart_consumer}."
                rejection_reason = "unsupported_restart_consumer"
            running_summary = ", ".join(
                _summarize_running_entry(entry) for entry in running_invocations[:3]
            )
            if len(running_invocations) > 3:
                running_summary += f", +{len(running_invocations) - 3} more"
            logger.warning(
                "RESTART: rejected restart request before marker writes "
                "(reason=%s, consumer=%s, source_agent=%s, session_id=%s, "
                "source_chat_id=%s, running_invocations=%d%s)",
                rejection_reason,
                restart_consumer,
                source_agent,
                session_id,
                source_chat_id,
                len(running_invocations),
                f", running_summary={running_summary}" if running_summary else "",
            )
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: restart_server cannot safely restart from this invocation context. "
                        f"{rejection_detail} "
                        "Restart success requires either the main.py streaming finalizer "
                        "consumer or the Patch-only agent-managed restart guard."
                        " "
                        "No pending restart or continuation marker was written."
                    ),
                }],
                "is_error": True,
            }
        if agent_managed_restart:
            running_agents_bootstrap_note = (
                "\nManaged restart accepted from a scheduled/invoked Patch context. "
                "Authoritative running_agents shows no protected active work beyond "
                "the current Patch invocation, so restart_server will spawn the "
                "detached restart subprocess directly after writing continuation state."
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
        if session_id and session_id not in all_active:
            all_active[session_id] = source_agent

        # For visible chat restarts, main.py's streaming finalizer does the clean
        # save and detached spawn after this tool returns. Agent-managed Patch
        # restarts have no streaming finalizer, so this tool writes the same marker
        # contract and spawns directly after the authoritative guard below passes.

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
            logger.info(
                "RESTART: accepting restart request before marker writes "
                "(consumer=%s, acceptance_mode=%s, source_agent=%s, session_id=%s, "
                "source_chat_id=%s, running_invocations=%d)",
                restart_consumer,
                acceptance_mode,
                source_agent,
                session_id,
                source_chat_id,
                len(running_invocations),
            )
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
                "restart_consumer": restart_consumer,
                "acceptance_mode": acceptance_mode,
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

        if agent_managed_restart:
            try:
                pending_restart_file.unlink()
            except FileNotFoundError:
                pass
            try:
                _spawn_managed_restart_subprocess(str(restart_script), str(log_file))
            except Exception:
                try:
                    pending_restart_file.unlink()
                except FileNotFoundError:
                    pass
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
