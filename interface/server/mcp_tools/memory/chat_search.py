"""
Chat History Search MCP Tool

Provides semantic search over raw conversation archives.
Two-layer pipeline:
  1. Qwen3 embedding search → top message-level hits grouped by conversation
  2. Haiku LLM extraction → structured selections (window/message index ranges)

Haiku returns message indices, not quoted text. The formatter splices verbatim
messages from the already-loaded context windows — zero paraphrasing, zero hallucination.

Falls back to raw embedding snippets if Haiku extraction fails.
"""

import calendar
import datetime
import json
import logging
import os
import re
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
HAIKU_MAX_TURNS = 50          # Safety valve only — effectively unlimited


# ── Pydantic models for Haiku structured output ──────────────────────────────

class Selection(BaseModel):
    """A message range selection from a context window."""
    model_config = {"extra": "ignore"}

    window_idx: int = Field(default=0, description="Index into the windows list")
    msg_start: int = Field(default=0, description="First message index (inclusive)")
    msg_end: int = Field(default=0, description="Last message index (inclusive)")


class ConversationResult(BaseModel):
    """Search result for a single conversation."""
    model_config = {"extra": "ignore"}

    conversation_id: str = Field(default="", description="Chat session ID")
    conversation_title: str = Field(default="", description="Chat title")
    date: str = Field(default="", description="Approximate date (YYYY-MM-DD) or empty string")
    relevance: str = Field(default="", description="Brief explanation of why this conversation is relevant")
    selections: List[Selection] = Field(default_factory=list, description="Message range selections from windows")


class ExtractionResponse(BaseModel):
    """Structured response from Haiku extraction."""
    model_config = {"extra": "ignore"}

    results: List[ConversationResult] = Field(
        default_factory=list,
        description="Relevant conversations with selections, ordered by relevance"
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

    # Check legacy JSONL projects
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
    """Load messages from a legacy JSONL chat file.

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
                "chat_agent": meta.chat_agent,
                "hit_count": 1,
            }
        else:
            if score > chat_groups[cid]["score"]:
                chat_groups[cid]["score"] = score
            chat_groups[cid]["match_indices"].append(meta.message_index)
            chat_groups[cid]["hit_count"] += 1

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
                        "chat_agent": group.get("chat_agent"),
                        "hit_count": group.get("hit_count", 1),
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


# ── Date query parsing ───────────────────────────────────────────────────────

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4,
    "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_date_query(date_query: str) -> Optional[Dict[str, float]]:
    """
    Parse a natural language date query into a timestamp range.

    Supports:
      today, yesterday, this week, last week, last N days,
      last N weeks, last N months, this month, last month,
      this year, last year, March 2025, January, 2025, Q1 2025

    Returns {"start": unix_ts, "end": unix_ts} or None if unparseable.
    Uses datetime and calendar only — no LLM.
    """
    q = date_query.strip().lower()
    if not q:
        return None

    now = datetime.datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now.replace(hour=23, minute=59, second=59, microsecond=0)

    # "today"
    if q == "today":
        return {"start": today_start.timestamp(), "end": today_end.timestamp()}

    # "yesterday"
    if q == "yesterday":
        yesterday = today_start - datetime.timedelta(days=1)
        yesterday_end = yesterday.replace(hour=23, minute=59, second=59)
        return {"start": yesterday.timestamp(), "end": yesterday_end.timestamp()}

    # "this week" — Monday of current week to now
    if q == "this week":
        monday = today_start - datetime.timedelta(days=today_start.weekday())
        return {"start": monday.timestamp(), "end": now.timestamp()}

    # "last week" — Monday to Sunday of previous week
    if q == "last week":
        this_monday = today_start - datetime.timedelta(days=today_start.weekday())
        last_monday = this_monday - datetime.timedelta(days=7)
        last_sunday = this_monday - datetime.timedelta(seconds=1)
        return {"start": last_monday.timestamp(), "end": last_sunday.timestamp()}

    # "this month"
    if q == "this month":
        month_start = today_start.replace(day=1)
        return {"start": month_start.timestamp(), "end": now.timestamp()}

    # "last month"
    if q == "last month":
        first_of_this = today_start.replace(day=1)
        last_of_prev = first_of_this - datetime.timedelta(days=1)
        first_of_prev = last_of_prev.replace(day=1, hour=0, minute=0, second=0)
        end_of_prev = last_of_prev.replace(hour=23, minute=59, second=59)
        return {"start": first_of_prev.timestamp(), "end": end_of_prev.timestamp()}

    # "this year"
    if q == "this year":
        year_start = today_start.replace(month=1, day=1)
        return {"start": year_start.timestamp(), "end": now.timestamp()}

    # "last year"
    if q == "last year":
        prev_year = now.year - 1
        year_start = datetime.datetime(prev_year, 1, 1)
        year_end = datetime.datetime(prev_year, 12, 31, 23, 59, 59)
        return {"start": year_start.timestamp(), "end": year_end.timestamp()}

    # "last N days"
    m = re.match(r"last\s+(\d+)\s+days?$", q)
    if m:
        n = int(m.group(1))
        start = today_start - datetime.timedelta(days=n)
        return {"start": start.timestamp(), "end": now.timestamp()}

    # "last N weeks"
    m = re.match(r"last\s+(\d+)\s+weeks?$", q)
    if m:
        n = int(m.group(1))
        start = today_start - datetime.timedelta(weeks=n)
        return {"start": start.timestamp(), "end": now.timestamp()}

    # "last N months" — approximate as N*30 days
    m = re.match(r"last\s+(\d+)\s+months?$", q)
    if m:
        n = int(m.group(1))
        start = today_start - datetime.timedelta(days=n * 30)
        return {"start": start.timestamp(), "end": now.timestamp()}

    # "Q1 2025", "Q2 2025", etc.
    m = re.match(r"q([1-4])\s+(\d{4})$", q)
    if m:
        quarter = int(m.group(1))
        year = int(m.group(2))
        quarter_months = {1: (1, 3), 2: (4, 6), 3: (7, 9), 4: (10, 12)}
        start_month, end_month = quarter_months[quarter]
        last_day = calendar.monthrange(year, end_month)[1]
        start = datetime.datetime(year, start_month, 1)
        end = datetime.datetime(year, end_month, last_day, 23, 59, 59)
        return {"start": start.timestamp(), "end": end.timestamp()}

    # "March 2025" — month name + year
    for month_name, month_num in _MONTH_NAMES.items():
        m = re.match(rf"^{month_name}\s+(\d{{4}})$", q)
        if m:
            year = int(m.group(1))
            last_day = calendar.monthrange(year, month_num)[1]
            start = datetime.datetime(year, month_num, 1)
            end = datetime.datetime(year, month_num, last_day, 23, 59, 59)
            return {"start": start.timestamp(), "end": end.timestamp()}

    # "January" — month name alone (current year)
    for month_name, month_num in _MONTH_NAMES.items():
        if q == month_name:
            year = now.year
            last_day = calendar.monthrange(year, month_num)[1]
            start = datetime.datetime(year, month_num, 1)
            end = datetime.datetime(year, month_num, last_day, 23, 59, 59)
            return {"start": start.timestamp(), "end": end.timestamp()}

    # "2025" — year alone
    m = re.match(r"^(\d{4})$", q)
    if m:
        year = int(m.group(1))
        start = datetime.datetime(year, 1, 1)
        end = datetime.datetime(year, 12, 31, 23, 59, 59)
        return {"start": start.timestamp(), "end": end.timestamp()}

    logger.warning(f"Could not parse date query: {date_query!r}")
    return None


# ── Haiku extraction ──────────────────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """You are a conversation search tool. Given a user's search query and excerpts from past conversations (with indexed headers), identify relevant message ranges.

Rules:
1. Return SELECTIONS as window_idx + message index ranges — do NOT quote or paraphrase text
2. Messages marked with ⭐ are search hits — prioritize selecting those and their surrounding context
3. Each selection should capture a coherent exchange (include enough context to be understandable)
4. Order results by relevance (most relevant first)
5. ONLY return an empty results list if the conversations have absolutely nothing to do with the query
6. Return valid, complete JSON — never truncate mid-string"""


EXTRACTION_SYSTEM_PROMPT_WITH_TOOLS = """You are a conversation search tool. Given a user's search query and excerpts from past conversations (with indexed headers), identify relevant message ranges.

Rules:
1. Return SELECTIONS as window_idx + message index ranges — do NOT quote or paraphrase text
2. Messages marked with ⭐ are search hits — prioritize selecting those and their surrounding context
3. Each selection should capture a coherent exchange (include enough context to be understandable)
4. Order results by relevance (most relevant first)
5. ONLY return an empty results list if the conversations have absolutely nothing to do with the query
6. Return valid, complete JSON — never truncate mid-string

You have tools to refine your search if the initial results don't fully answer the query. Use them SPARINGLY — only if the provided windows are clearly insufficient.

Available tools (include in "tool_calls" array if needed):
- refine_search: Run a new search with different terms. Args: {"query": "...", "keyword": "..."}
- expand_context: Load more messages around a specific window. Args: {"window_idx": N, "direction": "before"|"after"|"both", "count": 10}
- list_conversations: Browse available conversations. Args: {"date_start": "YYYY-MM-DD", "date_end": "YYYY-MM-DD"} (both optional)

If you need to use tools, return: {"tool_calls": [...], "results": []}
If results are sufficient, return: {"results": [...]}
Never mix non-empty results with tool_calls."""


def _build_window_blocks(windows: List[Dict[str, Any]]) -> str:
    """Build indexed window blocks for the Haiku prompt."""
    blocks = []
    for w_idx, w in enumerate(windows):
        lines = [f"[window_idx={w_idx}] ### Conversation: {w['title']} (ID: {w['chat_id']})"]

        ts = w.get("timestamp")
        if ts:
            try:
                dt = datetime.datetime.fromtimestamp(ts)
                lines.append(f"Date: {dt.strftime('%Y-%m-%d')}")
            except (OSError, ValueError):
                pass

        lines.append("")
        for m_idx, msg in enumerate(w["messages"]):
            role_label = "[USER]" if msg["role"] == "user" else "[ASSISTANT]"
            match_marker = " ⭐" if msg.get("is_match") else ""
            lines.append(f"[msg_idx={m_idx}] {role_label}{match_marker}: {msg['content']}")
            lines.append("")

        blocks.append("\n".join(lines))

    return "---\n".join(blocks)


def _build_extraction_prompt(
    query: str,
    windows: List[Dict[str, Any]],
    keyword_hint: Optional[str] = None,
    has_tools: bool = False,
) -> str:
    """Build the extraction prompt for Haiku."""
    keyword_note = ""
    if keyword_hint:
        keyword_note = f'\nNote: The user is specifically searching for the keyword "{keyword_hint}". Prioritize selections containing this exact term.\n'

    window_text = _build_window_blocks(windows)

    tool_call_schema = ""
    if has_tools:
        tool_call_schema = """,
    "tool_calls": [
        {"tool": "refine_search", "args": {"query": "new search terms", "keyword": "optional"}}
    ]"""

    return f"""Search query: "{query}"
{keyword_note}
Here are excerpts from past conversations that may be relevant:

---
{window_text}
---

Extract the most relevant conversations and message ranges that answer the search query.

Return ONLY a JSON object with this exact structure (no markdown, no explanation):
{{
  "results": [
    {{
      "conversation_id": "the chat session ID",
      "conversation_title": "the chat title",
      "date": "YYYY-MM-DD or empty string",
      "relevance": "brief explanation of why this is relevant",
      "selections": [
        {{"window_idx": 0, "msg_start": 2, "msg_end": 5}}
      ]
    }}
  ]{tool_call_schema}
}}"""


async def _call_haiku(prompt: str, system_prompt: str) -> Optional[str]:
    """Make a single small-model Codex call and return the result text."""
    try:
        from codex_backend import CodexRunOptions, ROOT_DIR, run_codex
    except Exception as import_err:
        logger.error(f"Codex backend import failed: {import_err}")
        return None

    try:
        result = await run_codex(
            CodexRunOptions(
                model="haiku",
                cwd=str(ROOT_DIR),
                identity_instructions=system_prompt,
                prompt=prompt,
                tools=[],
                timeout_seconds=120,
                max_turns=1,
            )
        )
        if result.returncode != 0:
            logger.warning(f"Small-model extraction error: {result.stderr}")
        return result.response or None
    except BaseException as e:
        logger.error(f"Small-model call failed ({type(e).__name__}): {e}", exc_info=True)
        return None


def _parse_haiku_json(result_text: str) -> Optional[Dict]:
    """Parse JSON from Haiku's text response, with repair for truncation."""
    if not result_text:
        return None

    json_str = result_text.strip()

    # Strip ```json ... ``` wrapper if present
    md_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', json_str, re.DOTALL)
    if md_match:
        json_str = md_match.group(1).strip()

    # Find JSON object in the response
    if not json_str.startswith('{'):
        start = json_str.find('{')
        end = json_str.rfind('}')
        if start >= 0 and end > start:
            json_str = json_str[start:end + 1]

    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json(json_str)
        if repaired:
            try:
                data = json.loads(repaired)
                logger.info("Repaired truncated JSON from Haiku response")
                return data
            except json.JSONDecodeError:
                pass

        logger.warning(f"Haiku returned invalid JSON. Response: {result_text[:500]}")
        return None


def _validate_selections(results: List[Dict], windows: List[Dict[str, Any]]) -> List[Dict]:
    """Validate and clamp selection indices to valid ranges, skip invalid window_idx."""
    for result in results:
        if "selections" not in result:
            result["selections"] = []
            continue

        valid_sels = []
        for sel in result["selections"]:
            w_idx = sel.get("window_idx", -1)

            # Skip invalid window index
            if w_idx < 0 or w_idx >= len(windows):
                logger.debug(f"Skipping selection with invalid window_idx={w_idx} (have {len(windows)} windows)")
                continue

            n_msgs = len(windows[w_idx]["messages"])
            if n_msgs == 0:
                continue

            # Clamp to valid range
            msg_start = max(0, min(sel.get("msg_start", 0), n_msgs - 1))
            msg_end = max(msg_start, min(sel.get("msg_end", msg_start), n_msgs - 1))

            valid_sels.append({
                "window_idx": w_idx,
                "msg_start": msg_start,
                "msg_end": msg_end,
            })

        result["selections"] = valid_sels

    return results


# ── Haiku inner tools (closures for multi-turn extraction) ───────────────────

def _execute_haiku_tool(
    tool_call: Dict,
    current_windows: List[Dict[str, Any]],
    index,
    date_range: Optional[Dict],
    agent_filter: Optional[set],
) -> List[Dict[str, Any]]:
    """Execute a Haiku inner tool and return new context windows (may be empty)."""
    tool_name = tool_call.get("tool", "")
    args = tool_call.get("args", {})

    try:
        if tool_name == "refine_search":
            return _tool_refine_search(args, index, date_range, agent_filter)
        elif tool_name == "expand_context":
            return _tool_expand_context(args, current_windows)
        elif tool_name == "list_conversations":
            return _tool_list_conversations(args, index, agent_filter)
        else:
            logger.warning(f"Unknown Haiku tool: {tool_name}")
            return []
    except Exception as e:
        logger.warning(f"Haiku tool '{tool_name}' failed: {e}")
        return []


def _tool_refine_search(
    args: Dict,
    index,
    date_range: Optional[Dict],
    agent_filter: Optional[set],
) -> List[Dict[str, Any]]:
    """Run a new embedding/keyword search and return new context windows."""
    from contextual_memory.chat_embedding_index import search, keyword_search, hybrid_search

    query = args.get("query", "").strip()
    keyword = args.get("keyword", "").strip()

    if not query and not keyword:
        return []

    k = 20
    if query and keyword:
        hits = hybrid_search(index, query, keyword, k=k, date_range=date_range, agent_filter=agent_filter)
    elif keyword:
        hits = keyword_search(index, keyword, k=k, date_range=date_range, agent_filter=agent_filter)
    else:
        hits = search(index, query, k=k, date_range=date_range, agent_filter=agent_filter)

    if not hits:
        return []

    # Smaller budget for refinement windows
    return build_context_windows(hits, max_conversations=5, max_total_tokens=10000)


def _tool_expand_context(
    args: Dict,
    current_windows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Load more messages around a specific window."""
    from contextual_memory.chat_embedding_index import strip_tool_markers

    w_idx = args.get("window_idx", -1)
    direction = args.get("direction", "both")
    count = min(args.get("count", 10), 20)  # Cap at 20

    if w_idx < 0 or w_idx >= len(current_windows):
        return []

    window = current_windows[w_idx]
    chat_id = window["chat_id"]

    found = _find_chat_file(chat_id)
    if not found:
        return []

    chat_path, chat_format = found

    try:
        if chat_format == "json":
            all_messages = _load_messages_json(chat_path)
        else:
            all_messages = _load_messages_jsonl(chat_path)

        if not all_messages:
            return []

        # Find approximate position of current window in the full message list
        # by matching the first message's content prefix
        current_msgs = window["messages"]
        if not current_msgs:
            return []

        first_content = current_msgs[0]["content"][:100]
        window_start = 0
        for i, msg in enumerate(all_messages):
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if isinstance(content, str) and first_content in strip_tool_markers(content):
                window_start = i
                break

        window_end = window_start + len(current_msgs)

        # Determine range to load based on direction
        if direction == "before":
            new_start = max(0, window_start - count)
            new_end = window_start
        elif direction == "after":
            new_start = window_end
            new_end = min(len(all_messages), window_end + count)
        else:  # both
            new_start = max(0, window_start - count)
            new_end = min(len(all_messages), window_end + count)

        new_messages = []
        for i in range(new_start, new_end):
            msg = all_messages[i]
            role = msg.get("role", "")
            if role not in ("user", "assistant"):
                continue
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

            # Truncate for token budget
            max_chars = int(MAX_TOKENS_PER_WINDOW / TOKENS_PER_CHAR)
            if len(cleaned) > max_chars:
                cleaned = cleaned[:max_chars] + "..."

            new_messages.append({
                "role": role,
                "content": cleaned,
                "is_match": False,
            })

        if new_messages:
            return [{
                "chat_id": chat_id,
                "title": window["title"] + f" (expanded {direction})",
                "score": window.get("score", 0),
                "timestamp": window.get("timestamp"),
                "messages": new_messages,
            }]

    except Exception as e:
        logger.warning(f"expand_context failed for window {w_idx}: {e}")

    return []


def _tool_list_conversations(
    args: Dict,
    index,
    agent_filter: Optional[set],
) -> List[Dict[str, Any]]:
    """Return a pseudo-window listing available conversations for browsing."""
    date_start = args.get("date_start", "")
    date_end = args.get("date_end", "")

    # Parse date strings if provided
    start_ts = 0.0
    end_ts = float("inf")
    if date_start:
        try:
            dt = datetime.datetime.strptime(date_start, "%Y-%m-%d")
            start_ts = dt.timestamp()
        except ValueError:
            pass
    if date_end:
        try:
            dt = datetime.datetime.strptime(date_end, "%Y-%m-%d")
            dt = dt.replace(hour=23, minute=59, second=59)
            end_ts = dt.timestamp()
        except ValueError:
            pass

    # Scan index metadata, group by conversation
    conversations: Dict[str, Dict] = {}
    for meta in index.metadata:
        if agent_filter and meta.chat_agent not in agent_filter:
            continue

        if meta.timestamp is not None:
            if meta.timestamp < start_ts or meta.timestamp > end_ts:
                continue

        cid = meta.chat_id
        if cid not in conversations:
            conversations[cid] = {
                "title": meta.chat_title,
                "date": "",
                "message_count": 0,
                "chat_id": cid,
            }
            if meta.timestamp:
                try:
                    conversations[cid]["date"] = datetime.datetime.fromtimestamp(
                        meta.timestamp
                    ).strftime("%Y-%m-%d")
                except (OSError, ValueError):
                    pass

        conversations[cid]["message_count"] += 1

    # Sort by date (most recent first), limit to 30
    sorted_convos = sorted(
        conversations.values(),
        key=lambda x: x.get("date", ""),
        reverse=True,
    )[:30]

    # Format as a pseudo-window for Haiku to read
    listing_lines = ["Available conversations:"]
    for c in sorted_convos:
        listing_lines.append(
            f"- [{c['chat_id']}] {c['title']} ({c['date']}, {c['message_count']} messages)"
        )

    return [{
        "chat_id": "__listing__",
        "title": "Conversation Listing",
        "score": 0,
        "timestamp": None,
        "messages": [{"role": "assistant", "content": "\n".join(listing_lines), "is_match": False}],
    }]


# ── Multi-turn extraction orchestrator ───────────────────────────────────────

async def extract_with_haiku(
    query: str,
    windows: List[Dict[str, Any]],
    keyword_hint: Optional[str] = None,
    index=None,
    date_range: Optional[Dict] = None,
    agent_filter: Optional[set] = None,
) -> Tuple[Optional[ExtractionResponse], List[Dict[str, Any]]]:
    """
    Call Haiku to extract structured results from context windows.

    Supports multi-turn with inner tools (refine_search, expand_context,
    list_conversations) when index is provided.

    Returns (extraction_response, final_windows) tuple.
    final_windows may be larger than input if Haiku tools added more context.
    """
    logger.info(
        f"[DIAG] extract_with_haiku ENTERED: query={query[:80]!r}, "
        f"windows={len(windows)}, keyword_hint={keyword_hint!r}"
    )

    has_tools = index is not None
    max_turns = HAIKU_MAX_TURNS if has_tools else 1
    system_prompt = EXTRACTION_SYSTEM_PROMPT_WITH_TOOLS if has_tools else EXTRACTION_SYSTEM_PROMPT

    all_windows = list(windows)  # Mutable copy — may grow with tool results

    for turn in range(max_turns):
        prompt = _build_extraction_prompt(
            query, all_windows, keyword_hint,
            has_tools=has_tools,
        )

        result_text = await _call_haiku(prompt, system_prompt)
        if not result_text:
            logger.warning(f"Haiku extraction returned empty on turn {turn}")
            return None, all_windows

        data = _parse_haiku_json(result_text)
        if data is None:
            return None, all_windows

        # Check for tool calls (multi-turn refinement)
        tool_calls = data.get("tool_calls", [])

        if tool_calls and has_tools:
            logger.info(f"Haiku requested {len(tool_calls)} tool call(s) on turn {turn}")
            for tc in tool_calls:
                new_windows = _execute_haiku_tool(tc, all_windows, index, date_range, agent_filter)
                if new_windows:
                    all_windows.extend(new_windows)
                    logger.info(
                        f"  Tool '{tc.get('tool')}' added {len(new_windows)} windows "
                        f"(total now: {len(all_windows)})"
                    )
            continue  # Next turn with expanded context

        # Parse final results
        if isinstance(data, dict) and "results" in data:
            # Validate and clamp selection indices
            data["results"] = _validate_selections(data.get("results", []), all_windows)

            # Ensure required fields have defaults
            for r in data["results"]:
                for field in ("conversation_id", "conversation_title", "relevance"):
                    if field not in r:
                        r[field] = ""

            try:
                extraction = ExtractionResponse.model_validate(data)
                return extraction, all_windows
            except Exception as e:
                logger.warning(f"Haiku extraction validation failed: {e}. Data: {json.dumps(data)[:500]}")
                return None, all_windows

        return None, all_windows

    # Exhausted all turns without final results
    logger.warning("Haiku exhausted all turns without returning final results")
    return None, all_windows


# ── Fallback formatting ──────────────────────────────────────────────────────

def format_embedding_fallback(
    hits: list,
    max_results: int = 5,
) -> str:
    """Format raw embedding search results as readable markdown (fallback path)."""
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
                "chat_agent": meta.chat_agent,
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

        agent_label = f" [{info['chat_agent']}]" if info.get("chat_agent") else ""
        lines.append(f"### {i}. {info['title']}{date_str}{agent_label}")
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
            "date_query": {
                "type": "string",
                "description": "Natural date filter: 'last week', 'last month', 'this year', 'March 2025', 'yesterday', 'last 3 days', 'January'. Takes precedence over date_range if both provided.",
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
                "description": (
                    "Filter by agent. Defaults to your own chats only. "
                    "Use 'all' to search across all agents, or specify an agent name. "
                    "Available agents: character, patch, kestrel, jack, ops, "
                    "ash, deep_think, research_critic, "
                    "life_admin, finance, running_coach, nutrition_coach, "
                    "lifting_coach, moltbook. "
                    "Legacy names (zeke_coder, information_gatherer, general_purpose, "
                    "chat_coder, chat_research, ren, zeke_research, coder, deep_research) "
                    "are automatically aliased."
                ),
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
        date_query = args.get("date_query", "").strip()
        agent_param = args.get("agent", "").strip()
        calling_agent = args.pop("_agent_name", None)

        if not search_query and not keyword:
            return _error("At least one of 'query' or 'keyword' is required.")

        start_time = time.time()

        # Parse date filter: date_query takes precedence over date_range
        date_range = None
        if date_query:
            date_range = _parse_date_query(date_query)
            if date_range is None:
                logger.warning(f"Could not parse date_query={date_query!r}, ignoring")
        elif date_range_raw:
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
            suggestions = []

            # Suggest widening agent scope
            if agent_filter:
                suggestions.append(f'Use `agent: "all"` to search across all agents (currently filtered to {agent_scope_label})')

            # Suggest changing search mode
            if search_mode == "semantic":
                suggestions.append('Try `keyword` search for exact term matching')
            elif search_mode == "keyword":
                suggestions.append('Try `query` (semantic search) for conceptual/topical matching')
            else:
                suggestions.append('Try keyword-only or semantic-only search separately')

            # Suggest removing date filter
            if date_range:
                suggestions.append('Remove the date filter to search all time periods')

            # Always suggest different terms
            suggestions.append('Try different or broader search terms')

            suggestion_text = "\n".join(f"- {s}" for s in suggestions)
            return {"content": [{"type": "text", "text": (
                f'No conversations found matching "{display_query}" ({mode_label} search).\n\n'
                f"**Suggestions:**\n{suggestion_text}"
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

        # Try Haiku extraction (multi-turn if index available)
        extraction, final_windows = await extract_with_haiku(
            haiku_query, windows,
            keyword_hint=keyword if keyword else None,
            index=index,
            date_range=date_range,
            agent_filter=agent_filter,
        )

        total_time = time.time() - start_time

        if extraction and extraction.results:
            # Format structured results with verbatim spliced messages
            return _format_extraction_response(
                extraction, final_windows, display_query, total_time, search_time,
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
    windows: List[Dict[str, Any]],
    query: str,
    total_time: float,
    search_time: float,
    index_size: int,
    search_mode: str = "semantic",
) -> Dict[str, Any]:
    """Format the Haiku extraction response as readable markdown.

    Splices verbatim messages from windows based on selection indices —
    no paraphrasing, no Haiku-generated quotes.
    """
    mode_label = {"semantic": "🔍 Semantic", "keyword": "🔑 Keyword", "hybrid": "🔀 Hybrid"}
    mode_str = mode_label.get(search_mode, search_mode.title())
    lines = [f'## Conversation Search: "{query}"\n']

    for i, result in enumerate(extraction.results, 1):
        date_str = f" ({result.date})" if result.date else ""

        # Find agent label from the window associated with first selection
        agent_label = ""
        if result.selections:
            first_sel = result.selections[0]
            if 0 <= first_sel.window_idx < len(windows):
                w_agent = windows[first_sel.window_idx].get("chat_agent")
                if w_agent:
                    agent_label = f" [{w_agent}]"

        lines.append(f"### {i}. {result.conversation_title}{date_str}{agent_label}")
        lines.append(f"*{result.relevance}*")
        lines.append("")

        # Splice verbatim messages from windows for each selection
        for sel in result.selections:
            if sel.window_idx < 0 or sel.window_idx >= len(windows):
                continue

            window = windows[sel.window_idx]
            msgs = window["messages"]
            start = max(0, sel.msg_start)
            end = min(len(msgs) - 1, sel.msg_end)

            for m_idx in range(start, end + 1):
                msg = msgs[m_idx]
                role_label = "**User**" if msg["role"] == "user" else "**Assistant**"
                lines.append(f"{role_label}:")
                # Format as blockquote, handling multiline content
                for content_line in msg["content"].split("\n"):
                    lines.append(f"> {content_line}")
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
