"""
Agent Runner - Execution engine for agents.

Supports four invocation modes:
- foreground: Blocking execution, returns result directly
- ping: Async execution, notification when done
- trust: Fire and forget, logged but no notification
- scheduled: Like trust, but triggered by scheduler
"""

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from models import (
    AgentConfig, AgentInvocation, AgentResult, InvocationMode
)
from agent_notifications import get_notification_queue

# Ensure server directory is importable (for process_registry)
_server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../interface/server"))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)

from process_registry import register_process, deregister_process

logger = logging.getLogger("agents.runner")

# Execution log file
EXECUTIONS_LOG = Path(__file__).parent / "executions.json"

# Default working directory for agents
WORKING_DIR = "/home/debian/second_brain"


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
    project: Optional[Union[str, List[str]]] = None
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
        response = await _run_sdk_agent(config, invocation)

        return AgentResult(
            agent=config.name,
            status="success",
            response=response,
            started_at=started_at,
            completed_at=datetime.utcnow(),
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
    if config.system_prompt_preset:
        append_parts = []
        if config.prompt:
            append_parts.append(config.prompt)
        # Include per-agent memory.md in append
        agent_memory_path = Path(__file__).parent / config.name / "memory.md"
        if agent_memory_path.exists():
            try:
                memory_content = agent_memory_path.read_text().strip()
                if memory_content:
                    append_parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{memory_content}"
                    )
            except Exception as e:
                logger.warning(f"Agent '{config.name}': could not read memory.md for preset: {e}")
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
        # Include per-agent memory.md alongside instructions
        agent_memory_path = Path(__file__).parent / config.name / "memory.md"
        if agent_memory_path.exists():
            try:
                memory_content = agent_memory_path.read_text().strip()
                if memory_content:
                    parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{memory_content}"
                    )
                    logger.info(f"Agent '{config.name}': loaded memory.md for replace-mode system prompt")
            except Exception as e:
                logger.warning(f"Agent '{config.name}': could not read memory.md for replace: {e}")
        system_prompt = "\n".join(parts) if parts else ""

    # Build MCP server if the agent uses any mcp__brain__ tools.
    # Without this, the SDK subprocess has no MCP server to resolve those tool names.
    MCP_PREFIX = "mcp__brain__"
    mcp_servers = {}

    # Determine if this agent has skill access (for auto-including fetch_skill)
    agent_has_skills = config.skills is None or (isinstance(config.skills, list) and len(config.skills) > 0)

    if config.tools:
        mcp_tool_names = [t for t in config.tools if t.startswith(MCP_PREFIX)]

        # Auto-include fetch_skill if agent has skill access and it's not already listed
        fetch_skill_mcp = f"{MCP_PREFIX}fetch_skill"
        if agent_has_skills and fetch_skill_mcp not in mcp_tool_names:
            mcp_tool_names.append(fetch_skill_mcp)

        if mcp_tool_names:
            # Strip prefix to get internal tool names for the MCP registry
            internal_names = [t[len(MCP_PREFIX):] for t in mcp_tool_names]
            try:
                from mcp_tools import create_mcp_server
                mcp_server = create_mcp_server(
                    name="brain",
                    include_tools=internal_names,
                    agent_name=config.name,
                    allowed_skills=config.skills,
                )
                mcp_servers["brain"] = mcp_server
                logger.info(
                    f"Created MCP server for agent '{config.name}' with "
                    f"{len(internal_names)} tools: {internal_names}"
                )
            except Exception as e:
                logger.error(f"Failed to create MCP server for agent '{config.name}': {e}")
    elif agent_has_skills:
        # Agent has no tools configured but has skill access — create MCP server
        # with just fetch_skill so the agent can load skills on demand
        try:
            from mcp_tools import create_mcp_server
            mcp_server = create_mcp_server(
                name="brain",
                include_tools=["fetch_skill"],
                agent_name=config.name,
                allowed_skills=config.skills,
            )
            mcp_servers["brain"] = mcp_server
            mcp_tool_names = [f"{MCP_PREFIX}fetch_skill"]
            logger.info(f"Created MCP server for agent '{config.name}' with fetch_skill only")
        except Exception as e:
            logger.error(f"Failed to create fetch_skill MCP server for agent '{config.name}': {e}")

    # Build the effective allowed_tools list (config.tools + any auto-added tools)
    effective_tools = list(config.tools) if config.tools else []
    if agent_has_skills:
        fetch_skill_mcp = f"{MCP_PREFIX}fetch_skill"
        if fetch_skill_mcp not in effective_tools:
            effective_tools.append(fetch_skill_mcp)

    # Inject the shared "Available agents" block into the system prompt — but only
    # if this agent has access to any agent-calling tools (invoke_agent, etc.).
    # Agents without agent tools never see the agent list.
    try:
        from mcp_tools.agents import get_agent_list_for_prompt
        agent_list_block = get_agent_list_for_prompt(effective_tools)
        if agent_list_block:
            if isinstance(system_prompt, dict):
                # Preset mode — append to the "append" field
                existing = system_prompt.get("append", "")
                system_prompt["append"] = existing + agent_list_block
            else:
                # Replace mode — append to the string
                system_prompt = system_prompt + agent_list_block
            logger.info(f"Agent '{config.name}': injected agent list into system prompt")
    except Exception as e:
        logger.warning(f"Agent '{config.name}': failed to inject agent list: {e}")

    options = ClaudeAgentOptions(
        model=config.model,
        system_prompt=system_prompt,
        allowed_tools=effective_tools if effective_tools else None,
        permission_mode="bypassPermissions",
        setting_sources=[],  # Never load project settings for subagents
        max_turns=config.max_turns,
        mcp_servers=mcp_servers if mcp_servers else None,
    )

    # Add output format if specified
    if config.output_format:
        options.output_format = config.output_format

    # Inject skill menu (lightweight list of available skills) into the prompt.
    # Full skill bodies are loaded on demand via the fetch_skill MCP tool.
    effective_prompt = invocation.prompt
    if agent_has_skills:
        try:
            from skill_injector import get_skill_reminder
            skill_reminder = get_skill_reminder(allowed_skills=config.skills)
            if skill_reminder:
                effective_prompt = f"{skill_reminder}\n\n{invocation.prompt}"
                logger.info(f"Injected skill menu into agent '{config.name}' prompt")
        except Exception as e:
            logger.warning(f"Skill menu injection failed for agent '{config.name}': {e}")

    result_text = ""

    try:
        async with asyncio.timeout(config.timeout_seconds):
            result_text = await _consume_query(effective_prompt, options)
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

    return result_text


async def _consume_query(prompt: str, options) -> str:
    """
    Consume the async generator from query() and return the final result.

    When MCP servers are configured, the prompt is sent as an AsyncIterable
    (streaming mode) so the SDK keeps stdin open for the bidirectional MCP
    control protocol. Without this, the CLI subprocess can't send MCP JSONRPC
    requests back through stdin/stdout.
    """
    from claude_agent_sdk import query

    # Determine if we need streaming mode for MCP bridge
    has_mcp = bool(options.mcp_servers)

    if has_mcp:
        # Wrap prompt in an async iterable so the SDK uses stream_input(),
        # which keeps stdin open for MCP JSONRPC communication.
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
    async for message in query(prompt=effective_prompt, options=options):
        # Capture final result
        if hasattr(message, "result"):
            result_text = message.result
        elif hasattr(message, "structured_output") and message.structured_output:
            result_text = json.dumps(message.structured_output, indent=2)

    return result_text


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

                # Add new entry
                entry = {
                    "invocation": invocation.to_dict(),
                    "result": result.to_dict(),
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
