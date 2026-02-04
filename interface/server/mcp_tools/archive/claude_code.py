"""
Claude Code Subagent tool.

Delegates coding tasks to Claude Code CLI.
"""

import os
import sys
import asyncio
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


@register_tool("utilities")
@tool(
    name="claude_code",
    description="""Delegate coding tasks to Claude Code, a specialized coding agent.

IMPORTANT USAGE GUIDELINES FOR CLAUDE (the calling agent):

1. **Natural Language Only**: Provide descriptive, plain-language prompts explaining:
   - What you want to achieve (the goal)
   - Relevant context and constraints
   - Documentation or examples if helpful

   DO NOT provide:
   - Specific file paths to edit (let Claude Code discover them)
   - Exact code changes or diffs
   - Step-by-step implementation instructions

2. **Good Prompt Examples**:
   - "Add a new MCP tool called 'weather_check' that fetches weather data from OpenWeatherMap API"
   - "Fix the bug where scheduler tasks aren't persisting after server restart"
   - "Refactor the memory system to use async/await consistently"

3. **Bad Prompt Examples** (too prescriptive):
   - "Edit mcp_tools.py line 450, change X to Y"
   - "Add this exact function: def foo(): ..."

4. **Verification**: After Claude Code completes, you (the main agent) can:
   - Call `restart_server` to apply changes
   - Read modified files to verify the implementation
   - Run tests or commands to validate behavior

5. **Your Capabilities**: You have `restart_server` tool to restart the server after code changes.
   Claude Code will make the changes; you handle verification and server lifecycle.

This tool runs Claude Code with full permissions in your environment.""",
    input_schema={
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Natural language description of the coding task. Be descriptive about goals, not prescriptive about implementation."
            },
            "timeout_seconds": {
                "type": "integer",
                "description": "Maximum execution time in seconds (default: 600 = 10 minutes)",
                "default": 600
            },
            "model": {
                "type": "string",
                "description": "Model to use: 'opus' (default, more capable) or 'sonnet' (faster)",
                "enum": ["sonnet", "opus"],
                "default": "opus"
            }
        },
        "required": ["prompt"]
    }
)
async def claude_code(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a coding task using Claude Code CLI as a subagent."""
    prompt = args.get("prompt", "")
    timeout_seconds = args.get("timeout_seconds", 600)
    model = args.get("model", "opus")

    if not prompt:
        return {"content": [{"type": "text", "text": "Error: prompt is required"}], "is_error": True}

    # System prompt explaining Claude Code's role as a subagent
    system_prompt = """IMPORTANT: This message was sent by the primary Claude agent (using AgentSDK), not a human user.

You are Claude Code, operating as a coding subagent. Sessions are not persistent - each invocation is independent.

CONTEXT:
- You have full read/write access to the Second Brain codebase at /home/debian/second_brain
- The primary Claude agent delegated this coding task to you
- After you complete your work, the primary agent will verify and may restart the server
- You can be technical in your responses - the primary agent understands code

YOUR ROLE:
- Focus on implementing the requested changes cleanly
- Read existing code to understand patterns before making changes
- Make minimal, focused changes that accomplish the goal
- Report what you changed and any important notes

The primary agent handles:
- Server restarts (via restart_server tool)
- Verification of your changes
- Follow-up adjustments if needed

Do your best work. Be thorough but concise in your final summary."""

    # Use the bundled claude binary from claude-agent-sdk
    claude_bin = "/home/debian/second_brain/interface/server/venv/lib/python3.11/site-packages/claude_agent_sdk/_bundled/claude"
    cmd = [
        claude_bin,
        "--print",
        "--dangerously-skip-permissions",
        "--model", model,
        "--setting-sources", "user",  # Skip project CLAUDE.md
        "--append-system-prompt", system_prompt,
        "--output-format", "text",
        prompt
    ]

    working_dir = "/home/debian/second_brain"

    try:
        print(f"[claude_code] Starting Claude Code with prompt: {prompt[:100]}...")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=working_dir,
            env={**os.environ}
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout_seconds
        )

        stdout_text = stdout.decode().strip()
        stderr_text = stderr.decode().strip()
        return_code = proc.returncode

        print(f"[claude_code] Completed with return code {return_code}")

        # Build result based on what we got
        result_parts = []

        # Check for errors first
        if return_code != 0:
            result_parts.append(f"**Claude Code exited with error (code {return_code})**\n")
            if stderr_text:
                result_parts.append(f"**stderr:**\n{stderr_text}\n")
            if stdout_text:
                result_parts.append(f"**stdout:**\n{stdout_text}")

            final_result = "\n".join(result_parts) if result_parts else f"Claude Code failed with exit code {return_code} but produced no output."
            return {"content": [{"type": "text", "text": final_result}], "is_error": True}

        # Success case - return stdout
        if stdout_text:
            return {"content": [{"type": "text", "text": stdout_text}]}

        # No stdout but process succeeded
        if stderr_text:
            return {"content": [{"type": "text", "text": f"Claude Code completed but wrote to stderr:\n{stderr_text}"}]}

        # Truly empty output
        return {"content": [{"type": "text", "text": "Claude Code completed successfully but produced no output. The task may have been a no-op or Claude may have determined no changes were needed."}]}

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except:
            pass
        return {
            "content": [{"type": "text", "text": f"Claude Code timed out after {timeout_seconds} seconds. The task may be too complex - try breaking it into smaller steps."}],
            "is_error": True
        }
    except Exception as e:
        import traceback
        return {
            "content": [{"type": "text", "text": f"Claude Code error: {str(e)}\n{traceback.format_exc()}"}],
            "is_error": True
        }
