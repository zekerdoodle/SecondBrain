"""
Gardener Background Agent Runner

Maintains the memory store:
1. Identifies and merges duplicate memories
2. Suggests thread consolidations
3. Adjusts importance based on usage patterns
4. Flags stale memories for review

Runs daily via scheduler.
"""

import json
import logging
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta

logger = logging.getLogger("ltm.gardener")

# Load system prompt from gardener.md
_PROMPT_PATH = Path(__file__).parent / "gardener.md"

def _get_system_prompt() -> str:
    """Load the gardener system prompt from file."""
    if _PROMPT_PATH.exists():
        return _PROMPT_PATH.read_text()
    else:
        raise FileNotFoundError(f"Gardener prompt not found at {_PROMPT_PATH}")


# JSON Schema for structured output
GARDENER_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "duplicates_to_merge": {
            "type": "array",
            "description": "Duplicate memories to merge",
            "items": {
                "type": "object",
                "properties": {
                    "keep_id": {"type": "string", "description": "Memory ID to keep"},
                    "remove_ids": {"type": "array", "items": {"type": "string"}, "description": "Memory IDs to remove"},
                    "merged_content": {"type": "string", "description": "Optional improved merged content"},
                    "reason": {"type": "string", "description": "Why these are duplicates"}
                },
                "required": ["keep_id", "remove_ids", "reason"]
            }
        },
        "threads_to_consolidate": {
            "type": "array",
            "description": "Threads that should be merged",
            "items": {
                "type": "object",
                "properties": {
                    "source_thread_ids": {"type": "array", "items": {"type": "string"}},
                    "new_name": {"type": "string"},
                    "new_description": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["source_thread_ids", "new_name", "reason"]
            }
        },
        "threads_to_split": {
            "type": "array",
            "description": "Threads that should be split into multiple",
            "items": {
                "type": "object",
                "properties": {
                    "source_thread_id": {"type": "string"},
                    "new_threads": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "memory_ids": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["name", "memory_ids"]
                        }
                    },
                    "reason": {"type": "string"}
                },
                "required": ["source_thread_id", "new_threads", "reason"]
            }
        },
        "importance_adjustments": {
            "type": "array",
            "description": "Memories whose importance should change",
            "items": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "current_importance": {"type": "integer"},
                    "suggested_importance": {"type": "integer"},
                    "reason": {"type": "string"}
                },
                "required": ["memory_id", "suggested_importance", "reason"]
            }
        },
        "stale_memories": {
            "type": "array",
            "description": "Memories that may be outdated",
            "items": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "content_preview": {"type": "string"},
                    "recommendation": {"type": "string", "enum": ["archive", "delete", "keep"]},
                    "reason": {"type": "string"}
                },
                "required": ["memory_id", "recommendation", "reason"]
            }
        },
        "orphaned_atoms": {
            "type": "array",
            "description": "Memories not in any thread",
            "items": {
                "type": "object",
                "properties": {
                    "memory_id": {"type": "string"},
                    "content_preview": {"type": "string"},
                    "recommendation": {"type": "string", "enum": ["delete", "assign_to_thread"]},
                    "suggested_thread": {"type": "string"},
                    "reason": {"type": "string"}
                },
                "required": ["memory_id", "recommendation", "reason"]
            }
        },
        "summary": {
            "type": "string",
            "description": "Brief summary of maintenance recommendations"
        }
    },
    "required": ["duplicates_to_merge", "threads_to_consolidate", "importance_adjustments", "stale_memories", "summary"]
}


def _format_memories_for_gardener(memories: List[Dict[str, Any]]) -> str:
    """Format memories for Gardener analysis."""
    lines = []
    for mem in memories:
        lines.append(
            f"- **ID**: {mem['id']}\n"
            f"  **Content**: {mem['content'][:300]}\n"
            f"  **Importance**: {mem.get('importance', 50)}\n"
            f"  **Created**: {mem.get('created_at', 'unknown')[:10]}\n"
            f"  **Tags**: {', '.join(mem.get('tags', []))}"
        )
    return "\n".join(lines)


def _format_threads_for_gardener(threads: List[Dict[str, Any]]) -> str:
    """Format threads for Gardener analysis."""
    lines = []
    for t in threads:
        lines.append(
            f"- **ID**: {t['id']}\n"
            f"  **Name**: {t['name']}\n"
            f"  **Description**: {t.get('description', '')[:200]}\n"
            f"  **Memory Count**: {len(t.get('memory_ids', []))}\n"
            f"  **Last Updated**: {t.get('last_updated', 'unknown')[:10]}"
        )
    return "\n".join(lines)


async def run_gardener(
    memories: List[Dict[str, Any]],
    threads: List[Dict[str, Any]],
    usage_stats: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Run the Gardener agent for memory maintenance.

    Uses Claude Agent SDK with OAuth (same auth as main agent).
    Uses structured outputs for validated JSON response.

    Args:
        memories: All atomic memories
        threads: All threads
        usage_stats: Optional usage statistics

    Returns:
        Maintenance recommendations
    """
    from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

    if not memories:
        logger.info("No memories to maintain")
        return {"summary": "No memories to maintain"}

    # Load system prompt
    system_prompt = _get_system_prompt()

    # Build the prompt
    prompt = f"""## Memory Store Analysis

### Atomic Memories ({len(memories)} total)

{_format_memories_for_gardener(memories)}

### Threads ({len(threads)} total)

{_format_threads_for_gardener(threads)}

### Usage Statistics
{json.dumps(usage_stats or {"note": "No usage stats available"}, indent=2)}

---

Analyze this memory store and suggest maintenance actions. Focus on:
1. Duplicate or near-duplicate memories
2. Threads that could be consolidated
3. Importance scores that seem off
4. Stale or outdated content"""

    logger.info(f"Running Gardener on {len(memories)} memories, {len(threads)} threads")

    try:
        async for message in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="sonnet",  # Better quality for memory maintenance
                system_prompt=system_prompt,
                max_turns=2,  # Need 2 turns for structured output (tool call + result)
                permission_mode="bypassPermissions",
                allowed_tools=[],
                output_format={
                    "type": "json_schema",
                    "schema": GARDENER_OUTPUT_SCHEMA
                }
            )
        ):
            # With structured outputs, we get validated JSON directly
            if isinstance(message, ResultMessage) and message.structured_output:
                result = message.structured_output
                result.setdefault("duplicates_to_merge", [])
                result.setdefault("threads_to_consolidate", [])
                result.setdefault("importance_adjustments", [])
                result.setdefault("stale_memories", [])
                result.setdefault("summary", "")
                logger.info(
                    f"Gardener found: {len(result['duplicates_to_merge'])} duplicates, "
                    f"{len(result['threads_to_consolidate'])} consolidations, "
                    f"{len(result['importance_adjustments'])} importance adjustments, "
                    f"{len(result['stale_memories'])} stale memories"
                )
                return result

        # Fallback if no structured output received
        logger.warning("No structured output received from Gardener")
        return {"summary": "No structured output received", "error": "No structured output"}

    except Exception as e:
        logger.error(f"Gardener agent failed: {e}")
        return {"error": str(e)}


async def apply_gardener_results(
    results: Dict[str, Any],
    auto_apply: bool = False
) -> Dict[str, Any]:
    """
    Apply the Gardener's recommendations.

    Args:
        results: Gardener output
        auto_apply: If True, automatically apply non-destructive changes

    Returns:
        Stats about what was applied
    """
    # Import from the original location
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager
    from memory_throttle import mark_gardener_run

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    stats = {
        "duplicates_merged": 0,
        "threads_consolidated": 0,
        "importance_adjusted": 0,
        "marked_stale": 0,
        "skipped": 0,
        "errors": []
    }

    # Apply importance adjustments (safe operation)
    if auto_apply:
        for adj in results.get("importance_adjustments", []):
            try:
                memory_id = adj.get("memory_id")
                new_importance = adj.get("suggested_importance")
                if memory_id and new_importance is not None:
                    atom = atom_mgr.update(memory_id, importance=new_importance)
                    if atom:
                        stats["importance_adjusted"] += 1
            except Exception as e:
                stats["errors"].append(f"Importance adjustment failed: {e}")

    # Log but don't auto-apply destructive operations
    for dup in results.get("duplicates_to_merge", []):
        logger.info(
            f"Duplicate suggestion: keep {dup.get('keep_id')}, "
            f"remove {dup.get('remove_ids')}: {dup.get('reason')}"
        )
        stats["skipped"] += 1

    for consol in results.get("threads_to_consolidate", []):
        logger.info(
            f"Thread consolidation suggestion: {consol.get('source_thread_ids')} -> "
            f"{consol.get('new_name')}: {consol.get('reason')}"
        )
        stats["skipped"] += 1

    for stale in results.get("stale_memories", []):
        logger.info(
            f"Stale memory: {stale.get('memory_id')} - "
            f"{stale.get('recommendation')}: {stale.get('reason')}"
        )
        stats["marked_stale"] += 1

    # Mark that Gardener ran
    mark_gardener_run()

    return stats


async def run_gardener_cycle(auto_apply: bool = False) -> Dict[str, Any]:
    """
    Run a complete Gardener cycle.

    Returns stats about the run.
    """
    # Import from the original location
    import sys
    scripts_dir = Path(__file__).parent.parent.parent.parent / "scripts" / "ltm"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from atomic_memory import get_atomic_manager
    from thread_memory import get_thread_manager

    atom_mgr = get_atomic_manager()
    thread_mgr = get_thread_manager()

    # Get all data
    memories = [
        {
            "id": m.id,
            "content": m.content,
            "importance": m.importance,
            "created_at": m.created_at,
            "tags": m.tags
        }
        for m in atom_mgr.list_all()
    ]

    threads = [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "memory_ids": t.memory_ids,
            "last_updated": t.last_updated
        }
        for t in thread_mgr.list_all()
    ]

    if not memories:
        return {"status": "empty", "message": "No memories to maintain"}

    # Run analysis
    results = await run_gardener(memories, threads)

    if "error" in results:
        return {"status": "error", "error": results["error"]}

    # Apply results
    stats = await apply_gardener_results(results, auto_apply=auto_apply)

    return {
        "status": "completed",
        "memories_analyzed": len(memories),
        "threads_analyzed": len(threads),
        "summary": results.get("summary", ""),
        **stats
    }


# Synchronous wrapper
def run_gardener_sync(auto_apply: bool = False) -> Dict[str, Any]:
    """Synchronous wrapper for run_gardener_cycle."""
    return asyncio.run(run_gardener_cycle(auto_apply=auto_apply))
