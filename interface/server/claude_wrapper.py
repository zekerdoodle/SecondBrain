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
from pathlib import Path
from typing import Optional, AsyncIterator, Dict, Any, List

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


def _load_primary_agent_config(cwd: str) -> tuple[list, list, list]:
    """
    Load primary agent configuration from .claude/agents/claude_primary/config.yaml.

    Returns:
        Tuple of (mcp_tool_names, internal_tool_names, disallowed_native_tools)
        - mcp_tool_names: e.g., ["mcp__brain__google_list", ...]
        - internal_tool_names: e.g., ["google_list", ...]
        - disallowed_native_tools: e.g., ["WebSearch", "Bash", ...]

    Falls back to legacy agent_config.yaml if new config doesn't exist.
    """
    import yaml

    # Try new unified config location first
    new_config_path = Path(cwd) / ".claude" / "agents" / "claude_primary" / "config.yaml"
    legacy_config_path = Path(cwd) / ".claude" / "agent_config.yaml"

    config_path = None
    if new_config_path.exists():
        config_path = new_config_path
        logger.info("Loading primary agent config from new location: agents/claude_primary/config.yaml")
    elif legacy_config_path.exists():
        config_path = legacy_config_path
        logger.warning("Using legacy agent_config.yaml - consider migrating to agents/claude_primary/")
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
        # New format: mcp_tools section
        # Legacy format: tools section
        mcp_config = config.get("mcp_tools") or config.get("tools")

        if not mcp_config:
            logger.info("No MCP tools config, using all MCP tools")
            mcp_tools = list(ALL_MCP_TOOLS)
            internal_tools = [t.replace(MCP_PREFIX, "") for t in mcp_tools]
            return mcp_tools, internal_tools, disallowed_native

        allowed = set()

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


class ClaudeWrapper:
    """Async wrapper for Claude Agent SDK with streaming support."""

    def __init__(self, session_id: str, cwd: str):
        self.session_id = session_id
        self.cwd = cwd
        self.client: Optional[ClaudeSDKClient] = None
        self._current_session_id: Optional[str] = None
        self._conversation_history: List[Dict[str, Any]] = []

    async def _build_system_prompt(self, user_query: Optional[str] = None) -> str:
        """
        Build system prompt with primary agent instructions and memory injection.

        Primary agent instructions are loaded from .claude/agents/claude_primary/prompt.md
        (falls back to legacy .claude/primary_agent_instructions.md).
        Memory.md is injected here so it's always in context (no tool call needed to read it).
        Working memory is also injected for ephemeral cross-exchange context.
        Semantic LTM is injected based on the user's query for relevant context.
        """
        # Load primary agent instructions - try new location first, then legacy
        new_prompt_path = Path(self.cwd) / ".claude" / "agents" / "claude_primary" / "prompt.md"
        legacy_prompt_path = Path(self.cwd) / ".claude" / "primary_agent_instructions.md"

        primary_instructions = ""
        if new_prompt_path.exists():
            try:
                primary_instructions = new_prompt_path.read_text()
                logger.info(f"Loaded primary agent prompt from agents/claude_primary/prompt.md ({len(primary_instructions)} chars)")
            except Exception as e:
                logger.warning(f"Could not read prompt.md: {e}")
        elif legacy_prompt_path.exists():
            try:
                primary_instructions = legacy_prompt_path.read_text()
                logger.info(f"Loaded primary agent prompt from legacy location ({len(primary_instructions)} chars)")
                logger.warning("Using legacy primary_agent_instructions.md - consider migrating to agents/claude_primary/")
            except Exception as e:
                logger.warning(f"Could not read primary_agent_instructions.md: {e}")
        else:
            logger.warning("No primary agent prompt found (checked agents/claude_primary/prompt.md and primary_agent_instructions.md)")

        base_prompt = f"""You are Claude, an AI assistant with full access to this workspace.

Key capabilities:
- All standard tools (Read, Write, Edit, Bash, Glob, Grep, WebSearch, WebFetch)
- Custom MCP tools (mcp__brain__*) for Google, Scheduler, and Memory
- Task tool for spawning subagents
- Skills defined in .claude/skills/

Working style:
- Be direct and take action
- Use MCP tools when available instead of raw Bash
- Save important context to memory proactively
- This VM is yours - bypassPermissions is enabled

---

{primary_instructions}
"""

        # Inject memory.md if it exists - this gives Claude persistent context without needing a tool call
        memory_path = Path(self.cwd) / ".claude" / "memory.md"
        if memory_path.exists():
            try:
                memory_content = memory_path.read_text()
                base_prompt += f"\n\n<long-term-memory>\n{memory_content}\n</long-term-memory>\n"
                logger.info(f"Injected memory.md ({len(memory_content)} chars) into system prompt")
            except Exception as e:
                logger.warning(f"Could not read memory.md: {e}")

        # Inject working memory if it has items
        try:
            import sys
            scripts_dir = Path(self.cwd) / ".claude" / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from working_memory import get_store
            store = get_store()
            wm_block = store.format_prompt_block()
            if wm_block:
                base_prompt += f"\n\n<working-memory>\n{wm_block}\n</working-memory>\n"
                logger.info(f"Injected working memory ({len(store.list_items())} items) into system prompt")
        except Exception as e:
            logger.debug(f"Could not inject working memory: {e}")

        # Inject semantic long-term memory based on user query
        if user_query:
            try:
                ltm_scripts = Path(self.cwd) / ".claude" / "scripts" / "ltm"
                if str(ltm_scripts) not in sys.path:
                    sys.path.insert(0, str(ltm_scripts))
                from memory_retrieval import get_memory_context, MemoryContext
                from query_rewriter import rewrite_query

                # Rewrite query for better semantic search
                rewritten = await rewrite_query(
                    user_message=user_query,
                    conversation_context=self._conversation_history
                )

                # Run multiple queries, merge results
                all_memories = []
                seen_ids = set()
                threads = []
                guaranteed = []

                # Split budget across queries (20k total = 10% of 200k context)
                budget_per_query = 20000 // len(rewritten.queries)

                for i, q in enumerate(rewritten.queries):
                    context = get_memory_context(q, token_budget=budget_per_query)

                    # Collect unique atomic memories
                    for mem in context.atomic_memories:
                        mem_id = mem.get('id', str(mem.get('content', '')))
                        if mem_id not in seen_ids:
                            all_memories.append(mem)
                            seen_ids.add(mem_id)

                    # Include threads from first query only (highest relevance)
                    if i == 0:
                        threads = context.threads
                        guaranteed = context.guaranteed_memories

                # Build merged context and format
                if all_memories or threads or guaranteed:
                    merged = MemoryContext(
                        atomic_memories=all_memories[:100],  # Cap at 100 (was 30)
                        threads=threads,
                        guaranteed_memories=guaranteed,
                        total_tokens=0,
                        token_breakdown={}
                    )
                    formatted = merged.format_for_prompt()

                    if formatted and len(formatted) > 50:
                        base_prompt += f"\n\n<semantic-memory>\n{formatted}\n</semantic-memory>\n"
                        logger.info(
                            f"Injected semantic LTM via rewritten queries {rewritten.queries}: "
                            f"{len(threads)} threads, {len(all_memories)} atoms, {len(guaranteed)} guaranteed"
                        )
            except Exception as e:
                logger.debug(f"Could not inject semantic LTM: {e}")

        # NOTE: Agent notifications are now injected into the user prompt in main.py
        # This is more reliable for resumed SDK sessions where the system prompt may be cached

        return base_prompt

    async def _build_options(self, resume_session: Optional[str] = None, fork: bool = False, user_query: Optional[str] = None) -> ClaudeAgentOptions:
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
                filtered_mcp_server = create_mcp_server(
                    name="brain",  # Must match the key in mcp_servers dict
                    include_tools=internal_tool_names
                )
                mcp_servers["brain"] = filtered_mcp_server
                logger.info(f"Created filtered MCP server with {len(internal_tool_names)} tools")
            else:
                logger.warning("No MCP tools allowed, MCP server not created")

        options = ClaudeAgentOptions(
            # Use latest Claude Opus (auto-updates to newest version)
            model="opus",

            # Enable extended thinking (max tokens for thinking blocks)
            max_thinking_tokens=32000,

            # CUSTOM system prompt - replaces Claude Code's default instructions entirely
            # This is minimal; CLAUDE.md is loaded automatically via setting_sources
            system_prompt=system_prompt,

            # Use Claude Code's tools (schemas injected separately from system prompt)
            tools={"type": "preset", "preset": "claude_code"},

            # Disable native tools based on config (computed from native_tools allowlist)
            disallowed_tools=disallowed_native_tools,

            # Load project settings:
            # - CLAUDE.md (project context, injected as system-reminder)
            # - Skills from .claude/skills/
            setting_sources=["project"],

            # Custom MCP tools for Second Brain functionality
            # Server is created with only allowed tools (filtered at creation time)
            mcp_servers=mcp_servers if mcp_servers else None,

            # Also specify allowed_tools as a secondary safeguard
            # This acts as defense-in-depth in case SDK behavior changes
            allowed_tools=mcp_tool_names if mcp_servers else [],

            # Bypass all permission prompts - this VM is ours
            permission_mode="bypassPermissions",

            # Working directory
            cwd=self.cwd,

            # Session management
            resume=resume_session if resume_session and resume_session not in ("new", "default-session") else None,
            fork_session=fork,

            # Enable partial message streaming for better UX
            include_partial_messages=True,
        )

        return options

    async def run_prompt(
        self,
        prompt: str,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        fork_session: bool = False
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Execute a prompt through Claude SDK and stream results.

        Args:
            prompt: The user's message
            conversation_history: Optional list of previous messages for context
            fork_session: If True, fork the session instead of continuing it

        Yields:
            Event dictionaries with types: session_init, content, content_delta,
            tool_start, tool_end, thinking, status, error, result_meta
        """

        # Store conversation history for query rewriter
        self._conversation_history = conversation_history or []

        # Determine if we should resume a session
        resume_session = None
        if self.session_id and self.session_id not in ("new", "default-session"):
            resume_session = self.session_id

        options = await self._build_options(resume_session, fork=fork_session, user_query=prompt)

        logger.info(f"Running Claude SDK: session={self.session_id}, resume={resume_session}, fork={fork_session}")

        try:
            self.client = ClaudeSDKClient(options=options)
            await self.client.connect()

            # Send the prompt
            await self.client.query(prompt)

            current_tool_name: Optional[str] = None
            current_tool_id: Optional[str] = None

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
                            current_tool_name = block.get("name", "tool")
                            current_tool_id = block.get("id")
                            yield {
                                "type": "tool_start",
                                "name": current_tool_name,
                                "id": current_tool_id
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
                            current_tool_name = block.name
                            current_tool_id = block.id
                            yield {
                                "type": "tool_use",
                                "name": block.name,
                                "id": block.id,
                                "args": json.dumps(block.input) if block.input else "{}"
                            }

                        elif isinstance(block, ToolResultBlock):
                            # Tool result - extract content
                            logger.info(f"DEBUG: ToolResultBlock detected for {current_tool_name}, id={block.tool_use_id}")
                            content = block.content
                            if isinstance(content, list):
                                content = "\n".join(
                                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                                    for c in content
                                )
                            yield {
                                "type": "tool_end",
                                "name": current_tool_name or "tool",
                                "id": block.tool_use_id,
                                "output": str(content)[:2000] if content else "",
                                "is_error": block.is_error or False
                            }
                            current_tool_name = None
                            current_tool_id = None

                # Handle user messages (usually tool results injected by SDK)
                elif isinstance(message, UserMessage):
                    # Tool results come through here as UserMessage
                    logger.info(f"DEBUG UserMessage content types: {[type(b).__name__ for b in message.content]}")
                    for block in message.content:
                        logger.info(f"DEBUG UserMessage block: {type(block).__name__} - {block}")
                        if isinstance(block, ToolResultBlock):
                            logger.info(f"DEBUG: ToolResultBlock in UserMessage for {current_tool_name}")
                            content = block.content
                            if isinstance(content, list):
                                content = "\n".join(
                                    c.get("text", str(c)) if isinstance(c, dict) else str(c)
                                    for c in content
                                )
                            yield {
                                "type": "tool_end",
                                "name": current_tool_name or "tool",
                                "id": block.tool_use_id,
                                "output": str(content)[:2000] if content else "",
                                "is_error": block.is_error or False
                            }
                            current_tool_name = None
                            current_tool_id = None

                # Handle result message (completion) - this signals the end
                elif isinstance(message, ResultMessage):
                    session_id = message.session_id
                    self._current_session_id = session_id

                    # Log full ResultMessage for debugging
                    logger.info(f"ResultMessage: session_id={session_id}, is_error={message.is_error}, "
                               f"result={message.result[:200] if message.result else None}, "
                               f"num_turns={message.num_turns}, duration_ms={message.duration_ms}")

                    # Extract token usage from SDK response
                    usage = message.usage or {}
                    logger.info(f"RAW USAGE FROM SDK: {usage}")

                    input_tokens = usage.get("input_tokens", 0)
                    output_tokens = usage.get("output_tokens", 0)
                    cache_read = usage.get("cache_read_input_tokens", 0)
                    cache_creation = usage.get("cache_creation_input_tokens", 0)

                    # Log the breakdown
                    # Actual context = input + cache_read + cache_creation
                    # cache_creation tokens ARE part of the context (being written to cache)
                    actual_context = input_tokens + cache_read + cache_creation
                    logger.info(f"TOKEN BREAKDOWN: input={input_tokens}, cache_read={cache_read}, cache_creation={cache_creation}, output={output_tokens}")
                    logger.info(f"ACTUAL CONTEXT SIZE: {actual_context} tokens ({actual_context/200000*100:.1f}% of 200k)")

                    # Calculate context percentage (auto-compaction triggers at 95%)
                    MAX_CONTEXT = 200000
                    COMPACTION_THRESHOLD_PCT = 95
                    compaction_threshold = MAX_CONTEXT * (COMPACTION_THRESHOLD_PCT / 100)
                    context_percent = (actual_context / compaction_threshold) * 100

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
                        },
                        "context": {
                            "actual_tokens": actual_context,
                            "max_tokens": MAX_CONTEXT,
                            "compaction_threshold": int(compaction_threshold),
                            "percent_until_compaction": round(context_percent, 1)
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
            if self.client:
                try:
                    await self.client.disconnect()
                except:
                    pass
                self.client = None

        logger.info(f"Claude SDK completed, session_id={self._current_session_id}")

    async def interrupt(self):
        """Send interrupt signal to running Claude session."""
        if self.client:
            try:
                await self.client.interrupt()
                logger.info("Claude session interrupted")
            except Exception as e:
                logger.error(f"Error interrupting: {e}")


class ChatManager:
    """Manages chat sessions and history."""

    def __init__(self, chats_dir: str):
        self.chats_dir = chats_dir
        os.makedirs(chats_dir, exist_ok=True)

    def get_chat_path(self, session_id: str) -> str:
        return os.path.join(self.chats_dir, f"{session_id}.json")

    def load_chat(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Load a chat from disk."""
        path = self.get_chat_path(session_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None

    def save_chat(self, session_id: str, data: Dict[str, Any]):
        """Save a chat to disk. Automatically sets last_message_at timestamp."""
        import time
        # Always update last_message_at when saving - this is the canonical sort timestamp
        data["last_message_at"] = time.time()
        path = self.get_chat_path(session_id)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)

    def delete_chat(self, session_id: str) -> bool:
        """Delete a chat from disk."""
        path = self.get_chat_path(session_id)
        if os.path.exists(path):
            os.remove(path)
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
                        "scheduled": is_scheduled
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

    def add_message(self, role: str, content: str, msg_id: Optional[str] = None):
        """Add a message to the conversation."""
        self.messages.append({
            "id": msg_id or str(uuid.uuid4()),
            "role": role,
            "content": content
        })

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
