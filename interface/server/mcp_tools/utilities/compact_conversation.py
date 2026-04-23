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
   summary of older messages. Semantic, expensive (~$0.10+ per call), but
   preserves meaning across long-ago dialogue. Use when there's lots of OLD
   dialogue that needs collapsing (not just tool bloat).
"""

import os
import sys
import uuid
import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

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
    """
    user_count = 0
    split_index = 0  # Default: everything is "recent"

    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            user_count += 1
            if user_count >= keep_last_n:
                split_index = i
                break

    return messages[:split_index], messages[split_index:]


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

    Uses Opus via the Claude Agent SDK for maximum comprehension and thoroughness.
    Returns a text summary.
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    conversation_text = _format_messages_for_summary(messages)

    prompt = f"Summarize this conversation history:\n\n{conversation_text}"

    logger.info(
        f"Running compaction subagent on {len(messages)} messages "
        f"({len(conversation_text)} chars)"
    )

    try:
        result_text = None

        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="opus",
                system_prompt=COMPACTION_SYSTEM_PROMPT,
                max_turns=1,
                permission_mode="bypassPermissions",
                allowed_tools=[],
                setting_sources=[],
            ),
        ):
            if isinstance(message, ResultMessage) and message.result:
                result_text = message.result

        if result_text:
            logger.info(
                f"Compaction subagent produced {len(result_text)} char summary"
            )
            return result_text

        logger.warning("Compaction subagent returned no result")
        return None

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

        # Skip already-shrunk messages
        if m.get("_compacted_mode") in ("truncate_tools", "strip_tools"):
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


@register_tool("utilities")
@tool(
    name="compact_conversation",
    description="""Compact the current conversation's history to free up context window.

Three modes — pick based on WHY context is bloated:

- **"truncate_tools"** (default): Truncate tool call outputs & args for messages older than `keep_exchanges`. All user/assistant dialogue is preserved verbatim. Fast, free (no LLM call). Use this when tool outputs (bash, reads, greps, agent invocations) are eating your context — which is almost always the case. START HERE.

- **"strip_tools"**: More aggressive — replace tool outputs entirely with "[output stripped]", keeping only tool name + short arg hint. Dialogue still verbatim. Fast, free. Use when truncate isn't enough, or when the tool results are truly unneeded going forward.

- **"summarize"**: Opus-powered semantic summary of older messages. Collapses BOTH dialogue and tools into a narrative. Slower (~10-30s) and costs money. Use only when there's lots of old user/assistant discussion that also needs collapsing — not just tool bloat.

Always preserves the last `keep_exchanges` exchanges (default 5) completely verbatim. Modifies the conversation in-place and saves to disk.

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
                "description": "Number of recent exchanges to keep fully verbatim (default: 5). Older exchanges are shrunk according to `mode`.",
                "default": 5,
                "minimum": 0,
                "maximum": 20,
            },
            "truncate_chars": {
                "type": "integer",
                "description": "For 'truncate_tools' mode: max chars per tool output & arg value (default: 200). Ignored for other modes.",
                "default": 200,
                "minimum": 0,
                "maximum": 2000,
            },
            "reason": {
                "type": "string",
                "description": "Why compaction is being triggered (for logging).",
            },
        },
    },
)
async def compact_conversation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compact the current conversation's history."""
    mode = args.get("mode", "truncate_tools")
    keep_n = args.get("keep_exchanges", 5)
    truncate_chars = args.get("truncate_chars", 200)
    reason = args.get("reason", "Conversation compaction requested")

    if mode not in ("truncate_tools", "strip_tools", "summarize"):
        return {
            "content": [{"type": "text", "text": f"Invalid mode: {mode}. Use 'truncate_tools', 'strip_tools', or 'summarize'."}],
            "is_error": True,
        }

    # Access the current conversation (same pattern as restart_server)
    main_module = sys.modules.get("main") or sys.modules.get("__main__")
    active_convs = getattr(main_module, "active_conversations", {})
    chat_manager = getattr(main_module, "chat_manager", None)
    active_processing = getattr(main_module, "active_processing_sessions", {})

    # Auto-detect which session is calling this tool
    current_session = None
    for sid in active_processing:
        if sid in active_convs:
            current_session = sid
            break

    if not current_session:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: Could not determine current session ID. No active conversations found.",
                }
            ],
            "is_error": True,
        }

    if current_session not in active_convs:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Error: Session {current_session} not found in active conversations.",
                }
            ],
            "is_error": True,
        }

    conv = active_convs[current_session]
    total_exchanges = _count_exchanges(conv.messages)

    logger.info(
        f"Compaction requested: mode={mode}, {total_exchanges} exchanges, "
        f"keep_last={keep_n}, truncate_chars={truncate_chars}, reason={reason}"
    )

    # Auto-adjust keep_n so there's always at least 1 exchange to compact
    if total_exchanges > 0 and keep_n >= total_exchanges:
        keep_n = max(total_exchanges - 1, 0)
        logger.info(f"Auto-adjusted keep_exchanges to {keep_n} (only {total_exchanges} total)")

    # Split into older (to compact) and recent (to keep verbatim)
    older, recent = _split_at_exchange_boundary(conv.messages, keep_n)

    if not older:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "No older messages to compact. No changes made.",
                }
            ],
        }

    older_exchanges = _count_exchanges(older)
    logger.info(
        f"Splitting: {len(older)} older messages ({older_exchanges} exchanges) | "
        f"{len(recent)} recent messages"
    )

    # Backup for rollback
    original_messages = conv.messages.copy()

    # ── Dispatch by mode ──
    try:
        if mode in ("truncate_tools", "strip_tools"):
            # Mechanical tool output shrinking — preserves dialogue verbatim
            shrunk_older, num_shrunk, bytes_saved = _apply_tool_shrink(
                older, mode=mode, truncate_chars=truncate_chars, strip_args=True
            )

            if num_shrunk == 0:
                return {
                    "content": [{
                        "type": "text",
                        "text": (
                            f"No tool outputs to shrink in the older {older_exchanges} "
                            f"exchange(s). Conversation unchanged. "
                            f"(Try mode='summarize' if you want to collapse old dialogue too.)"
                        ),
                    }],
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
            if not summary:
                return {
                    "content": [{
                        "type": "text",
                        "text": "Compaction failed: subagent produced no summary. Conversation unchanged.",
                    }],
                    "is_error": True,
                }

            compacted_msg = {
                "id": str(uuid.uuid4()),
                "role": "compacted",
                "content": summary,
                "compacted_at": datetime.now().isoformat(),
                "original_count": len(older),
                "original_exchanges": older_exchanges,
            }
            conv.messages = [compacted_msg] + recent
            action_summary = (
                f"Summarized {len(older)} older messages ({older_exchanges} exchanges) "
                f"into 1 narrative summary. {len(recent)} recent messages preserved verbatim."
            )

        # Save to disk — PRESERVE all existing fields (display_messages, agent,
        # reactions, etc.) and only overwrite `messages`. chat_manager.save_chat
        # does a full overwrite with no merging, so we must merge ourselves or
        # we'll wipe display_messages and break UI rendering.
        if chat_manager:
            existing = chat_manager.load_chat(current_session) or {}
            save_data = dict(existing)  # shallow copy of all existing fields
            save_data["sessionId"] = current_session
            save_data["messages"] = conv.messages
            if hasattr(conv, "cumulative_usage") and conv.cumulative_usage:
                save_data["cumulative_usage"] = conv.cumulative_usage
            # NOTE: display_messages is preserved as-is from `existing`. The
            # context savings come from shrinking `messages` (what the agent
            # reads). display_messages drives UI rendering and may still show
            # full tool outputs — that's intentional for now.
            chat_manager.save_chat(current_session, save_data)
            logger.info(f"Saved compacted conversation to disk: {current_session}")

    except Exception as e:
        conv.messages = original_messages
        logger.error(f"Compaction failed, rolled back: {e}", exc_info=True)
        return {
            "content": [{"type": "text", "text": f"Compaction failed, rolled back: {e}"}],
            "is_error": True,
        }

    result_text = (
        f"[mode={mode}] {action_summary} "
        f"History now has {len(conv.messages)} messages total."
    )
    logger.info(result_text)

    return {"content": [{"type": "text", "text": result_text}]}
