"""
Tool call serialization for chat history.

Each tool gets its own serializer that decides which params and output to preserve.
Serialized tool calls are stored as hidden messages in chat history and injected
into conversation context on subsequent turns.
"""

from typing import Any, Callable, Dict, Optional
import json
import logging
import re
import uuid
from datetime import datetime, timedelta

from tool_output_artifacts import format_raw_output_pointer

logger = logging.getLogger(__name__)

# MCP prefix used by Second Brain tools
MCP_PREFIX = "mcp__brain__"


def _truncate(text: str, max_chars: int) -> str:
    """Truncate text, preserving complete lines where possible."""
    if not text or len(text) <= max_chars:
        return text or ""
    # Try to break at a newline
    cut = text[:max_chars]
    last_nl = cut.rfind("\n")
    if last_nl > max_chars * 0.6:
        return cut[:last_nl] + "\n..."
    return cut + "..."


def _one_line(text: str, max_chars: int) -> str:
    compact = " ".join(str(text or "").split())
    return _truncate(compact, max_chars)


def _parse_args(args_raw) -> dict:
    """Parse args from string or dict."""
    if isinstance(args_raw, dict):
        return args_raw
    if isinstance(args_raw, str):
        try:
            return json.loads(args_raw)
        except (json.JSONDecodeError, TypeError):
            return {"_raw": args_raw}
    return {}


def _has_text(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, list):
        return any(_has_text(item) for item in value)
    return value is not None


def _parse_json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _native_web_tool_name(tool_name: str) -> bool:
    normalized = str(tool_name or "").replace("_", "").replace("-", "").lower()
    return normalized in {
        "websearch",
        "websearchcall",
        "webfetch",
        "webfetchcall",
    }


def _web_args_have_signal(args: dict) -> bool:
    if any(_has_text(args.get(key)) for key in ("query", "queries", "url", "pattern")):
        return True
    action = args.get("action")
    if isinstance(action, dict):
        return any(_has_text(action.get(key)) for key in ("query", "queries", "url", "pattern"))
    return False


def _extract_web_payload(payload: dict) -> dict:
    recovered = {
        key: payload.get(key)
        for key in ("query", "queries", "url", "pattern")
        if _has_text(payload.get(key))
    }
    action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
    action_payload = {
        key: action.get(key)
        for key in ("type", "query", "queries", "url", "pattern")
        if _has_text(action.get(key))
    }
    if action_payload:
        recovered["action"] = action_payload
        for key in ("query", "queries", "url", "pattern"):
            if key not in recovered and _has_text(action_payload.get(key)):
                recovered[key] = action_payload[key]
    return recovered


def recover_tool_args_from_output(tool_name: str, args_raw: Any, output: str) -> dict:
    """Recover native tool args when the final receipt has better parameter data."""
    args = _parse_args(args_raw)
    if not _native_web_tool_name(tool_name) or _web_args_have_signal(args):
        return args

    payload = _parse_json_object(output)
    recovered = _extract_web_payload(payload)
    if not recovered:
        return args

    merged = dict(args)
    for key, value in recovered.items():
        current = merged.get(key)
        if not _has_text(current) or key == "action":
            merged[key] = value
    return merged


def _pick_native_web_args(args: dict) -> dict:
    return {
        key: args[key]
        for key in ("query", "queries", "url", "pattern", "action")
        if key in args and (key == "action" or _has_text(args.get(key)))
    }


def _pick(d: dict, keys: list) -> dict:
    """Pick specific keys from a dict."""
    return {k: d[k] for k in keys if k in d}


# ── Tier 1: Full params + truncated output ──

def serialize_bash(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["command", "description"]),
        "output_summary": _truncate(output, 500),
    }


def serialize_invoke_agent(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["agent", "mode", "model_override"])
    if "prompt" in args:
        kept["prompt"] = str(args["prompt"])  # Prompts are already compressed; store verbatim
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_invoke_agent_chain(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["chain", "agents", "context", "on_failure", "summarize"])
    if "initial_prompt" in args:
        kept["initial_prompt"] = str(args["initial_prompt"])  # Store verbatim
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_invoke_agent_parallel(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["context"])
    if "agents" in args and isinstance(args["agents"], list):
        # Preserve full agent/prompt pairs verbatim — prompts are already compressed
        kept["agents"] = [
            {k: v for k, v in inv.items() if k in ("agent", "prompt", "model_override")}
            for inv in args["agents"]
        ]
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_native_web(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick_native_web_args(args),
        "output_summary": _truncate(output, 500),
    }


def serialize_consult_llm(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["provider", "model", "temperature"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 300)
    return {
        "args": kept,
        "output_summary": _truncate(output, 500),
    }


def serialize_generate_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["prompt", "aspect_ratio", "resolution"]),
        "output_summary": output,  # Just file path + dims, keep full
    }


def serialize_edit_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["image_path", "prompt", "aspect_ratio"]),
        "output_summary": output,
    }


def serialize_fal_text_to_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["prompt", "model", "image_size", "num_images", "negative_prompt", "seed"]),
        "output_summary": output,  # File paths + seed, keep full
    }


def serialize_fal_image_to_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["image_path", "prompt", "model", "strength", "image_size"]),
        "output_summary": output,
    }


def serialize_fal_multi_ref_image(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["image_paths", "prompt", "model", "image_size"]),
        "output_summary": output,
    }


def serialize_atlas_generate_images_parallel(args: dict, output: str, is_error: bool) -> dict:
    jobs = args.get("jobs")
    job_count = len(jobs) if isinstance(jobs, list) else 0
    kept_jobs = []
    for index, job in enumerate(jobs[:4] if isinstance(jobs, list) else []):
        if not isinstance(job, dict):
            kept_jobs.append({"index": index})
            continue
        metadata = {
            "index": index,
            "prompt": _one_line(job.get("prompt", ""), 240),
            "model": _one_line(job.get("model", "default"), 160),
            "reference_count": len(job.get("image_paths", []))
            if isinstance(job.get("image_paths"), list)
            else 0,
        }
        if isinstance(job.get("id"), str):
            metadata["id"] = _one_line(job["id"], 64)
        kept_jobs.append(metadata)
    return {
        "args": {"job_count": job_count, "jobs": kept_jobs},
        "output_summary": output,
    }


# ── Tier 2: Key params + IDs from output ──

def serialize_gmail_send(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["to", "subject"]),
        "output_summary": _truncate(output, 300),
    }


def serialize_gmail_reply(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["message_id", "reply_all"]),
        "output_summary": _truncate(output, 300),
    }


def serialize_gmail_draft_create(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["to", "subject"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_gmail_trash(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["message_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_gmail_modify_labels(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["message_ids", "add_labels", "remove_labels"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_google_create_tasks_and_events(args: dict, output: str, is_error: bool) -> dict:
    kept = {}
    if "tasks" in args and isinstance(args["tasks"], list):
        kept["tasks"] = [t.get("title", "?") for t in args["tasks"][:5]]
    if "events" in args and isinstance(args["events"], list):
        kept["events"] = [e.get("summary", "?") for e in args["events"][:5]]
    return {
        "args": kept,
        "output_summary": _truncate(output, 300),
    }


def serialize_google_update_task(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id", "title", "status", "due", "notes"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_google_delete_task(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_moltbook_post(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["title", "submolt"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_moltbook_comment(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["post_id"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_spotify_create_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["name"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_spotify_add_to_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id", "track_ids"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_spotify_playback_control(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["action"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_ytmusic_create_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["title"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_ytmusic_add_to_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id", "song_ids"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_ytmusic_remove_from_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id", "song_ids"]),
        "output_summary": _truncate(output, 200),
    }


def serialize_ytmusic_delete_playlist(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["playlist_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_schedule_self(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["schedule"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 200)
    return {
        "args": kept,
        "output_summary": _truncate(output, 200),
    }


def serialize_schedule_agent(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["agent", "schedule"])
    if "prompt" in args:
        kept["prompt"] = _truncate(str(args["prompt"]), 200)
    return {
        "args": kept,
        "output_summary": _truncate(output, 200),
    }


def serialize_scheduler_update(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id", "schedule", "prompt", "enabled"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_scheduler_remove(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["task_id"]),
        "output_summary": _truncate(output, 100),
    }


def serialize_forms_save(args: dict, output: str, is_error: bool) -> dict:
    return {
        "args": _pick(args, ["form_id", "submission_id"]),
        "output_summary": _truncate(output, 100),
    }


# ── Tier 3: Query tools — truncated results ──

def serialize_query_tool(keep_params: list, output_limit: int = 500):
    """Factory for query tool serializers."""
    def serializer(args: dict, output: str, is_error: bool) -> dict:
        return {
            "args": _pick(args, keep_params),
            "output_summary": _truncate(output, output_limit),
        }
    return serializer


# ── Tier 4: Compact — just note it happened ──

def serialize_working_memory_add(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["tag", "ttl"])
    if "content" in args:
        kept["content"] = _truncate(str(args["content"]), 100)
    return {"args": kept, "output_summary": ""}


def serialize_working_memory_update(args: dict, output: str, is_error: bool) -> dict:
    kept = _pick(args, ["index"])
    if "content" in args:
        kept["content"] = _truncate(str(args["content"]), 100)
    return {"args": kept, "output_summary": ""}


def serialize_compact(keep_params: list):
    """Factory for compact serializers (Tier 4) — minimal footprint."""
    def serializer(args: dict, output: str, is_error: bool) -> dict:
        return {
            "args": _pick(args, keep_params),
            "output_summary": _truncate(output, 100) if output else "",
        }
    return serializer


def serialize_memory_read(args: dict, output: str, is_error: bool) -> dict:
    return {"args": {}, "output_summary": "read memory.md"}


_RESTART_FAIL_CLOSED_PHRASES = (
    "restart_server cannot safely restart from this invocation context",
    "No pending restart or continuation marker was written",
)
_RESTART_NO_CONSUMER_PHRASE = "did not provide a restart consumer"
_RESTART_ACCEPTED_PHRASES = (
    "Restart initiated",
    "The server will restart",
    "Managed restart accepted",
)


def serialize_restart_server(args: dict, output: str, is_error: bool) -> dict:
    text = str(output or "").strip()

    if any(phrase in text for phrase in _RESTART_FAIL_CLOSED_PHRASES):
        if _RESTART_NO_CONSUMER_PHRASE in text:
            summary = "restart refused: no restart consumer"
        elif "No pending restart or continuation marker was written" in text:
            summary = "restart refused: no marker written"
        else:
            summary = "restart refused"
        return {"args": {}, "output_summary": summary}

    if is_error:
        detail = _one_line(text, 240)
        summary = f"restart error: {detail}" if detail else "restart error"
        return {"args": {}, "output_summary": summary}

    if any(phrase in text for phrase in _RESTART_ACCEPTED_PHRASES):
        return {"args": {}, "output_summary": "restart initiated"}

    if text:
        return {"args": {}, "output_summary": _one_line(text, 300)}

    return {"args": {}, "output_summary": "restart request finished"}


_SCHEDULER_STATUS_SCHEMA = "second_brain.scheduler_status.v1"
_SCHEDULER_STATUS_FALLBACK = "scheduler_status result unavailable"
_SCHEDULER_STATUS_TASK_ID_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$"
)
_SCHEDULER_STATUS_ATTEMPT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SCHEDULER_STATUS_CODES = frozenset({
    "task_status",
    "attempt_status",
    "invalid_request",
    "invalid_task_id",
    "invalid_attempt_id",
    "task_not_found",
    "attempt_not_found",
    "task_attempt_mismatch",
    "task_identity_conflict",
    "attempt_identity_conflict",
    "store_malformed",
    "store_unavailable",
})
_SCHEDULER_STATUS_STATES = frozenset({
    "claimed", "running", "succeeded", "failed", "malformed",
})
_SCHEDULER_STATUS_RECEIPT_STATUSES = frozenset({
    "no_attempts",
    "legacy_no_execution_receipt",
    "attempts",
    "attempts_with_malformed",
    "malformed_only",
})
_SCHEDULER_STATUS_ERROR_CLASSES = frozenset({
    "validation",
    "launch",
    "execution",
    "timeout",
    "cancelled",
    "interrupted",
    "delivery",
    "unknown",
})
_SCHEDULER_STATUS_ERROR_CODES = frozenset({
    "missing_agent",
    "target_room_missing",
    "inner_setup_rejected",
    "inner_launch_failed",
    "outer_correlation_failed",
    "running_gate_failed",
    "runner_error",
    "runner_timeout",
    "runner_cancelled",
    "wrapper_timeout",
    "wrapper_cancelled",
    "output_save_failed",
    "thread_finalization_failed",
    "interrupted_before_start",
    "interrupted_uncertain",
    "stale_live_row",
    "malformed_receipt",
    "unknown",
})
_SCHEDULER_STATUS_TOP_KEYS = frozenset({
    "schema", "ok", "code", "query", "task", "attempts", "attempt",
})
_SCHEDULER_STATUS_ATTEMPT_KEYS = frozenset({
    "task_id",
    "attempt_id",
    "state",
    "claimed_at",
    "running_at",
    "terminal_at",
    "updated_at",
    "resume_count",
    "error_class",
    "error_code",
    "receipt_error",
})


def _scheduler_status_is_task_id(value: Any) -> bool:
    return isinstance(value, str) and bool(_SCHEDULER_STATUS_TASK_ID_RE.fullmatch(value))


def _scheduler_status_is_canonical_attempt_id(value: Any) -> bool:
    if not isinstance(value, str) or not _SCHEDULER_STATUS_ATTEMPT_ID_RE.fullmatch(value):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _scheduler_status_is_output_attempt_id(value: Any) -> bool:
    if not _scheduler_status_is_task_id(value):
        return False
    try:
        uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _scheduler_status_safe_args(args: dict) -> dict:
    safe = {}
    task_id = args.get("task_id") if isinstance(args, dict) else None
    attempt_id = args.get("attempt_id") if isinstance(args, dict) else None
    if _scheduler_status_is_task_id(task_id):
        safe["task_id"] = task_id
    if _scheduler_status_is_canonical_attempt_id(attempt_id):
        safe["attempt_id"] = attempt_id
    return safe


def _scheduler_status_is_timestamp(value: Any) -> bool:
    if value is None:
        return True
    if (
        not isinstance(value, str)
        or not value.endswith("Z")
        or len(value) > 40
        or not re.fullmatch(r"[0-9T:.\-]+Z", value)
    ):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.utcoffset() == timedelta(0)


def _scheduler_status_attempt_projection(value: Any) -> Optional[dict]:
    if not isinstance(value, dict) or set(value) != _SCHEDULER_STATUS_ATTEMPT_KEYS:
        return None
    task_id = value.get("task_id")
    if task_id is not None and not _scheduler_status_is_task_id(task_id):
        return None
    if not _scheduler_status_is_output_attempt_id(value.get("attempt_id")):
        return None
    state = value.get("state")
    if state not in _SCHEDULER_STATUS_STATES:
        return None
    for key in ("claimed_at", "running_at", "terminal_at", "updated_at"):
        if not _scheduler_status_is_timestamp(value.get(key)):
            return None
    resume_count = value.get("resume_count")
    if type(resume_count) is not int or resume_count < 0:
        return None
    error_class = value.get("error_class")
    error_code = value.get("error_code")
    receipt_error = value.get("receipt_error")
    if state == "malformed":
        if any(value.get(key) is not None for key in (
            "claimed_at", "running_at", "terminal_at", "updated_at",
        )):
            return None
        if (
            resume_count != 0
            or error_class != "validation"
            or error_code != "malformed_receipt"
            or receipt_error != "malformed_receipt"
        ):
            return None
    else:
        if value.get("claimed_at") is None or value.get("updated_at") is None:
            return None
        if receipt_error is not None:
            return None
        if error_class is not None and error_class not in _SCHEDULER_STATUS_ERROR_CLASSES:
            return None
        if error_code is not None and error_code not in _SCHEDULER_STATUS_ERROR_CODES:
            return None

    return {key: value[key] for key in (
        "task_id",
        "attempt_id",
        "state",
        "claimed_at",
        "running_at",
        "terminal_at",
        "updated_at",
        "resume_count",
        "error_class",
        "error_code",
        "receipt_error",
    )}


def _scheduler_status_closed_projection(value: Any, safe_args: dict) -> Optional[dict]:
    if not isinstance(value, dict) or set(value) != _SCHEDULER_STATUS_TOP_KEYS:
        return None
    if value.get("schema") != _SCHEDULER_STATUS_SCHEMA:
        return None
    if type(value.get("ok")) is not bool or value.get("code") not in _SCHEDULER_STATUS_CODES:
        return None
    code = value["code"]
    if value["ok"] != (code in {"task_status", "attempt_status"}):
        return None

    query = value.get("query")
    if not isinstance(query, dict) or set(query) - {"task_id", "attempt_id"}:
        return None
    if query != safe_args:
        return None

    task = value.get("task")
    projected_task = None
    if task is not None:
        if not isinstance(task, dict) or set(task) != {"task_id", "definition_state"}:
            return None
        if (
            not _scheduler_status_is_task_id(task.get("task_id"))
            or task.get("definition_state") not in {"active", "inactive"}
        ):
            return None
        projected_task = {
            "task_id": task["task_id"],
            "definition_state": task["definition_state"],
        }

    attempts = value.get("attempts")
    projected_attempts = None
    if attempts is not None:
        if not isinstance(attempts, dict) or set(attempts) != {
            "receipt_status", "retained_count", "state_counts", "latest",
        }:
            return None
        if attempts.get("receipt_status") not in _SCHEDULER_STATUS_RECEIPT_STATUSES:
            return None
        retained_count = attempts.get("retained_count")
        if type(retained_count) is not int or retained_count < 0:
            return None
        state_counts = attempts.get("state_counts")
        if not isinstance(state_counts, dict) or set(state_counts) != _SCHEDULER_STATUS_STATES:
            return None
        if any(type(count) is not int or count < 0 for count in state_counts.values()):
            return None
        if sum(state_counts.values()) != retained_count:
            return None
        latest = attempts.get("latest")
        projected_latest = (
            None if latest is None else _scheduler_status_attempt_projection(latest)
        )
        if latest is not None and (
            projected_latest is None or projected_latest["state"] == "malformed"
        ):
            return None
        projected_attempts = {
            "receipt_status": attempts["receipt_status"],
            "retained_count": retained_count,
            "state_counts": {
                state: state_counts[state]
                for state in ("claimed", "running", "succeeded", "failed", "malformed")
            },
            "latest": projected_latest,
        }

    exact_attempt = value.get("attempt")
    projected_exact_attempt = (
        None if exact_attempt is None else _scheduler_status_attempt_projection(exact_attempt)
    )
    if exact_attempt is not None and projected_exact_attempt is None:
        return None

    if code == "task_status":
        if projected_task is None or projected_attempts is None or projected_exact_attempt is not None:
            return None
    elif code == "attempt_status":
        if projected_attempts is not None or projected_exact_attempt is None:
            return None
    elif any(item is not None for item in (
        projected_task, projected_attempts, projected_exact_attempt,
    )):
        return None

    return {
        "schema": _SCHEDULER_STATUS_SCHEMA,
        "ok": value["ok"],
        "code": code,
        "query": dict(query),
        "task": projected_task,
        "attempts": projected_attempts,
        "attempt": projected_exact_attempt,
    }


def serialize_scheduler_status(args: dict, output: str, is_error: bool) -> dict:
    safe_args = _scheduler_status_safe_args(args)
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return {"args": safe_args, "output_summary": _SCHEDULER_STATUS_FALLBACK}
    projection = _scheduler_status_closed_projection(parsed, safe_args)
    if projection is None:
        return {"args": safe_args, "output_summary": _SCHEDULER_STATUS_FALLBACK}
    return {
        "args": safe_args,
        "output_summary": json.dumps(
            projection,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
    }




# ── Registry ──
# Keys are tool names WITHOUT the MCP prefix.
# The lookup function strips the prefix before matching.

TOOL_SERIALIZERS: Dict[str, Callable[[dict, str, bool], dict]] = {
    # Tier 1
    "bash": serialize_bash,
    "Bash": serialize_bash,
    "WebSearch": serialize_native_web,
    "WebFetch": serialize_native_web,
    "web_search_call": serialize_native_web,
    "webSearch": serialize_native_web,
    "webFetch": serialize_native_web,
    "invoke_agent": serialize_invoke_agent,
    "invoke_agent_chain": serialize_invoke_agent_chain,
    "invoke_agent_parallel": serialize_invoke_agent_parallel,
    "consult_llm": serialize_consult_llm,
    "generate_image": serialize_generate_image,
    "edit_image": serialize_edit_image,
    "fal_text_to_image": serialize_fal_text_to_image,
    "fal_image_to_image": serialize_fal_image_to_image,
    "fal_multi_ref_image": serialize_fal_multi_ref_image,
    "atlas_generate_images_parallel": serialize_atlas_generate_images_parallel,

    # Tier 2
    "gmail_send": serialize_gmail_send,
    "gmail_reply": serialize_gmail_reply,
    "gmail_draft_create": serialize_gmail_draft_create,
    "gmail_trash": serialize_gmail_trash,
    "gmail_modify_labels": serialize_gmail_modify_labels,
    "google_create_tasks_and_events": serialize_google_create_tasks_and_events,
    "google_update_task": serialize_google_update_task,
    "google_delete_task": serialize_google_delete_task,
    "moltbook_post": serialize_moltbook_post,
    "moltbook_comment": serialize_moltbook_comment,
    "spotify_create_playlist": serialize_spotify_create_playlist,
    "spotify_add_to_playlist": serialize_spotify_add_to_playlist,
    "spotify_playback_control": serialize_spotify_playback_control,
    "ytmusic_create_playlist": serialize_ytmusic_create_playlist,
    "ytmusic_add_to_playlist": serialize_ytmusic_add_to_playlist,
    "ytmusic_remove_from_playlist": serialize_ytmusic_remove_from_playlist,
    "ytmusic_delete_playlist": serialize_ytmusic_delete_playlist,
    "schedule_self": serialize_schedule_self,
    "schedule_agent": serialize_schedule_agent,
    "scheduler_status": serialize_scheduler_status,
    "scheduler_update": serialize_scheduler_update,
    "scheduler_remove": serialize_scheduler_remove,
    "forms_save": serialize_forms_save,

    # Tier 3
    "gmail_list_messages": serialize_query_tool(["query", "max_results"]),
    "gmail_get_message": serialize_query_tool(["message_id"]),
    "gmail_list_labels": serialize_query_tool([], 300),
    "google_list": serialize_query_tool(["limit"]),
    "web_search": serialize_query_tool(["query"]),
    "page_parser": serialize_query_tool(["url"]),
    "moltbook_feed": serialize_query_tool(["sort", "submolt"]),
    "moltbook_get_post": serialize_query_tool(["post_id"]),
    "moltbook_notifications": serialize_query_tool([], 300),
    "moltbook_account_status": serialize_query_tool([], 500),
    "moltbook_check_dms": serialize_query_tool([], 500),
    "moltbook_respond_challenge": serialize_query_tool(["answer", "challenge_id"], 500),
    "moltbook_challenge_log": serialize_query_tool(["limit"], 500),
"spotify_search": serialize_query_tool(["query", "search_type"]),
    "spotify_recently_played": serialize_query_tool(["limit"]),
    "spotify_top_items": serialize_query_tool(["item_type", "time_range"]),
    "spotify_get_playlists": serialize_query_tool([]),
    "spotify_now_playing": serialize_query_tool([], 200),
    "ytmusic_search": serialize_query_tool(["query", "search_type"]),
    "ytmusic_get_playlists": serialize_query_tool([]),
    "ytmusic_get_playlist_items": serialize_query_tool(["playlist_id"]),
    "ytmusic_get_liked": serialize_query_tool([]),
    "fal_list_models": serialize_query_tool(["query", "category", "endpoint_id"]),

    # Tier 4
    "working_memory_add": serialize_working_memory_add,
    "working_memory_update": serialize_working_memory_update,
    "working_memory_remove": serialize_compact(["index"]),
    "working_memory_list": serialize_compact([]),
    "working_memory_snapshot": serialize_compact(["index", "section"]),
    "memory_create": serialize_compact(["triggers"]),
    "memory_update": serialize_compact(["id"]),
    "memory_delete": serialize_compact(["id"]),
    "forms_define": serialize_compact(["form_id", "title"]),
    "forms_show": serialize_compact(["form_id"]),
    "forms_list": serialize_compact(["form_id"]),
    "restart_server": serialize_restart_server,
    "chess": serialize_compact(["move", "action"]),
"scheduler_list": serialize_query_tool([], 300),
    "Skill": serialize_compact(["skill", "args"]),
    "compact_conversation": serialize_compact(["keep_exchanges", "reason"]),
}


def _default_serializer(args: dict, output: str, is_error: bool) -> dict:
    """Fallback for unknown tools: Tier 3 behavior."""
    truncated_args = {}
    for k, v in list(args.items())[:5]:
        truncated_args[k] = _truncate(str(v), 200) if isinstance(v, str) and len(str(v)) > 200 else v
    return {
        "args": truncated_args,
        "output_summary": _truncate(output, 300),
    }


def serialize_tool_call(
    tool_name: str,
    args_raw: Any,
    output: str,
    is_error: bool,
    tool_id: Optional[str] = None,
    raw_output: Optional[Dict[str, Any]] = None,
) -> dict:
    """
    Serialize a tool call for chat history storage.

    Returns a dict ready to be stored as a message with role="tool_call".
    """
    # Strip MCP prefix for registry lookup
    lookup_name = tool_name
    if lookup_name.startswith(MCP_PREFIX):
        lookup_name = lookup_name[len(MCP_PREFIX):]

    args = recover_tool_args_from_output(tool_name, args_raw, output or "")
    serializer = TOOL_SERIALIZERS.get(lookup_name, _default_serializer)

    try:
        result = serializer(args, output or "", is_error)
    except Exception as e:
        if lookup_name == "scheduler_status":
            logger.warning("Serializer error for scheduler_status")
            result = {"args": {}, "output_summary": _SCHEDULER_STATUS_FALLBACK}
        else:
            logger.warning(f"Serializer error for {tool_name}: {e}")
            result = _default_serializer(args, output or "", is_error)

    message = {
        "role": "tool_call",
        "hidden": True,
        "tool_name": tool_name,
        "tool_id": tool_id or "",
        "args": result.get("args", {}),
        "output_summary": result.get("output_summary", ""),
        "is_error": is_error,
    }
    if raw_output and lookup_name != "scheduler_status":
        raw_output_metadata = dict(raw_output)
        raw_output_metadata["history_truncated"] = result.get("output_summary", "") != (output or "")
        message["raw_output"] = raw_output_metadata
    return message


def format_tool_for_history(tool_msg: dict) -> str:
    """
    Format a stored tool_call message as a compact one-liner for history injection.

    Example output:
      [Tool: bash | cmd: `git status` | Output: On branch main...]
    """
    name = tool_msg.get("tool_name", "unknown")
    # Strip MCP prefix for display
    display_name = name
    if display_name.startswith(MCP_PREFIX):
        display_name = display_name[len(MCP_PREFIX):]

    args = tool_msg.get("args", {})
    output = tool_msg.get("output_summary", "")
    is_error = tool_msg.get("is_error", False)
    raw_output_pointer = format_raw_output_pointer(tool_msg.get("raw_output"))

    # Agent invocation tools — preserve prompts verbatim in history
    agent_tools = {"invoke_agent", "invoke_agent_chain", "invoke_agent_parallel"}
    is_agent_tool = display_name in agent_tools

    # Build param string from args
    param_parts = []
    for k, v in args.items():
        val = str(v)
        # Don't truncate prompt fields for agent invocations
        if not (is_agent_tool and k in ("prompt", "initial_prompt", "agents")):
            if len(val) > 120:
                val = val[:117] + "..."
        param_parts.append(f"{k}: {val}")
    params_str = " | ".join(param_parts) if param_parts else ""

    # Build output portion
    output_str = ""
    if output:
        prefix = "Error" if is_error else "Output"
        output_str = f" | {prefix}: {output}"
    if raw_output_pointer:
        output_str += f" | Raw output: {raw_output_pointer}"

    if params_str:
        return f"[Tool: {display_name} | {params_str}{output_str}]"
    else:
        return f"[Tool: {display_name}{output_str}]"
