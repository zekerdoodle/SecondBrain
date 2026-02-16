"""
Conversation Compaction tool.

Compacts older conversation history into a dense summary, preserving the last N
exchanges verbatim. Reduces context window usage for long conversations.

The compaction subagent (Sonnet) produces a narrative summary preserving key
decisions, facts, action items, tool results, and conversational flow.
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
COMPACTION_SYSTEM_PROMPT = """You are a conversation compactor. Produce a dense, accurate summary of a conversation between the user (user) and Claude (assistant).

Preserve:
1. KEY DECISIONS and their rationale
2. FACTS established (numbers, names, dates, preferences)
3. ACTION ITEMS (what was committed to, by whom)
4. TOOL RESULTS that mattered (commands run, API responses, errors hit)
5. CURRENT STATE (what we're in the middle of, what's pending, what's been tried)

Do NOT include: pleasantries, greetings, social filler, redundant restatements.
Write in narrative past tense. Use sections if the conversation spanned multiple distinct topics. Aim for 10-20% of the original length.

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

    Uses Sonnet via the Claude Agent SDK for speed and cost efficiency.
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
                model="sonnet",
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


@register_tool("utilities")
@tool(
    name="compact_conversation",
    description="""Compact older conversation history into a summary to free up context space.

Use this when a conversation is getting long and you want to preserve context window budget. The last 5 exchanges (user/assistant pairs + their tool calls) are kept verbatim. Everything older is replaced with a concise summary.

This modifies the current conversation's history in-place and saves to disk. The summary preserves key decisions, facts, action items, and context.

Call this proactively when the conversation exceeds ~15 exchanges, or when the user asks you to compact/summarize the conversation history.""",
    input_schema={
        "type": "object",
        "properties": {
            "keep_exchanges": {
                "type": "integer",
                "description": "Number of recent exchanges to keep verbatim (default: 5)",
                "default": 5,
                "minimum": 2,
                "maximum": 20,
            },
            "reason": {
                "type": "string",
                "description": "Why compaction is being triggered (for logging)",
            },
        },
    },
)
async def compact_conversation(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compact the current conversation's history."""
    keep_n = args.get("keep_exchanges", 5)
    reason = args.get("reason", "Conversation compaction requested")

    # Access the current conversation (same pattern as restart_server)
    main_module = sys.modules.get("main") or sys.modules.get("__main__")
    active_convs = getattr(main_module, "active_conversations", {})
    chat_manager = getattr(main_module, "chat_manager", None)
    current_session = getattr(main_module, "current_processing_session", None)

    if not current_session:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Error: Could not determine current session ID.",
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
        f"Compaction requested: {total_exchanges} exchanges, keep_last={keep_n}, "
        f"reason={reason}"
    )

    # Guard: not enough exchanges to compact
    if total_exchanges <= keep_n + 2:
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Only {total_exchanges} exchanges in this conversation — "
                        f"not enough to compact (need at least {keep_n + 3}). "
                        f"No changes made."
                    ),
                }
            ],
        }

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

    # Run the compaction subagent
    try:
        summary = await _summarize_messages(older)
    except Exception as e:
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Compaction failed (subagent error): {e}. Conversation unchanged.",
                }
            ],
            "is_error": True,
        }

    if not summary:
        return {
            "content": [
                {
                    "type": "text",
                    "text": "Compaction failed: subagent produced no summary. Conversation unchanged.",
                }
            ],
            "is_error": True,
        }

    # Build the compacted message
    compacted_msg = {
        "id": str(uuid.uuid4()),
        "role": "compacted",
        "content": summary,
        "compacted_at": datetime.now().isoformat(),
        "original_count": len(older),
        "original_exchanges": older_exchanges,
    }

    # Backup and replace
    original_messages = conv.messages.copy()

    try:
        conv.messages = [compacted_msg] + recent

        # Save to disk
        if chat_manager:
            existing = chat_manager.load_chat(current_session)
            title = existing.get("title", "Untitled") if existing else "Untitled"
            save_data = {
                "title": title,
                "sessionId": current_session,
                "messages": conv.messages,
            }
            # Preserve cumulative usage if it exists
            if hasattr(conv, "cumulative_usage") and conv.cumulative_usage:
                save_data["cumulative_usage"] = conv.cumulative_usage
            chat_manager.save_chat(current_session, save_data)
            logger.info(f"Saved compacted conversation to disk: {current_session}")

    except Exception as e:
        # Rollback on failure
        conv.messages = original_messages
        logger.error(f"Compaction save failed, rolled back: {e}")
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Compaction save failed, rolled back: {e}",
                }
            ],
            "is_error": True,
        }

    result_text = (
        f"Compacted conversation: {len(older)} messages ({older_exchanges} exchanges) "
        f"-> 1 summary. {len(recent)} recent messages preserved verbatim. "
        f"History now has {len(conv.messages)} messages total."
    )
    logger.info(result_text)

    return {"content": [{"type": "text", "text": result_text}]}
