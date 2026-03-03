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
PROJECTS_DIR = os.path.join(os.path.expanduser("~"), ".claude", "projects")

# ── Constants ──────────────────────────────────────────────────────────────────

TOKENS_PER_CHAR = 0.25
DEFAULT_WINDOW_SIZE = 5       # Messages before/after match
MAX_CONVERSATIONS = 10        # Max conversations in Haiku call
MAX_TOKENS_PER_WINDOW = 4000  # Token budget per conversation window
MAX_TOTAL_TOKENS = 30000      # Total token budget for all windows


# ── Pydantic models for Haiku structured output ──────────────────────────────

class Excerpt(BaseModel):
    """A single excerpt from a conversation."""
    model_config = {"extra": "ignore"}

    speaker: str = Field(default="assistant", description="Who said this: 'user' or 'assistant'")
    text: str = Field(default="", description="Verbatim quote from the conversation")


class ConversationResult(BaseModel):
    """Search result for a single conversation."""
    model_config = {"extra": "ignore"}

    conversation_id: str = Field(default="", description="Chat session ID")
    conversation_title: str = Field(default="", description="Chat title")
    date: str = Field(default="", description="Approximate date (YYYY-MM-DD) or empty string")
    relevance: str = Field(default="", description="Brief explanation of why this conversation is relevant")
    excerpts: List[Excerpt] = Field(default_factory=list, description="Key excerpts that answer the query")


class ExtractionResponse(BaseModel):
    """Structured response from Haiku extraction."""
    model_config = {"extra": "ignore"}

    results: List[ConversationResult] = Field(
        default_factory=list,
        description="Relevant conversations with excerpts, ordered by relevance"
    )



# ── Chat file loading ──────────────────────────────────────────────────────────

def _find_chat_file(chat_id: str) -> Optional[Tuple[Path, str]]:
    """Find a chat file by ID, checking both JSON and JSONL locations.

    Returns (path, format) where format is 'json' or 'jsonl', or None if not found.
    """
    # Check Second Brain JSON chats first
    json_path = Path(CHATS_DIR) / f"{chat_id}.json"
    if json_path.exists():
        return json_path, "json"

    # Check Claude Code JSONL projects
    projects_path = Path(PROJECTS_DIR)
    if projects_path.exists():
        for proj_dir in projects_path.iterdir():
            if not proj_dir.is_dir():
                continue
            jsonl_path = proj_dir / f"{chat_id}.jsonl"
            if jsonl_path.exists():
                return jsonl_path, "jsonl"

    return None


def _load_messages_json(chat_path: Path) -> List[Dict[str, Any]]:
    """Load messages from a Second Brain JSON chat file."""
    with open(chat_path, "r", encoding="utf-8") as f:
        chat_data = json.load(f)
    return chat_data.get("messages", [])


def _load_messages_jsonl(chat_path: Path) -> List[Dict[str, Any]]:
    """Load messages from a Claude Code JSONL chat file.

    Converts JSONL format to the same {role, content} structure as JSON chats.
    Only returns user/assistant messages (skips tool results, system messages).
    """
    from contextual_memory.chat_embedding_index import _extract_text_content

    messages = []
    with open(chat_path, "r", encoding="utf-8") as f:
        for line in f:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type")
            if msg_type not in ("user", "assistant"):
                continue

            # Skip tool result messages
            if "toolUseResult" in obj:
                continue

            message = obj.get("message", {})
            if not message:
                continue

            role = message.get("role", msg_type)
            content = _extract_text_content(message.get("content", ""))

            messages.append({
                "role": role,
                "content": content,
            })

    return messages


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

        # Find chat file (JSON or JSONL)
        found = _find_chat_file(group["chat_id"])
        if not found:
            logger.debug(f"Chat file not found for {group['chat_id']}")
            continue

        chat_path, chat_format = found

        try:
            if chat_format == "json":
                messages = _load_messages_json(chat_path)
            else:
                messages = _load_messages_jsonl(chat_path)

            if not messages:
                continue

            # Cluster nearby matches — matches within window_size*2 of each
            # other merge into one cluster; distant matches get separate windows
            match_indices = sorted(group["match_indices"])
            clusters: List[List[int]] = []
            current_cluster: List[int] = [match_indices[0]]

            for idx in match_indices[1:]:
                if idx - current_cluster[-1] <= window_size * 2:
                    current_cluster.append(idx)
                else:
                    clusters.append(current_cluster)
                    current_cluster = [idx]
            clusters.append(current_cluster)

            # Build a sub-window for each cluster
            for cluster in clusters:
                if total_tokens >= max_total_tokens:
                    break

                cluster_set = set(cluster)
                min_idx = max(0, min(cluster) - window_size)
                max_idx = min(len(messages), max(cluster) + window_size + 1)

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
                        "is_match": i in cluster_set,
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


# ── JSON repair ───────────────────────────────────────────────────────────────

def _repair_truncated_json(json_str: str) -> Optional[str]:
    """Attempt to repair truncated JSON from Haiku by closing open structures.

    Works by tracking open brackets/braces/strings and closing them.
    Returns None if the input is too broken to repair.
    """
    if not json_str or not json_str.strip().startswith('{'):
        return None

    # Find the last complete result object by looking for the pattern
    # Try progressively shorter substrings until we find valid JSON
    # Strategy: find last complete "}" and close the remaining structure

    # Count open structures
    in_string = False
    escape_next = False
    stack = []

    for ch in json_str:
        if escape_next:
            escape_next = False
            continue
        if ch == '\\' and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue

        if ch in '{[':
            stack.append(ch)
        elif ch == '}':
            if stack and stack[-1] == '{':
                stack.pop()
        elif ch == ']':
            if stack and stack[-1] == '[':
                stack.pop()

    if not stack:
        return json_str  # Already valid

    # If we're inside a string, close it first
    if in_string:
        json_str += '"'

    # Close remaining open structures in reverse order
    closers = {'{': '}', '[': ']'}
    suffix = ""
    for opener in reversed(stack):
        suffix += closers.get(opener, '')

    repaired = json_str + suffix
    return repaired


# ── Haiku extraction ──────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are a conversation search tool. Given a user's search query and excerpts from past conversations, extract relevant portions.

Rules:
1. Include conversations that contain information related to the query — even partial matches or indirect context count
2. Messages marked with ⭐ are search hits — prioritize extracting those and their surrounding context
3. Use VERBATIM quotes from the conversation text — do not paraphrase
4. Keep each excerpt to 1-3 sentences MAX (the most relevant sentences only)
5. Order results by relevance (most relevant first)
6. ONLY return an empty results list if the conversations have absolutely nothing to do with the query
7. Return valid, complete JSON — never truncate mid-string"""


async def extract_with_haiku(
    query: str,
    windows: List[Dict[str, Any]],
    keyword_hint: Optional[str] = None,
) -> Optional[ExtractionResponse]:
    """
    Call Haiku to extract structured results from context windows.

    Uses plain text JSON response (not SDK structured output) for reliability.
    """
    logger.info(f"[DIAG] extract_with_haiku ENTERED: query={query[:80]!r}, windows={len(windows)}, keyword_hint={keyword_hint!r}")
    try:
        from claude_agent_sdk import query as sdk_query, ClaudeAgentOptions, ResultMessage
        logger.info("[DIAG] SDK imports succeeded")
    except Exception as import_err:
        logger.error(f"[DIAG] SDK import failed: {import_err}")
        return None
    import datetime
    import re

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

    keyword_note = ""
    if keyword_hint:
        keyword_note = f'\nNote: The user is specifically searching for the keyword "{keyword_hint}". Prioritize excerpts containing this exact term.\n'

    prompt = f"""Search query: "{query}"
{keyword_note}
Here are excerpts from past conversations that may be relevant:

---
{"---".join(window_blocks)}
---

Extract the most relevant conversations and quotes that answer the search query.

Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{
  "results": [
    {{
      "conversation_id": "the chat session ID",
      "conversation_title": "the chat title",
      "date": "YYYY-MM-DD or null",
      "relevance": "brief explanation of why this is relevant",
      "excerpts": [
        {{
          "speaker": "user or assistant",
          "text": "verbatim quote from the conversation"
        }}
      ]
    }}
  ]
}}"""

    try:
        result_text = None

        # IMPORTANT: Must fully consume the async generator to avoid cancel scope errors
        async for message in sdk_query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="haiku",
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                max_turns=1,
                permission_mode="bypassPermissions",
                allowed_tools=[],
                setting_sources=[],
            )
        ):
            if isinstance(message, ResultMessage):
                if message.is_error:
                    logger.warning(f"Haiku extraction error: {message.result}")
                elif message.result:
                    result_text = message.result

        if not result_text:
            logger.warning("Haiku extraction returned empty result")
            return None

        # Parse JSON from text response — handle markdown code blocks
        json_str = result_text.strip()
        # Strip ```json ... ``` wrapper if present
        md_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', json_str, re.DOTALL)
        if md_match:
            json_str = md_match.group(1).strip()

        # Try to find JSON object in the response
        if not json_str.startswith('{'):
            start = json_str.find('{')
            end = json_str.rfind('}')
            if start >= 0 and end > start:
                json_str = json_str[start:end + 1]

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            # Try to repair truncated JSON by closing open structures
            repaired = _repair_truncated_json(json_str)
            if repaired:
                try:
                    data = json.loads(repaired)
                    logger.info("Repaired truncated JSON from Haiku response")
                except json.JSONDecodeError:
                    logger.warning(f"Haiku returned invalid JSON (repair failed): {e}. Response: {result_text[:500]}")
                    return None
            else:
                logger.warning(f"Haiku returned invalid JSON: {e}. Response: {result_text[:500]}")
                return None

        # Lenient validation — fix common issues before validating
        if isinstance(data, dict) and "results" in data:
            for r in data["results"]:
                if "excerpts" not in r:
                    r["excerpts"] = []
                for field in ("conversation_id", "conversation_title", "relevance"):
                    if field not in r:
                        r[field] = ""

        try:
            return ExtractionResponse.model_validate(data)
        except Exception as e:
            logger.warning(f"Haiku extraction validation failed: {e}. Data: {json.dumps(data)[:500]}")
            return None

    except BaseException as e:
        logger.error(f"[DIAG] Haiku extraction failed ({type(e).__name__}): {e}", exc_info=True)
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

This searches the raw chat archives (~900 conversations), NOT the structured memory system. Use memory_search for structured memories, and this tool for finding specific past discussions.

By default, searches only YOUR OWN conversation history. Use `agent: "all"` to search across all agents, or specify another agent's name to search their chats.

Search modes:
- Semantic only: provide `query` — best for conceptual/topical searches
- Keyword only: provide `keyword` — best for exact terms, function names, project names
- Hybrid: provide both `query` and `keyword` — best-of-both-worlds, keyword matches boosted""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for in past conversations. Be specific — e.g. 'discussion about switching from Redis to SQLite' rather than just 'database'.",
            },
            "keyword": {
                "type": "string",
                "description": "Exact keyword or phrase to match. Best for function names, project names, specific terms (e.g. 'build_chat_index', 'moltbook', 'Qwen3').",
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
            "agent": {
                "type": "string",
                "description": "Filter by agent. Defaults to your own chats only. Use 'all' to search across all agents, or specify another agent's name (e.g. 'character', 'ops', 'coder').",
            },
        },
    },
)
async def search_conversation_history(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search conversation history using semantic embeddings, keyword matching, or both."""
    try:
        search_query = args.get("query", "").strip()
        keyword = args.get("keyword", "").strip()
        max_results = min(args.get("max_results", 5), 10)
        date_range_raw = args.get("date_range")
        agent_param = args.get("agent", "").strip()
        calling_agent = args.pop("_agent_name", None)

        if not search_query and not keyword:
            return _error("At least one of 'query' or 'keyword' is required.")

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

        # Load index
        from contextual_memory.chat_embedding_index import (
            get_index, search, keyword_search, hybrid_search,
            expand_agent_names,
        )

        # Determine agent filter:
        # - "all" → no filter (search everything)
        # - specific agent name → expand aliases and filter
        # - not provided → default to calling agent's own chats
        agent_filter = None
        agent_scope_label = "all agents"
        if agent_param.lower() == "all":
            agent_filter = None
            agent_scope_label = "all agents"
        elif agent_param:
            agent_filter = expand_agent_names(agent_param)
            agent_scope_label = f"agent: {agent_param}"
        elif calling_agent:
            agent_filter = expand_agent_names(calling_agent)
            agent_scope_label = f"agent: {calling_agent}"

        index = get_index()

        if not index.metadata:
            return {"content": [{"type": "text", "text": (
                "Chat index is empty — no conversations have been indexed yet. "
                "The index will be built automatically on next use."
            )}]}

        # Determine search mode and run appropriate search
        k = max_results * 4  # Get more hits for per-conversation grouping

        if search_query and keyword:
            search_mode = "hybrid"
            hits = hybrid_search(index, search_query, keyword, k=k, date_range=date_range, agent_filter=agent_filter)
        elif keyword:
            search_mode = "keyword"
            hits = keyword_search(index, keyword, k=k, date_range=date_range, agent_filter=agent_filter)
        else:
            search_mode = "semantic"
            hits = search(index, search_query, k=k, date_range=date_range, agent_filter=agent_filter)

        display_query = search_query or keyword

        if not hits:
            mode_label = {"semantic": "semantic", "keyword": "keyword", "hybrid": "hybrid"}[search_mode]
            scope_hint = f' Scope: {agent_scope_label}.' if agent_filter else ''
            return {"content": [{"type": "text", "text": (
                f'No conversations found matching "{display_query}" ({mode_label} search).{scope_hint} '
                f"Try different search terms, broaden your query, or use agent: \"all\" to search across all agents."
            )}]}

        search_time = time.time() - start_time

        # Layer 2: Build context windows and extract with Haiku
        windows = build_context_windows(hits, max_conversations=max_results)

        if not windows:
            # Fallback to raw results
            fallback = format_embedding_fallback(hits, max_results)
            return {"content": [{"type": "text", "text": fallback}]}

        # Build Haiku extraction query — hint about keyword if present
        haiku_query = search_query or keyword
        if keyword and search_query:
            haiku_query = f'{search_query} (keyword: "{keyword}")'

        # Try Haiku extraction
        extraction = await extract_with_haiku(
            haiku_query, windows, keyword_hint=keyword if keyword else None,
        )

        total_time = time.time() - start_time

        if extraction and extraction.results:
            # Format structured results
            return _format_extraction_response(
                extraction, display_query, total_time, search_time,
                len(index.metadata), search_mode=search_mode,
            )
        else:
            # Fallback to raw embedding results
            logger.info("Haiku extraction returned no results, using fallback")
            fallback = format_embedding_fallback(hits, max_results)
            fallback += f"\n\n*{search_mode.title()} search completed in {total_time:.1f}s across {len(index.metadata)} indexed messages*"
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
    search_mode: str = "semantic",
) -> Dict[str, Any]:
    """Format the Haiku extraction response as readable markdown."""
    mode_label = {"semantic": "🔍 Semantic", "keyword": "🔑 Keyword", "hybrid": "🔀 Hybrid"}
    mode_str = mode_label.get(search_mode, search_mode.title())
    lines = [f'## Conversation Search: "{query}"\n']

    for i, result in enumerate(extraction.results, 1):
        date_str = f" ({result.date})" if result.date else ""
        lines.append(f"### {i}. {result.conversation_title}{date_str}")
        lines.append(f"*{result.relevance}*")
        lines.append("")

        for excerpt in result.excerpts:
            speaker = "**User**" if excerpt.speaker == "user" else "**Assistant**"
            lines.append(f"{speaker}:")
            lines.append(f"> {excerpt.text}")
            lines.append("")

    lines.append(
        f"*{mode_str} search — found {len(extraction.results)} relevant conversations. "
        f"Searched {index_size} messages in {search_time:.1f}s, "
        f"total: {total_time:.1f}s*"
    )

    return {"content": [{"type": "text", "text": "\n".join(lines)}]}


def _error(msg: str) -> Dict[str, Any]:
    """Return a standard error response."""
    return {"content": [{"type": "text", "text": msg}], "is_error": True}
