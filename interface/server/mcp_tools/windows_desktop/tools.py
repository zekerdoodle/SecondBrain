"""Patch-facing queue-only tools for the Windows Codex desktop bridge."""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Mapping, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

_SERVER_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _SERVER_DIR not in sys.path:
    sys.path.insert(0, _SERVER_DIR)

from windows_desktop_bridge import (  # noqa: E402
    ACTIVE_USE_POLICIES,
    DESKTOP_REQUIREMENTS,
    JOB_KINDS,
    THREAD_POLICIES,
    WindowsDesktopBridgeError,
    WindowsDesktopBridgeManager,
)


def _json_text(payload: Mapping[str, Any], *, summary: Optional[str] = None) -> str:
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


def _require_patch(args: Dict[str, Any]) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    caller = args.pop("_agent_name", None) or "<unknown>"
    args.pop("_source_chat_id", None)
    args.pop("_salon_id", None)
    if caller != "patch":
        payload = {
            "ok": False,
            "error": f"windows desktop bridge queue tools are Patch-only; caller {caller!r} is not allowed",
            "caller": caller,
        }
        return caller, _response(payload, summary="Patch-only Windows bridge tool rejected caller.", is_error=True)
    return caller, None


def _manager() -> WindowsDesktopBridgeManager:
    return WindowsDesktopBridgeManager()


def _receipt_public(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "status": receipt.get("status"),
        "created_at": receipt.get("created_at"),
        "updated_at": receipt.get("updated_at"),
        "claimed_at": receipt.get("claimed_at"),
        "started_at": receipt.get("started_at"),
        "last_heartbeat_at": receipt.get("last_heartbeat_at"),
        "ended_at": receipt.get("ended_at"),
        "attempt": receipt.get("attempt"),
        "next_attempt_at": receipt.get("next_attempt_at"),
        "worker": receipt.get("worker"),
        "continuity": receipt.get("continuity"),
        "summary": receipt.get("summary"),
        "preflight": receipt.get("preflight"),
        "safety": receipt.get("safety"),
        "stop": receipt.get("stop"),
        "artifacts": receipt.get("artifacts", []),
        "screenshots": receipt.get("screenshots", []),
        "logs": receipt.get("logs", []),
        "backups": receipt.get("backups", []),
        "orphaned_uncertain": receipt.get("orphaned_uncertain"),
    }


def _request_summary(request: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "job_id": request.get("job_id"),
        "created_at": request.get("created_at"),
        "not_before": request.get("not_before"),
        "deadline_at": request.get("deadline_at"),
        "requested_by": request.get("requested_by"),
        "caller": request.get("caller"),
        "origin": request.get("origin"),
        "project": request.get("project"),
        "dispatch": request.get("dispatch"),
        "objective": request.get("objective"),
        "kind": request.get("kind"),
        "conversation_key": request.get("conversation_key"),
        "thread_policy": request.get("thread_policy"),
        "target": request.get("target"),
        "expected_outputs": request.get("expected_outputs", []),
        "desktop_requirement": request.get("desktop_requirement"),
        "active_use_policy": request.get("active_use_policy"),
        "runtime_policy": request.get("runtime_policy"),
        "heartbeat_policy": request.get("heartbeat_policy"),
        "kill_policy": request.get("kill_policy"),
        "retry_policy": request.get("retry_policy"),
        "artifact_policy": request.get("artifact_policy"),
        "declared_risks": request.get("declared_risks", []),
        "backup_plan": request.get("backup_plan"),
        "rollback_notes": request.get("rollback_notes"),
        "sensitive_content_policy": request.get("sensitive_content_policy"),
    }


def _job_public(record: Mapping[str, Any], *, include_receipt: bool = False) -> Dict[str, Any]:
    request = record["request"]
    receipt = record["receipt"]
    payload: Dict[str, Any] = {
        "job_id": record["job_id"],
        "status": record["status"],
        "objective": request.get("objective"),
        "kind": request.get("kind"),
        "requested_by": request.get("requested_by"),
        "caller": request.get("caller"),
        "project": request.get("project"),
        "dispatch": request.get("dispatch"),
        "created_at": receipt.get("created_at"),
        "updated_at": receipt.get("updated_at"),
        "last_heartbeat_at": receipt.get("last_heartbeat_at"),
        "continuity": {
            "conversation_key": request.get("conversation_key"),
            "thread_policy": request.get("thread_policy"),
            "thread_id": receipt.get("continuity", {}).get("thread_id")
            if isinstance(receipt.get("continuity"), Mapping)
            else receipt.get("worker", {}).get("thread_id"),
            "turn_id": receipt.get("continuity", {}).get("turn_id")
            if isinstance(receipt.get("continuity"), Mapping)
            else receipt.get("worker", {}).get("turn_id"),
        },
        "stop_requested": receipt.get("stop", {}).get("stop_requested", False),
        "paths": record.get("paths", {}),
    }
    if include_receipt:
        payload["request_summary"] = _request_summary(request)
        payload["receipt"] = _receipt_public(receipt)
        payload["events"] = record.get("events", [])
    return payload


def _error_payload(exc: Exception) -> Dict[str, Any]:
    return {"ok": False, "error": str(exc), "error_type": type(exc).__name__}


_SUBMIT_DESCRIPTION = """Submit a queue-only Windows desktop bridge job.

Patch-only. This tool only writes the Second Brain queue/receipt ledger. It
does not contact Windows, launch Codex, control Chrome, start Computer Use,
generate images, open network listeners, or kill processes. Returned data is
metadata plus request/receipt/artifact paths, not raw prompt or input content.
Do not submit tokens, cookies, passwords, Chrome profile contents, or secret
values.
"""

_SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string", "description": "Optional path-safe job id. A UUID is generated if omitted."},
        "objective": {"type": "string", "description": "Concise human-readable goal."},
        "kind": {"type": "string", "enum": sorted(JOB_KINDS)},
        "conversation_key": {
            "type": "string",
            "description": (
                "Optional sanitized Windows Codex conversation continuity key. "
                "Execution-oriented continue jobs get a deterministic non-secret default if omitted."
            ),
        },
        "thread_policy": {
            "type": "string",
            "enum": sorted(THREAD_POLICIES),
            "default": "continue",
            "description": "Use continue by default; use new when Patch explicitly wants a fresh Windows Codex thread.",
        },
        "prompt": {"type": "string", "description": "Exact future Codex prompt/task body to store in request.json."},
        "target": {"description": "Optional app, website/domain, file, project path, URL, or artifact target."},
        "inputs": {"type": "array", "items": {}, "default": []},
        "expected_outputs": {"type": "array", "items": {}, "default": []},
        "origin": {"description": "Dispatch/conversation/manual origin metadata."},
        "project": {"type": "string"},
        "dispatch": {"type": "string"},
        "metadata": {"type": "object"},
        "not_before": {"type": "string", "description": "Optional ISO timestamp before which workers should not claim."},
        "deadline_at": {"type": "string", "description": "Optional ISO deadline."},
        "desktop_requirement": {
            "type": "string",
            "enum": sorted(DESKTOP_REQUIREMENTS),
            "default": "background_only",
        },
        "active_use_policy": {
            "type": "string",
            "enum": sorted(ACTIVE_USE_POLICIES),
            "default": "defer_if_active",
        },
        "idle_required_seconds": {"type": "integer", "minimum": 0, "default": 120},
        "runtime_policy": {"type": "object"},
        "heartbeat_policy": {"type": "object"},
        "max_runtime_seconds": {"type": "integer", "minimum": 1, "default": 3600},
        "heartbeat_interval_seconds": {"type": "integer", "minimum": 1, "default": 60},
        "heartbeat_timeout_seconds": {"type": "integer", "minimum": 1, "default": 300},
        "kill_policy": {"description": "String mode or structured kill policy."},
        "retry_policy": {"description": "String mode or structured retry policy."},
        "artifact_policy": {"type": "object"},
        "declared_risks": {"type": "array", "items": {}, "default": []},
        "backup_plan": {"description": "Backup/export/snapshot plan or not_applicable."},
        "rollback_notes": {"description": "Known back-out action when relevant."},
        "sensitive_content_policy": {"description": "Logging/screenshot/content policy; never include secret values."},
    },
    "required": ["objective", "kind", "prompt"],
}


@register_tool("windows_desktop")
@tool(name="windows_desktop_bridge_submit", description=_SUBMIT_DESCRIPTION, input_schema=_SUBMIT_SCHEMA)
async def windows_desktop_bridge_submit(args: Dict[str, Any]) -> Dict[str, Any]:
    caller, gate = _require_patch(args)
    if gate is not None:
        return gate
    try:
        record = _manager().submit_job(args, caller=caller)
    except WindowsDesktopBridgeError as exc:
        return _response(_error_payload(exc), summary="Windows bridge job submission failed.", is_error=True)
    payload = {"ok": True, "job": _job_public(record)}
    return _response(payload, summary=f"Queued Windows bridge job {record['job_id']}.")


_LIST_DESCRIPTION = """List Windows desktop bridge queue jobs.

Patch-only queue inspection. Returns compact metadata and receipt/artifact
paths only; it does not execute bridge work.
"""

_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "statuses": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional receipt statuses to include.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        "include_terminal": {"type": "boolean", "default": True},
    },
}


@register_tool("windows_desktop")
@tool(name="windows_desktop_bridge_list", description=_LIST_DESCRIPTION, input_schema=_LIST_SCHEMA)
async def windows_desktop_bridge_list(args: Dict[str, Any]) -> Dict[str, Any]:
    _, gate = _require_patch(args)
    if gate is not None:
        return gate
    try:
        records = _manager().list_jobs(
            statuses=args.get("statuses"),
            limit=int(args.get("limit", 50)),
            include_terminal=bool(args.get("include_terminal", True)),
        )
    except (WindowsDesktopBridgeError, ValueError, TypeError) as exc:
        return _response(_error_payload(exc), summary="Windows bridge job listing failed.", is_error=True)
    payload = {"ok": True, "count": len(records), "jobs": [_job_public(record) for record in records]}
    return _response(payload, summary=f"Found {len(records)} Windows bridge job(s).")


_READ_DESCRIPTION = """Read one Windows desktop bridge queue job.

Patch-only queue inspection. Returns request summary, receipt/status details,
events, and paths. The raw prompt and input body stay in request.json on disk.
"""

_READ_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string"},
        "include_events": {"type": "boolean", "default": True},
    },
    "required": ["job_id"],
}


@register_tool("windows_desktop")
@tool(name="windows_desktop_bridge_read", description=_READ_DESCRIPTION, input_schema=_READ_SCHEMA)
async def windows_desktop_bridge_read(args: Dict[str, Any]) -> Dict[str, Any]:
    _, gate = _require_patch(args)
    if gate is not None:
        return gate
    try:
        record = _manager().read_job(args.get("job_id", ""), include_events=bool(args.get("include_events", True)))
    except WindowsDesktopBridgeError as exc:
        return _response(_error_payload(exc), summary="Windows bridge job read failed.", is_error=True)
    payload = {"ok": True, "job": _job_public(record, include_receipt=True)}
    return _response(payload, summary=f"Read Windows bridge job {record['job_id']}.")


_CANCEL_DESCRIPTION = """Request stop/cancel for a Windows desktop bridge queue job.

Patch-only. This records a stop request in the queue/receipt ledger so a future
worker can observe it. This first slice does not kill Windows, Codex, Chrome,
GPU, or other desktop processes.
"""

_CANCEL_SCHEMA = {
    "type": "object",
    "properties": {
        "job_id": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["job_id", "reason"],
}


@register_tool("windows_desktop")
@tool(name="windows_desktop_bridge_cancel", description=_CANCEL_DESCRIPTION, input_schema=_CANCEL_SCHEMA)
async def windows_desktop_bridge_cancel(args: Dict[str, Any]) -> Dict[str, Any]:
    caller, gate = _require_patch(args)
    if gate is not None:
        return gate
    try:
        record = _manager().request_stop(
            args.get("job_id", ""),
            reason=args.get("reason", ""),
            requested_by=caller,
        )
    except WindowsDesktopBridgeError as exc:
        return _response(_error_payload(exc), summary="Windows bridge cancel/request-stop failed.", is_error=True)
    payload = {"ok": True, "job": _job_public(record, include_receipt=True)}
    return _response(payload, summary=f"Stop requested for Windows bridge job {record['job_id']}.")
