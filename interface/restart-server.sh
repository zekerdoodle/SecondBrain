#!/bin/bash
# Quick server restart - skips frontend build (assumes it's already built)
# Use this for code-only changes that don't touch the frontend

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="/tmp/server.log"
cd "$SCRIPT_DIR"

# Port the server runs on
PORT=8000

echo "Quick restart (skipping frontend build)..."

# Kill previous server
fuser -k $PORT/tcp 2>/dev/null
sleep 1

# Start server
cd server
if [ ! -d "venv" ]; then
    echo "Creating venv..."
    python3 -m venv venv
fi

source venv/bin/activate
pip install -q fastapi uvicorn websockets claude-agent-sdk 2>/dev/null

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
