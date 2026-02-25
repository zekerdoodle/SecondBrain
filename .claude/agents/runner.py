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

# The SDK's stream_input() keeps stdin open until the first `result` message arrives
# or a timeout fires (CLAUDE_CODE_STREAM_CLOSE_TIMEOUT, default 60s).  Agents that
# use page_parser with summary subagents can take 90-120 seconds, which hits the
# default 60s timeout and closes stdin while Claude is still mid-conversation,
# causing CLIConnectionError: ProcessTransport is not ready for writing.
# Set to 10 minutes — well above any agent's timeout_seconds.
os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "600000")

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

    invoked_at = datetime.utcnow()
    asyncio.create_task(_run_chain_agent(
        chain=chain,
        on_failure=on_failure,
        summarize=summarize,
        source_chat_id=source_chat_id,
        invoked_at=invoked_at,
    ))

    agent_names = [step["agent"] for step in chain]
    chain_str = " \u2192 ".join(agent_names)
    return {
        "status": "accepted",
        "mode": "chain",
        "message": f"Agent chain started: {chain_str}\n\nYou'll be notified when the chain completes."
    }


async def _run_chain_agent(
    chain: List[Dict[str, str]],
    on_failure: str,
    summarize: bool,
    source_chat_id: str,
    invoked_at: datetime,
) -> None:
    """Execute an agent chain sequentially and send notification on completion.

    Follows the same pattern as _run_ping_agent: run work, add notification,
    log execution. Top-level try/except ensures errors are always logged.
    """
    from registry import get_registry

    try:
        registry = get_registry()
        results = []  # List of (agent_name, status, response/error)
        chain_failed = False
        failed_agent = None

        for i, step in enumerate(chain):
            agent_name = step["agent"]
            prompt = step["prompt"]

            logger.info(f"Chain step {i+1}/{len(chain)}: Running agent '{agent_name}'")

            config = registry.get(agent_name)
            if not config:
                results.append((agent_name, "error", f"Unknown agent: {agent_name}"))
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
                    results.append((agent_name, "success", result.transcript or result.response))
                    logger.info(f"Chain step {i+1}: Agent '{agent_name}' succeeded")
                else:
                    error_msg = result.error or result.status
                    results.append((agent_name, "error", error_msg))
                    logger.warning(f"Chain step {i+1}: Agent '{agent_name}' failed: {error_msg}")

                    if on_failure == "alert_and_stop":
                        chain_failed = True
                        failed_agent = agent_name
                        break

            except Exception as e:
                logger.error(f"Chain step {i+1}: Agent '{agent_name}' exception: {e}")
                results.append((agent_name, "exception", str(e)))

                if on_failure == "alert_and_stop":
                    chain_failed = True
                    failed_agent = agent_name
                    break

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

    if config.system_prompt_preset:
        append_parts = []
        if config.prompt:
            append_parts.append(config.prompt)
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
                        "Your persistent memory (notes you've saved across conversations):\n\n"
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
                            "Your persistent memory (notes you've saved across conversations):\n\n"
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
                        "Your persistent memory (notes you've saved across conversations):\n\n"
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
                            "Your persistent memory (notes you've saved across conversations):\n\n"
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

    # Build MCP server if the agent uses any mcp__brain__ tools.
    # Without this, the SDK subprocess has no MCP server to resolve those tool names.
    MCP_PREFIX = "mcp__brain__"
    mcp_servers = {}

    # Config is the sole source of truth — no auto-injection.
    # What's in config.tools is exactly what the agent gets.
    effective_tools = list(config.tools) if config.tools else []

    # Compute disallowed native tools: block any native tool NOT in the agent's config.
    # The SDK provides all native tools by default; disallowed_tools is the only way
    # to restrict them. Without this, every agent gets ALL native tools regardless
    # of what's in config.yaml.
    from registry import VALID_NATIVE_TOOLS
    native_tool_names = [t for t in effective_tools if not t.startswith(MCP_PREFIX)]
    disallowed_native = [t for t in VALID_NATIVE_TOOLS if t not in native_tool_names]

    if config.tools:
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

    options_kwargs = {
        "model": config.model,
        "system_prompt": system_prompt,
        "allowed_tools": effective_tools if effective_tools else None,
        "permission_mode": "bypassPermissions",
        "setting_sources": [],  # Never load project settings for subagents
        "max_turns": config.max_turns,
        "mcp_servers": mcp_servers if mcp_servers else None,
    }

    # Preset agents need the tools preset so Claude Code's native tool suite is used
    if config.system_prompt_preset:
        options_kwargs["tools"] = {"type": "preset", "preset": config.system_prompt_preset}

    # Block native tools not in the agent's config — applies to ALL agents
    if disallowed_native:
        options_kwargs["disallowed_tools"] = disallowed_native

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

        raw_query = (invocation.prompt or "")[-1000:]
        retrieval_queries = await rewrite_query_for_retrieval(raw_query)
        logger.info(f"Agent '{config.name}': query rewrite: '{raw_query[:80]}' -> {retrieval_queries}")
        ctx_block = auto_retrieve_context(
            query=retrieval_queries,
            agent_name=config.name,
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

    # Determine if we need streaming mode for MCP bridge
    has_mcp = bool(options.mcp_servers)

    if has_mcp:
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
