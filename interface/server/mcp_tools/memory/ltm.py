"""
Long-Term Memory (LTM) tools.

Semantic memory storage with embeddings for retrieval.
"""

import os
import sys
from typing import Any, Dict

from claude_agent_sdk import tool

from ..registry import register_tool

# Add scripts directory to path
SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../../.claude/scripts"))
LTM_DIR = os.path.join(SCRIPTS_DIR, "ltm")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)
if LTM_DIR not in sys.path:
    sys.path.insert(0, LTM_DIR)


@register_tool("memory")
@tool(
    name="ltm_search",
    description="""Search long-term memory for relevant facts and context.

This searches the semantic memory store (atomic memories and threads) using embeddings.
Use this to find information that was previously learned about the user, their preferences,
past conversations, or any other stored knowledge.

Returns both individual facts and organized threads of related information.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query - what you're looking for"},
            "k": {"type": "integer", "description": "Number of results (default: 10)", "default": 10},
            "include_threads": {"type": "boolean", "description": "Include thread results (default: true)", "default": True}
        },
        "required": ["query"]
    }
)
async def ltm_search(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search long-term memory."""
    try:
        from ltm.memory_retrieval import search_memories

        query = args.get("query", "")
        k = args.get("k", 10)
        include_threads = args.get("include_threads", True)

        if not query:
            return {"content": [{"type": "text", "text": "Query is required"}], "is_error": True}

        results = search_memories(query, k=k, include_threads=include_threads)

        if not results:
            return {"content": [{"type": "text", "text": f"No memories found for: {query}"}]}

        output = [f"## Memory Search Results for: {query}\n"]
        for r in results:
            if r["type"] == "atomic":
                output.append(
                    f"**[Memory]** (score: {r['score']:.2f})\n"
                    f"{r['content']}\n"
                )
            else:
                output.append(
                    f"**[Thread: {r['name']}]** ({r['memory_count']} memories, score: {r['score']:.2f})\n"
                    f"{r['description']}\n"
                )

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_get_context",
    description="""Get relevant memory context for a query, formatted for prompt injection.

This is the main way to retrieve memory context for a conversation. It uses a thread-first
strategy: finds relevant threads, includes whole threads (to preserve context), and fills
remaining budget with individual relevant facts.

Uses purely semantic scoring — recency is handled separately by the recent-memory block.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Context query (usually the user's message)"},
            "token_budget": {"type": "integer", "description": "Max tokens for memory context (default: 20000)", "default": 20000}
        },
        "required": ["query"]
    }
)
async def ltm_get_context(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get memory context for prompt injection."""
    try:
        from ltm.memory_retrieval import get_memory_context

        query = args.get("query", "")
        token_budget = args.get("token_budget", 20000)

        if not query:
            return {"content": [{"type": "text", "text": "Query is required"}], "is_error": True}

        context = get_memory_context(query, token_budget=token_budget)
        formatted = context.format_for_prompt()

        if not formatted:
            return {"content": [{"type": "text", "text": "No relevant memory context found."}]}

        stats = (
            f"\n\n---\n*Memory stats: {len(context.threads)} threads, "
            f"{len(context.atomic_memories)} bonus facts, "
            f"~{context.total_tokens} tokens*"
        )

        return {"content": [{"type": "text", "text": formatted + stats}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_add_memory",
    description="""Add a new fact or insight to long-term memory.

Use this to explicitly save something important that should be remembered long-term.
The memory will be embedded for semantic search and can be organized into threads.""",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string", "description": "The fact or insight to remember"},
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional tags for categorization"
            },
            "thread_name": {"type": "string", "description": "Optional: add to this thread (creates if doesn't exist)"}
        },
        "required": ["content"]
    }
)
async def ltm_add_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add a memory to long-term storage."""
    try:
        from ltm.atomic_memory import get_atomic_manager
        from ltm.thread_memory import get_thread_manager

        content = args.get("content", "").strip()
        tags = args.get("tags", [])
        thread_name = args.get("thread_name")

        if not content:
            return {"content": [{"type": "text", "text": "Content is required"}], "is_error": True}

        atom_mgr = get_atomic_manager()

        # Check for duplicates
        existing = atom_mgr.find_similar(content, threshold=0.88)
        if existing:
            return {"content": [{"type": "text", "text": f"Similar memory already exists (ID: {existing.id}): {existing.content[:100]}..."}]}

        # Create the memory
        atom = atom_mgr.create(
            content=content,
            tags=tags
        )

        result = f"Created memory (ID: {atom.id})"

        # Add to thread if specified
        if thread_name:
            thread_mgr = get_thread_manager()
            thread = thread_mgr.find_or_create_thread(thread_name, f"Thread for {thread_name}")
            thread_mgr.add_memory_to_thread(thread.id, atom.id)
            result += f"\nAdded to thread: {thread_name}"

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_create_thread",
    description="""Create a new thread to organize related memories.

Threads are like playlists - they group related facts together for better context retrieval.
When a thread is retrieved, ALL its memories come together.""",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Thread name"},
            "description": {"type": "string", "description": "What this thread is about"},
            "memory_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: memory IDs to add initially"
            }
        },
        "required": ["name", "description"]
    }
)
async def ltm_create_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new thread."""
    try:
        from ltm.thread_memory import get_thread_manager

        name = args.get("name", "").strip()
        description = args.get("description", "").strip()
        memory_ids = args.get("memory_ids", [])

        if not name or not description:
            return {"content": [{"type": "text", "text": "Name and description are required"}], "is_error": True}

        thread_mgr = get_thread_manager()

        # Check if exists
        existing = thread_mgr.get_by_name(name)
        if existing:
            return {"content": [{"type": "text", "text": f"Thread '{name}' already exists (ID: {existing.id})"}]}

        thread = thread_mgr.create(name=name, description=description, memory_ids=memory_ids)

        return {"content": [{"type": "text", "text": f"Created thread '{name}' (ID: {thread.id}) with {len(memory_ids)} memories"}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_stats",
    description="""Get statistics about the long-term memory system.

Shows counts, distributions, and health metrics for the memory store.""",
    input_schema={"type": "object", "properties": {}}
)
async def ltm_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get memory system statistics."""
    try:
        from ltm.atomic_memory import get_atomic_manager
        from ltm.thread_memory import get_thread_manager
        from ltm.embeddings import get_embedding_manager
        from ltm.memory_throttle import get_buffer_stats

        atom_mgr = get_atomic_manager()
        thread_mgr = get_thread_manager()
        emb_mgr = get_embedding_manager()

        atom_stats = atom_mgr.stats()
        thread_stats = thread_mgr.stats()
        emb_stats = emb_mgr.stats()
        buffer_stats = get_buffer_stats()

        output = f"""## Long-Term Memory Statistics

### Atomic Memories
- Total: {atom_stats['total_memories']}
- With versions: {atom_stats['with_versions']}
- Tags: {', '.join(atom_stats['tags'][:10]) or 'None'}

### Threads
- Total: {thread_stats['total_threads']}
- Empty: {thread_stats['empty_threads']}
- Avg memories per thread: {thread_stats['avg_memories_per_thread']:.1f}

### Embeddings
- Total vectors: {emb_stats['total_embeddings']}
- Cache size: {emb_stats['cache_size']}

### Processing Buffer
- Pending exchanges: {buffer_stats['buffer_size']}
- Can run Librarian now: {buffer_stats['can_run_now']}
- Minutes until next run: {buffer_stats['minutes_until_next_run']}
- Total Librarian runs: {buffer_stats['total_librarian_runs']}
- Total exchanges processed: {buffer_stats['total_exchanges_processed']}
"""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_process_now",
    description="""Manually trigger the Librarian to process pending exchanges.

Normally the Librarian runs automatically every 20 minutes if there are pending exchanges.
Use this to force immediate processing.""",
    input_schema={"type": "object", "properties": {}}
)
async def ltm_process_now(args: Dict[str, Any]) -> Dict[str, Any]:
    """Force Librarian to run now."""
    try:
        from ltm.memory_throttle import force_librarian_ready, get_buffer_stats
        from ltm.librarian_agent import run_librarian_cycle

        # Check buffer first
        stats = get_buffer_stats()
        if stats["buffer_size"] == 0:
            return {"content": [{"type": "text", "text": "No exchanges in buffer to process."}]}

        # Force ready and run
        force_librarian_ready()
        result = await run_librarian_cycle()

        output = f"""## Librarian Run Complete

- Status: {result.get('status')}
- Exchanges processed: {result.get('exchanges_processed', 0)}
- Memories created: {result.get('memories_created', 0)}
- Duplicates skipped: {result.get('memories_skipped_duplicate', 0)}
- Threads created: {result.get('threads_created', 0)}
- Threads updated: {result.get('threads_updated', 0)}
"""
        if result.get('errors'):
            output += f"\nErrors: {result['errors']}"

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_run_gardener",
    description="""Run the Gardener for memory maintenance.

The Gardener processes unassigned atoms and maintains thread organization:
- Assigns atoms to threads with confidence scoring
- Detects superseded facts and versions atoms
- Splits oversized threads, merges duplicates
- Processes triage queue for low-confidence assignments

Optionally pass atom_ids to process specific atoms.""",
    input_schema={
        "type": "object",
        "properties": {
            "mode": {
                "type": "string",
                "enum": ["default", "maintenance", "health"],
                "description": "Run mode: 'default' processes atoms/triage, 'maintenance' runs full health check + triage + orphan processing, 'health' returns thread health stats only"
            },
            "atom_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional: specific atom IDs to process (for 'default' mode)"
            }
        }
    }
)
async def ltm_run_gardener(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run Gardener maintenance."""
    try:
        from ltm.gardener_agent import run_gardener_batched, run_maintenance, get_thread_health

        mode = args.get("mode", "default")
        atom_ids = args.get("atom_ids", [])

        if mode == "maintenance":
            result = await run_maintenance()
            health = result.get("health", {})
            output = f"""## Gardener Maintenance Complete

### Thread Health
- Threads: {health.get('total_threads', 0)} | Atoms: {health.get('total_atoms', 0)}
- Avg thread size: {health.get('avg_thread_size', 0)} | Max: {health.get('max_thread_size', 0)}
- Orphan atoms: {health.get('orphan_atoms', 0)} | Triage queue: {health.get('triage_queue', 0)}
"""
            if health.get('oversized_threads'):
                output += "\n### Oversized Threads (>50 atoms)\n"
                for t in health['oversized_threads']:
                    output += f"- {t['name']}: {t['size']} atoms\n"

            orphan = result.get("orphan_processing", {})
            if orphan.get("status") != "none_needed":
                output += f"\n### Orphan Processing\n- Assigned: {orphan.get('assigned', 0)}\n"

            triage = result.get("triage_processing", {})
            if triage.get("status") != "none_needed":
                output += f"\n### Triage Processing\n- Re-evaluated: {triage.get('triage_processed', 0)}\n"

            return {"content": [{"type": "text", "text": output}]}

        elif mode == "health":
            health = get_thread_health()
            import json
            return {"content": [{"type": "text", "text": f"## Thread Health\n```json\n{json.dumps(health, indent=2)}\n```"}]}

        else:
            result = await run_gardener_batched(atom_ids=atom_ids)
            output = f"""## Gardener Run Complete

- Status: {result.get('status')}
- Atoms processed: {result.get('atoms_processed', 0)}
- Triage processed: {result.get('triage_processed', 0)}
- Threads created: {result.get('threads_created', 0)}
- Assignments made: {result.get('assigned', 0)}
- Superseded: {result.get('superseded', 0)}
"""
            if result.get('errors'):
                output += f"\nErrors: {result['errors']}"

            return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_buffer_exchange",
    description="""Add a conversation exchange to the processing buffer.

This is called automatically after each conversation turn. The exchange will be
processed by the Librarian within 20 minutes.

You normally don't need to call this manually - it's for the system integration.""",
    input_schema={
        "type": "object",
        "properties": {
            "user_message": {"type": "string", "description": "The user's message"},
            "assistant_message": {"type": "string", "description": "The assistant's response"},
            "session_id": {"type": "string", "description": "Session identifier"}
        },
        "required": ["user_message", "assistant_message"]
    }
)
async def ltm_buffer_exchange(args: Dict[str, Any]) -> Dict[str, Any]:
    """Buffer an exchange for later processing."""
    try:
        from ltm.memory_throttle import add_exchange_to_buffer, get_buffer_stats
        from datetime import datetime

        user_msg = args.get("user_message", "")
        assistant_msg = args.get("assistant_message", "")
        session_id = args.get("session_id", "unknown")

        if not user_msg or not assistant_msg:
            return {"content": [{"type": "text", "text": "Both user_message and assistant_message are required"}], "is_error": True}

        exchange = {
            "user_message": user_msg,
            "assistant_message": assistant_msg,
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        }

        should_run = add_exchange_to_buffer(exchange)
        stats = get_buffer_stats()

        result = f"Exchange buffered. Buffer size: {stats['buffer_size']}"
        if should_run:
            result += " (Librarian ready to run)"

        return {"content": [{"type": "text", "text": result}]}

    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_backfill",
    description="""Process existing chat history through the Librarian to extract memories.

This runs the Librarian against all previous conversations to build up the memory store
from historical data. Useful when first setting up the LTM system or after clearing memories.

The backfill tracks which chats have been processed, so it's safe to run multiple times.
Only unprocessed chats will be analyzed.""",
    input_schema={
        "type": "object",
        "properties": {
            "dry_run": {"type": "boolean", "description": "Preview what would be processed without creating memories", "default": False},
            "batch_size": {"type": "integer", "description": "Exchanges per Librarian batch (default: 10)", "default": 10},
            "limit": {"type": "integer", "description": "Maximum chats to process (default: all)"},
            "reprocess": {"type": "boolean", "description": "Reprocess already-processed chats", "default": False}
        }
    }
)
async def ltm_backfill(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run backfill of chat history."""
    try:
        from ltm.backfill_chats import run_backfill, get_all_chats, load_backfill_state, extract_exchanges, load_chat

        dry_run = args.get("dry_run", False)
        batch_size = args.get("batch_size", 10)
        limit = args.get("limit")
        reprocess = args.get("reprocess", False)

        # Get overview first
        all_chats = get_all_chats()
        state = load_backfill_state()
        processed = set(state.get("processed_chats", []))

        total_exchanges = 0
        for chat_id, path in all_chats:
            if not reprocess and chat_id in processed:
                continue
            chat = load_chat(path)
            if chat:
                total_exchanges += len(extract_exchanges(chat, chat_id=chat_id))

        if total_exchanges == 0:
            return {"content": [{"type": "text", "text": "No unprocessed chats found. Use reprocess=true to reprocess all."}]}

        # Run backfill
        await run_backfill(
            dry_run=dry_run,
            batch_size=batch_size,
            limit=limit,
            skip_processed=not reprocess
        )

        # Get updated state
        new_state = load_backfill_state()

        output = f"""## Backfill {'Preview' if dry_run else 'Complete'}

- Total chats: {len(all_chats)}
- Previously processed: {len(processed)}
- Exchanges found: {total_exchanges}
- Total memories created (all time): {new_state.get('total_memories_created', 0)}
- Dry run: {dry_run}
"""

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_split_thread",
    description="""Split a mega-thread into more focused sub-threads.

Use this when a thread has grown too large or contains multiple distinct topics that
should be separated for better organization. The Gardener typically identifies threads
that need splitting.

This operation:
1. Creates new threads with the specified atoms
2. Removes those atoms from the source thread
3. Optionally deletes the source thread if it becomes empty

Note: Atoms can belong to multiple threads, so atoms are not "moved" but rather
"also assigned to" the new threads while being removed from the source.""",
    input_schema={
        "type": "object",
        "properties": {
            "source_thread_id": {
                "type": "string",
                "description": "ID of the thread to split"
            },
            "new_threads": {
                "type": "array",
                "description": "New threads to create from the split",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name for the new thread"},
                        "description": {"type": "string", "description": "Description of what this thread contains"},
                        "atom_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Memory IDs to include in this new thread"
                        }
                    },
                    "required": ["name", "description", "atom_ids"]
                }
            },
            "delete_source_if_empty": {
                "type": "boolean",
                "description": "Delete the source thread if all atoms are reassigned (default: true)",
                "default": True
            }
        },
        "required": ["source_thread_id", "new_threads"]
    }
)
async def ltm_split_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    """Split a thread into more focused sub-threads."""
    try:
        from ltm.thread_memory import get_thread_manager

        source_thread_id = args.get("source_thread_id", "")
        new_threads = args.get("new_threads", [])
        delete_source_if_empty = args.get("delete_source_if_empty", True)

        if not source_thread_id:
            return {"content": [{"type": "text", "text": "source_thread_id is required"}], "is_error": True}

        if not new_threads:
            return {"content": [{"type": "text", "text": "new_threads array is required"}], "is_error": True}

        thread_mgr = get_thread_manager()

        # Get source thread name for output
        source_thread = thread_mgr.get(source_thread_id)
        source_name = source_thread.name if source_thread else source_thread_id

        result = thread_mgr.split_thread(
            source_thread_id=source_thread_id,
            new_threads=new_threads,
            delete_source_if_empty=delete_source_if_empty
        )

        if not result["success"]:
            error_msg = "Thread split failed:\n" + "\n".join(f"- {e}" for e in result["errors"])
            return {"content": [{"type": "text", "text": error_msg}], "is_error": True}

        # Build success output
        output_lines = [f"## Thread Split Complete\n"]
        output_lines.append(f"**Source thread:** {source_name}")
        output_lines.append(f"**Atoms reassigned:** {result['atoms_reassigned']}")
        output_lines.append(f"**Source deleted:** {'Yes' if result['source_deleted'] else 'No'}\n")
        output_lines.append("**New threads created:**")
        for i, thread_id in enumerate(result["new_thread_ids"]):
            nt = new_threads[i]
            output_lines.append(f"- {nt['name']} ({len(nt['atom_ids'])} atoms) - ID: {thread_id}")

        return {"content": [{"type": "text", "text": "\n".join(output_lines)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_wipe_memory",
    description="""Wipe ALL memory state - complete reset of the LTM system.

WARNING: This is destructive and cannot be undone!

Clears:
- All atomic memories
- All threads
- All embeddings (FAISS index, metadata, cache)
- Backfill state (so backfill can be re-run)
- Exchange buffer
- Throttle state

Use this when you want to start fresh with the memory system.""",
    input_schema={
        "type": "object",
        "properties": {
            "confirm": {
                "type": "boolean",
                "description": "Must be true to confirm the wipe operation"
            }
        },
        "required": ["confirm"]
    }
)
async def ltm_wipe_memory(args: Dict[str, Any]) -> Dict[str, Any]:
    """Wipe all memory state."""
    try:
        confirm = args.get("confirm", False)

        if not confirm:
            return {"content": [{"type": "text", "text": "Wipe NOT executed. Set confirm=true to proceed with wiping all memory."}]}

        from ltm.atomic_memory import wipe_memory

        result = wipe_memory()

        output = f"""## Memory Wipe Complete

**Status:** {result['status']}

**Files deleted:**
{chr(10).join('- ' + f for f in result['files_deleted']) or '- None'}

**Directories deleted:**
{chr(10).join('- ' + d for d in result['directories_deleted']) or '- None'}
"""

        if result['errors']:
            output += f"""
**Errors:**
{chr(10).join('- ' + e for e in result['errors'])}
"""

        output += "\nMemory system has been reset. You can now run ltm_backfill to rebuild from chat history."

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_run_chronicler",
    description="""Run the Chronicler to summarize conversation threads.

The Chronicler reviews conversation-type threads and replaces their generic descriptions
with natural 2-3 sentence summaries of what the conversation covered. This improves
semantic search quality since thread descriptions are what get embedded.

The Chronicler tracks its last run timestamp and only processes threads that have been
updated since then. Safe to run repeatedly.""",
    input_schema={"type": "object", "properties": {}}
)
async def ltm_run_chronicler(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run the Chronicler to summarize conversation threads."""
    try:
        from ltm.chronicler_agent import run_chronicler_cycle

        result = await run_chronicler_cycle()

        output = f"""## Chronicler Run Complete

- Status: {result.get('status')}
- Threads found: {result.get('threads_found', 0)}
- Threads summarized: {result.get('threads_summarized', 0)}
- Threads failed: {result.get('threads_failed', 0)}
- Batches run: {result.get('batches_run', 0)}
"""
        if result.get('errors'):
            output += f"\nErrors: {result['errors']}"

        return {"content": [{"type": "text", "text": output}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("memory")
@tool(
    name="ltm_backfill_threads",
    description="""Reprocess all atoms through the Gardener to rebuild thread assignments.

Use this after a memory system redesign or when thread assignments need to be rebuilt.

Actions:
- "start": Begin backfill (snapshots current state, clears assignments, processes atoms)
- "status": Check backfill progress
- "continue": Continue after checkpoint validation
- "rollback": Restore previous thread assignments from snapshot

The backfill stops at 10% for validation. Review thread quality, then call with action="continue".""",
    input_schema={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "status", "continue", "rollback"],
                "description": "Action to perform"
            },
            "batch_size": {"type": "integer", "description": "Atoms per batch (default: 50)", "default": 50}
        },
        "required": ["action"]
    }
)
async def ltm_backfill_threads(args: Dict[str, Any]) -> Dict[str, Any]:
    """Run thread assignment backfill."""
    try:
        from ltm.backfill_thread_assignments import (
            run_backfill, continue_backfill, rollback_assignments,
            get_backfill_status
        )

        action = args.get("action", "status")
        batch_size = args.get("batch_size", 50)

        if action == "status":
            status = get_backfill_status()
            output = f"""## Backfill Status

- Phase: {status['phase']}
- Progress: {status['atoms_processed']}/{status['total_atoms']} ({status['percentage']:.1f}%)
- Batches: {status['batches_completed']}
- Checkpoint: {'reached' if status['checkpoint_reached'] else 'not yet'}
- Validated: {'yes' if status['checkpoint_validated'] else 'no'}
- Errors: {status['errors']}
- Started: {status['started_at'] or 'N/A'}
- Last updated: {status['last_updated'] or 'N/A'}"""
            return {"content": [{"type": "text", "text": output}]}

        elif action == "start":
            result = await run_backfill(batch_size=batch_size, resume=False)
            return {"content": [{"type": "text", "text": (
                f"## Backfill {'Checkpoint' if result['status'] == 'checkpoint' else 'Complete'}\n\n"
                f"- Status: {result['status']}\n"
                f"- Atoms processed: {result['atoms_processed']}/{result['total_atoms']}\n"
                f"- {result.get('message', '')}\n"
                f"- Errors: {len(result.get('errors', []))}"
            )}]}

        elif action == "continue":
            result = await continue_backfill()
            return {"content": [{"type": "text", "text": (
                f"## Backfill Continued\n\n"
                f"- Status: {result.get('status')}\n"
                f"- Atoms processed: {result.get('atoms_processed', 0)}/{result.get('total_atoms', 0)}\n"
                f"- Errors: {len(result.get('errors', []))}"
            )}]}

        elif action == "rollback":
            result = rollback_assignments()
            return {"content": [{"type": "text", "text": (
                f"## Rollback Complete\n\n"
                f"- Status: {result['status']}\n"
                f"- Threads restored: {result.get('threads_restored', 0)}/{result.get('total_in_snapshot', 0)}"
            )}]}

        else:
            return {"content": [{"type": "text", "text": f"Unknown action: {action}"}], "is_error": True}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
