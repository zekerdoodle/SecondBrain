"""
Patch-only coder worktree inspect/cleanup MCP tools.

These wrappers intentionally keep all worktree state semantics in
``worktree_manager.WorktreeManager``. The MCP layer owns caller restriction,
confirmation checks, and compact operator rendering only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Iterable, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

# Make ``worktree_manager`` importable when this module is loaded by MCP stdio
# bridge processes.
_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from worktree_manager import (  # noqa: E402
    CODER_AGENTS,
    WorktreeCleanupError,
    WorktreeError,
    WorktreeManager,
    WorktreeValidationError,
)
import running_agents  # noqa: E402

_ALLOWED_ACTIONS = {"cleanup", "force", "abandon"}
_DEFAULT_HISTORY_LIMIT = 10
_MAX_HISTORY_LIMIT = 50


def _json_text(payload: Dict[str, Any], *, summary: Optional[str] = None) -> str:
    parts = []
    if summary:
        parts.append(summary)
    parts.append("```json")
    parts.append(json.dumps(payload, indent=2, sort_keys=True))
    parts.append("```")
    return "\n".join(parts)


def _response(payload: Dict[str, Any], *, summary: Optional[str] = None, is_error: bool = False) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "content": [{"type": "text", "text": _json_text(payload, summary=summary)}]
    }
    if is_error:
        result["is_error"] = True
    return result


def _require_patch(args: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    caller = args.pop("_agent_name", None) or "<unknown>"
    args.pop("_source_chat_id", None)
    args.pop("_salon_id", None)
    if caller != "patch":
        payload = {
            "ok": False,
            "error": f"coder worktree tools are Patch-only; caller {caller!r} is not allowed",
            "caller": caller,
        }
        return _response(payload, summary="Patch-only coder worktree tool rejected caller.", is_error=True)
    return None


def _history_limit(raw: Any) -> int:
    if raw is None:
        return _DEFAULT_HISTORY_LIMIT
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        return _DEFAULT_HISTORY_LIMIT
    return max(0, min(limit, _MAX_HISTORY_LIMIT))


def _history_tail(manager: WorktreeManager, limit: int) -> list[Dict[str, Any]]:
    if limit <= 0 or not manager.cleanup_history_path.exists():
        return []
    records: list[Dict[str, Any]] = []
    for line in manager.cleanup_history_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            records.append({"raw": line, "parse_error": True})
    return records[-limit:]


def _inspection_to_dict(manager: WorktreeManager, agent: str, slug: str) -> Dict[str, Any]:
    try:
        return manager.inspect_active_worktree(agent, slug).to_dict()
    except (WorktreeValidationError, WorktreeError) as exc:
        key = f"{agent}:{slug}"
        return {
            "key": key,
            "registry_record": None,
            "worktree_path": None,
            "path_exists": False,
            "agent_lock_exists": False,
            "branch_lock_exists": False,
            "git_worktree_present": False,
            "dirty": None,
            "dirty_status": "",
            "raw_dirty": None,
            "normalized_status": None,
            "manifest_valid": False,
            "baseline_clean": None,
            "problems": [f"inspection-error: {exc}"],
            "stale": True,
        }


def _active_targets(manager: WorktreeManager) -> list[tuple[str, str]]:
    registry = manager.read_registry()
    active = registry.get("active") or {}
    targets: list[tuple[str, str]] = []
    for key, record in active.items():
        agent = record.get("agent") if isinstance(record, dict) else None
        slug = record.get("slug") if isinstance(record, dict) else None
        if not isinstance(agent, str) or not isinstance(slug, str):
            if isinstance(key, str) and ":" in key:
                agent, slug = key.split(":", 1)
        if isinstance(agent, str) and isinstance(slug, str):
            targets.append((agent, slug))
    return sorted(set(targets))


def _inspect_payload(
    manager: WorktreeManager,
    *,
    agent: Optional[str] = None,
    slug: Optional[str] = None,
    include_history: bool = True,
    history_limit: int = _DEFAULT_HISTORY_LIMIT,
) -> Dict[str, Any]:
    if (agent and not slug) or (slug and not agent):
        raise WorktreeValidationError("agent and slug must be supplied together")

    targets = [(agent, slug)] if agent and slug else _active_targets(manager)
    inspections = [
        _inspection_to_dict(manager, target_agent, target_slug)
        for target_agent, target_slug in targets
    ]
    payload: Dict[str, Any] = {
        "ok": True,
        "state_dir": str(manager.state_dir),
        "registry_path": str(manager.registry_path),
        "history_path": str(manager.cleanup_history_path),
        "active_count": len(_active_targets(manager)),
        "inspections": inspections,
    }
    if include_history:
        payload["history"] = _history_tail(manager, history_limit)
    return payload


def _count_stale(inspections: Iterable[Dict[str, Any]]) -> int:
    return sum(1 for item in inspections if item.get("stale"))


def _authoritative_running_rows() -> list[Dict[str, Any]]:
    """Read live-row truth or fail closed before cleanup can mutate state."""
    try:
        rows = running_agents.list_source_of_truth_sync()
    except Exception as exc:
        raise WorktreeCleanupError(
            "authoritative running-row truth is unavailable; "
            "cleanup refused without mutation"
        ) from exc
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise WorktreeCleanupError(
            "authoritative running-row truth is inconclusive; "
            "cleanup refused without mutation"
        )
    return rows


_INSPECT_DESCRIPTION = """Patch-only read-only inspection for active coder worktrees.

With no agent/slug, inspects every active canonical registry record. With both
agent and slug, inspects that single record and returns diagnostics even when it
is missing, preparing, old, tampered, or stale. It recomputes both manifest
digests and reports manifest_valid, raw_dirty, and tri-state baseline_clean
separately. This tool never mutates registry, locks, history, branches, or
worktree directories.
"""

_INSPECT_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "enum": sorted(CODER_AGENTS),
            "description": "Optional coder agent. If supplied, slug must also be supplied.",
        },
        "slug": {
            "type": "string",
            "description": "Optional worktree slug. If supplied, agent must also be supplied.",
        },
        "include_history": {
            "type": "boolean",
            "default": True,
            "description": "Include recent cleanup/refusal history from canonical .claude/worktrees/history/cleanup.jsonl.",
        },
        "history_limit": {
            "type": "integer",
            "minimum": 0,
            "maximum": _MAX_HISTORY_LIMIT,
            "default": _DEFAULT_HISTORY_LIMIT,
            "description": "Maximum cleanup history records to include, newest last.",
        },
    },
}


@register_tool("utilities")
@tool(name="coder_worktree_inspect", description=_INSPECT_DESCRIPTION, input_schema=_INSPECT_SCHEMA)
async def coder_worktree_inspect(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _require_patch(args)
    if gate is not None:
        return gate

    try:
        manager = WorktreeManager()
        payload = _inspect_payload(
            manager,
            agent=args.get("agent"),
            slug=args.get("slug"),
            include_history=bool(args.get("include_history", True)),
            history_limit=_history_limit(args.get("history_limit")),
        )
    except (WorktreeValidationError, WorktreeError) as exc:
        error_payload = {"ok": False, "error": str(exc)}
        return _response(error_payload, summary="Coder worktree inspection failed.", is_error=True)

    stale_count = _count_stale(payload["inspections"])
    summary = (
        f"Coder worktree inspect: {payload['active_count']} active record(s), "
        f"{len(payload['inspections'])} inspection(s), {stale_count} stale/problem record(s)."
    )
    return _response(payload, summary=summary)


_CLEANUP_DESCRIPTION = """Patch-only cleanup for exactly one active coder worktree record.

Requires confirm='<action>:<agent>:<slug>' before mutation. Cleanup semantics and
audit history are owned by WorktreeManager.cleanup_active_worktree; this wrapper
only gates caller/action/confirmation and formats the result.
"""

_CLEANUP_SCHEMA = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "enum": sorted(CODER_AGENTS),
            "description": "Coder agent that owns the active worktree.",
        },
        "slug": {
            "type": "string",
            "description": "Worktree slug to clean, force-clean, or abandon.",
        },
        "action": {
            "type": "string",
            "enum": sorted(_ALLOWED_ACTIONS),
            "default": "cleanup",
            "description": "cleanup removes clean git-listed worktrees; force removes dirty/unreadable git-listed worktrees; abandon clears registry/locks and leaves the path.",
        },
        "confirm": {
            "type": "string",
            "description": "Required confirmation phrase: '<action>:<agent>:<slug>'.",
        },
    },
    "required": ["agent", "slug", "confirm"],
}


@register_tool("utilities")
@tool(name="coder_worktree_cleanup", description=_CLEANUP_DESCRIPTION, input_schema=_CLEANUP_SCHEMA)
async def coder_worktree_cleanup(args: Dict[str, Any]) -> Dict[str, Any]:
    gate = _require_patch(args)
    if gate is not None:
        return gate

    agent = args.get("agent")
    slug = args.get("slug")
    action = args.get("action") or "cleanup"
    confirm = args.get("confirm")

    if action not in _ALLOWED_ACTIONS:
        payload = {"ok": False, "error": f"unsupported cleanup action: {action!r}"}
        return _response(payload, summary="Coder worktree cleanup rejected unsupported action.", is_error=True)
    if not isinstance(agent, str) or not isinstance(slug, str):
        payload = {"ok": False, "error": "agent and slug are required"}
        return _response(payload, summary="Coder worktree cleanup rejected missing target.", is_error=True)

    expected_confirm = f"{action}:{agent}:{slug}"
    if confirm != expected_confirm:
        payload = {
            "ok": False,
            "action": action,
            "agent": agent,
            "slug": slug,
            "error": f"confirmation required: {expected_confirm}",
            "expected_confirm": expected_confirm,
        }
        return _response(payload, summary="Coder worktree cleanup refused: confirmation mismatch.", is_error=True)

    manager = WorktreeManager()
    try:
        inspection_before = manager.inspect_active_worktree(agent, slug).to_dict()
        result = manager.cleanup_active_worktree(
            agent,
            slug,
            force=action == "force",
            abandon=action == "abandon",
            live_rows_provider=_authoritative_running_rows,
        )
        inspection_after = manager.inspect_active_worktree(agent, slug).to_dict()
    except WorktreeCleanupError as exc:
        inspection_before = _inspection_to_dict(manager, agent, slug)
        payload = {
            "ok": False,
            "action": action,
            "error": str(exc),
            "inspection_before": inspection_before,
            "history_path": str(manager.cleanup_history_path),
        }
        return _response(payload, summary="Coder worktree cleanup refused.", is_error=True)
    except (WorktreeValidationError, WorktreeError) as exc:
        payload = {
            "ok": False,
            "action": action,
            "error": str(exc),
            "history_path": str(manager.cleanup_history_path),
        }
        return _response(payload, summary="Coder worktree cleanup failed.", is_error=True)

    payload = {
        "ok": True,
        "action": action,
        "result": result.to_dict(),
        "inspection_before": inspection_before,
        "inspection_after": inspection_after,
        "history_path": str(manager.cleanup_history_path),
    }
    summary = f"Coder worktree {action} succeeded for {agent}:{slug}."
    return _response(payload, summary=summary)
