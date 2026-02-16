#!/bin/bash
# Startup script for Second Brain - runs on boot
# Starts cloudflared tunnel and the interface server

LOG_FILE="/home/debian/second_brain/.claude/startup.log"
INTERFACE_DIR="/home/debian/second_brain/interface"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

log "=== System startup initiated ==="

# Prevent nested-session detection if startup inherits Claude Code env vars
unset CLAUDECODE CLAUDE_CODE_ENTRYPOINT CLAUDE_AGENT_SDK_VERSION

# Start cloudflared tunnel in background
log "Starting cloudflared tunnel..."
nohup cloudflared tunnel run theo-tunnel >> "$LOG_FILE" 2>&1 &
TUNNEL_PID=$!
log "Cloudflared started (PID: $TUNNEL_PID)"

# Wait a moment for tunnel to initialize
sleep 3

# Start the interface server (production mode, no agent call)
log "Starting interface server..."
cd "$INTERFACE_DIR"

# Kill any existing server on port 8000
fuser -k 8000/tcp 2>/dev/null
sleep 1

# Start server in background (redirect to interface.log)
cd server
if [ ! -d "venv" ]; then
    log "Creating Python venv..."
    python3 -m venv venv
fi
source venv/bin/activate

# Install deps quietly if needed
pip install -q fastapi uvicorn websockets anthropic 2>/dev/null

log "Launching main.py..."
nohup python3 main.py >> "$INTERFACE_DIR/interface.log" 2>&1 &
SERVER_PID=$!
log "Server started (PID: $SERVER_PID)"

log "=== Startup complete ==="
log "  Tunnel PID: $TUNNEL_PID"
log "  Server PID: $SERVER_PID"
