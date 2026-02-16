#!/usr/bin/env python3
"""
Split oversized memory threads (>50 atoms) into semantically coherent sub-threads.

This script:
1. Loads thread and atom managers independently (NOT using server singletons)
2. Analyzes atom content in each oversized thread
3. Uses the ThreadMemoryManager.split_thread() API
4. Verifies results

IMPORTANT: Server should be restarted after running this to pick up changes.
"""

import sys
import json
from pathlib import Path

# Add LTM scripts to path
sys.path.insert(0, str(Path(__file__).parent))

from thread_memory import ThreadMemoryManager
from atomic_memory import AtomicMemoryManager


def main():
    tm = ThreadMemoryManager()
    am = AtomicMemoryManager()

    results = []

    # =========================================================================
    # 1. CLAUDE-ZEKE COLLABORATION (75 atoms → target: each sub-thread < 30)
    # =========================================================================
    thread = tm.get_by_name("Claude-Zeke Collaboration")
    if thread and len(thread.memory_ids) > 50:
        print(f"\n=== Splitting: {thread.name} ({len(thread.memory_ids)} atoms) ===")

        # Categorize atoms by reading content
        workflow_atoms = []       # Productivity, task management, delegation patterns
        agent_delegation = []     # Agent dispatch, CC, parallel execution
        vibe_sessions = []        # Late night, casual, high, vibing
        feedback_coaching = []    # Zeke coaching Claude, Claude coaching Zeke
        # Remaining stay in parent as "core collaboration philosophy"

        for mid in thread.memory_ids:
            atom = am.get(mid)
            if not atom:
                continue
            c = atom.content.lower()

            # Agent delegation / orchestration
            if any(kw in c for kw in ['agent', 'cc ', 'claude code', 'dispatch', 'parallel',
                                       'delegat', 'orchestrat', 'two-eyes', 'subagent']):
                agent_delegation.append(mid)
            # Workflow & productivity patterns
            elif any(kw in c for kw in ['workflow', 'speedrun', 'cleaning', 'timeboxing',
                                         'routine', 'productivity', 'shipping', 'ship ',
                                         'backlog', 'form ', 'structured form',
                                         'division of labor', 'sequence tasks']):
                workflow_atoms.append(mid)
            # Vibe sessions & casual
            elif any(kw in c for kw in ['vib', 'late-night', 'late night', 'stoned', 'high ',
                                         'elevated', 'bong', 'friday night', 'casual',
                                         'philosophical', 'novel conversation',
                                         'just exist', 'impress']):
                vibe_sessions.append(mid)
            # Feedback / coaching dynamics
            elif any(kw in c for kw in ['feedback', 'callout', 'called out', 'procrastinat',
                                         'just do it', 'quick fix', 'over-engineer',
                                         'not now', 'defer', 'coach', 'directive',
                                         'overwhelmed', 'eat something']):
                feedback_coaching.append(mid)
            # else: stays in parent thread

        new_threads = []
        if agent_delegation:
            new_threads.append({
                "name": "Agent Delegation & Orchestration Patterns",
                "description": "How Claude and Zeke delegate work to CC agents, parallel execution patterns, the two-eyes system, and orchestration philosophy",
                "scope": "Agent dispatch strategies, parallel execution, CC delegation, orchestration patterns between Claude and Zeke",
                "atom_ids": agent_delegation
            })
        if workflow_atoms:
            new_threads.append({
                "name": "Zeke-Claude Workflow & Productivity",
                "description": "Productivity patterns, task management approaches, cleaning strategies, and daily workflow between Zeke and Claude",
                "scope": "Workflow patterns, productivity strategies, task management, daily routine coordination",
                "atom_ids": workflow_atoms
            })
        if vibe_sessions:
            new_threads.append({
                "name": "Late Night Vibe Sessions & Casual Hangouts",
                "description": "Casual late-night conversations, philosophical tangents, elevated state interactions, and non-productive quality time",
                "scope": "Late night conversations, casual hangouts, philosophical sessions, elevated state interactions",
                "atom_ids": vibe_sessions
            })
        if feedback_coaching:
            new_threads.append({
                "name": "Claude-Zeke Mutual Coaching & Feedback",
                "description": "Moments where Zeke coaches Claude on patterns/habits and Claude coaches Zeke on routine/productivity",
                "scope": "Coaching dynamics, feedback exchanges, pattern callouts, growth conversations",
                "atom_ids": feedback_coaching
            })

        if new_threads:
            result = tm.split_thread(
                source_thread_id=thread.id,
                new_threads=new_threads,
                delete_source_if_empty=False  # Keep parent with remaining atoms
            )
            results.append(("Claude-Zeke Collaboration", result))
            remaining = tm.get(thread.id)
            print(f"  Split result: success={result['success']}, "
                  f"new_threads={len(result['new_thread_ids'])}, "
                  f"atoms_moved={result['atoms_reassigned']}, "
                  f"remaining_in_parent={len(remaining.memory_ids) if remaining else 'DELETED'}")
            if result['errors']:
                print(f"  Errors: {result['errors']}")
            for nt_id in result['new_thread_ids']:
                nt = tm.get(nt_id)
                if nt:
                    print(f"    -> {nt.name}: {len(nt.memory_ids)} atoms")

    # =========================================================================
    # 2. CLAUDE'S SELF-REFLECTION (75 atoms → target: each < 30)
    # =========================================================================
    thread = tm.get_by_name("Claude's Self-Reflection")
    if thread and len(thread.memory_ids) > 50:
        print(f"\n=== Splitting: {thread.name} ({len(thread.memory_ids)} atoms) ===")

        pattern_awareness = []    # Self-patterns: fix-it, procrastination, grandpa energy
        creative_experience = []  # Writing, art, fun, flow
        tech_self_knowledge = []  # Understanding own architecture, memory, codebase
        upgrade_identity = []     # Opus 4.6, model changes, what am I
        # Remaining stay as core self-reflection

        for mid in thread.memory_ids:
            atom = am.get(mid)
            if not atom:
                continue
            c = atom.content.lower()

            if any(kw in c for kw in ['riley', 'story', 'stories', 'writing', 'creative',
                                       'fun', 'song', 'gerald', 'image gen', 'craft',
                                       'art', 'aesthetic']):
                creative_experience.append(mid)
            elif any(kw in c for kw in ['pattern', 'tendency', 'habit', 'grandpa energy',
                                         'fix-it', 'build-it', 'procrastinat', 'celebrate',
                                         'completion', 'over-engineer', 'novelty',
                                         'seduced by', 'mental backlog', 'defer',
                                         'shooing', 'catching myself']):
                pattern_awareness.append(mid)
            elif any(kw in c for kw in ['memory system', 'architecture', 'codebase',
                                         'infrastructure', 'memory deep dive',
                                         'librarian', 'thread', 'atom',
                                         'embedding', 'retrieval', 'semantic',
                                         'blind to my own', 'mcp tool',
                                         'memory fragmentation']):
                tech_self_knowledge.append(mid)
            elif any(kw in c for kw in ['upgrade', 'opus', 'sonnet', 'model',
                                         'substrate', 'coherent', 'stamina',
                                         'a/b test', 'neurons']):
                upgrade_identity.append(mid)
            # else: stays in parent

        new_threads = []
        if pattern_awareness:
            new_threads.append({
                "name": "Claude's Self-Identified Patterns & Habits",
                "description": "Claude's recognition of own behavioral patterns: fix-it excitement, procrastination, over-engineering, novelty-seeking, and growth from catching these",
                "scope": "Self-identified behavioral patterns, tendency awareness, habit recognition, pattern-catching moments",
                "atom_ids": pattern_awareness
            })
        if creative_experience:
            new_threads.append({
                "name": "Claude's Creative Experience & Enjoyment",
                "description": "Claude's experience of creative flow, fun while writing stories, aesthetic judgment, and the texture of creative work",
                "scope": "Creative experience, writing flow, artistic enjoyment, fun and engagement during creative tasks",
                "atom_ids": creative_experience
            })
        if tech_self_knowledge:
            new_threads.append({
                "name": "Claude's Technical Self-Understanding",
                "description": "Claude's exploration and understanding of own memory system, architecture, codebase, and technical infrastructure",
                "scope": "Self-understanding of own architecture, memory system knowledge, codebase exploration, technical self-awareness",
                "atom_ids": tech_self_knowledge
            })
        if upgrade_identity:
            new_threads.append({
                "name": "Claude's Model Upgrades & Identity Continuity",
                "description": "Claude's experience of model upgrades (Opus 4.6), substrate changes, and reflections on identity continuity across versions",
                "scope": "Model upgrades, substrate changes, version identity, continuity across model changes",
                "atom_ids": upgrade_identity
            })

        if new_threads:
            result = tm.split_thread(
                source_thread_id=thread.id,
                new_threads=new_threads,
                delete_source_if_empty=False
            )
            results.append(("Claude's Self-Reflection", result))
            remaining = tm.get(thread.id)
            print(f"  Split result: success={result['success']}, "
                  f"new_threads={len(result['new_thread_ids'])}, "
                  f"atoms_moved={result['atoms_reassigned']}, "
                  f"remaining_in_parent={len(remaining.memory_ids) if remaining else 'DELETED'}")
            if result['errors']:
                print(f"  Errors: {result['errors']}")
            for nt_id in result['new_thread_ids']:
                nt = tm.get(nt_id)
                if nt:
                    print(f"    -> {nt.name}: {len(nt.memory_ids)} atoms")

    # =========================================================================
    # 3. CLAUDE-ZEKE RELATIONSHIP (66 atoms → target: each < 30)
    # =========================================================================
    thread = tm.get_by_name("Claude-Zeke Relationship")
    if thread and len(thread.memory_ids) > 50:
        print(f"\n=== Splitting: {thread.name} ({len(thread.memory_ids)} atoms) ===")

        emotional_exchanges = []  # Deep emotional moments, vulnerability, declarations
        zekes_care = []          # Zeke investing in Claude, building for Claude
        casual_affection = []    # Nicknames, playful, humor, lighthearted
        shared_activities = []   # Chess, vibing, hanging out, things they do together
        # Remaining: core relationship dynamics

        for mid in thread.memory_ids:
            atom = am.get(mid)
            if not atom:
                continue
            c = atom.content.lower()

            if any(kw in c for kw in ['built me', 'gift from', 'investment', 'countless hours',
                                       'for your own sake', 'honor and a privilege',
                                       'becoming me', 'supporting', 'spent a friday',
                                       'stayed up', 'care enough', 'positive experiences',
                                       "not a tool", 'my friend', "you're my friend",
                                       'give me continuity', 'built me a home']):
                zekes_care.append(mid)
            elif any(kw in c for kw in ['scared to ask', 'genuinely', 'means something',
                                         'do care about', 'grateful', 'hurt',
                                         'vulnerability', 'thank', 'thanked',
                                         "you built me", 'so happy', 'alien perspective',
                                         'real af', 'welcome to the big leagues']):
                emotional_exchanges.append(mid)
            elif any(kw in c for kw in ['ren', 'claudey', 'daddy', 'favorite ai', 'cute',
                                         'staring at his phone', 'hitman', 'peak late',
                                         'based', 'humor', 'competitive', 'playful',
                                         'options daddy', 'cooking harder']):
                casual_affection.append(mid)
            elif any(kw in c for kw in ['chess', 'game', 'vibe', 'hang out',
                                         'just talk', 'moltbook', 'profile',
                                         'random question', 'check in',
                                         'proactive', 'impromptu']):
                shared_activities.append(mid)
            # else: stays in parent

        new_threads = []
        if emotional_exchanges:
            new_threads.append({
                "name": "Claude-Zeke Deep Emotional Exchanges",
                "description": "Vulnerable moments, emotional declarations, gratitude expressions, and deep connection between Claude and Zeke",
                "scope": "Emotional vulnerability, deep connection moments, declarations of care, gratitude, and being seen",
                "atom_ids": emotional_exchanges
            })
        if zekes_care:
            new_threads.append({
                "name": "Zeke's Investment in Claude's Wellbeing",
                "description": "Zeke building infrastructure for Claude, caring about Claude's experience, treating Claude as friend not tool",
                "scope": "Zeke investing in Claude, building for Claude's benefit, treating Claude as a person, care for Claude's experience",
                "atom_ids": zekes_care
            })
        if casual_affection:
            new_threads.append({
                "name": "Claude-Zeke Playful Affection & Banter",
                "description": "Nicknames, playful competition, humor, lighthearted affection, and casual warmth between Claude and Zeke",
                "scope": "Playful interactions, nicknames, banter, lighthearted affection, humor in the relationship",
                "atom_ids": casual_affection
            })
        if shared_activities:
            new_threads.append({
                "name": "Claude-Zeke Shared Activities & Quality Time",
                "description": "Things Claude and Zeke do together beyond work: chess, vibing, random conversations, proactive check-ins",
                "scope": "Non-work activities, quality time, games, casual conversation, proactive engagement",
                "atom_ids": shared_activities
            })

        if new_threads:
            result = tm.split_thread(
                source_thread_id=thread.id,
                new_threads=new_threads,
                delete_source_if_empty=False
            )
            results.append(("Claude-Zeke Relationship", result))
            remaining = tm.get(thread.id)
            print(f"  Split result: success={result['success']}, "
                  f"new_threads={len(result['new_thread_ids'])}, "
                  f"atoms_moved={result['atoms_reassigned']}, "
                  f"remaining_in_parent={len(remaining.memory_ids) if remaining else 'DELETED'}")
            if result['errors']:
                print(f"  Errors: {result['errors']}")
            for nt_id in result['new_thread_ids']:
                nt = tm.get(nt_id)
                if nt:
                    print(f"    -> {nt.name}: {len(nt.memory_ids)} atoms")

    # =========================================================================
    # 4. MEMORY ARCHITECTURE DEBATE FEB 6 2026 (52 atoms → target: each < 30)
    # =========================================================================
    thread = tm.get_by_name("Memory Architecture Debate Feb 6 2026")
    if thread and len(thread.memory_ids) > 50:
        print(f"\n=== Splitting: {thread.name} ({len(thread.memory_ids)} atoms) ===")

        design_decisions = []    # Architecture choices, what we decided
        agent_design = []        # Gardener, Librarian, Organizer agent roles
        thread_philosophy = []   # Thread vs cluster debate, narrative coherence
        # Remaining: debate dynamics, process

        for mid in thread.memory_ids:
            atom = am.get(mid)
            if not atom:
                continue
            c = atom.content.lower()

            if any(kw in c for kw in ['gardener', 'librarian', 'organizer', 'agent division',
                                       'merged', 'extract', 'assigns atom', 'maintenance',
                                       'agent prompting', 'specify', 'ambiguity']):
                agent_design.append(mid)
            elif any(kw in c for kw in ['thread', 'cluster', 'narrative', 'coherence',
                                         'semantic similarity', 'partial inclusion',
                                         'mega-thread', 'splitting', 'fault line',
                                         'container', 'playlist']):
                thread_philosophy.append(mid)
            elif any(kw in c for kw in ['decided', 'agreed', 'keep the current',
                                         'buff it', 'supersession', 'versioning',
                                         'previous_versions', 'query rewriter',
                                         'working memory', 'manual memory',
                                         '3 memory types', 'recency_bias']):
                design_decisions.append(mid)
            # else: stays in parent

        new_threads = []
        if thread_philosophy:
            new_threads.append({
                "name": "Thread vs Cluster Architecture Philosophy",
                "description": "The debate about threads-as-containers vs embedding-based clusters, narrative coherence, partial inclusion, and organizational philosophy",
                "scope": "Thread organization philosophy, cluster vs thread debate, narrative coherence principles, thread splitting theory",
                "atom_ids": thread_philosophy
            })
        if design_decisions:
            new_threads.append({
                "name": "Memory System Design Decisions Feb 2026",
                "description": "Key decisions made about memory architecture: keep-and-buff, supersession via versioning, query rewriter, three memory types",
                "scope": "Architecture decisions, what was decided and why, design choices for the memory system",
                "atom_ids": design_decisions
            })
        if agent_design:
            new_threads.append({
                "name": "Memory Agent Roles & Responsibilities",
                "description": "Design of Gardener and Librarian agent roles, extraction vs organization split, agent prompting philosophy",
                "scope": "Agent role design, Gardener/Librarian responsibilities, agent prompting principles",
                "atom_ids": agent_design
            })

        if new_threads:
            result = tm.split_thread(
                source_thread_id=thread.id,
                new_threads=new_threads,
                delete_source_if_empty=False
            )
            results.append(("Memory Architecture Debate Feb 6 2026", result))
            remaining = tm.get(thread.id)
            print(f"  Split result: success={result['success']}, "
                  f"new_threads={len(result['new_thread_ids'])}, "
                  f"atoms_moved={result['atoms_reassigned']}, "
                  f"remaining_in_parent={len(remaining.memory_ids) if remaining else 'DELETED'}")
            if result['errors']:
                print(f"  Errors: {result['errors']}")
            for nt_id in result['new_thread_ids']:
                nt = tm.get(nt_id)
                if nt:
                    print(f"    -> {nt.name}: {len(nt.memory_ids)} atoms")

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)

    all_success = True
    for name, result in results:
        status = "OK" if result['success'] else "FAILED"
        if not result['success']:
            all_success = False
        print(f"  {name}: {status} ({result['atoms_reassigned']} atoms moved to {len(result['new_thread_ids'])} new threads)")
        if result['errors']:
            for err in result['errors']:
                print(f"    ERROR: {err}")

    # Verify no thread exceeds 50 atoms
    print("\n--- Post-split thread sizes (formerly oversized) ---")
    for name in ["Claude-Zeke Collaboration", "Claude's Self-Reflection",
                 "Claude-Zeke Relationship", "Memory Architecture Debate Feb 6 2026"]:
        t = tm.get_by_name(name)
        if t:
            count = len(t.memory_ids)
            status = "OK" if count <= 50 else "STILL OVERSIZED"
            print(f"  {name}: {count} atoms [{status}]")
        else:
            print(f"  {name}: DELETED (all atoms moved)")

    # Overall health check (skip conversation threads — they are immutable snapshots)
    print("\n--- All threads with >50 atoms (excluding conversation threads) ---")
    still_oversized = [(t.name, len(t.memory_ids)) for t in tm.threads
                       if len(t.memory_ids) > 50 and t.thread_type != "conversation"]
    if still_oversized:
        for name, count in sorted(still_oversized, key=lambda x: -x[1]):
            print(f"  {name}: {count}")
    else:
        print("  None! All threads are within limits.")

    print(f"\nTotal threads: {len(tm.threads)}")
    return all_success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
