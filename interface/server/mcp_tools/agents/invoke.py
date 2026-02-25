"""
Agent invocation tools.

Tools for invoking specialized agents:
- invoke_agent: Single agent invocation (parallel-friendly, independent tasks)
- invoke_agent_chain: Serial agent execution (dependent/sequential tasks)
- invoke_agent_parallel: Multiple agents concurrently, results returned together
"""

import asyncio
import os
import sys
import time
from typing import Any, Dict, List

from claude_agent_sdk import tool

from ..registry import register_tool

# Add agents directory to path
AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/agents"))
if AGENTS_DIR not in sys.path:
    sys.path.insert(0, AGENTS_DIR)


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

Invocation modes (all are non-silent — the user can see them):
- foreground: Wait for result (blocking). Use for quick tasks or when you need the response.
- ping: Run async, the user gets notified when done. Use for longer tasks where you want to continue working.
- trust: Fire and forget. Use for background tasks where you trust the agent to do the right thing.

When to use which mode:
- Use foreground for quick lookups, simple code fixes, or when you need the result immediately
- Use ping for research tasks, code refactoring, or anything that might take a while
- Use trust for maintenance tasks, background processing, or when the work itself IS the result

Note: For truly invisible (silent) execution where the user does NOT see the task, use schedule_agent
with silent=true instead.

Use case examples:
- Launch multiple independent research tasks in parallel with separate invoke_agent calls
- Quick one-off agent tasks that don't depend on other agents"""

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
                "description": "Invocation mode: foreground (wait), ping (notify when done), trust (fire and forget). Default: foreground",
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
            }
        },
        "required": ["agent", "prompt"]
    }

    return description, schema


_INVOKE_DESCRIPTION, _INVOKE_SCHEMA = _build_invoke_tool_schema()


@register_tool("agents")
@tool(name="invoke_agent", description=_INVOKE_DESCRIPTION, input_schema=_INVOKE_SCHEMA)
async def invoke_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke a specialized agent."""
    try:
        from runner import invoke_agent as _invoke_agent

        agent_name = args.get("agent", "")
        prompt = args.get("prompt", "")
        mode = args.get("mode", "foreground")
        model_override = args.get("model_override")
        project = args.get("project")

        if not agent_name:
            return {"content": [{"type": "text", "text": "Error: agent is required"}], "is_error": True}

        if not prompt:
            return {"content": [{"type": "text", "text": "Error: prompt is required"}], "is_error": True}

        # Get source chat ID: injected by MCP wrapper (concurrent-safe) or env var (fallback)
        source_chat_id = args.pop("_source_chat_id", None) or os.environ.get("CURRENT_CHAT_ID")

        result = await _invoke_agent(
            name=agent_name,
            prompt=prompt,
            mode=mode,
            source_chat_id=source_chat_id,
            model_override=model_override,
            project=project
        )

        # Handle different result types based on mode
        if mode == "foreground":
            # AgentResult object
            if hasattr(result, "status"):
                if result.status == "success":
                    return {"content": [{"type": "text", "text": result.transcript or result.response}]}
                else:
                    error_msg = f"Agent {agent_name} failed: {result.error or result.status}"
                    return {"content": [{"type": "text", "text": error_msg}], "is_error": True}
            else:
                # Dict result (error case)
                if "error" in result:
                    return {"content": [{"type": "text", "text": result["error"]}], "is_error": True}
                return {"content": [{"type": "text", "text": str(result)}]}
        else:
            # Acknowledgment dict for ping/trust modes
            message = result.get("message", f"Agent {agent_name} is working on your task.")
            return {"content": [{"type": "text", "text": message}]}

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

        # Hardcoded semaphore for system safety — not a user parameter
        semaphore = asyncio.Semaphore(5)

        # Per-agent timeout: 20 minutes (research/deep_think can legitimately take a while)
        AGENT_TIMEOUT = 1200

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
                        ),
                        timeout=AGENT_TIMEOUT,
                    )

                    duration = time.monotonic() - start

                    # Extract response from AgentResult or dict
                    if hasattr(result, "status"):
                        if result.status == "success":
                            return {"idx": idx, "status": "success", "response": result.transcript or result.response, "duration": duration}
                        else:
                            error_msg = result.error or result.status
                            return {"idx": idx, "status": "error", "error": error_msg, "duration": duration}
                    elif isinstance(result, dict) and "error" in result:
                        return {"idx": idx, "status": "error", "error": result["error"], "duration": duration}
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
