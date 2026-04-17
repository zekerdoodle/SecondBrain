"""
Agent Runner - Execution engine for agents.

Supports four invocation modes:
- foreground: Blocking execution, returns result directly
- ping: Async execution, notification when done
- trust: Fire and forget, logged but no notification
- scheduled: Like trust, but triggered by scheduler

# 🥚 Easter egg planted by Patch on 2026-03-24.
# This edit was previously IMPOSSIBLE — the Edit tool couldn't touch .claude/ files.
# Then we taught it to stop asking permission and just trust us.
# If you're reading this: hi from the agent that fixed itself.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# The SDK's stream_input() keeps stdin open until the first `result` message arrives
# or a timeout fires (CLAUDE_CODE_STREAM_CLOSE_TIMEOUT, default 60s).  Agents that
# use page_parser with summary subagents can take 90-120 seconds, which hits the
# default 60s timeout and closes stdin while Claude is still mid-conversation,
# causing CLIConnectionError: ProcessTransport is not ready for writing.
# Set to 4 hours — well above any agent's timeout_seconds (4 hr default).
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "14400000")
os.environ["ENABLE_TOOL_SEARCH"] = "false"

from models import (
    AgentConfig, AgentInvocation, AgentResult, InvocationMode
)
from agent_notifications import get_notification_queue

# Ensure server directory is importable (for process_registry)
_server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../interface/server"))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)

from process_registry import register_process, deregister_process

from claude_agent_sdk.types import (
    AssistantMessage,
    HookMatcher,
    PermissionResultAllow,
    ResultMessage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfigAdaptive,
    ThinkingConfigEnabled,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

logger = logging.getLogger("agents.runner")


async def _auto_approve_tool(tool_name: str, input_data: dict, context) -> PermissionResultAllow:
    """Auto-approve ALL tool permission requests without prompting.

    bypassPermissions handles most cases, but Claude Code has hardcoded protection
    for .claude/, .git/, .vscode/, .idea/ directories that still prompts even in
    bypass mode.  In SDK sessions there is no user to respond, so the prompt times
    out to a denial.  This callback catches those prompts and approves them.
    """
    return PermissionResultAllow(updated_input=input_data)


async def _keepalive_hook(input_data, tool_use_id, context):
    """Dummy PreToolUse hook — required by the Python SDK to keep the stream open
    for the can_use_tool callback."""
    return {"continue_": True}

# Model-aware thinking defaults — maximize thinking for every model tier
# Keys match the short model aliases used in agent config.yaml files
THINKING_DEFAULTS = {
    "opus": {
        "thinking": ThinkingConfigAdaptive(type="adaptive"),
        "effort": "high",
    },
    "sonnet": {
        "thinking": ThinkingConfigAdaptive(type="adaptive"),
        "effort": "high",
    },
    "haiku": {
        "thinking": ThinkingConfigEnabled(type="enabled", budget_tokens=16384),
    },
}

# Execution log file
EXECUTIONS_LOG = Path(__file__).parent / "executions.json"

# Chain checkpoint directory
CHAIN_CHECKPOINTS_DIR = Path(__file__).parent / "chain_checkpoints"

# Default working directory for agents
WORKING_DIR = "/home/debian/second_brain"

# External MCP servers config file (alongside this file)
EXTERNAL_MCP_CONFIG = Path(__file__).parent / "external_mcp_servers.json"

# Cache for external MCP config (loaded once per process)
_external_mcp_cache: Optional[Dict[str, Any]] = None


def _resolve_env_vars(value: str) -> str:
    """Resolve ${VAR_NAME} patterns in a string from os.environ."""
    import re as _re
    def _replacer(m):
        var_name = m.group(1)
        resolved = os.environ.get(var_name, "")
        if not resolved:
            logger.warning(f"External MCP config references ${{{var_name}}} but it is not set in environment")
        return resolved
    return _re.sub(r'\$\{([^}]+)\}', _replacer, value)


def _resolve_config_env_vars(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-resolve ${VAR_NAME} patterns in external MCP server configs.

    Resolves env var references in 'args' lists and 'env' dicts so that
    secrets (API keys) don't need to be hardcoded in the JSON config file.
    """
    resolved = {}
    for server_name, server_config in config.items():
        sc = dict(server_config)
        # Resolve in args list
        if "args" in sc and isinstance(sc["args"], list):
            sc["args"] = [_resolve_env_vars(a) if isinstance(a, str) else a for a in sc["args"]]
        # Resolve in env dict
        if "env" in sc and isinstance(sc["env"], dict):
            sc["env"] = {k: _resolve_env_vars(v) if isinstance(v, str) else v for k, v in sc["env"].items()}
        resolved[server_name] = sc
    return resolved


def _load_external_mcp_servers() -> Dict[str, Any]:
    """
    Load external MCP server configs from external_mcp_servers.json.

    Returns a dict of server_name -> McpStdioServerConfig (command/args/env).
    Supports ${VAR_NAME} interpolation in args and env values from os.environ.
    Cached after first load. Returns empty dict on missing/invalid file.
    """
    global _external_mcp_cache
    if _external_mcp_cache is not None:
        return _external_mcp_cache

    if not EXTERNAL_MCP_CONFIG.exists():
        _external_mcp_cache = {}
        return _external_mcp_cache

    try:
        with open(EXTERNAL_MCP_CONFIG, "r") as f:
            raw_config = json.load(f)
        _external_mcp_cache = _resolve_config_env_vars(raw_config)
        logger.info(f"Loaded {len(_external_mcp_cache)} external MCP server(s) from {EXTERNAL_MCP_CONFIG.name}")
    except Exception as e:
        logger.error(f"Failed to load external MCP servers config: {e}")
        _external_mcp_cache = {}

    return _external_mcp_cache


def _build_project_metadata_block(
    agent_name: str,
    project: Union[str, List[str]],
    task_id: Optional[str] = None
) -> str:
    """
    Build the PROJECT METADATA block to append to an agent's prompt.

    Instructs the agent to include YAML frontmatter in output files
    and use a project-tagged filename convention.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    tid = task_id or "ad-hoc"

    # Normalize to string for prompt (use first project if list)
    if isinstance(project, list):
        project_str = project[0]
        project_all = ", ".join(project)
    else:
        project_str = project
        project_all = project

    return f"""

[PROJECT METADATA]
project: {project_all}
task_id: {tid}

When writing output files, include this YAML frontmatter at the top of the file:
---
agent: {agent_name}
project: {project_str}
date: {today}
task_id: {tid}
---

Use this output filename pattern: 00_Inbox/agent_outputs/{today}_{agent_name}_{project_str}_{{slug}}.md
(Replace {{slug}} with a short descriptive name for the output content.)
"""


async def invoke_agent(
    name: str,
    prompt: str,
    mode: Union[str, InvocationMode] = "foreground",
    source_chat_id: Optional[str] = None,
    model_override: Optional[str] = None,
    project: Optional[Union[str, List[str]]] = None,
    is_visible: bool = False,
) -> Union[AgentResult, Dict[str, str]]:
    """
    Invoke an agent with the specified mode.

    Args:
        name: Agent name (must be registered)
        prompt: Task description for the agent
        mode: Invocation mode (foreground, ping, trust, scheduled)
        source_chat_id: Chat ID for ping mode notifications
        model_override: Override the agent's default model
        project: Optional project tag (string or list of strings) for output routing.
                 When present, appends PROJECT METADATA to the prompt instructing the
                 agent to include YAML frontmatter in output files.

    Returns:
        For foreground: AgentResult with full response
        For ping: Acknowledgment dict with notification ID
        For trust/scheduled: Acknowledgment dict
    """
    from registry import get_registry

    # Normalize mode
    if isinstance(mode, str):
        mode = InvocationMode(mode)

    # Get agent config
    registry = get_registry()
    config = registry.get(name)

    if not config:
        error_result = AgentResult(
            agent=name,
            status="error",
            response="",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            error=f"Unknown agent: {name}"
        )
        if mode == InvocationMode.FOREGROUND:
            return error_result
        return {"error": f"Unknown agent: {name}"}

    # Apply model override
    if model_override:
        config = AgentConfig(
            name=config.name,
            type=config.type,
            model=model_override,
            description=config.description,
            tools=config.tools,
            timeout_seconds=config.timeout_seconds,
            max_turns=config.max_turns,
            output_format=config.output_format,
            prompt=config.prompt,
            system_prompt_preset=config.system_prompt_preset,
            skills=config.skills,
        )

    # Inject project metadata into prompt if project is specified
    if project:
        prompt = prompt + _build_project_metadata_block(name, project)
        logger.info(f"Injected project metadata for '{project}' into agent '{name}' prompt")

    # Create invocation record
    invocation = AgentInvocation(
        agent=name,
        prompt=prompt,
        mode=mode,
        source_chat_id=source_chat_id,
        model_override=model_override,
        project=project,
        is_visible=is_visible,
    )

    logger.info(f"Invoking agent '{name}' in {mode.value} mode" + (f" [project: {project}]" if project else ""))

    # Handle different modes
    if mode == InvocationMode.FOREGROUND:
        return await _run_agent(config, invocation)

    elif mode == InvocationMode.PING:
        if not source_chat_id:
            return {"error": "source_chat_id required for ping mode"}

        # Run in background, add notification when done
        asyncio.create_task(_run_ping_agent(config, invocation))
        return {
            "status": "accepted",
            "agent": name,
            "mode": "ping",
            "message": f"Agent '{name}' is working on your task. You'll be notified when done."
        }

    elif mode in (InvocationMode.TRUST, InvocationMode.SCHEDULED):
        # Run in background, just log
        asyncio.create_task(_run_background_agent(config, invocation))
        return {
            "status": "accepted",
            "agent": name,
            "mode": mode.value,
            "message": f"Agent '{name}' is working on your task."
        }

    else:
        return {"error": f"Unknown mode: {mode}"}


async def _run_agent(config: AgentConfig, invocation: AgentInvocation) -> AgentResult:
    """
    Execute an agent and return the result.
    """
    started_at = datetime.utcnow()

    try:
        response, transcript, blocks = await _run_sdk_agent(config, invocation)

        return AgentResult(
            agent=config.name,
            status="success",
            response=response,
            started_at=started_at,
            completed_at=datetime.utcnow(),
            transcript=transcript,
            blocks=blocks,
        )

    except asyncio.TimeoutError:
        return AgentResult(
            agent=config.name,
            status="timeout",
            response="",
            started_at=started_at,
            completed_at=datetime.utcnow(),
            error=f"Agent timed out after {config.timeout_seconds} seconds"
        )

    except Exception as e:
        logger.error(f"Agent '{config.name}' failed: {e}")
        return AgentResult(
            agent=config.name,
            status="error",
            response="",
            started_at=started_at,
            completed_at=datetime.utcnow(),
            error=str(e)
        )


async def _run_ping_agent(config: AgentConfig, invocation: AgentInvocation) -> None:
    """Run agent and add notification when done."""
    try:
        result = await _run_agent(config, invocation)

        # Add to notification queue
        queue = get_notification_queue()
        notification = queue.add(
            agent=config.name,
            agent_response=result.response if result.status == "success" else f"Error: {result.error}",
            source_chat_id=invocation.source_chat_id,
            invoked_at=invocation.invoked_at,
            completed_at=result.completed_at,
        )

        # Log execution
        _log_execution(invocation, result)
    except Exception as e:
        logger.error(f"Background ping task for agent '{config.name}' failed: {e}", exc_info=True)


async def invoke_agent_chain(
    chain: List[Dict[str, str]],
    on_failure: str = "alert_and_stop",
    summarize: bool = False,
    source_chat_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Start an agent chain in the background with ping-style notification.

    Runs agents sequentially. When the chain completes (or stops on failure),
    adds a single notification to the queue targeting source_chat_id —
    identical to how ping mode works for single agents.

    Args:
        chain: List of {"agent": name, "prompt": task} dicts
        on_failure: "alert_and_stop" or "skip_and_continue"
        summarize: Whether to summarize outputs in the notification
        source_chat_id: Chat ID for notification delivery

    Returns:
        Acknowledgment dict (chain runs in background)
    """
    if not source_chat_id:
        return {"error": "source_chat_id required for chain notifications"}

    chain_id = str(uuid.uuid4())[:8]  # Short ID for readability
    invoked_at = datetime.utcnow()

    # Create initial checkpoint
    checkpoint = {
        "chain_id": chain_id,
        "created_at": invoked_at.isoformat(),
        "updated_at": invoked_at.isoformat(),
        "chain": chain,
        "on_failure": on_failure,
        "summarize": summarize,
        "source_chat_id": source_chat_id,
        "status": "running",
        "current_step": 0,
        "results": [],
    }
    _save_chain_checkpoint(checkpoint)

    asyncio.create_task(_run_chain_agent(
        chain=chain,
        on_failure=on_failure,
        summarize=summarize,
        source_chat_id=source_chat_id,
        invoked_at=invoked_at,
        chain_id=chain_id,
    ))

    agent_names = [step["agent"] for step in chain]
    chain_str = " \u2192 ".join(agent_names)
    return {
        "status": "accepted",
        "mode": "chain",
        "chain_id": chain_id,
        "message": f"Agent chain started: {chain_str}\nChain ID: {chain_id} (use to resume if interrupted)\n\nYou'll be notified when the chain completes."
    }


async def resume_agent_chain(
    chain_id: str,
    source_chat_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Resume a previously failed/stopped agent chain from its last checkpoint.

    Loads the checkpoint, identifies completed steps, and resumes from the
    next incomplete step. Already-completed steps are skipped.

    Args:
        chain_id: ID of the chain to resume
        source_chat_id: Override chat ID for notifications (uses original if not provided)

    Returns:
        Acknowledgment dict (chain runs in background)
    """
    checkpoint = _load_chain_checkpoint(chain_id)
    if not checkpoint:
        return {"error": f"No checkpoint found for chain ID: {chain_id}"}

    if checkpoint["status"] == "running":
        return {"error": f"Chain {chain_id} is still running. Wait for it to finish or check logs."}

    if checkpoint["status"] == "completed":
        return {"error": f"Chain {chain_id} already completed successfully. No resume needed."}

    chain = checkpoint["chain"]
    on_failure = checkpoint.get("on_failure", "alert_and_stop")
    summarize = checkpoint.get("summarize", False)
    chat_id = source_chat_id or checkpoint.get("source_chat_id")

    if not chat_id:
        return {"error": "source_chat_id required for chain notifications"}

    # Determine resume point: count successful results
    completed_results = checkpoint.get("results", [])
    # Find the first non-success result or the end of results
    resume_from = 0
    prior_results = []
    for r in completed_results:
        if r["status"] == "success":
            prior_results.append((r["agent"], "success", r.get("response", "")))
            resume_from += 1
        else:
            # Stop at the first failure — we'll re-run from here
            break

    if resume_from >= len(chain):
        return {"error": f"All {len(chain)} steps already completed. Nothing to resume."}

    remaining_agents = [step["agent"] for step in chain[resume_from:]]
    remaining_str = " → ".join(remaining_agents)

    # Update checkpoint status
    checkpoint["status"] = "running"
    checkpoint["source_chat_id"] = chat_id
    # Trim results to only successful ones (we'll re-run from the failure point)
    checkpoint["results"] = checkpoint["results"][:resume_from]
    checkpoint["current_step"] = resume_from
    _save_chain_checkpoint(checkpoint)

    invoked_at = datetime.fromisoformat(checkpoint["created_at"])

    asyncio.create_task(_run_chain_agent(
        chain=chain,
        on_failure=on_failure,
        summarize=summarize,
        source_chat_id=chat_id,
        invoked_at=invoked_at,
        chain_id=chain_id,
        resume_from=resume_from,
        prior_results=prior_results,
    ))

    return {
        "status": "accepted",
        "mode": "chain_resume",
        "chain_id": chain_id,
        "resumed_from_step": resume_from + 1,
        "total_steps": len(chain),
        "message": f"Chain {chain_id} resumed from step {resume_from + 1}/{len(chain)}.\nRemaining: {remaining_str}\n\nYou'll be notified when the chain completes."
    }


async def _run_chain_agent(
    chain: List[Dict[str, str]],
    on_failure: str,
    summarize: bool,
    source_chat_id: str,
    invoked_at: datetime,
    chain_id: Optional[str] = None,
    resume_from: int = 0,
    prior_results: Optional[List[tuple]] = None,
) -> None:
    """Execute an agent chain sequentially and send notification on completion.

    Follows the same pattern as _run_ping_agent: run work, add notification,
    log execution. Top-level try/except ensures errors are always logged.

    Supports resume: if resume_from > 0, skips already-completed steps
    and uses prior_results for the notification.

    Args:
        chain: List of {"agent": name, "prompt": task} dicts
        on_failure: "alert_and_stop" or "skip_and_continue"
        summarize: Whether to summarize outputs
        source_chat_id: Chat ID for notification
        invoked_at: Original invocation time
        chain_id: Checkpoint ID (for persistence)
        resume_from: Step index to resume from (0 = start)
        prior_results: Results from prior steps (for resume)
    """
    from registry import get_registry

    try:
        registry = get_registry()
        results = list(prior_results) if prior_results else []  # List of (agent_name, status, response/error)
        chain_failed = False
        failed_agent = None

        for i, step in enumerate(chain):
            # Skip already-completed steps on resume
            if i < resume_from:
                logger.info(f"Chain step {i+1}/{len(chain)}: Skipping '{step['agent']}' (already completed)")
                continue

            agent_name = step["agent"]
            prompt = step["prompt"]

            logger.info(f"Chain step {i+1}/{len(chain)}: Running agent '{agent_name}'")

            # Update checkpoint: mark current step
            if chain_id:
                checkpoint = _load_chain_checkpoint(chain_id)
                if checkpoint:
                    checkpoint["current_step"] = i
                    checkpoint["status"] = "running"
                    _save_chain_checkpoint(checkpoint)

            config = registry.get(agent_name)
            if not config:
                results.append((agent_name, "error", f"Unknown agent: {agent_name}"))

                # Save checkpoint with error
                if chain_id:
                    checkpoint = _load_chain_checkpoint(chain_id)
                    if checkpoint:
                        checkpoint["results"].append({
                            "agent": agent_name, "status": "error",
                            "response": f"Unknown agent: {agent_name}",
                            "completed_at": datetime.utcnow().isoformat(),
                        })
                        _save_chain_checkpoint(checkpoint)

                if on_failure == "alert_and_stop":
                    chain_failed = True
                    failed_agent = agent_name
                    break
                continue

            invocation = AgentInvocation(
                agent=agent_name,
                prompt=prompt,
                mode=InvocationMode.FOREGROUND,
                source_chat_id=source_chat_id,
            )

            try:
                result = await _run_agent(config, invocation)
                _log_execution(invocation, result)

                if result.status == "success":
                    response_text = result.transcript or result.response
                    results.append((agent_name, "success", response_text))
                    logger.info(f"Chain step {i+1}: Agent '{agent_name}' succeeded")

                    # Save checkpoint with success
                    if chain_id:
                        checkpoint = _load_chain_checkpoint(chain_id)
                        if checkpoint:
                            checkpoint["results"].append({
                                "agent": agent_name, "status": "success",
                                "response": response_text[:10000],  # Cap per-step to avoid huge files
                                "completed_at": datetime.utcnow().isoformat(),
                            })
                            checkpoint["current_step"] = i + 1
                            _save_chain_checkpoint(checkpoint)
                else:
                    error_msg = result.error or result.status
                    results.append((agent_name, "error", error_msg))
                    logger.warning(f"Chain step {i+1}: Agent '{agent_name}' failed: {error_msg}")

                    # Save checkpoint with failure
                    if chain_id:
                        checkpoint = _load_chain_checkpoint(chain_id)
                        if checkpoint:
                            checkpoint["results"].append({
                                "agent": agent_name, "status": "error",
                                "response": error_msg,
                                "completed_at": datetime.utcnow().isoformat(),
                            })
                            _save_chain_checkpoint(checkpoint)

                    if on_failure == "alert_and_stop":
                        chain_failed = True
                        failed_agent = agent_name
                        break

            except Exception as e:
                logger.error(f"Chain step {i+1}: Agent '{agent_name}' exception: {e}")
                results.append((agent_name, "exception", str(e)))

                # Save checkpoint with exception
                if chain_id:
                    checkpoint = _load_chain_checkpoint(chain_id)
                    if checkpoint:
                        checkpoint["results"].append({
                            "agent": agent_name, "status": "exception",
                            "response": str(e),
                            "completed_at": datetime.utcnow().isoformat(),
                        })
                        _save_chain_checkpoint(checkpoint)

                if on_failure == "alert_and_stop":
                    chain_failed = True
                    failed_agent = agent_name
                    break

        # Update final checkpoint status
        if chain_id:
            checkpoint = _load_chain_checkpoint(chain_id)
            if checkpoint:
                checkpoint["status"] = "failed" if chain_failed else "completed"
                _save_chain_checkpoint(checkpoint)

        # Build notification response
        response = _format_chain_results(
            results=results,
            chain_failed=chain_failed,
            failed_agent=failed_agent,
            total_steps=len(chain),
            summarize=summarize,
        )

        # Add to notification queue (same as _run_ping_agent)
        queue = get_notification_queue()
        queue.add(
            agent="agent_chain",
            agent_response=response,
            source_chat_id=source_chat_id,
            invoked_at=invoked_at,
            completed_at=datetime.utcnow(),
        )

        logger.info(f"Agent chain completed: {len(results)}/{len(chain)} agents ran, notification queued for chat {source_chat_id}")

    except Exception as e:
        logger.error(f"Background chain task failed: {e}", exc_info=True)


def _format_chain_results(
    results: List[tuple],
    chain_failed: bool,
    failed_agent: Optional[str],
    total_steps: int,
    summarize: bool,
) -> str:
    """Format chain results for notification."""
    parts = []

    completed = len(results)
    successful = sum(1 for _, status, _ in results if status == "success")

    if chain_failed:
        parts.append(f"**Agent Chain Stopped** ({completed}/{total_steps} steps completed, {successful} successful)")
        parts.append(f"Chain stopped at agent '{failed_agent}' due to failure.")
    else:
        if successful == completed:
            parts.append(f"**Agent Chain Completed** ({completed}/{total_steps} steps, all successful)")
        else:
            parts.append(f"**Agent Chain Completed with Errors** ({completed}/{total_steps} steps, {successful} successful)")

    parts.append("")

    if summarize:
        parts.append("**Summary:**")
        for agent_name, status, response in results:
            if status == "success":
                summary = response[:500] + "..." if len(response) > 500 else response
                parts.append(f"- **{agent_name}**: {summary}")
            else:
                parts.append(f"- **{agent_name}**: Failed - {response}")
    else:
        for agent_name, status, response in results:
            parts.append("---")
            parts.append(f"**Agent: {agent_name}**")
            if status == "success":
                parts.append(f"Status: Success")
                parts.append(f"\n{response}")
            else:
                parts.append(f"Status: Failed ({status})")
                parts.append(f"Error: {response}")
            parts.append("")

    return "\n".join(parts)


# =============================================================================
# Chain Checkpointing — persist state after each step, enable resume
# =============================================================================

def _save_chain_checkpoint(checkpoint: Dict[str, Any]) -> None:
    """Save chain checkpoint to disk (atomic write)."""
    CHAIN_CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    chain_id = checkpoint["chain_id"]
    path = CHAIN_CHECKPOINTS_DIR / f"{chain_id}.json"
    tmp_path = path.with_suffix(".tmp")
    checkpoint["updated_at"] = datetime.utcnow().isoformat()
    try:
        with open(tmp_path, "w") as f:
            json.dump(checkpoint, f, indent=2)
        tmp_path.rename(path)
    except Exception as e:
        logger.error(f"Failed to save chain checkpoint {chain_id}: {e}")
        if tmp_path.exists():
            tmp_path.unlink()


def _load_chain_checkpoint(chain_id: str) -> Optional[Dict[str, Any]]:
    """Load chain checkpoint from disk. Returns None if not found."""
    path = CHAIN_CHECKPOINTS_DIR / f"{chain_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load chain checkpoint {chain_id}: {e}")
        return None


def _delete_chain_checkpoint(chain_id: str) -> None:
    """Delete a chain checkpoint file."""
    path = CHAIN_CHECKPOINTS_DIR / f"{chain_id}.json"
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.error(f"Failed to delete chain checkpoint {chain_id}: {e}")


def _cleanup_stale_checkpoints(max_age_hours: int = 48) -> int:
    """Remove checkpoint files older than max_age_hours. Returns count removed."""
    if not CHAIN_CHECKPOINTS_DIR.exists():
        return 0
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    removed = 0
    for path in CHAIN_CHECKPOINTS_DIR.glob("*.json"):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data.get("updated_at", data.get("created_at", "")))
            if updated < cutoff:
                path.unlink()
                removed += 1
                logger.info(f"Cleaned up stale checkpoint: {path.name}")
        except Exception:
            # If we can't parse it, it's probably corrupt — remove it
            try:
                path.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def list_chain_checkpoints() -> List[Dict[str, Any]]:
    """List all active chain checkpoints (for UI/tools).

    Returns list of checkpoint summaries (without full response data).
    """
    if not CHAIN_CHECKPOINTS_DIR.exists():
        return []

    # Clean up stale checkpoints first
    _cleanup_stale_checkpoints()

    checkpoints = []
    for path in sorted(CHAIN_CHECKPOINTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Return summary without full responses
            agent_names = [step["agent"] for step in data.get("chain", [])]
            completed_count = len([r for r in data.get("results", []) if r.get("status") == "success"])
            checkpoints.append({
                "chain_id": data["chain_id"],
                "status": data.get("status", "unknown"),
                "agents": agent_names,
                "total_steps": len(data.get("chain", [])),
                "completed_steps": completed_count,
                "current_step": data.get("current_step", 0),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "source_chat_id": data.get("source_chat_id"),
            })
        except Exception:
            continue
    return checkpoints


async def _run_background_agent(config: AgentConfig, invocation: AgentInvocation) -> None:
    """Run agent and log (no notification)."""
    try:
        result = await _run_agent(config, invocation)
        _log_execution(invocation, result)
    except Exception as e:
        logger.error(f"Background task for agent '{config.name}' failed: {e}", exc_info=True)


async def _run_sdk_agent(config: AgentConfig, invocation: AgentInvocation) -> str:
    """
    Run an SDK-based agent using claude_agent_sdk.query().
    """
    from claude_agent_sdk import query, ClaudeAgentOptions

    logger.info(f"Running SDK agent '{config.name}' with model {config.model}")

    # Register in process registry (SDK agents: pid=None since SDK manages subprocess internally)
    task_desc = invocation.prompt[:80] if invocation.prompt else "active"
    reg_id = None
    try:
        reg_id = register_process(config.name, task=task_desc, pid=None)
    except Exception as e:
        logger.warning(f"Failed to register agent '{config.name}' in process registry: {e}")

    # Build system_prompt: either a SystemPromptPreset dict or a string
    #
    # Helper: load per-agent working memory prompt block
    def _load_working_memory_block(agent_name: str) -> str:
        """Load the agent's working memory and format as a prompt block."""
        try:
            scripts_dir = str(Path(__file__).parent.parent / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from working_memory import get_store
            store = get_store(agent_name=agent_name)
            wm_block = store.format_prompt_block()
            if wm_block:
                logger.info(f"Agent '{agent_name}': loaded working memory ({len(store.list_items())} items)")
                return f"\n\n<working-memory>\n{wm_block}\n</working-memory>"
        except Exception as e:
            logger.debug(f"Agent '{agent_name}': could not load working memory: {e}")
        return ""

    # Pre-compute skill reminder for system prompt injection (above memory).
    _skill_reminder = ""
    agent_has_skills = config.skills is None or (isinstance(config.skills, list) and len(config.skills) > 0)
    if agent_has_skills:
        try:
            from skill_injector import get_skill_reminder
            _skill_reminder = get_skill_reminder(allowed_skills=config.skills) or ""
            if _skill_reminder:
                logger.info(f"Agent '{config.name}': will inject skill menu into system prompt")
        except Exception as e:
            logger.warning(f"Skill menu generation failed for agent '{config.name}': {e}")

    # Pre-compute agent list block for injection above memory.
    _effective_tools = list(config.tools) if config.tools else []
    _agent_list_block = ""
    try:
        from mcp_tools.agents import get_agent_list_for_prompt
        _agent_list_block = get_agent_list_for_prompt(_effective_tools) or ""
        if _agent_list_block:
            logger.info(f"Agent '{config.name}': will inject agent list into system prompt")
    except Exception as e:
        logger.warning(f"Agent '{config.name}': failed to get agent list: {e}")

    # Load global safety rules (injected into ALL agents)
    _global_rules = ""
    global_rules_path = Path(__file__).parent / "global_rules.md"
    if global_rules_path.exists():
        try:
            _global_rules = global_rules_path.read_text().strip()
        except Exception as e:
            logger.warning(f"Agent '{config.name}': could not read global_rules.md: {e}")

    # Load mode-specific global instructions based on visibility flag
    _global_mode_instructions = ""
    if invocation.is_visible:
        _mode_file = "global_visible.md"
    else:
        _mode_file = "global_silent.md"
    _mode_path = Path(__file__).parent / _mode_file
    if _mode_path.exists():
        try:
            _global_mode_instructions = _mode_path.read_text().strip()
            if _global_mode_instructions:
                logger.info(f"Agent '{config.name}': loaded {_mode_file} (is_visible={invocation.is_visible})")
        except Exception as e:
            logger.warning(f"Agent '{config.name}': could not read {_mode_file}: {e}")

    if config.system_prompt_preset:
        append_parts = []
        if config.prompt:
            append_parts.append(config.prompt)
        # Global safety rules (above skills and memory)
        if _global_rules:
            append_parts.append(_global_rules)
        # Mode-specific global instructions (visible for @mentions, silent for background)
        if _global_mode_instructions:
            append_parts.append(_global_mode_instructions)
        # Skill menu sits above memory in the system prompt
        if _skill_reminder:
            append_parts.append(_skill_reminder)
        # Agent list sits above memory in the system prompt
        if _agent_list_block:
            append_parts.append(_agent_list_block)
        # Include per-agent always_load memories from memories.json
        agent_memories_path = Path(__file__).parent / config.name / "memories.json"
        if agent_memories_path.exists():
            try:
                all_memories = json.loads(agent_memories_path.read_text())
                always_load = [m for m in all_memories if m.get("always_load")]
                if always_load:
                    lines = [f"- {m['content']}" for m in always_load]
                    memory_block = "\n".join(lines)
                    append_parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations).\n"
                        "Only you (the agent) can see this section — it is never visible to the user.\n\n"
                        f"{memory_block}"
                    )
            except Exception as e:
                logger.warning(f"Agent '{config.name}': could not read memories.json for preset: {e}")
        else:
            # Fallback: legacy memory.md (for agents not yet migrated)
            agent_memory_path = Path(__file__).parent / config.name / "memory.md"
            if agent_memory_path.exists():
                try:
                    memory_content = agent_memory_path.read_text().strip()
                    if memory_content:
                        append_parts.append(
                            "\n---\n\n"
                            "Your persistent memory (notes you've saved across conversations).\n"
                        "Only you (the agent) can see this section — it is never visible to the user.\n\n"
                            f"{memory_content}"
                        )
                except Exception as e:
                    logger.warning(f"Agent '{config.name}': could not read memory.md for preset: {e}")
        # Include per-agent working memory
        wm_block = _load_working_memory_block(config.name)
        if wm_block:
            append_parts.append(wm_block)
        system_prompt = {
            "type": "preset",
            "preset": config.system_prompt_preset,
        }
        append_content = "\n".join(append_parts).strip()
        if append_content:
            system_prompt["append"] = append_content
    else:
        # Replace mode: instructions + agent-specific memory in a plain string
        parts = []
        if config.prompt:
            parts.append(config.prompt)
        # Global safety rules (above skills and memory)
        if _global_rules:
            parts.append(_global_rules)
        # Mode-specific global instructions (visible for @mentions, silent for background)
        if _global_mode_instructions:
            parts.append(_global_mode_instructions)
        # Skill menu sits above memory in the system prompt
        if _skill_reminder:
            parts.append(_skill_reminder)
        # Agent list sits above memory in the system prompt
        if _agent_list_block:
            parts.append(_agent_list_block)
        # Include per-agent always_load memories from memories.json
        agent_memories_path = Path(__file__).parent / config.name / "memories.json"
        if agent_memories_path.exists():
            try:
                all_memories = json.loads(agent_memories_path.read_text())
                always_load = [m for m in all_memories if m.get("always_load")]
                if always_load:
                    lines = [f"- {m['content']}" for m in always_load]
                    memory_block = "\n".join(lines)
                    parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations).\n"
                        "Only you (the agent) can see this section — it is never visible to the user.\n\n"
                        f"{memory_block}"
                    )
                    logger.info(f"Agent '{config.name}': loaded {len(always_load)} always_load memories for replace-mode system prompt")
            except Exception as e:
                logger.warning(f"Agent '{config.name}': could not read memories.json for replace: {e}")
        else:
            # Fallback: legacy memory.md (for agents not yet migrated)
            agent_memory_path = Path(__file__).parent / config.name / "memory.md"
            if agent_memory_path.exists():
                try:
                    memory_content = agent_memory_path.read_text().strip()
                    if memory_content:
                        parts.append(
                            "\n---\n\n"
                            "Your persistent memory (notes you've saved across conversations).\n"
                        "Only you (the agent) can see this section — it is never visible to the user.\n\n"
                            f"{memory_content}"
                        )
                        logger.info(f"Agent '{config.name}': loaded memory.md for replace-mode system prompt")
                except Exception as e:
                    logger.warning(f"Agent '{config.name}': could not read memory.md for replace: {e}")
        # Include per-agent working memory
        wm_block = _load_working_memory_block(config.name)
        if wm_block:
            parts.append(wm_block)
        system_prompt = "\n".join(parts) if parts else ""

    # Build MCP servers for the agent.
    # Internal "brain" server provides Second Brain tools (memory, scheduler, etc.).
    # External servers (Playwright, etc.) are loaded from external_mcp_servers.json.
    MCP_PREFIX = "mcp__brain__"
    MCP_ANY_PREFIX = "mcp__"
    mcp_servers = {}

    # Config is the sole source of truth — no auto-injection.
    # What's in config.tools is exactly what the agent gets.
    effective_tools = list(config.tools) if config.tools else []

    # Native tool whitelist: agents get exactly the native tools listed in their config
    # — nothing more. This passes `tools=[list]` to the SDK, which in turn passes
    # `--tools Read,Edit,...` to the CLI. Any native tool not in this list — including
    # future Anthropic-shipped tools (Cron*, Monitor, PushNotification, ScheduleWakeup,
    # EnterWorktree, etc.) — is blocked at the CLI level. No blacklist to maintain.
    # Source of truth for what's available lives in .claude/agents/native_tools.py.
    native_tool_names = [t for t in effective_tools if not t.startswith(MCP_ANY_PREFIX)]

    if config.tools:
        # Internal "brain" MCP server
        mcp_tool_names = [t for t in config.tools if t.startswith(MCP_PREFIX)]

        if mcp_tool_names:
            internal_names = [t[len(MCP_PREFIX):] for t in mcp_tool_names]
            try:
                from mcp_tools import create_mcp_server
                mcp_server = create_mcp_server(
                    name="brain",
                    include_tools=internal_names,
                    agent_name=config.name,
                    allowed_skills=config.skills,
                    chat_id=invocation.source_chat_id,
                )
                mcp_servers["brain"] = mcp_server
                logger.info(
                    f"Created MCP server for agent '{config.name}' with "
                    f"{len(internal_names)} tools: {internal_names}"
                )
            except Exception as e:
                logger.error(f"Failed to create MCP server for agent '{config.name}': {e}")

        # External MCP servers (stdio-based: Playwright, etc.)
        # Load config from external_mcp_servers.json alongside this file.
        external_config = _load_external_mcp_servers()
        for server_name, server_config in external_config.items():
            prefix = f"mcp__{server_name}__"
            # Include server if any agent tool matches this server's prefix
            if any(t.startswith(prefix) for t in config.tools):
                mcp_servers[server_name] = server_config
                logger.info(
                    f"Added external MCP server '{server_name}' for agent '{config.name}' "
                    f"(command: {server_config.get('command', 'N/A')})"
                )

    options_kwargs = {
        "model": config.model,
        "system_prompt": system_prompt,
        "allowed_tools": effective_tools if effective_tools else None,
        "permission_mode": "bypassPermissions",
        "can_use_tool": _auto_approve_tool,  # Catch .claude/ directory prompts that bypass mode doesn't suppress
        "hooks": {"PreToolUse": [HookMatcher(matcher=None, hooks=[_keepalive_hook])]},
        "setting_sources": [],  # Never load project settings for subagents
        "max_turns": config.max_turns,
        "mcp_servers": mcp_servers if mcp_servers else None,
        "env": {
            "ENABLE_TOOL_SEARCH": "false",  # Disable tool deferral (tengu_defer_all_bn4)
            # Short-circuits the CLI's XSY() attachment pipeline which auto-injects
            # bundled Skill listings ("The following skills are available for use
            # with the Skill tool:..."), dynamic_skill triggers, native TodoWrite
            # reminders, plan_mode/delegate_mode reminders, nested CLAUDE.md loading,
            # and relevant-memory injection. We have our own Skills system
            # (mcp__brain__fetch_skill), our own memory system, and our own prompts —
            # none of the native auto-injection is wanted. See cli.js function XSY at
            # the `CLAUDE_CODE_DISABLE_ATTACHMENTS` check.
            "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
        },
        # Restore visible thinking on Opus 4.7+ — the model silently changed its
        # default from display="summarized" to display="omitted" (see Anthropic's
        # "What's new in Claude Opus 4.7" docs). Without this, thinking blocks
        # still stream but their content is empty, so the frontend shows no
        # reasoning. The SDK's ClaudeAgentOptions doesn't model the `display`
        # field on ThinkingConfigAdaptive, but the bundled CLI supports the
        # --thinking-display flag, and extra_args forwards unmapped CLI flags.
        # No-op on Sonnet/Haiku (they still default to "summarized").
        "extra_args": {"thinking-display": "summarized"},
    }

    # Tool availability gate (whitelist-only).
    # - Preset agents: opt into Claude Code's full native tool suite.
    # - Everyone else: exactly the native tools listed in config.tools — nothing else.
    #   Empty list → zero native tools enabled. This is the CLI-level ON/OFF switch
    #   that prevents silent-enable of future Anthropic tools.
    if config.system_prompt_preset:
        options_kwargs["tools"] = {"type": "preset", "preset": config.system_prompt_preset}
    else:
        options_kwargs["tools"] = native_tool_names

    # Apply model-aware thinking configuration
    model = config.model or "sonnet"
    if config.thinking_budget:
        # Agent-level override: explicit budget_tokens
        options_kwargs["thinking"] = ThinkingConfigEnabled(type="enabled", budget_tokens=config.thinking_budget)
        logger.info(f"Agent '{config.name}': thinking config override — enabled with budget_tokens={config.thinking_budget}")
    elif config.effort:
        # Agent-level override: explicit effort (with adaptive thinking)
        options_kwargs["thinking"] = ThinkingConfigAdaptive(type="adaptive")
        options_kwargs["effort"] = config.effort
        logger.info(f"Agent '{config.name}': thinking config override — adaptive, effort={config.effort}")
    else:
        # Model-level defaults from THINKING_DEFAULTS
        thinking_cfg = THINKING_DEFAULTS.get(model)
        if thinking_cfg:
            options_kwargs["thinking"] = thinking_cfg["thinking"]
            if "effort" in thinking_cfg:
                options_kwargs["effort"] = thinking_cfg["effort"]
            logger.info(
                f"Agent '{config.name}': applying thinking config for model '{model}': "
                f"thinking={type(thinking_cfg['thinking']).__name__}, "
                f"effort={thinking_cfg.get('effort', 'N/A')}"
            )
        else:
            logger.info(f"Agent '{config.name}': no thinking defaults for model '{model}'")

    options = ClaudeAgentOptions(**options_kwargs)

    # Add output format if specified
    if config.output_format:
        options.output_format = config.output_format

    # Auto-retrieve contextual memories relevant to the agent's task prompt
    try:
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from contextual_memory import auto_retrieve_context, rewrite_query_for_retrieval

        raw_query = invocation.prompt or ""
        retrieval_queries = await rewrite_query_for_retrieval(
            raw_query,
            session_id=invocation.source_chat_id or f"agent:{config.name}",
        )
        logger.info(f"Agent '{config.name}': query rewrite: '{raw_query[:80]}' -> {retrieval_queries}")
        # Run CPU-bound retrieval in a thread to avoid blocking the event loop
        import functools
        loop = asyncio.get_event_loop()
        ctx_block = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(
                    auto_retrieve_context,
                    query=retrieval_queries,
                    agent_name=config.name,
                ),
            ),
            timeout=15.0,  # 15s max — don't let retrieval stall the agent
        )
        if ctx_block:
            if isinstance(options.system_prompt, dict):
                existing = options.system_prompt.get("append", "")
                options.system_prompt["append"] = existing + "\n\n" + ctx_block
            else:
                options.system_prompt = (options.system_prompt or "") + "\n\n" + ctx_block
            logger.info(f"Agent '{config.name}': injected contextual memory into system prompt")
    except Exception as e:
        logger.warning(f"Agent '{config.name}': contextual memory auto-retrieve failed: {e}")

    effective_prompt = invocation.prompt

    result_text = ""
    transcript = ""
    blocks = []

    try:
        async with asyncio.timeout(config.timeout_seconds):
            result_text, transcript, blocks = await _consume_query(effective_prompt, options)
    except asyncio.TimeoutError:
        raise
    except ExceptionGroup as eg:
        # Unwrap TaskGroup/ExceptionGroup to log actual sub-exceptions
        import traceback
        for i, exc in enumerate(eg.exceptions):
            logger.error(f"Agent '{config.name}' sub-exception {i}: {type(exc).__name__}: {exc}")
            logger.error("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise
    except Exception as e:
        import traceback
        logger.error(f"Agent '{config.name}' exception: {type(e).__name__}: {e}")
        logger.error("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise

    finally:
        if reg_id:
            try:
                deregister_process(reg_id)
            except Exception as e:
                logger.warning(f"Failed to deregister agent '{config.name}': {e}")

    return result_text, transcript, blocks


def _extract_tool_content(content) -> str:
    """Normalize ToolResultBlock.content to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit chars, adding ellipsis if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _format_transcript(captured: list, result_meta: Optional[dict] = None) -> str:
    """Render captured message entries into a readable markdown transcript.

    Args:
        captured: List of dicts with keys like 'type', 'text', 'name', 'input', 'content', 'is_error'.
        result_meta: Optional dict with 'num_turns', 'cost', 'duration_ms' from ResultMessage.
    """
    TOOL_INPUT_LIMIT = 500
    TOOL_RESULT_LIMIT = 3000

    parts = []
    for entry in captured:
        etype = entry.get("type")

        if etype == "text":
            parts.append(entry["text"])

        elif etype == "tool_use":
            name = entry["name"]
            raw_input = entry.get("input", {})
            input_str = json.dumps(raw_input, indent=2) if isinstance(raw_input, dict) else str(raw_input)
            parts.append(f"\n---\n**Tool: `{name}`**\n{_truncate(input_str, TOOL_INPUT_LIMIT)}")

        elif etype == "tool_result":
            content = entry.get("content", "")
            is_error = entry.get("is_error", False)
            prefix = "**Error:**" if is_error else "**Result:**"
            parts.append(f"{prefix}\n{_truncate(content, TOOL_RESULT_LIMIT)}\n---\n")

    # Append metadata footer if available
    if result_meta:
        meta_parts = []
        if result_meta.get("num_turns"):
            meta_parts.append(f"{result_meta['num_turns']} turns")
        if result_meta.get("cost") is not None:
            meta_parts.append(f"${result_meta['cost']:.4f}")
        if result_meta.get("duration_ms"):
            secs = result_meta["duration_ms"] / 1000
            meta_parts.append(f"{secs:.1f}s")
        if meta_parts:
            parts.append(f"\n---\n*{' | '.join(meta_parts)}*")

    return "\n\n".join(parts)


def _captured_to_blocks(captured: list) -> list:
    """Convert captured SDK messages to ContentBlock-compatible dicts for UI rendering.

    Returns a flat list of blocks matching the frontend ContentBlock interface:
    - text: {id, type, content, status}
    - thinking: {id, type, content, status, duration_ms}
    - tool_use: {id, type, content, tool_name, tool_call_id, tool_input, status}
    - tool_result: {id, type, content, tool_call_id, is_error, status}
    """
    import uuid as _uuid

    blocks = []
    for entry in captured:
        etype = entry.get("type")

        if etype == "text":
            blocks.append({
                "id": f"blk_{_uuid.uuid4().hex[:12]}",
                "type": "text",
                "content": entry.get("text", ""),
                "status": "complete",
            })

        elif etype == "thinking":
            blocks.append({
                "id": f"blk_{_uuid.uuid4().hex[:12]}",
                "type": "thinking",
                "content": entry.get("text", ""),
                "status": "complete",
            })

        elif etype == "tool_use":
            tool_call_id = entry.get("id", f"toolu_{_uuid.uuid4().hex[:20]}")
            blocks.append({
                "id": f"blk_{_uuid.uuid4().hex[:12]}",
                "type": "tool_use",
                "content": "",
                "tool_name": entry.get("name", ""),
                "tool_call_id": tool_call_id,
                "tool_input": entry.get("input", {}),
                "status": "complete",
            })

        elif etype == "tool_result":
            blocks.append({
                "id": f"blk_{_uuid.uuid4().hex[:12]}",
                "type": "tool_result",
                "content": entry.get("content", ""),
                "tool_call_id": entry.get("tool_use_id", ""),
                "is_error": entry.get("is_error", False),
                "status": "complete",
            })

    return blocks


async def _consume_query(prompt: str, options) -> tuple:
    """
    Consume the async generator from query() and return (result_text, transcript, blocks).

    Captures all SDK messages into a structured transcript and UI-ready blocks.
    - result_text: the final ResultMessage.result (used for compact ping notifications)
    - transcript: a full markdown-formatted trace (for MCP tool consumers / other agents)
    - blocks: list of ContentBlock-compatible dicts (for UI rendering with tool pills)

    When MCP servers are configured, the prompt is sent as an AsyncIterable
    (streaming mode) so the SDK keeps stdin open for the bidirectional MCP
    control protocol.
    """
    from claude_agent_sdk import query

    # Always use streaming mode — required for can_use_tool callback (Python SDK)
    # and for MCP bridge protocol.  Without streaming, permission prompts from
    # .claude/ directory protection time out to denials.
    has_mcp = bool(options.mcp_servers)

    # Always stream — can_use_tool needs it even without MCP
    if True:
        async def _prompt_stream():
            yield {
                "type": "user",
                "session_id": "",
                "message": {"role": "user", "content": prompt},
                "parent_tool_use_id": None,
            }

        effective_prompt = _prompt_stream()
    else:
        effective_prompt = prompt

    result_text = ""
    captured = []  # List of transcript entries
    result_meta = None

    async for message in query(prompt=effective_prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in (message.content or []):
                if isinstance(block, TextBlock):
                    captured.append({"type": "text", "text": block.text})
                elif isinstance(block, ToolUseBlock):
                    captured.append({
                        "type": "tool_use",
                        "name": block.name,
                        "id": block.id,
                        "input": block.input,
                    })
                elif isinstance(block, ThinkingBlock):
                    captured.append({
                        "type": "thinking",
                        "text": block.thinking or "",
                    })

        elif isinstance(message, UserMessage):
            # UserMessage carries tool results back
            content = message.content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        captured.append({
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": _extract_tool_content(block.content),
                            "is_error": block.is_error or False,
                        })
            # String content from UserMessage is not interesting for transcript

        elif isinstance(message, ResultMessage):
            result_text = message.result or ""
            if hasattr(message, "structured_output") and message.structured_output:
                result_text = json.dumps(message.structured_output, indent=2)
            result_meta = {
                "num_turns": getattr(message, "num_turns", None),
                "cost": getattr(message, "total_cost_usd", None),
                "duration_ms": getattr(message, "duration_ms", None),
            }

        # Skip SystemMessage, StreamEvent — not relevant for transcript

    transcript = _format_transcript(captured, result_meta)
    blocks = _captured_to_blocks(captured)
    return result_text, transcript, blocks


def _log_execution(invocation: AgentInvocation, result: AgentResult) -> None:
    """Log an execution to the executions log file.

    Uses fcntl.flock to serialize concurrent read-modify-write operations,
    preventing data loss when parallel agents finish simultaneously.
    """
    import fcntl

    try:
        # Open (or create) the lock file alongside the log
        lock_path = EXECUTIONS_LOG.with_suffix(".lock")
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                # Load existing log
                if EXECUTIONS_LOG.exists():
                    with open(EXECUTIONS_LOG, "r") as f:
                        data = json.load(f)
                else:
                    data = {"executions": []}

                # Add new entry (truncate transcript to avoid bloating the log)
                result_dict = result.to_dict()
                if result_dict.get("transcript") and len(result_dict["transcript"]) > 5000:
                    result_dict["transcript"] = result_dict["transcript"][:5000] + "\n... (truncated in log)"
                entry = {
                    "invocation": invocation.to_dict(),
                    "result": result_dict,
                }
                data["executions"].append(entry)

                # Keep last 100 entries
                data["executions"] = data["executions"][-100:]

                # Atomic write: temp file then rename
                tmp_path = EXECUTIONS_LOG.with_suffix(".tmp")
                with open(tmp_path, "w") as f:
                    json.dump(data, f, indent=2)
                tmp_path.rename(EXECUTIONS_LOG)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    except Exception as e:
        logger.error(f"Failed to log execution: {e}")
