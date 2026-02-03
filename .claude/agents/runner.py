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
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

from models import (
    AgentConfig, AgentInvocation, AgentResult, AgentType, InvocationMode
)
from agent_notifications import get_notification_queue

logger = logging.getLogger("agents.runner")

# Execution log file
EXECUTIONS_LOG = Path(__file__).parent / "executions.json"

# Default working directory for agents
WORKING_DIR = "/home/debian/second_brain"

# Claude CLI binary path
CLAUDE_CLI = "/home/debian/second_brain/interface/server/venv/lib/python3.11/site-packages/claude_agent_sdk/_bundled/claude"


async def invoke_agent(
    name: str,
    prompt: str,
    mode: Union[str, InvocationMode] = "foreground",
    source_chat_id: Optional[str] = None,
    model_override: Optional[str] = None
) -> Union[AgentResult, Dict[str, str]]:
    """
    Invoke an agent with the specified mode.

    Args:
        name: Agent name (must be registered)
        prompt: Task description for the agent
        mode: Invocation mode (foreground, ping, trust, scheduled)
        source_chat_id: Chat ID for ping mode notifications
        model_override: Override the agent's default model

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
            output_format=config.output_format,
            prompt=config.prompt,
            prompt_appendage=config.prompt_appendage,
        )

    # Create invocation record
    invocation = AgentInvocation(
        agent=name,
        prompt=prompt,
        mode=mode,
        source_chat_id=source_chat_id,
        model_override=model_override,
    )

    logger.info(f"Invoking agent '{name}' in {mode.value} mode")

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

    Routes to appropriate runner based on agent type.
    """
    started_at = datetime.utcnow()

    try:
        if config.type == AgentType.CLI:
            response = await _run_cli_agent(config, invocation)
        else:
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
    result = await _run_agent(config, invocation)

    # Add to notification queue
    queue = get_notification_queue()
    queue.add(
        agent=config.name,
        agent_response=result.response if result.status == "success" else f"Error: {result.error}",
        source_chat_id=invocation.source_chat_id,
        invoked_at=invocation.invoked_at,
        completed_at=result.completed_at,
    )

    # Log execution
    _log_execution(invocation, result)


async def _run_background_agent(config: AgentConfig, invocation: AgentInvocation) -> None:
    """Run agent and log (no notification)."""
    result = await _run_agent(config, invocation)
    _log_execution(invocation, result)


async def _run_sdk_agent(config: AgentConfig, invocation: AgentInvocation) -> str:
    """
    Run an SDK-based agent using claude_agent_sdk.query().
    """
    from claude_agent_sdk import query, ClaudeAgentOptions

    logger.info(f"Running SDK agent '{config.name}' with model {config.model}")

    # Build options
    options = ClaudeAgentOptions(
        model=config.model,
        system_prompt=config.prompt,
        allowed_tools=config.tools if config.tools else None,
        permission_mode="bypassPermissions",
        setting_sources=[],  # Skip project CLAUDE.md - agents use their own prompts
        max_turns=20,
    )

    # Add output format if specified
    if config.output_format:
        options.output_format = config.output_format

    result_text = ""

    try:
        async with asyncio.timeout(config.timeout_seconds):
            result_text = await _consume_query(invocation.prompt, options)
    except asyncio.TimeoutError:
        raise

    return result_text


async def _consume_query(prompt: str, options) -> str:
    """
    Consume the async generator from query() and return the final result.
    """
    from claude_agent_sdk import query

    result_text = ""
    async for message in query(prompt=prompt, options=options):
        # Capture final result
        if hasattr(message, "result"):
            result_text = message.result
        elif hasattr(message, "structured_output") and message.structured_output:
            result_text = json.dumps(message.structured_output, indent=2)

    return result_text


async def _run_cli_agent(config: AgentConfig, invocation: AgentInvocation) -> str:
    """
    Run a CLI-based agent using claude --print.

    Used for claude_code agent which needs full Claude Code capabilities.
    """
    logger.info(f"Running CLI agent '{config.name}' with model {config.model}")

    # Build command
    cmd = [
        CLAUDE_CLI,
        "--print",
        "--dangerously-skip-permissions",
        "--model", config.model,
        "--setting-sources", "user",  # Skip project CLAUDE.md
        "--output-format", "json",  # Use JSON to extract final model reply
    ]

    # Add system prompt appendage if present
    if config.prompt_appendage:
        cmd.extend(["--append-system-prompt", config.prompt_appendage])

    # Add the prompt
    cmd.append(invocation.prompt)

    # Run the command
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKING_DIR,
        env={**os.environ}
    )

    stdout, stderr = await asyncio.wait_for(
        proc.communicate(),
        timeout=config.timeout_seconds
    )

    stdout_text = stdout.decode().strip()
    stderr_text = stderr.decode().strip()
    return_code = proc.returncode

    logger.info(f"CLI agent '{config.name}' completed with return code {return_code}")

    if return_code != 0:
        error_msg = f"Agent exited with code {return_code}"
        if stderr_text:
            error_msg += f"\nstderr: {stderr_text}"
        if stdout_text:
            error_msg += f"\nstdout: {stdout_text}"
        raise RuntimeError(error_msg)

    # Parse JSON output to extract the final model reply
    return _extract_final_reply(stdout_text, config.name)


def _extract_final_reply(json_output: str, agent_name: str) -> str:
    """
    Extract the final model reply from Claude CLI JSON output.

    The JSON output is an array of messages. We look for the message
    with type="result" which contains the final response in its "result" field.

    Args:
        json_output: Raw JSON output from claude --print --output-format json
        agent_name: Agent name for logging

    Returns:
        The final model reply text
    """
    try:
        messages = json.loads(json_output)

        # Find the result message
        for msg in messages:
            if msg.get("type") == "result":
                result = msg.get("result", "")
                if result:
                    return result
                # If result is empty, check if there was an error
                if msg.get("is_error"):
                    error_msg = msg.get("error", "Unknown error")
                    raise RuntimeError(f"Agent returned error: {error_msg}")

        # Fallback: look for assistant message content
        for msg in reversed(messages):
            if msg.get("type") == "assistant":
                message_data = msg.get("message", {})
                content = message_data.get("content", [])
                for block in content:
                    if block.get("type") == "text":
                        return block.get("text", "")

        logger.warning(f"CLI agent '{agent_name}' produced no extractable result")
        return ""

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse CLI agent JSON output: {e}")
        # Fallback to returning raw output if JSON parsing fails
        return json_output


def _log_execution(invocation: AgentInvocation, result: AgentResult) -> None:
    """Log an execution to the executions log file."""
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

        # Save
        with open(EXECUTIONS_LOG, "w") as f:
            json.dump(data, f, indent=2)

    except Exception as e:
        logger.error(f"Failed to log execution: {e}")
