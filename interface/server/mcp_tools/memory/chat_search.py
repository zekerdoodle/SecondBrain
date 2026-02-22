"""
Chat History Search MCP Tool

Provides semantic search over raw conversation archives.
Two-layer pipeline:
  1. Qwen3 embedding search → top message-level hits grouped by conversation
  2. Haiku LLM extraction → structured excerpts with verbatim quotes

Falls back to raw embedding snippets if Haiku extraction fails.
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field
from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.memory.chat_search")

# ── Path setup ─────────────────────────────────────────────────────────────────

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

CLAUDE_DIR = os.path.dirname(SCRIPTS_DIR)  # .claude/
CHATS_DIR = os.path.join(CLAUDE_DIR, "chats")

# ── Constants ──────────────────────────────────────────────────────────────────

TOKENS_PER_CHAR = 0.25
DEFAULT_WINDOW_SIZE = 5       # Messages before/after match
MAX_CONVERSATIONS = 10        # Max conversations in Haiku call
MAX_TOKENS_PER_WINDOW = 4000  # Token budget per conversation window
MAX_TOTAL_TOKENS = 30000      # Total token budget for all windows


# ── Pydantic models for Haiku structured output ──────────────────────────────

class Excerpt(BaseModel):
    """A single excerpt from a conversation."""
    speaker: str = Field(description="Who said this: 'user' or 'assistant'")
    text: str = Field(description="Verbatim quote from the conversation")
    timestamp: Optional[str] = Field(
        default=None,
        description="When this was said (ISO date or relative like '2 days ago')"
    )


class ConversationResult(BaseModel):
    """Search result for a single conversation."""
    conversation_id: str = Field(description="Chat session ID")
    conversation_title: str = Field(description="Chat title")
    date: Optional[str] = Field(default=None, description="Approximate date of the conversation")
    relevance: str = Field(description="Brief explanation of why this conversation is relevant")
    excerpts: List[Excerpt] = Field(description="Key excerpts that answer the query")


class ExtractionResponse(BaseModel):
    """Structured response from Haiku extraction."""
    results: List[ConversationResult] = Field(
        description="Relevant conversations with excerpts, ordered by relevance"
    )


# ── Context window building ──────────────────────────────────────────────────

def build_context_windows(
    hits: list,
    max_conversations: int = MAX_CONVERSATIONS,
    window_size: int = DEFAULT_WINDOW_SIZE,
    max_tokens_per_window: int = MAX_TOKENS_PER_WINDOW,
    max_total_tokens: int = MAX_TOTAL_TOKENS,
) -> List[Dict[str, Any]]:
    """
    Group search hits by conversation, load context windows around matches.

    Args:
        hits: List of (ChatMessageMeta, score) tuples from embedding search
        max_conversations: Max number of conversations to include
        window_size: Number of messages before/after each match to include
        max_tokens_per_window: Max tokens per conversation window
        max_total_tokens: Total token budget across all windows

    Returns:
        List of window dicts: {chat_id, title, score, messages: [{role, content, is_match}]}
    """
    from contextual_memory.chat_embedding_index import strip_tool_markers

    # Group hits by chat_id, keeping max score per conversation
    chat_groups: Dict[str, Dict] = {}
    for meta, score in hits:
        cid = meta.chat_id
        if cid not in chat_groups:
            chat_groups[cid] = {
                "chat_id": cid,
                "title": meta.chat_title,
                "score": score,
                "match_indices": [meta.message_index],
                "timestamp": meta.timestamp,
            }
        else:
            if score > chat_groups[cid]["score"]:
                chat_groups[cid]["score"] = score
            chat_groups[cid]["match_indices"].append(meta.message_index)

    # Sort by score, take top N
    sorted_groups = sorted(
        chat_groups.values(), key=lambda x: x["score"], reverse=True
    )[:max_conversations]

    # Load each chat and extract context windows
    windows = []
    total_tokens = 0

    for group in sorted_groups:
        if total_tokens >= max_total_tokens:
            break

        chat_path = Path(CHATS_DIR) / f"{group['chat_id']}.json"
        if not chat_path.exists():
            continue

        try:
            with open(chat_path, "r", encoding="utf-8") as f:
                chat_data = json.load(f)

            messages = chat_data.get("messages", [])

            # Determine the range of messages to include
            match_indices = set(group["match_indices"])
            min_idx = max(0, min(match_indices) - window_size)
            max_idx = min(len(messages), max(match_indices) + window_size + 1)

            # Extract messages in range (only user/assistant)
            window_messages = []
            window_tokens = 0

            for i in range(min_idx, max_idx):
                if i >= len(messages):
                    break

                msg = messages[i]
                role = msg.get("role", "")

                if role not in ("user", "assistant"):
                    continue

                # Skip hidden messages
                if msg.get("hidden", False):
                    continue

                content = msg.get("content", "")
                if isinstance(content, list):
                    text_parts = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text_parts.append(block.get("text", ""))
                        elif isinstance(block, str):
                            text_parts.append(block)
                    content = "\n".join(text_parts)

                if not isinstance(content, str):
                    continue

                cleaned = strip_tool_markers(content)
                if not cleaned:
                    continue

                # Token budget per window
                msg_tokens = int(len(cleaned) * TOKENS_PER_CHAR)
                if window_tokens + msg_tokens > max_tokens_per_window:
                    # Truncate this message to fit
                    remaining = max_tokens_per_window - window_tokens
                    if remaining > 100:  # Only include if meaningful
                        char_limit = int(remaining / TOKENS_PER_CHAR)
                        cleaned = cleaned[:char_limit] + "..."
                        msg_tokens = remaining
                    else:
                        break

                window_messages.append({
                    "role": role,
                    "content": cleaned,
                    "is_match": i in match_indices,
                })
                window_tokens += msg_tokens

            if window_messages:
                windows.append({
                    "chat_id": group["chat_id"],
                    "title": group["title"],
                    "score": group["score"],
                    "timestamp": group.get("timestamp"),
                    "messages": window_messages,
                })
                total_tokens += window_tokens

        except Exception as e:
            logger.warning(f"Error loading chat {group['chat_id']}: {e}")
            continue

    return windows


# ── Haiku extraction ──────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are a precise conversation search tool. Given a user's search query and excerpts from past conversations, extract the most relevant portions.

Rules:
1. Only include conversations that are genuinely relevant to the query
2. Use VERBATIM quotes from the conversation text — do not paraphrase
3. Include enough context for the excerpts to be meaningful
4. Order results by relevance (most relevant first)
5. If none of the conversations are relevant, return an empty results list
6. Keep excerpts concise but complete — include the key information"""


async def extract_with_haiku(
    query: str,
    windows: List[Dict[str, Any]],
) -> Optional[ExtractionResponse]:
    """
    Call Haiku to extract structured results from context windows.

    Follows the query_rewriter.py pattern: fully consume async generator.
    """
    from claude_agent_sdk import query as sdk_query, ClaudeAgentOptions, ResultMessage, AssistantMessage, ToolUseBlock
    import datetime

    # Format windows for the prompt
    window_blocks = []
    for w in windows:
        lines = [f"### Conversation: {w['title']} (ID: {w['chat_id']})"]

        # Add date if available
        ts = w.get("timestamp")
        if ts:
            try:
                dt = datetime.datetime.fromtimestamp(ts)
                lines.append(f"Date: {dt.strftime('%Y-%m-%d')}")
            except (OSError, ValueError):
                pass

        lines.append("")
        for msg in w["messages"]:
            role_label = "[USER]" if msg["role"] == "user" else "[ASSISTANT]"
            match_marker = " ⭐" if msg.get("is_match") else ""
            lines.append(f"{role_label}{match_marker}: {msg['content']}")
            lines.append("")

        window_blocks.append("\n".join(lines))

    prompt = f"""Search query: "{query}"

Here are excerpts from past conversations that may be relevant:

---
{"---".join(window_blocks)}
---

Extract the most relevant conversations and quotes that answer the search query. Return structured JSON."""

    try:
        structured_data = None
        result = None

        # IMPORTANT: Must fully consume the async generator to avoid cancel scope errors
        async for message in sdk_query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="haiku",
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                output_format={
                    "type": "json_schema",
                    "schema": ExtractionResponse.model_json_schema()
                },
                max_turns=1,
            )
        ):
            # SDK workaround: structured output arrives as StructuredOutput tool use block
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, ToolUseBlock) and block.name == "StructuredOutput":
                        structured_data = block.input

            if isinstance(message, ResultMessage):
                data = message.structured_output or structured_data
                if data:
                    result = ExtractionResponse.model_validate(data)
                elif message.is_error:
                    logger.warning(f"Haiku extraction error: {message.result}")

        return result

    except Exception as e:
        logger.warning(f"Haiku extraction failed: {e}")
        return None


# ── Fallback formatting ──────────────────────────────────────────────────────

def format_embedding_fallback(
    hits: list,
    max_results: int = 5,
) -> str:
    """Format raw embedding search results as readable markdown (fallback path)."""
    import datetime

    # Group by conversation, take top by score
    seen_chats: Dict[str, Dict] = {}
    for meta, score in hits:
        cid = meta.chat_id
        if cid not in seen_chats:
            seen_chats[cid] = {
                "title": meta.chat_title,
                "score": score,
                "previews": [],
                "timestamp": meta.timestamp,
            }
        if len(seen_chats[cid]["previews"]) < 2:
            seen_chats[cid]["previews"].append({
                "role": meta.role,
                "preview": meta.content_preview,
            })

    # Sort and limit
    sorted_chats = sorted(
        seen_chats.items(), key=lambda x: x[1]["score"], reverse=True
    )[:max_results]

    lines = ["## Conversation Search Results\n"]
    lines.append("*Note: Showing raw search matches (LLM extraction unavailable)*\n")

    for i, (chat_id, info) in enumerate(sorted_chats, 1):
        date_str = ""
        if info["timestamp"]:
            try:
                dt = datetime.datetime.fromtimestamp(info["timestamp"])
                date_str = f" ({dt.strftime('%Y-%m-%d')})"
            except (OSError, ValueError):
                pass

        lines.append(f"### {i}. {info['title']}{date_str}")
        lines.append(f"Relevance score: {info['score']:.3f}")
        lines.append("")

        for preview in info["previews"]:
            role_label = "User" if preview["role"] == "user" else "Assistant"
            lines.append(f"**{role_label}**: {preview['preview']}")
            lines.append("")

    return "\n".join(lines)


# ── MCP Tool ──────────────────────────────────────────────────────────────────

@register_tool("memory")
@tool(
    name="search_conversation_history",
    description="""Search past conversation archives for specific discussions, decisions, or information.

Use this when you need to find what was discussed in a previous chat — specific technical decisions, user preferences expressed in conversation, debugging sessions, or any past dialogue.

Returns relevant conversation excerpts with verbatim quotes and context.

This searches the raw chat archives (~900 conversations), NOT the structured memory system. Use memory_search for structured memories, and this tool for finding specific past discussions.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in past conversations. Be specific — e.g. 'discussion about switching from Redis to SQLite' rather than just 'database'.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum number of conversation results to return (default: 5, max: 10).",
                "default": 5,
                "minimum": 1,
                "maximum": 10,
            },
            "date_range": {
                "type": "object",
                "description": "Optional date filter. Provide start and/or end as ISO date strings (YYYY-MM-DD).",
                "properties": {
                    "start": {
                        "type": "string",
                        "description": "Start date (inclusive), e.g. '2025-01-01'",
                    },
                    "end": {
                        "type": "string",
                        "description": "End date (inclusive), e.g. '2025-12-31'",
                    },
                },
            },
        },
        "required": ["query"],
    },
)
async def search_conversation_history(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search conversation history using semantic embeddings + LLM extraction."""
    try:
        search_query = args.get("query", "").strip()
        max_results = min(args.get("max_results", 5), 10)
        date_range_raw = args.get("date_range")

        if not search_query:
            return _error("query is required")

        start_time = time.time()

        # Parse date range if provided
        date_range = None
        if date_range_raw:
            import datetime
            dr: Dict[str, float] = {}
            if "start" in date_range_raw:
                try:
                    dt = datetime.datetime.strptime(date_range_raw["start"], "%Y-%m-%d")
                    dr["start"] = dt.timestamp()
                except ValueError:
                    pass
            if "end" in date_range_raw:
                try:
                    dt = datetime.datetime.strptime(date_range_raw["end"], "%Y-%m-%d")
                    # End of day
                    dt = dt.replace(hour=23, minute=59, second=59)
                    dr["end"] = dt.timestamp()
                except ValueError:
                    pass
            if dr:
                date_range = dr

        # Layer 1: Get embedding search results
        from contextual_memory.chat_embedding_index import get_index, search

        index = get_index()

        if not index.metadata:
            return {"content": [{"type": "text", "text": (
                "Chat index is empty — no conversations have been indexed yet. "
                "The index will be built automatically on next use."
            )}]}

        # Search with generous k for grouping (we'll narrow down per-conversation)
        k = max_results * 4  # Get more hits to have good per-conversation coverage
        hits = search(index, search_query, k=k, date_range=date_range)

        if not hits:
            return {"content": [{"type": "text", "text": (
                f'No conversations found matching "{search_query}". '
                f"Try different search terms or broaden your query."
            )}]}

        search_time = time.time() - start_time

        # Layer 2: Build context windows and extract with Haiku
        windows = build_context_windows(hits, max_conversations=max_results)

        if not windows:
            # Fallback to raw results
            fallback = format_embedding_fallback(hits, max_results)
            return {"content": [{"type": "text", "text": fallback}]}

        # Try Haiku extraction
        extraction = await extract_with_haiku(search_query, windows)

        total_time = time.time() - start_time

        if extraction and extraction.results:
            # Format structured results
            return _format_extraction_response(
                extraction, search_query, total_time, search_time, len(index.metadata)
            )
        else:
            # Fallback to raw embedding results
            logger.info("Haiku extraction returned no results, using fallback")
            fallback = format_embedding_fallback(hits, max_results)
            fallback += f"\n\n*Search completed in {total_time:.1f}s across {len(index.metadata)} indexed messages*"
            return {"content": [{"type": "text", "text": fallback}]}

    except Exception as e:
        import traceback
        logger.error(f"search_conversation_history error: {e}\n{traceback.format_exc()}")
        return _error(f"Error searching conversation history: {e}")


# ── Response formatting ──────────────────────────────────────────────────────

def _format_extraction_response(
    extraction: ExtractionResponse,
    query: str,
    total_time: float,
    search_time: float,
    index_size: int,
) -> Dict[str, Any]:
    """Format the Haiku extraction response as readable markdown."""
    lines = [f'## Conversation Search: "{query}"\n']

    for i, result in enumerate(extraction.results, 1):
        date_str = f" ({result.date})" if result.date else ""
        lines.append(f"### {i}. {result.conversation_title}{date_str}")
        lines.append(f"*{result.relevance}*")
        lines.append("")

        for excerpt in result.excerpts:
            speaker = "**User**" if excerpt.speaker == "user" else "**Assistant**"
            ts = f" _{excerpt.timestamp}_" if excerpt.timestamp else ""
            lines.append(f"{speaker}{ts}:")
            lines.append(f"> {excerpt.text}")
            lines.append("")

    lines.append(
        f"*Found {len(extraction.results)} relevant conversations. "
        f"Searched {index_size} messages in {search_time:.1f}s, "
        f"total: {total_time:.1f}s*"
    )

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _error(msg: str) -> Dict[str, Any]:
    """Return a standard error response."""
    return {"content": [{"type": "text", "text": msg}], "is_error": True}
