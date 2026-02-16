"""
Claude Agent SDK Wrapper for Second Brain Interface

Uses the official Python SDK with:
- Claude Code preset system prompt with CLAUDE.md appended
- Full tool access with bypassPermissions
- Session management (resume/fork)
- Streaming message support with partial events
"""

import os
import sys
import json
import asyncio
import logging
import uuid
import tempfile
from pathlib import Path
from typing import Optional, AsyncIterator, Dict, Any, List, Callable

from filelock import FileLock

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    UserMessage,
    SystemMessage,
    ResultMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ThinkingBlock,
)
from claude_agent_sdk.types import StreamEvent

logger = logging.getLogger(__name__)

# Import custom MCP tools
try:
    from mcp_tools import (
        create_mcp_server,
        create_second_brain_tools,
        get_mcp_tools_for_categories,
        ALL_MCP_TOOLS,
        MCP_PREFIX,
    )
    # Note: We no longer create a global server here.
    # Instead, we create per-agent servers dynamically in _build_options()
    # to ensure proper tool filtering based on agent_config.yaml
    logger.info("MCP tools module loaded successfully")
except Exception as e:
    logger.warning(f"Could not load Second Brain MCP tools: {e}")
    create_mcp_server = None
    create_second_brain_tools = None
    get_mcp_tools_for_categories = None
    ALL_MCP_TOOLS = set()
    MCP_PREFIX = "mcp__brain__"


# All native Claude Code tools that can be controlled
ALL_NATIVE_TOOLS = {
    # File operations
    "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit",
    # Execution
    "Bash", "Task", "TaskOutput", "KillShell",
    # Web
    "WebFetch", "WebSearch",
    # UI/Interaction
    "AskUserQuestion", "Skill", "TodoWrite",
    # Plan mode
    "EnterPlanMode", "ExitPlanMode",
}


# Lock to prevent concurrent CLAUDE.md swaps between agents.
# When a non-primary agent launches with setting_sources=["project"],
# we temporarily write a minimal CLAUDE.md to prevent identity bleed
# from Ren's content. This lock serializes those swaps.
_claude_md_lock = asyncio.Lock()


def _load_primary_agent_config(cwd: str) -> tuple[list, list, list]:
    """
    Load primary agent configuration from .claude/agents/ren/config.yaml.

    Returns:
        Tuple of (mcp_tool_names, internal_tool_names, disallowed_native_tools)
        - mcp_tool_names: e.g., ["mcp__brain__google_list", ...]
        - internal_tool_names: e.g., ["google_list", ...]
        - disallowed_native_tools: e.g., ["WebSearch", "Bash", ...]

    Falls back to legacy agent_config.yaml if new config doesn't exist.
    """
    import yaml

    # Try new unified config location first
    new_config_path = Path(cwd) / ".claude" / "agents" / "ren" / "config.yaml"
    legacy_config_path = Path(cwd) / ".claude" / "agent_config.yaml"

    config_path = None
    if new_config_path.exists():
        config_path = new_config_path
        logger.info("Loading primary agent config from new location: agents/ren/config.yaml")
    elif legacy_config_path.exists():
        config_path = legacy_config_path
        logger.warning("Using legacy agent_config.yaml - consider migrating to agents/ren/")
    else:
        logger.info("No primary agent config found, using all tools")
        mcp_tools = list(ALL_MCP_TOOLS)
        internal_tools = [t.replace(MCP_PREFIX, "") for t in mcp_tools]
        return mcp_tools, internal_tools, ["WebSearch"]  # Default: only disable WebSearch

    try:
        with open(config_path) as f:
            config = yaml.safe_load(f)

        if not config:
            logger.warning("Empty config file, using all tools")
            mcp_tools = list(ALL_MCP_TOOLS)
            internal_tools = [t.replace(MCP_PREFIX, "") for t in mcp_tools]
            return mcp_tools, internal_tools, ["WebSearch"]

        # === Native tools configuration ===
        disallowed_native = []
        if "native_tools" in config:
            # Explicit allowlist provided - compute disallowed as the difference
            allowed_native = set(config.get("native_tools", []))
            disallowed_native = list(ALL_NATIVE_TOOLS - allowed_native)
            logger.info(f"Native tools: {len(allowed_native)} allowed, {len(disallowed_native)} disallowed")
            if disallowed_native:
                logger.info(f"Disallowed native tools: {sorted(disallowed_native)}")
        else:
            # No native_tools section - only disable WebSearch (current default behavior)
            disallowed_native = ["WebSearch"]
            logger.info("No native_tools config, using default (WebSearch disabled)")

        # === MCP tools configuration ===
        # Flat list format: mcp_tools is a list of MCP tool names (e.g., ["mcp__brain__foo", ...])
        # Structured format: mcp_tools is a dict with categories/include/exclude
        # Legacy format: tools section
        mcp_config = config.get("mcp_tools") or config.get("tools")

        if not mcp_config:
            logger.info("No MCP tools config, using all MCP tools")
            mcp_tools = list(ALL_MCP_TOOLS)
            internal_tools = [t.replace(MCP_PREFIX, "") for t in mcp_tools]
            return mcp_tools, internal_tools, disallowed_native

        allowed = set()

        if isinstance(mcp_config, list):
            # Flat list format — each entry is an MCP tool name (mcp__brain__...)
            # or a bare tool name that needs the prefix
            for tool in mcp_config:
                if tool.startswith(MCP_PREFIX):
                    allowed.add(tool)
                else:
                    allowed.add(f"{MCP_PREFIX}{tool}")
            logger.info(f"Loaded {len(allowed)} MCP tools from flat list")
        else:
            # Structured dict format with categories/include/exclude
            # Add tools from categories
            categories = mcp_config.get("categories", [])
            if categories and get_mcp_tools_for_categories:
                category_tools = get_mcp_tools_for_categories(categories)
                allowed.update(category_tools)
                logger.info(f"Loaded {len(category_tools)} MCP tools from categories: {categories}")

            # Add explicitly included tools
            include = mcp_config.get("include", [])
            if include:
                allowed.update(include)
                logger.info(f"Added {len(include)} explicitly included MCP tools")

            # Remove excluded tools
            exclude = mcp_config.get("exclude", [])
            if exclude:
                allowed -= set(exclude)
                logger.info(f"Excluded {len(exclude)} MCP tools")

        mcp_tool_names = list(allowed)
        internal_tool_names = [t.replace(MCP_PREFIX, "") for t in mcp_tool_names]

        # Log detailed info for debugging
        logger.info(f"Final MCP tools count: {len(mcp_tool_names)}")

        # Log what MCP tools are EXCLUDED
        all_internal = {t.replace(MCP_PREFIX, "") for t in ALL_MCP_TOOLS}
        excluded_internal = all_internal - set(internal_tool_names)
        if excluded_internal:
            logger.info(f"Excluded MCP tools: {sorted(excluded_internal)}")

        return mcp_tool_names, internal_tool_names, disallowed_native

    except Exception as e:
        logger.error(f"Error loading primary agent config: {e}")
        mcp_tools = list(ALL_MCP_TOOLS)
        internal_tools = [t.replace(MCP_PREFIX, "") for t in mcp_tools]
        return mcp_tools, internal_tools, ["WebSearch"]


class MessageInjectionQueue:
    """
    Async queue for mid-stream message injection.

    This allows sending new user messages WHILE Claude is working.
    Messages are injected into the prompt stream and Claude sees them
    at the next processing point.
    """

    def __init__(self):
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()
        self._closed = False
        self._initial_content = None  # str or list of content blocks
        self._initial_sent = False

    def set_initial_prompt(self, prompt):
        """Set the initial prompt to be yielded first.

        Args:
            prompt: Either a string or a list of content blocks (for multimodal messages).
                    Content blocks follow the Anthropic API format:
                    [{"type": "text", "text": "..."}, {"type": "image", "source": {...}}]
        """
        self._initial_content = prompt
        self._initial_sent = False

    async def inject(self, content: str, msg_id: Optional[str] = None):
        """Inject a new user message into the stream."""
        if self._closed:
            logger.warning("Cannot inject message - queue is closed")
            return False

        message = {
            "type": "user",
            "message": {
                "role": "user",
                "content": content
            }
        }
        if msg_id:
            message["message"]["id"] = msg_id

        await self._queue.put(message)
        logger.info(f"Injected message into stream: {str(content)[:50]}...")
        return True

    def close(self):
        """Close the queue - no more messages can be injected."""
        self._closed = True

    async def __aiter__(self):
        """Async iterator that yields messages for the SDK."""
        # First, yield the initial prompt (string or structured content blocks)
        if self._initial_content and not self._initial_sent:
            self._initial_sent = True
            yield {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": self._initial_content
                }
            }

        # Then yield any injected messages
        while not self._closed:
            try:
                # Use a timeout so we can check if closed
                message = await asyncio.wait_for(self._queue.get(), timeout=0.5)
                yield message
            except asyncio.TimeoutError:
                # No message ready, continue waiting
                continue
            except Exception as e:
                logger.warning(f"Error getting message from queue: {e}")
                break


class ClaudeWrapper:
    """Async wrapper for Claude Agent SDK with streaming support."""

    # HARD LIMIT: Must stay under Linux MAX_ARG_STRLEN (131,072 bytes / 128KB).
    # SDK's _SINGLE_ARG_LENGTH_LIMIT raised to 130,000 to avoid broken shell wrapper.
    # We cap at 125KB to leave headroom for CLI arg encoding overhead.
    _MAX_SYSTEM_PROMPT_BYTES = 125_000

    def __init__(self, session_id: str, cwd: str, chat_id: Optional[str] = None, chat_messages: Optional[List[Dict[str, Any]]] = None):
        self.session_id = session_id
        self.cwd = cwd
        self.chat_id = chat_id  # Storage chat ID for same-session LTM filtering
        self.chat_messages = chat_messages or []  # For compaction detection
        self.client: Optional[ClaudeSDKClient] = None
        self._current_session_id: Optional[str] = None
        self._conversation_history: List[Dict[str, Any]] = []
        # Message injection queue for mid-stream messages
        self._injection_queue: Optional[MessageInjectionQueue] = None

    @staticmethod
    def _truncate_to_byte_limit(text: str, max_bytes: int, label: str = "content") -> str:
        """Truncate text to fit within a byte budget, cutting at clean boundaries.

        Tries paragraph break first, then line break, then raw byte cut.
        Always produces valid UTF-8 output.
        """
        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text

        marker = "\n\n[... truncated due to size limit ...]\n"
        marker_bytes = len(marker.encode("utf-8"))
        target = max_bytes - marker_bytes
        if target <= 0:
            return marker.strip()

        # Decode back safely at the byte boundary (ignore trailing partial chars)
        truncated = encoded[:target].decode("utf-8", errors="ignore")

        # Try to cut at a paragraph boundary
        para_idx = truncated.rfind("\n\n")
        if para_idx > len(truncated) // 2:
            truncated = truncated[:para_idx]
        else:
            # Try a line boundary
            line_idx = truncated.rfind("\n")
            if line_idx > len(truncated) // 2:
                truncated = truncated[:line_idx]

        original_kb = len(encoded) / 1024
        truncated_kb = len(truncated.encode("utf-8")) / 1024
        logger.warning(
            f"Truncated {label}: {original_kb:.1f}KB -> {truncated_kb:.1f}KB "
            f"(limit {max_bytes / 1024:.1f}KB)"
        )
        return truncated + marker

    def _refresh_claude_md(self) -> None:
        """
        Write .claude/CLAUDE.md with static content (prompt.md + memory.md).

        CLAUDE.md is loaded from disk by the CLI via setting_sources=["project"].
        This channel has NO size limit (unlike CLI args which cap at 128KB).
        Only the primary agent loads CLAUDE.md — subagents either omit
        setting_sources or set it to [].

        Called before each query so memory.md changes are picked up.
        """
        claude_md_path = Path(self.cwd) / ".claude" / "CLAUDE.md"
        sections = []

        # ── 1. Primary agent instructions (prompt.md) ──
        prompt_path = Path(self.cwd) / ".claude" / "agents" / "ren" / "prompt.md"
        if prompt_path.exists():
            try:
                sections.append(prompt_path.read_text())
                logger.info(f"CLAUDE.md: loaded prompt.md ({prompt_path.stat().st_size} bytes)")
            except Exception as e:
                logger.warning(f"CLAUDE.md: could not read prompt.md: {e}")

        # ── 2. Self-journal (memory.md) ──
        memory_path = Path(self.cwd) / ".claude" / "memory.md"
        if memory_path.exists():
            try:
                memory_preamble = (
                    "Your permanent self-journal. You write here to remember things\n"
                    "across all conversations — preferences, lessons learned, operating\n"
                    "rules, and facts that must always be available regardless of topic.\n"
                    "Update or remove entries as things change."
                )
                memory_content = memory_path.read_text()
                sections.append(f"\n---\n\n{memory_preamble}\n\n{memory_content}")
                logger.info(f"CLAUDE.md: loaded memory.md ({memory_path.stat().st_size} bytes)")
            except Exception as e:
                logger.warning(f"CLAUDE.md: could not read memory.md: {e}")

        if sections:
            combined = "\n".join(sections)
            try:
                # Atomic write: write to temp file then rename to prevent partial reads
                # during concurrent access from multiple chat sessions
                fd, tmp_path = tempfile.mkstemp(
                    dir=str(claude_md_path.parent),
                    prefix=".CLAUDE.md.",
                    suffix=".tmp"
                )
                try:
                    with os.fdopen(fd, 'w') as f:
                        f.write(combined)
                    os.rename(tmp_path, str(claude_md_path))
                except Exception:
                    # Clean up temp file on failure
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass
                    raise
                logger.info(f"CLAUDE.md: wrote {len(combined)} bytes atomically to {claude_md_path}")
            except Exception as e:
                logger.warning(f"CLAUDE.md: could not write: {e}")

    def _write_agent_claude_md(self, agent_name: str) -> None:
        """
        Write an agent-scoped CLAUDE.md for non-primary agents.

        When a non-primary agent uses setting_sources=["project"] (for Skill
        discovery), the SDK also loads .claude/CLAUDE.md. Normally that file
        contains Ren's personality and memory, causing identity bleed.

        This method overwrites CLAUDE.md with an agent-scoped version that
        includes only:
        1. A directive pointing the agent to its system prompt
        2. The agent's own memory.md (if it exists)

        This prevents identity bleed while giving each agent its own
        persistent memory that survives across conversations.
        """
        claude_md_path = Path(self.cwd) / ".claude" / "CLAUDE.md"

        sections = [
            f"# Agent: {agent_name}\n\n"
            "Your system instructions are provided via the system prompt.\n"
            "Follow only those instructions.\n"
        ]

        # Include per-agent memory.md if it exists
        memory_path = Path(self.cwd) / ".claude" / "agents" / agent_name / "memory.md"
        if memory_path.exists():
            try:
                memory_content = memory_path.read_text().strip()
                if memory_content:
                    sections.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{memory_content}"
                    )
                    logger.info(f"CLAUDE.md: included memory.md for agent '{agent_name}' ({memory_path.stat().st_size} bytes)")
            except Exception as e:
                logger.warning(f"CLAUDE.md: could not read memory.md for agent '{agent_name}': {e}")

        content = "".join(sections)
        try:
            fd, tmp_path = tempfile.mkstemp(
                dir=str(claude_md_path.parent),
                prefix=".CLAUDE.md.",
                suffix=".tmp"
            )
            try:
                with os.fdopen(fd, 'w') as f:
                    f.write(content)
                os.rename(tmp_path, str(claude_md_path))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
            logger.info(f"CLAUDE.md: wrote minimal stub for agent '{agent_name}'")
        except Exception as e:
            logger.warning(f"CLAUDE.md: could not write agent stub: {e}")

    async def _build_system_prompt(self, user_query: Optional[str] = None) -> str:
        """
        Build the dynamic system prompt (working memory + semantic LTM only).

        Static content (prompt.md, memory.md) lives in CLAUDE.md, loaded from
        disk by the CLI — no size limit. This system prompt only carries the
        per-query dynamic context, giving semantic LTM nearly the full 125KB
        CLI arg budget.

        Budget breakdown:
          CLAUDE.md (disk, unlimited): prompt.md (~9KB) + memory.md (~35KB)
          System prompt (CLI arg, 125KB):
            1. Working memory   (~2KB, reserves 5KB for LTM)
            2. Recent memory    (conversation threads from last 24h, capped at 20KB)
            3. Semantic LTM     (gets remaining budget, excludes recent threads)
        """
        MAX_BYTES = self._MAX_SYSTEM_PROMPT_BYTES

        def _byte_len(s: str) -> int:
            return len(s.encode("utf-8"))

        # Refresh CLAUDE.md with latest static content before the CLI reads it
        self._refresh_claude_md()

        # ── 1. Working memory ──
        system_prompt = ""
        current_bytes = 0
        LTM_RESERVE = 5_000

        try:
            import sys
            scripts_dir = Path(self.cwd) / ".claude" / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from working_memory import get_store
            store = get_store()
            wm_block = store.format_prompt_block()
            if wm_block:
                wm_budget = MAX_BYTES - current_bytes - LTM_RESERVE
                if wm_budget > 0:
                    wm_block = self._truncate_to_byte_limit(wm_block, wm_budget, label="working memory")
                    system_prompt += f"<working-memory>\n{wm_block}\n</working-memory>\n\n"
                    current_bytes = _byte_len(system_prompt)
                    logger.info(f"Injected working memory ({len(store.list_items())} items), {current_bytes} bytes")
                else:
                    logger.warning(f"No budget for working memory (cumulative {current_bytes} bytes)")
        except Exception as e:
            logger.debug(f"Could not inject working memory: {e}")

        # ── Session filtering (shared by recent memory + semantic LTM) ──
        exclude_session_id = self.chat_id if self.chat_id and self.chat_id != "new" else None
        session_uncompacted_after = None

        if exclude_session_id and self.chat_messages:
            for msg in self.chat_messages:
                if msg.get("role") == "compacted":
                    compacted_at = msg.get("compacted_at")
                    if compacted_at:
                        session_uncompacted_after = compacted_at
                        logger.info(
                            f"Session {exclude_session_id} has compaction at {compacted_at}, "
                            f"only filtering atoms created after this"
                        )

        if exclude_session_id:
            logger.info(f"Session filter: excluding atoms from session {exclude_session_id}"
                       + (f" (after {session_uncompacted_after})" if session_uncompacted_after else ""))

        # ── 2. Recent memory (conversation continuity, last 24h) ──
        recent_thread_ids = set()
        if user_query:
            try:
                import sys
                ltm_scripts = Path(self.cwd) / ".claude" / "scripts" / "ltm"
                if str(ltm_scripts) not in sys.path:
                    sys.path.insert(0, str(ltm_scripts))
                from memory_retrieval import get_recent_conversation_threads, format_recent_memory

                RECENT_MEMORY_CAP = 20_000  # 20KB cap
                recent_budget_bytes = min(RECENT_MEMORY_CAP, MAX_BYTES - current_bytes - LTM_RESERVE)

                if recent_budget_bytes > 1000:
                    recent_token_budget = max(0, (recent_budget_bytes - 200) // 5)
                    recent_threads, recent_thread_ids, recent_tokens = get_recent_conversation_threads(
                        hours=24,
                        token_budget=recent_token_budget,
                        exclude_room_id=self.chat_id if self.chat_id and self.chat_id != "new" else None,
                        exclude_session_id=exclude_session_id,
                        session_uncompacted_after=session_uncompacted_after
                    )

                    if recent_threads:
                        formatted = format_recent_memory(recent_threads, hours=24)
                        if formatted and len(formatted) > 50:
                            formatted = self._truncate_to_byte_limit(
                                formatted, recent_budget_bytes - 100, label="recent memory"
                            )
                            system_prompt += f"<recent-memory>\n{formatted}\n</recent-memory>\n\n"
                            current_bytes = _byte_len(system_prompt)
                            logger.info(
                                f"Injected recent memory: {len(recent_threads)} conversation threads, "
                                f"cumulative={current_bytes} bytes"
                            )
            except Exception as e:
                logger.warning(f"Could not inject recent memory: {e}")
                recent_thread_ids = set()

        # ── 3. Semantic LTM (gets all remaining budget, excludes recent threads) ──
        if user_query:
            try:
                ltm_scripts = Path(self.cwd) / ".claude" / "scripts" / "ltm"
                if str(ltm_scripts) not in sys.path:
                    sys.path.insert(0, str(ltm_scripts))
                from memory_retrieval import get_memory_context, MemoryContext
                from query_rewriter import rewrite_query

                remaining_bytes = MAX_BYTES - current_bytes
                # ~5 bytes per token for formatted output to prevent overshoot
                ltm_token_budget = max(0, (remaining_bytes - 200) // 5)

                if ltm_token_budget < 500:
                    logger.warning(f"Skipping semantic LTM: only {remaining_bytes} bytes remaining ({ltm_token_budget} tokens)")
                else:
                    logger.info(f"Semantic LTM budget: {remaining_bytes} bytes -> {ltm_token_budget} tokens")

                    # Rewrite query for better semantic search
                    rewritten = await rewrite_query(
                        user_message=user_query,
                        conversation_context=self._conversation_history
                    )

                    # Run multiple queries with weighted budget allocation
                    all_memories = []
                    seen_atom_ids = set()
                    all_threads = []
                    seen_thread_ids = set()

                    # Allocate budget proportional to query weights
                    total_weight = sum(q.weight for q in rewritten.queries) or 1.0

                    for q in rewritten.queries:
                        budget_for_query = int(ltm_token_budget * (q.weight / total_weight))
                        if budget_for_query < 100:
                            continue

                        context = get_memory_context(
                            q.text,
                            token_budget=budget_for_query,
                            recency_weight=q.recency_bias,
                            exclude_session_id=exclude_session_id,
                            session_uncompacted_after=session_uncompacted_after,
                            exclude_thread_ids=recent_thread_ids
                        )

                        # Dedupe atoms across queries
                        for mem in context.atomic_memories:
                            mem_id = mem.get('id', str(mem.get('content', '')))
                            if mem_id not in seen_atom_ids:
                                all_memories.append(mem)
                                seen_atom_ids.add(mem_id)

                        # Dedupe threads across queries
                        for thread in context.threads:
                            tid = thread.get('id')
                            if tid and tid not in seen_thread_ids:
                                all_threads.append(thread)
                                seen_thread_ids.add(tid)

                    if all_memories or all_threads:
                        merged = MemoryContext(
                            atomic_memories=all_memories[:100],
                            threads=all_threads,
                            total_tokens=0,
                            token_breakdown={}
                        )
                        formatted = merged.format_for_prompt()

                        if formatted and len(formatted) > 50:
                            formatted = self._truncate_to_byte_limit(
                                formatted, remaining_bytes - 100, label="semantic LTM"
                            )
                            system_prompt += f"<semantic-memory>\n{formatted}\n</semantic-memory>\n"
                            current_bytes = _byte_len(system_prompt)
                            query_summary = [(q.text, f"w={q.weight}", f"r={q.recency_bias}") for q in rewritten.queries]
                            logger.info(
                                f"Injected semantic LTM via rewritten queries {query_summary} "
                                f"(global_recency={rewritten.global_recency_weight}): "
                                f"{len(all_threads)} threads, {len(all_memories)} atoms, "
                                f"cumulative={current_bytes} bytes"
                            )
            except Exception as e:
                logger.warning(f"Could not inject semantic LTM: {e}")

        # ── Final safety check ──
        final_bytes = _byte_len(system_prompt)
        if final_bytes > MAX_BYTES:
            logger.error(f"System prompt {final_bytes} bytes exceeds limit {MAX_BYTES}, hard-truncating!")
            system_prompt = self._truncate_to_byte_limit(system_prompt, MAX_BYTES, label="final system prompt")
            final_bytes = _byte_len(system_prompt)

        logger.info(
            f"Final system prompt: {final_bytes} bytes ({final_bytes / 1024:.1f}KB, "
            f"{final_bytes * 100 // MAX_BYTES}% of {MAX_BYTES // 1024}KB limit) "
            f"[static content in CLAUDE.md]"
        )

        return system_prompt

    async def _build_options(self, user_query: Optional[str] = None) -> ClaudeAgentOptions:
        """Build SDK options with proper system prompt and settings."""

        # Build minimal system prompt (CLAUDE.md loaded via setting_sources)
        # Pass user_query for semantic memory retrieval
        system_prompt = await self._build_system_prompt(user_query=user_query)

        # Build MCP servers config with FILTERED tools based on primary agent config
        # This is the PRIMARY enforcement mechanism for tool exclusions
        mcp_servers = {}
        mcp_tool_names = []
        disallowed_native_tools = ["WebSearch"]  # Default fallback

        if create_mcp_server:
            # Load tool config - returns MCP names, internal names, and disallowed native tools
            mcp_tool_names, internal_tool_names, disallowed_native_tools = _load_primary_agent_config(self.cwd)

            if internal_tool_names:
                # Create MCP server with ONLY the allowed tools
                # This ensures excluded tools (like LTM for primary agent) literally don't exist
                # Pass chat_id for concurrent session support (tools get chat context via closure)
                filtered_mcp_server = create_mcp_server(
                    name="brain",  # Must match the key in mcp_servers dict
                    include_tools=internal_tool_names,
                    chat_id=self.chat_id,
                )
                mcp_servers["brain"] = filtered_mcp_server
                logger.info(f"Created filtered MCP server with {len(internal_tool_names)} tools")
            else:
                logger.warning("No MCP tools allowed, MCP server not created")

        options = ClaudeAgentOptions(
            # Use latest Claude Opus (auto-updates to newest version)
            model="opus",

            # Adaptive thinking (Opus 4.6 default): Claude dynamically decides
            # when and how much to think. Effort defaults to "high" when omitted.
            # Removed deprecated max_thinking_tokens/budget_tokens — see:
            # https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking

            # Dynamic system prompt: only working memory + semantic LTM.
            # Static content (prompt.md, memory.md) is in CLAUDE.md, loaded from
            # disk via setting_sources — no CLI arg size limit.
            system_prompt=system_prompt,

            # Use Claude Code's tools (schemas injected separately from system prompt)
            tools={"type": "preset", "preset": "claude_code"},

            # Disable native tools based on config (computed from native_tools allowlist)
            disallowed_tools=disallowed_native_tools,

            # Load project settings (ONLY the primary agent loads these):
            # - CLAUDE.md: personality (prompt.md) + self-journal (memory.md)
            # - Skills from .claude/skills/
            # Subagents either omit setting_sources or set it to [] — they
            # never see CLAUDE.md, which is how we scope it to primary only.
            setting_sources=["project"],

            # Custom MCP tools for Second Brain functionality
            # Server is created with only allowed tools (filtered at creation time)
            mcp_servers=mcp_servers if mcp_servers else None,

            # Also specify allowed_tools as a secondary safeguard
            # This acts as defense-in-depth in case SDK behavior changes
            # Include "Skill" so the primary agent can invoke skills from .claude/skills/
            allowed_tools=(mcp_tool_names + ["Skill"]) if mcp_servers else ["Skill"],

            # Bypass all permission prompts - this VM is ours
            permission_mode="bypassPermissions",

            # Working directory
            cwd=self.cwd,

            # Session management: always fresh sessions, never resume.
            # Conversation history is injected into the prompt by the caller.
            # This eliminates "session expired" errors and ensures Claude
            # always sees the same prompt format regardless of code path.

            # Enable partial message streaming for better UX
            include_partial_messages=True,
        )

        return options

    async def inject_message(self, content: str, msg_id: Optional[str] = None) -> bool:
        """
        Inject a user message into the active stream.

        This allows mid-stream corrections/additions while Claude is working.
        The message is queued and Claude sees it at the next processing point.

        Args:
            content: The message content to inject
            msg_id: Optional message ID for tracking

        Returns:
            True if injection was successful, False otherwise
        """
        if not self._injection_queue:
            logger.warning("No active injection queue - cannot inject message")
            return False

        return await self._injection_queue.inject(content, msg_id)

    def get_injection_queue(self) -> Optional[MessageInjectionQueue]:
        """Get the current injection queue for external message injection."""
        return self._injection_queue

    async def run_prompt(
        self,
        prompt,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        use_streaming_input: bool = True
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Execute a prompt through Claude SDK and stream results.

        Always starts a fresh SDK session. Conversation context should be
        injected into the prompt by the caller (via _build_history_context).

        Args:
            prompt: The user's message - either a string or a list of content blocks
                    (for multimodal messages with images). Content blocks follow the
                    Anthropic API format: [{"type": "text", "text": "..."}, {"type": "image", ...}]
            conversation_history: Optional list of previous messages for query rewriter
            use_streaming_input: If True, use AsyncIterable prompt for mid-stream injection

        Yields:
            Event dictionaries with types: session_init, content, content_delta,
            tool_start, tool_end, thinking, status, error, result_meta
        """

        # Store conversation history for query rewriter
        self._conversation_history = conversation_history or []

        # Extract text content for query rewriting and semantic memory search
        # When prompt is structured content blocks, extract just the text parts
        if isinstance(prompt, list):
            text_parts = [block.get("text", "") for block in prompt if block.get("type") == "text"]
            query_text = "\n".join(text_parts)
        else:
            query_text = prompt

        options = await self._build_options(user_query=query_text)

        logger.info(f"Running Claude SDK: fresh session, streaming_input={use_streaming_input}")

        try:
            self.client = ClaudeSDKClient(options=options)

            # Create injection queue for mid-stream message injection
            if use_streaming_input:
                self._injection_queue = MessageInjectionQueue()
                self._injection_queue.set_initial_prompt(prompt)
                # Pass AsyncIterable to connect() - it runs stream_input() in
                # a background task via start_soon(), allowing receive_messages()
                # to work concurrently. Do NOT use query() with an AsyncIterable
                # because query() iterates it synchronously and blocks forever.
                await self.client.connect(self._injection_queue)
                logger.info("Started query with streaming input mode (mid-stream injection enabled)")
            else:
                # Standard mode - connect first, then send the prompt string
                await self.client.connect()
                await self.client.query(prompt)
                logger.info("Started query with standard mode")

            active_tools: Dict[str, str] = {}  # tool_id -> tool_name

            # Stream responses
            async for message in self.client.receive_messages():
                # DEBUG: Log message type
                logger.info(f"DEBUG SDK MESSAGE TYPE: {type(message).__name__}")

                # Handle streaming events (partial content)
                if isinstance(message, StreamEvent):
                    event = message.event
                    event_type = event.get("type", "")

                    # Content block delta - streaming text
                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        delta_type = delta.get("type", "")

                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield {"type": "content_delta", "text": text}

                        elif delta_type == "thinking_delta":
                            # Extended thinking content
                            thinking = delta.get("thinking", "")
                            if thinking:
                                yield {"type": "thinking_delta", "text": thinking}

                    # Content block start - tool use beginning
                    elif event_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "tool")
                            tool_id = block.get("id")
                            if tool_id:
                                active_tools[tool_id] = tool_name
                            yield {
                                "type": "tool_start",
                                "name": tool_name,
                                "id": tool_id
                            }

                # Handle system messages (includes session init)
                elif isinstance(message, SystemMessage):
                    if message.subtype == "init":
                        session_id = message.data.get("session_id", str(uuid.uuid4()))
                        self._current_session_id = session_id
                        yield {"type": "session_init", "id": session_id}

                # Handle assistant messages (complete content blocks)
                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            # Complete text block (may come after deltas)
                            yield {"type": "content", "text": block.text}

                        elif isinstance(block, ThinkingBlock):
                            # Complete thinking block
                            yield {"type": "thinking", "text": block.thinking}

                        elif isinstance(block, ToolUseBlock):
                            active_tools[block.id] = block.name
                            yield {
                                "type": "tool_use",
                                "name": block.name,
                                "id": block.id,
                                "args": json.dumps(block.input) if block.input else "{}"
                            }

                        elif isinstance(block, ToolResultBlock):
                            # Tool result - look up name from active_tools dict
                            resolved_name = active_tools.pop(block.tool_use_id, "tool")
                            logger.info(f"DEBUG: ToolResultBlock detected for {resolved_name}, id={block.tool_use_id}")
                            content = block.content
                            if isinstance(content, list):
                                content = "\n".join(
                                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                                    for c in content
                                )
                            yield {
                                "type": "tool_end",
                                "name": resolved_name,
                                "id": block.tool_use_id,
                                "output": str(content)[:2000] if content else "",
                                "is_error": block.is_error or False
                            }

                # Handle user messages (usually tool results injected by SDK)
                elif isinstance(message, UserMessage):
                    # Tool results come through here as UserMessage
                    logger.info(f"DEBUG UserMessage content types: {[type(b).__name__ for b in message.content]}")
                    for block in message.content:
                        logger.info(f"DEBUG UserMessage block: {type(block).__name__} - {block}")
                        if isinstance(block, ToolResultBlock):
                            resolved_name = active_tools.pop(block.tool_use_id, "tool")
                            logger.info(f"DEBUG: ToolResultBlock in UserMessage for {resolved_name}")
                            content = block.content
                            if isinstance(content, list):
                                content = "\n".join(
                                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                                    for c in content
                                )
                            yield {
                                "type": "tool_end",
                                "name": resolved_name,
                                "id": block.tool_use_id,
                                "output": str(content)[:2000] if content else "",
                                "is_error": block.is_error or False
                            }

                # Handle result message (completion) - this signals the end
                elif isinstance(message, ResultMessage):
                    session_id = message.session_id
                    self._current_session_id = session_id

                    # Log full ResultMessage for debugging
                    logger.info(f"ResultMessage: session_id={session_id}, is_error={message.is_error}, "
                               f"result={message.result[:200] if message.result else None}, "
                               f"num_turns={message.num_turns}, duration_ms={message.duration_ms}")

                    # Extract token usage from SDK response
                    # Note: usage is cumulative across all API calls in the agentic
                    # loop, so it does NOT represent actual context window size.
                    usage = message.usage or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_creation = usage.get("cache_creation_input_tokens", 0)

                    logger.info(f"TOKEN USAGE (cumulative): input={input_tokens}, cache_read={cache_read}, cache_creation={cache_creation}, output={output_tokens}")

                    yield {
                        "type": "result_meta",
                        "session_id": session_id,
                        "cost_usd": message.total_cost_usd or 0,
                        "duration_ms": message.duration_ms or 0,
                        "num_turns": message.num_turns or 0,
                        "is_error": message.is_error,
                        "usage": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cache_read_input_tokens": cache_read,
                            "cache_creation_input_tokens": cache_creation,
                            "total_tokens": input_tokens + output_tokens
                        }
                    }

                    if message.is_error and message.result:
                        yield {"type": "error", "text": message.result}

                    # ResultMessage signals completion - break out of the loop
                    logger.info("ResultMessage received, breaking out of receive loop")
                    break

        except Exception as e:
            logger.error(f"Claude SDK error: {e}", exc_info=True)
            yield {"type": "error", "text": str(e)}

        finally:
            # Close the injection queue to signal no more messages
            if self._injection_queue:
                self._injection_queue.close()
                self._injection_queue = None

            if self.client:
                try:
                    await self.client.disconnect()
                except:
                    pass
                self.client = None

        logger.info(f"Claude SDK completed, session_id={self._current_session_id}")

    async def interrupt(self):
        """Send interrupt signal to running Claude session."""
        # Close injection queue first
        if self._injection_queue:
            self._injection_queue.close()

        if self.client:
            try:
                await self.client.interrupt()
                logger.info("Claude session interrupted")
            except Exception as e:
                logger.error(f"Error interrupting: {e}")


    def _build_agent_system_prompt(self, agent_config) -> str:
        """Build system prompt for a chattable agent (prompt.md + optional memory.md).

        Memory is loaded from .claude/agents/{name}/memory.md. This file is
        unique to each agent — it is NOT shared with Ren or other agents.
        Agents can write to it to persist notes across conversations.
        """
        parts = []
        if agent_config.prompt:
            parts.append(agent_config.prompt)
        # Per-agent memory.md (optional, agent-scoped)
        memory_path = Path(self.cwd) / ".claude" / "agents" / agent_config.name / "memory.md"
        if memory_path.exists():
            try:
                content = memory_path.read_text().strip()
                if content:
                    parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{content}"
                    )
                    logger.info(f"Agent '{agent_config.name}': loaded memory.md ({memory_path.stat().st_size} bytes)")
            except Exception as e:
                logger.warning(f"Agent '{agent_config.name}': could not read memory.md: {e}")
        return "\n".join(parts)

    def _build_agent_system_prompt_preset(self, agent_config) -> dict:
        """Build a SystemPromptPreset dict for agents using a system prompt preset.

        The agent's prompt.md content and memory.md become the 'append' field,
        layered on top of Claude Code's native system instructions.
        """
        append_parts = []
        if agent_config.prompt:
            append_parts.append(agent_config.prompt)
        # Per-agent memory.md (same logic as _build_agent_system_prompt)
        memory_path = Path(self.cwd) / ".claude" / "agents" / agent_config.name / "memory.md"
        if memory_path.exists():
            try:
                content = memory_path.read_text().strip()
                if content:
                    append_parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{content}"
                    )
                    logger.info(f"Agent '{agent_config.name}': loaded memory.md for preset append")
            except Exception as e:
                logger.warning(f"Agent '{agent_config.name}': could not read memory.md: {e}")

        preset = {
            "type": "preset",
            "preset": agent_config.system_prompt_preset,
        }
        append_content = "\n".join(append_parts).strip()
        if append_content:
            preset["append"] = append_content
        return preset

    def _build_agent_options(self, agent_config) -> ClaudeAgentOptions:
        """Build SDK options for a chattable (non-primary) agent."""
        if agent_config.system_prompt_preset:
            system_prompt = self._build_agent_system_prompt_preset(agent_config)
        else:
            system_prompt = self._build_agent_system_prompt(agent_config)

        # Separate native tools from MCP tools
        agent_tools = agent_config.tools or []
        mcp_tool_names = [t for t in agent_tools if t.startswith("mcp__")]
        native_tool_names = [t for t in agent_tools if not t.startswith("mcp__")]

        # Compute disallowed native tools
        disallowed = [t for t in ALL_NATIVE_TOOLS if t not in native_tool_names]

        # Create filtered MCP server (with agent_name for memory isolation)
        mcp_servers = {}
        if create_mcp_server and mcp_tool_names:
            internal_names = [t.replace(MCP_PREFIX, "") for t in mcp_tool_names]
            mcp_servers["brain"] = create_mcp_server(
                name="brain",
                include_tools=internal_names,
                chat_id=self.chat_id,
                agent_name=agent_config.name,
            )

        # Build allowed_tools for SDK defense-in-depth
        allowed = list(mcp_tool_names)
        if "Skill" in native_tool_names:
            allowed.append("Skill")

        has_skills = "Skill" in native_tool_names

        return ClaudeAgentOptions(
            model=agent_config.model,
            system_prompt=system_prompt,
            tools={"type": "preset", "preset": "claude_code"},
            disallowed_tools=disallowed,
            setting_sources=["project"] if has_skills else [],
            mcp_servers=mcp_servers if mcp_servers else None,
            allowed_tools=allowed if allowed else None,
            permission_mode="bypassPermissions",
            cwd=self.cwd,
            include_partial_messages=True,
        )

    async def run_agent_chat(
        self,
        prompt,
        agent_config,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        use_streaming_input: bool = True
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Execute a prompt through a chattable agent and stream results.

        Similar to run_prompt() but uses agent-specific options instead of
        the primary agent's full LTM + CLAUDE.md pipeline.

        For agents with Skill tool (which require setting_sources=["project"]),
        we temporarily swap CLAUDE.md to a minimal stub to prevent identity
        bleed from Ren's content. The swap is protected by _claude_md_lock
        and restored after the SDK subprocess caches its config at init.
        """
        self._conversation_history = conversation_history or []
        options = self._build_agent_options(agent_config)

        logger.info(f"Running agent chat '{agent_config.name}': model={agent_config.model}, streaming_input={use_streaming_input}")

        # Determine if we need to swap CLAUDE.md to prevent identity bleed
        has_skills = agent_config.tools and "Skill" in agent_config.tools
        claude_md_swapped = False

        try:
            # If agent uses setting_sources=["project"], swap CLAUDE.md
            # to a minimal stub before the SDK reads it at startup.
            if has_skills:
                await _claude_md_lock.acquire()
                self._write_agent_claude_md(agent_config.name)
                claude_md_swapped = True
                logger.info(f"CLAUDE.md: swapped for agent '{agent_config.name}' (lock acquired)")

            self.client = ClaudeSDKClient(options=options)

            if use_streaming_input:
                self._injection_queue = MessageInjectionQueue()
                self._injection_queue.set_initial_prompt(prompt)
                await self.client.connect(self._injection_queue)
            else:
                await self.client.connect()
                await self.client.query(prompt)

            active_tools: Dict[str, str] = {}

            async for message in self.client.receive_messages():
                if isinstance(message, StreamEvent):
                    event = message.event
                    event_type = event.get("type", "")

                    if event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        delta_type = delta.get("type", "")
                        if delta_type == "text_delta":
                            text = delta.get("text", "")
                            if text:
                                yield {"type": "content_delta", "text": text}
                        elif delta_type == "thinking_delta":
                            thinking = delta.get("thinking", "")
                            if thinking:
                                yield {"type": "thinking_delta", "text": thinking}

                    elif event_type == "content_block_start":
                        block = event.get("content_block", {})
                        if block.get("type") == "tool_use":
                            tool_name = block.get("name", "tool")
                            tool_id = block.get("id")
                            if tool_id:
                                active_tools[tool_id] = tool_name
                            yield {"type": "tool_start", "name": tool_name, "id": tool_id}

                elif isinstance(message, SystemMessage):
                    if message.subtype == "init":
                        # SDK subprocess has started and cached CLAUDE.md.
                        # Restore Ren's version and release the lock.
                        if claude_md_swapped:
                            self._refresh_claude_md()
                            _claude_md_lock.release()
                            claude_md_swapped = False
                            logger.info(f"CLAUDE.md: restored after agent '{agent_config.name}' init (lock released)")
                        session_id = message.data.get("session_id", str(uuid.uuid4()))
                        self._current_session_id = session_id
                        yield {"type": "session_init", "id": session_id}

                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            yield {"type": "content", "text": block.text}
                        elif isinstance(block, ThinkingBlock):
                            yield {"type": "thinking", "text": block.thinking}
                        elif isinstance(block, ToolUseBlock):
                            active_tools[block.id] = block.name
                            yield {
                                "type": "tool_use",
                                "name": block.name,
                                "id": block.id,
                                "args": json.dumps(block.input) if block.input else "{}"
                            }
                        elif isinstance(block, ToolResultBlock):
                            resolved_name = active_tools.pop(block.tool_use_id, "tool")
                            content = block.content
                            if isinstance(content, list):
                                content = "\n".join(
                                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                                    for c in content
                                )
                            yield {
                                "type": "tool_end",
                                "name": resolved_name,
                                "id": block.tool_use_id,
                                "output": str(content)[:2000] if content else "",
                                "is_error": block.is_error or False
                            }

                elif isinstance(message, UserMessage):
                    for block in message.content:
                        if isinstance(block, ToolResultBlock):
                            resolved_name = active_tools.pop(block.tool_use_id, "tool")
                            content = block.content
                            if isinstance(content, list):
                                content = "\n".join(
                                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                                    for c in content
                                )
                            yield {
                                "type": "tool_end",
                                "name": resolved_name,
                                "id": block.tool_use_id,
                                "output": str(content)[:2000] if content else "",
                                "is_error": block.is_error or False
                            }

                elif isinstance(message, ResultMessage):
                    session_id = message.session_id
                    self._current_session_id = session_id
                    usage = message.usage or {}
                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_creation = usage.get("cache_creation_input_tokens", 0)

                    yield {
                        "type": "result_meta",
                        "session_id": session_id,
                        "cost_usd": message.total_cost_usd or 0,
                        "duration_ms": message.duration_ms or 0,
                        "num_turns": message.num_turns or 0,
                        "is_error": message.is_error,
                        "usage": {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cache_read_input_tokens": cache_read,
                            "cache_creation_input_tokens": cache_creation,
                            "total_tokens": input_tokens + output_tokens
                        }
                    }

                    if message.is_error and message.result:
                        yield {"type": "error", "text": message.result}

                    break

        except Exception as e:
            logger.error(f"Agent chat SDK error: {e}", exc_info=True)
            yield {"type": "error", "text": str(e)}

        finally:
            # Ensure CLAUDE.md is restored and lock released if still held
            if claude_md_swapped:
                try:
                    self._refresh_claude_md()
                except Exception as e:
                    logger.warning(f"CLAUDE.md: failed to restore after agent error: {e}")
                try:
                    _claude_md_lock.release()
                except RuntimeError:
                    pass  # Already released
                logger.info(f"CLAUDE.md: restored in finally for agent '{agent_config.name}'")
            if self._injection_queue:
                self._injection_queue.close()
                self._injection_queue = None
            if self.client:
                try:
                    await self.client.disconnect()
                except:
                    pass
                self.client = None

        logger.info(f"Agent chat '{agent_config.name}' completed, session_id={self._current_session_id}")


class ChatManager:
    """Manages chat sessions and history.

    Uses FileLock for save operations to prevent data loss from concurrent
    writes (e.g., two chat sessions or a chat + titler writing simultaneously).
    """

    def __init__(self, chats_dir: str):
        self.chats_dir = chats_dir
        self._locks_dir = os.path.join(chats_dir, ".locks")
        os.makedirs(chats_dir, exist_ok=True)
        os.makedirs(self._locks_dir, exist_ok=True)

    def get_chat_path(self, session_id: str) -> str:
        return os.path.join(self.chats_dir, f"{session_id}.json")

    def _get_lock_path(self, session_id: str) -> str:
        return os.path.join(self._locks_dir, f"{session_id}.lock")

    def load_chat(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a chat from disk."""
        path = self.get_chat_path(session_id)
        if not os.path.exists(path):
            return None
        try:
            with FileLock(self._get_lock_path(session_id), timeout=5):
                with open(path, 'r') as f:
                    return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def save_chat(self, session_id: str, data: Dict[str, Any]):
        """Save a chat to disk atomically with file locking.

        Uses FileLock to prevent concurrent writes from losing messages,
        and atomic write (temp file + rename) to prevent partial reads.
        """
        import time
        # Always update last_message_at when saving - this is the canonical sort timestamp
        data["last_message_at"] = time.time()
        path = self.get_chat_path(session_id)
        with FileLock(self._get_lock_path(session_id), timeout=10):
            # Atomic write: temp file + rename
            fd, tmp_path = tempfile.mkstemp(
                dir=self.chats_dir,
                prefix=f".{session_id}.",
                suffix=".tmp"
            )
            try:
                with os.fdopen(fd, 'w') as f:
                    json.dump(data, f, indent=2)
                os.rename(tmp_path, path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise

        # Update room metadata (if available)
        try:
            import sys
            scripts_dir = str(Path(self.chats_dir).parent / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import rooms_meta
            # Bump updated_at and set title if provided
            rooms_meta.bump(session_id)
            if data.get("title"):
                rooms_meta.set_title(session_id, data["title"])
        except ImportError:
            pass  # Room metadata not available
        except Exception as e:
            logger.debug(f"Failed to update room metadata: {e}")

    def delete_chat(self, session_id: str) -> bool:
        """Delete a chat from disk and its room metadata."""
        path = self.get_chat_path(session_id)
        if os.path.exists(path):
            os.remove(path)

            # Delete room metadata (if available)
            try:
                import sys
                scripts_dir = str(Path(self.chats_dir).parent / "scripts")
                if scripts_dir not in sys.path:
                    sys.path.insert(0, scripts_dir)
                import rooms_meta
                rooms_meta.delete_room_meta(session_id)
            except ImportError:
                pass  # Room metadata not available
            except Exception as e:
                logger.debug(f"Failed to delete room metadata: {e}")

            return True
        return False

    def list_chats(self) -> List[Dict[str, Any]]:
        """List all saved chats with metadata, sorted by most recent last message."""
        import glob
        chats = []
        for f in glob.glob(os.path.join(self.chats_dir, "*.json")):
            try:
                with open(f, 'r') as cf:
                    data = json.load(cf)

                    # Priority for sort timestamp:
                    # 1. last_message_at field (set by save_chat on new saves)
                    # 2. Max timestamp from message IDs (user messages have timestamp IDs)
                    # 3. File mtime as final fallback

                    last_message_time = data.get("last_message_at")

                    if not last_message_time:
                        # Check message IDs for timestamps
                        messages = data.get("messages", [])
                        for msg in messages:
                            msg_id = msg.get("id", "")
                            if isinstance(msg_id, str) and msg_id.isdigit() and len(msg_id) >= 13:
                                ts = int(msg_id) / 1000.0
                                if not last_message_time or ts > last_message_time:
                                    last_message_time = ts

                    if not last_message_time:
                        # Final fallback to file mtime
                        last_message_time = os.path.getmtime(f)

                    # Add clock emoji for scheduled chats at display time
                    raw_title = data.get("title", "Untitled Chat")
                    is_scheduled = data.get("scheduled", False)
                    display_title = f"🕐 {raw_title}" if is_scheduled else raw_title

                    chats.append({
                        "id": os.path.splitext(os.path.basename(f))[0],
                        "title": display_title,
                        "updated": last_message_time,
                        "is_system": data.get("is_system", False),
                        "scheduled": is_scheduled,
                        "agent": data.get("agent")
                    })
            except:
                pass
        return sorted(chats, key=lambda x: x['updated'], reverse=True)

    def generate_title(self, first_message: str) -> str:
        """Generate a chat title from the first message."""
        # Clean and truncate
        title = first_message.strip()
        title = title.replace('\n', ' ')
        title = ' '.join(title.split())  # Normalize whitespace

        # Remove common prefixes
        for prefix in ['[CONTEXT:', '[SCHEDULED', 'Hey ', 'Hi ', 'Hello ']:
            if title.startswith(prefix):
                title = title[len(prefix):].strip()

        # Truncate
        if len(title) > 50:
            title = title[:47] + "..."

        return title or "New Chat"


class ConversationState:
    """Tracks conversation state for edit/regenerate functionality."""

    def __init__(self):
        self.messages: List[Dict[str, Any]] = []
        self.session_id: Optional[str] = None
        # Track in-progress assistant response (for restart continuity)
        self.pending_response: List[str] = []  # Accumulated streaming segments
        # Track cumulative token usage across all turns in this conversation
        self.cumulative_usage: Dict[str, int] = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        }
        # Track number of completed exchanges for chat titler
        self.exchange_count: int = 0

    def add_message(self, role: str, content: str, msg_id: Optional[str] = None, images: Optional[List[Dict]] = None):
        """Add a message to the conversation."""
        msg = {
            "id": msg_id or str(uuid.uuid4()),
            "role": role,
            "content": content
        }
        if images:
            msg["images"] = images
        self.messages.append(msg)

    def truncate_at(self, message_id: str) -> bool:
        """
        Truncate conversation at the specified message (for edit/regenerate).
        Returns True if truncation occurred.
        """
        for i, msg in enumerate(self.messages):
            if msg.get("id") == message_id:
                self.messages = self.messages[:i]
                return True
        return False

    def get_context_for_prompt(self) -> str:
        """Build context string from conversation history."""
        if not self.messages:
            return ""

        context_parts = []
        for msg in self.messages[-10:]:  # Last 10 messages for context
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "user":
                context_parts.append(f"User: {content}")
            elif role == "assistant":
                context_parts.append(f"Assistant: {content}")

        return "\n\n".join(context_parts)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "messages": self.messages,
            "session_id": self.session_id,
            "cumulative_usage": self.cumulative_usage,
            "exchange_count": self.exchange_count
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ConversationState':
        state = cls()
        state.messages = data.get("messages", [])
        state.session_id = data.get("session_id")
        state.cumulative_usage = data.get("cumulative_usage", {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0
        })
        state.exchange_count = data.get("exchange_count", 0)
        return state
