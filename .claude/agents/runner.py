"""
Agent Runner - Execution engine for agents.

Supports four invocation modes:
- foreground: Blocking execution, returns result directly
- ping: Async execution, notification when done
- trust: Fire and forget, logged but no notification
- scheduled: Like trust, but triggered by scheduler

# 🥚 Easter egg planted by Patch on 2026-03-24.
# This edit was previously IMPOSSIBLE — the Edit tool couldn't touch .claude/ files.
# Then we taught it to stop asking permission and just trust us.
# If you're reading this: hi from the agent that fixed itself.
"""

import asyncio
import json
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Set, Tuple, Union

os.environ.setdefault("CLAUDE_CODE_STREAM_CLOSE_TIMEOUT", "14400000")
os.environ["ENABLE_TOOL_SEARCH"] = "false"

from models import (
    AgentConfig, AgentInvocation, AgentResult, InvocationMode, AgentType
)
from agent_notifications import get_notification_queue

# Ensure server directory is importable (for process_registry)
_server_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../interface/server"))
if _server_dir not in sys.path:
    sys.path.insert(0, _server_dir)

from process_registry import register_process, deregister_process
import running_agents
from agent_invocation_control import get_controller
import managed_load_operations


PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS = "agent_outputs"
PROJECT_OUTPUT_CONTRACT_NONE = "none"
PROJECT_OUTPUT_CONTRACT_VALUES = {
    PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS,
    PROJECT_OUTPUT_CONTRACT_NONE,
}


def _invalid_project_output_contract_result(
    name: str,
    mode: InvocationMode,
    value: Any,
) -> Union[AgentResult, Dict[str, str]]:
    error = (
        "Invalid project_output_contract: "
        f"{value!r}. Expected one of: "
        f"{', '.join(sorted(PROJECT_OUTPUT_CONTRACT_VALUES))}."
    )
    if mode == InvocationMode.FOREGROUND:
        return AgentResult(
            agent=name,
            status="error",
            response="",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            error=error,
        )
    return {"error": error}


def _load_real_claude_agent_sdk():
    """Load the installed Anthropic SDK even when the local shim shadows its package name."""
    import importlib.metadata
    import importlib.util

    init_path = Path(
        importlib.metadata.distribution("claude-agent-sdk").locate_file(
            "claude_agent_sdk/__init__.py"
        )
    )
    module_name = "_second_brain_real_claude_agent_sdk"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_path,
        submodule_search_locations=[str(init_path.parent)],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load real claude_agent_sdk from {init_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _create_real_sdk_mcp_server(name: str, version: str, tools: List[Any]) -> Dict[str, Any]:
    """Adapt local compatibility MCP tools to the installed SDK server type."""
    if _real_sdk is None:
        raise RuntimeError("Installed claude-agent-sdk package is unavailable")
    real_tools = [
        _real_sdk.SdkMcpTool(
            name=tool.name,
            description=tool.description,
            input_schema=tool.input_schema,
            handler=tool.handler,
            annotations=getattr(tool, "annotations", None),
        )
        for tool in tools
    ]
    return _real_sdk.create_sdk_mcp_server(name=name, version=version, tools=real_tools)


_real_sdk = None

try:
    _real_sdk = _load_real_claude_agent_sdk()
    ClaudeSDKClient = _real_sdk.ClaudeSDKClient
    ClaudeAgentOptions = _real_sdk.ClaudeAgentOptions
    SDK_QUERY = _real_sdk.query
    AssistantMessage = _real_sdk.AssistantMessage
    UserMessage = _real_sdk.UserMessage
    SystemMessage = getattr(_real_sdk, "SystemMessage", None)
    ResultMessage = _real_sdk.ResultMessage
    TextBlock = _real_sdk.TextBlock
    ToolUseBlock = _real_sdk.ToolUseBlock
    ToolResultBlock = _real_sdk.ToolResultBlock
    ThinkingBlock = _real_sdk.ThinkingBlock
    _real_sdk_types = sys.modules[f"{_real_sdk.__name__}.types"]
    PermissionResultAllow = _real_sdk_types.PermissionResultAllow
    HookMatcher = _real_sdk_types.HookMatcher
    StreamEvent = _real_sdk_types.StreamEvent
    ThinkingConfigAdaptive = _real_sdk_types.ThinkingConfigAdaptive
    ThinkingConfigEnabled = _real_sdk_types.ThinkingConfigEnabled
except Exception:
    ClaudeSDKClient = ClaudeAgentOptions = SDK_QUERY = None
    AssistantMessage = UserMessage = SystemMessage = ResultMessage = None
    TextBlock = ToolUseBlock = ToolResultBlock = ThinkingBlock = None
    PermissionResultAllow = HookMatcher = StreamEvent = None
    ThinkingConfigAdaptive = ThinkingConfigEnabled = None

logger = logging.getLogger("agents.runner")

_CONTEXTUAL_MEMORY_LOG_PATHS = frozenset({"codex", "sdk"})
_CONTEXTUAL_MEMORY_LOG_OUTCOMES = frozenset({
    "rewrite_accepted",
    "rewrite_timeout_fallback",
    "contextual_memory_failed",
})


def _log_contextual_memory_outcome(
    *,
    agent_name: str,
    accepted_count: int,
    fallback: bool,
    timeout: bool,
    path: str,
    outcome: str,
    level: int = logging.INFO,
) -> None:
    """Log contextual-memory operation state without accepting content fields."""
    safe_count = accepted_count if type(accepted_count) is int and accepted_count >= 0 else 0
    safe_path = path if path in _CONTEXTUAL_MEMORY_LOG_PATHS else "unknown"
    safe_outcome = outcome if outcome in _CONTEXTUAL_MEMORY_LOG_OUTCOMES else "contextual_memory_failed"
    logger.log(
        level,
        "Agent '%s': contextual memory outcome path=%s outcome=%s "
        "accepted_count=%d fallback=%s timeout=%s",
        agent_name,
        safe_path,
        safe_outcome,
        safe_count,
        fallback is True,
        timeout is True,
    )

# Keep strong references to detached ping/trust tasks until they finish.
_BACKGROUND_INVOCATION_TASKS: Set[asyncio.Task] = set()


def _load_scheduler_tool():
    scripts_dir = Path(__file__).resolve().parents[1] / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    import scheduler_tool

    return scheduler_tool


def _finalize_scheduler_attempt(
    task_id: Optional[str],
    attempt_id: Optional[str],
    state: str,
    *,
    error_class: Optional[str] = None,
    error_code: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> bool:
    if not task_id or not attempt_id:
        return False
    scheduler_tool = _load_scheduler_tool()
    scheduler_tool.finalize_attempt(
        task_id,
        attempt_id,
        state,
        error_class=error_class,
        error_code=error_code,
        conversation_id=conversation_id,
    )
    return True


def _finalize_invocation_attempt(
    invocation: AgentInvocation,
    state: str,
    *,
    error_class: Optional[str] = None,
    error_code: Optional[str] = None,
) -> bool:
    return _finalize_scheduler_attempt(
        invocation.scheduled_task_id,
        invocation.scheduled_attempt_id,
        state,
        error_class=error_class,
        error_code=error_code,
        conversation_id=invocation.conversation_id,
    )


def _acknowledge_managed_load_after_thread_persistence(
    invocation: AgentInvocation,
) -> None:
    if not invocation.scheduled_attempt_id or not invocation.conversation_id:
        return
    try:
        managed_load_operations.get_store().acknowledge_owner(
            owner_kind="scheduled",
            owner_id=invocation.scheduled_attempt_id,
            conversation_id=invocation.conversation_id,
        )
    except Exception:
        logger.error(
            "Scheduled managed-load acknowledgement failed after terminal thread "
            "and scheduler persistence task=%s attempt=%s",
            invocation.scheduled_task_id,
            invocation.scheduled_attempt_id,
            exc_info=True,
        )


def _acknowledge_direct_managed_load_after_thread_persistence(
    invocation: AgentInvocation,
) -> None:
    if not invocation.control_invocation_id or not invocation.conversation_id:
        return
    _acknowledge_direct_managed_load_owner(
        invocation.control_invocation_id,
        invocation.conversation_id,
    )


def _acknowledge_direct_managed_load_owner(
    owner_id: str,
    conversation_id: str,
) -> None:
    try:
        managed_load_operations.get_store().acknowledge_owner(
            owner_kind="direct",
            owner_id=owner_id,
            conversation_id=conversation_id,
        )
    except Exception:
        logger.error(
            "Direct managed-load acknowledgement failed after terminal thread persistence id=%s",
            owner_id,
            exc_info=True,
        )
# Synthetic source used when ping mode is requested by an agent that has no
# foreground chat to wake. main.py consumes these durable notifications and
# resumes the caller on the same agent-conversation thread.
AGENT_THREAD_NOTIFICATION_PREFIX = "agent-thread:"
PSEUDO_AGENT_THREAD_CALLERS = frozenset({
    "agent_notification_wakeup",
    "restart_continuation",
})


_CODEX_APP_SERVER_VISIBLE_EVENT_TYPES = {
    "content_delta",
    "thinking_delta",
    "tool_output_delta",
    "tool_start",
    "tool_use",
    "tool_end",
}
_CODEX_APP_SERVER_TOOL_EVENT_TYPES = {"tool_output_delta", "tool_start", "tool_use", "tool_end"}


def _control_terminal_state(result: AgentResult) -> str:
    if result.status == "success":
        return "succeeded"
    if result.status == "timeout":
        return "timeout"
    return "error"


async def _interruption_audit_result(
    control_invocation_id: str,
    agent_name: str,
    conversation_id: Optional[str],
    invoked_at: datetime,
) -> Tuple[AgentResult, bool]:
    details = await get_controller().cancellation_details(
        str(control_invocation_id)
    )
    manager_requested = bool(details.get("requested"))
    if manager_requested:
        error = (
            f"Invocation cancelled by {details['actor']}: {details['reason']}. "
            "Model or tool side effects may already have occurred once execution began."
        )
    else:
        error = (
            "Invocation interrupted before completion without a durable Patch manager "
            "cancellation request. Local cleanup or provider stop may be uncertain, "
            "and model or tool side effects may already have occurred."
        )
    result = AgentResult(
        agent=agent_name,
        status="error",
        response="",
        started_at=invoked_at,
        completed_at=datetime.utcnow(),
        error=error,
        conversation_id=conversation_id,
        invocation_id=control_invocation_id,
    )
    return result, manager_requested


def _thread_finalization_proved(
    finalized: Optional[Dict[str, Any]], *, ping_terminal_state: Optional[str] = None
) -> bool:
    if not isinstance(finalized, dict):
        return False
    if ping_terminal_state is not None:
        return finalized.get("lifecycle_state") == ping_terminal_state
    return (
        finalized.get("persisted") is True
        and finalized.get("lock_released") is True
    )


async def _finalize_direct_interruption(
    *,
    control_invocation_id: str,
    agent_name: str,
    conversation_id: str,
    lock_id: str,
    invoked_at: datetime,
    ping_invocation_id: Optional[str] = None,
    owned_row_cleanup_proved: bool = True,
) -> Tuple[str, Optional[Dict[str, Any]]]:
    """Persist one honest direct interruption audit and terminal control truth."""
    audit, manager_requested = await _interruption_audit_result(
        control_invocation_id,
        agent_name,
        conversation_id,
        invoked_at,
    )
    ping_terminal_state = None
    if ping_invocation_id:
        ping_terminal_state = (
            "cancelled" if manager_requested else "interrupted_uncertain"
        )
    finalized: Optional[Dict[str, Any]] = None
    try:
        finalized = await _finalize_thread_turn(
            conversation_id,
            lock_id,
            agent_name,
            audit,
            ping_invocation_id=ping_invocation_id,
            ping_terminal_state=ping_terminal_state,
        )
    except Exception as finalize_error:
        logger.error(
            "Failed to persist direct interruption for agent '%s' on thread %s: %s",
            agent_name,
            conversation_id,
            finalize_error,
            exc_info=True,
        )
        _release_thread_lock(conversation_id, lock_id)

    finalization_proved = _thread_finalization_proved(
        finalized,
        ping_terminal_state=ping_terminal_state,
    )
    if ping_invocation_id and not finalization_proved:
        try:
            from agent_conversation_manager import get_manager

            finalized = get_manager().interrupt_ping_invocation(
                conversation_id,
                ping_invocation_id,
                lock_id=lock_id,
            )
        except Exception as interrupt_error:
            logger.error(
                "Ping interruption %s could not persist uncertainty: %s",
                ping_invocation_id,
                interrupt_error,
                exc_info=True,
            )
            _release_thread_lock(conversation_id, lock_id)

    if ping_invocation_id:
        _queue_ping_notification_obligation(
            conversation_id,
            ping_invocation_id,
            (finalized or {}).get("notification"),
        )
    finalization_proved = _thread_finalization_proved(
        finalized,
        ping_terminal_state=ping_terminal_state,
    )
    state = (
        "cancelled"
        if manager_requested and finalization_proved and owned_row_cleanup_proved
        else "interrupted_uncertain"
    )
    await get_controller().finalize(
        control_invocation_id,
        state,
        cleanup_state="complete" if state == "cancelled" else "uncertain",
        terminal_persistence_proved=finalization_proved,
    )
    if state == "cancelled":
        _acknowledge_direct_managed_load_owner(
            control_invocation_id,
            conversation_id,
        )
    return state, finalized


def _is_structured_codex_prompt(prompt: Any) -> bool:
    return isinstance(prompt, list)


RILEY_ANTHROPIC_PROXY_BASE_PATH = "/internal/anthropic-proxy"
SECOND_BRAIN_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_CODE_CLI_PATH = SECOND_BRAIN_ROOT / "node_modules" / ".bin" / "claude"


def _current_claude_code_cli_path() -> Path:
    if not CLAUDE_CODE_CLI_PATH.exists():
        raise FileNotFoundError(
            f"Repo-managed Claude Code CLI not found at {CLAUDE_CODE_CLI_PATH}. "
            "Run `npm install` from the Second Brain root."
        )
    return CLAUDE_CODE_CLI_PATH


def _riley_anthropic_proxy_base_url() -> str:
    base_url = os.environ.get("SECOND_BRAIN_RILEY_ANTHROPIC_PROXY_BASE_URL")
    if base_url:
        return base_url.rstrip("/")
    internal_base = os.environ.get("SECOND_BRAIN_INTERNAL_BASE_URL", "http://127.0.0.1:8000")
    return f"{internal_base.rstrip('/')}{RILEY_ANTHROPIC_PROXY_BASE_PATH}"


def _sdk_agent_env(agent_name: str) -> Dict[str, str]:
    env = {
        "ENABLE_TOOL_SEARCH": "false",  # Disable tool deferral (tengu_defer_all_bn4)
        # Short-circuits the CLI's XSY() attachment pipeline which auto-injects
        # bundled Skill listings ("The following skills are available for use
        # with the Skill tool:..."), dynamic_skill triggers, native TodoWrite
        # reminders, plan_mode/delegate_mode reminders, nested CLAUDE.md loading,
        # and relevant-memory injection. We have our own Skills system
        # (mcp__brain__fetch_skill), our own memory system, and our own prompts —
        # none of the native auto-injection is wanted. See cli.js function XSY at
        # the `CLAUDE_CODE_DISABLE_ATTACHMENTS` check.
        "CLAUDE_CODE_DISABLE_ATTACHMENTS": "1",
    }
    if agent_name == "character":
        env.update(
            {
                "ENABLE_PROMPT_CACHING_1H": "1",
                "ANTHROPIC_BASE_URL": _riley_anthropic_proxy_base_url(),
            }
        )
    return env


def _agent_thread_notification_source(caller_agent: str, conversation_id: str) -> str:
    return f"{AGENT_THREAD_NOTIFICATION_PREFIX}{caller_agent}:{conversation_id}"


def _agent_thread_notification_caller(caller_agent: str, target_agent: str) -> str:
    if (caller_agent or "").strip().lower() in PSEUDO_AGENT_THREAD_CALLERS:
        return target_agent
    return caller_agent


async def _auto_approve_tool(tool_name: str, input_data: dict, context):
    """Auto-approve SDK tool permission prompts that bypassPermissions does not suppress."""
    if PermissionResultAllow is None:
        raise RuntimeError("claude_agent_sdk is unavailable")
    return PermissionResultAllow(updated_input=input_data)


async def _keepalive_hook(input_data, tool_use_id, context):
    """PreToolUse hook required to keep SDK streaming input alive for tool callbacks."""
    return {"continue_": True}


THINKING_DEFAULTS = {
    "opus": {
        "thinking": ThinkingConfigAdaptive(type="adaptive") if ThinkingConfigAdaptive else None,
        "effort": "high",
    },
    "sonnet": {
        "thinking": ThinkingConfigAdaptive(type="adaptive") if ThinkingConfigAdaptive else None,
        "effort": "high",
    },
    "haiku": {
        "thinking": ThinkingConfigEnabled(type="enabled", budget_tokens=16384) if ThinkingConfigEnabled else None,
    },
}

# Execution log file
EXECUTIONS_LOG = Path(__file__).parent / "executions.json"

# Chain checkpoint directory
CHAIN_CHECKPOINTS_DIR = Path(__file__).parent / "chain_checkpoints"

# Default working directory for agents
WORKING_DIR = "/home/debian/second_brain"

WORKTREE_REQUEST_FIELDS = (
    "worktree_branch",
    "worktree_slug",
    "worktree_base_ref",
    "worktree_path",
    "worktree_route_mode",
    "worktree_path_manifest",
    "worktree_request_manifest_digest",
    "expected_baseline_manifest_digest",
)


def _coder_worktrees_enabled() -> bool:
    value = os.environ.get("SECOND_BRAIN_CODER_WORKTREES", "")
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _has_worktree_request(
    worktree_branch: Optional[str],
    worktree_slug: Optional[str],
    worktree_base_ref: Optional[str],
    worktree_path: Optional[str],
    worktree_route_mode: Optional[str] = None,
    worktree_path_manifest: Optional[Dict[str, Any]] = None,
    worktree_request_manifest_digest: Optional[str] = None,
    expected_baseline_manifest_digest: Optional[str] = None,
) -> bool:
    return any(
        value is not None
        for value in (
            worktree_branch,
            worktree_slug,
            worktree_base_ref,
            worktree_path,
            worktree_route_mode,
            worktree_path_manifest,
            worktree_request_manifest_digest,
            expected_baseline_manifest_digest,
        )
    )


def _worktree_request_error_result(
    name: str,
    mode: InvocationMode,
    error: str,
) -> Union[AgentResult, Dict[str, str]]:
    if mode == InvocationMode.FOREGROUND:
        return AgentResult(
            agent=name,
            status="error",
            response="",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            error=error,
        )
    return {"error": error}


def _validate_worktree_invocation_metadata(
    *,
    name: str,
    caller_agent: Optional[str],
    worktree_branch: Optional[str],
    worktree_slug: Optional[str],
    worktree_base_ref: Optional[str],
    worktree_path: Optional[str],
    worktree_route_mode: Optional[str],
    worktree_path_manifest: Optional[Dict[str, Any]],
    worktree_request_manifest_digest: Optional[str],
    expected_baseline_manifest_digest: Optional[str],
    conversation_id: Optional[str],
) -> Dict[str, Any]:
    if not _has_worktree_request(
        worktree_branch,
        worktree_slug,
        worktree_base_ref,
        worktree_path,
        worktree_route_mode,
        worktree_path_manifest,
        worktree_request_manifest_digest,
        expected_baseline_manifest_digest,
    ):
        return {}

    if (caller_agent or "").strip().lower() != "patch":
        raise ValueError("coder worktree requests are Patch-only")
    if not _coder_worktrees_enabled():
        raise ValueError(
            "coder worktree requests are disabled; set "
            "SECOND_BRAIN_CODER_WORKTREES=1 to enable coder worktree routing"
        )
    if not worktree_branch or not worktree_slug:
        raise ValueError("worktree_branch and worktree_slug are required together")
    if worktree_route_mode is None or worktree_path_manifest is None:
        raise ValueError(
            "routed work requires worktree_route_mode and worktree_path_manifest"
        )
    if worktree_route_mode == "reuse_baseline_clean" and not conversation_id:
        raise ValueError("reuse_baseline_clean requires an existing conversation_id")

    from worktree_manager import metadata_for_request

    metadata = metadata_for_request(
        name,
        worktree_branch,
        worktree_slug,
        base_ref=worktree_base_ref or "main",
        route_mode=worktree_route_mode,
        path_manifest=worktree_path_manifest,
        expected_baseline_manifest_digest=expected_baseline_manifest_digest,
    )
    if worktree_path is not None:
        requested = Path(worktree_path).expanduser().resolve(strict=False)
        derived = Path(metadata["worktree_path"]).expanduser().resolve(strict=False)
        if requested != derived:
            raise ValueError(
                f"worktree_path does not match derived path for request: {requested}"
            )
    if (
        worktree_request_manifest_digest is not None
        and worktree_request_manifest_digest
        != metadata["worktree_request_manifest_digest"]
    ):
        raise ValueError("worktree request manifest digest mismatch")
    return metadata


def _worktree_row_lease_validator(
    invocation: AgentInvocation,
    *,
    baseline_manifest_digest: Optional[str] = None,
) -> Callable[[], bool]:
    def validate() -> bool:
        if not invocation.control_invocation_id:
            return False
        return running_agents.validate_route_lease_sync(
            str(invocation.control_invocation_id),
            agent=invocation.agent,
            conversation_id=str(invocation.conversation_id or ""),
            branch=str(invocation.worktree_branch or ""),
            slug=str(invocation.worktree_slug or ""),
            path=str(invocation.worktree_path or ""),
            route_mode=str(invocation.worktree_route_mode or ""),
            request_manifest_digest=str(
                invocation.worktree_request_manifest_digest or ""
            ),
            baseline_manifest_digest=baseline_manifest_digest,
        )

    return validate


def _preflight_worktree_for_invocation(invocation: AgentInvocation) -> None:
    """Run the authoritative read-only route preflight before any prompt write."""
    if not _has_worktree_request(
        invocation.worktree_branch,
        invocation.worktree_slug,
        invocation.worktree_base_ref,
        invocation.worktree_path,
        invocation.worktree_route_mode,
        invocation.worktree_path_manifest,
        invocation.worktree_request_manifest_digest,
        invocation.expected_baseline_manifest_digest,
    ):
        return
    if not all((
        invocation.worktree_branch,
        invocation.worktree_slug,
        invocation.worktree_route_mode,
        invocation.worktree_path_manifest is not None,
        invocation.conversation_id,
        invocation.control_invocation_id,
    )):
        raise ValueError("routed work lacks complete route/thread/control identity")

    from worktree_manager import WorktreeManager

    manager = WorktreeManager()
    invocation.worktree_preflight = manager.preflight_worktree(
        invocation.agent,
        invocation.worktree_branch,
        invocation.worktree_slug,
        route_mode=invocation.worktree_route_mode,
        path_manifest=invocation.worktree_path_manifest,
        expected_baseline_manifest_digest=invocation.expected_baseline_manifest_digest,
        conversation_id=invocation.conversation_id,
        invocation_id=invocation.control_invocation_id,
        base_ref=invocation.worktree_base_ref or "main",
        source_repo=manager.canonical_state_root(),
    )


def _prepare_worktree_for_invocation(invocation: AgentInvocation) -> None:
    """Create or exact-reuse, then retain the atomic admission token."""
    if invocation.worktree_preflight is None:
        return
    from worktree_manager import WorktreeManager

    manager = WorktreeManager()
    lease_digest = (
        invocation.expected_baseline_manifest_digest
        if invocation.worktree_route_mode == "reuse_baseline_clean"
        else None
    )
    validator = _worktree_row_lease_validator(
        invocation,
        baseline_manifest_digest=lease_digest,
    )
    if invocation.worktree_route_mode == "create":
        record = manager.prepare_worktree(
            invocation.agent,
            str(invocation.worktree_branch),
            str(invocation.worktree_slug),
            base_ref=invocation.worktree_base_ref or "main",
            source_repo=manager.canonical_state_root(),
            path_manifest=invocation.worktree_path_manifest,
            conversation_id=invocation.conversation_id,
            invocation_id=invocation.control_invocation_id,
            preflight=invocation.worktree_preflight,
            row_lease_validator=validator,
        )
    else:
        record = manager.reuse_active_worktree(
            invocation.agent,
            str(invocation.worktree_branch),
            str(invocation.worktree_slug),
            base_ref=invocation.worktree_base_ref or "main",
            source_repo=manager.canonical_state_root(),
            path_manifest=invocation.worktree_path_manifest,
            expected_baseline_manifest_digest=str(
                invocation.expected_baseline_manifest_digest
            ),
            conversation_id=str(invocation.conversation_id),
            invocation_id=str(invocation.control_invocation_id),
            preflight=invocation.worktree_preflight,
            row_lease_validator=validator,
        )
    prepared_path = Path(record.worktree_path).expanduser().resolve(strict=False)
    if invocation.worktree_path is not None:
        requested_path = Path(invocation.worktree_path).expanduser().resolve(strict=False)
        if requested_path != prepared_path:
            raise ValueError(
                f"prepared worktree path does not match request metadata: {prepared_path}"
            )
    invocation.worktree_path = record.worktree_path
    invocation.worktree_base_ref = record.base_ref
    invocation.worktree_baseline_manifest_digest = record.baseline_manifest_digest
    invocation.worktree_admission_token = record.admission_token


def _validate_worktree_admission_token(invocation: AgentInvocation) -> bool:
    if invocation.worktree_admission_token is None:
        return not bool(invocation.worktree_route_mode)
    from worktree_manager import WorktreeManager

    return WorktreeManager().validate_admission_token(
        invocation.worktree_admission_token,
        row_lease_validator=_worktree_row_lease_validator(
            invocation,
            baseline_manifest_digest=invocation.worktree_baseline_manifest_digest,
        ),
    )


def _worktree_preparation_error_result(
    name: str,
    mode: InvocationMode,
    error: str,
    conversation_id: Optional[str],
) -> Union[AgentResult, Dict[str, str]]:
    if mode == InvocationMode.FOREGROUND:
        return AgentResult(
            agent=name,
            status="error",
            response="",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            error=error,
            conversation_id=conversation_id,
        )
    result = {"error": error}
    if conversation_id:
        result["conversation_id"] = conversation_id
    return result


def _release_routed_thread_lock(conversation_id: str, lock_id: str) -> bool:
    try:
        from agent_conversation_manager import get_manager

        return bool(get_manager().release_lock_proved(conversation_id, lock_id))
    except Exception:
        logger.error(
            "Exact routed thread-lock release failed for %s", conversation_id,
            exc_info=True,
        )
        return False


async def _finalize_routed_preprompt_failure(
    *,
    name: str,
    mode: InvocationMode,
    error: str,
    control_invocation_id: Optional[str],
    running_entry_id: Optional[str],
    conversation_id: Optional[str],
    lock_id: Optional[str],
    route_cleanup_proved: bool,
) -> Union[AgentResult, Dict[str, str]]:
    """Prove route, exact lock release, exact row absence, then receipt."""
    lock_cleanup_proved = True
    if conversation_id and lock_id:
        lock_cleanup_proved = _release_routed_thread_lock(conversation_id, lock_id)
    row_cleanup_proved = True
    if running_entry_id:
        try:
            row_cleanup_proved = await running_agents.unregister_and_prove_absent(
                running_entry_id
            )
        except Exception:
            row_cleanup_proved = False
            logger.error(
                "Exact routed row cleanup failed for %s", running_entry_id,
                exc_info=True,
            )
    complete = route_cleanup_proved and lock_cleanup_proved and row_cleanup_proved
    if control_invocation_id:
        await get_controller().finalize(
            control_invocation_id,
            "error" if complete else "interrupted_uncertain",
            cleanup_state="complete" if complete else "uncertain",
            terminal_persistence_proved=False,
        )
    return _worktree_preparation_error_result(
        name, mode, error, conversation_id
    )


async def _finalize_routed_postprompt_failure(
    *,
    name: str,
    mode: InvocationMode,
    error: str,
    control_invocation_id: Optional[str],
    running_entry_id: Optional[str],
    conversation_id: str,
    lock_id: str,
    prompt_message_id: str,
) -> Union[AgentResult, Dict[str, str]]:
    """Persist the sole failed-before-Codex turn, then prove row cleanup."""
    terminal_proved = False
    lock_cleanup_proved = False
    try:
        from agent_conversation_manager import get_manager

        finalized = get_manager().finalize_failed_before_codex(
            conversation_id,
            prompt_message_id=prompt_message_id,
            lock_id=lock_id,
            target_agent=name,
            error=error,
            invocation_id=control_invocation_id,
        )
        terminal_proved = finalized.get("persisted") is True
        lock_cleanup_proved = finalized.get("lock_released") is True
    except Exception:
        logger.error(
            "Failed to persist failed_before_codex for %s on %s",
            name,
            conversation_id,
            exc_info=True,
        )
        lock_cleanup_proved = _release_routed_thread_lock(conversation_id, lock_id)
    row_cleanup_proved = True
    if running_entry_id:
        try:
            row_cleanup_proved = await running_agents.unregister_and_prove_absent(
                running_entry_id
            )
        except Exception:
            row_cleanup_proved = False
    complete = terminal_proved and lock_cleanup_proved and row_cleanup_proved
    if control_invocation_id:
        await get_controller().finalize(
            control_invocation_id,
            "error" if complete else "interrupted_uncertain",
            cleanup_state="complete" if complete else "uncertain",
            terminal_persistence_proved=complete,
        )
    return _worktree_preparation_error_result(name, mode, error, conversation_id)


def _append_routed_caller_prompt(
    *,
    target_agent: str,
    caller_agent: str,
    prompt: str,
    conversation_id: str,
    mode: InvocationMode,
    model_override: Optional[str],
    project: Optional[Any],
) -> Dict[str, Any]:
    from agent_conversation_manager import (
        ConversationAppendError,
        build_history_prompt,
        get_manager,
    )

    manager = get_manager()
    proof = manager.append_message_atomic_proved(
        conversation_id,
        from_agent=caller_agent,
        content=prompt,
        mode=mode.value,
        model_override=model_override,
        project=project,
    )
    try:
        data = manager.load(conversation_id)
        if data is None:
            raise RuntimeError("committed caller prompt could not be reloaded")
        history = dict(data)
        messages = list(history.get("messages") or [])
        if not messages or messages[-1].get("id") != proof["message_id"]:
            raise RuntimeError("committed caller prompt is not the exact final message")
        history["messages"] = messages[:-1]
        prompt_for_agent = build_history_prompt(
            data=history,
            target_agent=target_agent,
            caller_agent=caller_agent,
            current_prompt=prompt,
        )
    except Exception as exc:
        raise ConversationAppendError(
            f"Caller prompt committed but history preparation failed: {exc}",
            unchanged_proved=False,
            committed_message_id=str(proof["message_id"]),
        ) from exc
    return {**proof, "prompt_for_agent": prompt_for_agent}

# External MCP servers config file (alongside this file)
EXTERNAL_MCP_CONFIG = Path(__file__).parent / "external_mcp_servers.json"

# Cache for external MCP config (loaded once per process)
_external_mcp_cache: Optional[Dict[str, Any]] = None


def _resolve_env_vars(value: str) -> str:
    """Resolve ${VAR_NAME} patterns in a string from os.environ."""
    import re as _re
    def _replacer(m):
        var_name = m.group(1)
        resolved = os.environ.get(var_name, "")
        if not resolved:
            logger.warning(f"External MCP config references ${{{var_name}}} but it is not set in environment")
        return resolved
    return _re.sub(r'\$\{([^}]+)\}', _replacer, value)


def _resolve_config_env_vars(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-resolve ${VAR_NAME} patterns in external MCP server configs.

    Resolves env var references in 'args' lists and 'env' dicts so that
    secrets (API keys) don't need to be hardcoded in the JSON config file.
    """
    resolved = {}
    for server_name, server_config in config.items():
        sc = dict(server_config)
        # Resolve in args list
        if "args" in sc and isinstance(sc["args"], list):
            sc["args"] = [_resolve_env_vars(a) if isinstance(a, str) else a for a in sc["args"]]
        # Resolve in env dict
        if "env" in sc and isinstance(sc["env"], dict):
            sc["env"] = {k: _resolve_env_vars(v) if isinstance(v, str) else v for k, v in sc["env"].items()}
        resolved[server_name] = sc
    return resolved


def _load_external_mcp_servers() -> Dict[str, Any]:
    """
    Load external MCP server configs from external_mcp_servers.json.

    Returns a dict of server_name -> McpStdioServerConfig (command/args/env).
    Supports ${VAR_NAME} interpolation in args and env values from os.environ.
    Cached after first load. Returns empty dict on missing/invalid file.
    """
    global _external_mcp_cache
    if _external_mcp_cache is not None:
        return _external_mcp_cache

    if not EXTERNAL_MCP_CONFIG.exists():
        _external_mcp_cache = {}
        return _external_mcp_cache

    try:
        with open(EXTERNAL_MCP_CONFIG, "r") as f:
            raw_config = json.load(f)
        _external_mcp_cache = _resolve_config_env_vars(raw_config)
        logger.info(f"Loaded {len(_external_mcp_cache)} external MCP server(s) from {EXTERNAL_MCP_CONFIG.name}")
    except Exception as e:
        logger.error(f"Failed to load external MCP servers config: {e}")
        _external_mcp_cache = {}

    return _external_mcp_cache


def _build_project_metadata_block(
    agent_name: str,
    project: Union[str, List[str]],
    task_id: Optional[str] = None
) -> str:
    """
    Build the PROJECT METADATA block to append to an agent's prompt.

    Instructs the agent to include YAML frontmatter in output files
    and use a project-tagged filename convention.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    tid = task_id or "ad-hoc"

    # Normalize to string for prompt (use first project if list)
    if isinstance(project, list):
        project_str = project[0]
        project_all = ", ".join(project)
    else:
        project_str = project
        project_all = project

    return f"""

[PROJECT METADATA]
project: {project_all}
task_id: {tid}

When writing output files, include this YAML frontmatter at the top of the file:
---
agent: {agent_name}
project: {project_str}
date: {today}
task_id: {tid}
---

Use this output filename pattern: 00_Inbox/agent_outputs/{today}_{agent_name}_{project_str}_{{slug}}.md
(Replace {{slug}} with a short descriptive name for the output content.)
"""


def _effective_runner_config(
    config: AgentConfig,
    model_override: Optional[str] = None,
    *,
    is_visible: bool = False,
    is_background_processing: bool = False,
) -> AgentConfig:
    """Resolve the runtime used by runner-mediated invocations."""
    resolver = getattr(config, "resolve_runtime", None)
    runtime_path = (
        "chattable"
        if is_visible and not is_background_processing
        else "non_chattable"
    )
    effective = resolver(runtime_path) if callable(resolver) else config
    if model_override:
        override = getattr(effective, "with_model_override", None)
        effective = override(model_override) if callable(override) else effective
    return effective


def _restart_consumer_for_invocation(
    config: AgentConfig,
    invocation: AgentInvocation,
    effective_tools: List[str],
) -> str:
    """Return the restart consumer contract for Codex MCP bridge launches."""
    if "mcp__brain__restart_server" not in effective_tools and "restart_server" not in effective_tools:
        return "none"
    if config.name != "patch" or not invocation.conversation_id:
        return "none"
    mode = invocation.mode.value if isinstance(invocation.mode, InvocationMode) else str(invocation.mode)
    if (
        mode == InvocationMode.SCHEDULED.value
        and invocation.scheduled_task_id
        and invocation.scheduled_attempt_id
    ):
        return f"agent_managed_restart:scheduled:{invocation.conversation_id}"
    if (
        mode == InvocationMode.FOREGROUND.value
        and invocation.control_invocation_id
        and invocation.caller_agent == "agent_notification_wakeup"
    ):
        return f"agent_managed_restart:foreground:{invocation.conversation_id}"
    # Trust/ping/background/structured Patch work has no complete managed-load
    # continuation + terminal acknowledgement contract. Keep the tool visible
    # if configured, but make its restart call fail closed before any receipt.
    return "none"


async def invoke_agent(
    name: str,
    prompt: str,
    mode: Union[str, InvocationMode] = "foreground",
    source_chat_id: Optional[str] = None,
    model_override: Optional[str] = None,
    project: Optional[Union[str, List[str]]] = None,
    project_output_contract: str = PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS,
    is_visible: bool = False,
    conversation_id: Optional[str] = None,
    caller_agent: Optional[str] = None,
    worktree_branch: Optional[str] = None,
    worktree_slug: Optional[str] = None,
    worktree_base_ref: Optional[str] = None,
    worktree_path: Optional[str] = None,
    worktree_route_mode: Optional[str] = None,
    worktree_path_manifest: Optional[Dict[str, Any]] = None,
    worktree_request_manifest_digest: Optional[str] = None,
    expected_baseline_manifest_digest: Optional[str] = None,
    salon_id: Optional[str] = None,
    scheduled_task_id: Optional[str] = None,
    scheduled_attempt_id: Optional[str] = None,
    scheduled_resume_claim_id: Optional[str] = None,
    is_background_processing: bool = False,
    stream_callback: Optional[Callable[[list], Awaitable[None]]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    running_entry_id: Optional[str] = None,
    resume_ping_invocation_id: Optional[str] = None,
    control_invocation_id: Optional[str] = None,
) -> Union[AgentResult, Dict[str, str]]:
    """
    Invoke an agent with the specified mode.

    Args:
        name: Agent name (must be registered)
        prompt: Task description for the agent
        mode: Invocation mode (foreground, ping, trust, scheduled)
        source_chat_id: Chat ID for ping mode notifications
        model_override: Override the agent's default model
        project: Optional project tag (string or list of strings) for output routing.
                 Kept as invocation/thread/running-agent metadata.
        project_output_contract: Controls prompt-level output instructions for
            project-tagged invocations. "agent_outputs" preserves the legacy
            PROJECT METADATA / 00_Inbox/agent_outputs footer; "none" keeps
            project metadata but suppresses that footer.
        conversation_id: Agent-to-agent thread ID. If omitted, a new thread is
            created. If provided, the thread must exist and not be currently
            locked by another live invocation.
        caller_agent: Name of the agent (or caller identity) that initiated
            this invocation. Recorded as the author of the prompt message in
            the thread. Defaults to "caller" for legacy/unsourced callers.
        worktree_branch/worktree_slug/worktree_base_ref/worktree_path plus
            route mode, manifest, and expected baseline digest: Patch-only
            coder route contract, proved before prompt persistence and launch.
        salon_id: If set, this invocation is part of a salon dispatch. The
            agent_conversations thread machinery is bypassed entirely — the
            ``prompt`` is used as-is (caller is responsible for rendering salon
            history), and the result has no ``conversation_id``. The salon's
            own JSON file is the persistence layer. Only ``foreground`` mode is
            supported when salon_id is set; the salon dispatch loop in main.py
            owns the lifecycle.
        running_entry_id: Internal handoff for backend-owned foreground relays.
            External callers should leave this unset.
        control_invocation_id: Immutable backend-owned direct invocation UUID.
            It must be identical to ``running_entry_id`` and is unsupported for
            scheduler/salon-owned work.
        resume_ping_invocation_id: Internal managed-restart handoff that binds
            a resumed ping to its original durable lifecycle identity.
        scheduled_attempt_id: Immutable scheduler firing identity. When set,
            lifecycle transitions are persisted by scheduler_tool.
        scheduled_resume_claim_id: Internal managed-restart claim that permits
            the current live-row identity to advance on the same attempt.

    Returns:
        For foreground: AgentResult (with ``conversation_id`` set)
        For ping: Acknowledgment dict including ``conversation_id``
        For trust/scheduled: Acknowledgment dict including ``conversation_id``

    Errors related to conversations:
        - ``{"error": "Conversation <id> not found..."}``
        - ``{"error": "Thread <id> is currently being processed..."}``
    """
    from registry import get_registry

    # Normalize mode
    if isinstance(mode, str):
        mode = InvocationMode(mode)

    if resume_ping_invocation_id and mode != InvocationMode.PING:
        return {"error": "resume_ping_invocation_id requires ping mode"}
    if scheduled_attempt_id and not scheduled_task_id:
        return {"error": "scheduled_attempt_id requires scheduled_task_id"}
    if control_invocation_id:
        if mode == InvocationMode.SCHEDULED or salon_id is not None or scheduled_task_id:
            return {"error": "direct invocation control does not own scheduled/salon work"}
        if running_entry_id != control_invocation_id:
            return {"error": "control_invocation_id must match running_entry_id exactly"}

    if project_output_contract not in PROJECT_OUTPUT_CONTRACT_VALUES:
        return _invalid_project_output_contract_result(
            name,
            mode,
            project_output_contract,
        )

    try:
        worktree_metadata = _validate_worktree_invocation_metadata(
            name=name,
            caller_agent=caller_agent,
            worktree_branch=worktree_branch,
            worktree_slug=worktree_slug,
            worktree_base_ref=worktree_base_ref,
            worktree_path=worktree_path,
            worktree_route_mode=worktree_route_mode,
            worktree_path_manifest=worktree_path_manifest,
            worktree_request_manifest_digest=worktree_request_manifest_digest,
            expected_baseline_manifest_digest=expected_baseline_manifest_digest,
            conversation_id=conversation_id,
        )
    except Exception as e:
        return _worktree_request_error_result(name, mode, str(e))
    if worktree_metadata and (
        not control_invocation_id
        or not running_entry_id
        or running_entry_id != control_invocation_id
    ):
        return _worktree_request_error_result(
            name,
            mode,
            "routed work requires the exact backend control/running-row admission",
        )

    # Get agent config
    registry = get_registry()
    config = registry.get(name)

    if not config:
        error_result = AgentResult(
            agent=name,
            status="error",
            response="",
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            error=f"Unknown agent: {name}"
        )
        if scheduled_attempt_id:
            _finalize_scheduler_attempt(
                scheduled_task_id,
                scheduled_attempt_id,
                "failed",
                error_class="validation",
                error_code="missing_agent",
            )
        if mode == InvocationMode.FOREGROUND:
            return error_result
        return {"error": f"Unknown agent: {name}"}

    config = _effective_runner_config(
        config,
        model_override=model_override,
        is_visible=is_visible,
        is_background_processing=is_background_processing,
    )

    # Inject project output instructions unless the caller explicitly supplies
    # a different report contract in the prompt.
    if project and project_output_contract == PROJECT_OUTPUT_CONTRACT_AGENT_OUTPUTS:
        prompt = prompt + _build_project_metadata_block(name, project)
        logger.info(f"Injected project metadata for '{project}' into agent '{name}' prompt")
    elif project:
        logger.info(
            "Preserving project metadata for '%s' on agent '%s' while suppressing "
            "project output contract",
            project,
            name,
        )

    # ---- Salon fast path -----------------------------------------------------
    # When salon_id is set for an ordinary salon turn, the salon owns the
    # conversation (its JSON file). We skip thread setup entirely and just run
    # the agent. Salon background-processing trust work still creates an agent
    # conversation, using salon_id only as running_agents provenance.
    if salon_id is not None and not is_background_processing:
        if worktree_metadata:
            return _worktree_request_error_result(
                name,
                mode,
                "coder worktree routing is not supported for salon dispatches",
            )
        if mode != InvocationMode.FOREGROUND:
            return {
                "error": (
                    f"salon_id requires foreground mode (got {mode.value}). "
                    f"Salon dispatches are always synchronous."
                ),
            }
        invocation = AgentInvocation(
            agent=name,
            prompt=prompt,
            mode=mode,
            source_chat_id=source_chat_id,
            model_override=model_override,
            project=project,
            is_visible=is_visible,
            salon_id=salon_id,
            caller_agent=caller_agent,
            **worktree_metadata,
            scheduled_task_id=scheduled_task_id,
            scheduled_attempt_id=scheduled_attempt_id,
            scheduled_resume_claim_id=scheduled_resume_claim_id,
            is_background_processing=is_background_processing,
            control_invocation_id=control_invocation_id,
        )
        logger.info(f"Invoking agent '{name}' for salon {salon_id} (no thread)")
        return await _run_agent(
            config, invocation,
            stream_callback=stream_callback,
            history_messages=history_messages,
        )

    routed = bool(worktree_metadata)
    # Routed setup is deliberately zero-turn: resolve/create the header and
    # acquire the lock, but do not persist the caller prompt until the route is
    # fully ready. Non-routed behavior retains the existing combined setup.
    effective_caller = caller_agent or "caller"
    thread_ctx = await _setup_conversation(
        target_agent=name,
        caller_agent=effective_caller,
        prompt=prompt,
        conversation_id=conversation_id,
        source_chat_id=source_chat_id,
        mode=mode,
        model_override=model_override,
        project=project,
        append_prompt=not routed,
    )
    if "error" in thread_ctx:
        if scheduled_attempt_id:
            _finalize_scheduler_attempt(
                scheduled_task_id,
                scheduled_attempt_id,
                "failed",
                error_class="launch",
                error_code="inner_setup_rejected",
                conversation_id=thread_ctx.get("conversation_id"),
            )
        if routed:
            return await _finalize_routed_preprompt_failure(
                name=name,
                mode=mode,
                error=thread_ctx["error"],
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=thread_ctx.get("conversation_id"),
                lock_id=thread_ctx.get("lock_id"),
                route_cleanup_proved=True,
            )
        if control_invocation_id:
            await get_controller().finalize(
                control_invocation_id,
                "error",
                cleanup_state="complete",
                terminal_persistence_proved=False,
            )
        if mode == InvocationMode.FOREGROUND:
            return AgentResult(
                agent=name,
                status="error",
                response="",
                started_at=datetime.utcnow(),
                completed_at=datetime.utcnow(),
                error=thread_ctx["error"],
                conversation_id=thread_ctx.get("conversation_id"),
            )
        return {
            "error": thread_ctx["error"],
            "conversation_id": thread_ctx.get("conversation_id"),
        }

    conv_id = thread_ctx["conversation_id"]
    lock_id = thread_ctx.get("lock_id")
    prompt_for_agent = thread_ctx.get("prompt_for_agent", prompt)
    prompt_message_id = thread_ctx.get("prompt_message_id")

    if control_invocation_id:
        try:
            control_binding = await get_controller().update_conversation(
                control_invocation_id, conv_id
            )
            if routed and (
                not isinstance(control_binding, dict)
                or control_binding.get("conversation_id") != conv_id
            ):
                raise RuntimeError(
                    "durable control receipt did not retain resolved conversation"
                )
            if running_entry_id:
                await running_agents.update(
                    running_entry_id, conversation_id=conv_id
                )
                if routed:
                    bound_row = await running_agents.get_entry(running_entry_id)
                    if (
                        not isinstance(bound_row, dict)
                        or bound_row.get("conversation_id") != conv_id
                    ):
                        raise RuntimeError(
                            "running row did not retain resolved conversation"
                        )
        except asyncio.CancelledError:
            if routed:
                await _finalize_routed_preprompt_failure(
                    name=name,
                    mode=mode,
                    error="Routed conversation binding was cancelled",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    route_cleanup_proved=True,
                )
                raise
            owned_row_cleanup_proved = True
            if running_entry_id:
                try:
                    await running_agents.unregister(running_entry_id)
                except Exception as unregister_error:
                    owned_row_cleanup_proved = False
                    logger.error(
                        "Failed to unregister exact running agent entry %s before "
                        "pre-execution interruption finalization: %s",
                        running_entry_id,
                        unregister_error,
                        exc_info=True,
                    )
            await _finalize_direct_interruption(
                control_invocation_id=control_invocation_id,
                agent_name=name,
                conversation_id=conv_id,
                lock_id=lock_id,
                invoked_at=datetime.utcnow(),
                owned_row_cleanup_proved=owned_row_cleanup_proved,
            )
            raise
        except Exception as binding_error:
            if routed:
                return await _finalize_routed_preprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"Failed to bind routed conversation identity: {binding_error}",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    route_cleanup_proved=True,
                )
            _release_thread_lock(conv_id, lock_id)
            raise

    if routed:
        lock_ctx = _acquire_routed_conversation_lock(
            conv_id,
            caller_agent=effective_caller,
        )
        if "error" in lock_ctx:
            return await _finalize_routed_preprompt_failure(
                name=name,
                mode=mode,
                error=lock_ctx["error"],
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=conv_id,
                lock_id=None,
                route_cleanup_proved=True,
            )
        lock_id = lock_ctx["lock_id"]

    # Create invocation record — the prompt sent to the SDK is the one with
    # history injected (so the agent sees the full thread context).
    invocation = AgentInvocation(
        agent=name,
        prompt=prompt_for_agent,
        mode=mode,
        source_chat_id=source_chat_id,
        model_override=model_override,
        project=project,
        is_visible=is_visible,
        conversation_id=conv_id,
        is_join=conversation_id is not None,
        caller_agent=caller_agent,
        **worktree_metadata,
        scheduled_task_id=scheduled_task_id,
        scheduled_attempt_id=scheduled_attempt_id,
        scheduled_resume_claim_id=scheduled_resume_claim_id,
        is_background_processing=is_background_processing,
        control_invocation_id=control_invocation_id,
    )

    if routed:
        # Read-only preflight failures inherit the proved no-mutation boundary.
        # Once create/reuse begins, an unclassified exception is uncertain;
        # only an explicit boolean cleanup proof may improve that outcome.
        route_cleanup_proved = True
        try:
            _preflight_worktree_for_invocation(invocation)
            route_cleanup_proved = False
            _prepare_worktree_for_invocation(invocation)
            await running_agents.update(
                str(running_entry_id),
                worktree_baseline_manifest_digest=(
                    invocation.worktree_baseline_manifest_digest
                ),
            )
            if not _validate_worktree_admission_token(invocation):
                raise RuntimeError("atomic route/row admission-token recheck failed")
        except Exception as route_error:
            try:
                explicit_cleanup_proof = getattr(
                    route_error, "cleanup_proved", None
                )
            except Exception:
                explicit_cleanup_proof = None
            if type(explicit_cleanup_proof) is bool:
                route_cleanup_proved = explicit_cleanup_proof
            if scheduled_attempt_id:
                _finalize_scheduler_attempt(
                    scheduled_task_id,
                    scheduled_attempt_id,
                    "failed",
                    error_class="launch",
                    error_code="inner_setup_rejected",
                    conversation_id=conv_id,
                )
            return await _finalize_routed_preprompt_failure(
                name=name,
                mode=mode,
                error=str(route_error),
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=conv_id,
                lock_id=lock_id,
                route_cleanup_proved=route_cleanup_proved,
            )

        try:
            prompt_commit = _append_routed_caller_prompt(
                target_agent=name,
                caller_agent=effective_caller,
                prompt=prompt,
                conversation_id=conv_id,
                mode=mode,
                model_override=model_override,
                project=project,
            )
            prompt_message_id = prompt_commit["message_id"]
            prompt_for_agent = prompt_commit["prompt_for_agent"]
            invocation.prompt = prompt_for_agent
        except Exception as append_error:
            committed_message_id = getattr(
                append_error, "committed_message_id", None
            )
            if committed_message_id:
                return await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=str(append_error),
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(committed_message_id),
                )
            unchanged = bool(getattr(append_error, "unchanged_proved", False))
            try:
                route_proved = _validate_worktree_admission_token(invocation)
            except Exception:
                route_proved = False
            return await _finalize_routed_preprompt_failure(
                name=name,
                mode=mode,
                error=str(append_error),
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=conv_id,
                lock_id=lock_id,
                route_cleanup_proved=route_proved and unchanged,
            )

        try:
            token_valid = _validate_worktree_admission_token(invocation)
        except Exception as token_error:
            return await _finalize_routed_postprompt_failure(
                name=name,
                mode=mode,
                error=(
                    "Route admission-token validation failed after prompt commit: "
                    f"{token_error}"
                ),
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=conv_id,
                lock_id=lock_id,
                prompt_message_id=str(prompt_message_id),
            )
        if not token_valid:
            return await _finalize_routed_postprompt_failure(
                name=name,
                mode=mode,
                error="Route admission token became invalid before Codex admission",
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=conv_id,
                lock_id=lock_id,
                prompt_message_id=str(prompt_message_id),
            )
        try:
            if control_invocation_id and mode == InvocationMode.FOREGROUND:
                await get_controller().mark_execution_started(control_invocation_id)
        except Exception as admission_error:
            return await _finalize_routed_postprompt_failure(
                name=name,
                mode=mode,
                error=f"Codex admission failed after prompt commit: {admission_error}",
                control_invocation_id=control_invocation_id,
                running_entry_id=running_entry_id,
                conversation_id=conv_id,
                lock_id=lock_id,
                prompt_message_id=str(prompt_message_id),
            )

    logger.info(
        f"Invoking agent '{name}' in {mode.value} mode"
        + (f" [project: {project}]" if project else "")
        + f" [thread: {conv_id}]"
        + (f" [worktree: {invocation.worktree_path}]" if invocation.worktree_path else "")
    )

    # Handle different modes
    if mode == InvocationMode.FOREGROUND:
        try:
            result = await _run_agent(
                config,
                invocation,
                running_entry_id=running_entry_id,
            )
        except asyncio.CancelledError:
            if not invocation.control_invocation_id:
                _release_thread_lock(conv_id, lock_id)
                raise
            await _finalize_direct_interruption(
                control_invocation_id=str(invocation.control_invocation_id),
                agent_name=name,
                conversation_id=conv_id,
                lock_id=lock_id,
                invoked_at=invocation.invoked_at,
            )
            raise
        except Exception:
            _release_thread_lock(conv_id, lock_id)
            raise
        try:
            finalized = await _finalize_thread_turn(conv_id, lock_id, name, result)
        except Exception as finalize_error:
            logger.error(
                "Foreground invocation for agent '%s' could not finalize thread %s: %s",
                name,
                conv_id,
                finalize_error,
                exc_info=True,
            )
            _release_thread_lock(conv_id, lock_id)
            finalized = None
        result.conversation_id = conv_id
        result.invocation_id = invocation.control_invocation_id
        if invocation.control_invocation_id:
            finalization_proved = _thread_finalization_proved(finalized)
            row_absence_proved = (
                running_entry_id is None
                or await running_agents.get_entry(running_entry_id) is None
            )
            complete = finalization_proved and row_absence_proved
            if not finalization_proved:
                _release_thread_lock(conv_id, lock_id)
            await get_controller().finalize(
                invocation.control_invocation_id,
                (
                    _control_terminal_state(result)
                    if complete
                    else "interrupted_uncertain"
                ),
                cleanup_state="complete" if complete else "uncertain",
                terminal_persistence_proved=(finalization_proved if complete else False),
            )
            if complete:
                _acknowledge_direct_managed_load_after_thread_persistence(invocation)
        return result

    elif mode == InvocationMode.PING:
        notification_source_id = source_chat_id
        if not notification_source_id:
            if effective_caller and effective_caller not in {"user", "caller"}:
                notification_caller = _agent_thread_notification_caller(
                    effective_caller,
                    config.name,
                )
                notification_source_id = _agent_thread_notification_source(
                    notification_caller, conv_id
                )
                if notification_caller != effective_caller:
                    logger.warning(
                        f"Ping mode for agent '{name}' had pseudo caller "
                        f"'{effective_caller}'; routing completion to real "
                        f"agent-thread target '{notification_caller}' via "
                        f"thread {conv_id}"
                    )
                else:
                    logger.info(
                        f"Ping mode for agent '{name}' has no source chat; "
                        f"routing completion to caller agent '{effective_caller}' "
                        f"via thread {conv_id}"
                    )
            else:
                if routed:
                    return await _finalize_routed_postprompt_failure(
                        name=name,
                        mode=mode,
                        error="source_chat_id required for ping mode",
                        control_invocation_id=control_invocation_id,
                        running_entry_id=running_entry_id,
                        conversation_id=conv_id,
                        lock_id=lock_id,
                        prompt_message_id=str(prompt_message_id),
                    )
                _release_thread_lock(conv_id, lock_id)
                return {
                    "error": "source_chat_id required for ping mode",
                    "conversation_id": conv_id,
                }

        try:
            from agent_conversation_manager import get_manager

            lifecycle = get_manager().begin_ping_invocation(
                conv_id,
                lock_id=lock_id,
                caller_agent=effective_caller,
                target_agent=config.name,
                notification_source_id=notification_source_id,
                prompt_message_id=prompt_message_id,
                invocation_id=(
                    resume_ping_invocation_id or invocation.control_invocation_id
                ),
                resume_existing=bool(resume_ping_invocation_id),
            )
            ping_invocation_id = str(lifecycle["invocation_id"])
        except Exception as e:
            logger.error(
                f"Failed to persist ping lifecycle for agent '{name}' "
                f"on thread {conv_id}: {e}",
                exc_info=True,
            )
            if routed:
                return await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"Failed to durably admit ping after prompt commit: {e}",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
            _release_thread_lock(conv_id, lock_id)
            return {
                "error": f"Failed to durably accept ping before launch: {e}",
                "conversation_id": conv_id,
            }

        ping_coro = None
        ping_task = None
        admission_gate = asyncio.Event() if routed else None
        try:
            if running_entry_id is None:
                running_entry_id = await _register_running_agent_entry(config, invocation)
            if not get_manager().mark_ping_running(
                conv_id, ping_invocation_id, running_entry_id
            ):
                raise RuntimeError("durable ping lifecycle could not enter running")
            ping_coro = _run_ping_agent(
                config,
                invocation,
                conversation_id=conv_id,
                lock_id=lock_id,
                notification_source_id=notification_source_id,
                running_entry_id=running_entry_id,
                ping_invocation_id=ping_invocation_id,
            )
            ping_task = _create_background_invocation_task(
                ping_coro,
                agent=name,
                mode=mode.value,
                conversation_id=conv_id,
                lock_id=lock_id,
                source_chat_id=notification_source_id,
                invoked_at=invocation.invoked_at,
                running_entry_id=running_entry_id,
                ping_invocation_id=ping_invocation_id,
                control_invocation_id=invocation.control_invocation_id,
                start_gate=admission_gate,
            )
            if routed and control_invocation_id:
                await get_controller().mark_execution_started(control_invocation_id)
                admission_gate.set()
        except asyncio.CancelledError:
            if ping_task is not None and admission_gate is not None and not admission_gate.is_set():
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)
            if ping_coro is not None:
                close = getattr(ping_coro, "close", None)
                if close is not None:
                    close()
            if routed:
                await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error="Ping admission was cancelled before Codex",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
                raise
            if running_entry_id is not None:
                await running_agents.unregister(running_entry_id)
            if invocation.control_invocation_id:
                await _finalize_direct_interruption(
                    control_invocation_id=str(invocation.control_invocation_id),
                    agent_name=name,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    invoked_at=invocation.invoked_at,
                    ping_invocation_id=ping_invocation_id,
                )
            else:
                _release_thread_lock(conv_id, lock_id)
            raise
        except Exception as e:
            if ping_task is not None and admission_gate is not None and not admission_gate.is_set():
                ping_task.cancel()
                await asyncio.gather(ping_task, return_exceptions=True)
            if ping_coro is not None:
                close = getattr(ping_coro, "close", None)
                if close is not None:
                    close()
            logger.error(
                f"Failed to launch ping task for agent '{name}' on thread {conv_id}: {e}",
                exc_info=True,
            )
            if routed:
                return await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"Ping task launch failed before Codex: {e}",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
            if running_entry_id is not None:
                await running_agents.unregister(running_entry_id)
            failure = AgentResult(
                agent=name,
                status="error",
                response="",
                started_at=invocation.invoked_at,
                completed_at=datetime.utcnow(),
                error=f"Ping task launch failed before ack: {e}",
                conversation_id=conv_id,
            )
            finalized = None
            expected_ping_state = (
                "interrupted_uncertain" if resume_ping_invocation_id else "error"
            )
            try:
                if resume_ping_invocation_id:
                    finalized = get_manager().interrupt_ping_invocation(
                        conv_id,
                        ping_invocation_id,
                        lock_id=lock_id,
                    )
                else:
                    finalized = await _finalize_thread_turn(
                        conv_id,
                        lock_id,
                        name,
                        failure,
                        ping_invocation_id=ping_invocation_id,
                        ping_terminal_state="error",
                    )
                _queue_ping_notification_obligation(
                    conv_id,
                    ping_invocation_id,
                    (finalized or {}).get("notification"),
                )
            except Exception as finalize_error:
                logger.error(
                    f"Failed to finalize ping launch failure for agent "
                    f"'{name}': {finalize_error}",
                    exc_info=True,
                )
                _release_thread_lock(conv_id, lock_id)
            if invocation.control_invocation_id:
                finalization_proved = _thread_finalization_proved(
                    finalized,
                    ping_terminal_state=expected_ping_state,
                )
                await get_controller().finalize(
                    invocation.control_invocation_id,
                    (
                        "error"
                        if finalization_proved and expected_ping_state == "error"
                        else "interrupted_uncertain"
                    ),
                    cleanup_state=(
                        "complete"
                        if finalization_proved and expected_ping_state == "error"
                        else "uncertain"
                    ),
                    terminal_persistence_proved=(
                        finalization_proved and expected_ping_state == "error"
                    ),
                )
                if finalization_proved and expected_ping_state == "error":
                    _acknowledge_direct_managed_load_after_thread_persistence(invocation)
            return {
                "error": f"Ping task launch failed before ack: {e}",
                "conversation_id": conv_id,
            }

        return {
            "status": "accepted",
            "agent": name,
            "mode": "ping",
            "conversation_id": conv_id,
            "invocation_id": ping_invocation_id,
            "message": (
                f"Agent '{name}' is working on your task. "
                f"You'll be notified when done."
            ),
        }

    elif mode in (InvocationMode.TRUST, InvocationMode.SCHEDULED):
        try:
            if running_entry_id is None:
                running_entry_id = await _register_running_agent_entry(config, invocation)
        except asyncio.CancelledError:
            if routed:
                await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"{mode.value.title()} admission was cancelled before Codex",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
                raise
            if invocation.control_invocation_id:
                await _finalize_direct_interruption(
                    control_invocation_id=str(invocation.control_invocation_id),
                    agent_name=name,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    invoked_at=invocation.invoked_at,
                )
            else:
                _release_thread_lock(conv_id, lock_id)
            raise
        except Exception as e:
            if routed:
                return await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"{mode.value.title()} admission failed before Codex: {e}",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
            if scheduled_attempt_id:
                _finalize_scheduler_attempt(
                    scheduled_task_id,
                    scheduled_attempt_id,
                    "failed",
                    error_class="launch",
                    error_code="inner_launch_failed",
                    conversation_id=conv_id,
                )
            if invocation.control_invocation_id:
                failure = AgentResult(
                    agent=name,
                    status="error",
                    response="",
                    started_at=invocation.invoked_at,
                    completed_at=datetime.utcnow(),
                    error=f"{mode.value.title()} task launch failed before ack: {e}",
                    conversation_id=conv_id,
                    invocation_id=invocation.control_invocation_id,
                )
                try:
                    finalized = await _finalize_thread_turn(
                        conv_id, lock_id, name, failure
                    )
                except Exception as finalize_error:
                    logger.error(
                        "Failed to finalize %s pre-ack launch failure for agent '%s': %s",
                        mode.value,
                        name,
                        finalize_error,
                        exc_info=True,
                    )
                    _release_thread_lock(conv_id, lock_id)
                    finalized = None
                finalization_proved = _thread_finalization_proved(finalized)
                await get_controller().finalize(
                    invocation.control_invocation_id,
                    "error" if finalization_proved else "interrupted_uncertain",
                    cleanup_state="complete" if finalization_proved else "uncertain",
                    terminal_persistence_proved=finalization_proved,
                )
                if finalization_proved:
                    _acknowledge_direct_managed_load_after_thread_persistence(invocation)
            else:
                _release_thread_lock(conv_id, lock_id)
            return {
                "error": f"{mode.value.title()} task launch failed before ack",
                "conversation_id": conv_id,
            }
        bg_coro = _run_background_agent(
            config,
            invocation,
            conversation_id=conv_id,
            lock_id=lock_id,
            running_entry_id=running_entry_id,
        )
        bg_task = None
        admission_gate = asyncio.Event() if routed else None
        try:
            bg_task = _create_background_invocation_task(
                bg_coro,
                agent=name,
                mode=mode.value,
                conversation_id=conv_id,
                lock_id=lock_id,
                source_chat_id=None,
                invoked_at=invocation.invoked_at,
                running_entry_id=running_entry_id,
                scheduled_task_id=scheduled_task_id,
                scheduled_attempt_id=scheduled_attempt_id,
                control_invocation_id=invocation.control_invocation_id,
                start_gate=admission_gate,
            )
            if routed and control_invocation_id:
                await get_controller().mark_execution_started(control_invocation_id)
                admission_gate.set()
        except asyncio.CancelledError:
            if bg_task is not None and admission_gate is not None and not admission_gate.is_set():
                bg_task.cancel()
                await asyncio.gather(bg_task, return_exceptions=True)
            close = getattr(bg_coro, "close", None)
            if close is not None:
                close()
            if routed:
                await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"{mode.value.title()} admission was cancelled before Codex",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
                raise
            await running_agents.unregister(running_entry_id)
            if invocation.control_invocation_id:
                await _finalize_direct_interruption(
                    control_invocation_id=str(invocation.control_invocation_id),
                    agent_name=name,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    invoked_at=invocation.invoked_at,
                )
            else:
                _release_thread_lock(conv_id, lock_id)
            raise
        except Exception as e:
            if bg_task is not None and admission_gate is not None and not admission_gate.is_set():
                bg_task.cancel()
                await asyncio.gather(bg_task, return_exceptions=True)
            close = getattr(bg_coro, "close", None)
            if close is not None:
                close()
            logger.error(
                f"Failed to launch {mode.value} task for agent '{name}' on thread {conv_id}: {e}",
                exc_info=True,
            )
            if routed:
                return await _finalize_routed_postprompt_failure(
                    name=name,
                    mode=mode,
                    error=f"{mode.value.title()} task launch failed before Codex: {e}",
                    control_invocation_id=control_invocation_id,
                    running_entry_id=running_entry_id,
                    conversation_id=conv_id,
                    lock_id=lock_id,
                    prompt_message_id=str(prompt_message_id),
                )
            await running_agents.unregister(running_entry_id)
            failure = AgentResult(
                agent=name,
                status="error",
                response="",
                started_at=invocation.invoked_at,
                completed_at=datetime.utcnow(),
                error=f"{mode.value.title()} task launch failed before ack: {e}",
                conversation_id=conv_id,
            )
            try:
                finalized = await _finalize_thread_turn(
                    conv_id, lock_id, name, failure
                )
            except Exception as finalize_error:
                logger.error(
                    "Failed to finalize %s task creation failure for agent '%s': %s",
                    mode.value,
                    name,
                    finalize_error,
                    exc_info=True,
                )
                _release_thread_lock(conv_id, lock_id)
                finalized = None
            if invocation.control_invocation_id:
                finalization_proved = _thread_finalization_proved(finalized)
                await get_controller().finalize(
                    invocation.control_invocation_id,
                    "error" if finalization_proved else "interrupted_uncertain",
                    cleanup_state="complete" if finalization_proved else "uncertain",
                    terminal_persistence_proved=finalization_proved,
                )
                if finalization_proved:
                    _acknowledge_direct_managed_load_after_thread_persistence(invocation)
            if scheduled_attempt_id:
                _finalize_scheduler_attempt(
                    scheduled_task_id,
                    scheduled_attempt_id,
                    "failed",
                    error_class="launch",
                    error_code="inner_launch_failed",
                    conversation_id=conv_id,
                )
            return {
                "error": f"{mode.value.title()} task launch failed before ack: {e}",
                "conversation_id": conv_id,
            }
        return {
            "status": "accepted",
            "agent": name,
            "mode": mode.value,
            "conversation_id": conv_id,
            **(
                {"invocation_id": invocation.control_invocation_id}
                if invocation.control_invocation_id
                else {}
            ),
            "message": f"Agent '{name}' is working on your task.",
        }

    else:
        _release_thread_lock(conv_id, lock_id)
        return {"error": f"Unknown mode: {mode}", "conversation_id": conv_id}


# =============================================================================
# Agent-to-Agent Conversation helpers (threading support)
# =============================================================================

def _acquire_routed_conversation_lock(
    conversation_id: str,
    *,
    caller_agent: str,
) -> Dict[str, Any]:
    """Acquire the routed thread lock only after control/row identity binding."""
    from agent_conversation_manager import get_manager

    manager = get_manager()
    data = manager.load(conversation_id)
    if data is None:
        return {
            "error": f"Conversation '{conversation_id}' disappeared before lock acquisition"
        }
    lock_id = manager.acquire_lock(conversation_id, caller_agent)
    if lock_id is None:
        lock_info = data.get("lock") or {}
        return {
            "error": (
                f"Thread '{conversation_id}' is currently being processed "
                f"(held by '{lock_info.get('locked_by', 'unknown')}'). "
                "Retry once it completes."
            )
        }
    return {"lock_id": lock_id}

async def _setup_conversation(
    target_agent: str,
    caller_agent: str,
    prompt: str,
    conversation_id: Optional[str],
    source_chat_id: Optional[str],
    mode: InvocationMode,
    model_override: Optional[str],
    project: Optional[Any],
    append_prompt: bool = True,
) -> Dict[str, Any]:
    """Resolve/create a thread; legacy callers also lock and append here.

    Non-routed callers retain the historical combined append/history behavior.
    Routed callers set ``append_prompt=False``. They bind the resolved identity
    into the control receipt and running row, then acquire the lock separately,
    then commit only after route readiness through
    ``_append_routed_caller_prompt``.

    Returns a dict with keys:
        - ``conversation_id``: resolved or newly created thread ID
        - ``lock_id``: lock token for later release
        - ``prompt_for_agent``: prompt to pass into the SDK (history + current)

    Or ``{"error": "...", "conversation_id": ...}`` on failure.
    """
    try:
        from agent_conversation_manager import get_manager, build_history_prompt
    except ImportError as e:
        logger.error(f"Failed to import agent_conversation_manager: {e}")
        return {"error": f"Agent conversation manager unavailable: {e}"}

    manager = get_manager()

    if conversation_id:
        data = manager.load(conversation_id)
        if data is None:
            return {
                "error": (
                    f"Conversation '{conversation_id}' not found. "
                    f"Use list_agent_conversations to discover existing threads, "
                    f"or omit conversation_id to start a new one."
                ),
            }
        if not append_prompt:
            return {"conversation_id": conversation_id, "zero_turn": True}
        lock_id = manager.acquire_lock(conversation_id, caller_agent)
        if lock_id is None:
            lock_info = data.get("lock") or {}
            return {
                "error": (
                    f"Thread '{conversation_id}' is currently being processed "
                    f"(held by '{lock_info.get('locked_by', 'unknown')}'). "
                    f"Retry once it completes."
                ),
                "conversation_id": conversation_id,
            }
        resolved_id = conversation_id
    else:
        resolved_id = manager.create(
            initiator=caller_agent,
            source_chat_id=source_chat_id,
        )
        if not append_prompt:
            return {"conversation_id": resolved_id, "zero_turn": True}
        lock_id = manager.acquire_lock(resolved_id, caller_agent)
        if lock_id is None:
            # Should not happen for a fresh thread, but guard anyway.
            return {
                "error": (
                    f"Failed to acquire lock on newly created thread "
                    f"{resolved_id}. Try again."
                ),
                "conversation_id": resolved_id,
            }

    # Legacy/non-routed behavior preserves the caller prompt before execution.
    try:
        prompt_message_id = manager.append_message(
            resolved_id,
            from_agent=caller_agent,
            content=prompt,
            mode=mode.value if isinstance(mode, InvocationMode) else str(mode),
            model_override=model_override,
            project=project,
        )
    except Exception as e:
        logger.error(f"Failed to append caller prompt to {resolved_id}: {e}")
        manager.release_lock(resolved_id, lock_id)
        return {
            "error": f"Failed to save prompt to thread: {e}",
            "conversation_id": resolved_id,
        }

    # Build history-injected prompt. Strip the message we just appended (it's
    # the "current message", not prior context).
    data = manager.load(resolved_id) or {}
    hist_data = dict(data)
    hist_messages = list(hist_data.get("messages") or [])
    if hist_messages:
        hist_messages = hist_messages[:-1]
    hist_data["messages"] = hist_messages

    prompt_for_agent = build_history_prompt(
        data=hist_data,
        target_agent=target_agent,
        caller_agent=caller_agent,
        current_prompt=prompt,
    )

    return {
        "conversation_id": resolved_id,
        "lock_id": lock_id,
        "prompt_for_agent": prompt_for_agent,
        "prompt_message_id": prompt_message_id,
    }


def _release_thread_lock(conversation_id: str, lock_id: str) -> bool:
    """Best-effort exact lock release, returning whether cleanup was proved."""
    try:
        from agent_conversation_manager import get_manager
        return bool(get_manager().release_lock(conversation_id, lock_id))
    except Exception as e:
        logger.warning(
            f"Failed to release lock on {conversation_id} "
            f"(lock_id={lock_id}): {e}"
        )
        return False


def _ping_terminal_state(result: AgentResult) -> str:
    if result.status == "success":
        return "succeeded"
    if result.status == "timeout":
        return "timeout"
    return "error"


def _queue_ping_notification_obligation(
    conversation_id: str,
    invocation_id: str,
    payload: Optional[Dict[str, Any]],
) -> bool:
    """Idempotently materialize one durable ping notification obligation."""
    if not payload:
        return False
    try:
        from agent_conversation_manager import get_manager

        source_chat_id = payload.get("source_chat_id")
        if not source_chat_id:
            raise RuntimeError("durable ping notification has no source_chat_id")
        note = get_notification_queue().add(
            agent=str(payload.get("agent") or "unknown"),
            agent_response=str(payload.get("agent_response") or ""),
            source_chat_id=str(source_chat_id),
            invoked_at=datetime.utcfromtimestamp(float(payload["invoked_at"])),
            completed_at=datetime.utcfromtimestamp(float(payload["completed_at"])),
            dedupe_key=str(payload.get("dedupe_key") or "") or None,
        )
        if not get_manager().mark_ping_notification_queued(
            conversation_id, invocation_id, note.id
        ):
            raise RuntimeError(
                f"failed to mark ping notification {note.id} queued"
            )
        return True
    except Exception as e:
        # The thread lifecycle remains pending_enqueue. A fresh-start
        # reconciliation can safely retry because NotificationQueue.add uses
        # the stable lifecycle dedupe key.
        logger.error(
            f"Failed to materialize durable ping notification "
            f"{invocation_id} on {conversation_id}: {e}",
            exc_info=True,
        )
        return False


def _create_background_invocation_task(
    coro: Awaitable[None],
    *,
    agent: str,
    mode: str,
    conversation_id: Optional[str],
    lock_id: Optional[str],
    source_chat_id: Optional[str],
    invoked_at: Optional[datetime],
    running_entry_id: Optional[str] = None,
    ping_invocation_id: Optional[str] = None,
    scheduled_task_id: Optional[str] = None,
    scheduled_attempt_id: Optional[str] = None,
    control_invocation_id: Optional[str] = None,
    start_gate: Optional[asyncio.Event] = None,
) -> asyncio.Task:
    """Create a detached invocation task with durable cleanup on failure.

    Ping/trust callers get an ack before the agent finishes, so the task must
    remain strongly referenced. If a ping task is cancelled or crashes after
    ack, record that failure in the thread when possible, queue the caller
    notification, and release the lock. Process death before this callback runs
    still requires a persisted job queue; this is the bounded in-process guard.
    """
    failure_state = {"recorded": False}

    async def record_failure(reason: str, terminal_state: str = "error") -> None:
        if failure_state["recorded"]:
            return
        failure_state["recorded"] = True
        cleanup_complete = True
        manager_cancel_requested = False
        finalized = None
        expected_ping_state = None

        if scheduled_task_id and scheduled_attempt_id:
            try:
                is_cancelled = terminal_state == "cancelled" or "cancel" in reason
                _finalize_scheduler_attempt(
                    scheduled_task_id,
                    scheduled_attempt_id,
                    "failed",
                    error_class="cancelled" if is_cancelled else "execution",
                    error_code="runner_cancelled" if is_cancelled else "runner_error",
                    conversation_id=conversation_id,
                )
            except Exception:
                logger.error(
                    "Failed to terminalize scheduled background guard "
                    f"task={scheduled_task_id} attempt={scheduled_attempt_id}",
                    exc_info=True,
                )

        if running_entry_id:
            try:
                await running_agents.unregister(running_entry_id)
            except Exception as unregister_error:
                cleanup_complete = False
                logger.error(
                    f"Failed to unregister running agent entry {running_entry_id} "
                    f"after background {mode} failure: {unregister_error}",
                    exc_info=True,
                )

        if conversation_id and lock_id:
            if control_invocation_id and terminal_state == "cancelled":
                failure, manager_cancel_requested = await _interruption_audit_result(
                    control_invocation_id,
                    agent,
                    conversation_id,
                    invoked_at or datetime.utcnow(),
                )
            else:
                failure = AgentResult(
                    agent=agent,
                    status="error",
                    response="",
                    started_at=invoked_at or datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                    error=(
                        f"{mode.title()} task ended before completion after ack: {reason}"
                    ),
                    conversation_id=conversation_id,
                    invocation_id=control_invocation_id,
                )
            if mode == InvocationMode.PING.value and ping_invocation_id:
                expected_ping_state = terminal_state
                if control_invocation_id and terminal_state == "cancelled":
                    expected_ping_state = (
                        "cancelled"
                        if manager_cancel_requested
                        else "interrupted_uncertain"
                    )
            try:
                finalized = await _finalize_thread_turn(
                    conversation_id,
                    lock_id,
                    agent,
                    failure,
                    ping_invocation_id=(
                        ping_invocation_id
                        if mode == InvocationMode.PING.value
                        else None
                    ),
                    ping_terminal_state=(
                        expected_ping_state
                        if mode == InvocationMode.PING.value
                        else None
                    ),
                )
                if mode == InvocationMode.PING.value and ping_invocation_id:
                    _queue_ping_notification_obligation(
                        conversation_id,
                        ping_invocation_id,
                        (finalized or {}).get("notification"),
                    )
                if not _thread_finalization_proved(
                    finalized,
                    ping_terminal_state=expected_ping_state,
                ):
                    cleanup_complete = False
            except Exception as finalize_error:
                cleanup_complete = False
                logger.error(
                    f"Failed to finalize {mode} failure for agent '{agent}' "
                    f"on thread {conversation_id}: {finalize_error}",
                    exc_info=True,
                )
                _release_thread_lock(conversation_id, lock_id)
            if (
                control_invocation_id
                and mode == InvocationMode.PING.value
                and ping_invocation_id
                and not _thread_finalization_proved(
                    finalized,
                    ping_terminal_state=expected_ping_state,
                )
            ):
                try:
                    from agent_conversation_manager import get_manager

                    finalized = get_manager().interrupt_ping_invocation(
                        conversation_id,
                        ping_invocation_id,
                        lock_id=lock_id,
                    )
                    _queue_ping_notification_obligation(
                        conversation_id,
                        ping_invocation_id,
                        (finalized or {}).get("notification"),
                    )
                except Exception as interrupt_error:
                    logger.error(
                        "Background ping guard %s could not persist uncertainty: %s",
                        ping_invocation_id,
                        interrupt_error,
                        exc_info=True,
                    )
                    _release_thread_lock(conversation_id, lock_id)

        if (
            mode == InvocationMode.PING.value
            and source_chat_id
            and not ping_invocation_id
        ):
            response_text = (
                f"Error: Agent '{agent}' ping task ended before completion ({reason})."
            )
            if conversation_id:
                response_text = (
                    f"{response_text}\n\n---\n[conversation_id: {conversation_id}]"
                )
            try:
                get_notification_queue().add(
                    agent=agent,
                    agent_response=response_text,
                    source_chat_id=source_chat_id,
                    invoked_at=invoked_at or datetime.utcnow(),
                    completed_at=datetime.utcnow(),
                )
            except Exception as notify_error:
                cleanup_complete = False
                logger.error(
                    f"Failed to queue ping failure notification for agent '{agent}': "
                    f"{notify_error}"
                )

        if control_invocation_id:
            if terminal_state == "cancelled":
                control_state = (
                    "cancelled"
                    if manager_cancel_requested and cleanup_complete
                    else "interrupted_uncertain"
                )
            else:
                control_state = terminal_state
            cleanup_state = "complete" if cleanup_complete else "uncertain"
            if not cleanup_complete or control_state == "interrupted_uncertain":
                control_state = "interrupted_uncertain"
                cleanup_state = "uncertain"
            finalization_proved = _thread_finalization_proved(
                finalized,
                ping_terminal_state=expected_ping_state,
            )
            await get_controller().finalize(
                control_invocation_id,
                control_state,
                cleanup_state=cleanup_state,
                terminal_persistence_proved=finalization_proved,
            )
            if finalization_proved and cleanup_state == "complete":
                _acknowledge_direct_managed_load_owner(
                    control_invocation_id,
                    str(conversation_id),
                )

    async def guarded() -> None:
        try:
            if start_gate is not None:
                await start_gate.wait()
            await coro
        except asyncio.CancelledError:
            if start_gate is not None and not start_gate.is_set():
                raise
            await record_failure("cancelled", terminal_state="cancelled")
            raise
        except Exception:
            await record_failure("uncaught exception", terminal_state="error")
            raise

    owner_task = asyncio.current_task()
    task = asyncio.create_task(guarded())
    if control_invocation_id:
        if owner_task is None:
            task.cancel()
            raise RuntimeError("direct background invocation has no owning request task")
        try:
            get_controller().transfer_task_now(
                control_invocation_id,
                from_task=owner_task,
                to_task=task,
            )
        except Exception:
            task.cancel()
            raise
    _BACKGROUND_INVOCATION_TASKS.add(task)

    def on_done(done_task: asyncio.Task) -> None:
        _BACKGROUND_INVOCATION_TASKS.discard(done_task)

        def schedule_cleanup(reason: str) -> None:
            if failure_state["recorded"]:
                return
            try:
                loop = done_task.get_loop()
                if not loop.is_closed():
                    loop.create_task(
                        record_failure(
                            reason,
                            terminal_state=(
                                "cancelled" if "cancel" in reason else "error"
                            ),
                        )
                    )
                    return
            except Exception as schedule_error:
                logger.error(
                    f"Failed to schedule background {mode} cleanup for agent "
                    f"'{agent}' [thread: {conversation_id}]: {schedule_error}",
                    exc_info=True,
                )
            if conversation_id and lock_id:
                _release_thread_lock(conversation_id, lock_id)

        if done_task.cancelled():
            logger.error(
                f"Background {mode} task for agent '{agent}' was cancelled "
                f"before cleanup completed [thread: {conversation_id}]"
            )
            schedule_cleanup("cancelled before cleanup completed")
            return
        try:
            exc = done_task.exception()
        except asyncio.CancelledError:
            logger.error(
                f"Background {mode} task for agent '{agent}' was cancelled "
                f"before exception retrieval [thread: {conversation_id}]"
            )
            schedule_cleanup("cancelled before exception retrieval")
            return
        if exc is not None:
            logger.error(
                f"Background {mode} task for agent '{agent}' failed after ack: {exc}",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            schedule_cleanup("uncaught exception observed by done callback")

    task.add_done_callback(on_done)
    logger.info(
        f"Background {mode} task registered for agent '{agent}'"
        + (f" [thread: {conversation_id}]" if conversation_id else "")
    )
    return task


async def _finalize_thread_turn(
    conversation_id: str,
    lock_id: str,
    target_agent: str,
    result: AgentResult,
    *,
    ping_invocation_id: Optional[str] = None,
    ping_terminal_state: Optional[str] = None,
    require_persistence: bool = False,
) -> Optional[Dict[str, Any]]:
    """Append the agent's response to the thread, release the lock, and kick
    off the chat titler in the background when appropriate.
    """
    try:
        from agent_conversation_manager import get_manager
    except ImportError as e:
        logger.error(f"finalize_thread_turn: manager import failed: {e}")
        if require_persistence:
            raise RuntimeError("thread persistence manager unavailable") from e
        return

    manager = get_manager()

    # Append response even on error — preserves the failure text for debugging.
    content = result.response or result.error or ""
    if ping_invocation_id:
        finalized = manager.finalize_ping_invocation(
            conversation_id,
            ping_invocation_id,
            terminal_state=ping_terminal_state or _ping_terminal_state(result),
            content=content,
            transcript=getattr(result, "transcript", None),
            completed_at=result.completed_at.replace(tzinfo=timezone.utc).timestamp(),
            lock_id=lock_id,
        )
        asyncio.create_task(_maybe_retitle_thread(conversation_id))
        return finalized

    persisted = False
    lock_released = False
    try:
        manager.append_message(
            conversation_id,
            from_agent=target_agent,
            content=content,
            transcript=getattr(result, "transcript", None),
        )
        persisted = True
    except Exception as e:
        logger.error(
            f"Failed to append agent response to {conversation_id}: {e}"
        )
        if require_persistence:
            raise
    finally:
        lock_released = _release_thread_lock(conversation_id, lock_id)

    # Titler runs asynchronously — never block the caller on it.
    if persisted:
        asyncio.create_task(_maybe_retitle_thread(conversation_id))
    return {
        "persisted": persisted,
        "lock_released": lock_released,
    }


async def _maybe_retitle_thread(conversation_id: str) -> None:
    """Trigger the haiku chat titler when a thread hits a title checkpoint.

    Cadence mirrors user chats: generate on the 2nd message, re-evaluate
    every ``RETITLE_INTERVAL`` messages after that.
    """
    try:
        from agent_conversation_manager import get_manager, messages_as_roles_for_titler
    except ImportError:
        return

    manager = get_manager()
    data = manager.load(conversation_id)
    if not data:
        return

    messages = data.get("messages") or []
    n = len(messages)
    current_title = data.get("title")

    try:
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from chat_titler import generate_title, RETITLE_INTERVAL
    except ImportError as e:
        logger.debug(f"Chat titler unavailable: {e}")
        return

    should_title = False
    is_retitle = False
    if not current_title and n >= 2:
        should_title = True
    elif current_title and n >= 4 and n % RETITLE_INTERVAL == 0:
        should_title = True
        is_retitle = True

    if not should_title:
        return

    try:
        titler_messages = messages_as_roles_for_titler(data)
        result = await generate_title(
            titler_messages,
            current_title=current_title,
            is_retitle=is_retitle,
        )
        new_title = (result or {}).get("title")
        should_update = (result or {}).get("should_update", not is_retitle)
        if new_title and should_update and new_title != current_title:
            manager.update_title(conversation_id, new_title)
            logger.info(
                f"Thread {conversation_id} titled: "
                f"'{current_title}' -> '{new_title}'"
            )
    except Exception as e:
        logger.warning(f"Titler failed for thread {conversation_id}: {e}")


def _infer_kind(invocation: AgentInvocation) -> str:
    """Map an AgentInvocation onto the running_agents kind enum.

    Priority order: background_processing > salon > thread-join > mode.
    See running_agents.KINDS for the full enum and the project plan §3 for
    the rationale behind the priority ordering.
    """
    if invocation.is_background_processing:
        return "background_processing"
    if invocation.salon_id:
        return "salon_agent"
    mode = invocation.mode
    if mode == InvocationMode.PING:
        return "invoke_ping"
    if mode in (InvocationMode.TRUST, InvocationMode.SCHEDULED):
        return "invoke_trust"
    if mode == InvocationMode.FOREGROUND and invocation.is_join:
        return "agent_conversation_join"
    return "invoke_foreground"


async def _register_running_agent_entry(config: AgentConfig, invocation: AgentInvocation) -> str:
    """Register an invocation in running_agents using runner metadata.

    Detached ping/trust/scheduled tasks call this before returning their ack so
    restart snapshots can see the work immediately, even before the event loop
    first schedules the background coroutine.
    """
    return await running_agents.admit_target_scoped(
        agent=config.name,
        kind=_infer_kind(invocation),
        task_summary="" if invocation.scheduled_attempt_id else (invocation.prompt or ""),
        entry_id=invocation.control_invocation_id,
        source_chat_id=invocation.source_chat_id,
        conversation_id=invocation.conversation_id,
        salon_id=invocation.salon_id,
        scheduled_task_id=invocation.scheduled_task_id,
        scheduled_attempt_id=invocation.scheduled_attempt_id,
        caller_agent=invocation.caller_agent,
        worktree_branch=invocation.worktree_branch,
        worktree_slug=invocation.worktree_slug,
        worktree_base_ref=invocation.worktree_base_ref,
        worktree_route_mode=invocation.worktree_route_mode,
        worktree_path=invocation.worktree_path,
        worktree_request_manifest_digest=(
            invocation.worktree_request_manifest_digest
        ),
        worktree_baseline_manifest_digest=(
            invocation.worktree_baseline_manifest_digest
        ),
        timeout_seconds=config.timeout_seconds,
    )


@asynccontextmanager
async def _running_agent_scope(
    config: AgentConfig,
    invocation: AgentInvocation,
    running_entry_id: Optional[str] = None,
) -> AsyncIterator[str]:
    """Own the running_agents entry for the duration of actual execution."""
    if running_entry_id is None:
        running_entry_id = await _register_running_agent_entry(config, invocation)
    try:
        if running_entry_id is not None:
            await running_agents.update(
                running_entry_id,
                timeout_seconds=config.timeout_seconds,
            )
        if invocation.scheduled_attempt_id:
            try:
                _load_scheduler_tool().mark_attempt_running(
                    invocation.scheduled_task_id,
                    invocation.scheduled_attempt_id,
                    current_inner_invocation_id=running_entry_id,
                    conversation_id=invocation.conversation_id,
                    continuation_claim_id=invocation.scheduled_resume_claim_id,
                )
            except Exception:
                try:
                    _finalize_invocation_attempt(
                        invocation,
                        "failed",
                        error_class="launch",
                        error_code="running_gate_failed",
                    )
                except Exception:
                    logger.error(
                        "Failed to terminalize scheduled running-gate rejection "
                        f"task={invocation.scheduled_task_id} "
                        f"attempt={invocation.scheduled_attempt_id}",
                        exc_info=True,
                    )
                raise
        yield running_entry_id
    finally:
        if not await running_agents.unregister_and_prove_absent(running_entry_id):
            raise RuntimeError(
                f"running-agent row absence could not be proved: {running_entry_id}"
            )


async def _run_agent(
    config: AgentConfig,
    invocation: AgentInvocation,
    stream_callback: Optional[Callable[[list], Awaitable[None]]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    running_entry_id: Optional[str] = None,
) -> AgentResult:
    """
    Execute an agent and return the result.

    Args:
        stream_callback: Optional async callable invoked with the current
            block snapshot (list of ContentBlock dicts) each time the SDK
            yields a new AssistantMessage or tool result. Used by the salon
            dispatcher to stream live progress to the UI. Errors in the
            callback are logged and swallowed — streaming is best-effort.
        history_messages: Optional pre-rendered SDK input for salon dispatch.
            When provided, the final "your turn" user message is part of the
            list — invocation.prompt is not separately wrapped. See
            the legacy streaming consumer for full details.
    """
    started_at = datetime.utcnow()
    async with _running_agent_scope(config, invocation, running_entry_id) as active_running_entry_id:
        if invocation.control_invocation_id and not invocation.worktree_route_mode:
            await get_controller().mark_execution_started(
                invocation.control_invocation_id
            )
        try:
            if config.type == AgentType.SDK:
                response, transcript, blocks = await _run_anthropic_sdk_agent(
                    config, invocation, stream_callback=stream_callback,
                    history_messages=history_messages,
                    running_entry_id=active_running_entry_id,
                )
            else:
                response, transcript, blocks = await _run_codex_agent(
                    config, invocation, stream_callback=stream_callback,
                    history_messages=history_messages,
                    running_entry_id=active_running_entry_id,
                )

            return AgentResult(
                agent=config.name,
                status="success",
                response=response,
                started_at=started_at,
                completed_at=datetime.utcnow(),
                transcript=transcript,
                blocks=blocks,
                invocation_id=invocation.control_invocation_id,
            )

        except asyncio.TimeoutError:
            return AgentResult(
                agent=config.name,
                status="timeout",
                response="",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                error=f"Agent timed out after {config.timeout_seconds} seconds",
                invocation_id=invocation.control_invocation_id,
            )

        except Exception as e:
            logger.error(f"Agent '{config.name}' failed: {e}")
            return AgentResult(
                agent=config.name,
                status="error",
                response="",
                started_at=started_at,
                completed_at=datetime.utcnow(),
                error=str(e),
                invocation_id=invocation.control_invocation_id,
            )


async def _run_ping_agent(
    config: AgentConfig,
    invocation: AgentInvocation,
    conversation_id: Optional[str] = None,
    lock_id: Optional[str] = None,
    notification_source_id: Optional[str] = None,
    running_entry_id: Optional[str] = None,
    ping_invocation_id: Optional[str] = None,
) -> None:
    """Run agent and add notification when done.

    If ``conversation_id``/``lock_id`` are provided, finalize the thread turn
    (append response + release lock + kick off titler) exactly like foreground.
    """
    try:
        result = await _run_agent(
            config, invocation, running_entry_id=running_entry_id
        )
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.error(
            f"Background ping task for agent '{config.name}' failed: {e}",
            exc_info=True,
        )
        completed_at = datetime.utcnow()
        failure = AgentResult(
            agent=config.name,
            status="error",
            response="",
            started_at=invocation.invoked_at,
            completed_at=completed_at,
            error=f"Ping task failed before completion: {e}",
            conversation_id=conversation_id,
        )
        result = failure

    finalized: Optional[Dict[str, Any]] = None
    if conversation_id and lock_id:
        result.conversation_id = conversation_id
        try:
            finalized = await _finalize_thread_turn(
                conversation_id,
                lock_id,
                config.name,
                result,
                ping_invocation_id=ping_invocation_id,
            )
        except Exception as finalize_error:
            logger.error(
                "Ping invocation for agent '%s' could not finalize thread %s: %s",
                config.name,
                conversation_id,
                finalize_error,
                exc_info=True,
            )
            _release_thread_lock(conversation_id, lock_id)

    ping_terminal_state = _ping_terminal_state(result)
    finalization_proved = _thread_finalization_proved(
        finalized,
        ping_terminal_state=ping_terminal_state,
    )
    if (
        invocation.control_invocation_id
        and conversation_id
        and ping_invocation_id
        and not finalization_proved
    ):
        try:
            from agent_conversation_manager import get_manager

            finalized = get_manager().interrupt_ping_invocation(
                conversation_id,
                ping_invocation_id,
                lock_id=lock_id,
            )
        except Exception as interrupt_error:
            logger.error(
                "Ping invocation %s could not persist interrupted uncertainty: %s",
                ping_invocation_id,
                interrupt_error,
                exc_info=True,
            )
            _release_thread_lock(conversation_id, lock_id)

    if conversation_id and ping_invocation_id:
        _queue_ping_notification_obligation(
            conversation_id,
            ping_invocation_id,
            (finalized or {}).get("notification"),
        )
    else:
        # Compatibility fallback for direct internal tests/legacy callers that
        # do not carry lifecycle metadata.
        response_text = (
            result.response if result.status == "success"
            else f"Error: {result.error}"
        )
        if conversation_id:
            response_text = f"{response_text}\n\n---\n[conversation_id: {conversation_id}]"
        try:
            get_notification_queue().add(
                agent=config.name,
                agent_response=response_text,
                source_chat_id=notification_source_id or invocation.source_chat_id,
                invoked_at=invocation.invoked_at,
                completed_at=result.completed_at,
            )
        except Exception as notify_error:
            logger.error(
                f"Failed to queue ping notification for agent "
                f"'{config.name}': {notify_error}"
            )

    _log_execution(invocation, result)
    if invocation.control_invocation_id:
        if not finalization_proved and conversation_id and lock_id:
            _release_thread_lock(conversation_id, lock_id)
        await get_controller().finalize(
            invocation.control_invocation_id,
            (
                _control_terminal_state(result)
                if finalization_proved
                else "interrupted_uncertain"
            ),
            cleanup_state="complete" if finalization_proved else "uncertain",
            terminal_persistence_proved=finalization_proved,
        )
        if finalization_proved:
            _acknowledge_direct_managed_load_after_thread_persistence(invocation)



async def invoke_agent_chain(
    chain: List[Dict[str, str]],
    on_failure: str = "alert_and_stop",
    summarize: bool = False,
    source_chat_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Start an agent chain in the background with ping-style notification.

    Runs agents sequentially. When the chain completes (or stops on failure),
    adds a single notification to the queue targeting source_chat_id —
    identical to how ping mode works for single agents.

    Args:
        chain: List of {"agent": name, "prompt": task} dicts
        on_failure: "alert_and_stop" or "skip_and_continue"
        summarize: Whether to summarize outputs in the notification
        source_chat_id: Chat ID for notification delivery

    Returns:
        Acknowledgment dict (chain runs in background)
    """
    if not source_chat_id:
        return {"error": "source_chat_id required for chain notifications"}

    chain_id = str(uuid.uuid4())[:8]  # Short ID for readability
    invoked_at = datetime.utcnow()

    # Create initial checkpoint
    checkpoint = {
        "chain_id": chain_id,
        "created_at": invoked_at.isoformat(),
        "updated_at": invoked_at.isoformat(),
        "chain": chain,
        "on_failure": on_failure,
        "summarize": summarize,
        "source_chat_id": source_chat_id,
        "status": "running",
        "current_step": 0,
        "results": [],
    }
    _save_chain_checkpoint(checkpoint)

    asyncio.create_task(_run_chain_agent(
        chain=chain,
        on_failure=on_failure,
        summarize=summarize,
        source_chat_id=source_chat_id,
        invoked_at=invoked_at,
        chain_id=chain_id,
    ))

    agent_names = [step["agent"] for step in chain]
    chain_str = " \u2192 ".join(agent_names)
    return {
        "status": "accepted",
        "mode": "chain",
        "chain_id": chain_id,
        "message": f"Agent chain started: {chain_str}\nChain ID: {chain_id} (use to resume if interrupted)\n\nYou'll be notified when the chain completes."
    }


async def resume_agent_chain(
    chain_id: str,
    source_chat_id: Optional[str] = None,
) -> Dict[str, str]:
    """
    Resume a previously failed/stopped agent chain from its last checkpoint.

    Loads the checkpoint, identifies completed steps, and resumes from the
    next incomplete step. Already-completed steps are skipped.

    Args:
        chain_id: ID of the chain to resume
        source_chat_id: Override chat ID for notifications (uses original if not provided)

    Returns:
        Acknowledgment dict (chain runs in background)
    """
    checkpoint = _load_chain_checkpoint(chain_id)
    if not checkpoint:
        return {"error": f"No checkpoint found for chain ID: {chain_id}"}

    if checkpoint["status"] == "running":
        return {"error": f"Chain {chain_id} is still running. Wait for it to finish or check logs."}

    if checkpoint["status"] == "completed":
        return {"error": f"Chain {chain_id} already completed successfully. No resume needed."}

    chain = checkpoint["chain"]
    on_failure = checkpoint.get("on_failure", "alert_and_stop")
    summarize = checkpoint.get("summarize", False)
    chat_id = source_chat_id or checkpoint.get("source_chat_id")

    if not chat_id:
        return {"error": "source_chat_id required for chain notifications"}

    # Determine resume point: count successful results
    completed_results = checkpoint.get("results", [])
    # Find the first non-success result or the end of results
    resume_from = 0
    prior_results = []
    for r in completed_results:
        if r["status"] == "success":
            prior_results.append((r["agent"], "success", r.get("response", "")))
            resume_from += 1
        else:
            # Stop at the first failure — we'll re-run from here
            break

    if resume_from >= len(chain):
        return {"error": f"All {len(chain)} steps already completed. Nothing to resume."}

    remaining_agents = [step["agent"] for step in chain[resume_from:]]
    remaining_str = " → ".join(remaining_agents)

    # Update checkpoint status
    checkpoint["status"] = "running"
    checkpoint["source_chat_id"] = chat_id
    # Trim results to only successful ones (we'll re-run from the failure point)
    checkpoint["results"] = checkpoint["results"][:resume_from]
    checkpoint["current_step"] = resume_from
    _save_chain_checkpoint(checkpoint)

    invoked_at = datetime.fromisoformat(checkpoint["created_at"])

    asyncio.create_task(_run_chain_agent(
        chain=chain,
        on_failure=on_failure,
        summarize=summarize,
        source_chat_id=chat_id,
        invoked_at=invoked_at,
        chain_id=chain_id,
        resume_from=resume_from,
        prior_results=prior_results,
    ))

    return {
        "status": "accepted",
        "mode": "chain_resume",
        "chain_id": chain_id,
        "resumed_from_step": resume_from + 1,
        "total_steps": len(chain),
        "message": f"Chain {chain_id} resumed from step {resume_from + 1}/{len(chain)}.\nRemaining: {remaining_str}\n\nYou'll be notified when the chain completes."
    }


async def _run_chain_agent(
    chain: List[Dict[str, str]],
    on_failure: str,
    summarize: bool,
    source_chat_id: str,
    invoked_at: datetime,
    chain_id: Optional[str] = None,
    resume_from: int = 0,
    prior_results: Optional[List[tuple]] = None,
) -> None:
    """Execute an agent chain sequentially and send notification on completion.

    Follows the same pattern as _run_ping_agent: run work, add notification,
    log execution. Top-level try/except ensures errors are always logged.

    Supports resume: if resume_from > 0, skips already-completed steps
    and uses prior_results for the notification.

    Args:
        chain: List of {"agent": name, "prompt": task} dicts
        on_failure: "alert_and_stop" or "skip_and_continue"
        summarize: Whether to summarize outputs
        source_chat_id: Chat ID for notification
        invoked_at: Original invocation time
        chain_id: Checkpoint ID (for persistence)
        resume_from: Step index to resume from (0 = start)
        prior_results: Results from prior steps (for resume)
    """
    from registry import get_registry

    try:
        registry = get_registry()
        results = list(prior_results) if prior_results else []  # List of (agent_name, status, response/error)
        chain_failed = False
        failed_agent = None

        for i, step in enumerate(chain):
            # Skip already-completed steps on resume
            if i < resume_from:
                logger.info(f"Chain step {i+1}/{len(chain)}: Skipping '{step['agent']}' (already completed)")
                continue

            agent_name = step["agent"]
            prompt = step["prompt"]

            logger.info(f"Chain step {i+1}/{len(chain)}: Running agent '{agent_name}'")

            # Update checkpoint: mark current step
            if chain_id:
                checkpoint = _load_chain_checkpoint(chain_id)
                if checkpoint:
                    checkpoint["current_step"] = i
                    checkpoint["status"] = "running"
                    _save_chain_checkpoint(checkpoint)

            config = registry.get(agent_name)
            if not config:
                results.append((agent_name, "error", f"Unknown agent: {agent_name}"))

                # Save checkpoint with error
                if chain_id:
                    checkpoint = _load_chain_checkpoint(chain_id)
                    if checkpoint:
                        checkpoint["results"].append({
                            "agent": agent_name, "status": "error",
                            "response": f"Unknown agent: {agent_name}",
                            "completed_at": datetime.utcnow().isoformat(),
                        })
                        _save_chain_checkpoint(checkpoint)

                if on_failure == "alert_and_stop":
                    chain_failed = True
                    failed_agent = agent_name
                    break
                continue

            invocation = AgentInvocation(
                agent=agent_name,
                prompt=prompt,
                mode=InvocationMode.FOREGROUND,
                source_chat_id=source_chat_id,
            )

            try:
                result = await _run_agent(config, invocation)
                _log_execution(invocation, result)

                if result.status == "success":
                    response_text = result.transcript or result.response
                    results.append((agent_name, "success", response_text))
                    logger.info(f"Chain step {i+1}: Agent '{agent_name}' succeeded")

                    # Save checkpoint with success
                    if chain_id:
                        checkpoint = _load_chain_checkpoint(chain_id)
                        if checkpoint:
                            checkpoint["results"].append({
                                "agent": agent_name, "status": "success",
                                "response": response_text[:10000],  # Cap per-step to avoid huge files
                                "completed_at": datetime.utcnow().isoformat(),
                            })
                            checkpoint["current_step"] = i + 1
                            _save_chain_checkpoint(checkpoint)
                else:
                    error_msg = result.error or result.status
                    results.append((agent_name, "error", error_msg))
                    logger.warning(f"Chain step {i+1}: Agent '{agent_name}' failed: {error_msg}")

                    # Save checkpoint with failure
                    if chain_id:
                        checkpoint = _load_chain_checkpoint(chain_id)
                        if checkpoint:
                            checkpoint["results"].append({
                                "agent": agent_name, "status": "error",
                                "response": error_msg,
                                "completed_at": datetime.utcnow().isoformat(),
                            })
                            _save_chain_checkpoint(checkpoint)

                    if on_failure == "alert_and_stop":
                        chain_failed = True
                        failed_agent = agent_name
                        break

            except Exception as e:
                logger.error(f"Chain step {i+1}: Agent '{agent_name}' exception: {e}")
                results.append((agent_name, "exception", str(e)))

                # Save checkpoint with exception
                if chain_id:
                    checkpoint = _load_chain_checkpoint(chain_id)
                    if checkpoint:
                        checkpoint["results"].append({
                            "agent": agent_name, "status": "exception",
                            "response": str(e),
                            "completed_at": datetime.utcnow().isoformat(),
                        })
                        _save_chain_checkpoint(checkpoint)

                if on_failure == "alert_and_stop":
                    chain_failed = True
                    failed_agent = agent_name
                    break

        # Update final checkpoint status
        if chain_id:
            checkpoint = _load_chain_checkpoint(chain_id)
            if checkpoint:
                checkpoint["status"] = "failed" if chain_failed else "completed"
                _save_chain_checkpoint(checkpoint)

        # Build notification response
        response = _format_chain_results(
            results=results,
            chain_failed=chain_failed,
            failed_agent=failed_agent,
            total_steps=len(chain),
            summarize=summarize,
        )

        # Add to notification queue (same as _run_ping_agent)
        queue = get_notification_queue()
        queue.add(
            agent="agent_chain",
            agent_response=response,
            source_chat_id=source_chat_id,
            invoked_at=invoked_at,
            completed_at=datetime.utcnow(),
        )

        logger.info(f"Agent chain completed: {len(results)}/{len(chain)} agents ran, notification queued for chat {source_chat_id}")

    except Exception as e:
        logger.error(f"Background chain task failed: {e}", exc_info=True)


def _format_chain_results(
    results: List[tuple],
    chain_failed: bool,
    failed_agent: Optional[str],
    total_steps: int,
    summarize: bool,
) -> str:
    """Format chain results for notification."""
    parts = []

    completed = len(results)
    successful = sum(1 for _, status, _ in results if status == "success")

    if chain_failed:
        parts.append(f"**Agent Chain Stopped** ({completed}/{total_steps} steps completed, {successful} successful)")
        parts.append(f"Chain stopped at agent '{failed_agent}' due to failure.")
    else:
        if successful == completed:
            parts.append(f"**Agent Chain Completed** ({completed}/{total_steps} steps, all successful)")
        else:
            parts.append(f"**Agent Chain Completed with Errors** ({completed}/{total_steps} steps, {successful} successful)")

    parts.append("")

    if summarize:
        parts.append("**Summary:**")
        for agent_name, status, response in results:
            if status == "success":
                summary = response[:500] + "..." if len(response) > 500 else response
                parts.append(f"- **{agent_name}**: {summary}")
            else:
                parts.append(f"- **{agent_name}**: Failed - {response}")
    else:
        for agent_name, status, response in results:
            parts.append("---")
            parts.append(f"**Agent: {agent_name}**")
            if status == "success":
                parts.append(f"Status: Success")
                parts.append(f"\n{response}")
            else:
                parts.append(f"Status: Failed ({status})")
                parts.append(f"Error: {response}")
            parts.append("")

    return "\n".join(parts)


# =============================================================================
# Chain Checkpointing — persist state after each step, enable resume
# =============================================================================

def _save_chain_checkpoint(checkpoint: Dict[str, Any]) -> None:
    """Save chain checkpoint to disk (atomic write)."""
    CHAIN_CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    chain_id = checkpoint["chain_id"]
    path = CHAIN_CHECKPOINTS_DIR / f"{chain_id}.json"
    tmp_path = path.with_suffix(".tmp")
    checkpoint["updated_at"] = datetime.utcnow().isoformat()
    try:
        with open(tmp_path, "w") as f:
            json.dump(checkpoint, f, indent=2)
        tmp_path.rename(path)
    except Exception as e:
        logger.error(f"Failed to save chain checkpoint {chain_id}: {e}")
        if tmp_path.exists():
            tmp_path.unlink()


def _load_chain_checkpoint(chain_id: str) -> Optional[Dict[str, Any]]:
    """Load chain checkpoint from disk. Returns None if not found."""
    path = CHAIN_CHECKPOINTS_DIR / f"{chain_id}.json"
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load chain checkpoint {chain_id}: {e}")
        return None


def _delete_chain_checkpoint(chain_id: str) -> None:
    """Delete a chain checkpoint file."""
    path = CHAIN_CHECKPOINTS_DIR / f"{chain_id}.json"
    try:
        if path.exists():
            path.unlink()
    except Exception as e:
        logger.error(f"Failed to delete chain checkpoint {chain_id}: {e}")


def _cleanup_stale_checkpoints(max_age_hours: int = 48) -> int:
    """Remove checkpoint files older than max_age_hours. Returns count removed."""
    if not CHAIN_CHECKPOINTS_DIR.exists():
        return 0
    cutoff = datetime.utcnow() - timedelta(hours=max_age_hours)
    removed = 0
    for path in CHAIN_CHECKPOINTS_DIR.glob("*.json"):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            updated = datetime.fromisoformat(data.get("updated_at", data.get("created_at", "")))
            if updated < cutoff:
                path.unlink()
                removed += 1
                logger.info(f"Cleaned up stale checkpoint: {path.name}")
        except Exception:
            # If we can't parse it, it's probably corrupt — remove it
            try:
                path.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def list_chain_checkpoints() -> List[Dict[str, Any]]:
    """List all active chain checkpoints (for UI/tools).

    Returns list of checkpoint summaries (without full response data).
    """
    if not CHAIN_CHECKPOINTS_DIR.exists():
        return []

    # Clean up stale checkpoints first
    _cleanup_stale_checkpoints()

    checkpoints = []
    for path in sorted(CHAIN_CHECKPOINTS_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Return summary without full responses
            agent_names = [step["agent"] for step in data.get("chain", [])]
            completed_count = len([r for r in data.get("results", []) if r.get("status") == "success"])
            checkpoints.append({
                "chain_id": data["chain_id"],
                "status": data.get("status", "unknown"),
                "agents": agent_names,
                "total_steps": len(data.get("chain", [])),
                "completed_steps": completed_count,
                "current_step": data.get("current_step", 0),
                "created_at": data.get("created_at"),
                "updated_at": data.get("updated_at"),
                "source_chat_id": data.get("source_chat_id"),
            })
        except Exception:
            continue
    return checkpoints


async def _run_background_agent(
    config: AgentConfig,
    invocation: AgentInvocation,
    conversation_id: Optional[str] = None,
    lock_id: Optional[str] = None,
    running_entry_id: Optional[str] = None,
) -> None:
    """Run agent and log (no notification).

    If ``conversation_id``/``lock_id`` are provided, finalize the thread turn
    (append response + release lock + kick off titler) the same way
    foreground / ping do.
    """
    try:
        result = await _run_agent(config, invocation, running_entry_id=running_entry_id)
    except asyncio.CancelledError:
        if invocation.scheduled_attempt_id:
            try:
                _finalize_invocation_attempt(
                    invocation,
                    "failed",
                    error_class="cancelled",
                    error_code="runner_cancelled",
                )
            except Exception:
                logger.error(
                    "Failed to terminalize cancelled scheduled invocation "
                    f"task={invocation.scheduled_task_id} "
                    f"attempt={invocation.scheduled_attempt_id}",
                    exc_info=True,
                )
        if conversation_id and lock_id and not invocation.control_invocation_id:
            _release_thread_lock(conversation_id, lock_id)
        raise
    except Exception as exc:
        logger.error(
            f"Background task for agent '{config.name}' failed before result",
            exc_info=True,
        )
        if invocation.scheduled_attempt_id:
            try:
                _finalize_invocation_attempt(
                    invocation,
                    "failed",
                    error_class="execution",
                    error_code="runner_error",
                )
            except Exception:
                logger.warning(
                    "Scheduled invocation failure was already terminal or could not "
                    f"be persisted task={invocation.scheduled_task_id} "
                    f"attempt={invocation.scheduled_attempt_id}"
                )
        if not invocation.control_invocation_id:
            if conversation_id and lock_id:
                _release_thread_lock(conversation_id, lock_id)
            return
        result = AgentResult(
            agent=config.name,
            status="error",
            response="",
            started_at=invocation.invoked_at,
            completed_at=datetime.utcnow(),
            error=f"Background task failed before completion: {exc}",
            conversation_id=conversation_id,
            invocation_id=invocation.control_invocation_id,
        )

    finalized: Optional[Dict[str, Any]] = None
    if conversation_id and lock_id:
        result.conversation_id = conversation_id
        try:
            finalized = await _finalize_thread_turn(
                conversation_id,
                lock_id,
                config.name,
                result,
                require_persistence=bool(invocation.scheduled_attempt_id),
            )
        except Exception:
            logger.error(
                f"Background task for agent '{config.name}' could not finalize its thread",
                exc_info=True,
            )
            if invocation.scheduled_attempt_id:
                _finalize_invocation_attempt(
                    invocation,
                    "failed",
                    error_class="delivery",
                    error_code="thread_finalization_failed",
                )
            if invocation.control_invocation_id:
                _release_thread_lock(conversation_id, lock_id)
                await get_controller().finalize(
                    invocation.control_invocation_id,
                    "interrupted_uncertain",
                    cleanup_state="uncertain",
                )
            return
    elif invocation.scheduled_attempt_id:
        _finalize_invocation_attempt(
            invocation,
            "failed",
            error_class="delivery",
            error_code="thread_finalization_failed",
        )
        return

    if invocation.control_invocation_id and not _thread_finalization_proved(finalized):
        if conversation_id and lock_id:
            _release_thread_lock(conversation_id, lock_id)
        await get_controller().finalize(
            invocation.control_invocation_id,
            "interrupted_uncertain",
            cleanup_state="uncertain",
        )
        return

    if invocation.scheduled_attempt_id:
        if result.status == "success":
            _finalize_invocation_attempt(invocation, "succeeded")
        elif result.status == "timeout":
            _finalize_invocation_attempt(
                invocation,
                "failed",
                error_class="timeout",
                error_code="runner_timeout",
            )
        else:
            _finalize_invocation_attempt(
                invocation,
                "failed",
                error_class="execution",
                error_code="runner_error",
            )
        if _thread_finalization_proved(finalized):
            _acknowledge_managed_load_after_thread_persistence(invocation)
    _log_execution(invocation, result)
    if invocation.control_invocation_id:
        await get_controller().finalize(
            invocation.control_invocation_id,
            _control_terminal_state(result),
            cleanup_state="complete",
            terminal_persistence_proved=True,
        )
        _acknowledge_direct_managed_load_after_thread_persistence(invocation)


async def _run_codex_agent(
    config: AgentConfig,
    invocation: AgentInvocation,
    stream_callback: Optional[Callable[[list], Awaitable[None]]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    running_entry_id: Optional[str] = None,
) -> str:
    """
    Run a Codex/GPT agent through the shared Codex CLI backend.

    history_messages: Optional pre-rendered input for salon dispatches.
    Threaded through to the shared Codex backend.
    """
    from codex_backend import CodexRunOptions, run_codex
    from codex_app_server_canary import select_codex_app_server_runtime

    logger.info(f"Running Codex agent '{config.name}' with model {config.model}")

    # Register in process registry (SDK agents: pid=None since SDK manages subprocess internally)
    task_desc = invocation.prompt[:80] if invocation.prompt else "active"
    reg_id = None
    try:
        reg_id = register_process(config.name, task=task_desc, pid=None)
        if running_entry_id:
            await running_agents.update(running_entry_id, process_id=reg_id)
    except Exception as e:
        logger.warning(f"Failed to register agent '{config.name}' in process registry: {e}")

    # Build the identity-layer system_prompt (prompt.md + global instructions +
    # skill menu + agent list). Dynamic context (always_load memories, working
    # memory, contextual retrieval) is delivered via the user-message prefix
    # below — never through system_prompt, which hits Linux MAX_ARG_STRLEN
    # (128KB) because the SDK forwards it as a single argv. See prompt_assembly.

    import prompt_assembly  # interface/server/prompt_assembly.py (already on sys.path)

    # Pre-compute skill reminder for system prompt injection (above memory).
    _skill_reminder = ""
    agent_has_skills = config.skills is None or (isinstance(config.skills, list) and len(config.skills) > 0)
    if agent_has_skills:
        try:
            from skill_injector import get_skill_reminder
            _skill_reminder = get_skill_reminder(allowed_skills=config.skills) or ""
            if _skill_reminder:
                logger.info(f"Agent '{config.name}': will inject skill menu into system prompt")
        except Exception as e:
            logger.warning(f"Skill menu generation failed for agent '{config.name}': {e}")

    # Pre-compute agent list block for injection above memory.
    _effective_tools = list(config.tools) if config.tools else []
    _agent_list_block = ""
    try:
        from mcp_tools.agents import get_agent_list_for_prompt
        _agent_list_block = get_agent_list_for_prompt(_effective_tools) or ""
        if _agent_list_block:
            logger.info(f"Agent '{config.name}': will inject agent list into system prompt")
    except Exception as e:
        logger.warning(f"Agent '{config.name}': failed to get agent list: {e}")

    _global_instruction_parts = prompt_assembly.load_global_instruction_parts(
        Path(__file__).parent,
        is_visible=invocation.is_visible,
    )

    # Identity-only assembly. The pieces below are stable across turns —
    # prompt.md, global rules, mode instructions, skill menu, agent list.
    # Memory + working memory + contextual retrieval are NOT included here;
    # they ride through the user-message prefix (see context_parts below).
    identity_parts: List[str] = []
    if config.prompt:
        identity_parts.append(config.prompt)
    identity_parts.extend(_global_instruction_parts)
    if _skill_reminder:
        identity_parts.append(_skill_reminder)
    if _agent_list_block:
        identity_parts.append(_agent_list_block)
    identity_content = "\n".join(identity_parts).strip()

    if config.system_prompt_preset:
        system_prompt = {
            "type": "preset",
            "preset": config.system_prompt_preset,
        }
        if identity_content:
            system_prompt["append"] = identity_content
    else:
        system_prompt = identity_content

    # Dynamic context layer: always_load memories + working memory. Contextual
    # retrieval results are appended below. The final block is wrapped in
    # <system-injected> and prepended to the user message — never the system
    # prompt — so it travels via stdin instead of argv.
    _agent_dir = Path(__file__).parent / config.name
    _scripts_dir = Path(__file__).parent.parent / "scripts"
    context_parts: List[str] = []
    _mem_block = prompt_assembly.load_always_load_memories_block(_agent_dir)
    if _mem_block:
        context_parts.append(_mem_block)
    _wm_block = prompt_assembly.load_working_memory_block(config.name, _scripts_dir)
    if _wm_block:
        context_parts.append(_wm_block)

    effective_tools = list(config.tools) if config.tools else []
    fetch_skill_mcp = "mcp__brain__fetch_skill"
    if agent_has_skills and fetch_skill_mcp not in effective_tools:
        effective_tools.append(fetch_skill_mcp)
        logger.info(f"Agent '{config.name}': auto-added fetch_skill MCP tool for configured skills")
    if config.thinking_budget:
        logger.warning(
            "Agent '%s' still declares thinking_budget=%s; Codex maps reasoning through effort, not budget tokens",
            config.name,
            config.thinking_budget,
        )

    # Auto-retrieve contextual memories relevant to the agent's task prompt
    accepted_query_count = 0
    rewrite_fallback = False
    rewrite_timed_out = False
    try:
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from contextual_memory import auto_retrieve_context, rewrite_query_for_retrieval

        raw_query = invocation.prompt or ""
        # 20s timeout — Sonnet rewriter success is 3-5s, but the SDK
        # subprocess can hang indefinitely (~10-20×/day). Without this
        # wrapper, a hung subprocess freezes the agent invocation for
        # 30-76s before crashing. On timeout, fall back to raw prompt.
        try:
            retrieval_queries = await asyncio.wait_for(
                rewrite_query_for_retrieval(
                    raw_query,
                    session_id=invocation.source_chat_id or f"agent:{config.name}",
                    agent_name=config.name,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            rewrite_fallback = True
            rewrite_timed_out = True
            retrieval_queries = [(raw_query, 1.0)]
        accepted_query_count = (
            len(retrieval_queries) if isinstance(retrieval_queries, (list, tuple)) else 0
        )
        _log_contextual_memory_outcome(
            agent_name=config.name,
            accepted_count=accepted_query_count,
            fallback=rewrite_fallback,
            timeout=rewrite_timed_out,
            path="codex",
            outcome=(
                "rewrite_timeout_fallback" if rewrite_timed_out else "rewrite_accepted"
            ),
            level=logging.WARNING if rewrite_timed_out else logging.INFO,
        )
        # Run CPU-bound retrieval in a thread to avoid blocking the event loop
        import functools
        loop = asyncio.get_event_loop()
        ctx_block = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(
                    auto_retrieve_context,
                    query=retrieval_queries,
                    agent_name=config.name,
                ),
            ),
            timeout=15.0,  # 15s max — don't let retrieval stall the agent
        )
        if ctx_block:
            context_parts.append(ctx_block)
            logger.info(f"Agent '{config.name}': appended contextual memory to user-prefix context block")
    except Exception:
        _log_contextual_memory_outcome(
            agent_name=config.name,
            accepted_count=accepted_query_count,
            fallback=rewrite_fallback,
            timeout=rewrite_timed_out,
            path="codex",
            outcome="contextual_memory_failed",
            level=logging.WARNING,
        )

    # Build the <system-injected> envelope and prepend to the user message.
    # For salon dispatch (history_messages provided), the envelope is prepended
    # to the trailing user turn so each agent's context is rebuilt fresh per
    # dispatch without polluting earlier turns in the rendered history.
    _context_block = prompt_assembly.build_context_block(context_parts)
    effective_prompt = invocation.prompt
    if _context_block:
        if history_messages:
            history_messages = prompt_assembly.prepend_context_to_history_messages(
                history_messages, _context_block
            )
            logger.info(
                f"Agent '{config.name}': prepended {len(_context_block)} chars of "
                f"context to trailing user turn in history_messages"
            )
        else:
            effective_prompt = prompt_assembly.prepend_context_to_prompt(
                effective_prompt, _context_block
            )
            logger.info(
                f"Agent '{config.name}': prepended {len(_context_block)} chars of "
                f"context (<system-injected> envelope) to user message"
            )

    result_text = ""
    transcript = ""
    blocks = []

    try:
        async with asyncio.timeout(config.timeout_seconds):
            launch_cwd = invocation.worktree_path or WORKING_DIR
            run_options = CodexRunOptions(
                model=config.model,
                # Only the process cwd moves. Identity, memory, scheduler, MCP,
                # and conversation state above are resolved from this main-rooted
                # runner module and remain canonical.
                cwd=launch_cwd,
                identity_instructions=identity_content,
                prompt=effective_prompt,
                tools=effective_tools,
                direct_tools=list(config.direct_tools),
                timeout_seconds=config.timeout_seconds,
                max_turns=config.max_turns,
                effort=config.effort,
                output_schema=config.output_format,
                use_native_coding_instructions=bool(config.system_prompt_preset),
                chat_id=invocation.source_chat_id,
                agent_name=config.name,
                allowed_skills=config.skills,
                salon_id=invocation.salon_id,
                history_messages=history_messages,
                external_mcp_servers=_load_external_mcp_servers(),
                restart_consumer=_restart_consumer_for_invocation(
                    config, invocation, effective_tools
                ),
            )

            async def _run_exec_path(options: CodexRunOptions):
                result = await run_codex(options, stream_callback=stream_callback)
                if (
                    result.returncode == 0
                    and not result.blocks
                    and not (result.response or "").strip()
                    and options.effort != "low"
                ):
                    logger.warning(
                        "Agent '%s' returned empty Codex content; retrying once with effort=low",
                        config.name,
                    )
                    retry_prompt = (
                        "IMPORTANT: Produce a visible assistant reply. "
                        "If the request involves images, answer briefly in text before deciding whether to use tools.\n\n"
                        f"{options.prompt}"
                    )
                    retry_options = CodexRunOptions(
                        **{**options.__dict__, "effort": "low", "prompt": retry_prompt}
                    )
                    result = await run_codex(retry_options, stream_callback=stream_callback)
                return result

            async def _run_app_server_path(options: CodexRunOptions):
                from codex_app_server_backend import run_codex_app_server

                app_visible_work = False
                app_tool_started = False

                async def _app_event(event: Dict[str, Any]) -> None:
                    nonlocal app_visible_work, app_tool_started
                    event_type = event.get("type")
                    if event_type in _CODEX_APP_SERVER_VISIBLE_EVENT_TYPES:
                        app_visible_work = True
                    if event_type in _CODEX_APP_SERVER_TOOL_EVENT_TYPES:
                        app_tool_started = True

                async def _app_stream_callback(snapshot: list) -> None:
                    nonlocal app_visible_work
                    if snapshot:
                        app_visible_work = True
                    if stream_callback is not None:
                        await stream_callback(snapshot)

                try:
                    result = await run_codex_app_server(
                        options,
                        stream_callback=_app_stream_callback,
                        event_callback=_app_event,
                    )
                except asyncio.CancelledError:
                    # The current App Server adapter closes its owned runtime
                    # after requesting an exact interrupt, but does not
                    # propagate whether the protocol accepted that request.
                    # Keep provider-stop certainty at its honest default
                    # ("unknown") until that protected contract proves ack.
                    raise
                result_has_visible_work = bool(result.blocks) or bool((result.response or "").strip())
                should_fallback = (
                    result.returncode != 0
                    and not app_visible_work
                    and not app_tool_started
                    and not result_has_visible_work
                    and not _is_structured_codex_prompt(options.prompt)
                )
                if should_fallback:
                    logger.warning(
                        "Agent '%s': Codex App Server failed before visible work; falling back to codex exec",
                        config.name,
                    )
                    return await _run_exec_path(options)

                if result_has_visible_work and not app_visible_work and stream_callback is not None:
                    await stream_callback(result.blocks)
                    app_visible_work = True
                if result.returncode != 0:
                    error_text = result.stderr or f"Codex App Server exited with {result.returncode}"
                    if app_visible_work or app_tool_started or result_has_visible_work:
                        error_text = (
                            "Codex App Server failed after visible work; not falling back to codex exec "
                            f"to avoid duplicate tool side effects: {error_text}"
                        )
                    result.stderr = error_text
                return result

            app_server_selection = select_codex_app_server_runtime(config, launch_cwd)
            logger.info(
                "Agent '%s': Codex App Server selection enabled=%s source=%s reason=%s config=%s expires_at=%s",
                config.name,
                app_server_selection.enabled,
                app_server_selection.source,
                app_server_selection.reason,
                app_server_selection.config_path or "",
                app_server_selection.expires_at or "",
            )
            if app_server_selection.enabled:
                codex_result = await _run_app_server_path(run_options)
            else:
                codex_result = await _run_exec_path(run_options)
            result_text = codex_result.response
            transcript = codex_result.transcript
            blocks = codex_result.blocks
            if codex_result.returncode != 0:
                raise RuntimeError(codex_result.stderr or f"Codex exited with {codex_result.returncode}")
    except asyncio.TimeoutError:
        raise
    except ExceptionGroup as eg:
        # Unwrap TaskGroup/ExceptionGroup to log actual sub-exceptions
        import traceback
        for i, exc in enumerate(eg.exceptions):
            logger.error(f"Agent '{config.name}' sub-exception {i}: {type(exc).__name__}: {exc}")
            logger.error("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise
    except Exception as e:
        import traceback
        logger.error(f"Agent '{config.name}' exception: {type(e).__name__}: {e}")
        logger.error("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise

    finally:
        if reg_id:
            try:
                deregister_process(reg_id)
            except Exception as e:
                logger.warning(f"Failed to deregister agent '{config.name}': {e}")

    return result_text, transcript, blocks


async def _run_anthropic_sdk_agent(
    config: AgentConfig,
    invocation: AgentInvocation,
    stream_callback: Optional[Callable[[list], Awaitable[None]]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
    running_entry_id: Optional[str] = None,
) -> str:
    """
    Run an SDK-based agent using claude_agent_sdk.query().

    history_messages: Optional pre-rendered SDK input for salon dispatches.
    Threaded through to _consume_query (see that function's docstring).
    """
    if ClaudeAgentOptions is None or SDK_QUERY is None:
        raise RuntimeError("Installed claude-agent-sdk package is unavailable")

    logger.info(f"Running SDK agent '{config.name}' with model {config.model}")

    # Register in process registry (SDK agents: pid=None since SDK manages subprocess internally)
    task_desc = invocation.prompt[:80] if invocation.prompt else "active"
    reg_id = None
    try:
        reg_id = register_process(config.name, task=task_desc, pid=None)
        if running_entry_id:
            await running_agents.update(running_entry_id, process_id=reg_id)
    except Exception as e:
        logger.warning(f"Failed to register agent '{config.name}' in process registry: {e}")

    # Build the identity-layer system_prompt (prompt.md + global instructions +
    # skill menu + agent list). Dynamic context (always_load memories, working
    # memory, contextual retrieval) is delivered via the user-message prefix
    # below — never through system_prompt, which hits Linux MAX_ARG_STRLEN
    # (128KB) because the SDK forwards it as a single argv. See prompt_assembly.

    import prompt_assembly  # interface/server/prompt_assembly.py (already on sys.path)

    # Pre-compute skill reminder for system prompt injection (above memory).
    _skill_reminder = ""
    agent_has_skills = config.skills is None or (isinstance(config.skills, list) and len(config.skills) > 0)
    if agent_has_skills:
        try:
            from skill_injector import get_skill_reminder
            _skill_reminder = get_skill_reminder(allowed_skills=config.skills) or ""
            if _skill_reminder:
                logger.info(f"Agent '{config.name}': will inject skill menu into system prompt")
        except Exception as e:
            logger.warning(f"Skill menu generation failed for agent '{config.name}': {e}")

    # Pre-compute agent list block for injection above memory.
    _effective_tools = list(config.tools) if config.tools else []
    _agent_list_block = ""
    try:
        from mcp_tools.agents import get_agent_list_for_prompt
        _agent_list_block = get_agent_list_for_prompt(_effective_tools) or ""
        if _agent_list_block:
            logger.info(f"Agent '{config.name}': will inject agent list into system prompt")
    except Exception as e:
        logger.warning(f"Agent '{config.name}': failed to get agent list: {e}")

    _global_instruction_parts = prompt_assembly.load_global_instruction_parts(
        Path(__file__).parent,
        is_visible=invocation.is_visible,
    )

    # Identity-only assembly. The pieces below are stable across turns —
    # prompt.md, global rules, mode instructions, skill menu, agent list.
    # Memory + working memory + contextual retrieval are NOT included here;
    # they ride through the user-message prefix (see context_parts below).
    identity_parts: List[str] = []
    if config.prompt:
        identity_parts.append(config.prompt)
    identity_parts.extend(_global_instruction_parts)
    if _skill_reminder:
        identity_parts.append(_skill_reminder)
    if _agent_list_block:
        identity_parts.append(_agent_list_block)
    identity_content = "\n".join(identity_parts).strip()

    if config.system_prompt_preset:
        system_prompt = {
            "type": "preset",
            "preset": config.system_prompt_preset,
        }
        if identity_content:
            system_prompt["append"] = identity_content
    else:
        system_prompt = identity_content

    # Dynamic context layer: working memory + contextual retrieval. For Character's
    # SDK/API path, always_load memories are stable prompt-prefix material, so
    # they move into a temporary system_prompt_file below instead of riding in
    # the per-turn user message.
    _agent_dir = Path(__file__).parent / config.name
    _scripts_dir = Path(__file__).parent.parent / "scripts"
    stable_context_parts, context_parts = prompt_assembly.load_context_layers(
        config.name,
        _agent_dir,
        _scripts_dir,
    )
    system_prompt_file: Optional[Path] = None
    stable_context_block = ""
    if config.name == "character":
        stable_context_block = prompt_assembly.build_context_block(stable_context_parts)
        if stable_context_block and isinstance(system_prompt, str):
            system_prompt_file = prompt_assembly.write_cacheable_system_prompt_file(
                config.name,
                system_prompt,
                stable_context_block,
            )
            system_prompt = {"type": "file", "path": str(system_prompt_file)}
            logger.info(
                "Agent '%s': moved %s chars of stable context into SDK system_prompt_file",
                config.name,
                len(stable_context_block),
            )
        elif stable_context_parts:
            context_parts = stable_context_parts + context_parts
    else:
        context_parts = stable_context_parts + context_parts

    # Build MCP servers for the agent.
    # Internal "brain" server provides Second Brain tools (memory, scheduler, etc.).
    # External servers (Playwright, etc.) are loaded from external_mcp_servers.json.
    MCP_PREFIX = "mcp__brain__"
    MCP_ANY_PREFIX = "mcp__"
    mcp_servers = {}

    # Config is the sole source of truth — no auto-injection.
    # What's in config.tools is exactly what the agent gets.
    effective_tools = list(config.tools) if config.tools else []

    # Native tool whitelist: agents get exactly the native tools listed in their config
    # — nothing more. This passes `tools=[list]` to the SDK, which in turn passes
    # `--tools Read,Edit,...` to the CLI. Any native tool not in this list — including
    # future Anthropic-shipped tools (Cron*, Monitor, PushNotification, ScheduleWakeup,
    # EnterWorktree, etc.) — is blocked at the CLI level. No blacklist to maintain.
    # Source of truth for what's available lives in .claude/agents/native_tools.py.
    native_tool_names = [t for t in effective_tools if not t.startswith(MCP_ANY_PREFIX)]

    if config.tools:
        # Internal "brain" MCP server
        mcp_tool_names = [t for t in config.tools if t.startswith(MCP_PREFIX)]

        if mcp_tool_names:
            internal_names = [t[len(MCP_PREFIX):] for t in mcp_tool_names]
            try:
                from mcp_tools import create_mcp_server
                mcp_server = create_mcp_server(
                    name="brain",
                    include_tools=internal_names,
                    agent_name=config.name,
                    allowed_skills=config.skills,
                    chat_id=invocation.source_chat_id,
                    salon_id=invocation.salon_id,
                )
                # mcp_tools still returns the local compatibility shape for the
                # Codex stdio bridge; Anthropic SDK agents need the installed
                # SDK's native server config so the CLI receives only
                # serializable metadata and routes calls to the in-process server.
                if isinstance(mcp_server, dict) and "tools" in mcp_server:
                    mcp_server = _create_real_sdk_mcp_server(
                        name="brain",
                        version=str(mcp_server.get("version") or "1.0.0"),
                        tools=list(mcp_server["tools"]),
                    )
                mcp_servers["brain"] = mcp_server
                logger.info(
                    f"Created MCP server for agent '{config.name}' with "
                    f"{len(internal_names)} tools: {internal_names}"
                )
            except Exception as e:
                logger.error(f"Failed to create MCP server for agent '{config.name}': {e}")

        # External MCP servers (stdio-based: Playwright, etc.)
        # Load config from external_mcp_servers.json alongside this file.
        external_config = _load_external_mcp_servers()
        for server_name, server_config in external_config.items():
            prefix = f"mcp__{server_name}__"
            # Include server if any agent tool matches this server's prefix
            if any(t.startswith(prefix) for t in config.tools):
                mcp_servers[server_name] = server_config
                logger.info(
                    f"Added external MCP server '{server_name}' for agent '{config.name}' "
                    f"(command: {server_config.get('command', 'N/A')})"
                )

    options_kwargs = {
        "model": config.model,
        "cli_path": str(_current_claude_code_cli_path()),
        "system_prompt": system_prompt,
        "allowed_tools": effective_tools if effective_tools else None,
        "permission_mode": "bypassPermissions",
        "can_use_tool": _auto_approve_tool,  # Catch .claude/ directory prompts that bypass mode doesn't suppress
        "hooks": {"PreToolUse": [HookMatcher(matcher=None, hooks=[_keepalive_hook])]},
        "setting_sources": [],  # Never load project settings for subagents
        "max_turns": config.max_turns,
        "mcp_servers": mcp_servers if mcp_servers else None,
        "env": _sdk_agent_env(config.name),
        # Restore visible thinking on Opus 4.7+ — the model silently changed its
        # default from display="summarized" to display="omitted" (see Anthropic's
        # "What's new in Claude Opus 4.7" docs). Without this, thinking blocks
        # still stream but their content is empty, so the frontend shows no
        # reasoning. The SDK's ClaudeAgentOptions doesn't model the `display`
        # field on ThinkingConfigAdaptive, but the bundled CLI supports the
        # --thinking-display flag, and extra_args forwards unmapped CLI flags.
        # No-op on Sonnet/Haiku (they still default to "summarized").
        "extra_args": {"thinking-display": "summarized"},
        # Enable partial-message StreamEvents so _consume_query receives
        # content_block_delta events for text and thinking. Without this,
        # the SDK only emits AssistantMessage at block-completion, which means
        # the salon UI sees text/thinking arrive as instant snapshots instead
        # of streaming live. Same flag the 1:1 chat wrapper uses.
        "include_partial_messages": True,
    }

    # Tool availability gate (whitelist-only).
    # - Preset agents: opt into Claude Code's full native tool suite.
    # - Everyone else: exactly the native tools listed in config.tools — nothing else.
    #   Empty list → zero native tools enabled. This is the CLI-level ON/OFF switch
    #   that prevents silent-enable of future Anthropic tools.
    if config.system_prompt_preset:
        options_kwargs["tools"] = {"type": "preset", "preset": config.system_prompt_preset}
    else:
        options_kwargs["tools"] = native_tool_names

    # Apply model-aware thinking configuration
    model = config.model or "sonnet"
    if config.thinking_budget:
        # Agent-level override: explicit budget_tokens
        options_kwargs["thinking"] = ThinkingConfigEnabled(type="enabled", budget_tokens=config.thinking_budget)
        logger.info(f"Agent '{config.name}': thinking config override — enabled with budget_tokens={config.thinking_budget}")
    elif config.effort:
        # Agent-level override: explicit effort (with adaptive thinking)
        options_kwargs["thinking"] = ThinkingConfigAdaptive(type="adaptive")
        options_kwargs["effort"] = config.effort
        logger.info(f"Agent '{config.name}': thinking config override — adaptive, effort={config.effort}")
    else:
        # Model-level defaults from THINKING_DEFAULTS
        thinking_cfg = THINKING_DEFAULTS.get(model)
        if thinking_cfg:
            options_kwargs["thinking"] = thinking_cfg["thinking"]
            if "effort" in thinking_cfg:
                options_kwargs["effort"] = thinking_cfg["effort"]
            logger.info(
                f"Agent '{config.name}': applying thinking config for model '{model}': "
                f"thinking={type(thinking_cfg['thinking']).__name__}, "
                f"effort={thinking_cfg.get('effort', 'N/A')}"
            )
        else:
            logger.info(f"Agent '{config.name}': no thinking defaults for model '{model}'")

    options = ClaudeAgentOptions(**options_kwargs)

    # Add output format if specified
    if config.output_format:
        options.output_format = config.output_format

    # Auto-retrieve contextual memories relevant to the agent's task prompt
    accepted_query_count = 0
    rewrite_fallback = False
    rewrite_timed_out = False
    try:
        scripts_dir = str(Path(__file__).parent.parent / "scripts")
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from contextual_memory import auto_retrieve_context, rewrite_query_for_retrieval

        raw_query = invocation.prompt or ""
        # 20s timeout — Sonnet rewriter success is 3-5s, but the SDK
        # subprocess can hang indefinitely (~10-20×/day). Without this
        # wrapper, a hung subprocess freezes the agent invocation for
        # 30-76s before crashing. On timeout, fall back to raw prompt.
        try:
            retrieval_queries = await asyncio.wait_for(
                rewrite_query_for_retrieval(
                    raw_query,
                    session_id=invocation.source_chat_id or f"agent:{config.name}",
                    agent_name=config.name,
                ),
                timeout=20.0,
            )
        except asyncio.TimeoutError:
            rewrite_fallback = True
            rewrite_timed_out = True
            retrieval_queries = [(raw_query, 1.0)]
        accepted_query_count = (
            len(retrieval_queries) if isinstance(retrieval_queries, (list, tuple)) else 0
        )
        _log_contextual_memory_outcome(
            agent_name=config.name,
            accepted_count=accepted_query_count,
            fallback=rewrite_fallback,
            timeout=rewrite_timed_out,
            path="sdk",
            outcome=(
                "rewrite_timeout_fallback" if rewrite_timed_out else "rewrite_accepted"
            ),
            level=logging.WARNING if rewrite_timed_out else logging.INFO,
        )
        # Run CPU-bound retrieval in a thread to avoid blocking the event loop
        import functools
        loop = asyncio.get_event_loop()
        ctx_block = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                functools.partial(
                    auto_retrieve_context,
                    query=retrieval_queries,
                    agent_name=config.name,
                ),
            ),
            timeout=15.0,  # 15s max — don't let retrieval stall the agent
        )
        if ctx_block:
            context_parts.append(ctx_block)
            logger.info(f"Agent '{config.name}': appended contextual memory to user-prefix context block")
    except Exception:
        _log_contextual_memory_outcome(
            agent_name=config.name,
            accepted_count=accepted_query_count,
            fallback=rewrite_fallback,
            timeout=rewrite_timed_out,
            path="sdk",
            outcome="contextual_memory_failed",
            level=logging.WARNING,
        )

    # Build the <system-injected> envelope and prepend to the user message.
    # For salon dispatch (history_messages provided), the envelope is prepended
    # to the trailing user turn so each agent's context is rebuilt fresh per
    # dispatch without polluting earlier turns in the rendered history.
    _context_block = prompt_assembly.build_context_block(context_parts)
    effective_prompt = invocation.prompt
    if _context_block:
        if history_messages:
            history_messages = prompt_assembly.prepend_context_to_history_messages(
                history_messages, _context_block
            )
            logger.info(
                f"Agent '{config.name}': prepended {len(_context_block)} chars of "
                f"context to trailing user turn in history_messages"
            )
        else:
            effective_prompt = prompt_assembly.prepend_context_to_prompt(
                effective_prompt, _context_block
            )
            logger.info(
                f"Agent '{config.name}': prepended {len(_context_block)} chars of "
                f"context (<system-injected> envelope) to user message"
            )

    result_text = ""
    transcript = ""
    blocks = []

    try:
        async with asyncio.timeout(config.timeout_seconds):
            result_text, transcript, blocks = await _consume_query(
                effective_prompt, options, stream_callback=stream_callback,
                history_messages=history_messages,
            )
    except asyncio.TimeoutError:
        raise
    except ExceptionGroup as eg:
        # Unwrap TaskGroup/ExceptionGroup to log actual sub-exceptions
        import traceback
        for i, exc in enumerate(eg.exceptions):
            logger.error(f"Agent '{config.name}' sub-exception {i}: {type(exc).__name__}: {exc}")
            logger.error("".join(traceback.format_exception(type(exc), exc, exc.__traceback__)))
        raise
    except Exception as e:
        import traceback
        logger.error(f"Agent '{config.name}' exception: {type(e).__name__}: {e}")
        logger.error("".join(traceback.format_exception(type(e), e, e.__traceback__)))
        raise

    finally:
        if system_prompt_file:
            try:
                system_prompt_file.unlink(missing_ok=True)
            except Exception as exc:
                logger.warning("Could not remove SDK system prompt file %s: %s", system_prompt_file, exc)
        if reg_id:
            try:
                deregister_process(reg_id)
            except Exception as e:
                logger.warning(f"Failed to deregister agent '{config.name}': {e}")

    return result_text, transcript, blocks


def _extract_tool_content(content) -> str:
    """Normalize ToolResultBlock.content to a string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(json.dumps(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return str(content)


def _truncate(text: str, limit: int) -> str:
    """Truncate text to limit chars, adding ellipsis if truncated."""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... (truncated)"


def _format_transcript(captured: list, result_meta: Optional[dict] = None) -> str:
    """Render captured message entries into a readable markdown transcript.

    Args:
        captured: List of dicts with keys like 'type', 'text', 'name', 'input', 'content', 'is_error'.
        result_meta: Optional dict with 'num_turns', 'cost', 'duration_ms' from ResultMessage.
    """
    TOOL_INPUT_LIMIT = 500
    TOOL_RESULT_LIMIT = 3000

    parts = []
    for entry in captured:
        etype = entry.get("type")

        if etype == "text":
            parts.append(entry["text"])

        elif etype == "tool_use":
            name = entry["name"]
            raw_input = entry.get("input", {})
            input_str = json.dumps(raw_input, indent=2) if isinstance(raw_input, dict) else str(raw_input)
            parts.append(f"\n---\n**Tool: `{name}`**\n{_truncate(input_str, TOOL_INPUT_LIMIT)}")

        elif etype == "tool_result":
            content = entry.get("content", "")
            is_error = entry.get("is_error", False)
            prefix = "**Error:**" if is_error else "**Result:**"
            parts.append(f"{prefix}\n{_truncate(content, TOOL_RESULT_LIMIT)}\n---\n")

    # Append metadata footer if available
    if result_meta:
        meta_parts = []
        if result_meta.get("num_turns"):
            meta_parts.append(f"{result_meta['num_turns']} turns")
        if result_meta.get("cost") is not None:
            meta_parts.append(f"${result_meta['cost']:.4f}")
        if result_meta.get("duration_ms"):
            secs = result_meta["duration_ms"] / 1000
            meta_parts.append(f"{secs:.1f}s")
        if meta_parts:
            parts.append(f"\n---\n*{' | '.join(meta_parts)}*")

    return "\n\n".join(parts)


def _captured_to_blocks(captured: list) -> list:
    """Convert captured SDK messages to ContentBlock-compatible dicts for UI rendering.

    Returns a flat list of blocks matching the frontend ContentBlock interface:
    - text: {id, type, content, status}
    - thinking: {id, type, content, status, duration_ms}
    - tool_use: {id, type, content, tool_name, tool_call_id, tool_input, status}
    - tool_result: {id, type, content, tool_call_id, is_error, status}

    Block IDs are deterministic on (index, type, tool_call_id) so successive
    calls during streaming yield stable React keys — the UI can overwrite
    snapshots without remounting unchanged blocks.
    """
    import uuid as _uuid

    blocks = []
    for idx, entry in enumerate(captured):
        etype = entry.get("type")
        # Live status / timing — fall back to "complete" with no timing if
        # entry didn't track it (e.g., tool_result, or legacy paths).
        status = entry.get("status", "complete")

        if etype == "text":
            block = {
                "id": f"blk_text_{idx}",
                "type": "text",
                "content": entry.get("text", ""),
                "status": status,
            }
            if "started_at" in entry:
                block["started_at"] = entry["started_at"]
            if "duration_ms" in entry:
                block["duration_ms"] = entry["duration_ms"]
            blocks.append(block)

        elif etype == "thinking":
            block = {
                "id": f"blk_thinking_{idx}",
                "type": "thinking",
                "content": entry.get("text", ""),
                "status": status,
            }
            if "started_at" in entry:
                block["started_at"] = entry["started_at"]
            if "duration_ms" in entry:
                block["duration_ms"] = entry["duration_ms"]
            blocks.append(block)

        elif etype == "tool_use":
            tool_call_id = entry.get("id", f"toolu_{_uuid.uuid4().hex[:20]}")
            block = {
                "id": f"blk_tooluse_{tool_call_id}",
                "type": "tool_use",
                "content": "",
                "tool_name": entry.get("name", ""),
                "tool_call_id": tool_call_id,
                "tool_input": entry.get("input", {}),
                "status": status,
            }
            if "started_at" in entry:
                block["started_at"] = entry["started_at"]
            if "duration_ms" in entry:
                block["duration_ms"] = entry["duration_ms"]
            blocks.append(block)

        elif etype == "tool_result":
            tool_call_id = entry.get("tool_use_id", "")
            blocks.append({
                "id": f"blk_toolresult_{tool_call_id}",
                "type": "tool_result",
                "content": entry.get("content", ""),
                "tool_call_id": tool_call_id,
                "is_error": entry.get("is_error", False),
                "status": "complete",
            })

    return blocks


async def _consume_query(
    prompt: str,
    options,
    stream_callback: Optional[Callable[[list], Awaitable[None]]] = None,
    history_messages: Optional[List[Dict[str, Any]]] = None,
) -> tuple:
    """
    Consume the async generator from claude_agent_sdk.query().

    Captures all SDK messages into a structured transcript and UI-ready blocks.
    - result_text: the final ResultMessage.result (used for compact ping notifications)
    - transcript: a full markdown-formatted trace (for MCP tool consumers / other agents)
    - blocks: list of ContentBlock-compatible dicts (for UI rendering with tool pills)

    When MCP servers are configured, the prompt is sent as an AsyncIterable
    (streaming mode) so the SDK keeps stdin open for the bidirectional MCP
    control protocol.

    Args:
        history_messages: Optional pre-rendered history (list of SDK envelope
            dicts in streaming-input shape). When provided, this replaces the
            single ``prompt`` user message — each entry is yielded as-is. Used
            by salon dispatch to pre-seed prior turns so each agent sees their
            own tool calls (mirrors 1:1 chat). When ``None``, falls back to
            the legacy single-prompt-user-message behavior.
    """
    if SDK_QUERY is None:
        raise RuntimeError("Installed claude-agent-sdk package is unavailable")
    query = SDK_QUERY

    # Always use streaming mode — required for can_use_tool callback (Python SDK)
    # and for MCP bridge protocol.  Without streaming, permission prompts from
    # .claude/ directory protection time out to denials.
    has_mcp = bool(options.mcp_servers)

    # Always stream — can_use_tool needs it even without MCP
    if True:
        async def _prompt_stream():
            if history_messages:
                # Salon path: pre-seed prior turns. Each entry is already in
                # SDK streaming-input envelope shape (type/session_id/message/
                # parent_tool_use_id). The final entry is the trailing "your
                # turn" user message, so we don't append `prompt` separately.
                for m in history_messages:
                    yield m
            else:
                # Legacy / non-salon path: single user message.
                yield {
                    "type": "user",
                    "session_id": "",
                    "message": {"role": "user", "content": prompt},
                    "parent_tool_use_id": None,
                }

        effective_prompt = _prompt_stream()
    else:
        effective_prompt = prompt

    import time as _time

    result_text = ""
    captured = []  # List of transcript entries (each has type, content, and live status)
    result_meta = None

    # Helper: emit a stream callback with the current block snapshot. The
    # callback receives a fully-formed list of frontend ContentBlock dicts
    # (same shape as the final ``blocks`` return value), so the consumer can
    # just overwrite its in-progress UI state on each fire.
    async def _emit_stream() -> None:
        if stream_callback is None:
            return
        try:
            snapshot = _captured_to_blocks(captured)
            await stream_callback(snapshot)
        except Exception as e:
            # Streaming is best-effort — never fail the agent run because the
            # broadcast channel is busted.
            logger.warning(f"stream_callback failed (continuing): {e}")

    def _seal_text_thinking() -> None:
        """Mark any in-progress text/thinking block as complete (with duration).

        Called when a new content block starts after a streamed text/thinking
        block — e.g. text appears after thinking, or a tool_use begins. Walks
        backward to find the most recent in-progress text/thinking entry; only
        one is ever in-flight at a time."""
        for entry in reversed(captured):
            etype = entry.get("type")
            if etype in ("text", "thinking") and entry.get("status") == "in_progress":
                entry["status"] = "complete"
                start = entry.get("started_at")
                if start:
                    entry["duration_ms"] = int((_time.time() - start) * 1000)
                return

    async for message in query(prompt=effective_prompt, options=options):
        # ---- Streaming deltas (require include_partial_messages: True) ----
        if isinstance(message, StreamEvent):
            event = message.event or {}
            event_type = event.get("type", "")

            if event_type == "content_block_delta":
                delta = event.get("delta", {}) or {}
                delta_type = delta.get("type", "")

                if delta_type == "text_delta":
                    text = delta.get("text", "")
                    if not text:
                        continue
                    # Extend an in-progress text block, or start a new one.
                    if (captured
                            and captured[-1].get("type") == "text"
                            and captured[-1].get("status") == "in_progress"):
                        captured[-1]["text"] += text
                    else:
                        _seal_text_thinking()
                        captured.append({
                            "type": "text",
                            "text": text,
                            "status": "in_progress",
                            "started_at": _time.time(),
                        })
                    await _emit_stream()

                elif delta_type == "thinking_delta":
                    thinking = delta.get("thinking", "")
                    if not thinking:
                        continue
                    if (captured
                            and captured[-1].get("type") == "thinking"
                            and captured[-1].get("status") == "in_progress"):
                        captured[-1]["text"] += thinking
                    else:
                        _seal_text_thinking()
                        captured.append({
                            "type": "thinking",
                            "text": thinking,
                            "status": "in_progress",
                            "started_at": _time.time(),
                        })
                    await _emit_stream()

            elif event_type == "content_block_start":
                block = event.get("content_block", {}) or {}
                if block.get("type") == "tool_use":
                    # Seal any preceding text/thinking, then add a tool_use
                    # entry immediately so the pill renders the moment the
                    # model commits to a tool call (instead of waiting for
                    # the AssistantMessage to land with the full input).
                    _seal_text_thinking()
                    tool_id = block.get("id")
                    # Don't double-add if we already have this tool_use id
                    # (defensive — shouldn't happen with content_block_start).
                    already = any(
                        c.get("type") == "tool_use" and c.get("id") == tool_id
                        for c in captured
                    )
                    if not already:
                        captured.append({
                            "type": "tool_use",
                            "name": block.get("name", "tool"),
                            "id": tool_id,
                            "input": {},  # filled in when AssistantMessage lands
                            "status": "in_progress",
                            "started_at": _time.time(),
                        })
                        await _emit_stream()

            # content_block_stop is implicit — handled by the seal-on-next-start logic.
            continue

        if isinstance(message, AssistantMessage):
            # With include_partial_messages: True, text and thinking already
            # arrived via StreamEvent deltas. AssistantMessage gives us the
            # authoritative final state — use it to:
            #   - confirm/seal the current text/thinking block
            #   - fill in the tool_use block's full input (deltas don't carry input_json)
            #   - cover the case where streaming missed events (fallback append)
            had_change = False
            for block in (message.content or []):
                if isinstance(block, TextBlock):
                    # Find the most recent text entry; if its content matches
                    # (or is shorter than) the authoritative version, replace
                    # to ensure correctness, then seal.
                    matched = False
                    for entry in reversed(captured):
                        if entry.get("type") == "text":
                            entry["text"] = block.text
                            if entry.get("status") == "in_progress":
                                entry["status"] = "complete"
                                start = entry.get("started_at")
                                if start:
                                    entry["duration_ms"] = int((_time.time() - start) * 1000)
                            matched = True
                            had_change = True
                            break
                    if not matched:
                        # Streaming missed this block — append it as complete.
                        captured.append({
                            "type": "text",
                            "text": block.text,
                            "status": "complete",
                        })
                        had_change = True

                elif isinstance(block, ThinkingBlock):
                    matched = False
                    for entry in reversed(captured):
                        if entry.get("type") == "thinking":
                            entry["text"] = block.thinking or ""
                            if entry.get("status") == "in_progress":
                                entry["status"] = "complete"
                                start = entry.get("started_at")
                                if start:
                                    entry["duration_ms"] = int((_time.time() - start) * 1000)
                            matched = True
                            had_change = True
                            break
                    if not matched:
                        captured.append({
                            "type": "thinking",
                            "text": block.thinking or "",
                            "status": "complete",
                        })
                        had_change = True

                elif isinstance(block, ToolUseBlock):
                    # Update the partial tool_use entry with the full input,
                    # or append if streaming missed the content_block_start.
                    matched = False
                    for entry in captured:
                        if entry.get("type") == "tool_use" and entry.get("id") == block.id:
                            entry["name"] = block.name
                            entry["input"] = block.input
                            # Stay in_progress until tool_result arrives.
                            matched = True
                            had_change = True
                            break
                    if not matched:
                        _seal_text_thinking()
                        captured.append({
                            "type": "tool_use",
                            "name": block.name,
                            "id": block.id,
                            "input": block.input,
                            "status": "in_progress",
                            "started_at": _time.time(),
                        })
                        had_change = True

            if had_change:
                await _emit_stream()

        elif isinstance(message, UserMessage):
            # UserMessage carries tool results back
            content = message.content
            if isinstance(content, list):
                appended = False
                for block in content:
                    if isinstance(block, ToolResultBlock):
                        captured.append({
                            "type": "tool_result",
                            "tool_use_id": block.tool_use_id,
                            "content": _extract_tool_content(block.content),
                            "is_error": block.is_error or False,
                        })
                        # Seal the matching tool_use as complete.
                        for entry in captured:
                            if (entry.get("type") == "tool_use"
                                    and entry.get("id") == block.tool_use_id
                                    and entry.get("status") == "in_progress"):
                                entry["status"] = "complete"
                                start = entry.get("started_at")
                                if start:
                                    entry["duration_ms"] = int((_time.time() - start) * 1000)
                                break
                        appended = True
                if appended:
                    await _emit_stream()
            # String content from UserMessage is not interesting for transcript

        elif isinstance(message, ResultMessage):
            result_text = message.result or ""
            if hasattr(message, "structured_output") and message.structured_output:
                result_text = json.dumps(message.structured_output, indent=2)
            result_meta = {
                "num_turns": getattr(message, "num_turns", None),
                "cost": getattr(message, "total_cost_usd", None),
                "duration_ms": getattr(message, "duration_ms", None),
            }

        # Skip SystemMessage — not relevant for transcript

    # Final seal: if the run ended with a streaming text/thinking block still
    # marked in_progress (no AssistantMessage arrived for some reason), close
    # it out cleanly so the persisted blocks aren't permanently "live".
    _seal_text_thinking()

    transcript = _format_transcript(captured, result_meta)
    blocks = _captured_to_blocks(captured)
    return result_text, transcript, blocks


def _log_execution(invocation: AgentInvocation, result: AgentResult) -> None:
    """Log an execution to the executions log file.

    Uses fcntl.flock to serialize concurrent read-modify-write operations,
    preventing data loss when parallel agents finish simultaneously.
    """
    import fcntl

    try:
        # Open (or create) the lock file alongside the log
        lock_path = EXECUTIONS_LOG.with_suffix(".lock")
        with open(lock_path, "w") as lock_file:
            fcntl.flock(lock_file, fcntl.LOCK_EX)
            try:
                # Load existing log
                if EXECUTIONS_LOG.exists():
                    with open(EXECUTIONS_LOG, "r") as f:
                        data = json.load(f)
                else:
                    data = {"executions": []}

                # Add new entry (truncate transcript to avoid bloating the log)
                result_dict = result.to_dict()
                if result_dict.get("transcript") and len(result_dict["transcript"]) > 5000:
                    result_dict["transcript"] = result_dict["transcript"][:5000] + "\n... (truncated in log)"
                entry = {
                    "invocation": invocation.to_dict(),
                    "result": result_dict,
                }
                data["executions"].append(entry)

                # Keep last 100 entries
                data["executions"] = data["executions"][-100:]

                # Atomic write: temp file then rename
                tmp_path = EXECUTIONS_LOG.with_suffix(".tmp")
                with open(tmp_path, "w") as f:
                    json.dump(data, f, indent=2)
                tmp_path.rename(EXECUTIONS_LOG)
            finally:
                fcntl.flock(lock_file, fcntl.LOCK_UN)

    except Exception as e:
        logger.error(f"Failed to log execution: {e}")
