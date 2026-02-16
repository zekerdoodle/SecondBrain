"""
Gardener-only MCP tools.

These tools are registered under the "gardener" category and are ONLY available
to the Gardener agent. Primary Claude does not have access to these tools.

Tools:
- gardener_search_threads: Search threads by semantic similarity
- gardener_get_thread_detail: Get full thread details with all atoms
- gardener_assign_atom: Add atom to thread with confidence level
- gardener_update_atom: Version atom content (supersession)
- gardener_create_thread: Create thread with scope, check for duplicates
- gardener_split_thread: Split thread with lineage tracking
- gardener_merge_threads: Merge threads, preserve all atoms
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


@register_tool("gardener")
@tool(
    name="gardener_search_threads",
    description="""Search threads by semantic similarity to find candidate threads for atom assignment.

Returns thread summaries including name, scope, atom count, last updated, and 3 most recent atoms.""",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query to find relevant threads"},
            "limit": {"type": "integer", "description": "Max results (default: 10)", "default": 10}
        },
        "required": ["query"]
    }
)
async def gardener_search_threads(args: Dict[str, Any]) -> Dict[str, Any]:
    """Search threads by semantic similarity."""
    try:
        from ltm.thread_memory import get_thread_manager
        from ltm.atomic_memory import get_atomic_manager

        query = args.get("query", "")
        limit = args.get("limit", 10)

        if not query:
            return {"content": [{"type": "text", "text": "Query is required"}], "is_error": True}

        thread_mgr = get_thread_manager()
        atom_mgr = get_atomic_manager()

        results = thread_mgr.search(query, k=limit)

        if not results:
            return {"content": [{"type": "text", "text": f"No threads found for: {query}"}]}

        output = [f"## Thread Search Results for: {query}\n"]
        for thread, score in results:
            # Get 3 most recent atoms
            sample_atoms = []
            for mid in thread.memory_ids[-3:]:
                atom = atom_mgr.get(mid)
                if atom:
                    sample_atoms.append(f"  - {atom.content[:100]}")

            output.append(
                f"**{thread.name}** (score: {score:.3f})\n"
                f"  Scope: {thread.scope or thread.description}\n"
                f"  Atoms: {len(thread.memory_ids)} | Last updated: {thread.last_updated[:10] if thread.last_updated else 'unknown'}\n"
                f"  ID: {thread.id}\n"
                f"  Recent atoms:\n" + "\n".join(sample_atoms) + "\n"
            )

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_get_thread_detail",
    description="""Get full details of a thread including all its atoms.

Returns thread metadata, scope, and complete atom list with content and timestamps.""",
    input_schema={
        "type": "object",
        "properties": {
            "thread_name": {"type": "string", "description": "Thread name (case-insensitive)"},
            "thread_id": {"type": "string", "description": "Thread ID (alternative to name)"}
        }
    }
)
async def gardener_get_thread_detail(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get full thread details."""
    try:
        from ltm.thread_memory import get_thread_manager
        from ltm.atomic_memory import get_atomic_manager

        thread_name = args.get("thread_name")
        thread_id = args.get("thread_id")

        if not thread_name and not thread_id:
            return {"content": [{"type": "text", "text": "Either thread_name or thread_id is required"}], "is_error": True}

        thread_mgr = get_thread_manager()
        atom_mgr = get_atomic_manager()

        thread = None
        if thread_id:
            thread = thread_mgr.get(thread_id)
        if not thread and thread_name:
            thread = thread_mgr.get_by_name(thread_name)

        if not thread:
            return {"content": [{"type": "text", "text": f"Thread not found: {thread_name or thread_id}"}], "is_error": True}

        output = [
            f"## Thread: {thread.name}",
            f"**ID:** {thread.id}",
            f"**Scope:** {thread.scope or thread.description}",
            f"**Description:** {thread.description}",
            f"**Created:** {thread.created_at[:10] if thread.created_at else 'unknown'}",
            f"**Last updated:** {thread.last_updated[:10] if thread.last_updated else 'unknown'}",
            f"**Atom count:** {len(thread.memory_ids)}",
            f"**Split from:** {thread.split_from or 'N/A'}",
            f"**Split into:** {', '.join(thread.split_into) if thread.split_into else 'N/A'}",
            "",
            "### Atoms",
        ]

        for mid in thread.memory_ids:
            atom = atom_mgr.get(mid)
            if atom:
                confidence = atom.assignment_confidence.get(thread.id, "unset")
                output.append(
                    f"- **{atom.id}** [{atom.created_at[:10] if atom.created_at else '?'}] "
                    f"(confidence: {confidence})\n"
                    f"  {atom.content}"
                )
            else:
                output.append(f"- **{mid}** (missing atom)")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_assign_atom",
    description="""Assign an atom to a thread with a confidence level.

Confidence levels:
- "high": Clearly fits scope, advances the thread's purpose
- "medium": Fits scope but tangentially related
- "low": Borderline - might belong, might not (goes to triage queue)""",
    input_schema={
        "type": "object",
        "properties": {
            "atom_id": {"type": "string", "description": "Atom ID to assign"},
            "thread_name": {"type": "string", "description": "Thread name to assign to"},
            "thread_id": {"type": "string", "description": "Thread ID (alternative to name)"},
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Assignment confidence level",
                "default": "high"
            }
        },
        "required": ["atom_id"]
    }
)
async def gardener_assign_atom(args: Dict[str, Any]) -> Dict[str, Any]:
    """Assign atom to thread with confidence."""
    try:
        from ltm.thread_memory import get_thread_manager
        from ltm.atomic_memory import get_atomic_manager

        atom_id = args.get("atom_id", "")
        thread_name = args.get("thread_name")
        thread_id = args.get("thread_id")
        confidence = args.get("confidence", "high")

        if not atom_id:
            return {"content": [{"type": "text", "text": "atom_id is required"}], "is_error": True}
        if not thread_name and not thread_id:
            return {"content": [{"type": "text", "text": "Either thread_name or thread_id is required"}], "is_error": True}

        atom_mgr = get_atomic_manager()
        thread_mgr = get_thread_manager()

        # Verify atom exists
        atom = atom_mgr.get(atom_id)
        if not atom:
            return {"content": [{"type": "text", "text": f"Atom not found: {atom_id}"}], "is_error": True}

        # Find thread
        thread = None
        if thread_id:
            thread = thread_mgr.get(thread_id)
        if not thread and thread_name:
            thread = thread_mgr.get_by_name(thread_name)

        if not thread:
            return {"content": [{"type": "text", "text": f"Thread not found: {thread_name or thread_id}"}], "is_error": True}

        # Add atom to thread
        thread_mgr.add_memory_to_thread(thread.id, atom_id)

        # Record confidence on the atom
        atom.assignment_confidence[thread.id] = confidence
        atom_mgr._save()

        return {"content": [{"type": "text", "text": f"Assigned atom {atom_id} to '{thread.name}' with {confidence} confidence"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_update_atom",
    description="""Update an atom's content, creating a version history entry.

Use this for supersession: when a new fact replaces an old one.
The old content is preserved in previous_versions with a reason.""",
    input_schema={
        "type": "object",
        "properties": {
            "atom_id": {"type": "string", "description": "Atom ID to update"},
            "new_content": {"type": "string", "description": "Updated content"},
            "reason": {"type": "string", "description": "Why this was superseded (e.g., 'Status changed - got the job')"}
        },
        "required": ["atom_id", "new_content", "reason"]
    }
)
async def gardener_update_atom(args: Dict[str, Any]) -> Dict[str, Any]:
    """Version atom content (supersession)."""
    try:
        from ltm.atomic_memory import get_atomic_manager

        atom_id = args.get("atom_id", "")
        new_content = args.get("new_content", "")
        reason = args.get("reason", "")

        if not atom_id or not new_content or not reason:
            return {"content": [{"type": "text", "text": "atom_id, new_content, and reason are all required"}], "is_error": True}

        atom_mgr = get_atomic_manager()
        atom = atom_mgr.update(
            atom_id,
            content=new_content,
            superseded_reason=reason
        )

        if not atom:
            return {"content": [{"type": "text", "text": f"Atom not found: {atom_id}"}], "is_error": True}

        return {"content": [{"type": "text", "text": (
            f"Updated atom {atom_id}\n"
            f"New content: {new_content[:100]}...\n"
            f"Reason: {reason}\n"
            f"Previous versions: {len(atom.previous_versions)}"
        )}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_create_thread",
    description="""Create a new thread with a scope definition.

Checks for similar existing threads first and warns if found.
Scope should clearly describe what content belongs in this thread.""",
    input_schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Thread name (specific and descriptive)"},
            "scope": {"type": "string", "description": "What content belongs in this thread"},
            "description": {"type": "string", "description": "Brief description (defaults to scope if not provided)"}
        },
        "required": ["name", "scope"]
    }
)
async def gardener_create_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    """Create thread with scope, checking for similar existing threads."""
    try:
        from ltm.thread_memory import get_thread_manager

        name = args.get("name", "").strip()
        scope = args.get("scope", "").strip()
        description = args.get("description", "").strip() or scope

        if not name or not scope:
            return {"content": [{"type": "text", "text": "name and scope are required"}], "is_error": True}

        thread_mgr = get_thread_manager()

        # Check for exact name match
        existing = thread_mgr.get_by_name(name)
        if existing:
            return {"content": [{"type": "text", "text": (
                f"Thread '{name}' already exists (ID: {existing.id})\n"
                f"Scope: {existing.scope or existing.description}\n"
                f"Atoms: {len(existing.memory_ids)}"
            )}]}

        # Check for semantically similar threads
        similar = thread_mgr.search(f"{name}: {scope}", k=3)
        warning = ""
        if similar:
            similar_info = []
            for t, score in similar:
                if score > 0.5:
                    similar_info.append(f"  - '{t.name}' (similarity: {score:.2f}, {len(t.memory_ids)} atoms)")
            if similar_info:
                warning = "\n\n**Warning - similar threads exist:**\n" + "\n".join(similar_info)

        thread = thread_mgr.create(
            name=name,
            description=description,
            scope=scope
        )

        return {"content": [{"type": "text", "text": f"Created thread '{name}' (ID: {thread.id})\nScope: {scope}{warning}"}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_split_thread",
    description="""Split a thread into more focused sub-threads.

Preserves lineage (split_from on children, split_into on parent).
Source thread is deleted if all atoms are reassigned.""",
    input_schema={
        "type": "object",
        "properties": {
            "thread_name": {"type": "string", "description": "Source thread name"},
            "thread_id": {"type": "string", "description": "Source thread ID (alternative to name)"},
            "new_threads": {
                "type": "array",
                "description": "New threads to create from the split",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "scope": {"type": "string"},
                        "description": {"type": "string"},
                        "atom_ids": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["name", "scope", "atom_ids"]
                }
            }
        },
        "required": ["new_threads"]
    }
)
async def gardener_split_thread(args: Dict[str, Any]) -> Dict[str, Any]:
    """Split thread with lineage tracking."""
    try:
        from ltm.thread_memory import get_thread_manager

        thread_name = args.get("thread_name")
        thread_id = args.get("thread_id")
        new_threads = args.get("new_threads", [])

        if not thread_name and not thread_id:
            return {"content": [{"type": "text", "text": "Either thread_name or thread_id is required"}], "is_error": True}

        thread_mgr = get_thread_manager()

        # Find source thread
        thread = None
        if thread_id:
            thread = thread_mgr.get(thread_id)
        if not thread and thread_name:
            thread = thread_mgr.get_by_name(thread_name)

        if not thread:
            return {"content": [{"type": "text", "text": f"Thread not found: {thread_name or thread_id}"}], "is_error": True}

        result = thread_mgr.split_thread(
            source_thread_id=thread.id,
            new_threads=new_threads,
            delete_source_if_empty=True
        )

        if not result["success"]:
            return {"content": [{"type": "text", "text": "Split failed:\n" + "\n".join(result["errors"])}], "is_error": True}

        output = [
            f"Split '{thread.name}' into {len(result['new_thread_ids'])} threads:",
        ]
        for i, tid in enumerate(result["new_thread_ids"]):
            nt = new_threads[i]
            output.append(f"  - '{nt['name']}' ({len(nt['atom_ids'])} atoms) ID: {tid}")
        output.append(f"Source deleted: {'Yes' if result['source_deleted'] else 'No'}")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_get_triage_queue",
    description="""Get atoms with low-confidence thread assignments (triage queue).

These atoms were assigned with LOW confidence and need re-evaluation.
Returns atom ID, content, current assignments with confidence levels.""",
    input_schema={
        "type": "object",
        "properties": {
            "limit": {"type": "integer", "description": "Max results (default: 50)", "default": 50}
        }
    }
)
async def gardener_get_triage_queue(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get atoms needing re-evaluation (triage queue)."""
    try:
        from ltm.atomic_memory import get_atomic_manager
        from ltm.thread_memory import get_thread_manager

        limit = args.get("limit", 50)

        atom_mgr = get_atomic_manager()
        thread_mgr = get_thread_manager()

        triage_atoms = atom_mgr.get_low_confidence_atoms()

        if not triage_atoms:
            return {"content": [{"type": "text", "text": "Triage queue is empty - no low-confidence assignments."}]}

        output = [f"## Triage Queue ({len(triage_atoms)} atoms with low-confidence assignments)\n"]

        for atom in triage_atoms[:limit]:
            # Get thread names for assignments
            assignments = []
            for thread_id, confidence in atom.assignment_confidence.items():
                thread = thread_mgr.get(thread_id)
                thread_name = thread.name if thread else f"(deleted: {thread_id})"
                assignments.append(f"{thread_name} ({confidence})")

            output.append(
                f"**{atom.id}** [{atom.created_at[:10] if atom.created_at else '?'}]\n"
                f"  Content: {atom.content[:150]}{'...' if len(atom.content) > 150 else ''}\n"
                f"  Assignments: {', '.join(assignments) or 'none'}\n"
            )

        if len(triage_atoms) > limit:
            output.append(f"\n... and {len(triage_atoms) - limit} more atoms in triage")

        return {"content": [{"type": "text", "text": "\n".join(output)}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}


@register_tool("gardener")
@tool(
    name="gardener_merge_threads",
    description="""Merge multiple threads into one.

Combines all atoms from source threads into a new merged thread.
Source threads are deleted after merge. All atoms preserved.""",
    input_schema={
        "type": "object",
        "properties": {
            "thread_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of threads to merge"
            },
            "merged_name": {"type": "string", "description": "Name for the merged thread"},
            "merged_scope": {"type": "string", "description": "Scope for the merged thread"}
        },
        "required": ["thread_names", "merged_name", "merged_scope"]
    }
)
async def gardener_merge_threads(args: Dict[str, Any]) -> Dict[str, Any]:
    """Merge threads, preserving all atoms."""
    try:
        from ltm.thread_memory import get_thread_manager

        thread_names = args.get("thread_names", [])
        merged_name = args.get("merged_name", "").strip()
        merged_scope = args.get("merged_scope", "").strip()

        if len(thread_names) < 2:
            return {"content": [{"type": "text", "text": "At least 2 thread names are required"}], "is_error": True}
        if not merged_name or not merged_scope:
            return {"content": [{"type": "text", "text": "merged_name and merged_scope are required"}], "is_error": True}

        thread_mgr = get_thread_manager()

        # Find all source threads
        source_threads = []
        all_atom_ids = []
        for name in thread_names:
            thread = thread_mgr.get_by_name(name)
            if not thread:
                return {"content": [{"type": "text", "text": f"Thread not found: {name}"}], "is_error": True}
            source_threads.append(thread)
            all_atom_ids.extend(thread.memory_ids)

        # Deduplicate atom IDs
        unique_atom_ids = list(dict.fromkeys(all_atom_ids))

        # Create merged thread
        merged = thread_mgr.create(
            name=merged_name,
            description=merged_scope,
            memory_ids=unique_atom_ids,
            scope=merged_scope
        )

        # Delete source threads
        deleted = []
        for thread in source_threads:
            if thread_mgr.delete(thread.id):
                deleted.append(thread.name)

        return {"content": [{"type": "text", "text": (
            f"Merged {len(deleted)} threads into '{merged_name}' (ID: {merged.id})\n"
            f"Total atoms: {len(unique_atom_ids)}\n"
            f"Deleted: {', '.join(deleted)}"
        )}]}

    except Exception as e:
        import traceback
        return {"content": [{"type": "text", "text": f"Error: {str(e)}\n{traceback.format_exc()}"}], "is_error": True}
