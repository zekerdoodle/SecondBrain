#!/usr/bin/env python3
"""Assign a single atom to a single thread."""

import sys
import json
from pathlib import Path
from thread_memory import get_thread_manager
from atomic_memory import get_atomic_manager


def assign_atom_to_thread(atom_id, thread_id, confidence):
    """Assign an atom to a thread with explicit save and verify."""

    print(f"\nAssigning {atom_id} to {thread_id} with confidence={confidence}")
    print("-" * 80)

    # Step 1: Add atom to thread
    thread_mgr = get_thread_manager()
    thread = thread_mgr.get(thread_id)

    if not thread:
        print(f"ERROR: Thread not found: {thread_id}")
        return False

    print(f"Thread: {thread.name}")
    print(f"Atoms before: {len(thread.memory_ids)}")

    success = thread_mgr.add_memory_to_thread(thread_id, atom_id)
    if not success:
        print(f"ERROR: Failed to add atom to thread")
        return False

    print(f"✓ add_memory_to_thread returned success")

    # Verify in memory
    thread = thread_mgr.get(thread_id)
    print(f"Atoms after (in memory): {len(thread.memory_ids)}")
    print(f"Atom in list: {atom_id in thread.memory_ids}")

    # Verify in file
    threads_file = Path(__file__).parent.parent.parent / "memory" / "threads.json"
    with open(threads_file) as f:
        data = json.load(f)
        for t in data.get('threads', []):
            if t['id'] == thread_id:
                print(f"Atoms in JSON file: {len(t.get('memory_ids', []))}")
                print(f"Atom in JSON: {atom_id in t.get('memory_ids', [])}")
                break

    # Step 2: Add confidence to atom
    atom_mgr = get_atomic_manager()
    atom = atom_mgr.get(atom_id)

    if not atom:
        print(f"ERROR: Atom not found: {atom_id}")
        return False

    print(f"\nAtom confidence before: {atom.assignment_confidence}")
    atom.assignment_confidence[thread_id] = confidence
    atom_mgr._save()
    print(f"✓ Saved atom confidence")

    # Verify in file
    atomic_file = Path(__file__).parent.parent.parent / "memory" / "atomic_memories.json"
    with open(atomic_file) as f:
        data = json.load(f)
        for a in data.get('memories', []):
            if a['id'] == atom_id:
                conf = a.get('assignment_confidence', {})
                print(f"Atom confidence in JSON: {conf.get(thread_id, 'NOT FOUND')}")
                break

    print("✓ Assignment complete\n")
    return True


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python assign_one_atom.py <atom_id> <thread_id> <confidence>")
        print("Example: python assign_one_atom.py atom_123 thread_456 high")
        sys.exit(1)

    atom_id = sys.argv[1]
    thread_id = sys.argv[2]
    confidence = sys.argv[3]

    if confidence not in ['high', 'medium', 'low']:
        print(f"ERROR: Confidence must be 'high', 'medium', or 'low', not '{confidence}'")
        sys.exit(1)

    success = assign_atom_to_thread(atom_id, thread_id, confidence)
    sys.exit(0 if success else 1)
