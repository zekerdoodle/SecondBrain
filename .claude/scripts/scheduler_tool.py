import os
import json
import uuid
import fcntl
import time
import tempfile
import re
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

# Import atomic file ops with fallback for different run contexts
try:
    from .atomic_file_ops import load_json, save_json
except ImportError:
    from atomic_file_ops import load_json, save_json

# Setup
BASE_DIR = Path(__file__).parent
TASKS_FILE = BASE_DIR / "scheduled_tasks.json"
UTC = timezone.utc
CHICAGO = ZoneInfo("America/Chicago")
_ONCE_PREFIX_RE = re.compile(r"^\s*once\b", re.IGNORECASE)
_ONCE_SCHEDULE_RE = re.compile(
    r"^\s*once\s+at\s+(?P<timestamp>\S(?:.*\S)?)\s*$",
    re.IGNORECASE,
)
_SCHEDULE_ERROR_LIMIT = 240
_ATTEMPT_SCHEMA = "second_brain.scheduler_attempt.v1"
_ATTEMPT_RETENTION_TERMINAL = 64
_ATTEMPT_STATES = frozenset({"claimed", "running", "succeeded", "failed"})
_ATTEMPT_TERMINAL_STATES = frozenset({"succeeded", "failed"})
_ATTEMPT_ERROR_CLASSES = frozenset({
    "validation",
    "launch",
    "execution",
    "timeout",
    "cancelled",
    "interrupted",
    "delivery",
    "unknown",
})
_ATTEMPT_ERROR_CODES = frozenset({
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
_ATTEMPT_FIELDS = frozenset({
    "schema",
    "attempt_id",
    "task_id",
    "task_type",
    "agent",
    "state",
    "claimed_at",
    "running_at",
    "terminal_at",
    "updated_at",
    "outer_invocation_id",
    "current_inner_invocation_id",
    "conversation_id",
    "continuation_claim_id",
    "resume_count",
    "error_class",
    "error_code",
})
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/+\-]{0,255}$")
_CANONICAL_ATTEMPT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_STATUS_SCHEMA = "second_brain.scheduler_status.v1"
_STATUS_STATE_KEYS = ("claimed", "running", "succeeded", "failed", "malformed")


class OneTimeScheduleError(ValueError):
    """A one-time-looking schedule is malformed or names an invalid wall time."""


class SchedulerAttemptError(RuntimeError):
    """A scheduler attempt receipt or lifecycle transition is invalid."""


class _SchedulerStatusStoreError(RuntimeError):
    """Internal fixed-code failure from the read-only status snapshot boundary."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _utc_timestamp(now=None):
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_utc_timestamp(value, *, field):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SchedulerAttemptError(f"{field} must be a canonical UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise SchedulerAttemptError(f"{field} is not a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise SchedulerAttemptError(f"{field} must be UTC")
    return parsed


def _next_attempt_timestamp(attempt):
    now = datetime.now(UTC)
    previous = _parse_utc_timestamp(attempt["updated_at"], field="updated_at")
    if now <= previous:
        now = previous + timedelta(microseconds=1)
    return _utc_timestamp(now)


def _validate_safe_id(value, *, field, nullable=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise SchedulerAttemptError(f"{field} must be a bounded content-free identifier")


def _is_canonical_attempt_id(value):
    if not isinstance(value, str) or not _CANONICAL_ATTEMPT_ID_RE.fullmatch(value):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _validate_attempt(attempt, *, expected_task_id=None):
    if not isinstance(attempt, dict):
        raise SchedulerAttemptError("attempt must be an object")
    if set(attempt) != _ATTEMPT_FIELDS:
        raise SchedulerAttemptError("attempt fields do not match the strict v1 schema")
    if attempt.get("schema") != _ATTEMPT_SCHEMA:
        raise SchedulerAttemptError("attempt schema is not second_brain.scheduler_attempt.v1")

    _validate_safe_id(attempt.get("attempt_id"), field="attempt_id")
    try:
        uuid.UUID(attempt["attempt_id"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise SchedulerAttemptError("attempt_id must be a UUID") from exc
    _validate_safe_id(attempt.get("task_id"), field="task_id")
    if expected_task_id is not None and attempt["task_id"] != expected_task_id:
        raise SchedulerAttemptError("attempt task_id does not match its task definition")
    if attempt.get("task_type") not in {"agent", "prompt"}:
        raise SchedulerAttemptError("task_type must be agent or prompt")
    _validate_safe_id(attempt.get("agent"), field="agent", nullable=True)
    if attempt.get("task_type") == "agent" and attempt.get("agent") is None:
        raise SchedulerAttemptError("agent attempts require an agent identifier")

    state = attempt.get("state")
    if state not in _ATTEMPT_STATES:
        raise SchedulerAttemptError("attempt state is invalid")
    claimed_at = _parse_utc_timestamp(attempt.get("claimed_at"), field="claimed_at")
    updated_at = _parse_utc_timestamp(attempt.get("updated_at"), field="updated_at")
    if updated_at < claimed_at:
        raise SchedulerAttemptError("updated_at cannot precede claimed_at")

    running_at = attempt.get("running_at")
    terminal_at = attempt.get("terminal_at")
    parsed_running = (
        _parse_utc_timestamp(running_at, field="running_at")
        if running_at is not None else None
    )
    parsed_terminal = (
        _parse_utc_timestamp(terminal_at, field="terminal_at")
        if terminal_at is not None else None
    )
    if parsed_running is not None and parsed_running < claimed_at:
        raise SchedulerAttemptError("running_at cannot precede claimed_at")
    if parsed_terminal is not None:
        floor = parsed_running or claimed_at
        if parsed_terminal < floor or updated_at < parsed_terminal:
            raise SchedulerAttemptError("terminal timestamps are not monotonic")

    for field in (
        "outer_invocation_id",
        "current_inner_invocation_id",
        "conversation_id",
        "continuation_claim_id",
    ):
        _validate_safe_id(attempt.get(field), field=field, nullable=True)
    resume_count = attempt.get("resume_count")
    if type(resume_count) is not int or resume_count < 0:
        raise SchedulerAttemptError("resume_count must be a nonnegative integer")
    if (resume_count == 0) != (attempt.get("continuation_claim_id") is None):
        raise SchedulerAttemptError("continuation_claim_id and resume_count disagree")

    error_class = attempt.get("error_class")
    error_code = attempt.get("error_code")
    if state == "claimed":
        if any(value is not None for value in (running_at, terminal_at, error_class, error_code)):
            raise SchedulerAttemptError("claimed attempts cannot have running or terminal fields")
        if attempt.get("current_inner_invocation_id") is not None:
            raise SchedulerAttemptError("claimed attempts cannot have an inner invocation")
    elif state == "running":
        if running_at is None or attempt.get("current_inner_invocation_id") is None:
            raise SchedulerAttemptError("running attempts require running_at and an inner invocation")
        if any(value is not None for value in (terminal_at, error_class, error_code)):
            raise SchedulerAttemptError("running attempts cannot have terminal fields")
    elif state == "succeeded":
        if running_at is None or terminal_at is None:
            raise SchedulerAttemptError("succeeded attempts require running and terminal timestamps")
        if error_class is not None or error_code is not None:
            raise SchedulerAttemptError("succeeded attempts cannot have errors")
    else:
        if terminal_at is None:
            raise SchedulerAttemptError("failed attempts require terminal_at")
        if error_class not in _ATTEMPT_ERROR_CLASSES:
            raise SchedulerAttemptError("failed attempt error_class is not allowlisted")
        if error_code not in _ATTEMPT_ERROR_CODES:
            raise SchedulerAttemptError("failed attempt error_code is not allowlisted")
    return attempt


def _attempts_for_task(task):
    attempts = task.get("execution_attempts", [])
    return attempts if isinstance(attempts, list) else []


def _find_attempt(task, attempt_id):
    for index, raw in enumerate(_attempts_for_task(task)):
        if isinstance(raw, dict) and raw.get("attempt_id") == attempt_id:
            return index, _validate_attempt(raw, expected_task_id=task.get("id"))
    raise SchedulerAttemptError(f"attempt {attempt_id} was not found for task {task.get('id')}")


def _find_task(tasks, task_id):
    for task in tasks:
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise SchedulerAttemptError(f"task {task_id} was not found")


def _new_claimed_attempt(task, *, now=None):
    claimed_at = _utc_timestamp(now)
    task_type = task.get("type", "prompt")
    attempt = {
        "schema": _ATTEMPT_SCHEMA,
        "attempt_id": str(uuid.uuid4()),
        "task_id": task.get("id"),
        "task_type": task_type,
        "agent": task.get("agent") if task_type == "agent" else None,
        "state": "claimed",
        "claimed_at": claimed_at,
        "running_at": None,
        "terminal_at": None,
        "updated_at": claimed_at,
        "outer_invocation_id": None,
        "current_inner_invocation_id": None,
        "conversation_id": None,
        "continuation_claim_id": None,
        "resume_count": 0,
        "error_class": None,
        "error_code": None,
    }
    return _validate_attempt(attempt, expected_task_id=task.get("id"))


def _prune_execution_attempts(task, *, protected_attempt_id=None):
    if "execution_attempts" in task and not isinstance(task.get("execution_attempts"), list):
        return
    attempts = _attempts_for_task(task)
    valid_terminal = []
    keep_indices = set()
    for index, raw in enumerate(attempts):
        try:
            attempt = _validate_attempt(raw, expected_task_id=task.get("id"))
        except SchedulerAttemptError:
            keep_indices.add(index)
            continue
        if attempt["state"] in _ATTEMPT_TERMINAL_STATES:
            terminal_instant = _parse_utc_timestamp(
                attempt["terminal_at"],
                field="terminal_at",
            )
            valid_terminal.append((terminal_instant, index, attempt["attempt_id"]))
        else:
            keep_indices.add(index)
    valid_terminal.sort()
    for _, index, attempt_id in valid_terminal[-_ATTEMPT_RETENTION_TERMINAL:]:
        keep_indices.add(index)
    if protected_attempt_id:
        for _, index, attempt_id in valid_terminal:
            if attempt_id == protected_attempt_id:
                keep_indices.add(index)
                break
    task["execution_attempts"] = [
        raw for index, raw in enumerate(attempts) if index in keep_indices
    ]


def _bounded_error(prefix, error, limit=_SCHEDULE_ERROR_LIMIT):
    text = f"{prefix}: {error}"
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def _valid_chicago_instants(local_dt):
    """Return distinct UTC instants that round-trip to a naive Chicago wall time."""
    instants = []
    for fold in (0, 1):
        candidate = local_dt.replace(tzinfo=CHICAGO, fold=fold)
        instant = candidate.astimezone(UTC)
        round_trip = instant.astimezone(CHICAGO).replace(tzinfo=None)
        if round_trip == local_dt and instant not in instants:
            instants.append(instant)
    return instants


def _first_valid_chicago_instant_after_gap(local_dt):
    """Resolve a legacy nonexistent wall time to the first valid minute after its gap."""
    probe = local_dt.replace(second=0, microsecond=0)
    for _ in range(6 * 60 + 1):
        instants = _valid_chicago_instants(probe)
        if instants:
            return instants[0]
        probe += timedelta(minutes=1)
    return None


def _normalize_once_schedule(schedule_text, *, allow_legacy_gap=False):
    """Return a one-time schedule's aware UTC instant, or ``None`` if it is not one-time.

    Strings beginning with ``once`` are one-time-looking and must fully match the
    supported form. Offsetless timestamps are America/Chicago wall times; an
    explicit numeric offset or uppercase ``Z`` names an exact instant. Ambiguous
    Chicago fall-fold values choose fold=0. Newly submitted spring-gap values are
    invalid, while legacy persisted rows may opt into first-valid-instant catch-up.
    """
    raw = schedule_text if isinstance(schedule_text, str) else str(schedule_text or "")
    if not _ONCE_PREFIX_RE.match(raw):
        return None

    match = _ONCE_SCHEDULE_RE.fullmatch(raw)
    if not match:
        raise OneTimeScheduleError(
            "expected 'once at YYYY-MM-DDTHH:MM:SS' with an optional ISO numeric offset or uppercase Z"
        )

    timestamp_text = match.group("timestamp")
    try:
        parsed = datetime.fromisoformat(timestamp_text)
    except (TypeError, ValueError) as exc:
        raise OneTimeScheduleError(
            f"invalid ISO timestamp '{timestamp_text[:96]}'"
        ) from exc

    if parsed.tzinfo is not None and parsed.utcoffset() is not None:
        return parsed.astimezone(UTC)

    instants = _valid_chicago_instants(parsed)
    if instants:
        # Normal local values yield one instant. Fall-fold values yield two in
        # fold order, and the compatibility contract deliberately chooses fold=0.
        return instants[0]

    if allow_legacy_gap:
        catch_up = _first_valid_chicago_instant_after_gap(parsed)
        if catch_up is not None:
            return catch_up

    raise OneTimeScheduleError(
        f"nonexistent America/Chicago local time '{timestamp_text}' (DST spring-forward gap)"
    )


def _load_tasks():
    return load_json(TASKS_FILE, default=[])


def _read_status_tasks():
    """Read one scheduler snapshot without locks, writes, or fallback coercion."""
    try:
        with TASKS_FILE.open("r", encoding="utf-8") as handle:
            tasks = json.load(handle)
    except FileNotFoundError:
        return []
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _SchedulerStatusStoreError("store_malformed") from exc
    except OSError as exc:
        raise _SchedulerStatusStoreError("store_unavailable") from exc
    except Exception as exc:
        raise _SchedulerStatusStoreError("store_unavailable") from exc

    if not isinstance(tasks, list):
        raise _SchedulerStatusStoreError("store_malformed")
    return tasks


def _save_tasks(tasks):
    save_json(TASKS_FILE, tasks)


@contextmanager
def _transact_tasks():
    """
    Hold the file lock for an entire read→modify→write transaction.

    Prevents TOCTOU races where concurrent callers (e.g. check_due_tasks vs
    add_task from different processes) read stale data and clobber each other's
    writes. Uses the same .json.lock file as atomic_file_ops so the two
    locking mechanisms are mutually exclusive.

    Yields a mutable list of tasks. On normal exit the list is written back
    atomically (temp-file + rename). On exception the write is skipped.
    """
    lock_path = TASKS_FILE.with_suffix(f'{TASKS_FILE.suffix}.lock')
    lock_fd = None
    start_time = time.time()
    timeout = 10.0

    # Acquire exclusive lock (same lock file as atomic_file_ops).
    # Use 'a' mode (not 'w') to avoid truncation races, and never unlink
    # the lock file so all contenders share the same stable inode.
    while True:
        try:
            lock_fd = open(lock_path, 'a')
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (IOError, OSError):
            if lock_fd:
                try:
                    lock_fd.close()
                except Exception:
                    pass
                lock_fd = None
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"Could not acquire lock for {TASKS_FILE} within {timeout}s"
                )
            time.sleep(0.1)

    try:
        # --- Read (under lock, bypassing load_json's own lock) ---
        tasks = []
        if TASKS_FILE.exists():
            try:
                with open(TASKS_FILE, 'r', encoding='utf-8') as f:
                    tasks = json.load(f)
            except (json.JSONDecodeError, Exception):
                tasks = []

        yield tasks

        # --- Write atomically (only reached on normal exit) ---
        TASKS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode='w',
            encoding='utf-8',
            dir=TASKS_FILE.parent,
            prefix=f'.{TASKS_FILE.name}',
            suffix='.tmp',
            delete=False,
        ) as f:
            temp_path = Path(f.name)
            json.dump(tasks, f, indent=2, ensure_ascii=False, default=str)
        temp_path.replace(TASKS_FILE)
    finally:
        # Release lock but keep lock file (stable inode prevents races)
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def bind_attempt_outer(task_id, attempt_id, outer_invocation_id):
    """Persist the scheduled wrapper row while the attempt remains claimed."""
    _validate_safe_id(task_id, field="task_id")
    _validate_safe_id(attempt_id, field="attempt_id")
    _validate_safe_id(outer_invocation_id, field="outer_invocation_id")
    with _transact_tasks() as tasks:
        task = _find_task(tasks, task_id)
        _, attempt = _find_attempt(task, attempt_id)
        existing = attempt.get("outer_invocation_id")
        if existing == outer_invocation_id:
            return dict(attempt)
        if existing is not None:
            raise SchedulerAttemptError("outer invocation identity is immutable once set")
        if attempt["state"] != "claimed":
            raise SchedulerAttemptError("outer correlation must be persisted while claimed")
        attempt["outer_invocation_id"] = outer_invocation_id
        attempt["updated_at"] = _next_attempt_timestamp(attempt)
        _validate_attempt(attempt, expected_task_id=task_id)
        return dict(attempt)


def bind_attempt_continuation(
    task_id,
    attempt_id,
    *,
    conversation_id,
    current_inner_invocation_id,
    continuation_claim_id,
):
    """Bind one valid managed-restart claim without changing attempt identity."""
    for field, value in (
        ("task_id", task_id),
        ("attempt_id", attempt_id),
        ("conversation_id", conversation_id),
        ("current_inner_invocation_id", current_inner_invocation_id),
        ("continuation_claim_id", continuation_claim_id),
    ):
        _validate_safe_id(value, field=field)
    with _transact_tasks() as tasks:
        task = _find_task(tasks, task_id)
        _, attempt = _find_attempt(task, attempt_id)
        if attempt["state"] != "running":
            raise SchedulerAttemptError("only a running attempt can bind a continuation")
        if attempt.get("conversation_id") != conversation_id:
            raise SchedulerAttemptError("continuation conversation does not match the attempt")
        if attempt.get("current_inner_invocation_id") != current_inner_invocation_id:
            raise SchedulerAttemptError("continuation live-row claim is stale")
        if attempt.get("continuation_claim_id") == continuation_claim_id:
            return dict(attempt)
        attempt["continuation_claim_id"] = continuation_claim_id
        attempt["resume_count"] += 1
        attempt["updated_at"] = _next_attempt_timestamp(attempt)
        _validate_attempt(attempt, expected_task_id=task_id)
        return dict(attempt)


def mark_attempt_running(
    task_id,
    attempt_id,
    *,
    current_inner_invocation_id,
    conversation_id=None,
    continuation_claim_id=None,
):
    """Durably enter running before model/tool execution.

    Repeating the exact gate is idempotent. Replacing the current inner row is
    accepted only after ``bind_attempt_continuation`` recorded the same managed
    continuation claim and incremented ``resume_count``.
    """
    _validate_safe_id(task_id, field="task_id")
    _validate_safe_id(attempt_id, field="attempt_id")
    _validate_safe_id(
        current_inner_invocation_id,
        field="current_inner_invocation_id",
    )
    _validate_safe_id(conversation_id, field="conversation_id", nullable=True)
    _validate_safe_id(
        continuation_claim_id,
        field="continuation_claim_id",
        nullable=True,
    )
    with _transact_tasks() as tasks:
        task = _find_task(tasks, task_id)
        _, attempt = _find_attempt(task, attempt_id)
        state = attempt["state"]
        if state in _ATTEMPT_TERMINAL_STATES:
            raise SchedulerAttemptError("terminal attempts cannot re-enter running")

        existing_inner = attempt.get("current_inner_invocation_id")
        existing_conversation = attempt.get("conversation_id")
        if state == "claimed":
            if continuation_claim_id is not None:
                raise SchedulerAttemptError("a claimed attempt cannot resume")
            timestamp = _next_attempt_timestamp(attempt)
            attempt["state"] = "running"
            attempt["running_at"] = timestamp
            attempt["updated_at"] = timestamp
            attempt["current_inner_invocation_id"] = current_inner_invocation_id
            attempt["conversation_id"] = conversation_id
        elif (
            existing_inner == current_inner_invocation_id
            and existing_conversation == conversation_id
        ):
            if continuation_claim_id not in {None, attempt.get("continuation_claim_id")}:
                raise SchedulerAttemptError("running gate continuation claim does not match")
            return dict(attempt)
        else:
            if (
                continuation_claim_id is None
                or attempt.get("continuation_claim_id") != continuation_claim_id
                or attempt.get("resume_count", 0) < 1
                or existing_conversation != conversation_id
            ):
                raise SchedulerAttemptError(
                    "current inner invocation can change only for a bound same-attempt resume"
                )
            attempt["current_inner_invocation_id"] = current_inner_invocation_id
            attempt["updated_at"] = _next_attempt_timestamp(attempt)

        _validate_attempt(attempt, expected_task_id=task_id)
        return dict(attempt)


def finalize_attempt(
    task_id,
    attempt_id,
    state,
    *,
    error_class=None,
    error_code=None,
    conversation_id=None,
):
    """CAS-terminalize one attempt; same terminal repeats are idempotent."""
    _validate_safe_id(task_id, field="task_id")
    _validate_safe_id(attempt_id, field="attempt_id")
    _validate_safe_id(conversation_id, field="conversation_id", nullable=True)
    if state not in _ATTEMPT_TERMINAL_STATES:
        raise SchedulerAttemptError("terminal state must be succeeded or failed")
    if state == "succeeded":
        if error_class is not None or error_code is not None:
            raise SchedulerAttemptError("succeeded terminalization cannot include an error")
    else:
        if error_class not in _ATTEMPT_ERROR_CLASSES:
            raise SchedulerAttemptError("error_class is not allowlisted")
        if error_code not in _ATTEMPT_ERROR_CODES:
            raise SchedulerAttemptError("error_code is not allowlisted")

    with _transact_tasks() as tasks:
        task = _find_task(tasks, task_id)
        _, attempt = _find_attempt(task, attempt_id)
        existing_state = attempt["state"]
        if existing_state in _ATTEMPT_TERMINAL_STATES:
            if (
                existing_state == state
                and attempt.get("error_class") == error_class
                and attempt.get("error_code") == error_code
            ):
                if conversation_id not in {None, attempt.get("conversation_id")}:
                    raise SchedulerAttemptError("terminal conversation identity is immutable")
                return dict(attempt)
            raise SchedulerAttemptError("conflicting terminal rewrite rejected")
        if state == "succeeded" and existing_state != "running":
            raise SchedulerAttemptError("a claimed attempt cannot be labeled succeeded")
        if conversation_id is not None:
            existing_conversation = attempt.get("conversation_id")
            if existing_conversation not in {None, conversation_id}:
                raise SchedulerAttemptError("conversation identity is immutable once set")
            attempt["conversation_id"] = conversation_id

        timestamp = _next_attempt_timestamp(attempt)
        attempt["state"] = state
        attempt["terminal_at"] = timestamp
        attempt["updated_at"] = timestamp
        attempt["error_class"] = error_class
        attempt["error_code"] = error_code
        _validate_attempt(attempt, expected_task_id=task_id)
        _prune_execution_attempts(task, protected_attempt_id=attempt_id)
        return dict(attempt)


def _attempt_projection(task, raw):
    task_id = task.get("id")
    try:
        attempt = _validate_attempt(raw, expected_task_id=task_id)
    except SchedulerAttemptError:
        raw_attempt_id = raw.get("attempt_id") if isinstance(raw, dict) else None
        safe_attempt_id = (
            raw_attempt_id
            if isinstance(raw_attempt_id, str) and _SAFE_ID_RE.fullmatch(raw_attempt_id)
            else None
        )
        safe_task_id = (
            task_id if isinstance(task_id, str) and _SAFE_ID_RE.fullmatch(task_id) else None
        )
        return {
            "schema": _ATTEMPT_SCHEMA,
            "task_id": safe_task_id,
            "attempt_id": safe_attempt_id,
            "task_type": None,
            "agent": None,
            "state": "malformed",
            "claimed_at": None,
            "running_at": None,
            "terminal_at": None,
            "updated_at": None,
            "outer_invocation_id": None,
            "current_inner_invocation_id": None,
            "conversation_id": None,
            "continuation_claim_id": None,
            "resume_count": 0,
            "error_class": "validation",
            "error_code": "malformed_receipt",
            "receipt_error": "malformed_receipt",
        }
    return {**attempt, "receipt_error": None}


def _legacy_attempt_projection(task):
    task_id = task.get("id")
    task_type = task.get("type", "prompt")
    agent = task.get("agent") if task_type == "agent" else None
    if not isinstance(task_id, str) or not _SAFE_ID_RE.fullmatch(task_id):
        task_id = None
    if not isinstance(agent, str) or not _SAFE_ID_RE.fullmatch(agent):
        agent = None
    return {
        "schema": None,
        "task_id": task_id,
        "attempt_id": None,
        "task_type": task_type if task_type in {"agent", "prompt"} else None,
        "agent": agent,
        "state": "legacy",
        "claimed_at": None,
        "running_at": None,
        "terminal_at": None,
        "updated_at": None,
        "outer_invocation_id": None,
        "current_inner_invocation_id": None,
        "conversation_id": None,
        "continuation_claim_id": None,
        "resume_count": 0,
        "error_class": None,
        "error_code": None,
        "receipt_error": "legacy_no_execution_receipt",
    }


def _status_query(task_id=None, attempt_id=None):
    query = {}
    if isinstance(task_id, str) and _SAFE_ID_RE.fullmatch(task_id):
        query["task_id"] = task_id
    if _is_canonical_attempt_id(attempt_id):
        query["attempt_id"] = attempt_id
    return query


def _status_result(*, ok, code, query, task=None, attempts=None, attempt=None):
    return {
        "schema": _STATUS_SCHEMA,
        "ok": ok,
        "code": code,
        "query": dict(query),
        "task": task,
        "attempts": attempts,
        "attempt": attempt,
    }


def invalid_exact_status_request(task_id=None, attempt_id=None):
    """Return the fixed refusal used when the MCP request shape is invalid."""
    return _status_result(
        ok=False,
        code="invalid_request",
        query=_status_query(task_id, attempt_id),
    )


def _status_task_projection(task):
    task_id = task.get("id") if isinstance(task, dict) else None
    if not isinstance(task_id, str) or not _SAFE_ID_RE.fullmatch(task_id):
        return None
    return {
        "task_id": task_id,
        "definition_state": "active" if bool(task.get("active", True)) else "inactive",
    }


def _status_attempt_projection(task, raw, *, exact_attempt_id=None):
    task_id = task.get("id") if isinstance(task, dict) else None
    try:
        validated = _validate_attempt(raw, expected_task_id=task_id)
    except SchedulerAttemptError:
        safe_task_id = (
            task_id
            if isinstance(task_id, str) and _SAFE_ID_RE.fullmatch(task_id)
            else None
        )
        safe_attempt_id = exact_attempt_id if _is_canonical_attempt_id(exact_attempt_id) else None
        return {
            "task_id": safe_task_id,
            "attempt_id": safe_attempt_id,
            "state": "malformed",
            "claimed_at": None,
            "running_at": None,
            "terminal_at": None,
            "updated_at": None,
            "resume_count": 0,
            "error_class": "validation",
            "error_code": "malformed_receipt",
            "receipt_error": "malformed_receipt",
        }

    return {
        "task_id": validated["task_id"],
        "attempt_id": validated["attempt_id"],
        "state": validated["state"],
        "claimed_at": validated["claimed_at"],
        "running_at": validated["running_at"],
        "terminal_at": validated["terminal_at"],
        "updated_at": validated["updated_at"],
        "resume_count": validated["resume_count"],
        "error_class": validated["error_class"],
        "error_code": validated["error_code"],
        "receipt_error": None,
    }


def _status_raw_attempts(task):
    if "execution_attempts" not in task:
        return []
    attempts = task.get("execution_attempts")
    return attempts if isinstance(attempts, list) else [attempts]


def _status_task_matches(tasks, task_id):
    return [
        (index, task)
        for index, task in enumerate(tasks)
        if isinstance(task, dict) and task.get("id") == task_id
    ]


def _status_attempt_matches(tasks, attempt_id):
    matches = []
    for task_index, task in enumerate(tasks):
        if not isinstance(task, dict):
            continue
        for raw in _status_raw_attempts(task):
            if isinstance(raw, dict) and raw.get("attempt_id") == attempt_id:
                matches.append((task_index, task, raw))
    return matches


def _status_task_attempts_projection(task):
    raw_attempts = _status_raw_attempts(task)
    counts = {state: 0 for state in _STATUS_STATE_KEYS}
    valid = []
    for raw in raw_attempts:
        projection = _status_attempt_projection(task, raw)
        counts[projection["state"]] += 1
        if projection["state"] != "malformed":
            valid.append(projection)

    latest = None
    if valid:
        latest = max(
            valid,
            key=lambda row: (
                _parse_utc_timestamp(row["claimed_at"], field="claimed_at"),
                row["attempt_id"],
            ),
        )

    malformed_count = counts["malformed"]
    if not raw_attempts:
        schedule = str(task.get("schedule", ""))
        is_legacy = not bool(task.get("active", True)) and bool(
            _ONCE_PREFIX_RE.match(schedule)
        )
        receipt_status = (
            "legacy_no_execution_receipt" if is_legacy else "no_attempts"
        )
    elif valid and malformed_count:
        receipt_status = "attempts_with_malformed"
    elif valid:
        receipt_status = "attempts"
    else:
        receipt_status = "malformed_only"

    return {
        "receipt_status": receipt_status,
        "retained_count": len(raw_attempts),
        "state_counts": counts,
        "latest": latest,
    }


def get_exact_status(task_id=None, attempt_id=None):
    """Return one bounded exact-ID scheduler status projection.

    Caller input is validated before the dedicated read-only snapshot boundary.
    This path never calls ``list_tasks`` or enters ``_transact_tasks``.
    """
    query = _status_query(task_id, attempt_id)
    if task_id is None and attempt_id is None:
        return _status_result(ok=False, code="invalid_request", query=query)
    if task_id is not None and query.get("task_id") != task_id:
        return _status_result(ok=False, code="invalid_task_id", query=query)
    if attempt_id is not None and query.get("attempt_id") != attempt_id:
        return _status_result(ok=False, code="invalid_attempt_id", query=query)

    try:
        tasks = _read_status_tasks()
    except _SchedulerStatusStoreError as exc:
        return _status_result(ok=False, code=exc.code, query=query)

    task_match = None
    if task_id is not None:
        task_matches = _status_task_matches(tasks, task_id)
        if not task_matches:
            return _status_result(ok=False, code="task_not_found", query=query)
        if len(task_matches) != 1:
            return _status_result(ok=False, code="task_identity_conflict", query=query)
        task_match = task_matches[0]

    if attempt_id is None:
        _, task = task_match
        return _status_result(
            ok=True,
            code="task_status",
            query=query,
            task=_status_task_projection(task),
            attempts=_status_task_attempts_projection(task),
        )

    attempt_matches = _status_attempt_matches(tasks, attempt_id)
    if not attempt_matches:
        return _status_result(ok=False, code="attempt_not_found", query=query)
    if len(attempt_matches) != 1:
        return _status_result(
            ok=False,
            code="attempt_identity_conflict",
            query=query,
        )

    owner_index, owner_task, raw_attempt = attempt_matches[0]
    if task_match is not None and task_match[0] != owner_index:
        return _status_result(
            ok=False,
            code="task_attempt_mismatch",
            query=query,
        )

    owner_task_id = owner_task.get("id")
    if isinstance(owner_task_id, str) and _SAFE_ID_RE.fullmatch(owner_task_id):
        if len(_status_task_matches(tasks, owner_task_id)) != 1:
            return _status_result(
                ok=False,
                code="task_identity_conflict",
                query=query,
            )

    return _status_result(
        ok=True,
        code="attempt_status",
        query=query,
        task=_status_task_projection(owner_task),
        attempt=_status_attempt_projection(
            owner_task,
            raw_attempt,
            exact_attempt_id=attempt_id,
        ),
    )


def _list_execution_attempts_from_tasks(tasks, *, limit=20):
    entries = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        raw_attempts = task.get("execution_attempts", [])
        if not isinstance(raw_attempts, list):
            entries.append(_attempt_projection(task, raw_attempts))
            continue
        attempts = raw_attempts
        for raw in attempts:
            entries.append(_attempt_projection(task, raw))
        if not attempts and not task.get("active", True):
            schedule = str(task.get("schedule", ""))
            if _ONCE_PREFIX_RE.match(schedule):
                entries.append(_legacy_attempt_projection(task))

    def sort_key(entry):
        timestamp = entry.get("claimed_at")
        instant = (
            _parse_utc_timestamp(timestamp, field="claimed_at")
            if timestamp is not None
            else datetime.min.replace(tzinfo=UTC)
        )
        return (timestamp is not None, instant, entry.get("attempt_id") or "")

    entries.sort(key=sort_key, reverse=True)
    if limit is not None:
        entries = entries[:max(0, int(limit))]
    return entries


def list_execution_attempts(limit=20):
    """Return a content-free, malformed-row-isolated attempt projection."""
    return _list_execution_attempts_from_tasks(_load_tasks(), limit=limit)


def reconcile_execution_attempts(continuation_claims=None):
    """Bind valid managed claims and terminalize every unmatched nonterminal.

    This startup operation never dispatches or replays work. Its return value is
    a content-free binding map for ``main.py`` to filter the already-loaded
    continuation marker before the scheduler loop starts.
    """
    raw_claims = continuation_claims if isinstance(continuation_claims, list) else []
    bindings = []
    dropped = []
    terminalized = []
    matched_attempts = set()

    # Avoid a load-time rewrite when the store contains only pending/legacy or
    # terminal history. Legacy rows without receipts remain byte-for-byte lazy.
    snapshot = _load_tasks()
    has_nonterminal = False
    for task in snapshot:
        if not isinstance(task, dict):
            continue
        for raw in _attempts_for_task(task):
            try:
                attempt = _validate_attempt(raw, expected_task_id=task.get("id"))
            except SchedulerAttemptError:
                continue
            if attempt["state"] not in _ATTEMPT_TERMINAL_STATES:
                has_nonterminal = True
                break
        if has_nonterminal:
            break
    if not has_nonterminal:
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            if raw_claim.get("scheduled_task_id") is None and raw_claim.get("scheduled_attempt_id") is None:
                continue
            dropped.append({
                "task_id": raw_claim.get("scheduled_task_id") if isinstance(raw_claim.get("scheduled_task_id"), str) else None,
                "attempt_id": raw_claim.get("scheduled_attempt_id") if isinstance(raw_claim.get("scheduled_attempt_id"), str) else None,
                "continuation_claim_id": raw_claim.get("id") if isinstance(raw_claim.get("id"), str) else None,
                "error_code": "interrupted_uncertain",
            })
        return {
            "continuations": [],
            "dropped_continuation_claims": dropped,
            "terminalized": [],
        }

    with _transact_tasks() as tasks:
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            task_id = raw_claim.get("scheduled_task_id")
            attempt_id = raw_claim.get("scheduled_attempt_id")
            if task_id is None and attempt_id is None:
                continue
            claim_id = raw_claim.get("id")
            conversation_id = raw_claim.get("conversation_id")
            key = (task_id, attempt_id)
            try:
                for field, value in (
                    ("task_id", task_id),
                    ("attempt_id", attempt_id),
                    ("continuation_claim_id", claim_id),
                    ("conversation_id", conversation_id),
                ):
                    _validate_safe_id(value, field=field)
                if key in matched_attempts:
                    raise SchedulerAttemptError("duplicate continuation claim")
                task = _find_task(tasks, task_id)
                _, attempt = _find_attempt(task, attempt_id)
                if attempt["state"] in _ATTEMPT_TERMINAL_STATES:
                    raise SchedulerAttemptError("terminal attempt continuation is stale")
                if attempt["state"] != "running":
                    raise SchedulerAttemptError("only running attempts can resume")
                if attempt.get("conversation_id") != conversation_id:
                    raise SchedulerAttemptError("continuation conversation is stale")
                if attempt.get("current_inner_invocation_id") != claim_id:
                    raise SchedulerAttemptError("continuation live-row identity is stale")

                if attempt.get("continuation_claim_id") != claim_id:
                    attempt["continuation_claim_id"] = claim_id
                    attempt["resume_count"] += 1
                    attempt["updated_at"] = _next_attempt_timestamp(attempt)
                    _validate_attempt(attempt, expected_task_id=task_id)
                matched_attempts.add(key)
                bindings.append({
                    "task_id": task_id,
                    "attempt_id": attempt_id,
                    "conversation_id": conversation_id,
                    "current_inner_invocation_id": claim_id,
                    "continuation_claim_id": claim_id,
                })
            except SchedulerAttemptError:
                dropped.append({
                    "task_id": task_id if isinstance(task_id, str) else None,
                    "attempt_id": attempt_id if isinstance(attempt_id, str) else None,
                    "continuation_claim_id": claim_id if isinstance(claim_id, str) else None,
                    "error_code": "interrupted_uncertain",
                })

        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_id = task.get("id")
            task_terminalized = []
            for raw in list(_attempts_for_task(task)):
                try:
                    attempt = _validate_attempt(raw, expected_task_id=task_id)
                except SchedulerAttemptError:
                    continue
                key = (task_id, attempt["attempt_id"])
                if attempt["state"] in _ATTEMPT_TERMINAL_STATES or key in matched_attempts:
                    continue
                code = (
                    "interrupted_before_start"
                    if attempt["state"] == "claimed"
                    else "interrupted_uncertain"
                )
                timestamp = _next_attempt_timestamp(attempt)
                attempt["state"] = "failed"
                attempt["terminal_at"] = timestamp
                attempt["updated_at"] = timestamp
                attempt["error_class"] = "interrupted"
                attempt["error_code"] = code
                _validate_attempt(attempt, expected_task_id=task_id)
                terminalized.append({
                    "task_id": task_id,
                    "attempt_id": attempt["attempt_id"],
                    "error_code": code,
                })
                task_terminalized.append(attempt["attempt_id"])
            _prune_execution_attempts(
                task,
                protected_attempt_id=(task_terminalized[-1] if task_terminalized else None),
            )

    return {
        "continuations": bindings,
        "dropped_continuation_claims": dropped,
        "terminalized": terminalized,
    }


def add_task(prompt, schedule_text, silent=False, task_type="prompt", agent=None, room_id=None, project=None):
    """
    Schedules a new task.
    prompt: The text to send to the agent.
    schedule_text: "every X minutes/hours", "daily at HH:MM", or "once at YYYY-MM-DDTHH:MM:SS"
    silent: If True, task runs in background without notifications or chat visibility.
            Use for maintenance tasks (Librarian, Gardener). Default: False.
    task_type: "prompt" (default) or "agent" for agent invocations.
    agent: Agent name if task_type is "agent".
    room_id: Optional room ID to target. If specified:
             - For 'prompt' tasks: Output will be delivered to this room with history context.
             - For 'agent' tasks: Agent output will be delivered to this room.
             If None, uses active room or creates new chat.
    project: Optional project tag (string or list of strings). When present, the dispatcher
             injects PROJECT METADATA into the agent's prompt so output gets tagged with
             YAML frontmatter for automatic routing to the project's _status.md.
    """
    # Validate one-time schedules before acquiring the transaction or mutating
    # the store. The raw caller string remains the sole persisted representation.
    _normalize_once_schedule(schedule_text)

    with _transact_tasks() as tasks:
        new_task = {
            "id": str(uuid.uuid4())[:8],
            "prompt": prompt,
            "schedule": schedule_text,
            "created_at": datetime.now().isoformat(),
            "last_run": datetime.now().isoformat(),
            "active": True,
            "silent": silent,
            "type": task_type,
        }

        if task_type == "agent" and agent:
            new_task["agent"] = agent

        # Store room_id if provided for room-targeted delivery
        if room_id:
            new_task["room_id"] = room_id

        # Store project tag if provided for output routing
        if project:
            new_task["project"] = project

        tasks.append(new_task)
    mode = " (silent)" if silent else ""
    agent_info = f" via agent '{agent}'" if task_type == "agent" and agent else ""
    room_info = f" → room '{room_id}'" if room_id else ""
    project_info = f" [project: {project}]" if project else ""
    return f"✅ Task scheduled{mode}{agent_info}{room_info}{project_info} (ID: {new_task['id']}): '{prompt}' ({schedule_text})"


def add_agent_task(agent, prompt, schedule_text, room_id=None, silent=True, project=None):
    """
    Schedule an agent task.

    agent: Agent name (claude_code, kestrel, jack, deep_think, librarian, gardener)
    prompt: Task description for the agent.
    schedule_text: "every X minutes/hours", "daily at HH:MM", or "once at YYYY-MM-DDTHH:MM:SS"
    room_id: Optional room ID to target. If specified, agent output will be delivered to this room.
             If None, output goes to 00_Inbox/agent_outputs/ for async review.
    silent: If True (default), runs in background without creating a visible chat or notifications.
            If False, creates a visible chat with notifications when the agent completes.
    project: Optional project tag (string or list of strings) for output routing.
    """
    return add_task(prompt, schedule_text, silent=silent, task_type="agent", agent=agent, room_id=room_id, project=project)

def list_tasks(include_inactive=False):
    all_tasks = _load_tasks()
    if not all_tasks:
        return "No scheduled tasks found."

    # Filter by active status unless include_inactive is True
    tasks = all_tasks
    if not include_inactive:
        tasks = [t for t in tasks if t.get('active', True)]

    output = []
    if tasks:
        output.append("📅 **Scheduled Tasks:**")
        for t in tasks:
            status_icon = "🟢" if t.get('active', True) else "🔴"

            # Check for error status
            error_msg = t.get('last_error')
            if error_msg:
                status_icon = "⚠️"

            # Check for silent mode
            is_silent = t.get('silent', False)
            silent_indicator = " 🔇" if is_silent else ""

            # Check for agent task type
            task_type = t.get('type', 'prompt')
            agent_indicator = f" 🤖{t.get('agent', '?')}" if task_type == 'agent' else ""

            # Check for room targeting
            room_id = t.get('room_id')
            room_indicator = f" 📍{room_id[:8]}..." if room_id and len(room_id) > 8 else f" 📍{room_id}" if room_id else ""

            # Check for project tag
            project = t.get('project')
            project_indicator = f" 📂{project}" if project else ""

            last = t.get('last_run', 'Never')
            if last != 'Never':
                try:
                    dt = datetime.fromisoformat(last)
                    last = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass

            line = f"{status_icon} `{t['id']}`{silent_indicator}{agent_indicator}{project_indicator}{room_indicator}: {t['prompt']}\n   Schedule: {t['schedule']} (Last: {last})"
            if error_msg:
                line += f"\n   ❌ Error: {error_msg}"
            output.append(line)
    else:
        output.append("No active scheduled tasks. Use include_all=true to see inactive tasks.")

    attempts = _list_execution_attempts_from_tasks(all_tasks, limit=20)
    if attempts:
        output.extend(["", "🧾 **Recent execution receipts:**"])
        for attempt in attempts:
            task_id = attempt.get("task_id") or "unknown"
            attempt_id = attempt.get("attempt_id")
            state = attempt.get("state")
            if attempt.get("receipt_error") == "legacy_no_execution_receipt":
                output.append(
                    f"- task `{task_id}` — legacy — no execution receipt"
                )
                continue
            if attempt.get("receipt_error") == "malformed_receipt":
                attempt_label = f" attempt `{attempt_id}`" if attempt_id else ""
                output.append(
                    f"- task `{task_id}`{attempt_label} — malformed receipt "
                    f"(`validation/malformed_receipt`)"
                )
                continue

            agent_label = (
                f" agent `{attempt['agent']}`" if attempt.get("agent") else " prompt"
            )
            detail = (
                f"- **{state}** task `{task_id}` attempt `{attempt_id}`{agent_label}; "
                f"claimed `{attempt.get('claimed_at')}`"
            )
            if attempt.get("running_at"):
                detail += f", running `{attempt['running_at']}`"
            if attempt.get("terminal_at"):
                detail += f", terminal `{attempt['terminal_at']}`"
            if attempt.get("conversation_id"):
                detail += f", thread `{attempt['conversation_id'][:12]}`"
            if attempt.get("current_inner_invocation_id"):
                detail += f", live `{attempt['current_inner_invocation_id'][:12]}`"
            if attempt.get("resume_count"):
                detail += f", resumes {attempt['resume_count']}"
            if attempt.get("error_class"):
                detail += f", error `{attempt['error_class']}/{attempt['error_code']}`"
            if state == "claimed":
                detail += " — runner not yet entered"
            output.append(detail)

    return "\n".join(output)



def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _summarize_prompt(prompt, limit=160):
    text = str(prompt or '').replace('\n', ' ').strip()
    if len(text) <= limit:
        return text
    return text[:limit - 1] + '…'


def _cron_field_matches(field, current_val):
    if field == '*':
        return True
    if field.startswith('*/'):
        step = int(field[2:])
        return current_val % step == 0
    for part in field.split(','):
        part = part.strip()
        if '/' in part:
            range_part, step = part.split('/', 1)
            step = int(step)
            if '-' in range_part:
                lo, hi = range_part.split('-', 1)
                lo, hi = int(lo), int(hi)
                if lo <= current_val <= hi and (current_val - lo) % step == 0:
                    return True
        elif '-' in part:
            lo, hi = part.split('-', 1)
            lo, hi = int(lo), int(hi)
            if lo <= current_val <= hi:
                return True
        else:
            if int(part) == current_val:
                return True
    return False


def _next_daily_target(now, hour, minute, last_run=None, allowed_dow=None):
    for offset in range(0, 8):
        candidate_day = now + timedelta(days=offset)
        candidate = candidate_day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        python_dow = (candidate.weekday() + 1) % 7
        if allowed_dow and not _cron_field_matches(allowed_dow, python_dow):
            continue
        if candidate <= now:
            if last_run is None or last_run < candidate:
                return candidate
            continue
        return candidate
    return None


def _next_run_for_task(task, now):
    schedule_text = str(task.get('schedule', '')).strip()
    once_target = _normalize_once_schedule(
        schedule_text,
        allow_legacy_gap=True,
    )
    schedule = schedule_text.lower()
    last_run = _parse_iso_datetime(task.get('last_run'))

    match_every = re.match(r"every\s+(\d+)?\s*(minute|hour|day)s?", schedule)
    match_daily = re.search(r"daily at\s+(\d{1,2}):(\d{2})\s*(am|pm)?", schedule)

    if match_every:
        val = int(match_every.group(1)) if match_every.group(1) else 1
        unit = match_every.group(2)
        if 'minute' in unit:
            delta = timedelta(minutes=val)
        elif 'hour' in unit:
            delta = timedelta(hours=val)
        elif 'day' in unit:
            delta = timedelta(days=val)
        else:
            return None
        if last_run is None:
            return now
        return last_run + delta

    if match_daily:
        hour = int(match_daily.group(1))
        minute = int(match_daily.group(2))
        meridiem = match_daily.group(3)
        if meridiem:
            if meridiem == 'pm' and hour != 12:
                hour += 12
            elif meridiem == 'am' and hour == 12:
                hour = 0
        return _next_daily_target(now, hour, minute, last_run=last_run)

    if once_target is not None:
        return once_target

    cron_match = re.match(r'^([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)$', schedule_text)
    if cron_match:
        cron_min, cron_hour, cron_dom, cron_month, cron_dow = cron_match.groups()
        # Cheap exact daily/weekly cron support. Other cron forms still display
        # their schedule text, but do not pretend to have a precise next time.
        if cron_min.isdigit() and cron_hour.isdigit() and cron_dom == '*' and cron_month == '*':
            return _next_daily_target(
                now,
                int(cron_hour),
                int(cron_min),
                last_run=last_run,
                allowed_dow=cron_dow,
            )
    return None


def list_upcoming_runs(limit=20, include_inactive=False):
    """Return read-only scheduled task summaries for UI visibility.

    This does not mutate last_run or active flags; scheduler_loop remains the
    only runtime path that claims due work. Unsupported schedule forms keep
    their schedule text with next_run=None rather than presenting a false time.
    """
    tasks = _load_tasks()
    now = datetime.now()
    now_utc = datetime.now(UTC)
    entries = []

    for task in tasks:
        active = task.get('active', True)
        if not include_inactive and not active:
            continue

        next_run = None
        due_now = False
        parse_error = task.get('last_error')
        try:
            next_run = _next_run_for_task(task, now)
            if next_run is not None:
                comparison_now = now_utc if next_run.utcoffset() is not None else now
                due_now = next_run <= comparison_now
        except Exception as e:
            next_run = None
            due_now = False
            parse_error = _bounded_error("Could not calculate next run", e)

        task_type = task.get('type', 'prompt')
        agent = task.get('agent') if task_type == 'agent' else None
        entries.append({
            'id': task.get('id'),
            'task_id': task.get('id'),
            'type': task_type,
            'agent': agent,
            'name': agent or task_type,
            'silent': task.get('silent', False),
            'active': active,
            'schedule': task.get('schedule'),
            'next_run': next_run.isoformat() if next_run else None,
            'due_now': due_now,
            'last_run': task.get('last_run'),
            'prompt_summary': _summarize_prompt(task.get('prompt')),
            'project': task.get('project'),
            'room_id': task.get('room_id'),
            'error': parse_error,
        })

    def sort_key(entry):
        parsed = _parse_iso_datetime(entry.get('next_run'))
        if parsed is None:
            comparable = datetime.max.replace(tzinfo=UTC)
        else:
            # Naive recurring results retain their existing host-local display
            # semantics. Converting only the internal key makes mixed rows safe.
            comparable = parsed.astimezone(UTC)
        return (parsed is None, comparable, entry.get('id') or '')

    entries.sort(key=sort_key)
    if limit is not None:
        entries = entries[:int(limit)]
    return entries

def remove_task(task_id):
    with _transact_tasks() as tasks:
        for task in tasks:
            if task.get('id') != task_id:
                continue
            for raw in _attempts_for_task(task):
                try:
                    attempt = _validate_attempt(raw, expected_task_id=task_id)
                except SchedulerAttemptError:
                    continue
                if attempt["state"] not in _ATTEMPT_TERMINAL_STATES:
                    return (
                        f"❌ Task `{task_id}` has a nonterminal execution attempt. "
                        "Deactivate it and wait for or inspect the attempt before removal."
                    )
            tasks.remove(task)
            return f"✅ Task `{task_id}` removed."
    return f"❌ Task `{task_id}` not found."


def update_task(task_id, silent=None, active=None, schedule=None, prompt=None, room_id=None, project=None):
    """
    Update an existing scheduled task.
    task_id: The task ID to update.
    silent: Set to True/False to change silent mode.
    active: Set to True/False to enable/disable task.
    schedule: New schedule string.
    prompt: New prompt text.
    room_id: Set target room ID. Use empty string "" to clear room targeting.
    project: Set project tag (string or list). Use empty string "" to clear.
    """
    # Reject an invalid replacement before opening a write transaction so every
    # existing field remains semantically unchanged on failure.
    if schedule is not None:
        _normalize_once_schedule(schedule)

    with _transact_tasks() as tasks:
        for t in tasks:
            if t['id'] == task_id:
                changes = []

                if silent is not None:
                    old_silent = t.get('silent', False)
                    t['silent'] = silent
                    changes.append(f"silent: {old_silent} → {silent}")

                if active is not None:
                    old_active = t.get('active', True)
                    t['active'] = active
                    changes.append(f"active: {old_active} → {active}")

                if schedule is not None:
                    old_schedule = t.get('schedule')
                    t['schedule'] = schedule
                    changes.append(f"schedule: '{old_schedule}' → '{schedule}'")

                if prompt is not None:
                    t['prompt'] = prompt
                    changes.append("prompt updated")

                if room_id is not None:
                    old_room = t.get('room_id')
                    if room_id == "":
                        # Clear room targeting
                        t.pop('room_id', None)
                        changes.append(f"room_id: '{old_room}' → (cleared)")
                    else:
                        t['room_id'] = room_id
                        changes.append(f"room_id: '{old_room}' → '{room_id}'")

                if project is not None:
                    old_project = t.get('project')
                    if project == "":
                        # Clear project tag
                        t.pop('project', None)
                        changes.append(f"project: '{old_project}' → (cleared)")
                    else:
                        t['project'] = project
                        changes.append(f"project: '{old_project}' → '{project}'")

                return f"✅ Task `{task_id}` updated: {', '.join(changes)}"

    return f"❌ Task `{task_id}` not found."

def check_due_tasks():
    """
    Checks tasks and returns a list of prompts that actually need to run NOW.
    Updates 'last_run' for those tasks immediately.

    The entire read→check→update→write is wrapped in _transact_tasks() so that
    concurrent callers (e.g. add_task from an MCP tool) cannot read a stale
    snapshot and clobber the last_run updates.
    """
    due_prompts = []

    with _transact_tasks() as tasks:
        now = datetime.now()
        now_utc = datetime.now(UTC)

        for t in tasks:
            # Clear previous errors
            if 'last_error' in t:
                del t['last_error']

            if not t.get('active', True):
                continue

            should_run = False
            is_once = False
            last_run_str = t.get('last_run')
            last_run = datetime.fromisoformat(last_run_str) if last_run_str else None

            schedule_text = str(t['schedule']).strip()
            schedule = schedule_text.lower()

            try:
                once_target = _normalize_once_schedule(
                    schedule_text,
                    allow_legacy_gap=True,
                )

                # 1. "every X minutes/hours/days"
                match_every = re.match(r"every\s+(\d+)?\s*(minute|hour|day)s?", schedule)

                # 2. "daily at HH:MM(am/pm)?"
                match_daily = re.search(r"daily at\s+(\d{1,2}):(\d{2})\s*(am|pm)?", schedule)

                if once_target is not None:
                    if now_utc >= once_target:
                        should_run = True
                        is_once = True

                elif match_every:
                    val = int(match_every.group(1)) if match_every.group(1) else 1
                    unit = match_every.group(2) # minute, hour, day

                    delta = None
                    if "minute" in unit:
                        delta = timedelta(minutes=val)
                    elif "hour" in unit:
                        delta = timedelta(hours=val)
                    elif "day" in unit:
                        delta = timedelta(days=val)

                    if delta:
                        if last_run is None:
                            should_run = True
                        elif now - last_run >= delta:
                            should_run = True

                elif match_daily:
                    hour = int(match_daily.group(1))
                    minute = int(match_daily.group(2))
                    meridiem = match_daily.group(3) # am/pm/None

                    # Handle 12-hour format
                    if meridiem:
                        if meridiem == "pm" and hour != 12:
                            hour += 12
                        elif meridiem == "am" and hour == 12:
                            hour = 0

                    target_today = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

                    if now >= target_today:
                        if last_run is None or last_run < target_today:
                            should_run = True

                else:
                    # Try cron syntax: "minute hour day-of-month month day-of-week"
                    cron_match = re.match(r'^([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)\s+([\d,\-\*/]+)$', t['schedule'].strip())

                    if cron_match:
                        cron_min, cron_hour, cron_dom, cron_month, cron_dow = cron_match.groups()

                        def cron_field_matches(field, current_val):
                            if field == '*':
                                return True
                            if field.startswith('*/'):
                                step = int(field[2:])
                                return current_val % step == 0
                            for part in field.split(','):
                                part = part.strip()
                                if '/' in part:
                                    range_part, step = part.split('/', 1)
                                    step = int(step)
                                    if '-' in range_part:
                                        lo, hi = range_part.split('-', 1)
                                        lo, hi = int(lo), int(hi)
                                        if lo <= current_val <= hi and (current_val - lo) % step == 0:
                                            return True
                                elif '-' in part:
                                    lo, hi = part.split('-', 1)
                                    lo, hi = int(lo), int(hi)
                                    if lo <= current_val <= hi:
                                        return True
                                else:
                                    if int(part) == current_val:
                                        return True
                            return False

                        min_ok = cron_field_matches(cron_min, now.minute)
                        hour_ok = cron_field_matches(cron_hour, now.hour)
                        dom_ok = cron_field_matches(cron_dom, now.day)
                        month_ok = cron_field_matches(cron_month, now.month)
                        python_dow = (now.weekday() + 1) % 7
                        dow_ok = cron_field_matches(cron_dow, python_dow)

                        if min_ok and hour_ok and dom_ok and month_ok and dow_ok:
                            if last_run is None:
                                should_run = True
                            else:
                                this_minute_start = now.replace(second=0, microsecond=0)
                                if last_run < this_minute_start:
                                    should_run = True
                        else:
                            # CATCH-UP LOGIC for daily cron jobs
                            if cron_min != '*' and cron_hour != '*' and cron_dom == '*' and cron_month == '*':
                                scheduled_hour = int(cron_hour)
                                scheduled_min = int(cron_min)

                                today_target = now.replace(hour=scheduled_hour, minute=scheduled_min, second=0, microsecond=0)

                                dow_matches_today = cron_field_matches(cron_dow, python_dow)

                                if dow_matches_today and now > today_target:
                                    if last_run is None or last_run < today_target:
                                        hours_since_target = (now - today_target).total_seconds() / 3600
                                        if hours_since_target <= 6:
                                            should_run = True
                                            logging.getLogger(__name__).info(
                                                f"Catch-up: Running missed cron task '{t.get('id')}' "
                                                f"(scheduled {scheduled_hour}:{scheduled_min:02d}, "
                                                f"now {now.strftime('%H:%M')}, {hours_since_target:.1f}h late)"
                                            )
                    else:
                        # Unrecognized format
                        t['last_error'] = f"Unrecognized schedule format: '{t['schedule']}'"

            except Exception as e:
                t['last_error'] = _bounded_error("Parsing error", e)

            if should_run:
                try:
                    existing_attempts = t.get("execution_attempts")
                    if existing_attempts is not None and not isinstance(existing_attempts, list):
                        raise SchedulerAttemptError("execution_attempts must be a list")
                    attempt = _new_claimed_attempt(t, now=now_utc)
                except SchedulerAttemptError as e:
                    t['last_error'] = _bounded_error("Execution receipt error", e)
                    continue

                if existing_attempts is None:
                    t["execution_attempts"] = []
                t["execution_attempts"].append(attempt)
                if is_once:
                    t['active'] = False
                task_type = t.get('type', 'prompt')
                task_info = {
                    "id": t.get('id'),
                    "attempt_id": attempt["attempt_id"],
                    "type": task_type,
                    "silent": t.get('silent', False)
                }

                if t.get('room_id'):
                    task_info["room_id"] = t['room_id']

                if t.get('project'):
                    task_info["project"] = t['project']

                if task_type == "agent":
                    task_info["agent"] = t.get('agent')
                    task_info["prompt"] = t['prompt']
                else:
                    task_info["prompt"] = f"👇 [SCHEDULED AUTOMATION] 👇\n{t['prompt']}"

                due_prompts.append(task_info)
                t['last_run'] = now.isoformat()

    return due_prompts

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print(list_tasks())
    else:
        cmd = sys.argv[1]
        if cmd == "add":
            # python scheduler_tool.py add "prompt" "schedule"
            if len(sys.argv) >= 4:
                # Join remaining args for prompt if schedule is last?
                # Usage: add "Prompt string" "every 5 minutes"
                # argv[0]=script, argv[1]=add, argv[2]=Prompt, argv[3]=Schedule
                print(add_task(sys.argv[2], sys.argv[3]))
            else:
                print("Usage: add <prompt> <schedule>")
        elif cmd == "list":
            print(list_tasks())
        elif cmd == "remove":
            if len(sys.argv) >= 3:
                print(remove_task(sys.argv[2]))
        elif cmd == "check":
            # For internal use mostly
            print(check_due_tasks())
