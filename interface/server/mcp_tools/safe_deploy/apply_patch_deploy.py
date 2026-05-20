"""
apply_patch_deploy — the MCP tool that replaces the disabled bash apply_patch.

Phase A scope (synchronous portion — implemented below):
    Caller-restriction, lock, scope check, fetch, merge --no-ff, push.

Phase B scope (this file's additions, marked PHASE B inline):
    After the synchronous portion succeeds, a daemonized worker is spawned
    to own the post-push pipeline: restart → wait → smoke → maybe rollback
    → maybe second restart → maybe re-smoke. The synchronous caller
    returns immediately with state="deploy_in_progress"; the worker
    overwrites the result file with the final state when it completes.

    Phase B flips `skip_restart` default to False — the real intended use
    is the full pipeline. `skip_restart=True` is preserved as a debug
    branch (merge+push only, no daemonization, returns the existing
    "merged_pushed_skipped_restart" state).

Architectural invariants (from codebase/projects/active/apply-patch-mcp-plan.md):

    - The tool refuses to run if the calling agent is not `patch`. The
      consent prompt is a separate layer (legacy MCP permission
      system); this in-tool check is defense in depth.

    - The deploy artifact is the `previous_main_sha` recorded in the
      result file. There is no full-tree backup tag. Rollback is
      positive-filter `git checkout <previous_main_sha> -- <path>` per
      scope path, followed by a forward commit; live state is structurally
      absent from that pathspec and cannot be touched. See rollback.py.

    - The manifest's `include:` and `exclude:` lists are the shared
      deploy/rollback scope gate. Deploy accepts only branch-diff paths
      that match `include:` and do not match `exclude:`; rollback uses the
      same positive scope with `exclude:` appended as negative pathspecs.

    - Smoke runs in a scrubbed-env subprocess with FILE-MARKER failure
      injection (no env-var injection — that's the 2026-05-12 bug that
      caused state loss). See smoke.py.

Audience: Patch invokes this tool. Coder agents do not.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from claude_agent_sdk import tool

from ..registry import register_tool
from .manifest import (
    MANIFEST_PATH,
    REPO_ROOT,
    ManifestError,
    include_check,
    load_exclude_spec,
    load_include_spec,
    scope_check,
    working_tree_clean_for_deploy,
)


LOCK_FILE = REPO_ROOT / ".claude" / "apply_patch.lock"
LOCK_MAX_AGE_MIN = 30
RESULT_FILE = REPO_ROOT / ".claude" / "apply_patch_result.json"
WORKER_CONTEXT_FILE = REPO_ROOT / ".claude" / "apply_patch_worker_context.json"
WORKER_LOG = REPO_ROOT / ".claude" / "logs" / "apply_patch_worker.log"
WORKER_SCRIPT = Path(__file__).resolve().parent / "worker.py"
BRANCH_RE = re.compile(r"^[A-Za-z0-9._/-]+$")
PHASE_TAG = "phase-b"


class ApplyPatchError(RuntimeError):
    """Anything that aborts the synchronous flow with a structured error."""


# --------------------------------------------------------------------------- utility


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_git(
    args: List[str], check: bool = True, capture: bool = True
) -> subprocess.CompletedProcess:
    """Run a git command rooted at REPO_ROOT. Returns CompletedProcess."""
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=capture,
        text=True,
        check=False,
        env=os.environ.copy(),
    )


def _git_or_raise(args: List[str]) -> subprocess.CompletedProcess:
    """Run git; raise ApplyPatchError on nonzero exit."""
    r = _run_git(args)
    if r.returncode != 0:
        raise ApplyPatchError(
            f"git {' '.join(args)} failed (exit {r.returncode})"
            + (f": {r.stderr.strip()}" if r.stderr else "")
        )
    return r


# --------------------------------------------------------------------------- lock


def acquire_lock() -> None:
    """Atomic O_CREAT|O_EXCL. Stale locks (>30min) are reclaimed.

    Ported from the disabled apply_patch.py — same semantics so multiple
    deploys cannot interleave.
    """
    if LOCK_FILE.exists():
        age_min = (time.time() - LOCK_FILE.stat().st_mtime) / 60
        if age_min < LOCK_MAX_AGE_MIN:
            raise ApplyPatchError(
                f"lock held: {LOCK_FILE} ({age_min:.0f}m old, "
                f"max stale age {LOCK_MAX_AGE_MIN}m). Another apply_patch "
                "is running, or a previous run crashed recently. Wait, or "
                "remove the lock manually if you're sure no run is in flight."
            )
        LOCK_FILE.unlink()  # stale; reclaim

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    os.write(fd, f"{os.getpid()} {_utc_iso()}\n".encode())
    os.close(fd)


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def transfer_lock_to_worker(worker_pid: int) -> None:
    """Rewrite the lock file with the worker's PID so a stale-age check
    after a long deploy reflects the worker, not the sync portion.

    The worker is responsible for unlinking the lock when it exits.
    """
    try:
        LOCK_FILE.write_text(f"{worker_pid} {_utc_iso()} worker\n")
    except FileNotFoundError:
        # Lock was somehow removed between acquire and transfer — recreate.
        try:
            fd = os.open(
                str(LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644
            )
            os.write(fd, f"{worker_pid} {_utc_iso()} worker\n".encode())
            os.close(fd)
        except FileExistsError:
            pass


# --------------------------------------------------------------------------- result file


def write_result(payload: Dict[str, Any]) -> None:
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


# --------------------------------------------------------------------------- git helpers


def current_branch() -> str:
    return _git_or_raise(["rev-parse", "--abbrev-ref", "HEAD"]).stdout.strip()


def dirty_paths() -> List[str]:
    """Return paths from `git status --porcelain --ignore-submodules=all`.

    Submodules are deliberately ignored — they are not deploy machinery's
    concern (the PARA workspace is in the manifest's exclude list, but
    --ignore-submodules also keeps submodule content drift from showing up).
    """
    r = _git_or_raise(["status", "--porcelain", "--ignore-submodules=all"])
    paths: List[str] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    return paths


def branch_diff_files(branch: str) -> List[str]:
    """Files changed on origin/<branch> relative to main (merge base diff)."""
    r = _git_or_raise(["diff", "--name-only", f"main...origin/{branch}"])
    return [p for p in r.stdout.splitlines() if p.strip()]


# --------------------------------------------------------------------------- the sync flow


def _spawn_worker(context: Dict[str, Any]) -> int:
    """Write the worker context and spawn the detached worker process.

    PHASE B. Uses subprocess.Popen with start_new_session=True so the
    worker's process group is independent of the MCP tool's. The worker
    survives the upcoming server restart (which kills the MCP server
    process group on port 8000); the worker has its own session and is
    untouched by that kill.

    Returns the worker PID for the result payload.
    """
    WORKER_CONTEXT_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORKER_CONTEXT_FILE.write_text(
        json.dumps(context, indent=2, sort_keys=True) + "\n"
    )
    WORKER_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Open log file in append mode so multiple deploys' worker output
    # accumulates in the same file (useful for forensics).
    log_fh = open(WORKER_LOG, "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(WORKER_SCRIPT), str(WORKER_CONTEXT_FILE)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        # Worker has its own fd to the log file at this point; we can close
        # the parent-side handle.
        log_fh.close()
    return proc.pid


def _do_phase_a(
    branch: str,
    reason: str,
    caller: str,
    decision_trail: List[str],
) -> Dict[str, Any]:
    """Run the synchronous Phase A flow. Returns the result payload."""

    def step(msg: str) -> None:
        decision_trail.append(msg)

    # ---- preflight ------------------------------------------------------
    if not BRANCH_RE.match(branch):
        raise ApplyPatchError(f"branch name fails sanity regex: {branch!r}")
    if not reason or not reason.strip():
        raise ApplyPatchError("reason is required and must be non-empty")
    if not (REPO_ROOT / ".git").exists():
        raise ApplyPatchError(f"{REPO_ROOT}/.git missing — not a git repo")

    cb = current_branch()
    if cb != "main":
        raise ApplyPatchError(f"expected to be on branch main; on {cb!r} instead")

    step("preflight: on main, branch name OK, reason non-empty")

    # ---- manifest -------------------------------------------------------
    try:
        include_spec = load_include_spec()
        exclude_spec = load_exclude_spec()
    except ManifestError as e:
        raise ApplyPatchError(f"manifest: {e}") from e
    step(f"manifest loaded from {MANIFEST_PATH}")

    # ---- working tree check (live-state churn tolerated) ----------------
    clean, surprises = working_tree_clean_for_deploy(dirty_paths(), exclude_spec)
    if not clean:
        bullets = "\n  ".join(surprises)
        raise ApplyPatchError(
            "working tree dirty with paths NOT in manifest exclude list:\n  "
            + bullets
            + "\nCommit or revert in-scope changes before retrying."
        )
    step("working tree clean for deploy (all dirty paths are manifest-excluded)")

    # ---- fetch + verify branch ------------------------------------------
    _git_or_raise(["fetch", "origin", branch])
    r = _run_git(["rev-parse", "--verify", f"origin/{branch}"])
    if r.returncode != 0:
        raise ApplyPatchError(f"origin/{branch} does not exist after fetch")
    step(f"fetched origin/{branch}")

    # ---- scope check ----------------------------------------------------
    changed = branch_diff_files(branch)
    if not changed:
        raise ApplyPatchError(
            f"origin/{branch} has no changes vs main — nothing to deploy"
        )

    include_ok, out_of_include = include_check(changed, include_spec)
    exclude_ok, excluded = scope_check(changed, exclude_spec)
    if not include_ok or not exclude_ok:
        sections: List[str] = []
        if out_of_include:
            sections.append(
                "outside manifest include:\n  " + "\n  ".join(out_of_include)
            )
        if excluded:
            sections.append(
                "matched manifest exclude:\n  " + "\n  ".join(excluded)
            )
        raise ApplyPatchError(
            "scope violation — branch diff must be inside `include:` and "
            "outside `exclude:` in codebase/safe-deploy/manifest.yaml:\n"
            + "\n".join(sections)
        )
    step(f"scope OK — {len(changed)} files inside include and outside exclude")

    # ---- capture previous main SHA --------------------------------------
    # This SHA is the deploy artifact. There is NO full-tree backup tag.
    # Rollback (Phase B) reaches this SHA via positive-filter checkout.
    previous_main_sha = _git_or_raise(["rev-parse", "main"]).stdout.strip()
    step(f"previous_main_sha captured: {previous_main_sha[:10]}")

    # ---- merge --no-ff --------------------------------------------------
    merge_msg = f"apply_patch: {reason}"
    merge_result = _run_git(
        ["merge", "--no-ff", f"origin/{branch}", "-m", merge_msg]
    )
    if merge_result.returncode != 0:
        # Abort the half-applied merge so the working tree is clean again.
        _run_git(["merge", "--abort"])
        raise ApplyPatchError(
            f"merge of origin/{branch} failed (likely conflict). "
            "main was not updated. Rebase the branch on main and retry."
        )
    deployed_sha = _git_or_raise(["rev-parse", "main"]).stdout.strip()
    step(
        f"merged --no-ff origin/{branch}: "
        f"{previous_main_sha[:10]} -> {deployed_sha[:10]}"
    )

    # ---- push origin main -----------------------------------------------
    push_result = _run_git(["push", "origin", "main"])
    if push_result.returncode != 0:
        # Merge is local-only at this point. The next deploy will see main
        # ahead of origin/main; that's a recoverable state but worth flagging.
        raise ApplyPatchError(
            f"push origin main failed (exit {push_result.returncode}): "
            f"{push_result.stderr.strip()}. Local main is ahead of origin/main "
            f"by the merge commit {deployed_sha[:10]} — investigate before "
            "running another deploy."
        )
    step("pushed origin main")

    # Sync portion complete. State chosen by caller (skip_restart vs not).
    return {
        "state": "merged_pushed",  # caller overwrites with the right terminal state
        "ok": True,
        "phase": PHASE_TAG,
        "timestamp": _utc_iso(),
        "branch": branch,
        "reason": reason,
        "caller": caller,
        "previous_main_sha": previous_main_sha,
        "deployed_sha": deployed_sha,
        "attempted_commit_sha": deployed_sha,
        "files_changed": changed,
        "scope_check_result": "ok",
        "scope_check_files_count": len(changed),
        "smoke_run": False,
        "rollback_performed": False,
        "decision_tree_steps": list(decision_trail),
    }


# --------------------------------------------------------------------------- tool entry


@register_tool("safe_deploy")
@tool(
    name="apply_patch_deploy",
    description=(
        "Deploy a coder branch into main via the safe-deploy primitive. "
        "Default flow (skip_restart=False): caller restriction (Patch only), "
        "lock, manifest include/exclude scope check, fetch, merge --no-ff, push, "
        "then a daemonized worker handles restart + smoke + positive-filter "
        "rollback. Synchronous portion returns immediately with "
        "state=\"deploy_in_progress\"; the worker writes the final state to "
        ".claude/apply_patch_result.json when it finishes (deployed_smoke_passed, "
        "rolled_back, fatal_rollback_failed, or fatal_rollback_smoke_failed). "
        "**This tool causes a server restart — the user's consent is required on "
        "every invocation, per principles.md v3.** "
        "skip_restart=True is a debug branch: merge+push only, no daemonization, "
        "no restart. Coder agents are rejected — only Patch may invoke."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "branch": {
                "type": "string",
                "description": (
                    "The coder branch to merge into main "
                    "(e.g. 'coder/nib/some-fix'). Fetched from origin."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Short text describing why this deploy is happening. "
                    "Quoted in the merge message and the result file."
                ),
            },
            "skip_restart": {
                "type": "boolean",
                "description": (
                    "Debug-only. When True, merge+push only — no restart, "
                    "no smoke, no rollback, no daemonization. The result "
                    "file reads merged_pushed_skipped_restart. Default False — "
                    "the full restart+smoke+rollback pipeline runs (this is "
                    "the intended production behavior)."
                ),
                "default": False,
            },
        },
        "required": ["branch", "reason"],
    },
)
async def apply_patch_deploy(args: Dict[str, Any]) -> Dict[str, Any]:
    """Tool entry point. Returns the standard MCP content envelope."""
    caller = args.get("_agent_name") or "<unknown>"
    branch = args.get("branch") or ""
    reason = args.get("reason") or ""
    skip_restart = args.get("skip_restart", False)

    # ---- hard caller restriction (defense in depth) ---------------------
    # The consent prompt is a separate layer; this check exists so that
    # if the prompt is bypassed (settings.local.json glob, etc.) the tool
    # still refuses non-Patch invocations.
    if caller != "patch":
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"apply_patch_deploy refused: caller is {caller!r}, "
                        "not 'patch'. Only Patch may invoke the safe-deploy "
                        "tool. (Defense in depth on top of the consent prompt.)"
                    ),
                }
            ],
            "is_error": True,
        }

    decision_trail: List[str] = [f"caller_check: ok (agent={caller!r})"]
    worker_handoff = False  # True iff worker now owns the lock

    # ---- lock acquisition (also records baseline failure if it fails) ---
    try:
        acquire_lock()
        decision_trail.append("lock acquired")
    except ApplyPatchError as e:
        failure = {
            "state": "lock_acquire_failed",
            "ok": False,
            "phase": PHASE_TAG,
            "timestamp": _utc_iso(),
            "branch": branch,
            "reason": reason,
            "caller": caller,
            "error": str(e),
            "decision_tree_steps": list(decision_trail),
        }
        write_result(failure)
        return {
            "content": [
                {"type": "text", "text": f"apply_patch_deploy: {e}"}
            ],
            "is_error": True,
        }

    try:
        result = _do_phase_a(branch, reason, caller, decision_trail)

        if skip_restart:
            # Debug branch — merge+push only, no daemonization, no restart.
            result["state"] = "merged_pushed_skipped_restart"
            result["decision_tree_steps"] = list(decision_trail) + [
                "skip_restart=True — sync complete, no worker spawned"
            ]
            write_result(result)
            summary = (
                f"apply_patch_deploy (skip_restart=True) succeeded.\n"
                f"  branch: {branch}\n"
                f"  reason: {reason}\n"
                f"  caller: {caller}\n"
                f"  previous_main_sha: {result['previous_main_sha']}\n"
                f"  attempted_commit_sha: {result['attempted_commit_sha']}\n"
                f"  files_changed: {result['scope_check_files_count']}\n"
                f"  state: {result['state']}\n"
                f"  result_file: {RESULT_FILE}\n"
                f"  NOTE: server NOT restarted; smoke NOT run; rollback NOT armed."
            )
            return {"content": [{"type": "text", "text": summary}]}

        # ---- Phase B path: spawn the daemonized worker ------------------
        # The worker will own the restart + smoke + (optional) rollback
        # flow. It writes the final state to RESULT_FILE when done.
        worker_context = {
            "branch": branch,
            "reason": reason,
            "caller": caller,
            "previous_main_sha": result["previous_main_sha"],
            "deployed_sha": result["deployed_sha"],
            "files_changed": result["files_changed"],
            "decision_tree_steps": list(decision_trail) + [
                "sync portion complete; spawning daemonized worker"
            ],
            "phase": PHASE_TAG,
            "sync_timestamp": _utc_iso(),
        }

        worker_pid = _spawn_worker(worker_context)
        decision_trail.append(f"worker spawned (pid {worker_pid})")
        # Hand off lock ownership to the worker. Sync portion will NOT
        # release the lock in its finally — the worker unlinks it at exit.
        transfer_lock_to_worker(worker_pid)
        worker_handoff = True
        decision_trail.append("lock ownership transferred to worker")

        # Mark the deploy as in-progress so callers see something coherent
        # if they read the result file before the worker writes its final
        # outcome.
        in_progress = dict(result)
        in_progress["state"] = "deploy_in_progress"
        in_progress["worker_pid"] = worker_pid
        in_progress["worker_log"] = str(WORKER_LOG)
        in_progress["decision_tree_steps"] = list(decision_trail)
        write_result(in_progress)

        summary = (
            f"apply_patch_deploy: sync portion complete — daemonized worker spawned.\n"
            f"  branch: {branch}\n"
            f"  reason: {reason}\n"
            f"  caller: {caller}\n"
            f"  previous_main_sha: {result['previous_main_sha']}\n"
            f"  deployed_sha: {result['deployed_sha']}\n"
            f"  files_changed: {result['scope_check_files_count']}\n"
            f"  worker_pid: {worker_pid}\n"
            f"  worker_log: {WORKER_LOG}\n"
            f"  state: deploy_in_progress\n"
            f"  result_file: {RESULT_FILE}\n"
            f"  NOTE: server restart is imminent. Final state will appear in "
            f"the result file when the worker completes."
        )
        return {"content": [{"type": "text", "text": summary}]}
    except ApplyPatchError as e:
        failure = {
            "state": "aborted",
            "ok": False,
            "phase": PHASE_TAG,
            "timestamp": _utc_iso(),
            "branch": branch,
            "reason": reason,
            "caller": caller,
            "error": str(e),
            "decision_tree_steps": list(decision_trail),
        }
        write_result(failure)
        return {
            "content": [
                {"type": "text", "text": f"apply_patch_deploy aborted: {e}"}
            ],
            "is_error": True,
        }
    except Exception as e:
        import traceback

        failure = {
            "state": "unhandled_exception",
            "ok": False,
            "phase": PHASE_TAG,
            "timestamp": _utc_iso(),
            "branch": branch,
            "reason": reason,
            "caller": caller,
            "error": f"unhandled: {e!r}",
            "traceback": traceback.format_exc(),
            "decision_tree_steps": list(decision_trail),
        }
        write_result(failure)
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"apply_patch_deploy unhandled exception: {e!r}\n"
                        f"{traceback.format_exc()}"
                    ),
                }
            ],
            "is_error": True,
        }
    finally:
        if not worker_handoff:
            release_lock()
