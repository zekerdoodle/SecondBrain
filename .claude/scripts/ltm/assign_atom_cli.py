#!/usr/bin/env python3
"""Manual atom assignment script - directly calls thread manager functions."""

import sys
from thread_memory import get_thread_manager
from atomic_memory import get_atomic_manager


def search_and_assign(atom_id, search_query, confidence):
    """Search for thread and assign atom if found."""
    # Get fresh managers each time
    thread_mgr = get_thread_manager()
    atom_mgr = get_atomic_manager()

    # Verify atom exists
    atom = atom_mgr.get(atom_id)
    if not atom:
        return {"success": False, "error": f"Atom not found: {atom_id}"}

    # Search for threads
    results = thread_mgr.search(search_query, k=5)

    if not results:
        return {"success": False, "error": f"No threads found for: {search_query}"}

    # Display results
    print(f"\nSearch: '{search_query}'")
    print(f"Found {len(results)} threads:\n")

    for i, (thread, score) in enumerate(results, 1):
        print(f"{i}. {thread.name} (score: {score:.3f})")
        print(f"   ID: {thread.id}")
        print(f"   Scope: {thread.scope or thread.description}")
        print(f"   Atoms: {len(thread.memory_ids)}")
        print()

    # Use top result
    top_thread, top_score = results[0]

    # Step 1: Add atom to thread (this saves immediately)
    success = thread_mgr.add_memory_to_thread(top_thread.id, atom_id)
    if not success:
        return {"success": False, "error": f"Failed to add atom to thread"}

    # Step 2: Record confidence on the atom
    # Need to get fresh atom since it's a separate data structure
    atom = atom_mgr.get(atom_id)
    if atom:
        atom.assignment_confidence[top_thread.id] = confidence
        atom_mgr._save()

    # Step 3: Verify the assignment persisted
    thread_mgr_verify = get_thread_manager()
    atom_mgr_verify = get_atomic_manager()

    thread_verify = thread_mgr_verify.get(top_thread.id)
    atom_verify = atom_mgr_verify.get(atom_id)

    in_thread = atom_id in thread_verify.memory_ids if thread_verify else False
    has_confidence = (atom_verify and
                     top_thread.id in atom_verify.assignment_confidence and
                     atom_verify.assignment_confidence[top_thread.id] == confidence)

    return {
        "success": True,
        "thread_id": top_thread.id,
        "thread_name": top_thread.name,
        "confidence": confidence,
        "score": top_score,
        "all_results": [(t.name, t.id, s) for t, s in results],
        "verified_in_thread": in_thread,
        "verified_confidence": has_confidence
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python assign_atom_cli.py <atom_id>")
        print("Then provide search queries interactively")
        sys.exit(1)

    atom_id = sys.argv[1]

    # Verify atom exists
    atom_mgr = get_atomic_manager()
    atom = atom_mgr.get(atom_id)
    if not atom:
        print(f"ERROR: Atom not found: {atom_id}")
        sys.exit(1)

    print("="*80)
    print(f"ATOM: {atom_id}")
    print("="*80)
    print(f"Content: {atom.content}")
    print(f"Tags: {', '.join(atom.tags) if atom.tags else 'none'}")
    print(f"Created: {atom.created_at[:10] if atom.created_at else 'unknown'}")
    print(f"Assignment confidence records: {len(atom.assignment_confidence)}")
    print("="*80)

    # Assignment 1: Job Search Strategy
    print("\n\n[1/3] Job Search Strategy")
    result1 = search_and_assign(atom_id, "Job Search Strategy", "high")
    if result1["success"]:
        print(f"✓ Assigned to '{result1['thread_name']}' (ID: {result1['thread_id']})")
        print(f"  Confidence: {result1['confidence']} | Score: {result1['score']:.3f}")
        print(f"  Verified in thread: {result1.get('verified_in_thread', 'N/A')}")
        print(f"  Verified confidence: {result1.get('verified_confidence', 'N/A')}")
    else:
        print(f"✗ {result1['error']}")

    # Assignment 2: Claude's Advice Style
    print("\n\n[2/3] Claude's Advice Style")
    result2 = search_and_assign(atom_id, "Claude's Advice Style", "high")
    if result2["success"]:
        print(f"✓ Assigned to '{result2['thread_name']}' (ID: {result2['thread_id']})")
        print(f"  Confidence: {result2['confidence']} | Score: {result2['score']:.3f}")
        print(f"  Verified in thread: {result2.get('verified_in_thread', 'N/A')}")
        print(f"  Verified confidence: {result2.get('verified_confidence', 'N/A')}")
    else:
        print(f"✗ {result2['error']}")

    # Assignment 3: Job Search 2026
    print("\n\n[3/3] Job Search 2026")
    result3 = search_and_assign(atom_id, "Job Search 2026", "medium")
    if result3["success"]:
        print(f"✓ Assigned to '{result3['thread_name']}' (ID: {result3['thread_id']})")
        print(f"  Confidence: {result3['confidence']} | Score: {result3['score']:.3f}")
        print(f"  Verified in thread: {result3.get('verified_in_thread', 'N/A')}")
        print(f"  Verified confidence: {result3.get('verified_confidence', 'N/A')}")
    else:
        print(f"✗ {result3['error']}")

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)

    results = [
        ("Job Search Strategy", result1),
        ("Claude's Advice Style", result2),
        ("Job Search 2026", result3)
    ]

    for query, result in results:
        if result["success"]:
            print(f"\n✓ {query}")
            print(f"  Thread: {result['thread_name']}")
            print(f"  ID: {result['thread_id']}")
            print(f"  Confidence: {result['confidence']}")
            print(f"  Match score: {result['score']:.3f}")
            print(f"  Other candidates found: {len(result['all_results']) - 1}")
        else:
            print(f"\n✗ {query}")
            print(f"  Error: {result['error']}")

    print("\n")


if __name__ == "__main__":
    main()
