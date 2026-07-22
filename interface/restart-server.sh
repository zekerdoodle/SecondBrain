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

# The Codex CLI is installed under nvm on this server, while this script can be
# launched from non-interactive processes that do not source nvm shell hooks.
export SECOND_BRAIN_CODEX_BIN="${SECOND_BRAIN_CODEX_BIN:-/home/debian/.nvm/versions/node/v22.17.1/bin/codex}"
export PATH="$(dirname "$SECOND_BRAIN_CODEX_BIN"):$PATH"

ensure_restart_attempt_id() {
    if [ -z "${SECOND_BRAIN_RESTART_ATTEMPT_ID:-}" ]; then
        if [ -r /proc/sys/kernel/random/uuid ]; then
            SECOND_BRAIN_RESTART_ATTEMPT_ID="$(cat /proc/sys/kernel/random/uuid)"
        elif command -v uuidgen >/dev/null 2>&1; then
            SECOND_BRAIN_RESTART_ATTEMPT_ID="$(uuidgen)"
        else
            SECOND_BRAIN_RESTART_ATTEMPT_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
        fi
        export SECOND_BRAIN_RESTART_ATTEMPT_ID
    fi
    export SECOND_BRAIN_RESTART_PROVENANCE_SOURCE="${SECOND_BRAIN_RESTART_PROVENANCE_SOURCE:-manual_or_unknown_shell}"
    export SECOND_BRAIN_RESTART_PROVENANCE_TRIGGER="${SECOND_BRAIN_RESTART_PROVENANCE_TRIGGER:-restart-server.sh}"
    export SECOND_BRAIN_RESTART_PROVENANCE_RESTART_SCRIPT="${SECOND_BRAIN_RESTART_PROVENANCE_RESTART_SCRIPT:-$SCRIPT_DIR/restart-server.sh}"
}

managed_load_active() {
    [ -n "${SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID:-}" ]
}

managed_load_helper() {
    local python_bin="${SECOND_BRAIN_RESTART_PROVENANCE_PYTHON:-$SCRIPT_DIR/server/venv/bin/python3}"
    if [ ! -x "$python_bin" ]; then
        python_bin="$(command -v python3 || true)"
    fi
    if [ -z "$python_bin" ]; then
        echo "ERROR: python3 unavailable for managed-load receipt update."
        return 1
    fi
    "$python_bin" "$SCRIPT_DIR/server/managed_load_operations.py" \
        --project-root "$SCRIPT_DIR/.." "$@"
}

managed_load_failure() {
    managed_load_active || return 0
    managed_load_helper failure \
        --operation-id "$SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID" \
        --attempt-id "$SECOND_BRAIN_RESTART_ATTEMPT_ID" \
        --phase "$1" --code "$2"
}

listening_pid() {
    fuser "$PORT/tcp" 2>/dev/null | awk '{print $1}'
}

process_alive() {
    [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null
}

close_exact_startup_lock_fd() {
    local fd_target
    fd_target="$(readlink "/proc/$$/fd/9" 2>/dev/null || true)"
    if [ "$fd_target" = "/tmp/second-brain-startup.lock" ]; then
        exec 9>&-
    fi
}

write_restart_provenance_intent() {
    ensure_restart_attempt_id
    echo "Restart provenance: attempt_id=$SECOND_BRAIN_RESTART_ATTEMPT_ID source=$SECOND_BRAIN_RESTART_PROVENANCE_SOURCE"

    local helper="$SCRIPT_DIR/server/restart_provenance.py"
    if [ ! -f "$helper" ]; then
        echo "WARNING: restart provenance helper missing: $helper"
        managed_load_active && return 1
        return 0
    fi

    local python_bin="${SECOND_BRAIN_RESTART_PROVENANCE_PYTHON:-$SCRIPT_DIR/server/venv/bin/python3}"
    if [ ! -x "$python_bin" ]; then
        python_bin="$(command -v python3 || true)"
    fi
    if [ -z "$python_bin" ]; then
        echo "WARNING: python3 unavailable; skipping restart provenance marker."
        managed_load_active && return 1
        return 0
    fi

    if ! "$python_bin" "$helper" write-intent \
        --project-root "$SCRIPT_DIR/.." \
        --attempt-id "$SECOND_BRAIN_RESTART_ATTEMPT_ID" \
        --source "$SECOND_BRAIN_RESTART_PROVENANCE_SOURCE" \
        --trigger "$SECOND_BRAIN_RESTART_PROVENANCE_TRIGGER" \
        --reason "${SECOND_BRAIN_RESTART_PROVENANCE_REASON:-}" \
        --script-pid "$$" \
        --script-ppid "$PPID" \
        --restart-script "$SECOND_BRAIN_RESTART_PROVENANCE_RESTART_SCRIPT" \
        --pre-restart-evidence-dir "${SECOND_BRAIN_RESTART_PROVENANCE_PRE_RESTART_EVIDENCE_DIR:-}" \
        --acceptance-mode "${SECOND_BRAIN_RESTART_PROVENANCE_ACCEPTANCE_MODE:-}" \
        --restart-consumer "${SECOND_BRAIN_RESTART_PROVENANCE_CONSUMER:-}" \
        --load-operation-id "${SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID:-}" \
        --source-fingerprint "${SECOND_BRAIN_MANAGED_LOAD_SOURCE_FINGERPRINT:-}" \
        --old-process-pid "${SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID:-}"; then
        if managed_load_active; then
            echo "ERROR: managed-load restart provenance marker write failed."
            return 1
        fi
        echo "WARNING: restart provenance marker write failed; continuing restart."
    fi
}

scrub_restart_provenance_env() {
    if managed_load_active; then
        # The replacement backend needs only this bounded operation proof. All
        # free-text provenance is still scrubbed before uvicorn starts.
        unset SECOND_BRAIN_RESTART_PROVENANCE_SOURCE \
            SECOND_BRAIN_RESTART_PROVENANCE_TRIGGER \
            SECOND_BRAIN_RESTART_PROVENANCE_REASON \
            SECOND_BRAIN_RESTART_PROVENANCE_RESTART_SCRIPT \
            SECOND_BRAIN_RESTART_PROVENANCE_PRE_RESTART_EVIDENCE_DIR \
            SECOND_BRAIN_RESTART_PROVENANCE_ACCEPTANCE_MODE \
            SECOND_BRAIN_RESTART_PROVENANCE_CONSUMER \
            SECOND_BRAIN_RESTART_PROVENANCE_PYTHON
        return
    fi
    unset SECOND_BRAIN_RESTART_ATTEMPT_ID \
        SECOND_BRAIN_RESTART_PROVENANCE_SOURCE \
        SECOND_BRAIN_RESTART_PROVENANCE_TRIGGER \
        SECOND_BRAIN_RESTART_PROVENANCE_REASON \
        SECOND_BRAIN_RESTART_PROVENANCE_RESTART_SCRIPT \
        SECOND_BRAIN_RESTART_PROVENANCE_PRE_RESTART_EVIDENCE_DIR \
        SECOND_BRAIN_RESTART_PROVENANCE_ACCEPTANCE_MODE \
        SECOND_BRAIN_RESTART_PROVENANCE_CONSUMER \
        SECOND_BRAIN_RESTART_PROVENANCE_PYTHON \
        SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID \
        SECOND_BRAIN_MANAGED_LOAD_SOURCE_FINGERPRINT \
        SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID
}

echo "Quick restart (skipping frontend build)..."

# Gracefully stop previous server (SIGTERM first so shutdown handler can save state)
if managed_load_active; then
    CURRENT_PID="$(listening_pid)"
    if [ -z "$CURRENT_PID" ] || [ "$CURRENT_PID" != "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" ]; then
        managed_load_failure pre_kill old_listener_mismatch || true
        echo "ERROR: managed-load old listener does not match accepted PID."
        exit 1
    fi
    if ! managed_load_helper script-start \
        --operation-id "$SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID" \
        --source-fingerprint "$SECOND_BRAIN_MANAGED_LOAD_SOURCE_FINGERPRINT" \
        --attempt-id "$SECOND_BRAIN_RESTART_ATTEMPT_ID" \
        --old-pid "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" \
        --script-pid "$$"; then
        echo "ERROR: managed-load script-start receipt failed; refusing process replacement."
        exit 1
    fi
fi
if ! write_restart_provenance_intent; then
    managed_load_failure pre_kill intent_write_failed || true
    echo "ERROR: managed-load intent could not be durably written; refusing process replacement."
    exit 1
fi
if managed_load_active && ! managed_load_helper pre-kill-check \
    --operation-id "$SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID" \
    --source-fingerprint "$SECOND_BRAIN_MANAGED_LOAD_SOURCE_FINGERPRINT" \
    --attempt-id "$SECOND_BRAIN_RESTART_ATTEMPT_ID" \
    --old-pid "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" \
    --script-pid "$$"; then
    managed_load_failure pre_kill mixed_generation_pre_kill_proof_missing || true
    echo "ERROR: managed-load mixed-generation evidence is insufficient; refusing kill."
    exit 1
fi
scrub_restart_provenance_env
if managed_load_active; then
    # Signal only the exact PID accepted by the handler. The old backend may
    # still be the previous code generation, so its shutdown hook is supporting
    # evidence only; exact PID absence is journaled by this script/new startup.
    if ! kill -TERM "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" 2>/dev/null \
        && process_alive "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID"; then
        managed_load_failure pre_kill old_process_term_signal_failed || true
        echo "ERROR: could not signal the exact accepted old backend PID."
        exit 1
    fi
    for _ in {1..20}; do
        process_alive "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" || break
        sleep 0.2
    done
    if process_alive "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID"; then
        kill -KILL "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" 2>/dev/null || true
        for _ in {1..10}; do
            process_alive "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" || break
            sleep 0.1
        done
    fi
    if process_alive "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID"; then
        managed_load_failure replacement old_process_exit_unproved || true
        echo "ERROR: accepted old backend PID still exists after bounded shutdown."
        exit 1
    fi
    if ! managed_load_helper old-process-exited \
        --operation-id "$SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID" \
        --source-fingerprint "$SECOND_BRAIN_MANAGED_LOAD_SOURCE_FINGERPRINT" \
        --attempt-id "$SECOND_BRAIN_RESTART_ATTEMPT_ID" \
        --old-pid "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID"; then
        # The new startup repeats this exact-PID absence observation before it
        # may record startup proof. Continue so one post-kill write failure does
        # not strand an otherwise successful first mixed-generation load.
        echo "WARNING: restart script could not journal old PID exit; startup must reconcile it."
    fi
else
    fuser -k -TERM $PORT/tcp 2>/dev/null
    sleep 2
    # Preserve established non-agent restart behavior.
    fuser -k $PORT/tcp 2>/dev/null
    sleep 0.5
fi

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
close_exact_startup_lock_fd
nohup setsid uvicorn main:app --host 0.0.0.0 --port $PORT > "$LOG_FILE" 2>&1 < /dev/null &
SERVER_PID=$!

# Wait for port to be open (max 10 seconds)
echo "Waiting for port $PORT..."
for i in {1..20}; do
    if fuser $PORT/tcp >/dev/null 2>&1; then
        LISTENER_PID="$(listening_pid)"
        if managed_load_active; then
            PROCESS_PROOF_OK=false
            # uvicorn can own the listening socket just before its ASGI startup
            # hook atomically records matching startup provenance. Join those
            # two facts with a short bounded retry; never infer proof from the
            # socket alone.
            for proof_try in {1..40}; do
                if managed_load_helper process-replaced \
                    --operation-id "$SECOND_BRAIN_MANAGED_LOAD_OPERATION_ID" \
                    --source-fingerprint "$SECOND_BRAIN_MANAGED_LOAD_SOURCE_FINGERPRINT" \
                    --attempt-id "$SECOND_BRAIN_RESTART_ATTEMPT_ID" \
                    --old-pid "$SECOND_BRAIN_MANAGED_LOAD_OLD_PROCESS_PID" \
                    --new-pid "$LISTENER_PID"; then
                    PROCESS_PROOF_OK=true
                    break
                fi
                sleep 0.1
            done
            if [ "$PROCESS_PROOF_OK" != true ]; then
                managed_load_failure replacement process_proof_mismatch || true
                echo "ERROR: backend is listening but managed-load process proof failed."
                exit 1
            fi
        fi
        echo "Server started successfully (PID: $LISTENER_PID)"
        exit 0
    fi
    sleep 0.5
done

echo "Warning: Server may not have started - check $LOG_FILE"
managed_load_failure replacement listener_timeout || true
exit 1
