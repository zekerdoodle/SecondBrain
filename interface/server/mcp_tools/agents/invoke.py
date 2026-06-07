"""
Agent invocation tools.

Tools for invoking specialized agents:
- invoke_agent: Single agent invocation (parallel-friendly, independent tasks)
- invoke_agent_chain: Serial agent execution (dependent/sequential tasks)
- invoke_agent_parallel: Multiple agents concurrently, results returned together
"""

import asyncio
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

# Add agents directory to path
ROOT_DIR = Path(__file__).resolve().parents[4]
SERVER_DIR = str(ROOT_DIR / "interface" / "server")
AGENTS_DIR = str(ROOT_DIR / ".claude" / "agents")
INTERNAL_AGENT_INVOKE_TOKEN_FILE = ROOT_DIR / ".claude" / ".secrets" / "internal_agent_invoke_token"
for _path in (SERVER_DIR, AGENTS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)


PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS = "agent_outputs"
PROJECT_OUTPUT_CONTRACT_NONE = "none"
PROJECT_OUTPUT_CONTRACT_VALUES = {
    PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS,
    PROJECT_OUTPUT_CONTRACT_NONE,
}


def _build_invoke_tool_schema():
    """Build tool schema dynamically from registry.

    The agent list is NOT embedded here — it's injected once by
    ``create_mcp_server()`` when any agent tool is present.
    """
    from . import build_agent_list_block

    _, agent_names = build_agent_list_block()

    description = """Invoke a single specialized agent to handle a task.

IMPORTANT: This tool is for PARALLEL/INDEPENDENT agent invocations. If you need to run
multiple agents where each depends on the previous result, use invoke_agent_chain instead.

Invocation modes for agent-to-agent work; these do not directly deliver raw agent output to the user:
- foreground: Wait for result (blocking). Use for quick tasks or when you need the response now.
- ping: Run async. YOU — the agent calling this tool — get pinged back when the invoked
  agent finishes, so you can continue the thread. ("Ping me" means ping YOU, the caller,
  not the user.) Use for longer tasks where you want to continue working in parallel.
- trust: Fire and forget. No ping-back to you. Use when you trust the agent to do the
  right thing and don't need to react to the result yourself.

When to use which mode:
- Use foreground for quick lookups, simple code fixes, or when you need the result immediately
- Use ping for research tasks, code refactoring, or anything that might take a while AND
  you want to be re-invoked with the result so you can act on it
- Use trust for maintenance tasks, background processing, or when the work itself IS the
  result (no follow-up needed from you)

Note: For truly invisible (silent) execution where the user does NOT see the task, use schedule_agent
with silent=true instead.

THREADED CONVERSATIONS (optional):
Every response includes a `[conversation_id: <uuid>]` footer. To continue a prior
thread — for example, to answer a clarifying question the invoked agent asked —
pass that ID back as the `conversation_id` param. The invoked agent will see the
full prior history of the thread before responding.

- If the thread is currently being processed by another in-flight invocation,
  your call is rejected with a "thread locked" error — retry once it completes.
- Any agent with a `conversation_id` may invoke on that thread when idle;
  threads are NOT restricted to a single pair of agents. Another agent can join
  a thread to contribute context.
- Omit `conversation_id` to start a new thread; its ID is returned in the footer.
- Use list_agent_conversations / read_agent_conversation / delete_agent_conversation
  to discover and manage threads.

Use case examples:
- Launch multiple independent research tasks in parallel with separate invoke_agent calls
- Quick one-off agent tasks that don't depend on other agents
- Multi-turn back-and-forth with one agent (pass conversation_id on follow-ups)"""

    schema = {
        "type": "object",
        "properties": {
            "agent": {
                "type": "string",
                "enum": agent_names,
                "description": "Agent to invoke"
            },
            "prompt": {
                "type": "string",
                "description": "Task description for the agent. Be descriptive about goals, not prescriptive about implementation."
            },
            "mode": {
                "type": "string",
                "enum": ["foreground", "ping", "trust"],
                "description": "Invocation mode: foreground (wait for result), ping (async — YOU, the caller, get pinged back when done so you can continue the thread), trust (fire and forget, no ping-back). Default: foreground",
                "default": "foreground"
            },
            "model_override": {
                "type": "string",
                "enum": ["sonnet", "opus", "haiku"],
                "description": "Override the agent's default model (optional)"
            },
            "project": {
                "description": "Optional: Target project for output routing. When specified, agent output is tagged with YAML frontmatter for automatic routing to the project's _status.md during morning sync. String for single project, array for multi-project.",
                "oneOf": [
                    {"type": "string"},
                    {"type": "array", "items": {"type": "string"}}
                ]
            },
            "project_output_contract": {
                "type": "string",
                "enum": [
                    PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS,
                    PROJECT_OUTPUT_CONTRACT_NONE,
                ],
                "default": PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS,
                "description": "Controls prompt-level project output instructions. 'agent_outputs' preserves the legacy [PROJECT METADATA] / 00_Inbox/agent_outputs footer when project is set. 'none' keeps project metadata for routing/thread/log purposes but suppresses that footer."
            },
            "conversation_id": {
                "type": "string",
                "description": "Optional. Continue a prior agent-to-agent thread. The invoked agent will see the full prior history of the thread before responding. If the thread is currently being processed, the call is rejected — retry once it completes. Any agent with the ID may invoke on an idle thread. Omit to start a new thread; its ID is returned in the response footer."
            },
            "worktree_branch": {
                "type": "string",
                "description": "Optional Patch-only Phase 1A coder-worktree request branch. Requires SECOND_BRAIN_CODER_WORKTREES and does not change cwd yet."
            },
            "worktree_slug": {
                "type": "string",
                "description": "Optional Patch-only Phase 1A coder-worktree request slug used to derive the worktree path. Requires worktree_branch."
            },
            "worktree_base_ref": {
                "type": "string",
                "description": "Optional Patch-only Phase 1A base ref for coder worktree metadata. Defaults to main when omitted."
            }

        },
        "required": ["agent", "prompt"]
    }

    return description, schema


_INVOKE_DESCRIPTION, _INVOKE_SCHEMA = _build_invoke_tool_schema()


WORKTREE_REQUEST_FIELDS = ("worktree_branch", "worktree_slug", "worktree_base_ref")


def _coder_worktrees_enabled() -> bool:
    value = os.environ.get("SECOND_BRAIN_CODER_WORKTREES", "")
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _extract_worktree_request(args: Dict[str, Any]) -> Dict[str, Optional[str]]:
    return {
        field: args.get(field)
        for field in WORKTREE_REQUEST_FIELDS
        if field in args and args.get(field) is not None
    }


def _validate_worktree_request_for_mcp(
    *,
    caller_agent: str,
    agent_name: str,
    request: Dict[str, Optional[str]],
) -> Dict[str, str]:
    if not request:
        return {}
    if (caller_agent or "").strip().lower() != "patch":
        raise ValueError("coder worktree requests are Patch-only")
    if not _coder_worktrees_enabled():
        raise ValueError(
            "coder worktree requests are disabled; set "
            "SECOND_BRAIN_CODER_WORKTREES=1 to enable Phase 1A plumbing"
        )
    branch = request.get("worktree_branch")
    slug = request.get("worktree_slug")
    if not branch or not slug:
        raise ValueError("worktree_branch and worktree_slug are required together")

    from worktree_manager import metadata_for_request

    return metadata_for_request(
        agent_name,
        branch,
        slug,
        base_ref=request.get("worktree_base_ref") or "main",
    )

def _append_conversation_footer(text: str, conversation_id: Optional[str]) -> str:
    """Append the machine-readable conversation footer so callers can resume.

    Footer format: ``---\\n[conversation_id: <uuid>]`` — appears at the very
    end of the agent's text response. MCP tool responses are text-only, so a
    parseable sentinel beats trying to smuggle structured fields.
    """
    if not conversation_id:
        return text
    text = (text or "").rstrip()
    return f"{text}\n\n---\n[conversation_id: {conversation_id}]"


def _running_under_codex_stdio_bridge() -> bool:
    """Best-effort detection for the Codex stdio MCP bridge process."""
    argv = " ".join(sys.argv)
    return "mcp_tools/stdio_server.py" in argv or argv.endswith("stdio_server.py")


def _running_in_backend_process() -> bool:
    """Return True only in the long-lived backend process that owns ping tasks."""
    backend_pid = os.environ.get("SECOND_BRAIN_BACKEND_PID")
    if not backend_pid:
        return False
    try:
        return int(backend_pid) == os.getpid()
    except ValueError:
        return False


def _should_relay_ping_to_backend() -> bool:
    """Ping acks are trustworthy only when launch is owned by the backend loop."""
    return not _running_in_backend_process()


def _get_internal_agent_invoke_token() -> Optional[str]:
    # The backend-published token file is the canonical cross-process handoff.
    # Codex may scrub MCP bridge env, and inherited env can be stale across
    # backend restarts, so only fall back to env when the file is unavailable.
    try:
        token = INTERNAL_AGENT_INVOKE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        token = None
    if token:
        return token
    return os.environ.get("SECOND_BRAIN_INTERNAL_AGENT_TOKEN") or None


def _post_internal_agent_invoke(payload: Dict[str, Any]) -> Dict[str, Any]:
    token = _get_internal_agent_invoke_token()
    if not token:
        return {"error": "internal ping relay token unavailable"}

    base_url = os.environ.get("SECOND_BRAIN_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
    url = base_url.rstrip("/") + "/api/internal/agent-invoke"
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "X-Second-Brain-Internal-Token": token,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(detail)
            detail = data.get("detail", detail)
        except Exception:
            pass
        return {"error": f"internal ping relay failed ({e.code}): {detail}"}
    except Exception as e:
        return {"error": f"internal ping relay failed: {e}"}


async def _relay_ping_to_backend(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_post_internal_agent_invoke, payload)


@register_tool("agents")
@tool(name="invoke_agent", description=_INVOKE_DESCRIPTION, input_schema=_INVOKE_SCHEMA)
async def invoke_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a specialized agent."""
    try:
        agent_name = args.get("agent", "")
        prompt = args.get("prompt", "")
        mode = args.get("mode", "foreground")
        model_override = args.get("model_override")
        project = args.get("project")
        project_output_contract = args.get(
            "project_output_contract",
            PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS,
        )
        conversation_id = args.get("conversation_id")

        if not agent_name:
            return {"content": [{"type": "text", "text": "Error: agent is required"}], "is_error": True}

        if not prompt:
            return {"content": [{"type": "text", "text": "Error: prompt is required"}], "is_error": True}

        if project_output_contract not in PROJECT_OUTPUT_CONTRACT_VALUES:
            valid_values = ", ".join(sorted(PROJECT_OUTPUT_CONTRACT_VALUES))
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: project_output_contract must be one of: "
                        f"{valid_values}"
                    ),
                }],
                "is_error": True,
            }

        # Get source chat ID: injected by MCP wrapper (concurrent-safe) or env var (fallback)
        source_chat_id = args.pop("_source_chat_id", None) or os.environ.get("CURRENT_CHAT_ID")

        # Get calling agent name — injected by MCP wrapper for agent-owned MCP servers.
        # Falls back to "user" when the tool is called from a user chat (primary_claude
        # has no agent_name injected).
        caller_agent = args.pop("_agent_name", None) or "user"

        try:
            worktree_metadata = _validate_worktree_request_for_mcp(
                caller_agent=caller_agent,
                agent_name=agent_name,
                request=_extract_worktree_request(args),
            )
        except Exception as e:
            return {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "is_error": True,
            }

        if mode == "ping" and _should_relay_ping_to_backend():
            # Detached ping tasks created in caller-owned Codex/MCP processes can
            # be cancelled when the caller exits after receiving the ack. Hand
            # launch to the long-lived backend instead; if relay is unavailable,
            # fail visibly before any thread is created or locked locally.
            result = await _relay_ping_to_backend({
                "agent": agent_name,
                "prompt": prompt,
                "mode": mode,
                "source_chat_id": source_chat_id,
                "model_override": model_override,
                "project": project,
                "project_output_contract": project_output_contract,
                "conversation_id": conversation_id,
                "caller_agent": caller_agent,
                **worktree_metadata,
            })
        else:
            from runner import invoke_agent as _invoke_agent
            result = await _invoke_agent(
                name=agent_name,
                prompt=prompt,
                mode=mode,
                source_chat_id=source_chat_id,
                model_override=model_override,
                project=project,
                project_output_contract=project_output_contract,
                conversation_id=conversation_id,
                caller_agent=caller_agent,
                **worktree_metadata,
            )

        # Handle different result types based on mode
        if mode == "foreground":
            # AgentResult object
            if hasattr(result, "status"):
                conv_id = getattr(result, "conversation_id", None)
                if result.status == "success":
                    body = result.transcript or result.response
                    return {"content": [{"type": "text", "text": _append_conversation_footer(body, conv_id)}]}
                else:
                    error_msg = f"Agent {agent_name} failed: {result.error or result.status}"
                    return {
                        "content": [{"type": "text", "text": _append_conversation_footer(error_msg, conv_id)}],
                        "is_error": True,
                    }
            else:
                # Dict result (error case)
                if "error" in result:
                    conv_id = result.get("conversation_id")
                    return {
                        "content": [{"type": "text", "text": _append_conversation_footer(result["error"], conv_id)}],
                        "is_error": True,
                    }
                return {"content": [{"type": "text", "text": str(result)}]}
        else:
            # Acknowledgment dict for ping/trust modes
            if "error" in result:
                conv_id = result.get("conversation_id")
                return {
                    "content": [{"type": "text", "text": _append_conversation_footer(result["error"], conv_id)}],
                    "is_error": True,
                }
            message = result.get("message", f"Agent {agent_name} is working on your task.")
            conv_id = result.get("conversation_id")
            return {"content": [{"type": "text", "text": _append_conversation_footer(message, conv_id)}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error invoking agent: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


def _build_chain_tool_schema():
    """Build tool schema for invoke_agent_chain.

    The agent list is NOT embedded here — it's injected once by
    ``create_mcp_server()`` when any agent tool is present.
    """
    from . import build_agent_list_block

    _, agent_names = build_agent_list_block()

    description = """Run multiple agents in sequence (serial execution) with dependency support.

IMPORTANT: Use this for SEQUENTIAL/DEPENDENT tasks where agents must run one after another.
For parallel/independent tasks, use invoke_agent instead.

Failure handling:
- alert_and_stop (default): Stop the chain and notify on any agent failure
- skip_and_continue: Log the failure, continue to the next agent in the chain

Output:
- Collects all agent outputs and sends ONE notification when the chain completes
- If summarize=true, outputs are summarized before notification

Use case examples:
- Research → Write → Review workflows
- Multi-step code changes where each step depends on the previous
- Sequential data processing pipelines"""

    schema = {
        "type": "object",
        "properties": {
            "chain": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": agent_names,
                            "description": "Agent to invoke"
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Task description for the agent"
                        }
                    },
                    "required": ["agent", "prompt"]
                },
                "minItems": 1,
                "description": "List of agents to run in order, each with agent name and prompt"
            },
            "on_failure": {
                "type": "string",
                "enum": ["alert_and_stop", "skip_and_continue"],
                "default": "alert_and_stop",
                "description": "How to handle agent failures: alert_and_stop (default) stops chain on failure, skip_and_continue logs and moves to next agent"
            },
            "summarize": {
                "type": "boolean",
                "default": False,
                "description": "If true, summarize all outputs before notification (default: false)"
            }
        },
        "required": ["chain"]
    }

    return description, schema


_CHAIN_DESCRIPTION, _CHAIN_SCHEMA = _build_chain_tool_schema()


@register_tool("agents")
@tool(name="invoke_agent_chain", description=_CHAIN_DESCRIPTION, input_schema=_CHAIN_SCHEMA)
async def invoke_agent_chain(args: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke multiple agents in sequence (serial execution)."""
    try:
        from runner import invoke_agent_chain as _invoke_chain

        chain = args.get("chain", [])
        on_failure = args.get("on_failure", "alert_and_stop")
        summarize = args.get("summarize", False)

        if not chain:
            return {"content": [{"type": "text", "text": "Error: chain is required and must not be empty"}], "is_error": True}

        # Get source chat ID: injected by MCP wrapper (concurrent-safe) or env var (fallback)
        source_chat_id = args.pop("_source_chat_id", None) or os.environ.get("CURRENT_CHAT_ID")

        if not source_chat_id:
            return {"content": [{"type": "text", "text": "Error: source_chat_id required for chain notifications"}], "is_error": True}

        # Delegate to runner (same pattern as ping mode)
        result = await _invoke_chain(
            chain=chain,
            on_failure=on_failure,
            summarize=summarize,
            source_chat_id=source_chat_id,
        )

        if "error" in result:
            return {"content": [{"type": "text", "text": result["error"]}], "is_error": True}

        return {"content": [{"type": "text", "text": result["message"]}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error starting agent chain: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


# =============================================================================
# resume_agent_chain — Resume a failed/stopped chain from checkpoint
# =============================================================================

@register_tool("agents")
@tool(
    name="resume_agent_chain",
    description="""Resume a previously failed or stopped agent chain from its last checkpoint.

When an agent chain fails mid-way, its state is automatically checkpointed. Use this tool
to resume from the last successful step instead of re-running the entire chain.

Use list_chain_checkpoints to find available chain IDs.""",
    input_schema={
        "type": "object",
        "properties": {
            "chain_id": {
                "type": "string",
                "description": "The chain ID to resume (returned by invoke_agent_chain or from list_chain_checkpoints)"
            }
        },
        "required": ["chain_id"]
    }
)
async def resume_agent_chain(args: Dict[str, Any]) -> Dict[str, Any]:
    """Resume a failed/stopped agent chain from checkpoint."""
    try:
        from runner import resume_agent_chain as _resume_chain

        chain_id = args.get("chain_id", "")
        if not chain_id:
            return {"content": [{"type": "text", "text": "Error: chain_id is required"}], "is_error": True}

        # Get source chat ID
        source_chat_id = args.pop("_source_chat_id", None) or os.environ.get("CURRENT_CHAT_ID")

        result = await _resume_chain(
            chain_id=chain_id,
            source_chat_id=source_chat_id,
        )

        if "error" in result:
            return {"content": [{"type": "text", "text": result["error"]}], "is_error": True}

        return {"content": [{"type": "text", "text": result["message"]}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error resuming chain: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


# =============================================================================
# list_chain_checkpoints — View resumable chains
# =============================================================================

@register_tool("agents")
@tool(
    name="list_chain_checkpoints",
    description="""List all agent chain checkpoints (resumable chains).

Shows chains that were interrupted, failed, or completed, with their status and progress.
Use the chain_id from the results with resume_agent_chain to resume a failed chain.""",
    input_schema={
        "type": "object",
        "properties": {},
    }
)
async def list_chain_checkpoints_tool(args: Dict[str, Any]) -> Dict[str, Any]:
    """List all chain checkpoints."""
    try:
        from runner import list_chain_checkpoints as _list_checkpoints

        checkpoints = _list_checkpoints()

        if not checkpoints:
            return {"content": [{"type": "text", "text": "No chain checkpoints found."}]}

        lines = [f"## Chain Checkpoints ({len(checkpoints)} found)\n"]
        for cp in checkpoints:
            chain_str = " → ".join(cp["agents"])
            status_icon = {"running": "🔄", "completed": "✅", "failed": "❌", "stopped": "⏹️"}.get(cp["status"], "❓")
            lines.append(f"### {status_icon} {cp['chain_id']}")
            lines.append(f"- **Status**: {cp['status']}")
            lines.append(f"- **Progress**: {cp['completed_steps']}/{cp['total_steps']} steps completed")
            lines.append(f"- **Chain**: {chain_str}")
            lines.append(f"- **Created**: {cp['created_at']}")
            lines.append(f"- **Updated**: {cp['updated_at']}")
            if cp["status"] in ("failed", "stopped"):
                lines.append(f"- **Resume**: Use `resume_agent_chain` with chain_id `{cp['chain_id']}`")
            lines.append("")

        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error listing checkpoints: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


# =============================================================================
# invoke_agent_parallel — Run multiple agents concurrently
# =============================================================================

def _build_parallel_tool_schema():
    """Build tool schema for invoke_agent_parallel."""
    from . import build_agent_list_block

    _, agent_names = build_agent_list_block()

    description = """Run multiple agents in parallel and return all results.

All agents run concurrently as foreground tasks. One failure doesn't cancel the others.
Use this instead of multiple invoke_agent calls when you need results from several
agents at once (e.g., fanning out information gatherers during research).

Returns all results in a single response with truncated prompts so you can match
answers to your original questions."""

    schema = {
        "type": "object",
        "properties": {
            "agents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent": {
                            "type": "string",
                            "enum": agent_names,
                            "description": "Agent to invoke"
                        },
                        "prompt": {
                            "type": "string",
                            "description": "Task description for the agent"
                        },
                        "model_override": {
                            "type": "string",
                            "enum": ["sonnet", "opus", "haiku"],
                            "description": "Override the agent's default model (optional)"
                        }
                    },
                    "required": ["agent", "prompt"]
                },
                "minItems": 1,
                "maxItems": 10,
                "description": "Array of agent invocations to run concurrently"
            }
        },
        "required": ["agents"]
    }

    return description, schema


_PARALLEL_DESCRIPTION, _PARALLEL_SCHEMA = _build_parallel_tool_schema()


@register_tool("agents")
@tool(name="invoke_agent_parallel", description=_PARALLEL_DESCRIPTION, input_schema=_PARALLEL_SCHEMA)
async def invoke_agent_parallel(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run multiple agents in parallel and return all results."""
    import logging
    from runner import invoke_agent as _invoke_agent

    logger = logging.getLogger("agents.parallel")

    try:
        invocations = args.get("agents", [])
        if not invocations:
            return {"content": [{"type": "text", "text": "Error: agents array is required and must not be empty"}], "is_error": True}

        # Get source chat ID: injected by MCP wrapper (concurrent-safe) or env var (fallback)
        source_chat_id = args.pop("_source_chat_id", None) or os.environ.get("CURRENT_CHAT_ID")
        # Caller identity for thread authorship.
        caller_agent = args.pop("_agent_name", None) or "user"

        # Hardcoded semaphore for system safety — not a user parameter
        semaphore = asyncio.Semaphore(5)

        # Per-agent timeout: 4 hours (agents do advanced multi-step workflows)
        AGENT_TIMEOUT = 14400

        total_start = time.monotonic()

        async def _run_one(idx: int, inv: Dict[str, str]):
            """Run a single agent with semaphore + timeout."""
            agent_name = inv["agent"]
            prompt = inv["prompt"]
            model_override = inv.get("model_override")
            start = time.monotonic()

            async with semaphore:
                try:
                    result = await asyncio.wait_for(
                        _invoke_agent(
                            name=agent_name,
                            prompt=prompt,
                            mode="foreground",
                            source_chat_id=source_chat_id,
                            model_override=model_override,
                            caller_agent=caller_agent,
                        ),
                        timeout=AGENT_TIMEOUT,
                    )

                    duration = time.monotonic() - start

                    # Extract response + conversation_id from AgentResult or dict
                    if hasattr(result, "status"):
                        conv_id = getattr(result, "conversation_id", None)
                        if result.status == "success":
                            return {
                                "idx": idx, "status": "success",
                                "response": result.transcript or result.response,
                                "duration": duration, "conversation_id": conv_id,
                            }
                        else:
                            error_msg = result.error or result.status
                            return {
                                "idx": idx, "status": "error", "error": error_msg,
                                "duration": duration, "conversation_id": conv_id,
                            }
                    elif isinstance(result, dict) and "error" in result:
                        return {
                            "idx": idx, "status": "error", "error": result["error"],
                            "duration": duration,
                            "conversation_id": result.get("conversation_id"),
                        }
                    else:
                        return {"idx": idx, "status": "success", "response": str(result), "duration": duration}

                except asyncio.TimeoutError:
                    duration = time.monotonic() - start
                    return {"idx": idx, "status": "error", "error": f"Agent timed out after {AGENT_TIMEOUT}s", "duration": duration}
                except Exception as e:
                    duration = time.monotonic() - start
                    return {"idx": idx, "status": "error", "error": str(e), "duration": duration}

        # Launch all agents concurrently
        tasks = [_run_one(i, inv) for i, inv in enumerate(invocations)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        total_duration = time.monotonic() - total_start

        # Handle any unexpected gather exceptions
        final_results = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                final_results.append({"idx": i, "status": "error", "error": str(r), "duration": 0})
            else:
                final_results.append(r)

        # Sort by original index to preserve order
        final_results.sort(key=lambda r: r["idx"])

        formatted = _format_parallel_results(final_results, invocations, total_duration)

        logger.info(f"Parallel invocation complete: {len(invocations)} agents, {total_duration:.1f}s total")

        return {"content": [{"type": "text", "text": formatted}]}

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error in parallel invocation: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


def _format_parallel_results(
    results: List[Dict],
    invocations: List[Dict],
    total_duration: float,
) -> str:
    """Format parallel results for return to the calling agent.

    Each result block includes the agent name, a truncated view of the original
    prompt (first 120 chars), duration, and the response or error.
    """
    succeeded = sum(1 for r in results if r["status"] == "success")
    total = len(results)

    # Format total duration
    total_fmt = _fmt_duration(total_duration)

    parts = [f"## Parallel Results ({succeeded}/{total} succeeded, {total_fmt} total)", ""]

    for i, r in enumerate(results):
        inv = invocations[r["idx"]]
        agent_name = inv["agent"]
        prompt_text = inv["prompt"]
        duration_fmt = _fmt_duration(r["duration"])

        # Truncate prompt to 120 chars
        prompt_preview = prompt_text[:120] + "..." if len(prompt_text) > 120 else prompt_text

        status_marker = "" if r["status"] == "success" else " ❌ FAILED"

        parts.append("---")
        parts.append(f"### Result {i + 1}: {agent_name} ({duration_fmt}){status_marker}")
        parts.append(f"> **Prompt**: {prompt_preview}")
        conv_id = r.get("conversation_id")
        if conv_id:
            parts.append(f"> **conversation_id**: `{conv_id}`")
        parts.append("")

        if r["status"] == "success":
            parts.append(r["response"])
        else:
            parts.append(f"Error: {r['error']}")

        parts.append("")

    return "\n".join(parts)


def _fmt_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


# =============================================================================
# Agent-to-Agent Conversation tools
# =============================================================================

def _fmt_ago(ts: Optional[float]) -> str:
    """Format a UNIX timestamp as a human-readable relative time."""
    if not ts:
        return "unknown"
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)}m ago"
    if delta < 86400:
        return f"{int(delta // 3600)}h ago"
    return f"{int(delta // 86400)}d ago"


def _format_conversation_summary(summary: Dict[str, Any]) -> str:
    """Render one conversation summary as markdown."""
    title = summary.get("title") or "(untitled thread)"
    conv_id = summary.get("conversation_id")
    initiator = summary.get("initiator") or "unknown"
    participants = summary.get("participants") or []
    participants_str = ", ".join(participants) if participants else "(none)"
    msg_count = summary.get("message_count", 0)
    last = _fmt_ago(summary.get("last_message_at"))
    locked = summary.get("locked")
    lock_marker = f" 🔒 (held by {summary.get('locked_by')})" if locked else ""

    lines = [
        f"### \"{title}\"  [{conv_id}]{lock_marker}",
        f"- Started by **{initiator}**, participants: {participants_str}",
        f"- {msg_count} messages, last activity {last}",
    ]
    return "\n".join(lines)


@register_tool("agents")
@tool(
    name="list_agent_conversations",
    description="""List agent-to-agent conversation threads.

By default, returns threads YOU (the calling agent) have participated in, sorted by
most recent activity. You can discover other agents' threads too:

- `agent="<name>"`: list that agent's threads instead of your own
- `other_participant="<name>"`: filter to threads involving a specific other agent
  (combines with `agent` if both are provided)

All agent conversations are discoverable by all agents — we trust each other.
Use this to find threads worth re-opening or reviewing, or to see what other
agents are working on together.

Returns a markdown list of threads with titles, participants, message counts,
and last-activity times. Use `read_agent_conversation` to view a thread's full
contents, or pass the ID as `conversation_id` to `invoke_agent` to continue it.""",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max threads to return (default 20, max 100)",
                "default": 20,
                "minimum": 1,
                "maximum": 100,
            },
            "agent": {
                "type": "string",
                "description": "Whose conversations to list. Defaults to the calling agent.",
            },
            "other_participant": {
                "type": "string",
                "description": "Filter to threads also including this agent.",
            },
        },
    },
)
async def list_agent_conversations(args: Dict[str, Any]) -> Dict[str, Any]:
    """List agent-to-agent conversation threads."""
    try:
        from agent_conversation_manager import get_manager
    except ImportError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}

    caller = args.pop("_agent_name", None) or "user"
    limit = int(args.get("limit") or 20)
    agent_filter = args.get("agent") or caller
    other = args.get("other_participant")

    manager = get_manager()
    summaries = manager.list_for_agent(
        agent_name=agent_filter, limit=limit, other_participant=other
    )

    if not summaries:
        who = "you" if agent_filter == caller else agent_filter
        filter_str = f" with {other}" if other else ""
        return {"content": [{"type": "text", "text": f"No conversations found for {who}{filter_str}."}]}

    header_who = "you" if agent_filter == caller else agent_filter
    filter_str = f" (with {other})" if other else ""
    lines = [
        f"## Conversations where **{header_who}** has participated{filter_str} ({len(summaries)} shown)",
        "",
    ]
    for s in summaries:
        lines.append(_format_conversation_summary(s))
        lines.append("")

    return {"content": [{"type": "text", "text": "\n".join(lines).rstrip()}]}


@register_tool("agents")
@tool(
    name="read_agent_conversation",
    description="""Read the full contents of an agent-to-agent conversation thread.

Returns every message in the thread with sender, timestamp, and content.
Useful for catching up on a thread before deciding whether to continue it
(via `invoke_agent(..., conversation_id=...)`) or picking up context for a
related task.

Any agent can read any thread — threads are not access-controlled. Use
`list_agent_conversations` first if you don't know the ID.""",
    input_schema={
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "string",
                "description": "The conversation ID to read.",
            },
        },
        "required": ["conversation_id"],
    },
)
async def read_agent_conversation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Read the full contents of an agent-to-agent conversation thread."""
    try:
        from agent_conversation_manager import get_manager
    except ImportError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}

    # Not used for gating, but pop to keep args clean.
    args.pop("_agent_name", None)
    conv_id = args.get("conversation_id", "")
    if not conv_id:
        return {"content": [{"type": "text", "text": "Error: conversation_id is required"}], "is_error": True}

    manager = get_manager()
    data = manager.load(conv_id)
    if not data:
        return {
            "content": [{"type": "text", "text": f"Conversation '{conv_id}' not found."}],
            "is_error": True,
        }

    from datetime import datetime

    title = data.get("title") or "(untitled thread)"
    initiator = data.get("initiator") or "unknown"
    participants = manager.get_participants(data)
    messages = data.get("messages") or []
    locked = data.get("lock")
    lock_marker = ""
    if locked:
        lock_marker = f"\n**🔒 Currently locked** by `{locked.get('locked_by')}` (lock age: {_fmt_ago(locked.get('locked_at'))})"

    lines = [
        f"## \"{title}\"",
        f"**ID**: `{conv_id}`",
        f"**Started by**: {initiator}",
        f"**Participants**: {', '.join(participants) if participants else '(none)'}",
        f"**Messages**: {len(messages)}",
    ]
    if lock_marker:
        lines.append(lock_marker)
    lines.append("")
    lines.append("---")

    for msg in messages:
        sender = msg.get("from", "unknown")
        created = msg.get("created_at")
        try:
            ts_str = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M:%S") if created else "?"
        except (OSError, ValueError, TypeError):
            ts_str = "?"
        mode = msg.get("mode")
        mode_tag = f" _(mode: {mode})_" if mode else ""
        lines.append("")
        lines.append(f"### **{sender}** — {ts_str}{mode_tag}")
        lines.append("")
        lines.append(msg.get("content", ""))

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


@register_tool("agents")
@tool(
    name="delete_agent_conversation",
    description="""Delete an agent-to-agent conversation thread.

Only agents who have participated in the thread (as initiator or author of at
least one message) can delete it. This is a soft safety rail — it's easy to
lose work by mass-deleting someone else's threads.

Use `list_agent_conversations` to find thread IDs.""",
    input_schema={
        "type": "object",
        "properties": {
            "conversation_id": {
                "type": "string",
                "description": "The conversation ID to delete.",
            },
        },
        "required": ["conversation_id"],
    },
)
async def delete_agent_conversation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete an agent-to-agent conversation thread."""
    try:
        from agent_conversation_manager import get_manager
    except ImportError as e:
        return {"content": [{"type": "text", "text": f"Error: {e}"}], "is_error": True}

    caller = args.pop("_agent_name", None) or "user"
    conv_id = args.get("conversation_id", "")
    if not conv_id:
        return {"content": [{"type": "text", "text": "Error: conversation_id is required"}], "is_error": True}

    manager = get_manager()
    try:
        ok = manager.delete(conv_id, requesting_agent=caller, require_participant=True)
    except PermissionError as e:
        return {"content": [{"type": "text", "text": f"Not permitted: {e}"}], "is_error": True}

    if not ok:
        return {
            "content": [{"type": "text", "text": f"Conversation '{conv_id}' not found."}],
            "is_error": True,
        }
    return {"content": [{"type": "text", "text": f"Deleted conversation '{conv_id}'."}]}
