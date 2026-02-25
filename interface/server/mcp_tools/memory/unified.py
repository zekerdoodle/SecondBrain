"""
Unified Memory MCP Tools

Single JSON-backed store per agent with five tools:
- memory_create:       Create a new memory
- memory_search:       Search your own memories
- memory_update:       Update an existing memory by ID
- memory_delete:       Delete a memory by ID
- memory_search_agent: Search another agent's non-private memories

Data file: .claude/agents/{name}/memories.json
"""

import datetime
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from claude_agent_sdk import tool

from ..registry import register_tool

logger = logging.getLogger("mcp_tools.memory.unified")

# ── Path setup ─────────────────────────────────────────────────────────────────

SCRIPTS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts")
)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

CLAUDE_DIR = os.path.dirname(SCRIPTS_DIR)  # .claude/


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_memories_path(args: Dict[str, Any]) -> Path:
    """Return the memories.json path for the calling agent."""
    agent_name = args.get("_agent_name") or "ren"
    return Path(CLAUDE_DIR) / "agents" / agent_name / "memories.json"


def _agent_label(args: Dict[str, Any]) -> str:
    return args.get("_agent_name") or "ren"


def _load_memories(path: Path) -> List[Dict[str, Any]]:
    """Load memories from JSON file. Returns empty list if missing/invalid."""
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"Failed to load {path}: {e}")
        return []


def _save_memories(path: Path, memories: List[Dict[str, Any]]):
    """Save memories to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(memories, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _next_id(memories: List[Dict[str, Any]]) -> int:
    """Get the next available ID."""
    if not memories:
        return 1
    return max(m.get("id", 0) for m in memories) + 1


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _get_retriever():
    """Lazy import of the retrieval engine."""
    from contextual_memory.retrieval import get_retriever
    return get_retriever()


def _reindex_agent(agent_name: Optional[str]):
    """Re-index agent memories after a change."""
    try:
        retriever = _get_retriever()
        retriever.index_agent_memory(agent_name)
    except Exception as e:
        logger.warning(f"Re-indexing failed for agent '{agent_name}': {e}")


def _error(msg: str) -> Dict[str, Any]:
    return {"content": [{"type": "text", "text": msg}], "is_error": True}


def _simple_score(query: str, memory: Dict[str, Any]) -> float:
    """Simple keyword-based scoring for fallback search."""
    query_lower = query.lower()
    query_words = set(query_lower.split())
    best = 0.0

    for trigger in memory.get("triggers", []):
        tl = trigger.lower()
        if query_lower in tl:
            best = max(best, 1.0)
        elif tl in query_lower:
            best = max(best, 0.9)
        else:
            tw = set(tl.split())
            overlap = len(query_words & tw)
            if overlap:
                best = max(best, overlap / max(len(query_words), len(tw)))

    # Content substring match (lower weight)
    if query_lower in memory.get("content", "").lower():
        best = max(best, 0.5)

    return best


def _format_brief(m: Dict[str, Any], score: float = 0.0) -> str:
    """Format a memory for brief display."""
    triggers = ", ".join(f'"{t}"' for t in m.get("triggers", [])[:3])
    extra = len(m.get("triggers", [])) - 3
    if extra > 0:
        triggers += f" +{extra} more"

    snippet = m.get("content", "")[:100]
    if len(m.get("content", "")) > 100:
        snippet += "..."

    flags = []
    if m.get("always_load"):
        flags.append("always_load")
    if m.get("private"):
        flags.append("private")
    flags_str = f" [{', '.join(flags)}]" if flags else ""

    score_str = f" (score: {score:.2f})" if score > 0 else ""

    return f"**#{m['id']}**{score_str}{flags_str} — {triggers}\n  {snippet}"


def _format_full(m: Dict[str, Any], score: float = 0.0) -> str:
    """Format a memory for full display."""
    triggers = ", ".join(f'"{t}"' for t in m.get("triggers", []))

    flags = []
    if m.get("always_load"):
        flags.append("always_load")
    if m.get("private"):
        flags.append("private")
    flags_str = f" [{', '.join(flags)}]" if flags else ""

    extras = []
    if m.get("type"):
        extras.append(f"type: {m['type']}")
    if m.get("confidence") is not None:
        extras.append(f"confidence: {m['confidence']}")
    extras_str = f" | {' | '.join(extras)}" if extras else ""

    score_str = f" (score: {score:.2f})" if score > 0 else ""

    return (
        f"### #{m['id']}{score_str}{flags_str}{extras_str}\n"
        f"Triggers: {triggers}\n"
        f"Created: {m.get('created', '?')} | Updated: {m.get('updated', '?')}\n\n"
        f"{m.get('content', '')}"
    )


# ── memory_create ──────────────────────────────────────────────────────────────

@register_tool("memory")
@tool(
    name="memory_create",
    description="""Create a new memory in your unified memory store.

Memories are stored as JSON entries with trigger phrases for retrieval.
Set always_load=true for memories injected into every conversation (replaces old memory.md).
Set always_load=false (default) for contextual memories retrieved only when relevant.

Write triggers as phrases someone might search for — "User's package manager preference", not just "bun".
Each memory gets a unique ID for use with memory_update and memory_delete.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The memory content (markdown ok).",
            },
            "triggers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "1-7 retrieval trigger phrases.",
                "minItems": 1,
                "maxItems": 7,
            },
            "always_load": {
                "type": "boolean",
                "description": "If true, loaded into every conversation. Default: false.",
                "default": False,
            },
            "private": {
                "type": "boolean",
                "description": "If true, hidden from cross-agent search. Default: false.",
                "default": False,
            },
            "confidence": {
                "type": "number",
                "description": "0.0-1.0. 1.0 = user stated. 0.7 = inferred.",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "type": {
                "type": "string",
                "description": "Category: fact, preference, procedure, project, decision, pattern, reflection.",
            },
        },
        "required": ["content", "triggers"],
    },
)
async def memory_create(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new memory entry."""
    try:
        content = args.get("content", "").strip()
        triggers = args.get("triggers", [])
        always_load = args.get("always_load", False)
        private = args.get("private", False)
        confidence = args.get("confidence")
        mem_type = args.get("type")
        agent_name = args.get("_agent_name")
        author = _agent_label(args)

        # Handle triggers as JSON string (from MCP layer) or list
        if isinstance(triggers, str):
            try:
                triggers = json.loads(triggers)
            except json.JSONDecodeError:
                triggers = [triggers]

        if not content:
            return _error("content is required")
        if not triggers or len(triggers) < 1:
            return _error("At least 1 trigger phrase is required")
        if len(triggers) > 7:
            return _error("Maximum 7 trigger phrases allowed")

        path = _resolve_memories_path(args)
        memories = _load_memories(path)

        now = _now_iso()
        memory: Dict[str, Any] = {
            "id": _next_id(memories),
            "triggers": triggers,
            "content": content,
            "always_load": always_load,
            "private": private,
            "created": now,
            "updated": now,
        }
        if confidence is not None:
            memory["confidence"] = confidence
        if mem_type:
            memory["type"] = mem_type

        memories.append(memory)
        _save_memories(path, memories)
        _reindex_agent(agent_name)

        trigger_str = ", ".join(f'"{t}"' for t in triggers)
        al_str = " [always_load]" if always_load else ""
        response = (
            f"Created memory #{memory['id']}{al_str}\n"
            f"Triggers: {trigger_str}\n"
            f"Content: {content[:100]}{'...' if len(content) > 100 else ''}"
        )

        logger.info(f"[{author}] memory_create: #{memory['id']} - {triggers[0]}")
        return {"content": [{"type": "text", "text": response}]}

    except Exception as e:
        import traceback
        logger.error(f"memory_create error: {e}\n{traceback.format_exc()}")
        return _error(f"Error creating memory: {e}")


# ── memory_search ──────────────────────────────────────────────────────────────

@register_tool("memory")
@tool(
    name="memory_search",
    description="""Search your memories by query.

Returns matching memories ranked by relevance. Use detail="full" for complete content.
Memory IDs are always shown — use them with memory_update or memory_delete.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for.",
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default: 10).",
                "default": 10,
            },
            "detail": {
                "type": "string",
                "enum": ["brief", "full"],
                "description": '"brief" (default) or "full" for complete content.',
                "default": "brief",
            },
        },
        "required": ["query"],
    },
)
async def memory_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search the calling agent's memories."""
    try:
        query = args.get("query", "").strip()
        max_results = args.get("max_results", 10)
        detail = args.get("detail", "brief")
        agent_name = args.get("_agent_name")

        if not query:
            return _error("query is required")

        path = _resolve_memories_path(args)
        memories = _load_memories(path)

        if not memories:
            return {"content": [{"type": "text", "text": "No memories yet. Use memory_create to save your first memory."}]}

        # Try hybrid search via retrieval engine
        scored: List[tuple] = []  # [(memory_dict, score)]
        used_engine = False

        try:
            retriever = _get_retriever()
            response = retriever.retrieve(
                query=query,
                agent_name=agent_name,
                budget_tokens=999999,
                min_score=0.1,
            )
            results = response.loaded + response.overflow
            if results:
                used_engine = True
                results.sort(key=lambda r: r.score, reverse=True)
                # Map results back to memory dicts by ID
                mem_by_id = {m["id"]: m for m in memories}
                for r in results[:max_results]:
                    mem_id = r.file.frontmatter.get("id")
                    if mem_id and mem_id in mem_by_id:
                        scored.append((mem_by_id[mem_id], r.score))
        except Exception as e:
            logger.debug(f"Retrieval engine unavailable, using fallback: {e}")

        # Fallback: simple keyword search
        if not used_engine:
            for m in memories:
                s = _simple_score(query, m)
                if s > 0:
                    scored.append((m, s))
            scored.sort(key=lambda x: x[1], reverse=True)
            scored = scored[:max_results]

        if not scored:
            return {"content": [{"type": "text", "text": f'No memories matching "{query}".'}]}

        lines = [f'## Memory Search: "{query}"\n']
        for m, score in scored:
            if detail == "full":
                lines.append(_format_full(m, score))
            else:
                lines.append(_format_brief(m, score))
            lines.append("")

        lines.append(f"*{len(scored)} result{'s' if len(scored) != 1 else ''}*")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    except Exception as e:
        import traceback
        logger.error(f"memory_search error: {e}\n{traceback.format_exc()}")
        return _error(f"Error searching memory: {e}")


# ── memory_update ──────────────────────────────────────────────────────────────

@register_tool("memory")
@tool(
    name="memory_update",
    description="""Update an existing memory by ID. Only fields you provide are changed.

Use memory_search to find the ID first, then update specific fields.""",
    input_schema={
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "The memory ID to update.",
            },
            "content": {
                "type": "string",
                "description": "New content (replaces existing).",
            },
            "triggers": {
                "type": "array",
                "items": {"type": "string"},
                "description": "New trigger phrases (replaces existing).",
                "minItems": 1,
                "maxItems": 7,
            },
            "always_load": {
                "type": "boolean",
                "description": "Set always_load status.",
            },
            "private": {
                "type": "boolean",
                "description": "Set private status.",
            },
            "confidence": {
                "type": "number",
                "description": "Update confidence (0.0-1.0).",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "type": {
                "type": "string",
                "description": "Update category.",
            },
        },
        "required": ["id"],
    },
)
async def memory_update(args: Dict[str, Any]) -> Dict[str, Any]:
    """Update an existing memory."""
    try:
        mem_id = args.get("id")
        agent_name = args.get("_agent_name")
        author = _agent_label(args)

        if mem_id is None:
            return _error("id is required")

        path = _resolve_memories_path(args)
        memories = _load_memories(path)

        # Find the memory
        target_idx = None
        for i, m in enumerate(memories):
            if m.get("id") == mem_id:
                target_idx = i
                break

        if target_idx is None:
            return _error(f"Memory #{mem_id} not found")

        target = memories[target_idx]

        # Handle triggers as JSON string (from MCP layer) or list
        if "triggers" in args and isinstance(args["triggers"], str):
            try:
                args["triggers"] = json.loads(args["triggers"])
            except json.JSONDecodeError:
                args["triggers"] = [args["triggers"]]

        # Apply updates (only fields that are explicitly passed)
        changed = []
        updatable = ["content", "triggers", "always_load", "private", "confidence", "type"]
        for field in updatable:
            if field in args and args[field] is not None:
                target[field] = args[field]
                changed.append(field)

        if not changed:
            return _error("No fields to update. Provide at least one field to change.")

        target["updated"] = _now_iso()
        memories[target_idx] = target
        _save_memories(path, memories)

        # Re-index (triggers or content may have changed)
        _reindex_agent(agent_name)

        logger.info(f"[{author}] memory_update: #{mem_id} changed {changed}")
        return {"content": [{"type": "text", "text": f"Updated memory #{mem_id}: {', '.join(changed)}\n\n{_format_brief(target)}"}]}

    except Exception as e:
        import traceback
        logger.error(f"memory_update error: {e}\n{traceback.format_exc()}")
        return _error(f"Error updating memory: {e}")


# ── memory_delete ──────────────────────────────────────────────────────────────

@register_tool("memory")
@tool(
    name="memory_delete",
    description="""Delete a memory by ID. Returns the deleted memory's triggers for verification.""",
    input_schema={
        "type": "object",
        "properties": {
            "id": {
                "type": "integer",
                "description": "The memory ID to delete.",
            },
        },
        "required": ["id"],
    },
)
async def memory_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete a memory."""
    try:
        mem_id = args.get("id")
        agent_name = args.get("_agent_name")
        author = _agent_label(args)

        if mem_id is None:
            return _error("id is required")

        path = _resolve_memories_path(args)
        memories = _load_memories(path)

        target = None
        new_memories = []
        for m in memories:
            if m.get("id") == mem_id:
                target = m
            else:
                new_memories.append(m)

        if target is None:
            return _error(f"Memory #{mem_id} not found")

        _save_memories(path, new_memories)
        _reindex_agent(agent_name)

        triggers = ", ".join(f'"{t}"' for t in target.get("triggers", []))
        snippet = target.get("content", "")[:100]
        logger.info(f"[{author}] memory_delete: #{mem_id}")
        return {"content": [{"type": "text", "text": f"Deleted memory #{mem_id}\nTriggers: {triggers}\nContent: {snippet}{'...' if len(target.get('content', '')) > 100 else ''}"}]}

    except Exception as e:
        import traceback
        logger.error(f"memory_delete error: {e}\n{traceback.format_exc()}")
        return _error(f"Error deleting memory: {e}")


# ── memory_search_agent ────────────────────────────────────────────────────────

@register_tool("memory")
@tool(
    name="memory_search_agent",
    description="""Search another agent's non-private memories.

Omit agent to search ALL agents' memories. Private memories are always excluded.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "What to search for.",
            },
            "agent": {
                "type": "string",
                "description": 'Agent name (e.g., "coder", "deep_research"). Omit for all.',
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum results (default: 5).",
                "default": 5,
            },
        },
        "required": ["query"],
    },
)
async def memory_search_agent(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search another agent's memories."""
    try:
        query = args.get("query", "").strip()
        target_agent = args.get("agent")
        max_results = args.get("max_results", 5)
        calling_agent = args.get("_agent_name")

        if not query:
            return _error("query is required")

        # Determine which agents to search
        if target_agent:
            agents_to_search = [target_agent]
        else:
            agents_dir = Path(CLAUDE_DIR) / "agents"
            agents_to_search = []
            if agents_dir.exists():
                for agent_dir in sorted(agents_dir.iterdir()):
                    if (
                        agent_dir.is_dir()
                        and agent_dir.name != calling_agent
                        and (agent_dir / "config.yaml").exists()
                        and (
                            (agent_dir / "memories.json").exists()
                            or (agent_dir / "memory").is_dir()
                        )
                    ):
                        agents_to_search.append(agent_dir.name)

        all_results: List[tuple] = []  # (agent_name, memory_dict, score)

        for agent_name in agents_to_search:
            # Try retrieval engine first
            try:
                retriever = _get_retriever()
                response = retriever.retrieve(
                    query=query,
                    agent_name=agent_name,
                    budget_tokens=999999,
                    min_score=0.1,
                )
                # Map back to memory dicts
                json_path = Path(CLAUDE_DIR) / "agents" / agent_name / "memories.json"
                mem_by_id = {}
                if json_path.exists():
                    mems = _load_memories(json_path)
                    mem_by_id = {m["id"]: m for m in mems}

                for result in response.loaded + response.overflow:
                    fm = result.file.frontmatter
                    if fm.get("private", False):
                        continue
                    mem_id = fm.get("id")
                    mem_dict = mem_by_id.get(mem_id) if mem_id else None
                    if mem_dict:
                        all_results.append((agent_name, mem_dict, result.score))
                    else:
                        # Construct from MemoryFile (for .md file fallback during migration)
                        synthetic = {
                            "id": mem_id or "?",
                            "triggers": result.file.triggers,
                            "content": result.file.content,
                            "always_load": fm.get("always_load", False),
                            "private": False,
                        }
                        all_results.append((agent_name, synthetic, result.score))
                continue
            except Exception as e:
                logger.debug(f"Retrieval engine failed for {agent_name}: {e}")

            # Fallback: direct JSON scan
            json_path = Path(CLAUDE_DIR) / "agents" / agent_name / "memories.json"
            if json_path.exists():
                mems = _load_memories(json_path)
                for m in mems:
                    if m.get("private", False):
                        continue
                    s = _simple_score(query, m)
                    if s > 0:
                        all_results.append((agent_name, m, s))

        # Sort by score
        all_results.sort(key=lambda x: x[2], reverse=True)
        all_results = all_results[:max_results]

        if not all_results:
            return {"content": [{"type": "text", "text": f'No memories matching "{query}" in other agents\' memories.'}]}

        lines = [f'## Cross-Agent Memory Search: "{query}"\n']
        for agent_name, m, score in all_results:
            mem_id = m.get("id", "?")
            triggers = ", ".join(f'"{t}"' for t in m.get("triggers", [])[:3])
            snippet = m.get("content", "")[:300]
            if len(m.get("content", "")) > 300:
                snippet += "..."

            lines.append(f"### [{agent_name}] #{mem_id} (score: {score:.2f})")
            lines.append(f"Triggers: {triggers}")
            lines.append(f"```\n{snippet}\n```\n")

        lines.append(f"*{len(all_results)} result{'s' if len(all_results) != 1 else ''}*")
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    except Exception as e:
        import traceback
        logger.error(f"memory_search_agent error: {e}\n{traceback.format_exc()}")
        return _error(f"Error searching agent memory: {e}")
