"""
Server Restart tool.

Restarts the Second Brain server with conversation continuity.
"""

import os
import sys
import asyncio
import subprocess
import logging
import inspect
import json
import math
from typing import Any, Dict

from claude_agent_sdk import tool

import restart_provenance as rp
import managed_load_operations as mlo
from ..registry import register_tool

logger = logging.getLogger("mcp_tools.restart")

_ALLOWED_RESTART_CONSUMER = "main_streaming_finalizer"
_AGENT_MANAGED_RESTART_CONSUMER = "agent_managed_restart"
_AGENT_MANAGED_DIRECT_KIND = "invoke_foreground"
_AGENT_MANAGED_SCHEDULED_KIND = "invoke_trust"
_OWNER_LOSS_PUBLIC_FIELDS = frozenset(
    {
        "managed_load_action",
        "load_operation_id",
        "source_fingerprint",
        "required_artifact_checkpoints",
        "owner_loss_reconciliation",
    }
)
_OWNER_LOSS_INTERNAL_FIELDS = frozenset(
    {"_agent_name", "_restart_consumer", "_source_chat_id"}
)
_RUNNING_ROW_FIELDS = frozenset(
    {
        "id",
        "agent",
        "kind",
        "started_at",
        "task_summary",
        "source_chat_id",
        "conversation_id",
        "salon_id",
        "scheduled_task_id",
        "scheduled_attempt_id",
        "caller_agent",
        "worktree_branch",
        "worktree_slug",
        "worktree_base_ref",
        "worktree_route_mode",
        "worktree_path",
        "worktree_request_manifest_digest",
        "worktree_baseline_manifest_digest",
        "timeout_seconds",
        "deadline_at",
        "process_id",
    }
)
_RUNNING_ROW_PROJECTION_FIELDS = tuple(
    field
    for field in sorted(_RUNNING_ROW_FIELDS)
    if field != "task_summary"
)


def _summarize_running_entry(entry: dict[str, Any]) -> str:
    agent = entry.get("agent") or "unknown"
    kind = entry.get("kind") or "unknown"
    conversation_id = entry.get("conversation_id") or "no-thread"
    return f"{agent}/{kind}/{conversation_id}"


def _parse_agent_managed_restart_consumer(restart_consumer: str) -> tuple[str | None, str | None]:
    parts = (restart_consumer or "").split(":", 2)
    if len(parts) != 3 or parts[0] != _AGENT_MANAGED_RESTART_CONSUMER:
        return None, None
    mode, conversation_id = parts[1], parts[2]
    if not mode or not conversation_id:
        return None, None
    return mode, conversation_id


def _agent_managed_restart_error(
    *,
    restart_consumer: str,
    source_agent: str,
    running_invocations: list[dict[str, Any]],
) -> str | None:
    """Return None only for Patch's current runner invocation.

    Scheduled Patch wakes have two authoritative entries: the outer scheduler
    wrapper plus the inner durable agent-thread invocation. The inner
    conversation_id is carried in the restart consumer so extra active work still
    fails closed before marker writes.
    """
    mode, conversation_id = _parse_agent_managed_restart_consumer(restart_consumer)
    if not conversation_id:
        return "agent-managed restart consumer is missing the current conversation id"
    if source_agent != "patch":
        return "agent-managed restarts are Patch-only"

    current_entry, owner_error = _agent_managed_current_entry(
        restart_consumer, source_agent, running_invocations
    )
    if owner_error:
        return owner_error
    scheduled_wrappers = []
    unexpected_entries = []
    for entry in running_invocations:
        if entry.get("id") == (current_entry or {}).get("id"):
            continue
        elif (
            mode == "scheduled"
            and entry.get("agent") == source_agent
            and entry.get("kind") == "scheduled"
            and not entry.get("conversation_id")
            and entry.get("scheduled_task_id")
            == (current_entry or {}).get("scheduled_task_id")
            and entry.get("scheduled_attempt_id")
            == (current_entry or {}).get("scheduled_attempt_id")
        ):
            scheduled_wrappers.append(entry)
        else:
            unexpected_entries.append(entry)

    if len(scheduled_wrappers) > 1:
        return "authoritative running_agents showed multiple scheduled Patch wrappers"
    if unexpected_entries:
        details = ", ".join(_summarize_running_entry(entry) for entry in unexpected_entries[:3])
        more = "" if len(unexpected_entries) <= 3 else f", +{len(unexpected_entries) - 3} more"
        return f"authoritative running_agents showed other active invocation(s): {details}{more}"
    return None


def _is_agent_managed_restart_consumer(restart_consumer: str) -> bool:
    mode, conversation_id = _parse_agent_managed_restart_consumer(restart_consumer)
    return bool(mode and conversation_id)


def _agent_managed_current_entry(
    restart_consumer: str,
    source_agent: str,
    running_invocations: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str | None]:
    mode, conversation_id = _parse_agent_managed_restart_consumer(restart_consumer)
    if not mode or not conversation_id:
        return None, "agent-managed restart consumer is malformed"
    if source_agent != "patch":
        return None, "agent-managed restarts are Patch-only"
    if mode == "scheduled":
        matches = [
            entry
            for entry in running_invocations
            if entry.get("agent") == "patch"
            and entry.get("conversation_id") == conversation_id
            and entry.get("kind") == _AGENT_MANAGED_SCHEDULED_KIND
            and entry.get("scheduled_task_id")
            and entry.get("scheduled_attempt_id")
        ]
    elif mode == "foreground":
        matches = [
            entry
            for entry in running_invocations
            if entry.get("agent") == "patch"
            and entry.get("conversation_id") == conversation_id
            and entry.get("kind") == _AGENT_MANAGED_DIRECT_KIND
            and not entry.get("scheduled_task_id")
            and not entry.get("scheduled_attempt_id")
            and entry.get("caller_agent") == "agent_notification_wakeup"
        ]
    else:
        return None, (
            "unsupported Patch managed-load owner mode; only exact notification "
            "foreground callbacks and scheduler attempts are accepted"
        )
    if len(matches) != 1:
        return None, (
            "authoritative running_agents did not show exactly one current "
            f"Patch invocation for thread {conversation_id}"
        )
    return dict(matches[0]), None


def _managed_load_binding(
    restart_consumer: str,
    source_agent: str,
    current_entry: dict[str, Any],
    *,
    source_fingerprint: str,
    required_artifact_checkpoints: Any,
) -> dict[str, Any]:
    mode, conversation_id = _parse_agent_managed_restart_consumer(restart_consumer)
    if not mode or not conversation_id:
        raise mlo.ManagedLoadError("agent-managed restart consumer is malformed")
    scheduled = mode == "scheduled"
    scheduled_task_id = current_entry.get("scheduled_task_id") if scheduled else None
    scheduled_attempt_id = current_entry.get("scheduled_attempt_id") if scheduled else None
    if scheduled and (not scheduled_task_id or not scheduled_attempt_id):
        raise mlo.ManagedLoadError(
            "scheduled managed load requires exact task and attempt identity"
        )
    owner_id = scheduled_attempt_id if scheduled else current_entry.get("id")
    if not owner_id:
        raise mlo.ManagedLoadError("managed-load current owner identity is missing")
    return {
        "source_fingerprint": mlo.normalize_source_fingerprint(source_fingerprint),
        "consumer_kind": "scheduled" if scheduled else "invoked",
        "conversation_id": conversation_id,
        "owner_kind": "scheduled" if scheduled else "direct",
        "owner_id": str(owner_id),
        "caller_agent": source_agent,
        "scheduled_task_id": scheduled_task_id,
        "scheduled_attempt_id": scheduled_attempt_id,
        "required_artifact_checkpoints": mlo.normalize_checkpoints(
            required_artifact_checkpoints
        ),
    }


async def _validate_managed_load_owner(
    restart_consumer: str,
    current_entry: dict[str, Any],
    binding: dict[str, Any],
) -> None:
    """Require one durable terminal owner before receipt preparation."""
    mode, _ = _parse_agent_managed_restart_consumer(restart_consumer)
    if mode == "scheduled":
        if (
            current_entry.get("caller_agent") != "scheduler"
            or binding.get("owner_kind") != "scheduled"
            or binding.get("owner_id") != current_entry.get("scheduled_attempt_id")
        ):
            raise mlo.ManagedLoadError(
                "scheduled managed load lacks exact scheduler attempt ownership"
            )
        return
    if mode != "foreground":
        raise mlo.ManagedLoadError("unsupported managed-load continuation owner")

    from agent_invocation_control import get_controller

    owner_id = mlo._canonical_uuid(
        binding.get("owner_id"), field="direct managed-load owner_id"
    )
    receipt = await get_controller().get(owner_id)
    if not receipt:
        raise mlo.ManagedLoadError(
            "foreground managed load lacks a durable notification control receipt"
        )
    if (
        receipt.get("invocation_id") != owner_id
        or receipt.get("live_row_id") != owner_id
        or receipt.get("target_agent") != "patch"
        or receipt.get("caller_agent") != "agent_notification_wakeup"
        or receipt.get("conversation_id") != binding.get("conversation_id")
        or receipt.get("mode") != "foreground"
        or receipt.get("state") not in {"accepted", "running", "continuing"}
        or receipt.get("notification_delivery_state") != "claimed"
        or not receipt.get("notification_ids")
    ):
        raise mlo.ManagedLoadError(
            "foreground managed load control receipt is not an exact nonterminal callback owner"
        )


def _managed_load_receipt_result(
    receipt: dict[str, Any], *, existing: bool
) -> dict[str, Any]:
    disposition = "existing receipt returned; no restart replayed" if existing else "restart accepted"
    return {
        "content": [{
            "type": "text",
            "text": (
                f"Managed load {disposition}.\n"
                f"Load operation: {receipt['load_operation_id']}\n"
                f"Source fingerprint: {receipt['source_fingerprint']}\n"
                f"State: {receipt['state']}\n"
                f"Restart attempt: {receipt['restart_attempt_id']}\n"
                f"Process replacement: old={receipt['old_process_pid']} "
                f"new={receipt.get('new_process_pid') or 'not-proved'}\n"
                "Required checkpoints: "
                f"{', '.join(receipt['required_artifact_checkpoints'])}\n"
                "Completed checkpoints: "
                f"{', '.join(sorted(receipt['completed_artifact_checkpoints'])) or 'none'}"
            ),
        }],
        "managed_load_receipt": receipt,
    }


def _managed_load_existing_terminal_result(receipt: dict[str, Any]) -> dict[str, Any]:
    projection = {
        "load_operation_id": receipt["load_operation_id"],
        "source_fingerprint": receipt["source_fingerprint"],
        "state": receipt["state"],
        "restart_attempt_id": receipt["restart_attempt_id"],
        "old_process_pid": receipt["old_process_pid"],
        "new_process_pid": receipt["new_process_pid"],
        "required_artifact_checkpoints": list(
            receipt["required_artifact_checkpoints"]
        ),
        "completed_artifact_checkpoints": sorted(
            receipt["completed_artifact_checkpoints"]
        ),
        "existing_operation": True,
        "restart_replayed": False,
        "receipt_unchanged": True,
    }
    return {
        "content": [{
            "type": "text",
            "text": (
                "Managed load operation was already completed; no restart was replayed.\n"
                f"Load operation: {projection['load_operation_id']}\n"
                f"Source fingerprint: {projection['source_fingerprint']}\n"
                f"State: {projection['state']}\n"
                f"Historical restart attempt: {projection['restart_attempt_id']}\n"
                "Historical process replacement: "
                f"old={projection['old_process_pid']} new={projection['new_process_pid']}\n"
                "Required checkpoints: "
                f"{', '.join(projection['required_artifact_checkpoints'])}\n"
                "Completed checkpoints: "
                f"{', '.join(projection['completed_artifact_checkpoints'])}"
            ),
        }],
        "managed_load_receipt": projection,
    }


def _managed_load_owner_loss_result(
    receipt: dict[str, Any], *, created: bool
) -> dict[str, Any]:
    disposition = (
        "reconciled once without restart"
        if created
        else "existing exact reconciliation returned unchanged"
    )
    return {
        "content": [{
            "type": "text",
            "text": (
                f"Managed load owner loss {disposition}.\n"
                f"Load operation: {receipt['load_operation_id']}\n"
                f"Source fingerprint: {receipt['source_fingerprint']}\n"
                f"State: {receipt['state']}\n"
                f"Completion basis: {receipt['completion_basis']}\n"
                f"Historical owner: {receipt['owner_kind']}/{receipt['owner_id']}\n"
                "Historical continuation claim: none\n"
                "Historical caller acknowledgement: none\n"
                f"Restart replayed: false\n"
                f"Receipt changed by this call: {'true' if created else 'false'}"
            ),
        }],
        "managed_load_receipt": receipt,
    }


def _validate_owner_loss_action_args(args: Dict[str, Any]) -> dict[str, Any]:
    fields = set(args)
    unknown_internal = {
        field
        for field in fields
        if field.startswith("_") and field not in _OWNER_LOSS_INTERNAL_FIELDS
    }
    public = fields - _OWNER_LOSS_INTERNAL_FIELDS
    if unknown_internal or public != _OWNER_LOSS_PUBLIC_FIELDS:
        extra = sorted(public - _OWNER_LOSS_PUBLIC_FIELDS)
        missing = sorted(_OWNER_LOSS_PUBLIC_FIELDS - public)
        raise mlo.ManagedLoadError(
            "reconcile_owner_loss request fields are closed "
            f"(missing={missing}, extra={extra}, internal={sorted(unknown_internal)})"
        )
    if args.get("_agent_name") != "patch":
        raise mlo.ManagedLoadError("reconcile_owner_loss is Patch-only")
    return mlo.normalize_owner_loss_request(
        load_operation_id=args.get("load_operation_id"),
        source_fingerprint=args.get("source_fingerprint"),
        required_artifact_checkpoints=args.get(
            "required_artifact_checkpoints"
        ),
        owner_loss_reconciliation=args.get("owner_loss_reconciliation"),
    )


def _running_rows_projection(
    running_invocations: Any,
) -> dict[str, Any]:
    if not isinstance(running_invocations, list):
        raise mlo.ManagedLoadError(
            "current_owner_invalid: running_agents did not return an array"
        )
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in running_invocations:
        if (
            not isinstance(raw, dict)
            or set(raw) - _RUNNING_ROW_FIELDS
            or not {"id", "agent", "kind", "started_at", "task_summary"} <= set(raw)
        ):
            raise mlo.ManagedLoadError(
                "current_owner_invalid: running row fields are unrecognized"
            )
        row_id = mlo._canonical_uuid(raw.get("id"), field="running row id")
        if row_id in seen:
            raise mlo.ManagedLoadError(
                "current_owner_invalid: duplicate running row identity"
            )
        seen.add(row_id)
        started_at = raw.get("started_at")
        if (
            type(started_at) not in {int, float}
            or not math.isfinite(started_at)
        ):
            raise mlo.ManagedLoadError(
                "current_owner_invalid: running row timestamp is invalid"
            )
        task_summary = raw.get("task_summary")
        if not isinstance(task_summary, str) or len(task_summary) > 2000:
            raise mlo.ManagedLoadError(
                "current_owner_invalid: running row summary is invalid"
            )
        row: dict[str, Any] = {"id": row_id}
        for field in _RUNNING_ROW_PROJECTION_FIELDS:
            if field == "id":
                continue
            value = raw.get(field)
            if field in {"started_at", "timeout_seconds", "deadline_at"}:
                if value is not None and (
                    type(value) not in {int, float}
                    or not math.isfinite(value)
                ):
                    raise mlo.ManagedLoadError(
                        f"current_owner_invalid: running row {field} is invalid"
                    )
            elif value is not None and (
                not isinstance(value, str)
                or len(value) > 800
                or "\n" in value
                or "\r" in value
                or "\x00" in value
            ):
                raise mlo.ManagedLoadError(
                    f"current_owner_invalid: running row {field} is invalid"
                )
            row[field] = value
        projected.append(row)
    projected.sort(key=lambda row: row["id"])
    return {
        "schema": "second_brain.managed_load_running_agents_projection.v1",
        "row_count": len(projected),
        "rows": projected,
    }


async def _derive_owner_loss_repair_context(
    args: Dict[str, Any],
    request: dict[str, Any],
) -> dict[str, Any]:
    import running_agents

    restart_consumer = args.get("_restart_consumer") or "none"
    source_agent = args.get("_agent_name")
    running_invocations = await running_agents.list_source_of_truth()
    current_entry, owner_error = _agent_managed_current_entry(
        restart_consumer,
        source_agent,
        running_invocations,
    )
    if owner_error:
        raise mlo.ManagedLoadError(f"current_owner_invalid: {owner_error}")
    binding = _managed_load_binding(
        restart_consumer,
        source_agent,
        current_entry or {},
        source_fingerprint=request["source_fingerprint"],
        required_artifact_checkpoints=request[
            "required_artifact_checkpoints"
        ],
    )
    await _validate_managed_load_owner(
        restart_consumer,
        current_entry or {},
        binding,
    )
    safety_error = _agent_managed_restart_error(
        restart_consumer=restart_consumer,
        source_agent=source_agent,
        running_invocations=running_invocations,
    )
    if safety_error:
        raise mlo.ManagedLoadError(f"concurrent_live_work: {safety_error}")
    authority = mlo.normalize_repair_authority(
        {
            "schema": "second_brain.managed_load_repair_authority.v1",
            "caller_agent": source_agent,
            "owner_kind": binding["owner_kind"],
            "owner_id": binding["owner_id"],
            "running_entry_id": current_entry.get("id"),
            "conversation_id": binding["conversation_id"],
            "scheduled_task_id": binding["scheduled_task_id"],
            "scheduled_attempt_id": binding["scheduled_attempt_id"],
            "restart_consumer": restart_consumer,
        }
    )
    projection = _running_rows_projection(running_invocations)
    return mlo.normalize_repair_context(
        {
            "repair_authority": authority,
            "running_agents_projection_sha256": mlo._canonical_json_sha256(
                projection
            ),
        }
    )


async def _handle_owner_loss_reconciliation(
    args: Dict[str, Any],
) -> Dict[str, Any]:
    request = _validate_owner_loss_action_args(args)
    store = mlo.get_store()
    request_schema = request["owner_loss_reconciliation"]["schema"]
    if request_schema == mlo.OWNER_LOSS_REQUEST_SCHEMA_V1:
        request = store.preflight_owner_loss_request(request)
        context = await _derive_owner_loss_repair_context(args, request)
    else:
        context = await _derive_owner_loss_repair_context(args, request)
        request = store.preflight_owner_loss_request(request)

    async def revalidate() -> dict[str, Any]:
        return await _derive_owner_loss_repair_context(args, request)

    project_root = os.environ.get(
        "SECOND_BRAIN_OWNER_LOSS_PROJECT_ROOT", str(mlo.PROJECT_ROOT)
    )
    receipt, created = await store.reconcile_owner_loss(
        request,
        repair_context=context,
        project_root=project_root,
        revalidate_current_context=revalidate,
    )
    return _managed_load_owner_loss_result(receipt, created=created)


def _handle_managed_load_action(args: Dict[str, Any]) -> Dict[str, Any]:
    if args.get("_agent_name") != "patch":
        raise mlo.ManagedLoadError("managed-load status/checkpoints are Patch-only")
    operation_id = args.get("load_operation_id")
    fingerprint = mlo.normalize_source_fingerprint(args.get("source_fingerprint"))
    store = mlo.get_store()
    receipt = store.get(operation_id)
    if receipt is None:
        raise mlo.ManagedLoadError("unknown managed-load operation")
    if receipt["source_fingerprint"] != fingerprint:
        raise mlo.ManagedLoadError("managed-load source fingerprint conflict")
    action = args.get("managed_load_action")
    if action == "checkpoint":
        checkpoints = args.get("artifact_checkpoints")
        if not isinstance(checkpoints, list) or not checkpoints:
            raise mlo.ManagedLoadError(
                "artifact_checkpoints must be a non-empty array for checkpoint action"
            )
        for item in checkpoints:
            if not isinstance(item, dict) or set(item) != {"name", "path"}:
                raise mlo.ManagedLoadError(
                    "each artifact checkpoint must contain exactly name and path"
                )
            relative_path, digest = mlo.verify_artifact_path(
                receipt["load_operation_id"],
                str(item["name"]),
                str(item["path"]),
            )
            receipt = store.record_artifact_checkpoint(
                receipt["load_operation_id"],
                source_fingerprint=fingerprint,
                checkpoint=str(item["name"]),
                relative_path=relative_path,
                sha256=digest,
            )
    return _managed_load_receipt_result(receipt, existing=True)


def _spawn_managed_restart_subprocess(
    restart_script: str,
    log_file: str,
    *,
    provenance_env: dict[str, str] | None = None,
) -> None:
    env = os.environ.copy()
    if provenance_env:
        env.update(provenance_env)
    subprocess.Popen(
        f"sleep 1 && bash {restart_script} > {log_file} 2>&1",
        shell=True,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )


def _spawn_managed_restart_subprocess_compat(
    restart_script: str,
    log_file: str,
    *,
    provenance_env: dict[str, str],
) -> None:
    """Call the spawn hook with provenance when the hook supports it.

    Several focused tests monkeypatch the hook with a two-argument fake. Keeping
    that fake shape valid lets those tests continue proving "spawn/no spawn"
    behavior without needing to inspect provenance env.
    """
    try:
        signature = inspect.signature(_spawn_managed_restart_subprocess)
        params = signature.parameters.values()
        supports_provenance_env = "provenance_env" in signature.parameters or any(
            param.kind == inspect.Parameter.VAR_KEYWORD for param in params
        )
    except (TypeError, ValueError):
        supports_provenance_env = True

    if supports_provenance_env:
        _spawn_managed_restart_subprocess(
            restart_script,
            log_file,
            provenance_env=provenance_env,
        )
    else:
        _spawn_managed_restart_subprocess(restart_script, log_file)


# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)


def _owner_loss_manifest_schema(*, checkpoint: bool) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "path": {"type": "string"},
        "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
    }
    required = ["path", "sha256"]
    if checkpoint:
        properties["name"] = {
            "type": "string",
            "enum": sorted(mlo.ALLOWED_ARTIFACT_CHECKPOINTS),
        }
        required = ["name", "path", "sha256"]
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


def _owner_loss_evidence_schema_v1() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema": {
                "type": "string",
                "enum": [mlo.OWNER_LOSS_EVIDENCE_SCHEMA_V1],
            },
            "control_record_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "notification_store_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "notification_id": {"type": "string", "format": "uuid"},
            "notification_delivery_attempt": {
                "type": "integer",
                "minimum": 1,
            },
            "conversation_store_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "scheduler_store_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "intent_ledger_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "restart_log_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
        },
        "required": sorted(mlo.OWNER_LOSS_EVIDENCE_FIELDS_V1),
    }


def _owner_loss_evidence_schema_v2() -> dict[str, Any]:
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$"}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema": {
                "type": "string",
                "enum": [mlo.OWNER_LOSS_EVIDENCE_SCHEMA_V2],
            },
            "control_record_sha256": dict(digest),
            "notification_id": {"type": "string", "format": "uuid"},
            "notification_delivery_attempt": {
                "type": "integer",
                "minimum": 1,
            },
            "notification_authority_projection_sha256": dict(digest),
            "intent_record_projection_sha256": dict(digest),
            "restart_sequence_projection_sha256": dict(digest),
            "historical_evidence_identity_sha256": dict(digest),
        },
        "required": sorted(mlo.OWNER_LOSS_EVIDENCE_FIELDS_V2),
    }


def _owner_loss_request_branch(
    *, request_schema: str, evidence_schema: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema": {"type": "string", "enum": [request_schema]},
            "restart_attempt_id": {"type": "string", "format": "uuid"},
            "receipt_preimage_sha256": {
                "type": "string",
                "pattern": "^[0-9a-f]{64}$",
            },
            "accepted_source_manifest": {
                "type": "array",
                "minItems": 1,
                "maxItems": mlo.MAX_SOURCE_MANIFEST_ENTRIES,
                "items": _owner_loss_manifest_schema(checkpoint=False),
            },
            "checkpoint_manifest": {
                "type": "array",
                "minItems": 1,
                "maxItems": mlo.MAX_CHECKPOINT_MANIFEST_ENTRIES,
                "items": _owner_loss_manifest_schema(checkpoint=True),
            },
            "evidence": evidence_schema,
        },
        "required": sorted(mlo.OWNER_LOSS_REQUEST_FIELDS),
    }


def _owner_loss_public_request_schema() -> dict[str, Any]:
    return {
        "oneOf": [
            _owner_loss_request_branch(
                request_schema=mlo.OWNER_LOSS_REQUEST_SCHEMA_V1,
                evidence_schema=_owner_loss_evidence_schema_v1(),
            ),
            _owner_loss_request_branch(
                request_schema=mlo.OWNER_LOSS_REQUEST_SCHEMA_V2,
                evidence_schema=_owner_loss_evidence_schema_v2(),
            ),
        ],
        "description": (
            "Closed owner-loss union: v2 is required for first use; v1 is "
            "accepted only as an exact existing v1 terminal repeat."
        ),
    }


@register_tool("utilities")
@tool(
    name="restart_server",
    description="""Restart the Second Brain server to apply changes. Use this when you've made changes that require a server restart (e.g., modified server code, updated MCP tools, changed configurations).

Two modes available:
- **Quick restart** (default): Only restarts the Python server. Fast (~5 seconds).
- **Full restart with rebuild**: Rebuilds the frontend first, then restarts. Use when frontend code changed.

IMPORTANT: This tool will:
1. Save the current conversation state
2. Stop the server gracefully
3. Optionally rebuild the frontend (if rebuild=true)
4. Restart the server with your changes applied
5. Automatically continue ALL active conversations after restart (both yours and any other agents)

For Patch agent-managed loads, the restart action additionally requires a stable
load_operation_id, ordered source-manifest source_fingerprint, and exact allowlisted
required_artifact_checkpoints. Repeating that exact operation returns its durable
receipt and never spawns again. Patch may use managed_load_action=status or checkpoint
to read truth or verify declared codebase artifacts without restarting.
The exceptional reconcile_owner_loss action is a closed, monotonic,
schema-versioned no-restart transition for one already-proved process_replaced
receipt whose exact historical direct owner is durably lost. V2 first use pins
review-stable target history while the action recomputes current typed authority
twice and seals file generations before its one receipt write. V1 is accepted
only as an exact repeat of an existing v1 exceptional terminal. The action
preserves historical owner/claim/ack fields and cannot prepare or replay a load.
At a later natural-load boundary, existing_terminal_only=true lets a different exact
current Patch owner assert only the same fully artifacts_reconciled operation. Any
absence, conflict, or nonterminal state stops without preparation or replacement.

You will receive a system message after restart confirming it worked. Use the managed
load receipt—not later uptime or an intent marker—as process/load truth.""",
    input_schema={
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "Current session ID to continue after restart (auto-detected if not provided)"},
            "reason": {"type": "string", "description": "Why you're restarting — describe the change you made or why the restart is needed. Defaults to a generic message if omitted."},
            "rebuild": {"type": "boolean", "description": "If true, rebuild frontend before restart. Use when frontend code changed. Default: false (quick restart).", "default": False},
            "pending_messages": {"type": "array", "description": "Messages not yet saved (will be preserved)", "items": {"type": "object"}},
            "managed_load_action": {
                "type": "string",
                "enum": [
                    "restart",
                    "status",
                    "checkpoint",
                    "reconcile_owner_loss",
                ],
                "default": "restart",
                "description": (
                    "Patch-only managed-load action. status, checkpoint, and "
                    "reconcile_owner_loss never restart."
                )
            },
            "existing_terminal_only": {
                "type": "boolean",
                "default": False,
                "description": (
                    "Patch-only assertion for agent-managed quick restart calls: "
                    "return only an exact artifacts_reconciled operation without "
                    "creating, advancing, or replaying a restart."
                )
            },
            "load_operation_id": {
                "type": "string",
                "description": "Stable caller-persisted canonical UUID required for Patch agent-managed restart/status/checkpoint."
            },
            "source_fingerprint": {
                "type": "string",
                "description": "Ordered accepted source-manifest digest as sha256:<64 lowercase hex>; required for Patch managed-load actions."
            },
            "required_artifact_checkpoints": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(mlo.ALLOWED_ARTIFACT_CHECKPOINTS)},
                "description": "Exact unique non-empty checkpoint set required for Patch agent-managed restart."
            },
            "artifact_checkpoints": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "path": {"type": "string"}},
                    "required": ["name", "path"],
                    "additionalProperties": False
                },
                "description": "For checkpoint action only: existing codebase files that already contain the exact operation UUID."
            },
            "owner_loss_reconciliation": _owner_loss_public_request_schema(),
        }
    }
)
async def restart_server(args: Dict[str, Any]) -> Dict[str, Any]:
    """Restart the server with conversation continuity."""
    try:
        managed_load_action = args.get("managed_load_action", "restart")
        if managed_load_action not in {
            "restart",
            "status",
            "checkpoint",
            "reconcile_owner_loss",
        }:
            raise mlo.ManagedLoadError("unsupported managed_load_action")
        if managed_load_action == "reconcile_owner_loss":
            return await _handle_owner_loss_reconciliation(args)
        existing_terminal_only = args.get("existing_terminal_only", False)
        if not isinstance(existing_terminal_only, bool):
            raise mlo.ManagedLoadError("existing_terminal_only must be a boolean")
        if existing_terminal_only and managed_load_action != "restart":
            raise mlo.ManagedLoadError(
                "existing_terminal_only is valid only for managed restart assertions"
            )
        if managed_load_action != "restart":
            return _handle_managed_load_action(args)
        session_id = args.get("session_id")
        source_chat_id = args.get("_source_chat_id")
        calling_agent_name = args.get("_agent_name")
        reason = args.get("reason", "Server restart requested")
        rebuild = args.get("rebuild", False)
        restart_consumer = args.get("_restart_consumer") or "none"
        agent_managed_consumer = _is_agent_managed_restart_consumer(restart_consumer)
        if existing_terminal_only:
            if not agent_managed_consumer:
                raise mlo.ManagedLoadError(
                    "existing_terminal_only requires a Patch agent-managed restart consumer"
                )
            if rebuild is not False:
                raise mlo.ManagedLoadError(
                    "existing_terminal_only requires rebuild=false"
                )
            if "artifact_checkpoints" in args:
                raise mlo.ManagedLoadError(
                    "existing_terminal_only cannot include artifact_checkpoints"
                )

        # Import tools
        import restart_tool as rt
        import sys

        # Access the active conversations from the main server module
        main_module = sys.modules.get('main') or sys.modules.get('__main__')
        active_convs = {}
        chat_manager = None
        active_processing = {}
        if main_module:
            active_convs = getattr(main_module, 'active_conversations', {})
            chat_manager = getattr(main_module, 'chat_manager', None)
            active_processing = getattr(main_module, 'active_processing_sessions', {})

        # Prefer the MCP server's injected calling chat. Explicit session_id still
        # wins for manual/advanced use; active_processing remains only a fallback.
        if not session_id and source_chat_id:
            chat_file = rt.CHATS_DIR / f"{source_chat_id}.json"
            if source_chat_id in active_convs or chat_file.exists():
                session_id = source_chat_id

        if not session_id:
            for sid in active_processing:
                if sid in active_convs:
                    session_id = sid
                    break

        if not session_id and not agent_managed_consumer:
            try:
                active_room_file = rt.CLAUDE_DIR / "active_room.json"
                if active_room_file.exists():
                    import json
                    active_room = json.loads(active_room_file.read_text()).get("room")
                    if active_room and (rt.CHATS_DIR / f"{active_room}.json").exists():
                        session_id = active_room
            except Exception:
                pass

        if not session_id and not agent_managed_consumer:
            return {
                "content": [{"type": "text", "text": "Error: Could not determine session_id. No active conversations found."}],
                "is_error": True
            }

        # Auto-detect the source agent from the trigger chat's stored agent field.
        # MCP tools may run outside main.py's process, so fall back to direct chat
        # JSON and finally the injected calling agent.
        source_agent = calling_agent_name or "character"
        try:
            stored_chat = None
            if chat_manager:
                stored_chat = chat_manager.load_chat(session_id)
            if stored_chat is None and session_id:
                chat_file = rt.CHATS_DIR / f"{session_id}.json"
                if chat_file.exists():
                    import json
                    stored_chat = json.loads(chat_file.read_text())
            if stored_chat and not agent_managed_consumer:
                stored_agent = stored_chat.get("agent")
                if stored_agent:
                    source_agent = stored_agent
        except Exception:
            pass

        import running_agents

        running_agents_bootstrap_note = ""
        try:
            running_invocations = await running_agents.list_source_of_truth()
        except running_agents.RunningAgentsEndpointMissingError as e:
            if restart_consumer != _ALLOWED_RESTART_CONSUMER:
                if _is_agent_managed_restart_consumer(restart_consumer):
                    endpoint_rejection_reason = "agent_managed_guard_unavailable"
                elif restart_consumer == "none":
                    endpoint_rejection_reason = "no_restart_consumer"
                else:
                    endpoint_rejection_reason = "unsupported_restart_consumer"
                logger.warning(
                    "RESTART: rejected restart request before marker writes "
                    "(reason=%s, consumer=%s, "
                    "source_agent=%s, session_id=%s, source_chat_id=%s, "
                    "running_agents=endpoint_missing, error=%s)",
                    endpoint_rejection_reason,
                    restart_consumer,
                    source_agent,
                    session_id,
                    source_chat_id,
                    e,
                )
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            "Error: restart_server cannot safely restart from this invocation context. "
                            "This MCP call was not launched with the main.py streaming finalizer "
                            "that performs the clean save and detached restart subprocess spawn, "
                            "and the authoritative running_agents endpoint is not available to "
                            "validate a Patch-only agent-managed restart context. "
                            "No pending restart or continuation marker was written."
                        ),
                    }],
                    "is_error": True,
                }
            # First-load/deployment bootstrap only: this code can be live in the
            # MCP subprocess before the backend has restarted into the new
            # /api/internal/running-agents route. Let that specific explicit
            # finalizer restart proceed, visibly degraded, so the endpoint can
            # be loaded. Once the endpoint exists, every other authoritative-read
            # failure still fails closed below.
            running_invocations = []
            running_agents_bootstrap_note = (
                "\nWarning: authoritative running_agents endpoint is not loaded yet "
                f"({e}). Proceeding through the narrow deployment-bootstrap "
                "path; the live backend shutdown hook should merge its "
                "in-process running_agents snapshot during shutdown. After "
                "this endpoint is loaded, restart will fail closed on "
                "authoritative read failure."
            )
        except Exception as e:
            logger.warning(
                "RESTART: rejected restart request before marker writes "
                "(reason=running_agents_read_failed, consumer=%s, "
                "source_agent=%s, session_id=%s, source_chat_id=%s, error=%s)",
                restart_consumer,
                source_agent,
                session_id,
                source_chat_id,
                e,
            )
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: could not read authoritative running_agents "
                        f"source before restart: {e}"
                    ),
                }],
                "is_error": True,
            }

        agent_managed_restart = False
        agent_managed_restart_error = ""
        managed_load_store = None
        managed_load_binding = None
        managed_load_receipt = None
        managed_load_created = False
        if _is_agent_managed_restart_consumer(restart_consumer):
            current_entry, current_entry_error = _agent_managed_current_entry(
                restart_consumer,
                source_agent,
                running_invocations,
            )
            if current_entry_error:
                agent_managed_restart_error = current_entry_error
            else:
                try:
                    managed_load_binding = _managed_load_binding(
                        restart_consumer,
                        source_agent,
                        current_entry or {},
                        source_fingerprint=args.get("source_fingerprint"),
                        required_artifact_checkpoints=args.get(
                            "required_artifact_checkpoints"
                        ),
                    )
                    await _validate_managed_load_owner(
                        restart_consumer,
                        current_entry or {},
                        managed_load_binding,
                    )
                    operation_id = mlo._canonical_uuid(
                        args.get("load_operation_id"), field="load_operation_id"
                    )
                    managed_load_store = mlo.get_store()
                    if not existing_terminal_only:
                        managed_load_store.assert_store_readable()
                        managed_load_receipt = managed_load_store.get(operation_id)
                        if managed_load_receipt is not None:
                            managed_load_store.validate_binding(
                                managed_load_receipt, **managed_load_binding
                            )
                            return _managed_load_receipt_result(
                                managed_load_receipt, existing=True
                            )
                except mlo.ManagedLoadError as exc:
                    agent_managed_restart_error = str(exc)
            safety_error = _agent_managed_restart_error(
                restart_consumer=restart_consumer,
                source_agent=source_agent,
                running_invocations=running_invocations,
            ) or ""
            if not agent_managed_restart_error:
                agent_managed_restart_error = safety_error
            if existing_terminal_only and not agent_managed_restart_error:
                try:
                    assert managed_load_store is not None
                    assert managed_load_binding is not None
                    managed_load_receipt = (
                        managed_load_store.assert_existing_terminal(
                            operation_id,
                            source_fingerprint=managed_load_binding[
                                "source_fingerprint"
                            ],
                            required_artifact_checkpoints=managed_load_binding[
                                "required_artifact_checkpoints"
                            ],
                        )
                    )
                except mlo.ManagedLoadError as exc:
                    agent_managed_restart_error = str(exc)
                else:
                    return _managed_load_existing_terminal_result(
                        managed_load_receipt
                    )
            agent_managed_restart = not agent_managed_restart_error

        acceptance_mode = None
        if restart_consumer == _ALLOWED_RESTART_CONSUMER:
            acceptance_mode = _ALLOWED_RESTART_CONSUMER
        elif agent_managed_restart:
            acceptance_mode = _AGENT_MANAGED_RESTART_CONSUMER

        if acceptance_mode is None:
            if restart_consumer == "none":
                rejection_detail = (
                    "This MCP call did not provide a restart consumer, so no "
                    "streaming finalizer or direct-spawn owner is known."
                )
                rejection_reason = "no_restart_consumer"
            elif agent_managed_restart_error:
                rejection_detail = (
                    "This MCP call did not satisfy the Patch-only "
                    f"agent-managed restart guard. Detail: {agent_managed_restart_error}."
                )
                rejection_reason = "agent_managed_guard_failed"
            else:
                rejection_detail = f"Unsupported restart consumer: {restart_consumer}."
                rejection_reason = "unsupported_restart_consumer"
            running_summary = ", ".join(
                _summarize_running_entry(entry) for entry in running_invocations[:3]
            )
            if len(running_invocations) > 3:
                running_summary += f", +{len(running_invocations) - 3} more"
            logger.warning(
                "RESTART: rejected restart request before marker writes "
                "(reason=%s, consumer=%s, source_agent=%s, session_id=%s, "
                "source_chat_id=%s, running_invocations=%d%s)",
                rejection_reason,
                restart_consumer,
                source_agent,
                session_id,
                source_chat_id,
                len(running_invocations),
                f", running_summary={running_summary}" if running_summary else "",
            )
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        "Error: restart_server cannot safely restart from this invocation context. "
                        f"{rejection_detail} "
                        "Restart success requires either the main.py streaming finalizer "
                        "consumer or the Patch-only agent-managed restart guard."
                        " "
                        "No pending restart or continuation marker was written."
                    ),
                }],
                "is_error": True,
            }
        if agent_managed_restart:
            if rebuild:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            "Error: Patch managed loads currently support the quick "
                            "backend restart path only. No marker, intent, or process "
                            "replacement was created."
                        ),
                    }],
                    "is_error": True,
                }
            running_agents_bootstrap_note = (
                "\nManaged restart accepted from a scheduled/invoked Patch context. "
                "Authoritative running_agents shows no protected active work beyond "
                "the current Patch invocation, so restart_server will spawn the "
                "detached restart subprocess directly after writing continuation state."
            )

        # Build a map of ALL actively processing sessions -> their agent names.
        # If the MCP tool is process-isolated from main.py, preserve at least the
        # triggering session so restart continuation has a truthful agent.
        all_active = {}
        for sid in active_processing:
            agent = "character"  # Default
            try:
                if chat_manager:
                    sc = chat_manager.load_chat(sid)
                    if sc and sc.get("agent"):
                        agent = sc["agent"]
                else:
                    chat_file = rt.CHATS_DIR / f"{sid}.json"
                    if chat_file.exists():
                        import json
                        sc = json.loads(chat_file.read_text())
                        if sc.get("agent"):
                            agent = sc["agent"]
            except Exception:
                pass
            all_active[sid] = agent
        if session_id and session_id not in all_active:
            all_active[session_id] = source_agent

        # For visible chat restarts, main.py's streaming finalizer does the clean
        # save and detached spawn after this tool returns. Agent-managed Patch
        # restarts have no streaming finalizer, so this tool writes the same marker
        # contract and spawns directly after the authoritative guard below passes.

        # Choose restart script based on rebuild flag
        if rebuild:
            restart_script = rt.SECOND_BRAIN_ROOT / "interface" / "restart-server-full.sh"
            restart_type = "full (with frontend rebuild)"
            wait_time = 30
        else:
            restart_script = rt.QUICK_RESTART_SCRIPT
            restart_type = "quick (server only)"
            wait_time = 5

        log_file = rt.CLAUDE_DIR / "server_restart.log"
        old_process_pid = None
        if agent_managed_restart:
            old_process_pid = rt.find_port_pid(8000)
            if not old_process_pid:
                raise mlo.ManagedLoadError(
                    "cannot prepare managed load without the exact listening backend PID"
                )
        restart_provenance = rp.build_managed_restart_provenance(
            reason=reason,
            restart_script=str(restart_script),
            acceptance_mode=acceptance_mode,
            restart_consumer=restart_consumer,
            load_operation_id=args.get("load_operation_id") if agent_managed_restart else None,
            source_fingerprint=(
                managed_load_binding["source_fingerprint"]
                if agent_managed_restart and managed_load_binding
                else None
            ),
            old_process_pid=old_process_pid,
        )
        restart_provenance_env = rp.safe_env_from_record(restart_provenance)
        restart_attempt_id = restart_provenance["restart_attempt_id"]
        if agent_managed_restart:
            assert managed_load_store is not None and managed_load_binding is not None
            managed_load_receipt, managed_load_created = managed_load_store.prepare(
                load_operation_id=args.get("load_operation_id"),
                accepted_restart_consumer=restart_consumer,
                restart_attempt_id=restart_attempt_id,
                old_process_pid=old_process_pid,
                **managed_load_binding,
            )
            if not managed_load_created:
                return _managed_load_receipt_result(managed_load_receipt, existing=True)
            restart_provenance_env.update(
                mlo.managed_load_env(
                    load_operation_id=managed_load_receipt["load_operation_id"],
                    source_fingerprint=managed_load_receipt["source_fingerprint"],
                    old_process_pid=old_process_pid,
                )
            )

        # Save the continuation marker and pending restart config as one
        # logical operation. If the second write fails, remove the marker this
        # attempt just created so a failed-to-spawn restart cannot leave stale
        # continuation state behind.
        import json
        pending_restart_file = rt.CLAUDE_DIR / "pending_restart.json"
        continuation = None
        wrote_continuation = False
        try:
            logger.info(
                "RESTART: accepting restart request before marker writes "
                "(attempt_id=%s, consumer=%s, acceptance_mode=%s, source_agent=%s, session_id=%s, "
                "source_chat_id=%s, running_invocations=%d)",
                restart_attempt_id,
                restart_consumer,
                acceptance_mode,
                source_agent,
                session_id,
                source_chat_id,
                len(running_invocations),
            )
            continuation = rt.save_continuation_state(
                session_id=session_id,
                reason=reason,
                source=source_agent,
                all_active_sessions=all_active,
                running_invocations=running_invocations,
                restart_provenance=restart_provenance,
            )
            wrote_continuation = True
            pending_restart_file.write_text(json.dumps({
                "rebuild": rebuild,
                "restart_script": str(restart_script),
                "log_file": str(log_file),
                "restart_type": restart_type,
                "wait_time": wait_time,
                "restart_consumer": restart_consumer,
                "acceptance_mode": acceptance_mode,
                "restart_attempt_id": restart_attempt_id,
                "restart_provenance": restart_provenance,
                "restart_provenance_env": restart_provenance_env,
            }))
            if agent_managed_restart:
                managed_load_receipt = managed_load_store.mark_restart_accepted(
                    managed_load_receipt["load_operation_id"], restart_attempt_id
                )
        except Exception:
            try:
                pending_restart_file.unlink()
            except FileNotFoundError:
                pass
            if wrote_continuation:
                try:
                    rt.RESTART_MARKER.unlink()
                except FileNotFoundError:
                    pass
            if managed_load_created and managed_load_store and managed_load_receipt:
                managed_load_store.mark_failure(
                    managed_load_receipt["load_operation_id"],
                    restart_attempt_id=restart_attempt_id,
                    phase="pre_spawn",
                    code="marker_or_acceptance_write_failed",
                )
            raise

        if agent_managed_restart:
            try:
                pending_restart_file.unlink()
            except FileNotFoundError:
                pass
            try:
                managed_load_receipt = managed_load_store.mark_spawn_dispatched(
                    managed_load_receipt["load_operation_id"], restart_attempt_id
                )
                logger.info(
                    "RESTART: spawning agent-managed restart subprocess "
                    "(attempt_id=%s, script=%s)",
                    restart_attempt_id,
                    restart_script,
                )
                _spawn_managed_restart_subprocess_compat(
                    str(restart_script),
                    str(log_file),
                    provenance_env=restart_provenance_env,
                )
            except Exception:
                managed_load_store.mark_failure(
                    managed_load_receipt["load_operation_id"],
                    restart_attempt_id=restart_attempt_id,
                    phase="replacement",
                    code="spawn_dispatch_uncertain",
                )
                raise

        agent_invocation_count = len(continuation.get("agent_invocations", []))
        bystander_count = len(all_active) - 1  # Exclude the triggering session

        bystander_note = ""
        if bystander_count > 0:
            bystander_note = f"\n{bystander_count} other active session(s) will also be resumed after restart."
        if agent_invocation_count > 0:
            bystander_note += f"\n{agent_invocation_count} active agent invocation(s) will also be resumed after restart."

        if agent_managed_restart:
            return _managed_load_receipt_result(managed_load_receipt, existing=False)

        return {
            "content": [{
                "type": "text",
                "text": (
                    f"Restart initiated for session {session_id}.\n"
                    f"Source: {source_agent}\n"
                    f"Restart attempt: {restart_attempt_id}\n"
                    f"Reason: {reason}\n"
                    f"Mode: {restart_type}\n"
                    f"The server will restart in ~{wait_time} seconds.\n"
                    f"After restart, you'll receive a continuation message."
                    f"{bystander_note}"
                    f"{running_agents_bootstrap_note}"
                )
            }]
        }

    except Exception as e:
        import traceback
        return {
            "content": [{
                "type": "text",
                "text": f"Error initiating restart: {str(e)}\n{traceback.format_exc()}"
            }],
            "is_error": True
        }
