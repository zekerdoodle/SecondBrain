"""
Claude Agent SDK Wrapper for Second Brain Interface

Uses the official Python SDK with:
- Per-agent system prompts and memory (prompt.md + memory.md)
- Full tool access with bypassPermissions
- Streaming message support with partial events
- All agents (including Ren) route through run_chat()
"""

import os
import sys
import json
import asyncio
import logging
import time
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
from claude_agent_sdk.types import (
    StreamEvent,
    ThinkingConfigAdaptive,
    ThinkingConfigEnabled,
)

logger = logging.getLogger(__name__)

# Import custom MCP tools
try:
    from mcp_tools import (
        create_mcp_server,
        MCP_PREFIX,
    )
    # Per-agent MCP servers are created dynamically in _build_options()
    logger.info("MCP tools module loaded successfully")
except Exception as e:
    logger.warning(f"Could not load Second Brain MCP tools: {e}")
    create_mcp_server = None
    MCP_PREFIX = "mcp__brain__"


# All native Claude Code tools that can be controlled
ALL_NATIVE_TOOLS = {
    # File operations
    "Read", "Write", "Edit", "Glob", "Grep", "NotebookEdit",
    # Execution
    "Bash", "Task", "TaskOutput", "TaskStop", "KillShell",
    # Web
    "WebFetch", "WebSearch",
    # UI/Interaction
    "AskUserQuestion", "TodoWrite",
    # Plan mode
    "EnterPlanMode", "ExitPlanMode",
}

# Model-aware thinking defaults — maximize thinking for every model tier
# Keys match the short model aliases used in agent config.yaml files
THINKING_DEFAULTS = {
    "opus": {
        "thinking": ThinkingConfigAdaptive(type="adaptive"),
        "effort": "high",
    },
    "sonnet": {
        "thinking": ThinkingConfigAdaptive(type="adaptive"),
        "effort": "high",
    },
    "haiku": {
        "thinking": ThinkingConfigEnabled(type="enabled", budget_tokens=16384),
    },
}



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
        self.chat_id = chat_id  # Storage chat ID for MCP server context
        self.chat_messages = chat_messages or []
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


    def _load_agent_working_memory(self, agent_name: str) -> str:
        """Load per-agent working memory and format as a prompt block."""
        try:
            import sys
            scripts_dir = Path(self.cwd) / ".claude" / "scripts"
            if str(scripts_dir) not in sys.path:
                sys.path.insert(0, str(scripts_dir))
            from working_memory import get_store
            store = get_store(agent_name=agent_name)
            wm_block = store.format_prompt_block()
            if wm_block:
                logger.info(f"Agent '{agent_name}': loaded working memory ({len(store.list_items())} items)")
                return f"\n\n<working-memory>\n{wm_block}\n</working-memory>"
        except Exception as e:
            logger.debug(f"Agent '{agent_name}': could not load working memory: {e}")
        return ""

    def _get_skill_reminder(self, agent_config) -> str:
        """Get skill menu block for an agent, or empty string."""
        agent_skills = getattr(agent_config, "skills", None)
        agent_has_skills = agent_skills is None or (isinstance(agent_skills, list) and len(agent_skills) > 0)
        if not agent_has_skills:
            return ""
        try:
            import sys as _sys
            _agents_dir = str(Path(self.cwd) / ".claude" / "agents")
            if _agents_dir not in _sys.path:
                _sys.path.insert(0, _agents_dir)
            from skill_injector import get_skill_reminder
            reminder = get_skill_reminder(allowed_skills=agent_skills) or ""
            if reminder:
                logger.info(f"Agent '{agent_config.name}': will inject skill menu into system prompt")
            return reminder
        except Exception as e:
            logger.warning(f"Skill menu generation failed for agent '{agent_config.name}': {e}")
            return ""

    def _build_system_prompt(self, agent_config, agent_list_block: str = "") -> str:
        """Build system prompt for a chattable agent (prompt.md + always_load memories).

        Memory is loaded from .claude/agents/{name}/memories.json (always_load items).
        Falls back to memory.md for agents not yet migrated.
        """
        parts = []
        if agent_config.prompt:
            parts.append(agent_config.prompt)
        # Skill menu sits above memory in the system prompt
        skill_reminder = self._get_skill_reminder(agent_config)
        if skill_reminder:
            parts.append(skill_reminder)
        # Agent list sits above memory in the system prompt
        if agent_list_block:
            parts.append(agent_list_block)
        # Per-agent always_load memories from memories.json
        memories_path = Path(self.cwd) / ".claude" / "agents" / agent_config.name / "memories.json"
        if memories_path.exists():
            try:
                all_memories = json.loads(memories_path.read_text())
                always_load = [m for m in all_memories if m.get("always_load")]
                if always_load:
                    lines = [f"- {m['content']}" for m in always_load]
                    memory_block = "\n".join(lines)
                    parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{memory_block}"
                    )
                    logger.info(f"Agent '{agent_config.name}': loaded {len(always_load)} always_load memories")
            except Exception as e:
                logger.warning(f"Agent '{agent_config.name}': could not read memories.json: {e}")
        else:
            # Fallback: legacy memory.md
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
        # Per-agent working memory
        wm_block = self._load_agent_working_memory(agent_config.name)
        if wm_block:
            parts.append(wm_block)
        return "\n".join(parts)

    def _build_system_prompt_preset(self, agent_config, agent_list_block: str = "") -> dict:
        """Build a SystemPromptPreset dict for agents using a system prompt preset.

        The agent's prompt.md content and always_load memories become the 'append' field,
        layered on top of Claude Code's native system instructions.
        """
        append_parts = []
        if agent_config.prompt:
            append_parts.append(agent_config.prompt)
        # Skill menu sits above memory in the system prompt
        skill_reminder = self._get_skill_reminder(agent_config)
        if skill_reminder:
            append_parts.append(skill_reminder)
        # Agent list sits above memory in the system prompt
        if agent_list_block:
            append_parts.append(agent_list_block)
        # Per-agent always_load memories from memories.json
        memories_path = Path(self.cwd) / ".claude" / "agents" / agent_config.name / "memories.json"
        if memories_path.exists():
            try:
                all_memories = json.loads(memories_path.read_text())
                always_load = [m for m in all_memories if m.get("always_load")]
                if always_load:
                    lines = [f"- {m['content']}" for m in always_load]
                    memory_block = "\n".join(lines)
                    append_parts.append(
                        "\n---\n\n"
                        "Your persistent memory (notes you've saved across conversations):\n\n"
                        f"{memory_block}"
                    )
                    logger.info(f"Agent '{agent_config.name}': loaded {len(always_load)} always_load memories for preset append")
            except Exception as e:
                logger.warning(f"Agent '{agent_config.name}': could not read memories.json: {e}")
        else:
            # Fallback: legacy memory.md
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
        # Per-agent working memory
        wm_block = self._load_agent_working_memory(agent_config.name)
        if wm_block:
            append_parts.append(wm_block)

        preset = {
            "type": "preset",
            "preset": agent_config.system_prompt_preset,
        }
        append_content = "\n".join(append_parts).strip()
        if append_content:
            preset["append"] = append_content
        return preset

    def _build_options(self, agent_config) -> ClaudeAgentOptions:
        """Build SDK options for any chattable agent (including Ren)."""
        # Separate native tools from MCP tools (needed before building system prompt
        # so we can compute the agent list block for injection above memory).
        agent_tools = agent_config.tools or []
        mcp_tool_names = [t for t in agent_tools if t.startswith("mcp__")]
        native_tool_names = [t for t in agent_tools if not t.startswith("mcp__")]

        # Auto-include fetch_skill if agent has skill access
        agent_skills = getattr(agent_config, "skills", None)
        agent_has_skills = agent_skills is None or (isinstance(agent_skills, list) and len(agent_skills) > 0)
        fetch_skill_mcp = f"{MCP_PREFIX}fetch_skill"
        if agent_has_skills and fetch_skill_mcp not in mcp_tool_names:
            mcp_tool_names.append(fetch_skill_mcp)

        # Auto-include working memory tools for all agents
        WM_TOOLS = [
            f"{MCP_PREFIX}working_memory_add",
            f"{MCP_PREFIX}working_memory_update",
            f"{MCP_PREFIX}working_memory_remove",
            f"{MCP_PREFIX}working_memory_list",
            f"{MCP_PREFIX}working_memory_snapshot",
        ]
        for wm_tool in WM_TOOLS:
            if wm_tool not in mcp_tool_names:
                mcp_tool_names.append(wm_tool)

        # Pre-compute agent list block for injection above memory in system prompt.
        agent_list_block = ""
        try:
            from mcp_tools.agents import get_agent_list_for_prompt
            agent_list_block = get_agent_list_for_prompt(mcp_tool_names) or ""
            if agent_list_block:
                logger.info(f"Chattable agent '{agent_config.name}': will inject agent list into system prompt")
        except Exception as e:
            logger.warning(f"Chattable agent '{agent_config.name}': failed to get agent list: {e}")

        if agent_config.system_prompt_preset:
            system_prompt = self._build_system_prompt_preset(agent_config, agent_list_block)
        else:
            system_prompt = self._build_system_prompt(agent_config, agent_list_block)

        # Determine if this agent uses the Claude Code preset
        use_preset = bool(agent_config.system_prompt_preset)

        # Compute disallowed native tools for ALL agents (not just preset).
        # The SDK provides all native tools by default; disallowed_tools is the
        # only way to block ones not in this agent's config.
        disallowed = [t for t in ALL_NATIVE_TOOLS if t not in native_tool_names]

        if use_preset:
            # Preset mode: Claude Code tools preset provides native tools;
            # disallowed_tools restricts which ones this agent can use.
            # Only MCP tools go in allowed_tools (native tools come from preset).
            allowed = list(mcp_tool_names)
        else:
            # Non-preset mode: native + MCP tools in allowed_tools.
            allowed = list(native_tool_names) + list(mcp_tool_names)

        # Create filtered MCP server (with agent_name for memory isolation, allowed_skills for fetch_skill)
        mcp_servers = {}
        if create_mcp_server and mcp_tool_names:
            internal_names = [t.replace(MCP_PREFIX, "") for t in mcp_tool_names]
            mcp_servers["brain"] = create_mcp_server(
                name="brain",
                include_tools=internal_names,
                chat_id=self.chat_id,
                agent_name=agent_config.name,
                allowed_skills=agent_skills if agent_has_skills else "NO_SKILLS",
            )

        options_kwargs = {
            "model": agent_config.model,
            "system_prompt": system_prompt,
            "setting_sources": [],  # Skills handled by fetch_skill MCP tool, no project settings needed
            "mcp_servers": mcp_servers if mcp_servers else None,
            "allowed_tools": allowed if allowed else None,
            "permission_mode": "bypassPermissions",
            "cwd": self.cwd,
            "include_partial_messages": True,
        }

        if use_preset:
            # Preset agents get Claude Code's native tool suite via the preset.
            options_kwargs["tools"] = {"type": "preset", "preset": "claude_code"}

        # Block native tools not in the agent's config — applies to ALL agents.
        if disallowed:
            options_kwargs["disallowed_tools"] = disallowed

        # Apply model-aware thinking configuration
        model = agent_config.model or "sonnet"
        if agent_config.thinking_budget:
            # Agent-level override: explicit budget_tokens
            options_kwargs["thinking"] = ThinkingConfigEnabled(type="enabled", budget_tokens=agent_config.thinking_budget)
            logger.info(f"Agent '{agent_config.name}': thinking config override — enabled with budget_tokens={agent_config.thinking_budget}")
        elif agent_config.effort:
            # Agent-level override: explicit effort (with adaptive thinking)
            options_kwargs["thinking"] = ThinkingConfigAdaptive(type="adaptive")
            options_kwargs["effort"] = agent_config.effort
            logger.info(f"Agent '{agent_config.name}': thinking config override — adaptive, effort={agent_config.effort}")
        else:
            # Model-level defaults from THINKING_DEFAULTS
            thinking_cfg = THINKING_DEFAULTS.get(model)
            if thinking_cfg:
                options_kwargs["thinking"] = thinking_cfg["thinking"]
                if "effort" in thinking_cfg:
                    options_kwargs["effort"] = thinking_cfg["effort"]
                logger.info(
                    f"Agent '{agent_config.name}': applying thinking config for model '{model}': "
                    f"thinking={type(thinking_cfg['thinking']).__name__}, "
                    f"effort={thinking_cfg.get('effort', 'N/A')}"
                )
            else:
                logger.info(f"Agent '{agent_config.name}': no thinking defaults for model '{model}'")

        return ClaudeAgentOptions(**options_kwargs)

    async def run_chat(
        self,
        prompt,
        agent_config,
        conversation_history: Optional[List[Dict[str, Any]]] = None,
        use_streaming_input: bool = True
    ) -> AsyncIterator[Dict[str, Any]]:
        """
        Execute a prompt through an agent and stream results.

        All agents (including Ren) use this single execution path.
        Agent-specific options are built from config.yaml via _build_options().
        Skills are handled by the skill injector + fetch_skill MCP tool.
        """
        self._conversation_history = conversation_history or []
        options = self._build_options(agent_config)

        # Auto-retrieve contextual memories relevant to the user's message
        try:
            scripts_dir = str(Path(self.cwd) / ".claude" / "scripts")
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            from contextual_memory import auto_retrieve_context, rewrite_query_for_retrieval

            # Extract raw user text for retrieval query
            if isinstance(prompt, list):
                raw_query = " ".join(
                    block.get("text", "") for block in prompt if block.get("type") == "text"
                )
            else:
                raw_query = str(prompt)
            raw_query = raw_query[-1000:]

            retrieval_queries = await rewrite_query_for_retrieval(raw_query, self._conversation_history)
            ctx_block = auto_retrieve_context(
                query=retrieval_queries,
                agent_name=agent_config.name,
            )
            if ctx_block:
                if isinstance(options.system_prompt, dict):
                    existing = options.system_prompt.get("append", "")
                    options.system_prompt["append"] = existing + "\n\n" + ctx_block
                else:
                    options.system_prompt = (options.system_prompt or "") + "\n\n" + ctx_block
                logger.info(f"Agent '{agent_config.name}': injected contextual memory into system prompt")
        except Exception as e:
            logger.warning(f"Agent '{agent_config.name}': contextual memory auto-retrieve failed: {e}")

        logger.info(f"Running agent chat '{agent_config.name}': model={agent_config.model}, streaming_input={use_streaming_input}")

        try:
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
                        session_id = message.data.get("session_id", str(uuid.uuid4()))
                        self._current_session_id = session_id
                        yield {"type": "session_init", "id": session_id}

                elif isinstance(message, AssistantMessage):
                    for block in message.content:
                        # Note: TextBlock and ThinkingBlock are intentionally NOT yielded here.
                        # Their content is already streamed via content_delta and thinking_delta
                        # from StreamEvent. Yielding them again would create duplicates.
                        if isinstance(block, ToolUseBlock):
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
                    logger.info(f"ResultMessage received: is_error={message.is_error}, num_turns={message.num_turns}, subtype={message.subtype}, result={str(message.result)[:200] if message.result else None}")
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
            if self._injection_queue:
                self._injection_queue.close()
                self._injection_queue = None
            if self.client:
                try:
                    await self.client.disconnect()
                except Exception:
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
            except Exception:
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
            "content": content,
            "created_at": time.time()
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
