#!/usr/bin/env python3
"""
Restart Tool for Second Brain Server

This script handles graceful server restarts while preserving conversation continuity.
It's designed to be called by Claude via the MCP tool.

Flow:
1. Save current conversation state + continuation marker
2. Stop server gracefully (SIGTERM)
3. Wait for server to stop
4. Start server in background
5. Server detects continuation marker on startup and auto-continues

Usage:
    python restart_tool.py <session_id> [reason]
"""

import os
import sys
import json
import time
import signal
import subprocess
from pathlib import Path
from datetime import datetime

# Paths
SECOND_BRAIN_ROOT = Path(__file__).parent.parent.parent
CLAUDE_DIR = SECOND_BRAIN_ROOT / ".claude"
CHATS_DIR = CLAUDE_DIR / "chats"
RESTART_MARKER = CLAUDE_DIR / "restart_continuation.json"
SERVER_DIR = SECOND_BRAIN_ROOT / "interface" / "server"
START_SCRIPT = SECOND_BRAIN_ROOT / "interface" / "start.sh"
QUICK_RESTART_SCRIPT = SECOND_BRAIN_ROOT / "interface" / "restart-server.sh"
FULL_RESTART_SCRIPT = SECOND_BRAIN_ROOT / "interface" / "restart-server-full.sh"


def find_server_pid():
    """Find the uvicorn/python server process PID."""
    try:
        # Look for the main.py process
        result = subprocess.run(
            ["pgrep", "-f", "python.*main.py"],
            capture_output=True,
            text=True
        )
        pids = result.stdout.strip().split('\n')
        pids = [p for p in pids if p]
        return int(pids[0]) if pids else None
    except Exception:
        return None


def find_port_pid(port):
    """Find process using a specific port."""
    try:
        result = subprocess.run(
            ["fuser", f"{port}/tcp"],
            capture_output=True,
            text=True,
            stderr=subprocess.STDOUT
        )
        # fuser outputs like "8765/tcp:   12345"
        output = result.stdout + result.stderr
        pids = [p.strip() for p in output.split() if p.strip().isdigit()]
        return int(pids[0]) if pids else None
    except Exception:
        return None


def save_continuation_state(
    session_id: str,
    reason: str = None,
    source: str = None,
    messages: list = None,
    all_active_sessions: dict = None
):
    """Save continuation marker for post-restart resumption.

    Supports multi-session continuation: if other sessions were actively processing
    when the restart was triggered, they'll also be woken up after restart.

    Args:
        session_id: The session that triggered the restart.
        reason: Why the restart was triggered.
        source: Who/what triggered it (e.g. 'character', 'patch', 'settings_ui').
        messages: Optional messages for the triggering session.
        all_active_sessions: Optional dict of {session_id: agent_name} for ALL
            sessions that were actively processing at restart time.
    """
    reason = reason or "Server restart requested"
    source = source or "unknown"

    # Build the sessions list
    sessions = []

    # Determine the triggering session's agent name
    # Use all_active_sessions lookup if available, fall back to source
    trigger_agent = source
    if all_active_sessions and session_id in all_active_sessions:
        trigger_agent = all_active_sessions[session_id]

    # For external restarts (Settings UI, shutdown handler), ALL sessions are bystanders
    # (no specific session triggered the restart)
    is_external_trigger = source in ("settings_ui", "shutdown_handler")

    # The triggering session is first (unless this is an external trigger like Settings UI)
    triggering_msg_count = 0
    if messages is not None:
        triggering_msg_count = len(messages)
    else:
        chat_file = CHATS_DIR / f"{session_id}.json"
        if chat_file.exists():
            with open(chat_file) as f:
                chat_data = json.load(f)
                triggering_msg_count = len(chat_data.get("messages", []))

    sessions.append({
        "session_id": session_id,
        "agent": trigger_agent,
        "role": "bystander" if is_external_trigger else "trigger",
        "message_count": triggering_msg_count,
    })

    # Add remaining sessions (actively processing but didn't trigger the restart)
    if all_active_sessions:
        for sid, agent in all_active_sessions.items():
            if sid == session_id:
                continue  # Already added above
            bystander_msg_count = 0
            chat_file = CHATS_DIR / f"{sid}.json"
            if chat_file.exists():
                try:
                    with open(chat_file) as f:
                        chat_data = json.load(f)
                        bystander_msg_count = len(chat_data.get("messages", []))
                except Exception:
                    pass

            sessions.append({
                "session_id": sid,
                "agent": agent or "character",
                "role": "bystander",  # Was actively processing but didn't trigger restart
                "message_count": bystander_msg_count,
            })

    continuation = {
        "restart_time": datetime.now().isoformat(),
        "reason": reason,
        "source": source,
        "sessions": sessions,
        # Legacy field for backwards compat
        "session_id": session_id,
        "continuation_prompt": (
            "Restart completed successfully. "
            "Please continue from where you left off. "
            "If you were testing a change, verify it now."
        )
    }

    RESTART_MARKER.write_text(json.dumps(continuation, indent=2))
    return continuation


def stop_server():
    """Stop the server gracefully."""
    # Try to find by process name first
    pid = find_server_pid()

    # Fallback to port
    if not pid:
        pid = find_port_pid(8000)

    if not pid:
        print("Server not running")
        return True

    print(f"Stopping server (PID {pid})...")

    try:
        # Send SIGTERM for graceful shutdown
        os.kill(pid, signal.SIGTERM)

        # Wait for process to die (up to 10 seconds)
        for _ in range(20):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)  # Check if still alive
            except ProcessLookupError:
                print("Server stopped")
                return True

        # Force kill if still alive
        print("Force killing server...")
        os.kill(pid, signal.SIGKILL)
        time.sleep(1)
        return True

    except ProcessLookupError:
        print("Server already stopped")
        return True
    except Exception as e:
        print(f"Error stopping server: {e}")
        return False


def start_server(quick=True):
    """Start the server in background.

    Args:
        quick: If True, use quick restart (skip frontend build).
               If False, do full start with frontend build.
    """
    print("Starting server...")

    # Change to interface directory
    os.chdir(SECOND_BRAIN_ROOT / "interface")

    # Use nohup to detach from this process
    # Redirect output to a log file
    log_file = CLAUDE_DIR / "server_restart.log"

    if quick and QUICK_RESTART_SCRIPT.exists():
        # Quick restart - just restart the Python server (faster)
        # The script waits for the port to be open before returning
        print("Using quick restart (skipping frontend build)...")
        result = subprocess.run(
            f"bash {QUICK_RESTART_SCRIPT}",
            shell=True,
            capture_output=True,
            text=True
        )

        # Write output to log file
        with open(log_file, 'w') as f:
            f.write(result.stdout)
            if result.stderr:
                f.write(result.stderr)

        if result.returncode == 0:
            pid = find_port_pid(8000)
            print(f"Server started successfully (PID {pid})")
            return True
        else:
            print(f"Quick restart failed: {result.stderr}")
            return False
    else:
        # Full restart with frontend build (slower)
        print("Using full restart (includes frontend build)...")
        cmd = f"nohup bash {START_SCRIPT} production > {log_file} 2>&1 &"
        subprocess.run(cmd, shell=True)

        # Wait for server to start (full build takes longer)
        print("Waiting for server to start (max 60s)...")
        for i in range(120):
            time.sleep(0.5)
            pid = find_port_pid(8000)
            if pid:
                print(f"Server started (PID {pid})")
                return True

        print("Warning: Server may not have started - check logs")
        return False


def restart_server(session_id: str, reason: str = None, messages: list = None):
    """
    Main restart function.

    Args:
        session_id: The chat session to continue after restart
        reason: Optional reason for the restart
        messages: Optional list of messages to preserve (if not saved yet)

    Returns:
        dict with status and details
    """
    result = {
        "success": False,
        "session_id": session_id,
        "reason": reason,
        "steps": []
    }

    # Step 1: Save continuation state
    try:
        continuation = save_continuation_state(session_id, reason, messages)
        result["steps"].append(f"Saved continuation state for session {session_id}")
    except Exception as e:
        result["steps"].append(f"Warning: Could not save continuation: {e}")

    # Step 2: Stop server
    if stop_server():
        result["steps"].append("Server stopped gracefully")
    else:
        result["steps"].append("Warning: Server stop may have failed")

    # Step 3: Start server
    if start_server():
        result["steps"].append("Server started successfully")
        result["success"] = True
    else:
        result["steps"].append("Warning: Server start may have failed")

    return result


# Entry point for CLI usage
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: restart_tool.py <session_id> [reason]")
        sys.exit(1)

    session_id = sys.argv[1]
    reason = sys.argv[2] if len(sys.argv) > 2 else None

    result = restart_server(session_id, reason)

    print("\n=== Restart Result ===")
    for step in result["steps"]:
        print(f"  - {step}")
    print(f"\nSuccess: {result['success']}")
