"""
Conversation Compaction tool.

Three modes for reclaiming context window budget in long conversations:

1. "truncate_tools" (default) — Mechanically truncate tool call outputs & args
   for messages older than `keep_exchanges`. All user/assistant dialogue is
   preserved verbatim. Fast, no external LLM call. This is usually what you want
   because tool outputs are almost always the bulk of context bloat.

2. "strip_tools" — More aggressive: replace tool outputs with "[output stripped]"
   entirely, keeping only the tool name + a short hint of the args. All
   user/assistant dialogue preserved verbatim. Fast, no external LLM call.

3. "summarize" — Original behavior: an Opus subagent produces a dense narrative
   summary of older messages. Semantic and slower (~10-30s), but
   preserves meaning across long-ago dialogue. Use when there's lots of OLD
   dialogue that needs collapsing (not just tool bloat).

Salon mode (group chats):
  When called from inside a salon dispatch, the tool routes to a per-agent
  compaction path that ONLY mutates the calling agent's own tool blocks
  (truncate/strip), or — for `summarize` — produces a single shared summary
  message that REPLACES verbatim history for all participants. The boundary
  for "older" is anchored to the calling agent's Nth-from-last message: every
  message from that point forward (including other agents' replies between
  own ones) is preserved verbatim.
"""

import json
import os
import sys
import uuid
import logging
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.compact_conversation")

# Compaction subagent system prompt
COMPACTION_SYSTEM_PROMPT = """You are a conversation compactor. Produce a thorough, comprehensive summary of a conversation between the user (user) and Claude (assistant).

Your job is to preserve EVERYTHING of substance. When in doubt, INCLUDE it. Lost context is unrecoverable — err heavily on the side of completeness.

Preserve ALL of the following:
1. EVERY decision made and its full rationale (why X was chosen over Y)
2. ALL facts established — numbers, names, dates, file paths, URLs, preferences, versions, config values
3. ALL action items — what was committed to, by whom, and any conditions/caveats
4. ALL tool results that produced meaningful output — commands run, their output, API responses, errors encountered, file contents read
5. CURRENT STATE — what we're in the middle of, what's pending, what's been tried and failed, what's queued next
6. TECHNICAL DETAILS — code snippets discussed, architecture decisions, specific implementations, variable names, function signatures
7. USER PREFERENCES and opinions expressed — how the user wants things done, what he liked/disliked
8. DEBUGGING HISTORY — what was investigated, what was ruled out, what the root cause was
9. CONTEXT that would be needed to seamlessly continue the conversation — anything where losing it would force re-asking or re-investigating

Do NOT include: pleasantries, greetings, social filler, redundant restatements of the same point.
Write in narrative past tense. Use sections if the conversation spanned multiple distinct topics. Be thorough — aim for 20-40% of the original length. A longer summary that preserves everything is far better than a short one that loses context.

Begin the summary with: === Compacted History ===
End with: === End Compacted History ==="""


COMPACTION_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "The complete compacted conversation history. It must begin with "
                "=== Compacted History === and end with === End Compacted History ===."
            ),
        },
    },
    "required": ["summary"],
}

MIN_SUMMARY_CHARS = 80
SUMMARY_LENGTH_RATIO_FLOOR = 0.03
SUMMARY_LENGTH_FLOOR_CAP = 4000


class SummaryValidationError(ValueError):
    """Raised when summarize mode would otherwise write an unsafe summary."""


def _minimum_summary_chars(source_chars: int) -> int:
    if source_chars <= 0:
        return MIN_SUMMARY_CHARS
    return max(
        MIN_SUMMARY_CHARS,
        min(SUMMARY_LENGTH_FLOOR_CAP, int(source_chars * SUMMARY_LENGTH_RATIO_FLOOR)),
    )


def _validate_summary_text(summary: Any, source_text: str) -> str:
    if not isinstance(summary, str):
        raise SummaryValidationError("summary is not a string")

    text = summary.strip()
    if not text:
        raise SummaryValidationError("summary is empty")

    lowered = text.lower()
    if lowered in {"null", "none", "{}", "[]"}:
        raise SummaryValidationError("summary is placeholder output")

    if not text.startswith("=== Compacted History ==="):
        raise SummaryValidationError("summary missing start marker")
    if not text.endswith("=== End Compacted History ==="):
        raise SummaryValidationError("summary missing end marker")

    minimum = _minimum_summary_chars(len(source_text))
    if len(text) < minimum:
        raise SummaryValidationError(
            f"summary too short ({len(text)} chars, expected at least {minimum})"
        )

    return text


def _write_compaction_restore_backup(
    chat_manager: Any,
    session_id: str,
    backup_chat: Dict[str, Any],
) -> Optional[str]:
    """Write a restorable snapshot before destructive summarize compaction."""
    if not chat_manager or not hasattr(chat_manager, "get_chat_path"):
        return None

    source_path = Path(chat_manager.get_chat_path(session_id))
    backup_dir = source_path.parent / ".compact_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = backup_dir / f"{session_id}.{stamp}.summarize-restore.json"

    backup_path.write_text(
        json.dumps(backup_chat, indent=2, default=str),
        encoding="utf-8",
    )

    # Cheap restore validation: the backup must parse and preserve the exact
    # pre-compaction in-memory messages array before we permit the destructive
    # write to proceed.
    with backup_path.open("r", encoding="utf-8") as f:
        restored = json.load(f)
    original_messages = backup_chat.get("messages") or []
    restored_messages = restored.get("messages") or []
    if restored_messages != original_messages:
        try:
            backup_path.unlink()
        except OSError:
            pass
        raise RuntimeError("compaction restore backup validation failed")

    return str(backup_path)


def _count_exchanges(messages: List[Dict]) -> int:
    """Count exchanges (each user message = 1 exchange)."""
    return sum(1 for m in messages if m.get("role") == "user")


def _split_at_exchange_boundary(
    messages: List[Dict], keep_last_n: int = 5
) -> Tuple[List[Dict], List[Dict]]:
    """
    Split messages into (older, recent) preserving last N exchanges verbatim.

    An exchange = a user message + its associated assistant reply + any
    interleaved tool_call/system messages between them.

    Walks backwards counting user messages. The split point is placed just
    before the Nth user message from the end, so all associated tool_call
    and assistant messages for those exchanges stay in the recent portion.

    Special case: keep_last_n == 0 means compact EVERYTHING — even the current
    in-flight exchange's tool outputs. Returns (all_messages, []).
    """
    if keep_last_n <= 0:
        return list(messages), []

    user_count = 0
    split_index = 0  # Default: everything is "recent"

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count >= keep_last_n:
                split_index = i
                break

    return messages[:split_index], messages[split_index:]


DISPLAY_VISIBLE_ROLES = {"user", "assistant", "notice", "compacted"}


def _display_text_for_match(msg: Dict[str, Any]) -> str:
    """Extract user-visible text without treating block-based turns as empty."""
    content = msg.get("content")
    if isinstance(content, str) and content:
        return content

    block_text = []
    for block in msg.get("blocks") or []:
        if isinstance(block, dict) and block.get("type") == "text":
            text = block.get("content")
            if isinstance(text, str) and text:
                block_text.append(text)
    return "".join(block_text)


def _display_match_key(msg: Dict[str, Any]) -> Optional[Tuple[str, str]]:
    role = msg.get("role")
    if role not in DISPLAY_VISIBLE_ROLES and not msg.get("formData"):
        return None
    text = _display_text_for_match(msg).strip()
    if not text and not msg.get("formData"):
        return None
    return (role or "", text)


def _display_normalized_text(text: str) -> str:
    return " ".join(text.split())


def _display_block_represents_flat(display_msg: Dict[str, Any], flat_msg: Dict[str, Any]) -> bool:
    if not display_msg.get("blocks") or flat_msg.get("blocks"):
        return False
    if display_msg.get("role") != flat_msg.get("role"):
        return False
    if display_msg.get("id") and display_msg.get("id") == flat_msg.get("id"):
        return False

    flat_text = _display_normalized_text(_display_text_for_match(flat_msg).strip())
    display_text = _display_normalized_text(_display_text_for_match(display_msg).strip())
    return bool(flat_text and display_text and flat_text in display_text)


def _display_message_matches_flat(display_msg: Dict[str, Any], flat_msg: Dict[str, Any]) -> bool:
    display_id = display_msg.get("id")
    flat_id = flat_msg.get("id")
    if display_id and flat_id and display_id == flat_id:
        return True

    display_key = _display_match_key(display_msg)
    flat_key = _display_match_key(flat_msg)
    if display_key is not None and flat_key is not None and display_key == flat_key:
        return True

    return _display_block_represents_flat(display_msg, flat_msg)


def _build_summarized_display_messages(
    compacted_msg: Dict[str, Any],
    recent_messages: List[Dict[str, Any]],
    existing_display_messages: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build the UI projection for summarize mode from the new flat history.

    Summarize replaces older flat messages with one compacted summary. Any older
    display-only blocks are now semantically represented by that summary, so the
    only safe display projection is summary + display entries matching the kept
    recent flat messages.
    """
    display_messages: List[Dict[str, Any]] = [dict(compacted_msg)]
    used_ids: Set[str] = set()
    compacted_id = compacted_msg.get("id")
    if compacted_id:
        used_ids.add(compacted_id)

    for flat_msg in recent_messages:
        role = flat_msg.get("role")
        if role in {"tool_call", "system"}:
            continue
        if role not in DISPLAY_VISIBLE_ROLES and not flat_msg.get("formData"):
            continue

        chosen = None
        for display_msg in existing_display_messages:
            display_id = display_msg.get("id")
            if display_id and display_id in used_ids:
                continue
            if _display_message_matches_flat(display_msg, flat_msg):
                chosen = dict(display_msg)
                break

        if chosen is None:
            chosen = dict(flat_msg)

        chosen_id = chosen.get("id")
        if chosen_id:
            used_ids.add(chosen_id)
        display_messages.append(chosen)

    return display_messages


def _format_messages_for_summary(messages: List[Dict]) -> str:
    """Format messages into readable text for the compaction subagent."""
    parts = []
    for m in messages:
        role = m.get("role", "user")

        if role == "tool_call":
            # Reuse the existing compact format
            from tool_serializers import format_tool_for_history
            parts.append(format_tool_for_history(m))

        elif role == "compacted":
            # Previous compaction summary — include it for rolling compaction
            parts.append(m.get("content", ""))

        elif role == "system":
            content = m.get("content", "")
            if content:
                parts.append(f"System: {content}")

        elif role == "user":
            content = m.get("content", "")
            if content:
                parts.append(f"User: {content}")

        elif role == "assistant":
            content = m.get("content", "")
            if content:
                parts.append(f"Assistant: {content}")

    return "\n\n".join(parts)


async def _summarize_messages(messages: List[Dict]) -> str:
    """
    Run the compaction subagent to summarize older messages.

    Uses the configured Opus-equivalent Codex model for maximum comprehension and thoroughness.
    Returns a text summary.
    """
    from codex_backend import CodexRunOptions, ROOT_DIR, run_codex

    conversation_text = _format_messages_for_summary(messages)

    prompt = f"Summarize this conversation history:\n\n{conversation_text}"

    logger.info(
        f"Running compaction subagent on {len(messages)} messages "
        f"({len(conversation_text)} chars)"
    )

    try:
        result = await run_codex(
            CodexRunOptions(
                model="opus",
                cwd=str(ROOT_DIR),
                identity_instructions=COMPACTION_SYSTEM_PROMPT,
                prompt=prompt,
                tools=[],
                timeout_seconds=240,
                max_turns=1,
                output_schema=COMPACTION_OUTPUT_SCHEMA,
                ephemeral=True,
            )
        )
        structured = result.structured_output
        if not isinstance(structured, dict):
            raise SummaryValidationError("summarizer returned no structured output")

        result_text = _validate_summary_text(structured.get("summary"), conversation_text)
        logger.info(f"Compaction subagent produced {len(result_text)} char validated summary")
        return result_text

    except Exception as e:
        logger.error(f"Compaction subagent failed: {e}")
        raise


def _truncate_str(text: str, max_chars: int) -> str:
    """Simple truncation with ellipsis — no line-boundary cleverness."""
    if not text:
        return text or ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."


def _truncate_args(args: Dict[str, Any], max_chars: int) -> Dict[str, Any]:
    """Truncate each string value in an args dict to max_chars."""
    if not isinstance(args, dict):
        return args
    out = {}
    for k, v in args.items():
        if isinstance(v, str):
            out[k] = _truncate_str(v, max_chars)
        elif isinstance(v, (dict, list)):
            # Stringify and truncate — preserves readability without recursion bloat
            import json as _json
            try:
                s = _json.dumps(v, default=str)
            except Exception:
                s = str(v)
            out[k] = _truncate_str(s, max_chars)
        else:
            out[k] = v
    return out


def _apply_tool_shrink(
    messages: List[Dict], mode: str, truncate_chars: int, strip_args: bool
) -> Tuple[List[Dict], int, int]:
    """
    Walk messages and shrink tool_call entries in place (on a copy).

    Returns (new_messages, num_shrunk, bytes_saved).

    mode:
      - "truncate_tools": truncate output_summary to truncate_chars
      - "strip_tools": replace output_summary with "[output stripped]"
    """
    new_messages = []
    num_shrunk = 0
    bytes_saved = 0

    STRIP_PLACEHOLDER = "[output stripped]"
    ARG_CAP_STRIP = 80      # aggressive arg cap when stripping
    ARG_CAP_TRUNCATE = truncate_chars  # match output cap

    for m in messages:
        if m.get("role") != "tool_call":
            new_messages.append(m)
            continue

        # Skip ONLY if the message is already at-or-beyond the requested mode.
        # We allow re-shrinking when going to a more aggressive setting:
        #   - strip_tools always wins over truncate_tools
        #   - truncate_tools with smaller truncate_chars beats a previous truncate
        prev_mode = m.get("_compacted_mode")
        if prev_mode == "strip_tools":
            # Already maximally shrunk
            new_messages.append(m)
            continue
        if prev_mode == "truncate_tools" and mode == "truncate_tools":
            # Already truncated — only re-apply if the cap shrunk
            existing_len = len(m.get("output_summary", "") or "")
            if existing_len <= truncate_chars:
                new_messages.append(m)
                continue

        shrunk = dict(m)
        original_output = m.get("output_summary", "") or ""
        original_args_size = len(str(m.get("args", "")))

        if mode == "strip_tools":
            shrunk["output_summary"] = STRIP_PLACEHOLDER if original_output else ""
            if strip_args:
                shrunk["args"] = _truncate_args(m.get("args", {}), ARG_CAP_STRIP)
        else:  # truncate_tools
            shrunk["output_summary"] = _truncate_str(original_output, truncate_chars)
            if strip_args:
                shrunk["args"] = _truncate_args(m.get("args", {}), ARG_CAP_TRUNCATE)

        shrunk["_compacted_mode"] = mode
        shrunk["_compacted_at"] = datetime.now().isoformat()

        saved = (
            (len(original_output) - len(shrunk["output_summary"]))
            + (original_args_size - len(str(shrunk.get("args", ""))))
        )
        if saved > 0:
            num_shrunk += 1
            bytes_saved += saved

        new_messages.append(shrunk)

    return new_messages, num_shrunk, bytes_saved


async def run_compaction(
    *,
    session_id: str,
    mode: str = "truncate_tools",
    keep_n: int = 5,
    truncate_chars: int = 200,
    reason: str = "Compaction requested",
) -> Dict[str, Any]:
    """
    Run a compaction against a specific session. Reusable by both the MCP tool
    wrapper (agent-driven) and the slash command dispatcher (user-driven).

    Returns:
        {
            "ok": bool,
            "text": str,            # human-readable status text
            "mode": str,
            "num_shrunk": int | None,
            "bytes_saved": int | None,
            "older_exchanges": int,
            "total_messages_after": int,
            "session_id": str,
            "error": str | None,
        }
    """
    if mode not in ("truncate_tools", "strip_tools", "summarize"):
        return {
            "ok": False,
            "text": f"Invalid mode: {mode}. Use 'truncate_tools', 'strip_tools', or 'summarize'.",
            "error": "invalid_mode",
            "session_id": session_id,
        }

    main_module = sys.modules.get("main") or sys.modules.get("__main__")
    active_convs = getattr(main_module, "active_conversations", {})
    chat_manager = getattr(main_module, "chat_manager", None)

    if session_id not in active_convs:
        return {
            "ok": False,
            "text": f"Session {session_id} not found in active conversations. (Send a message first to load it.)",
            "error": "session_not_active",
            "session_id": session_id,
        }

    conv = active_convs[session_id]
    total_exchanges = _count_exchanges(conv.messages)

    logger.info(
        f"Compaction: session={session_id}, mode={mode}, {total_exchanges} exchanges, "
        f"keep_last={keep_n}, truncate_chars={truncate_chars}, reason={reason}"
    )

    # Auto-adjust keep_n so there's always at least 1 exchange to compact
    # (only when keep_n > 0; keep_n=0 explicitly compacts everything).
    if keep_n > 0 and total_exchanges > 0 and keep_n >= total_exchanges:
        keep_n = max(total_exchanges - 1, 0)
        logger.info(f"Auto-adjusted keep_exchanges to {keep_n} (only {total_exchanges} total)")

    older, recent = _split_at_exchange_boundary(conv.messages, keep_n)

    if not older:
        return {
            "ok": False,
            "text": "No older messages to compact. No changes made.",
            "error": "nothing_to_compact",
            "session_id": session_id,
        }

    older_exchanges = _count_exchanges(older)
    logger.info(
        f"Splitting: {len(older)} older messages ({older_exchanges} exchanges) | "
        f"{len(recent)} recent messages"
    )

    original_messages = conv.messages.copy()
    num_shrunk = None
    bytes_saved = None
    restore_backup_path = None
    summarized_display_messages = None

    try:
        if mode in ("truncate_tools", "strip_tools"):
            shrunk_older, num_shrunk, bytes_saved = _apply_tool_shrink(
                older, mode=mode, truncate_chars=truncate_chars, strip_args=True
            )

            if num_shrunk == 0:
                return {
                    "ok": True,
                    "kind": "noop",
                    "text": (
                        f"Nothing to compact — older history is already shrunk "
                        f"to this mode/threshold. Try a more aggressive mode "
                        f"('strip_tools' or 'summarize'), a smaller truncate_chars, "
                        f"or a smaller keep_exchanges."
                    ),
                    "error": None,
                    "session_id": session_id,
                }

            conv.messages = shrunk_older + recent
            action_summary = (
                f"Shrunk {num_shrunk} tool call(s) in older history "
                f"(~{bytes_saved:,} chars saved). "
                f"All {older_exchanges + _count_exchanges(recent)} exchanges preserved "
                f"verbatim; only tool outputs were modified."
            )

        else:  # mode == "summarize"
            summary = await _summarize_messages(older)
            # Validate again at the write boundary so tests or future alternate
            # summarizers cannot bypass the fail-closed guard.
            summary = _validate_summary_text(summary, _format_messages_for_summary(older))

            if chat_manager:
                existing_for_backup = chat_manager.load_chat(session_id) or {}
                backup_data = dict(existing_for_backup)
                backup_data["sessionId"] = session_id
                backup_data["messages"] = original_messages
                if hasattr(conv, "cumulative_usage") and conv.cumulative_usage:
                    backup_data["cumulative_usage"] = conv.cumulative_usage
                restore_backup_path = _write_compaction_restore_backup(
                    chat_manager, session_id, backup_data
                )

            compacted_msg = {
                "id": str(uuid.uuid4()),
                "role": "compacted",
                "content": summary,
                "compacted_at": datetime.now().isoformat(),
                "original_count": len(older),
                "original_exchanges": older_exchanges,
                "restore_backup_path": restore_backup_path,
            }
            conv.messages = [compacted_msg] + recent
            if chat_manager:
                existing_for_display = chat_manager.load_chat(session_id) or {}
                summarized_display_messages = _build_summarized_display_messages(
                    compacted_msg,
                    recent,
                    list(existing_for_display.get("display_messages") or []),
                )
            action_summary = (
                f"Summarized {len(older)} older messages ({older_exchanges} exchanges) "
                f"into 1 validated narrative summary. {len(recent)} recent messages "
                f"preserved verbatim."
            )
            if restore_backup_path:
                action_summary += f" Restore backup: {restore_backup_path}."

        # Save to disk — preserve all existing fields (agent, reactions, etc.)
        # while keeping UI-facing display_messages aligned with destructive
        # summarize rewrites. Truncate/strip still only overwrite `messages`.
        if chat_manager:
            existing = chat_manager.load_chat(session_id) or {}
            save_data = dict(existing)
            save_data["sessionId"] = session_id
            save_data["messages"] = conv.messages
            if summarized_display_messages is not None:
                save_data["display_messages"] = summarized_display_messages
            if hasattr(conv, "cumulative_usage") and conv.cumulative_usage:
                save_data["cumulative_usage"] = conv.cumulative_usage
            chat_manager.save_chat(session_id, save_data)
            logger.info(f"Saved compacted conversation to disk: {session_id}")

    except Exception as e:
        conv.messages = original_messages
        error_code = "summarize_invalid" if isinstance(e, SummaryValidationError) else "exception"
        logger.error(f"Compaction failed, rolled back: {e}", exc_info=True)
        return {
            "ok": False,
            "text": f"Compaction failed, rolled back: {e}",
            "error": error_code,
            "session_id": session_id,
        }

    result_text = (
        f"[mode={mode}] {action_summary} "
        f"History now has {len(conv.messages)} messages total."
    )
    logger.info(result_text)

    return {
        "ok": True,
        "text": result_text,
        "mode": mode,
        "num_shrunk": num_shrunk,
        "bytes_saved": bytes_saved,
        "older_exchanges": older_exchanges,
        "total_messages_after": len(conv.messages),
        "session_id": session_id,
        "restore_backup_path": restore_backup_path,
        "error": None,
    }


def _format_salon_messages_for_summary(messages: List[Dict[str, Any]]) -> str:
    """Render salon messages for the summarization subagent.

    Salon messages have ``from`` (sender) and ``content`` (text). We also
    inline any tool-blocks on a per-message basis as bracketed lines so the
    summarizer sees what each agent actually did.
    """
    parts: List[str] = []
    for m in messages:
        sender = m.get("from", "unknown")
        if m.get("kind") == "compacted":
            # Already-compacted prior summary — pass through so rolling
            # compaction preserves it.
            parts.append(m.get("content", ""))
            continue

        text = (m.get("content") or "").strip()
        block_lines: List[str] = []
        for b in m.get("blocks") or []:
            btype = b.get("type")
            if btype == "tool_use":
                tname = b.get("tool_name", "unknown")
                if tname.startswith("mcp__brain__"):
                    tname = tname[len("mcp__brain__"):]
                block_lines.append(f"  [Tool: {tname}]")
            elif btype == "tool_result":
                content = b.get("content") or ""
                if isinstance(content, str) and content:
                    snippet = content if len(content) <= 400 else content[:400] + "..."
                    block_lines.append(f"  [Output: {snippet}]")

        body = text
        if block_lines:
            body = (text + "\n" if text else "") + "\n".join(block_lines)
        if not body:
            continue
        parts.append(f"{sender}: {body}")
    return "\n\n".join(parts)


async def run_salon_compaction(
    *,
    salon_id: str,
    agent_name: str,
    mode: str = "truncate_tools",
    keep_n: int = 5,
    truncate_chars: int = 200,
    reason: str = "Compaction requested",
) -> Dict[str, Any]:
    """Salon-aware compaction — per-agent for truncate/strip, shared for summarize.

    See ``run_compaction`` for the 1:1 chat equivalent. This entry point is
    used when ``compact_conversation`` is invoked from within a salon
    dispatch (detected via ``_salon_id`` injection in mcp_tools/__init__.py).
    """
    if mode not in ("truncate_tools", "strip_tools", "summarize"):
        return {
            "ok": False,
            "text": f"Invalid mode: {mode}. Use 'truncate_tools', 'strip_tools', or 'summarize'.",
            "error": "invalid_mode",
            "salon_id": salon_id,
        }

    try:
        from salon_manager import get_manager
    except ImportError:
        return {
            "ok": False,
            "text": "Salon manager unavailable.",
            "error": "import_failed",
            "salon_id": salon_id,
        }

    manager = get_manager()
    if not manager.exists(salon_id):
        return {
            "ok": False,
            "text": f"Salon {salon_id} not found.",
            "error": "salon_not_found",
            "salon_id": salon_id,
        }

    logger.info(
        f"Salon compaction: salon={salon_id}, agent={agent_name}, mode={mode}, "
        f"keep_n={keep_n}, truncate_chars={truncate_chars}, reason={reason}"
    )

    if mode in ("truncate_tools", "strip_tools"):
        result = manager.shrink_own_tool_blocks(
            salon_id=salon_id,
            agent_name=agent_name,
            mode=mode,
            keep_n=keep_n,
            truncate_chars=truncate_chars,
        )
        if not result["ok"]:
            return {
                "ok": False,
                "text": f"Compaction failed: {result.get('error')}",
                "error": result.get("error"),
                "salon_id": salon_id,
            }

        if result["num_shrunk"] == 0:
            text = (
                f"[salon mode={mode}] Nothing to compact for {agent_name} — "
                f"either you have no own tool blocks older than your last "
                f"{keep_n} message(s), or they're already at-or-beyond this "
                f"mode/threshold. (Older own messages scanned: "
                f"{result['older_own_count']}.)"
            )
        else:
            text = (
                f"[salon mode={mode}] Shrunk tool blocks in {result['num_shrunk']} "
                f"of your own older message(s) (~{result['bytes_saved']:,} chars saved). "
                f"Other participants are unaffected — they never saw your tool blocks. "
                f"Your last {keep_n} message(s) and everything between them remain verbatim."
            )

        return {
            "ok": True,
            "text": text,
            "mode": mode,
            "salon_id": salon_id,
            "agent_name": agent_name,
            "num_shrunk": result["num_shrunk"],
            "bytes_saved": result["bytes_saved"],
            "error": None,
        }

    # mode == "summarize" — shared, destructive operation.
    data = manager.load(salon_id)
    if not data:
        return {
            "ok": False, "text": "Failed to load salon for summarize.",
            "error": "load_failed", "salon_id": salon_id,
        }

    messages = data.get("messages") or []
    split = manager._split_index_for_agent(messages, agent_name, keep_n)
    older = messages[:split]

    if not older:
        return {
            "ok": False,
            "text": (
                f"Nothing to compact — older history is empty for keep_n={keep_n} "
                f"anchored on {agent_name}."
            ),
            "error": "nothing_to_compact",
            "salon_id": salon_id,
        }

    rendered_older = _format_salon_messages_for_summary(older)
    if not rendered_older.strip():
        return {
            "ok": False,
            "text": "Older messages are all empty after rendering — nothing to summarize.",
            "error": "empty_older",
            "salon_id": salon_id,
        }

    # Reuse the 1:1 compaction subagent, with a salon-flavored prompt.
    from codex_backend import CodexRunOptions, ROOT_DIR, run_codex
    salon_intro = (
        f"This is a group-chat (\"salon\") between the user and multiple AI agents. "
        f"Participants visible in this excerpt: "
        f"{', '.join(sorted({m.get('from','?') for m in older}))}.\n\n"
        f"Summarize the conversation history below."
    )

    try:
        result = await run_codex(
            CodexRunOptions(
                model="opus",
                cwd=str(ROOT_DIR),
                identity_instructions=COMPACTION_SYSTEM_PROMPT,
                prompt=f"{salon_intro}\n\n{rendered_older}",
                tools=[],
                timeout_seconds=240,
                max_turns=1,
                ephemeral=True,
            )
        )
        summary_text = result.response
    except Exception as e:
        logger.error(f"Salon summarize failed: {e}", exc_info=True)
        return {
            "ok": False,
            "text": f"Salon summarize subagent failed: {e}",
            "error": "summarize_exception",
            "salon_id": salon_id,
        }

    if not summary_text:
        return {
            "ok": False,
            "text": "Salon summarize subagent produced no output.",
            "error": "summarize_empty",
            "salon_id": salon_id,
        }

    write_result = manager.replace_older_with_summary(
        salon_id=salon_id,
        target_agent=agent_name,
        keep_n=keep_n,
        summary_text=summary_text,
        triggered_by=agent_name,
    )

    if not write_result["ok"]:
        return {
            "ok": False,
            "text": f"Failed to write summary back to salon: {write_result.get('error')}",
            "error": write_result.get("error"),
            "salon_id": salon_id,
        }

    text = (
        f"[salon mode=summarize] Replaced {write_result['older_count']} earlier "
        f"message(s) with a single shared summary. ALL participants will now see "
        f"the summary instead of verbatim history when this salon is next rendered."
    )
    return {
        "ok": True,
        "text": text,
        "mode": "summarize",
        "salon_id": salon_id,
        "agent_name": agent_name,
        "older_count": write_result["older_count"],
        "compacted_msg_id": write_result["compacted_msg_id"],
        "error": None,
    }


@register_tool("utilities")
@tool(
    name="compact_conversation",
    description="""Compact the conversation's history to free up your context window.

Three modes — pick based on WHY context is bloated:

- **"truncate_tools"** (default): Truncate tool call outputs & args for messages older than `keep_exchanges`. All user/assistant dialogue is preserved verbatim. Fast, free (no LLM call). Use this when tool outputs (bash, reads, greps, agent invocations) are eating your context — which is almost always the case. START HERE.

- **"strip_tools"**: More aggressive — replace tool outputs entirely with "[output stripped]", keeping only tool name + short arg hint. Dialogue still verbatim. Fast, free. Use when truncate isn't enough, or when the tool results are truly unneeded going forward.

- **"summarize"**: Opus-powered semantic summary of older messages. Collapses BOTH dialogue and tools into a narrative. Slower (~10-30s). ⚠️ **In a group chat (salon), this summarizes the conversation for EVERYONE — verbatim chat history is permanently lost from every other participant who may still need it.** Think carefully before using summarize in a salon. Generally prefer `truncate_tools` or `strip_tools` to avoid burdening other agents. In a 1:1 chat with the user, summarize is safe.

**`keep_exchanges` semantics:**
- In a 1:1 chat with the user: counts user→assistant exchanges (default 5).
- In a salon (group chat): counts YOUR OWN past messages (default 5). The boundary is anchored to your Nth-from-last message — every message from that point forward (including OTHER agents' replies between your own messages) is preserved verbatim. So if you've spoken sparsely in a busy salon, a small `keep_exchanges` may still preserve a lot of others' messages by design (so they don't lose context that came after your last turn).

**Salon scope rules:**
- `truncate_tools` / `strip_tools` only mutate YOUR OWN tool blocks. Other agents are unaffected — they never saw your tool blocks anyway. Safe to call freely.
- `summarize` collapses ALL older messages (yours and everyone else's) into a single shared summary visible to everyone. Use sparingly.

Setting `keep_exchanges=0` compacts EVERYTHING. Modifies the conversation in-place and saves to disk.

Call proactively when context feels heavy, or when the user asks you to compact/shrink the conversation.""",
    input_schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["truncate_tools", "strip_tools", "summarize"],
                "description": "Compaction strategy. Default 'truncate_tools' is fast and free — preserves all dialogue verbatim, truncates tool outputs older than keep_exchanges. Use 'strip_tools' for aggressive removal of old tool outputs. Use 'summarize' only when old dialogue (not just tools) needs collapsing.",
                "default": "truncate_tools",
            },
            "keep_exchanges": {
                "type": "integer",
                "description": "Number of recent exchanges to keep fully verbatim (default: 5). Older exchanges are shrunk according to `mode`. Set to 0 to compact EVERYTHING, including the current in-flight exchange's tool outputs (useful for very long single tool outputs).",
                "default": 5,
                "minimum": 0,
            },
            "truncate_chars": {
                "type": "integer",
                "description": "For 'truncate_tools' mode: max chars per tool output & arg value (default: 200). Ignored for other modes.",
                "default": 200,
                "minimum": 0,
            },
            "reason": {
                "type": "string",
                "description": "Why compaction is being triggered (for logging).",
            },
        },
    },
)
async def compact_conversation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compact the current conversation's history (MCP tool wrapper).

    Routing:
      - If ``_salon_id`` is present (injected when called from a salon
        dispatch), route to ``run_salon_compaction`` — per-agent for
        truncate/strip, shared for summarize. NEVER falls through to the
        1:1 path, which would otherwise risk clobbering an unrelated 1:1
        chat that happens to be processing concurrently.
      - Otherwise (1:1 chat), auto-detect the active session and run
        ``run_compaction``.
    """
    mode = args.get("mode", "truncate_tools")
    keep_n = args.get("keep_exchanges", 5)
    truncate_chars = args.get("truncate_chars", 200)
    reason = args.get("reason", "Conversation compaction requested")

    # Salon mode — injected by mcp_tools/__init__.py::_inject_agent_context
    # when the calling agent is part of a salon dispatch.
    salon_id = args.get("_salon_id")
    agent_name = args.get("_agent_name")

    if salon_id:
        if not agent_name:
            return {
                "content": [{
                    "type": "text",
                    "text": "Error: salon compaction requires _agent_name (internal injection bug).",
                }],
                "is_error": True,
            }
        result = await run_salon_compaction(
            salon_id=salon_id,
            agent_name=agent_name,
            mode=mode,
            keep_n=keep_n,
            truncate_chars=truncate_chars,
            reason=reason,
        )
        return {
            "content": [{"type": "text", "text": result["text"]}],
            "is_error": not result["ok"],
        }

    # 1:1 chat — auto-detect which session is calling this tool.
    main_module = sys.modules.get("main") or sys.modules.get("__main__")
    active_convs = getattr(main_module, "active_conversations", {})
    active_processing = getattr(main_module, "active_processing_sessions", {})

    current_session = None
    for sid in active_processing:
        if sid in active_convs:
            current_session = sid
            break

    if not current_session:
        return {
            "content": [{
                "type": "text",
                "text": "Error: Could not determine current session ID. No active conversations found.",
            }],
            "is_error": True,
        }

    result = await run_compaction(
        session_id=current_session,
        mode=mode,
        keep_n=keep_n,
        truncate_chars=truncate_chars,
        reason=reason,
    )

    return {
        "content": [{"type": "text", "text": result["text"]}],
        "is_error": not result["ok"],
    }
