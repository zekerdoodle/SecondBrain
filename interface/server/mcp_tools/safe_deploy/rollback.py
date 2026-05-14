"""
Positive-filter rollback for apply_patch_deploy (Phase B).

This module implements the safe rollback primitive: NO `git reset --hard`,
NO full-tree restore, NO force-push. The rollback iterates a positive list
of code-scope paths and, for each path, surgically restores files from the
previous main SHA — then forward-commits the result.

Why positive-filter:

    Live-state paths (chats, memories, scheduler files, conversation files,
    app data) are STRUCTURALLY ABSENT from the rollback's pathspec. They
    cannot be touched because the rollback never names them. This is the
    structural fix for the 2026-05-12 data-loss incident, where
    `git reset --hard <backup-tag>` overwrote live state along with code.

Why forward-commit:

    The rollback IS new history, not rewritten history. The bad merge
    commit stays on the branch as a historical fact; the forward commit
    that undoes its in-scope effects sits on top. Standard `git push`
    suffices — no `--force`, no `--force-with-lease`.

Per-path handling:

    For each scope path, we diff `previous_sha..HEAD` (limited to that
    pathspec) and restore each file by its diff status:

        A  added by merge          → `git rm <file>`        (remove)
        M  modified by merge       → `git checkout prev_sha -- <file>` (restore)
        D  deleted by merge        → `git checkout prev_sha -- <file>` (restore)
        R  renamed (handled with --no-renames; appears as A+D)
        C  copied (handled with --no-renames; appears as A)

    `--no-renames` keeps the matrix small (3 states instead of 5+) and the
    behavior obvious: every changed file is one of {add, modify, delete}.

Phase B scope:

    The scope path list is HARDCODED in this module (see SCOPE_PATHS).
    Phase C will move it to `codebase/safe-deploy/manifest.yaml` as a
    positive `include:` list and read it from there. This module's API
    accepts `scope_paths` as a parameter so the Phase C move is additive.
"""

from __future__ import annotations

import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple


REPO_ROOT = Path("/home/debian/second_brain")


# TODO Phase C: read this from codebase/safe-deploy/manifest.yaml's positive
# `include:` list once that field is added. For now, hardcode the
# directory-level scope so the rollback machinery exists end-to-end.
#
# These paths cover the deploy-scope as documented in manifest.yaml's
# "Deploy-scope reference" section. Live-state paths (chats, memories,
# scheduler files, PARA workspace, etc.) are deliberately NOT in this list
# and are therefore structurally unreachable from the rollback.
SCOPE_PATHS: Tuple[str, ...] = (
    "interface/",
    "codebase/",
    ".claude/agents/",
    ".claude/scripts/",
    ".claude/skill_defs/",
    ".claude/templates/",
    ".claude/docs/",
    "scripts/",
    "docs/",
    "tests/",
    "requirements.txt",
    "README.md",
    ".gitignore",
)


class RollbackError(RuntimeError):
    """Anything that aborts the rollback flow."""


def _run_git(
    args: List[str], cwd: Path = None, check: bool = False
) -> subprocess.CompletedProcess:
    """Run a git command. Returns CompletedProcess. Inherits no extra env.

    `cwd` defaults to the module-level REPO_ROOT at call time (not def
    time) so tests can monkey-patch the module to point at a tmpdir.
    """
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd if cwd is not None else REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=os.environ.copy(),
    )


def _git_or_raise(args: List[str], cwd: Path = None) -> subprocess.CompletedProcess:
    r = _run_git(args, cwd=cwd)
    if r.returncode != 0:
        raise RollbackError(
            f"git {' '.join(args)} failed (exit {r.returncode}): "
            f"{r.stderr.strip() or '(no stderr)'}"
        )
    return r


def enumerate_changes(
    previous_sha: str, scope_paths: List[str]
) -> List[Tuple[str, str]]:
    """Return [(status, path)] for files changed between previous_sha and HEAD
    within the given scope pathspecs.

    Status letters: A (added in HEAD), M (modified), D (deleted in HEAD).
    --no-renames is used so renames decompose into A+D.
    """
    args = [
        "diff",
        "--name-status",
        "--no-renames",
        previous_sha,
        "HEAD",
        "--",
        *scope_paths,
    ]
    r = _git_or_raise(args)
    changes: List[Tuple[str, str]] = []
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, path = parts
        # Take just the first letter (e.g. 'A100' → 'A')
        status_letter = status[0] if status else ""
        if status_letter not in ("A", "M", "D"):
            # Unexpected; skip (with --no-renames this shouldn't happen)
            continue
        changes.append((status_letter, path))
    return changes


def perform_rollback(
    previous_sha: str,
    attempted_sha: str,
    reason: str,
    scope_paths: List[str] = None,
) -> Tuple[bool, str, List[Tuple[str, str]]]:
    """Execute a positive-filter rollback.

    Args:
        previous_sha: the main SHA before the bad deploy. Restored from.
        attempted_sha: the bad deploy's merge commit SHA. Used in the
            rollback commit message for forensics.
        reason: the original deploy's reason text (echoed in the message).
        scope_paths: list of pathspecs to include in the rollback. Defaults
            to SCOPE_PATHS (hardcoded for Phase B; Phase C moves this to
            manifest.yaml).

    Returns:
        (ok, detail, changes) where:
            ok: True iff rollback completed (committed AND pushed).
            detail: human-readable summary of what happened.
            changes: list of (status, path) tuples applied.

    The rollback uses ONLY:
        - git diff --name-status (introspection)
        - git checkout <previous_sha> -- <file> (per-file restore)
        - git rm <file> (for files added by the bad deploy)
        - git add (only paths we touched)
        - git commit (forward commit, no amend)
        - git push origin main (standard push, NO --force)

    It NEVER calls:
        - git reset --hard
        - git clean
        - git restore with --worktree on broad pathspecs
        - any --force or --force-with-lease push
    """
    paths = list(scope_paths) if scope_paths is not None else list(SCOPE_PATHS)
    if not paths:
        return False, "scope_paths is empty — refusing to roll back anything", []

    try:
        changes = enumerate_changes(previous_sha, paths)
    except RollbackError as e:
        return False, f"enumerate_changes failed: {e}", []

    if not changes:
        return (
            False,
            f"no scope-path changes between {previous_sha[:10]} and HEAD — "
            "nothing to roll back. Either the bad deploy didn't touch in-scope "
            "code, or the SHAs are wrong.",
            [],
        )

    # Per-file actions.
    for status, path in changes:
        if status == "A":
            # File added by the bad deploy. Remove it.
            r = _run_git(["rm", "--", path])
            if r.returncode != 0:
                # If the file doesn't exist on disk (race), tolerate it.
                # Otherwise this is a real failure.
                if "did not match any files" in (r.stderr or "").lower():
                    continue
                return (
                    False,
                    f"git rm {path} failed: {r.stderr.strip()}",
                    changes,
                )
        else:
            # M or D: restore file from previous_sha. checkout updates
            # both index and working tree.
            r = _run_git(["checkout", previous_sha, "--", path])
            if r.returncode != 0:
                return (
                    False,
                    f"git checkout {previous_sha[:10]} -- {path} failed: "
                    f"{r.stderr.strip()}",
                    changes,
                )

    # Verify we have something to commit. (If every restoration was a
    # no-op — unlikely but possible — there'd be nothing staged.)
    status_r = _run_git(["status", "--porcelain"])
    if not status_r.stdout.strip():
        return (
            False,
            "rollback produced no staged changes — diff said files changed "
            "but checkout/rm left the index clean. Inconsistent state; "
            "Patch must triage.",
            changes,
        )

    # Forward-commit. Message format calls out that this is a rollback.
    commit_msg = (
        f"apply_patch: rollback from {attempted_sha[:10]} to {previous_sha[:10]}\n"
        f"\n"
        f"Original deploy reason: {reason}\n"
        f"Rolled back {len(changes)} file(s) within positive-filter scope.\n"
        f"Live-state paths were not touched (structurally absent from pathspec).\n"
    )
    r = _run_git(["commit", "-m", commit_msg])
    if r.returncode != 0:
        return False, f"git commit failed: {r.stderr.strip()}", changes

    # Push origin main. Standard push, no --force.
    r = _run_git(["push", "origin", "main"])
    if r.returncode != 0:
        return (
            False,
            f"git push origin main failed: {r.stderr.strip()}. Rollback "
            "commit is local-only — origin still points at the bad commit. "
            "Patch must intervene before another deploy.",
            changes,
        )

    return (
        True,
        f"rollback OK: {len(changes)} file(s) restored, forward-committed, pushed.",
        changes,
    )


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
