"""
Bash execution tool for Claude agents.

Provides controlled shell access with:
- Blocklist-based security (permissive by default)
- Sudo allowlist for common system operations
- Environment variable filtering
- UTF-8-byte-budgeted model output
- Atomic full-output receipts when truncated or failed
"""

import asyncio
import os
import re
import shlex
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from claude_agent_sdk import tool

from tool_output_artifacts import (
    MODEL_VISIBLE_TOOL_OUTPUT_LIMIT_BYTES,
    REPO_ROOT,
    format_model_visible_tool_output,
)

from ..registry import register_tool

# =============================================================================
# Security Configuration
# =============================================================================

# Blocklist patterns - block catastrophic/irreversible operations
BLOCKLIST_PATTERNS = [
    # System shutdown/reboot
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r"\bhalt\b",
    r"\binit\s+(0|6)\b",
    r"systemctl\s+(poweroff|reboot|halt)\b",

    # Destructive filesystem operations
    r"rm\s+-rf\s+/(\s|$)",           # rm -rf /
    r"rm\s+-rf\s+\*/?\s*$",          # rm -rf *
    r"mkfs\w*\b",                     # Format filesystems
    r"dd\s+if=/dev/zero\s+of=/dev/sd\w+",  # Wipe disks

    # Fork bombs and system destabilization
    r":\(\)\{\s*:\|:&\s*\};:",       # Classic fork bomb

    # Kernel module manipulation
    r"\b(insmod|rmmod|modprobe)\b",

    # Catastrophic permission changes
    r"chown\s+-R\s+/\s*$",           # chown -R /
    r"chmod\s+-R\s+0{3}\s+/",        # chmod -R 000 /
]

# Sudo allowlist - commands that can be run with sudo
SUDO_ALLOWLIST = [
    # Service management
    "systemctl",
    "service",
    # Container operations
    "docker",
    "docker-compose",
    "podman",
    # Package management
    "apt-get",
    "apt-cache",
    "apt",
    "dpkg",
    # File operations with elevated privileges
    "tee",
    "chown",
    "chmod",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "ln",
    "touch",
    # Filesystem
    "mount",
    "umount",
    # Python packages
    "pip",
    "pip3",
    # System info/logs
    "journalctl",
    "dmesg",
    # Network
    "netstat",
    "ss",
    "lsof",
    "iptables",
    "ufw",
    # Process management
    "kill",
    "pkill",
    "killall",
    # User management
    "useradd",
    "usermod",
    "groupadd",
]

# Environment variable allowlist patterns
ENV_ALLOWLIST_PATTERNS = [
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "LANG",
    "LC_*",
    "PYTHON*",
    "VIRTUAL_ENV",
    "NODE_*",
    "NPM_*",
    "DEBIAN_FRONTEND",
    "SECOND_BRAIN_*",
    "TERM",
    "COLORTERM",
    "DISPLAY",
    "XDG_*",
    "EDITOR",
    "VISUAL",
    "PAGER",
    "TZ",
]

# Output configuration
MAX_MODEL_VISIBLE_OUTPUT_BYTES = MODEL_VISIBLE_TOOL_OUTPUT_LIMIT_BYTES
DEFAULT_CWD = str(REPO_ROOT)
DEFAULT_TIMEOUT = 120
MAX_TIMEOUT = 600


# =============================================================================
# Helper Functions
# =============================================================================

def _is_blocked(command: str) -> Optional[str]:
    """Check if command matches any blocklist pattern. Returns pattern if blocked."""
    for pattern in BLOCKLIST_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return pattern
    return None


def _parse_first_program(cmd_segment: str) -> Tuple[bool, str]:
    """Parse a command segment to find the first program.

    Returns (uses_sudo, program_name)
    """
    try:
        tokens = shlex.split(cmd_segment)
    except ValueError:
        tokens = cmd_segment.split()

    if not tokens:
        return False, ""

    uses_sudo = tokens[0] == "sudo"
    if uses_sudo and len(tokens) > 1:
        # Skip sudo flags like -u, -E, etc.
        i = 1
        while i < len(tokens) and tokens[i].startswith("-"):
            i += 1
            # Handle flags with arguments like -u root
            if i < len(tokens) and not tokens[i].startswith("-"):
                i += 1
        if i < len(tokens):
            return True, tokens[i]
        return True, ""

    return False, tokens[0]


def _validate_sudo(command: str) -> Tuple[bool, str]:
    """Validate sudo usage in command.

    Returns (allowed, error_message)
    """
    # Split by command separators to check each part
    segments = re.split(r'[;&|]+', command)

    for segment in segments:
        segment = segment.strip()
        if not segment:
            continue

        # Check each pipe stage
        for pipe_part in segment.split("|"):
            pipe_part = pipe_part.strip()
            uses_sudo, program = _parse_first_program(pipe_part)

            if uses_sudo:
                if not program:
                    return False, "sudo without command"
                if program not in SUDO_ALLOWLIST:
                    return False, f"sudo not allowed for '{program}'"

    return True, ""


def _env_matches_pattern(key: str, pattern: str) -> bool:
    """Check if environment variable key matches a pattern."""
    if pattern.endswith("*"):
        return key.startswith(pattern[:-1])
    return key == pattern


def _filter_env(env: Dict[str, str]) -> Dict[str, str]:
    """Filter environment variables by allowlist."""
    filtered = {}
    for key, value in env.items():
        for pattern in ENV_ALLOWLIST_PATTERNS:
            if _env_matches_pattern(key, pattern):
                filtered[key] = value
                break
    return filtered


def _tool_result(args: Dict[str, Any], text: str, *, is_error: bool) -> Dict[str, Any]:
    """Apply the shared provider-facing receipt/budget contract."""
    bounded = format_model_visible_tool_output(
        chat_id=args.get("_source_chat_id") or "unknown-chat",
        tool_call_id=args.get("_tool_call_id") or f"bash-{uuid.uuid4().hex}",
        tool_name="bash",
        output=text,
        is_error=is_error,
    )
    return {
        "content": [{"type": "text", "text": bounded}],
        "is_error": is_error,
    }


# =============================================================================
# Main Tool
# =============================================================================

@register_tool("bash")
@tool(
    name="bash",
    description="""Execute bash commands with controlled permissions.

Supports:
- Standard shell commands with pipes, redirects, and chaining (&&, ||, ;)
- Sudo for system operations: systemctl, docker, apt-get, tee, chmod, etc.
- Environment variable injection (filtered by allowlist)
- Custom working directory
- Stdin input piping
- Configurable timeout (max 10 minutes)

Blocked by security policy:
- System shutdown/reboot commands
- Destructive operations: rm -rf /, mkfs, dd to /dev/*
- Fork bombs
- Kernel module manipulation

Output:
- Complete when the full model-visible result fits within 32 KiB UTF-8
- Larger results use a bounded head/tail view and an atomic receipt under .claude/tool_outputs/
- Failed commands also receive a durable receipt; the receipt pointer is the final line

Examples:
- "ls -la /home/debian"
- "sudo systemctl status nginx"
- "cat file.txt | grep pattern"
- "sudo apt-get update && sudo apt-get install -y package"
- "docker ps -a"
""",
    input_schema={
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The bash command to execute"
            },
            "timeout_seconds": {
                "type": "integer",
                "description": f"Maximum execution time in seconds (default: {DEFAULT_TIMEOUT}, max: {MAX_TIMEOUT})",
                "default": DEFAULT_TIMEOUT
            },
            "cwd": {
                "type": "string",
                "description": f"Working directory (default: {DEFAULT_CWD})"
            },
            "stdin": {
                "type": "string",
                "description": "Optional stdin input to pipe to the command"
            },
            "env": {
                "type": "object",
                "description": "Additional environment variables (filtered by allowlist)",
                "additionalProperties": {"type": "string"}
            }
        },
        "required": ["command"]
    }
)
async def bash(args: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a bash command with security controls."""
    command = args.get("command", "")
    timeout = min(args.get("timeout_seconds", DEFAULT_TIMEOUT), MAX_TIMEOUT)
    cwd = args.get("cwd", DEFAULT_CWD)
    stdin = args.get("stdin")
    env_override = args.get("env", {})

    # Validate command is not empty
    if not command.strip():
        return _tool_result(args, "Error: empty command", is_error=True)

    # Security check: blocklist
    blocked_pattern = _is_blocked(command)
    if blocked_pattern:
        return _tool_result(
            args,
            f"Blocked by security policy (matched pattern: {blocked_pattern})",
            is_error=True,
        )

    # Security check: sudo validation
    if "sudo" in command:
        allowed, error = _validate_sudo(command)
        if not allowed:
            return _tool_result(args, f"Sudo not allowed: {error}", is_error=True)

    # Prepare environment
    base_env = dict(os.environ)
    base_env.update(env_override)
    run_env = _filter_env(base_env)

    # Ensure essential variables
    run_env["DEBIAN_FRONTEND"] = "noninteractive"
    run_env["PATH"] = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    run_env["HOME"] = os.environ.get("HOME", "/home/debian")

    # Validate working directory
    cwd_path = Path(cwd)
    if not cwd_path.exists():
        cwd_path.mkdir(parents=True, exist_ok=True)

    # Execute command
    try:
        start = time.time()

        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE if stdin else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(cwd_path),
            env=run_env,
            executable="/bin/bash"
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(stdin.encode("utf-8") if stdin else None),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return _tool_result(
                args,
                f"Command timed out after {timeout}s",
                is_error=True,
            )

        duration_ms = int((time.time() - start) * 1000)

        # Decode output
        stdout_text = stdout.decode("utf-8", errors="replace")
        stderr_text = stderr.decode("utf-8", errors="replace")

        # Combine output
        full_output = stdout_text
        if stderr_text.strip():
            if full_output.strip():
                full_output += "\n\n[stderr]\n" + stderr_text
            else:
                full_output = "[stderr]\n" + stderr_text

        # Build result
        status = "PASS" if proc.returncode == 0 else "FAIL"
        summary = f"{status} (exit {proc.returncode}), {duration_ms}ms"
        result = f"{summary}\n\n{full_output}"
        return _tool_result(args, result, is_error=proc.returncode != 0)

    except Exception as e:
        return _tool_result(args, f"Execution error: {str(e)}", is_error=True)
