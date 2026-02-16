"""
Agent invocation tools.

Tools for invoking specialized agents:
- invoke_agent: Single agent invocation (parallel-friendly, independent tasks)
- invoke_agent_chain: Serial agent execution (dependent/sequential tasks)
"""

import os
import sys
from typing import Any, Dict, List

from claude_agent_sdk import tool

from ..registry import register_tool

# Add agents directory to path
AGENTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/agents"))
if AGENTS_DIR not in sys.path:
    sys.path.insert(0, AGENTS_DIR)


def _build_invoke_tool_schema():
    """Build tool schema dynamically from registry."""
    from registry import get_registry

    registry = get_registry()
    all_agents = registry.get_all_configs()
    all_background = registry.get_all_background_configs()
    combined = {**all_agents, **all_background}

    # Filter out hidden agents from the description (they remain invocable by name)
    visible = {k: v for k, v in combined.items() if not v.hidden}

    # Build description from visible agents only
    agent_lines = []
    for name, config in sorted(visible.items()):
        desc = config.description or "No description"
        agent_lines.append(f"- {name}: {desc}")

    agent_list = "\n".join(agent_lines)
    # Include ALL agent names in the enum so hidden agents are still invocable by name
    agent_names = list(combined.keys())

    description = f"""Invoke a single specialized agent to handle a task.

IMPORTANT: This tool is for PARALLEL/INDEPENDENT agent invocations. If you need to run
multiple agents where each depends on the previous result, use invoke_agent_chain instead.

Available agents:
{agent_list}

Invocation modes:
- foreground: Wait for result (blocking). Use for quick tasks or when you need the response.
- ping: Run async, notify when done. Use for longer tasks where you want to continue working.
- trust: Fire and forget. Use for background tasks where you trust the agent to do the right thing.

When to use which mode:
- Use foreground for quick lookups, simple code fixes, or when you need the result immediately
- Use ping for research tasks, code refactoring, or anything that might take a while
- Use trust for maintenance tasks, background processing, or when the work itself IS the result

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
                    return {"content": [{"type": "text", "text": result.response}]}
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
    """Build tool schema for invoke_agent_chain."""
    from registry import get_registry

    registry = get_registry()
    all_agents = registry.get_all_configs()
    all_background = registry.get_all_background_configs()
    combined = {**all_agents, **all_background}

    # Filter out hidden agents from the description (they remain invocable by name)
    visible = {k: v for k, v in combined.items() if not v.hidden}

    # Build agent list for description from visible agents only
    agent_lines = []
    for name, config in sorted(visible.items()):
        desc = config.description or "No description"
        agent_lines.append(f"- {name}: {desc}")

    agent_list = "\n".join(agent_lines)
    # Include ALL agent names in the enum so hidden agents are still invocable by name
    agent_names = list(combined.keys())

    description = f"""Run multiple agents in sequence (serial execution) with dependency support.

IMPORTANT: Use this for SEQUENTIAL/DEPENDENT tasks where agents must run one after another.
For parallel/independent tasks, use invoke_agent instead.

Available agents:
{agent_list}

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
    import asyncio
    from datetime import datetime
    from runner import invoke_agent as _invoke_agent
    from agent_notifications import get_notification_queue

    try:
        chain = args.get("chain", [])
        on_failure = args.get("on_failure", "alert_and_stop")
        summarize = args.get("summarize", False)

        if not chain:
            return {"content": [{"type": "text", "text": "Error: chain is required and must not be empty"}], "is_error": True}

        # Get source chat ID: injected by MCP wrapper (concurrent-safe) or env var (fallback)
        source_chat_id = args.pop("_source_chat_id", None) or os.environ.get("CURRENT_CHAT_ID")

        if not source_chat_id:
            return {"content": [{"type": "text", "text": "Error: source_chat_id required for chain notifications"}], "is_error": True}

        # Start the chain execution in the background
        asyncio.create_task(_run_agent_chain(
            chain=chain,
            on_failure=on_failure,
            summarize=summarize,
            source_chat_id=source_chat_id,
            invoked_at=datetime.utcnow()
        ))

        # Build acknowledgment message
        agent_names = [step["agent"] for step in chain]
        chain_str = " → ".join(agent_names)

        return {
            "content": [{
                "type": "text",
                "text": f"Agent chain started: {chain_str}\n\nYou'll be notified when the chain completes."
            }]
        }

    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Error starting agent chain: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }


async def _run_agent_chain(
    chain: List[Dict[str, str]],
    on_failure: str,
    summarize: bool,
    source_chat_id: str,
    invoked_at
) -> None:
    """
    Execute an agent chain sequentially and send notification on completion.

    Args:
        chain: List of {"agent": name, "prompt": task} dicts
        on_failure: "alert_and_stop" or "skip_and_continue"
        summarize: Whether to summarize outputs
        source_chat_id: Chat ID for notification
        invoked_at: When the chain was invoked
    """
    import logging
    from datetime import datetime
    from runner import invoke_agent as _invoke_agent
    from agent_notifications import get_notification_queue

    logger = logging.getLogger("agents.chain")
    logger.info(f"Starting agent chain with {len(chain)} agents")

    results = []  # List of (agent_name, status, response/error)
    chain_failed = False
    failed_agent = None

    for i, step in enumerate(chain):
        agent_name = step["agent"]
        prompt = step["prompt"]

        logger.info(f"Chain step {i+1}/{len(chain)}: Running agent '{agent_name}'")

        try:
            # Run agent in foreground mode (blocking)
            result = await _invoke_agent(
                name=agent_name,
                prompt=prompt,
                mode="foreground",
                source_chat_id=source_chat_id,
                model_override=None
            )

            # Check result
            if hasattr(result, "status"):
                if result.status == "success":
                    results.append((agent_name, "success", result.response))
                    logger.info(f"Chain step {i+1}: Agent '{agent_name}' succeeded")
                else:
                    error_msg = result.error or result.status
                    results.append((agent_name, "error", error_msg))
                    logger.warning(f"Chain step {i+1}: Agent '{agent_name}' failed: {error_msg}")

                    if on_failure == "alert_and_stop":
                        chain_failed = True
                        failed_agent = agent_name
                        break
            else:
                # Dict result (error case)
                if "error" in result:
                    results.append((agent_name, "error", result["error"]))
                    logger.warning(f"Chain step {i+1}: Agent '{agent_name}' failed: {result['error']}")

                    if on_failure == "alert_and_stop":
                        chain_failed = True
                        failed_agent = agent_name
                        break
                else:
                    results.append((agent_name, "success", str(result)))

        except Exception as e:
            logger.error(f"Chain step {i+1}: Agent '{agent_name}' exception: {e}")
            results.append((agent_name, "exception", str(e)))

            if on_failure == "alert_and_stop":
                chain_failed = True
                failed_agent = agent_name
                break

    # Build the notification response
    response = _format_chain_results(
        results=results,
        chain_failed=chain_failed,
        failed_agent=failed_agent,
        total_steps=len(chain),
        summarize=summarize
    )

    # Add to notification queue (same pattern as ping mode)
    queue = get_notification_queue()
    queue.add(
        agent="agent_chain",  # Use special agent name for chain notifications
        agent_response=response,
        source_chat_id=source_chat_id,
        invoked_at=invoked_at,
        completed_at=datetime.utcnow(),
    )

    logger.info(f"Agent chain completed: {len(results)}/{len(chain)} agents ran")


def _format_chain_results(
    results: List[tuple],
    chain_failed: bool,
    failed_agent: str,
    total_steps: int,
    summarize: bool
) -> str:
    """
    Format chain results for notification.

    Args:
        results: List of (agent_name, status, response/error) tuples
        chain_failed: Whether the chain was stopped due to failure
        failed_agent: Name of agent that caused failure (if any)
        total_steps: Total number of steps in the chain
        summarize: Whether to summarize outputs

    Returns:
        Formatted response string
    """
    parts = []

    # Header with status
    completed = len(results)
    successful = sum(1 for _, status, _ in results if status == "success")

    if chain_failed:
        parts.append(f"⚠️ **Agent Chain Stopped** ({completed}/{total_steps} steps completed, {successful} successful)")
        parts.append(f"Chain stopped at agent '{failed_agent}' due to failure.")
    else:
        if successful == completed:
            parts.append(f"✅ **Agent Chain Completed** ({completed}/{total_steps} steps, all successful)")
        else:
            parts.append(f"⚠️ **Agent Chain Completed with Errors** ({completed}/{total_steps} steps, {successful} successful)")

    parts.append("")

    # Results for each agent
    if summarize:
        # Summarized format
        parts.append("**Summary:**")
        for agent_name, status, response in results:
            if status == "success":
                # Truncate long responses for summary
                summary = response[:500] + "..." if len(response) > 500 else response
                parts.append(f"- **{agent_name}**: {summary}")
            else:
                parts.append(f"- **{agent_name}**: ❌ {response}")
    else:
        # Full format
        for agent_name, status, response in results:
            parts.append(f"---")
            parts.append(f"**Agent: {agent_name}**")
            if status == "success":
                parts.append(f"Status: ✅ Success")
                parts.append(f"\n{response}")
            else:
                parts.append(f"Status: ❌ Failed ({status})")
                parts.append(f"Error: {response}")
            parts.append("")

    return "\n".join(parts)
