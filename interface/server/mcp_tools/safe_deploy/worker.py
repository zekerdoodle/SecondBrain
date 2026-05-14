"""
Daemonized worker for apply_patch_deploy (Phase B).

This module is the entry point for the post-push pipeline. It runs in a
detached subprocess (spawned by `apply_patch_deploy.py` via
`subprocess.Popen(..., start_new_session=True)`) so it survives the
upcoming server restart.

Flow:

    1. Read context JSON written by the sync portion (branch, reason,
       caller, previous_main_sha, deployed_sha, decision_trail, ...).
    2. Trigger restart by invoking interface/restart-server.sh
       synchronously (it waits for port 8000 to open before returning).
    3. Poll the new server for /api/agents 200 (settle window).
    4. Run smoke in a scrubbed-env subprocess.
    5. If smoke passes:    write `deployed_smoke_passed` result, exit 0.
    6. If smoke fails:     run positive-filter rollback.
         - If rollback fails:        write `fatal_rollback_failed`, file
                                     deploy-failure log, exit 1.
         - If rollback succeeds:     trigger second restart, wait, run
                                     smoke again.
             - If re-smoke passes:   write `rolled_back`, file deploy-
                                     failure log, exit 0.
             - If re-smoke fails:    write `fatal_rollback_smoke_failed`,
                                     file deploy-failure log, exit 1.

    No looping. A failed rollback or failed re-smoke is terminal — the
    system stays in whatever state it's in and Patch triages on next wake.

Worker visibility:

    All progress is written to:
        .claude/apply_patch_result.json    — final state for callers
        .claude/logs/apply_patch_worker.log — line-buffered log
        codebase/meta/deploy-failures/<utc-ts>.md — on any failure path

    The worker NEVER prints to stdout/stderr (those are redirected to
    /dev/null at daemonize time). The log file is the only narration.

Restart consent boundary:

    The worker triggers two restarts (one for the new code, one for
    rollback). Both happen inside a deploy that the user explicitly consented
    to via the apply_patch_deploy MCP tool's consent prompt. The worker
    does NOT prompt — it's executing within the consent envelope already
    granted. (Per principles.md v3: consent is per-invocation of the
    deploy tool; the rollback restart is part of that same invocation.)

Run modes (test injection):

    The CLI entry point reads context from the file path given as argv[1].
    For isolation tests, `run_worker_flow()` is callable directly with
    injectable smoke_fn and rollback_fn, which makes the FATAL paths
    exercisable without an actual deploy.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


REPO_ROOT = Path("/home/debian/second_brain")
RESULT_FILE = REPO_ROOT / ".claude" / "apply_patch_result.json"
LOCK_FILE = REPO_ROOT / ".claude" / "apply_patch.lock"
WORKER_LOG = REPO_ROOT / ".claude" / "logs" / "apply_patch_worker.log"
DEPLOY_FAILURES_DIR = REPO_ROOT / "codebase" / "meta" / "deploy-failures"
RESTART_SCRIPT = REPO_ROOT / "interface" / "restart-server.sh"
RESTART_SETTLE_TIMEOUT_SEC = 25
PHASE_TAG = "phase-b"
SERVER_BASE = "http://localhost:8000"


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink()
        _log("lock released")
    except FileNotFoundError:
        _log("lock already gone at worker exit")
    except Exception as e:
        _log(f"lock release error (continuing): {e}")


# --------------------------------------------------------------------------- log helper


def _log(msg: str) -> None:
    """Append a timestamped line to the worker log."""
    try:
        WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(WORKER_LOG, "a") as f:
            f.write(f"{datetime.now(timezone.utc).isoformat()} {msg}\n")
    except Exception:
        pass


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")


# --------------------------------------------------------------------------- result / deploy-failure I/O


def write_result(payload: Dict[str, Any]) -> None:
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    _log(f"result written: state={payload.get('state')}")


def write_deploy_failure_log(payload: Dict[str, Any]) -> Path:
    """Write a deploy-failure markdown record. Returns the file path."""
    DEPLOY_FAILURES_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{_utc_ts()}.md"
    path = DEPLOY_FAILURES_DIR / fname
    state = payload.get("state", "unknown")
    branch = payload.get("branch", "?")
    attempted = payload.get("attempted_commit_sha", payload.get("deployed_sha", "?"))
    prev = payload.get("previous_main_sha", "?")
    reason = payload.get("reason", "?")
    smoke_dump = json.dumps(payload.get("smoke", {}), indent=2, sort_keys=True)
    rollback_smoke_dump = json.dumps(
        payload.get("rollback_smoke", {}), indent=2, sort_keys=True
    )
    rollback_detail = payload.get("rollback_detail", "(none)")
    rollback_changes = payload.get("rollback_changes", [])
    rollback_changes_md = (
        "\n".join(f"- `{s}` `{p}`" for s, p in rollback_changes) or "_(none)_"
    )
    decision_tree_steps = payload.get("decision_tree_steps", [])
    decision_tree_md = (
        "\n".join(f"- {s}" for s in decision_tree_steps) or "_(none)_"
    )
    body = f"""---
date: {payload.get("timestamp", _utc_iso())}
state: {state}
branch: {branch}
attempted_commit: {attempted}
previous_main_sha: {prev}
reason_given: {reason}
phase: {PHASE_TAG}
---

# Deploy failure — {branch}

## Final state

`{state}`

## Reason given

> {reason}

## Decision tree taken

{decision_tree_md}

## Initial smoke output

```json
{smoke_dump}
```

## Rollback detail

{rollback_detail}

## Rollback changes (per-file actions)

{rollback_changes_md}

## Rollback re-smoke output

```json
{rollback_smoke_dump}
```

## Patch actions

_(populated by Patch on next wake during triage)_
"""
    path.write_text(body)
    _log(f"deploy-failure log filed: {path}")
    return path


# --------------------------------------------------------------------------- restart + wait


def trigger_restart() -> bool:
    """Invoke the restart-server.sh script. Returns True iff it exits 0.

    The script kills port 8000, then starts the new server in the
    background (via nohup setsid + uvicorn) and waits for the port to
    open. We block on the script — it's fast (~5s on quick restart).

    Because this worker was daemonized with start_new_session=True, the
    port-8000 kill does NOT affect us (we're in our own session).
    """
    _log(f"trigger_restart: invoking {RESTART_SCRIPT}")
    try:
        r = subprocess.run(
            ["bash", str(RESTART_SCRIPT)],
            cwd=str(REPO_ROOT / "interface"),
            capture_output=True,
            text=True,
            timeout=60,
        )
    except subprocess.TimeoutExpired:
        _log("trigger_restart: bash restart-server.sh TIMED OUT")
        return False
    except Exception as e:
        _log(f"trigger_restart: bash launch error: {e}")
        return False

    _log(
        f"trigger_restart: bash returned {r.returncode}; "
        f"stdout_tail={r.stdout.strip()[-300:]!r}; "
        f"stderr_tail={r.stderr.strip()[-300:]!r}"
    )
    return r.returncode == 0


def wait_for_server_settled(timeout_sec: int = RESTART_SETTLE_TIMEOUT_SEC) -> bool:
    """Poll /api/agents until 200, up to timeout_sec."""
    # Defer import so worker can be loaded without smoke module errors
    from smoke import wait_for_server_up

    ok, elapsed = wait_for_server_up(SERVER_BASE, timeout_sec=timeout_sec)
    _log(
        f"wait_for_server_settled: ok={ok} after {elapsed:.1f}s "
        f"(deadline={timeout_sec}s)"
    )
    return ok


# --------------------------------------------------------------------------- worker flow


def _default_smoke_fn() -> Dict[str, Any]:
    from smoke import run_smoke_scrubbed

    return run_smoke_scrubbed(server_base=SERVER_BASE)


def _default_rollback_fn(
    previous_sha: str, attempted_sha: str, reason: str
) -> Dict[str, Any]:
    from rollback import perform_rollback

    ok, detail, changes = perform_rollback(
        previous_sha=previous_sha,
        attempted_sha=attempted_sha,
        reason=reason,
    )
    return {"ok": ok, "detail": detail, "changes": changes}


def run_worker_flow(
    ctx: Dict[str, Any],
    smoke_fn: Optional[Callable[[], Dict[str, Any]]] = None,
    rollback_fn: Optional[Callable[[str, str, str], Dict[str, Any]]] = None,
    restart_fn: Optional[Callable[[], bool]] = None,
    wait_fn: Optional[Callable[[], bool]] = None,
) -> int:
    """Execute the post-push pipeline. Returns process exit code.

    Args:
        ctx: parsed context dict from the worker context file.
        smoke_fn: callable returning a smoke result dict. Defaults to the
            scrubbed-env subprocess smoke runner. Override for tests.
        rollback_fn: callable(prev_sha, attempted_sha, reason) returning
            {"ok": bool, "detail": str, "changes": [(status, path), ...]}.
            Override for tests.
        restart_fn: callable returning True iff restart succeeded.
        wait_fn: callable returning True iff server came back up.
    """
    smoke_fn = smoke_fn or _default_smoke_fn
    rollback_fn = rollback_fn or _default_rollback_fn
    restart_fn = restart_fn or trigger_restart
    wait_fn = wait_fn or wait_for_server_settled

    decision_trail: List[str] = list(ctx.get("decision_tree_steps", []))

    def step(msg: str) -> None:
        decision_trail.append(msg)
        _log(f"step: {msg}")

    branch = ctx["branch"]
    reason = ctx["reason"]
    caller = ctx["caller"]
    previous_sha = ctx["previous_main_sha"]
    deployed_sha = ctx["deployed_sha"]
    files_changed = ctx.get("files_changed", [])

    base_payload = {
        "branch": branch,
        "reason": reason,
        "caller": caller,
        "previous_main_sha": previous_sha,
        "deployed_sha": deployed_sha,
        "attempted_commit_sha": deployed_sha,
        "files_changed": files_changed,
        "phase": PHASE_TAG,
    }

    # ---- 1. First restart ----------------------------------------------
    step("worker: triggering first restart (post-deploy)")
    restart_ok = restart_fn()
    if not restart_ok:
        step("first restart script exited nonzero — proceeding to server-wait anyway")

    # ---- 2. Wait for server ---------------------------------------------
    step(f"worker: waiting for server to settle (≤{RESTART_SETTLE_TIMEOUT_SEC}s)")
    server_up = wait_fn()
    if not server_up:
        step("server did NOT come up within settle window — treating as smoke failure")
        smoke = {
            "passed": False,
            "first_failure": "server_settle_timeout",
            "elapsed_sec": RESTART_SETTLE_TIMEOUT_SEC,
            "checks": [
                {
                    "name": "server_settle_timeout",
                    "ok": False,
                    "detail": (
                        f"port 8000 never returned 200 within "
                        f"{RESTART_SETTLE_TIMEOUT_SEC}s after first restart"
                    ),
                }
            ],
        }
    else:
        # ---- 3. Run smoke -----------------------------------------------
        step("worker: running smoke (scrubbed-env subprocess)")
        try:
            smoke = smoke_fn()
        except Exception as e:
            smoke = {
                "passed": False,
                "first_failure": "smoke_runner_exception",
                "elapsed_sec": 0,
                "checks": [
                    {
                        "name": "smoke_runner_exception",
                        "ok": False,
                        "detail": f"{type(e).__name__}: {e}",
                    }
                ],
            }

    # ---- 4. Green path --------------------------------------------------
    if smoke.get("passed"):
        step("smoke PASSED — deploy complete")
        write_result(
            {
                **base_payload,
                "state": "deployed_smoke_passed",
                "ok": True,
                "timestamp": _utc_iso(),
                "smoke_run": True,
                "smoke": smoke,
                "rollback_performed": False,
                "decision_tree_steps": decision_trail,
            }
        )
        return 0

    # ---- 5. Rollback path -----------------------------------------------
    step(
        f"smoke FAILED (first_failure={smoke.get('first_failure')}); "
        "executing positive-filter rollback"
    )
    try:
        rollback = rollback_fn(previous_sha, deployed_sha, reason)
    except Exception as e:
        rollback = {
            "ok": False,
            "detail": f"rollback runner exception: {type(e).__name__}: {e}",
            "changes": [],
        }

    if not rollback.get("ok"):
        step(f"ROLLBACK MACHINERY FAILED: {rollback.get('detail')}")
        payload = {
            **base_payload,
            "state": "fatal_rollback_failed",
            "ok": False,
            "timestamp": _utc_iso(),
            "smoke_run": True,
            "smoke": smoke,
            "rollback_performed": False,
            "rollback_detail": rollback.get("detail", ""),
            "rollback_changes": rollback.get("changes", []),
            "decision_tree_steps": decision_trail,
        }
        write_result(payload)
        write_deploy_failure_log(payload)
        return 1

    step(
        f"rollback OK: {rollback.get('detail')} — "
        "re-spawning restart on the rolled-back tree"
    )

    # ---- 6. Second restart ---------------------------------------------
    restart2_ok = restart_fn()
    if not restart2_ok:
        step("second restart script exited nonzero — proceeding to server-wait anyway")
    server_up2 = wait_fn()
    if not server_up2:
        step("rollback's restart did NOT come up — FATAL")
        rollback_smoke = {
            "passed": False,
            "first_failure": "server_settle_timeout",
            "elapsed_sec": RESTART_SETTLE_TIMEOUT_SEC,
            "checks": [
                {
                    "name": "server_settle_timeout",
                    "ok": False,
                    "detail": (
                        f"rolled-back server didn't return 200 within "
                        f"{RESTART_SETTLE_TIMEOUT_SEC}s"
                    ),
                }
            ],
        }
    else:
        step("worker: running rollback re-smoke")
        try:
            rollback_smoke = smoke_fn()
        except Exception as e:
            rollback_smoke = {
                "passed": False,
                "first_failure": "rollback_smoke_runner_exception",
                "elapsed_sec": 0,
                "checks": [
                    {
                        "name": "rollback_smoke_runner_exception",
                        "ok": False,
                        "detail": f"{type(e).__name__}: {e}",
                    }
                ],
            }

    if rollback_smoke.get("passed"):
        step("rollback re-smoke PASSED — system safe on previous-known-good code")
        payload = {
            **base_payload,
            "state": "rolled_back",
            "ok": True,
            "timestamp": _utc_iso(),
            "smoke_run": True,
            "smoke": smoke,
            "rollback_performed": True,
            "rollback_detail": rollback.get("detail", ""),
            "rollback_changes": rollback.get("changes", []),
            "rollback_smoke": rollback_smoke,
            "decision_tree_steps": decision_trail,
        }
        write_result(payload)
        write_deploy_failure_log(payload)
        return 0  # rollback success: deploy failed but system is safe

    # rollback re-smoke also failed — terminal.
    step("rollback re-smoke FAILED — FATAL, no further attempts")
    payload = {
        **base_payload,
        "state": "fatal_rollback_smoke_failed",
        "ok": False,
        "timestamp": _utc_iso(),
        "smoke_run": True,
        "smoke": smoke,
        "rollback_performed": True,
        "rollback_detail": rollback.get("detail", ""),
        "rollback_changes": rollback.get("changes", []),
        "rollback_smoke": rollback_smoke,
        "decision_tree_steps": decision_trail,
    }
    write_result(payload)
    write_deploy_failure_log(payload)
    return 1


# --------------------------------------------------------------------------- CLI entry


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        _log("worker started with no context path — refusing to run")
        sys.stderr.write("worker.py: usage: worker.py <context_file>\n")
        return 2
    ctx_path = Path(argv[1])
    if not ctx_path.exists():
        _log(f"worker context file missing: {ctx_path}")
        sys.stderr.write(f"worker.py: context file missing: {ctx_path}\n")
        return 2

    try:
        ctx = json.loads(ctx_path.read_text())
    except Exception as e:
        _log(f"worker context unreadable: {e}")
        return 2

    _log(f"worker started (pid {os.getpid()}); ctx={ctx_path}")
    try:
        rc = run_worker_flow(ctx)
        _log(f"worker exiting rc={rc}")
        return rc
    except Exception as e:
        _log(f"worker unhandled exception: {e}\n{traceback.format_exc()}")
        # Write a FATAL result so the caller doesn't see a stale
        # deploy_in_progress.
        try:
            write_result(
                {
                    "state": "fatal_worker_crashed",
                    "ok": False,
                    "branch": ctx.get("branch", "?"),
                    "reason": ctx.get("reason", "?"),
                    "caller": ctx.get("caller", "?"),
                    "previous_main_sha": ctx.get("previous_main_sha", "?"),
                    "deployed_sha": ctx.get("deployed_sha", "?"),
                    "error": f"{type(e).__name__}: {e}",
                    "traceback": traceback.format_exc(),
                    "phase": PHASE_TAG,
                    "timestamp": _utc_iso(),
                }
            )
        except Exception:
            pass
        return 1
    finally:
        # Clean up the context file so it doesn't linger.
        try:
            ctx_path.unlink()
        except Exception:
            pass
        # Release the lock that the sync portion handed off to us.
        _release_lock()


if __name__ == "__main__":
    # Add own dir to sys.path so plain `from smoke import ...` works when
    # invoked as a script via `python worker.py <ctx>`.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main(sys.argv))
