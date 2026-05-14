#!/bin/bash
# Quick server restart - skips frontend build (assumes it's already built)
# Use this for code-only changes that don't touch the frontend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/tmp/server.log"
cd "$SCRIPT_DIR"

# Port the server runs on
PORT=8000

# Prevent nested-session detection when restarted from inside Claude Code
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_AGENT_SDK_VERSION

# Strip apply_patch test-only env vars so they cannot survive a restart.
# These were introduced during Phase 3 testing (force-fail smoke tests) and
# leaked into the server process's ancestry, causing every subsequent deploy
# to roll back. Unset defensively — they should NEVER be live in normal ops.
unset APPLY_PATCH_FORCE_SMOKE_FAIL APPLY_PATCH_FORCE_SMOKE_FAIL_ALWAYS

# Disable tool search/deferral — load all tools upfront
export ENABLE_TOOL_SEARCH=false

echo "Quick restart (skipping frontend build)..."

# Gracefully stop previous server (SIGTERM first so shutdown handler can save state)
fuser -k -TERM $PORT/tcp 2>/dev/null
sleep 2
# Force kill if still running
fuser -k $PORT/tcp 2>/dev/null
sleep 0.5

# Start server
cd server
if [ ! -d "venv" ]; then
    echo "Creating venv..."
    python3 -m venv venv
fi

source venv/bin/activate
# Honor the canonical dependency list at the repo root. Fast when everything
# is already installed (pip just verifies); slow only when something new
# needs to land. Wired up by apply_patch Phase 3 (replaced the hardcoded
# 4-package list that ignored requirements.txt).
REQS="$SCRIPT_DIR/../requirements.txt"
if [ -f "$REQS" ]; then
    pip install -q -r "$REQS" 2>/dev/null
else
    echo "WARNING: $REQS missing; falling back to hardcoded core packages"
    pip install -q fastapi uvicorn websockets claude-agent-sdk 2>/dev/null
fi

echo "Starting server on port $PORT..."

# Run in background with proper daemonization - use uvicorn directly
nohup setsid uvicorn main:app --host 0.0.0.0 --port $PORT > "$LOG_FILE" 2>&1 < /dev/null &
SERVER_PID=$!

# Wait for port to be open (max 10 seconds)
echo "Waiting for port $PORT..."
for i in {1..20}; do
    if fuser $PORT/tcp >/dev/null 2>&1; then
        echo "Server started successfully (PID: $(fuser $PORT/tcp 2>/dev/null))"
        exit 0
    fi
    sleep 0.5
done

echo "Warning: Server may not have started - check $LOG_FILE"
exit 1
