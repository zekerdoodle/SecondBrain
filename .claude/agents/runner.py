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
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from models import (
    AgentConfig, AgentInvocation, AgentResult, AgentType, InvocationMode
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

# Claude CLI binary path
CLAUDE_CLI = "/home/debian/second_brain/interface/server/venv/lib/python3.11/site-packages/claude_agent_sdk/_bundled/claude"


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
            prompt_appendage=config.prompt_appendage,
            system_prompt_preset=config.system_prompt_preset,
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

    Routes to appropriate runner based on agent type.
    Process registration is handled by each runner individually
    so they can capture the correct subprocess PID.
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
        system_prompt = config.prompt

    # Build options
    # If agent has Skill in its tools, enable project settings so the CLI
    # discovers skills from .claude/skills/. We swap CLAUDE.md to a minimal
    # stub to prevent identity bleed from the primary agent's content.
    has_skills = config.tools and "Skill" in config.tools
    options = ClaudeAgentOptions(
        model=config.model,
        system_prompt=system_prompt,
        allowed_tools=config.tools if config.tools else None,
        permission_mode="bypassPermissions",
        setting_sources=["project"] if has_skills else [],
        max_turns=config.max_turns,
    )

    # Add output format if specified
    if config.output_format:
        options.output_format = config.output_format

    # CONCURRENCY-SAFE CLAUDE.md HANDLING
    # Instead of swapping the shared CLAUDE.md (which races when agents run in parallel),
    # we write a per-invocation temp CLAUDE.md and point the SDK at a temp working directory
    # that symlinks everything except CLAUDE.md from the real .claude/ directory.
    temp_claude_dir = None
    claude_md_path = Path(__file__).parent.parent  # .claude/
    if has_skills and (claude_md_path / "CLAUDE.md").exists():
        try:
            # Build agent-scoped CLAUDE.md content
            sections = [
                f"# Agent: {config.name}\n\n"
                "Your system instructions are provided via the system prompt.\n"
                "Follow only those instructions.\n"
            ]

            # Include per-agent memory.md if it exists (skip for preset agents —
            # they already get memory via the system prompt append field)
            agent_memory_path = Path(__file__).parent / config.name / "memory.md"
            if not config.system_prompt_preset and agent_memory_path.exists():
                try:
                    memory_content = agent_memory_path.read_text().strip()
                    if memory_content:
                        sections.append(
                            "\n---\n\n"
                            "Your persistent memory (notes you've saved across conversations):\n\n"
                            f"{memory_content}"
                        )
                        logger.info(f"CLAUDE.md: included memory.md for agent '{config.name}'")
                except Exception as e:
                    logger.warning(f"CLAUDE.md: could not read memory.md for agent '{config.name}': {e}")

            # Create a temp .claude/ directory with symlinks to all real contents
            # but a unique CLAUDE.md for this invocation
            temp_claude_dir = tempfile.mkdtemp(prefix=f"agent_{config.name}_")
            temp_dot_claude = Path(temp_claude_dir) / ".claude"
            temp_dot_claude.mkdir()

            # Symlink all items from real .claude/ except CLAUDE.md
            real_dot_claude = claude_md_path
            for item in real_dot_claude.iterdir():
                if item.name == "CLAUDE.md":
                    continue
                target = temp_dot_claude / item.name
                target.symlink_to(item)

            # Write the agent-scoped CLAUDE.md
            (temp_dot_claude / "CLAUDE.md").write_text("".join(sections))
            logger.info(f"CLAUDE.md: created isolated copy for agent '{config.name}' at {temp_claude_dir}")

            # Override the working directory for this invocation
            options.cwd = temp_claude_dir

        except Exception as e:
            logger.warning(f"CLAUDE.md: failed to create isolated copy for agent '{config.name}': {e}")
            temp_claude_dir = None

    result_text = ""

    try:
        async with asyncio.timeout(config.timeout_seconds):
            result_text = await _consume_query(invocation.prompt, options)
    except asyncio.TimeoutError:
        raise

    finally:
        # Clean up temp directory (symlinks are safe to remove)
        if temp_claude_dir is not None:
            try:
                import shutil
                shutil.rmtree(temp_claude_dir, ignore_errors=True)
                logger.info(f"CLAUDE.md: cleaned up isolated copy for agent '{config.name}'")
            except Exception as e:
                logger.warning(f"CLAUDE.md: failed to clean up temp dir for agent '{config.name}': {e}")
        if reg_id:
            try:
                deregister_process(reg_id)
            except Exception as e:
                logger.warning(f"Failed to deregister agent '{config.name}': {e}")

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
    logger.info(f"Running CLI agent '{config.name}' with model {config.model}, timeout {config.timeout_seconds}s")

    # Build command
    # If agent has Skill in its tools, enable project settings so the CLI
    # discovers skills from .claude/skills/.
    has_skills = config.tools and "Skill" in config.tools
    cmd = [
        CLAUDE_CLI,
        "--print",
        "--dangerously-skip-permissions",
        "--model", config.model,
        "--setting-sources", "project" if has_skills else "",
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

    # Register in process registry with the actual subprocess PID
    task_desc = invocation.prompt[:80] if invocation.prompt else "active"
    reg_id = None
    try:
        reg_id = register_process(config.name, task=task_desc, pid=proc.pid)
    except Exception as e:
        logger.warning(f"Failed to register agent '{config.name}' in process registry: {e}")

    try:
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

    except asyncio.TimeoutError:
        # Kill the subprocess to prevent zombie processes
        try:
            proc.kill()
            await proc.wait()
            logger.info(f"Killed timed-out CLI agent '{config.name}' (PID {proc.pid})")
        except ProcessLookupError:
            pass  # Already dead
        raise

    finally:
        if reg_id:
            try:
                deregister_process(reg_id)
            except Exception as e:
                logger.warning(f"Failed to deregister agent '{config.name}': {e}")


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
